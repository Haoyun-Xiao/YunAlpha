"""Public-source discovery and evidence capture for industry research.

The service is deliberately conservative: a provider error is preserved in
diagnostics (and raises when no usable document remains), publication dates are
never guessed, and every external page is marked as untrusted input.  A small
hash-addressed replay cache makes tests and repeatable research independent of
the network once a capture exists.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup

from .config import Settings

try:
    import pymupdf as fitz  # PyMuPDF, optional until the dependency installation completes
except ImportError:  # pragma: no cover - dependency doctor covers this case
    try:
        import fitz  # type: ignore[no-redef]  # older PyMuPDF compatibility
    except ImportError:
        fitz = None  # type: ignore[assignment]


MAX_CONTENT_CHARS = 200_000
REPLAY_VERSION = "industry-chain-sources-v1"
# Bump when normalized source text changes.  Workflow input fingerprints can
# import this constant so polluted source checkpoints are invalidated.
CONTENT_VERSION = "industry-chain-content-v2-inline-image-strip"
TRACKING_KEYS = ("utm_", "spm", "from", "source", "share")
INJECTION_MARKERS = re.compile(
    r"(ignore\s+(the\s+)?(system|developer|previous)\s+instructions|"
    r"忽略.{0,20}(系统|开发者|之前).{0,20}(指令|要求)|tool[_ -]?call|调用工具)",
    re.I,
)
_MARKDOWN_DATA_IMAGE = re.compile(
    r"!\[(?P<alt>[^\]\n]*)\]\(\s*[^)]*?data:image/[^;\s,)\"'>]+;base64,.*?\)(?:\s*)",
    re.IGNORECASE | re.DOTALL,
)
_MARKDOWN_DATA_IMAGE_UNTERMINATED = re.compile(
    r"!\[(?P<alt>[^\]\n]*)\]\(\s*[^)]*?data:image/[^;\s,)\"'>]+;base64,.*\Z",
    re.IGNORECASE | re.DOTALL,
)
_HTML_DATA_IMAGE = re.compile(
    r"<(?P<tag>img|source)\b(?P<attrs>[^>]*?data:image/[^;\s,)\"'>]+;base64,.*?)(?:>|$)",
    re.IGNORECASE | re.DOTALL,
)


def _image_placeholder(alt: str | None) -> str:
    caption = " ".join(str(alt or "").split())
    return f"[图片未解析：{caption}]" if caption else "[图片未解析]"


def _strip_embedded_images(value: str) -> tuple[str, bool]:
    """Remove inline image payloads before HTML parsing or length limits.

    The markdown matcher intentionally accepts an unterminated destination: a
    replay may already have been mechanically cut in the middle of a base64
    image.  Only images whose destination contains an image data URI are
    removed; ordinary public image URLs remain available as source text.
    """

    changed = False

    def markdown_replacement(match: re.Match[str]) -> str:
        nonlocal changed
        changed = True
        return _image_placeholder(match.group("alt"))

    text = _MARKDOWN_DATA_IMAGE.sub(markdown_replacement, str(value or ""))
    # An old replay can end at MAX_CONTENT_CHARS in the middle of the image
    # destination, so there is no closing ``)`` to match.  In that case the
    # remainder is image payload by definition and must not enter the model.
    text = _MARKDOWN_DATA_IMAGE_UNTERMINATED.sub(markdown_replacement, text)

    def html_replacement(match: re.Match[str]) -> str:
        nonlocal changed
        attrs = match.group("attrs")
        changed = True
        alt_match = re.search(
            r"\b(?:alt|title)\s*=\s*(['\"])(.*?)\1", attrs, re.IGNORECASE | re.DOTALL
        )
        return _image_placeholder(alt_match.group(2) if alt_match else None)

    text = _HTML_DATA_IMAGE.sub(html_replacement, text)
    # A malformed replay can lose the closing markdown/HTML delimiter.  Keep
    # this fallback deliberately narrow: remove a marker and its contiguous
    # base64 payload, but do not scan arbitrary ordinary article text.
    text, count = re.subn(
        r"data:image/[^;\s,)\"'>]+;base64,[A-Za-z0-9+/=_-]+",
        "[图片未解析]",
        text,
        flags=re.IGNORECASE,
    )
    changed = changed or count > 0
    return text, changed


class SourceProviderError(RuntimeError):
    """Visible failure from Tavily or a public page fetch."""

    def __init__(self, message: str, *, provider: str = "tavily", status_code: int | None = None):
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code


def canonical_url(url: str) -> str:
    value = str(url or "").strip()
    if not value:
        return ""
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return ""
    query = [
        (key, val)
        for key, val in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith(TRACKING_KEYS)
    ]
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path.rstrip("/") or "/",
            urlencode(query),
            "",
        )
    )


def _iso_date(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        if len(text) == 10:
            return datetime.fromisoformat(text).date().isoformat()
        return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat()
    except ValueError:
        match = re.search(
            r"(?P<year>20\d{2})\s*[年./-]\s*(?P<month>\d{1,2})\s*[月./-]\s*(?P<day>\d{1,2})\s*日?",
            text,
        )
        if not match:
            return None
        try:
            return (
                datetime(
                    int(match.group("year")),
                    int(match.group("month")),
                    int(match.group("day")),
                )
                .date()
                .isoformat()
            )
        except ValueError:
            return None


def extract_published_at(value: str) -> tuple[str | None, dict[str, str] | None]:
    """Extract an explicitly published date and its provenance.

    Only metadata, ``datetime`` attributes, JSON-LD ``datePublished`` and
    visible header labels are accepted.  A bare year in article prose (for
    example a forecast or report period) is intentionally ignored.
    """

    raw = str(value or "")
    if not raw.strip():
        return None, None

    def candidate(
        raw_value: Any, *, source: str, field: str
    ) -> tuple[str | None, dict[str, str] | None]:
        normalized = _iso_date(raw_value)
        if not normalized:
            return None, None
        return normalized, {
            "source": source,
            "field": field,
            "raw_value": str(raw_value)[:160],
        }

    if "<" in raw and ">" in raw:
        soup = BeautifulSoup(raw, "html.parser")
        for meta in soup.find_all("meta"):
            field = str(meta.get("property") or meta.get("name") or "").strip().lower()
            if field in {"article:published_time", "datepublished", "date_published"}:
                result = candidate(meta.get("content"), source="html_meta", field=field)
                if result[0]:
                    return result
        for time_tag in soup.find_all("time"):
            if time_tag.get("datetime"):
                result = candidate(
                    time_tag.get("datetime"), source="html_time", field="time.datetime"
                )
                if result[0]:
                    return result
        # JSON-LD is data-only parsing; scripts are never passed to the model.
        for script in soup.find_all("script"):
            if "ld+json" not in str(script.get("type") or "").lower():
                continue
            try:
                payload = json.loads(script.string or script.get_text() or "")
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            values: list[Any] = []
            stack = payload if isinstance(payload, list) else [payload]
            while stack:
                item = stack.pop()
                if isinstance(item, dict):
                    if item.get("datePublished"):
                        values.append(item["datePublished"])
                    stack.extend(
                        value for value in item.values() if isinstance(value, (dict, list))
                    )
                elif isinstance(item, list):
                    stack.extend(item)
            for item in values:
                result = candidate(item, source="html_jsonld", field="datePublished")
                if result[0]:
                    return result
        visible_header = soup.get_text(" ", strip=True)[:4000]
    else:
        visible_header = raw[:4000]

    header_patterns = (
        (
            r"(?:发布时间|发布日期|发布于|发表于|发表时间)\s*[:：]?\s*([^\s|｜,，;；]{8,32})",
            "header_text",
        ),
        (r"(?:published(?:\s+on)?|datepublished)\s*[:：]?\s*([^\s|,;]{8,32})", "header_text"),
    )
    for pattern, source in header_patterns:
        match = re.search(pattern, visible_header, re.IGNORECASE)
        if match:
            result = candidate(match.group(1), source=source, field="explicit_header_date")
            if result[0]:
                return result
    return None, None


def _clean_content(value: str) -> tuple[str, list[str]]:
    text, embedded_image_removed = _strip_embedded_images(str(value or ""))
    flags = ["external_untrusted"]
    if embedded_image_removed:
        flags.append("embedded_image_not_parsed")
    if "<html" in text.lower() or "<script" in text.lower():
        soup = BeautifulSoup(text, "html.parser")
        for node in soup(["script", "style", "nav", "noscript", "svg", "form"]):
            node.decompose()
        text = "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())
    text = text.replace("\x00", "").strip()
    if len(text) > MAX_CONTENT_CHARS:
        text = text[:MAX_CONTENT_CHARS]
        flags.append("content_truncated")
    if not text:
        flags.append("content_empty")
    if INJECTION_MARKERS.search(text):
        flags.append("untrusted_prompt_like_text")
    return text, flags


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _source_priority(url: str) -> int:
    """Rank official/industry sources without filtering out other sources."""

    host = urlsplit(url).netloc.lower()
    if any(
        token in host
        for token in (
            "gov.cn",
            "sse.com.cn",
            "szse.cn",
            "cninfo.com.cn",
            "csrc.gov.cn",
        )
    ):
        return 0
    if (
        host.endswith(".org.cn")
        or host.endswith(".org")
        or any(token in host for token in ("miit.gov.cn", "mofcom.gov.cn", "cnii.com.cn"))
    ):
        return 1
    return 2


class SourceService:
    """Tavily discovery + public extraction with local replay and dedup."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.var = settings.var.resolve()
        self.replay_root = self.var / "replays" / "industry-sources"
        self.raw_root = self.var / "sources" / "raw"
        self._keys = list(dict.fromkeys(settings.tavily_keys))
        self._key_index = 0
        self._unavailable: set[int] = set()
        self._errors: list[dict[str, Any]] = []
        self._state_path = self.var / "state" / "source-key-pool.json"
        self._load_key_state()

    @property
    def configured_key_count(self) -> int:
        return len(self._keys)

    @property
    def last_errors(self) -> list[dict[str, Any]]:
        return list(self._errors)

    def _load_key_state(self) -> None:
        if not self._state_path.exists():
            return
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        fingerprints = {_fingerprint(key): index for index, key in enumerate(self._keys)}
        for fingerprint in raw.get("unavailable_fingerprints", []):
            if fingerprint in fingerprints:
                self._unavailable.add(fingerprints[fingerprint])
        current = fingerprints.get(raw.get("current_fingerprint"))
        if current is not None:
            self._key_index = current

    def _save_key_state(self) -> None:
        if not self._keys or self.var not in self._state_path.resolve().parents:
            return
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        current = self._keys[self._key_index] if self._keys else ""
        data = {
            "current_fingerprint": _fingerprint(current) if current else None,
            "unavailable_fingerprints": sorted(
                _fingerprint(self._keys[i]) for i in self._unavailable
            ),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        self._state_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def _next_key(self, attempted: set[int]) -> int | None:
        if not self._keys:
            return None
        for offset in range(len(self._keys)):
            index = (self._key_index + offset) % len(self._keys)
            if index not in self._unavailable and index not in attempted:
                return index
        return None

    async def _tavily(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        attempted: set[int] = set()
        if not self._keys:
            raise SourceProviderError("未配置 Tavily API Key 且没有可用回放")
        while True:
            index = self._next_key(attempted)
            if index is None:
                raise SourceProviderError("所有 Tavily API Key 均不可用")
            attempted.add(index)
            body = {"api_key": self._keys[index], **payload}
            try:
                async with httpx.AsyncClient(timeout=45) as client:
                    response = await client.post(f"https://api.tavily.com/{endpoint}", json=body)
                if response.status_code in {401, 403, 429}:
                    self._unavailable.add(index)
                    self._save_key_state()
                    self._errors.append(
                        {
                            "provider": "tavily",
                            "status_code": response.status_code,
                            "endpoint": endpoint,
                        }
                    )
                    continue
                if response.status_code >= 500:
                    self._errors.append(
                        {
                            "provider": "tavily",
                            "status_code": response.status_code,
                            "endpoint": endpoint,
                        }
                    )
                    if len(attempted) < len(self._keys):
                        continue
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict):
                    raise SourceProviderError("Tavily 返回了非对象 JSON", provider="tavily")
                self._key_index = index
                return data
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                raise SourceProviderError(
                    f"Tavily 请求失败：HTTP {status}", provider="tavily", status_code=status
                ) from exc
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                self._errors.append(
                    {"provider": "tavily", "error_type": type(exc).__name__, "endpoint": endpoint}
                )
                if len(attempted) < len(self._keys):
                    continue
                raise SourceProviderError(
                    f"Tavily 网络请求失败：{type(exc).__name__}", provider="tavily"
                ) from exc

    def _replay_path(self, anchor: str, queries: list[str], as_of: str | None) -> Path:
        key = _fingerprint(
            REPLAY_VERSION + "\n" + anchor + "\n" + "\n".join(queries) + "\n" + str(as_of)
        )[:32]
        return self.replay_root / f"{key}.json"

    async def search(
        self, query: str, as_of: str | None = None, limit: int = 5
    ) -> list[dict[str, Any]]:
        """Search and normalize candidates; content extraction is best-effort."""

        replay = self._replay_path(query, [query], as_of)
        mode = self.settings.web_mode.lower()
        if mode in {"auto", "replay"} and replay.exists():
            payload = json.loads(replay.read_text(encoding="utf-8"))
            return list(payload.get("documents", []))[:limit]
        if mode == "off":
            return []
        if mode == "replay":
            raise SourceProviderError(f"未找到资料回放：{replay.name}", provider="replay")
        self._errors = []
        response = await self._tavily(
            "search",
            {
                "query": query,
                "search_depth": self.settings.search_depth,
                "max_results": max(1, min(limit, 20)),
                "include_answer": False,
                "include_raw_content": False,
                "country": "china",
            },
        )
        docs = [self._candidate(item, query) for item in response.get("results", [])]
        docs = [doc for doc in docs if doc["url"]]
        if as_of:
            docs = [doc for doc in docs if self._allowed_by_as_of(doc, as_of)]
        self._save_replay(
            replay, {"query": query, "documents": docs, "provider_errors": self.last_errors}
        )
        return docs[:limit]

    def _candidate(self, item: dict[str, Any], query: str) -> dict[str, Any]:
        url = canonical_url(item.get("url", ""))
        title = str(item.get("title") or "").strip()
        published_raw = item.get("published_date") or item.get("published_at")
        published = _iso_date(published_raw)
        published_provenance = (
            {
                "source": "provider_metadata",
                "field": "published_date" if item.get("published_date") else "published_at",
                "raw_value": str(published_raw)[:160],
            }
            if published
            else None
        )
        return {
            "evidence_id": _fingerprint(url or title),
            "title": title,
            "url": url,
            "published_at": published,
            "published_at_provenance": published_provenance,
            "fetched_at": datetime.now(UTC).isoformat(),
            "content": "",
            "content_hash": _fingerprint(""),
            "source_type": "web",
            "quality_flags": ["external_untrusted", "content_pending_extract"],
            "fact_type": "source_fact",
            "matched_queries": [query],
            "search_score": item.get("score"),
            "source_priority": _source_priority(url),
            "best_query_rank": item.get("rank"),
            "source_description": item.get("content", ""),
        }

    @staticmethod
    def _allowed_by_as_of(doc: dict[str, Any], as_of: str) -> bool:
        published = doc.get("published_at")
        if not published:
            doc.setdefault("quality_flags", []).append("published_at_unknown")
            return True
        try:
            return published[:10] <= as_of[:10]
        except (TypeError, IndexError):
            doc.setdefault("quality_flags", []).append("published_at_unknown")
            return True

    @staticmethod
    def _filter_documents_as_of(
        docs: list[dict[str, Any]], as_of: str | None
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Filter explicit publication dates after extraction, retaining provenance."""

        if not as_of:
            return docs, []
        allowed: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for doc in docs:
            published = doc.get("published_at")
            if published and str(published)[:10] > str(as_of)[:10]:
                flags = doc.setdefault("quality_flags", [])
                if "published_after_as_of" not in flags:
                    flags.append("published_after_as_of")
                rejected.append(
                    {
                        "title": doc.get("title"),
                        "url": doc.get("url"),
                        "published_at": published,
                        "published_at_provenance": doc.get("published_at_provenance"),
                        "content_hash": doc.get("content_hash"),
                        "quality_flags": flags,
                        "rejection_reason": "published_after_as_of",
                    }
                )
            else:
                allowed.append(doc)
        return allowed, rejected

    async def collect(
        self,
        anchor: str,
        keywords: list[str] | None = None,
        as_of: str | None = None,
        job_dir: Path | None = None,
        limit: int = 12,
    ) -> list[dict[str, Any]]:
        queries = list(dict.fromkeys([anchor, *(keywords or [])]))
        replay = self._replay_path(anchor, queries, as_of)
        mode = self.settings.web_mode.lower()
        if mode in {"auto", "replay"} and replay.exists():
            payload = json.loads(replay.read_text(encoding="utf-8"))
            docs, rejected = self._usable_documents(list(payload.get("documents", [])))
            docs = self._deduplicate_documents(docs)
            docs, future_rejected = self._filter_documents_as_of(docs, as_of)
            rejected.extend(future_rejected)
            self._persist_rejected(rejected, job_dir)
            if not docs:
                raise SourceProviderError(
                    "资料回放中的正文均为空，不能作为有效证据", provider="replay"
                )
            docs = docs[:limit]
            self._persist_documents(docs, job_dir)
            return docs
        if mode == "off":
            return []
        if mode == "replay":
            raise SourceProviderError(f"未找到资料回放：{replay.name}", provider="replay")
        self._errors = []
        candidates: dict[str, dict[str, Any]] = {}
        per_query: dict[str, list[str]] = {}
        query_errors: list[dict[str, Any]] = []
        for query in queries:
            try:
                found = await self.search(query, as_of=as_of, limit=max(limit, 5))
            except SourceProviderError as exc:
                query_errors.append(
                    {
                        "query": query,
                        "provider": exc.provider,
                        "status_code": exc.status_code,
                        "error": str(exc),
                    }
                )
                continue
            per_query.setdefault(query, [])
            for doc in found:
                previous = candidates.get(doc["url"])
                if previous is None:
                    candidates[doc["url"]] = doc
                else:
                    previous["matched_queries"] = sorted(
                        set(previous.get("matched_queries", []))
                        | set(doc.get("matched_queries", []))
                    )
                if doc["url"] not in per_query[query]:
                    per_query[query].append(doc["url"])
        if not candidates:
            raise SourceProviderError(
                "资料搜索未返回可用候选；"
                + (json.dumps(query_errors, ensure_ascii=False) if query_errors else "无结果"),
                provider="tavily",
            )
        selected = self._round_robin_select(candidates, per_query, limit)
        await self._extract_content(selected)
        selected, rejected = self._usable_documents(selected)
        selected = self._deduplicate_documents(selected)
        selected, future_rejected = self._filter_documents_as_of(selected, as_of)
        rejected.extend(future_rejected)
        for doc in selected:
            if query_errors or self._errors:
                doc.setdefault("quality_flags", []).append("provider_partial_failure")
                doc["provider_errors"] = query_errors + self.last_errors
        self._persist_rejected(rejected, job_dir)
        if not selected:
            raise SourceProviderError(
                "候选资料均未成功抽取正文，不能作为有效证据；"
                + json.dumps(rejected, ensure_ascii=False)[:2000],
                provider="tavily",
            )
        self._persist_documents(selected, job_dir)
        self._save_replay(
            replay,
            {
                "anchor": anchor,
                "queries": queries,
                "documents": selected,
                "rejected_documents": rejected,
                "provider_errors": query_errors,
            },
        )
        return selected

    async def collect_segment(
        self,
        anchor: str,
        node: Mapping[str, Any],
        as_of: str | None = None,
        job_dir: Path | None = None,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        """Collect a small, segment-specific evidence set.

        This is intentionally a thin wrapper around :meth:`collect`: it only
        builds bounded queries and isolates each segment's manifest directory.
        A provider failure becomes a stage error plus an empty result so the
        research workflow can publish the segment as a documented data gap.
        """

        anchor_text = " ".join(str(anchor or "").split())
        segment = " ".join(
            str(
                node.get("name") or node.get("segment") or node.get("title") or node.get("id") or ""
            ).split()
        )
        if not anchor_text or not segment:
            raise ValueError("collect_segment 需要 anchor 和 node.name/segment")

        terms = node.get("core_terms") or node.get("keywords") or node.get("key_terms") or []
        if isinstance(terms, str):
            terms = [terms]
        core_terms = [" ".join(str(term).split()) for term in terms if str(term).strip()][:3]
        topic = " ".join([segment, *core_terms[:2]])
        queries = [
            f"{anchor_text} {topic}".strip(),
            f"{anchor_text} {segment} 景气 价格 产能".strip(),
        ]
        companies = node.get("candidate_companies") or []
        if isinstance(companies, (str, bytes)):
            companies = [companies]
        for candidate in companies[:2]:
            if isinstance(candidate, Mapping):
                company_name = (
                    candidate.get("name") or candidate.get("company") or candidate.get("symbol")
                )
                symbol = candidate.get("symbol")
                if company_name and symbol and str(symbol) not in str(company_name):
                    company_name = f"{company_name} {symbol}"
            else:
                company_name = candidate
            if str(company_name or "").strip():
                queries.append(f"{str(company_name).strip()} {segment} 公告 主营业务".strip())
        queries = list(dict.fromkeys(queries))
        bounded_limit = max(1, min(int(limit), 3))

        base = job_dir.resolve() if job_dir else (self.var / "sources").resolve()
        if self.var not in base.parents and base != self.var:
            raise ValueError("job_dir 必须位于新项目 var/ 内")
        segment_key = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "_", segment).strip("_")[:72]
        segment_key = segment_key or "segment"
        segment_dir = base / "segments" / f"{segment_key}_{_fingerprint('|'.join(queries))[:10]}"
        errors: list[dict[str, Any]] = []
        documents: list[dict[str, Any]] = []
        try:
            documents = await self.collect(
                queries[0],
                keywords=queries[1:],
                as_of=as_of,
                job_dir=segment_dir,
                limit=bounded_limit,
            )
            errors.extend(self.last_errors)
            for document in documents:
                for error in (
                    document.get("provider_errors", []) if isinstance(document, Mapping) else []
                ):
                    if error not in errors:
                        errors.append(error)
            if not documents:
                errors.append({"type": "no_documents", "message": "该环节没有可用正文资料"})
        except SourceProviderError as exc:
            errors.append(
                {
                    "type": "source_provider_error",
                    "provider": exc.provider,
                    "status_code": exc.status_code,
                    "message": str(exc),
                }
            )
        except Exception as exc:  # stage failure becomes a documented gap
            errors.append({"type": "segment_collection_error", "message": str(exc)})

        segment_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "content_version": CONTENT_VERSION,
            "anchor": anchor_text,
            "segment": segment,
            "as_of": as_of,
            "queries": queries,
            "limit": bounded_limit,
            "documents": documents[:bounded_limit],
            "errors": errors,
            "created_at": datetime.now(UTC).isoformat(),
        }
        manifest_path = segment_dir / "segment-manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return documents[:bounded_limit]

    @staticmethod
    def _round_robin_select(
        candidates: dict[str, dict[str, Any]],
        per_query: dict[str, list[str]],
        limit: int,
    ) -> list[dict[str, Any]]:
        """Select across queries first, then fill by global source quality.

        Each query gets a turn. Within a turn official and industry sources
        come first, but media and third-party pages remain eligible.
        """

        ordered: dict[str, list[str]] = {}
        for query, urls in per_query.items():
            ordered[query] = sorted(
                dict.fromkeys(urls),
                key=lambda url: (
                    _source_priority(url),
                    -float(candidates[url].get("search_score") or 0),
                    int(candidates[url].get("best_query_rank") or 999),
                ),
            )
        selected_urls: list[str] = []
        offsets = {query: 0 for query in ordered}
        while len(selected_urls) < limit:
            progressed = False
            for query in ordered:
                urls = ordered[query]
                while offsets[query] < len(urls) and urls[offsets[query]] in selected_urls:
                    offsets[query] += 1
                if offsets[query] >= len(urls):
                    continue
                selected_urls.append(urls[offsets[query]])
                offsets[query] += 1
                progressed = True
                if len(selected_urls) >= limit:
                    break
            if not progressed:
                break
        if len(selected_urls) < limit:
            remaining = sorted(
                (url for url in candidates if url not in selected_urls),
                key=lambda url: (
                    _source_priority(url),
                    -float(candidates[url].get("search_score") or 0),
                    int(candidates[url].get("best_query_rank") or 999),
                ),
            )
            selected_urls.extend(remaining[: limit - len(selected_urls)])
        return [candidates[url] for url in selected_urls]

    @staticmethod
    def _usable_documents(
        docs: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        usable: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for doc in docs:
            SourceService._normalize_document(doc)
            flags = doc.setdefault("quality_flags", [])
            if str(doc.get("content") or "").strip() and not {
                "not_usable_evidence",
                "replay_content_incomplete",
            }.intersection(flags):
                usable.append(doc)
                continue
            for flag in ("content_empty", "not_usable_evidence"):
                if flag not in flags:
                    flags.append(flag)
            rejected.append(
                {
                    "title": doc.get("title"),
                    "url": doc.get("url"),
                    "content_hash": doc.get("content_hash"),
                    "raw_content_hash": doc.get("raw_content_hash"),
                    "content_version": doc.get("content_version"),
                    "quality_flags": flags,
                    "extract_error": doc.get("extract_error"),
                    "fetch_error": doc.get("fetch_error"),
                }
            )
        return usable, rejected

    @staticmethod
    def _deduplicate_documents(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Deduplicate by canonical URL first and normalized content hash second."""

        result: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        seen_hashes: set[str] = set()
        for doc in docs:
            url = canonical_url(str(doc.get("url") or ""))
            content_hash = str(doc.get("content_hash") or "")
            if (url and url in seen_urls) or (content_hash and content_hash in seen_hashes):
                continue
            if url:
                seen_urls.add(url)
            if content_hash:
                seen_hashes.add(content_hash)
            result.append(doc)
        return result

    @staticmethod
    def _normalize_document(doc: dict[str, Any], raw_content: str | None = None) -> dict[str, Any]:
        """Normalize a document in memory and retain hashes for audit.

        ``raw_content_hash`` identifies the captured body before image removal;
        the body passed to research and ``content_hash`` always refer to the
        normalized text.  Existing raw files/replays are never deleted here.
        """

        source = str(doc.get("content") or "") if raw_content is None else str(raw_content or "")
        doc.setdefault("raw_content_hash", _fingerprint(source))
        if not doc.get("published_at"):
            published_at, provenance = extract_published_at(source)
            if published_at:
                doc["published_at"] = published_at
                doc["published_at_provenance"] = provenance
        elif not doc.get("published_at_provenance"):
            doc["published_at_provenance"] = {
                "source": "existing_document",
                "field": "published_at",
                "raw_value": str(doc.get("published_at"))[:160],
            }
        text, flags = _clean_content(source)
        doc["content"] = text
        doc["content_hash"] = _fingerprint(text)
        doc["content_version"] = CONTENT_VERSION
        quality_flags = doc.setdefault("quality_flags", [])
        for flag in flags:
            if flag not in quality_flags:
                quality_flags.append(flag)
        if text and "content_pending_extract" in quality_flags:
            quality_flags.remove("content_pending_extract")
        # A replay that was cut in the middle of a base64 image cannot recover
        # the missing tail from its local body.  It is retained in the rejected
        # manifest for audit, but never sent to a researcher.
        if "content_truncated" in quality_flags and "embedded_image_not_parsed" in quality_flags:
            if "replay_content_incomplete" not in quality_flags:
                quality_flags.append("replay_content_incomplete")
            if "not_usable_evidence" not in quality_flags:
                quality_flags.append("not_usable_evidence")
        return doc

    async def _extract_content(self, docs: list[dict[str, Any]]) -> None:
        urls = [doc["url"] for doc in docs if doc.get("url")]
        extracted: dict[str, str] = {}
        if self.settings.web_mode.lower() != "off" and self._keys:
            try:
                response = await self._tavily(
                    "extract", {"urls": urls, "extract_depth": "basic", "format": "markdown"}
                )
                for item in response.get("results", []):
                    url = canonical_url(item.get("url", ""))
                    if url:
                        extracted[url] = str(item.get("raw_content") or item.get("content") or "")
                for item in response.get("failed_results", []):
                    url = canonical_url(item.get("url", ""))
                    for doc in docs:
                        if doc.get("url") == url:
                            doc.setdefault("quality_flags", []).append("extract_failed")
                            doc["extract_error"] = str(item.get("error") or "extract_failed")
            except SourceProviderError as exc:
                self._errors.append(
                    {"provider": exc.provider, "error": str(exc), "endpoint": "extract"}
                )

        async def fetch(doc: dict[str, Any]) -> None:
            content = extracted.get(doc["url"], "")
            if not content:
                try:
                    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                        response = await client.get(
                            doc["url"], headers={"User-Agent": "IndustryResearchAgent/0.1"}
                        )
                    response.raise_for_status()
                    content = self._decode_response(response)
                except Exception as exc:
                    doc.setdefault("quality_flags", []).append("page_fetch_failed")
                    doc["fetch_error"] = type(exc).__name__
            if content:
                raw_path = self.raw_root / f"{_fingerprint(content)}.txt"
                if self.var not in raw_path.resolve().parents:
                    raise ValueError("原始抽取正文必须位于新项目 var/ 内")
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                if not raw_path.exists():
                    raw_path.write_text(content, encoding="utf-8")
                doc["raw_content_ref"] = str(raw_path.relative_to(self.var))
            self._normalize_document(doc, content)
            doc["fetched_at"] = datetime.now(UTC).isoformat()

        await asyncio.gather(*(fetch(doc) for doc in docs))

    @staticmethod
    def _decode_response(response: httpx.Response) -> str:
        content_type = response.headers.get("content-type", "").lower()
        if "pdf" in content_type or response.content[:4] == b"%PDF":
            if fitz is None:
                return ""
            document = fitz.open(stream=response.content, filetype="pdf")
            return "\n".join(page.get_text() for page in document)
        return response.text

    def _persist_documents(self, docs: Iterable[dict[str, Any]], job_dir: Path | None) -> None:
        destination = job_dir.resolve() if job_dir else self.var / "sources"
        if self.var not in destination.parents and destination != self.var:
            raise ValueError("job_dir 必须位于新项目 var/ 内")
        raw_dir = destination / "sources" / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        for doc in docs:
            content = str(doc.get("content", ""))
            if content:
                path = raw_dir / f"{doc['content_hash']}.txt"
                if not path.exists():
                    path.write_text(content, encoding="utf-8")
        manifest = destination / "sources" / "documents.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            json.dumps(list(docs), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def _persist_rejected(self, docs: Iterable[dict[str, Any]], job_dir: Path | None) -> None:
        rejected = list(docs)
        if not rejected:
            return
        destination = job_dir.resolve() if job_dir else self.var / "sources"
        if self.var not in destination.parents and destination != self.var:
            raise ValueError("job_dir 必须位于新项目 var/ 内")
        path = destination / "sources" / "rejected-documents.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rejected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _save_replay(self, path: Path, payload: dict[str, Any]) -> None:
        if self.var not in path.resolve().parents:
            raise ValueError("资料回放必须位于新项目 var/ 内")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"version": REPLAY_VERSION, "created_at": datetime.now(UTC).isoformat(), **payload},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
