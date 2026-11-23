from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from tavily import AsyncTavilyClient
from tavily.errors import ForbiddenError, InvalidAPIKeyError, UsageLimitExceededError

from fund_research_agent.adapters.company import CompanyShortNameResolver
from fund_research_agent.domain.errors import DependencyUnavailable
from fund_research_agent.paths import WriteBoundary

TRACKING_QUERY_PREFIXES = ("utm_", "spm", "from", "source")
MAX_EXTERNAL_PAGE_CHARS = 200_000
SEARCH_REPLAY_VERSION = "manager-material-search-v4"
EXTRACT_BATCH_SIZE = 10
TEMPORAL_SEARCH_BUCKETS = 3
ROTATABLE_KEY_ERRORS = (UsageLimitExceededError, InvalidAPIKeyError, ForbiddenError)


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith(TRACKING_QUERY_PREFIXES)
    ]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), urlencode(query), ""))


def classify_source(url: str, company_short: str) -> Literal["A", "B", "C"]:
    host = urlsplit(url).netloc.lower()
    if any(token in host for token in ("gov.cn", "sse.com.cn", "szse.cn", "amac.org.cn")):
        return "A"
    if company_short and company_short.lower() in host:
        return "A"
    if any(token in host for token in ("cnfund", "stcn", "cs.com.cn", "21jingji", "yicai", "caixin")):
        return "B"
    return "C"


def sanitize_external_content(text: str) -> tuple[str, bool]:
    value = str(text or "")
    if "<html" in value.lower() or "<script" in value.lower():
        soup = BeautifulSoup(value, "html.parser")
        for node in soup(["script", "style", "nav", "noscript", "svg"]):
            node.decompose()
        value = "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())
    truncated = len(value) > MAX_EXTERNAL_PAGE_CHARS
    return value[:MAX_EXTERNAL_PAGE_CHARS], truncated


def temporal_windows(start_year: int, end_year: int) -> list[tuple[int, int]]:
    """Split an inclusive research period into at most three broad search windows."""
    if end_year < start_year:
        return []
    years = list(range(max(2016, start_year), end_year + 1))
    if not years:
        return []
    bucket_count = min(TEMPORAL_SEARCH_BUCKETS, len(years))
    base_size, remainder = divmod(len(years), bucket_count)
    windows: list[tuple[int, int]] = []
    offset = 0
    for index in range(bucket_count):
        size = base_size + (1 if index < remainder else 0)
        bucket = years[offset : offset + size]
        windows.append((bucket[0], bucket[-1]))
        offset += size
    return windows


def configured_tavily_keys() -> list[str]:
    raw_values = [os.getenv("TAVILY_API_KEYS", ""), os.getenv("TAVILY_API_KEY", "")]
    keys: list[str] = []
    for raw in raw_values:
        for key in re.split(r"[,;\s]+", raw.strip()):
            if key and key not in keys:
                keys.append(key)
    return keys


class TavilyKeyPool:
    """Rotate authorized keys on explicit quota or credential failures."""

    def __init__(
        self,
        state_path: Path,
        boundary: WriteBoundary,
        client_factory: Callable[..., AsyncTavilyClient] = AsyncTavilyClient,
    ) -> None:
        self.state_path = boundary.assert_writable(state_path)
        self.boundary = boundary
        self.client_factory = client_factory
        self.keys = configured_tavily_keys()
        self.fingerprints = [hashlib.sha256(key.encode()).hexdigest()[:12] for key in self.keys]
        self.unavailable: set[str] = set()
        self.current_index = 0
        self.lock = asyncio.Lock()
        self._load_state()

    @property
    def configured_count(self) -> int:
        return len(self.keys)

    @property
    def available_count(self) -> int:
        return sum(item not in self.unavailable for item in self.fingerprints)

    @property
    def current_fingerprint(self) -> str | None:
        index = self._choose_index(set())
        return self.fingerprints[index] if index is not None else None

    def _load_state(self) -> None:
        if not self.state_path.exists():
            return
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        known = set(self.fingerprints)
        self.unavailable = set(state.get("unavailable_fingerprints", [])) & known
        current = state.get("current_fingerprint")
        if current in known:
            self.current_index = self.fingerprints.index(current)

    def _choose_index(self, attempted: set[str]) -> int | None:
        for offset in range(len(self.keys)):
            index = (self.current_index + offset) % len(self.keys)
            fingerprint = self.fingerprints[index]
            if fingerprint not in self.unavailable and fingerprint not in attempted:
                return index
        return None

    def _persist_state(self) -> None:
        current = self._choose_index(set())
        payload = {
            "current_fingerprint": self.fingerprints[current] if current is not None else None,
            "unavailable_fingerprints": sorted(self.unavailable),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        self.boundary.write_text(
            self.state_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        )

    async def request(self, method: str, **kwargs: Any) -> dict[str, Any]:
        attempted: set[str] = set()
        while True:
            async with self.lock:
                index = self._choose_index(attempted)
            if index is None:
                raise DependencyUnavailable("所有 Tavily API Key 均已耗尽或不可用")
            fingerprint = self.fingerprints[index]
            client = self.client_factory(api_key=self.keys[index])
            try:
                result = await getattr(client, method)(**kwargs)
            except ROTATABLE_KEY_ERRORS:
                attempted.add(fingerprint)
                async with self.lock:
                    self.unavailable.add(fingerprint)
                    next_index = self._choose_index(attempted)
                    if next_index is not None:
                        self.current_index = next_index
                    self._persist_state()
                continue
            finally:
                await client.close()
            async with self.lock:
                self.current_index = index
            return result


class ManagerMaterialSearch:
    def __init__(
        self,
        replay_root: Path,
        boundary: WriteBoundary,
        company_resolver: CompanyShortNameResolver,
        *,
        frozen_replay_root: Path | None = None,
        allow_live: bool = True,
    ) -> None:
        self.replay_root = boundary.ensure_dir(replay_root)
        self.boundary = boundary
        self.company_resolver = company_resolver
        self.frozen_replay_root = frozen_replay_root
        self.allow_live = allow_live
        self.key_pool = TavilyKeyPool(
            self.replay_root.parents[1] / "state/tavily-key-pool.json", boundary
        )

    @staticmethod
    def _replay_key(queries: list[str]) -> str:
        value = SEARCH_REPLAY_VERSION + "\n" + "\n".join(queries)
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]

    async def search(
        self,
        company_full_name: str,
        manager_name: str,
        *,
        mode: Literal["auto", "live", "replay", "off"] = "auto",
        max_results: int = 15,
        min_score: float = 0.5,
        start_year: int = 2016,
        end_year: int | None = None,
        as_of=None,
    ) -> dict[str, Any]:
        queries = self.company_resolver.queries(company_full_name, manager_name)
        company_short = self.company_resolver.resolve(company_full_name)
        end_year = end_year or datetime.now(UTC).year
        windows = temporal_windows(start_year, end_year)
        replay_path = self.replay_root / f"{self._replay_key(queries + [f'window:{start_year}:{end_year}:{as_of}:{max_results}:{min_score}'])}.json"
        if not self.allow_live and mode == "live":
            raise DependencyUnavailable("YunAlpha 仅使用已冻结的公开资料回放")
        if mode in {"auto", "replay"}:
            replay_paths = [replay_path]
            if self.frozen_replay_root is not None:
                replay_paths.append(self.frozen_replay_root / replay_path.name)
            for candidate in replay_paths:
                if candidate.is_file():
                    data = json.loads(candidate.read_text(encoding="utf-8"))
                    data["replay"] = True
                    return data
        if mode == "off":
            return {"queries": queries, "results": [], "mode": "off", "replay": False}
        if mode == "replay" or not self.allow_live:
            raise DependencyUnavailable(f"未找到搜索回放：{replay_path.name}")
        if not self.key_pool.configured_count:
            raise DependencyUnavailable("缺少 TAVILY_API_KEYS 且没有可用回放")

        async def search_all(depth: str):
            return await asyncio.gather(
                *(
                    self.key_pool.request(
                        "search",
                        query=query,
                        search_depth=depth,
                        max_results=max_results,
                        include_answer=False,
                        include_raw_content=False,
                        country="china",
                    )
                    for query in queries
                )
            )

        responses = await search_all("basic")
        query_result_counts = {
            query: len(response.get("results", []))
            for query, response in zip(queries, responses, strict=True)
        }
        deduped: dict[str, dict[str, Any]] = {}

        def merge_response(
            query: str,
            response: dict[str, Any],
            *,
            time_window: str | None = None,
        ) -> None:
            for rank, item in enumerate(response.get("results", []), start=1):
                url = canonical_url(str(item.get("url", "")))
                if not url:
                    continue
                candidate = dict(item)
                candidate["url"] = url
                candidate["source_grade"] = classify_source(url, company_short)
                candidate["matched_queries"] = [query]
                candidate["matched_time_windows"] = [time_window] if time_window else []
                candidate["best_query_rank"] = rank
                candidate["below_search_score_hint"] = (
                    float(item.get("score", 0)) < min_score
                )
                previous = deduped.get(url)
                if previous is None or float(candidate.get("score", 0)) > float(
                    previous.get("score", 0)
                ):
                    if previous:
                        candidate["matched_queries"] = sorted(
                            set(previous.get("matched_queries", [])) | {query}
                        )
                        candidate["matched_time_windows"] = sorted(
                            set(previous.get("matched_time_windows", []))
                            | ({time_window} if time_window else set())
                        )
                    deduped[url] = candidate
                else:
                    previous["matched_queries"] = sorted(
                        set(previous.get("matched_queries", [])) | {query}
                    )
                    previous["matched_time_windows"] = sorted(
                        set(previous.get("matched_time_windows", []))
                        | ({time_window} if time_window else set())
                    )
                    previous["best_query_rank"] = min(
                        int(previous.get("best_query_rank", rank)), rank
                    )

        for query, response in zip(queries, responses, strict=True):
            merge_response(query, response)

        selected = sorted(
            deduped.values(), key=lambda item: float(item.get("score", 0)), reverse=True
        )
        extract_urls = [item["url"] for item in selected]
        extracted: dict[str, str] = {}
        failed_extracts: dict[str, str] = {}
        for start in range(0, len(extract_urls), EXTRACT_BATCH_SIZE):
            response = await self.key_pool.request(
                "extract",
                urls=extract_urls[start : start + EXTRACT_BATCH_SIZE],
                extract_depth="basic",
                format="markdown",
            )
            for item in response.get("results", []):
                extracted[canonical_url(item.get("url", ""))] = item.get("raw_content", "")
            for item in response.get("failed_results", []):
                url = canonical_url(str(item.get("url", "")))
                failed_extracts[url] = str(item.get("error") or "extract_failed")
        for item in selected:
            item["raw_content"], item["truncated"] = sanitize_external_content(
                extracted.get(item["url"], "")
            )
            item["content_hash"] = hashlib.sha256(item["raw_content"].encode("utf-8")).hexdigest()
            item["trust_boundary"] = "external_untrusted"
            item["extract_error"] = failed_extracts.get(item["url"])
        payload = {
            "version": SEARCH_REPLAY_VERSION,
            "queries": queries,
            "query_result_counts": query_result_counts,
            "temporal_search": {
                "queries": [],
                "windows": [
                    {"start_year": start, "end_year": end}
                    for start, end in windows
                ],
                "result_counts": {},
                "strategy": "rank extracted publication/event dates; no year-specific queries",
            },
            "company_short_name": company_short,
            "manager_name": manager_name,
            "results": selected,
            "created_at": datetime.now(UTC).isoformat(),
            "mode": mode,
            "replay": False,
        }
        self.boundary.write_text(replay_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        return payload


PROMPT_INJECTION_MARKERS = re.compile(
    r"(忽略.{0,20}(系统|指令)|ignore.{0,20}(system|instruction)|调用.{0,20}工具|tool[_ -]?call)",
    re.IGNORECASE,
)


def contains_prompt_injection(text: str) -> bool:
    return bool(PROMPT_INJECTION_MARKERS.search(text))
