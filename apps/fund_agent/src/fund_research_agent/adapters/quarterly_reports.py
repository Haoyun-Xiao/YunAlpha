from __future__ import annotations

import concurrent.futures
import hashlib
import threading
import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pymupdf

from fund_research_agent.paths import WriteBoundary, sha256_file

EXTRACTOR_VERSION = "sections-v2"
_FUND_LOCKS: dict[str, threading.RLock] = {}
_LOCK_GUARD = threading.Lock()

PDF_TEMPLATE = "https://pdf.dfcfw.com/pdf/H2_{rid}_1.pdf"

_OP_ANALYSIS_PATTERNS = (
    re.compile(r"^4\.4(?:\.[0-9])?\s*报告期内基金(?:的)?投资策略和运作分析", re.M),
    re.compile(r"报告期内基金(?:的)?投资策略和运作分析"),
)
_OUTLOOK_PATTERNS = (
    re.compile(r"^4\.5\s*管理人对宏观经济、证券市场及行业走势的?简要?展望", re.M),
    re.compile(r"^4\.4\.[0-9]\s*管理人对宏观经济、证券市场及行业走势的?简要?展望", re.M),
    re.compile(r"管理人对宏观经济、证券市场及行业走势的?简要?展望"),
)


@dataclass(frozen=True)
class QuarterlyReportResult:
    path: Path | None
    records: int
    source: str
    warning: str | None = None


def _report_kind(title: str) -> tuple[int, int] | None:
    match = re.search(r"(20\d{2})年(?:第)?([1-4一二三四])季度报告", title)
    if match:
        quarter = {"1": 1, "2": 2, "3": 3, "4": 4, "一": 1, "二": 2, "三": 3, "四": 4}[
            match.group(2)
        ]
        return int(match.group(1)), quarter
    match = re.search(r"(20\d{2})年(?:半年度|中期)报告", title)
    if match:
        return int(match.group(1)), 5
    match = re.search(r"(20\d{2})年年度报告", title)
    if match:
        return int(match.group(1)), 6
    return None


def _report_metadata(year: int, quarter: int) -> tuple[date, str, str]:
    labels = {1: "一季报", 2: "二季报", 3: "三季报", 4: "四季报", 5: "半年报", 6: "年报"}
    month = {1: 3, 2: 6, 3: 9, 4: 12, 5: 6, 6: 12}[quarter]
    day = {3: 31, 6: 30, 9: 30, 12: 31}[month]
    report_type = "quarterly" if quarter <= 4 else "half_year" if quarter == 5 else "annual"
    return date(year, month, day), f"{year}年{labels[quarter]}", report_type


def _extract_section(text: str, patterns: tuple[re.Pattern[str], ...]) -> str:
    lines = text.splitlines()
    start: int | None = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if any(pattern.match(stripped) for pattern in patterns):
            if "...." in line:
                continue
            start = index
            break
    if start is None:
        return ""
    body = [lines[start]]
    next_section = re.compile(r"^(\d+\.\d+(?:\.\d+)?|§\s*\d+|附录)(?:\s*$|\s*[^\d\s%])")
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if next_section.match(stripped) and not any(pattern.match(stripped) for pattern in patterns):
            break
        body.append(line)
    value = "\n".join(body)
    value = re.sub(r"^.*?证券投资基金20\d{2}年.*?报告\s*$", "", value, flags=re.M)
    value = re.sub(r"^第\s*\d+\s*页\s*共\s*\d+\s*页\s*$", "", value, flags=re.M)
    return value.strip()


class QuarterlyReportProvider:
    """Fetch missing official report sections into the new project's writable cache."""

    def __init__(
        self, cache_root: Path, boundary: WriteBoundary, pdf_workers: int = 2, *,
        frozen_root: Path | None = None, allow_live: bool = True,
    ) -> None:
        self.cache_root = boundary.ensure_dir(cache_root)
        self.boundary = boundary
        self.pdf_workers = pdf_workers
        self.frozen_root = frozen_root
        self.allow_live = allow_live

    @staticmethod
    def _announcement_list(code: str) -> list[dict]:
        import akshare as ak

        frame = ak.fund_announcement_report_em(symbol=code)
        results = []
        for row in frame.to_dict(orient="records"):
            title = str(row.get("公告标题") or "")
            if "摘要" in title:
                continue
            kind = _report_kind(title)
            report_id = str(row.get("报告ID") or "").strip()
            if kind is None or not report_id:
                continue
            results.append(
                {
                    "year": kind[0],
                    "quarter": kind[1],
                    "title": title,
                    "announce_date": str(row.get("公告日期") or "")[:10],
                    "report_id": report_id,
                }
            )
        return results

    @staticmethod
    def _download_pdf(report_id: str) -> bytes:
        for attempt in range(4):
            try:
                response = httpx.get(
                    PDF_TEMPLATE.format(rid=report_id),
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=60,
                    follow_redirects=True,
                )
                response.raise_for_status()
                return response.content
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError):
                if attempt == 3:
                    raise
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError("unreachable")

    def ensure(self, fund_symbol, fund_name, tenure_start, as_of) -> QuarterlyReportResult:
        with _LOCK_GUARD:
            lock = _FUND_LOCKS.setdefault(fund_symbol, threading.RLock())
        with lock:
            return self._ensure(fund_symbol, fund_name, tenure_start, as_of)

    def _ensure(
        self,
        fund_symbol: str,
        fund_name: str,
        tenure_start: date | None,
        as_of: date,
    ) -> QuarterlyReportResult:
        code = fund_symbol.split(".", 1)[0]
        fund_dir = self.boundary.ensure_dir(self.cache_root / code)
        start = tenure_start or date(2015, 1, 1)
        key = hashlib.sha256(f"{fund_symbol}:{start}:{as_of}:{EXTRACTOR_VERSION}".encode()).hexdigest()[:20]
        manifest_path = fund_dir / f"reports-{key}.json"
        candidates = [manifest_path]
        if self.frozen_root is not None:
            candidates.append(self.frozen_root / code / manifest_path.name)
        for candidate in candidates:
            if not candidate.is_file():
                continue
            try:
                cached = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if (
                cached.get("extractor_version") == EXTRACTOR_VERSION
                and cached.get("requested_as_of", "") == as_of.isoformat()
                and cached.get("tenure_start", "9999-12-31") <= start.isoformat()
                and cached.get("records")
                and not cached.get("warning")
            ):
                return QuarterlyReportResult(candidate, len(cached["records"]), "cache", None)

        if not self.allow_live:
            return QuarterlyReportResult(None, 0, "frozen_inputs", "quarterly_report_replay_missing")

        try:
            announcements = self._announcement_list(code)
        except Exception as exc:
            # An existing PDF catalogue can be reparsed offline; never reuse its unfiltered text.
            old_path = fund_dir / "reports.json"
            old = json.loads(old_path.read_text(encoding="utf-8")) if old_path.exists() else {}
            announcements = []
            for record in old.get("records", []):
                period = date.fromisoformat(record["report_period"])
                kind = record.get("report_type")
                announcements.append({"year": period.year, "quarter": 5 if kind == "half_year" else 6 if kind == "annual" else (period.month - 1)//3+1, "title": record["report_label"], "announce_date": record.get("announce_date", ""), "report_id": record.get("source_uri", "").split("H2_")[-1].split("_1.pdf")[0]})
            if not announcements:
                return QuarterlyReportResult(None, 0, "eastmoney", f"report_list_error:{type(exc).__name__}:{exc}")

        selected = []
        for item in announcements:
            period, label, report_type = _report_metadata(item["year"], item["quarter"])
            if period < start or period > as_of:
                continue
            try:
                announced = date.fromisoformat(item["announce_date"])
            except ValueError:
                announced = None
            if announced is None or announced > as_of:
                continue
            selected.append(item | {"report_period": period, "report_label": label, "report_type": report_type})

        diagnostics: list[dict] = []
        def fetch_one(item: dict) -> dict | None:
            pdf_path = fund_dir / "pdf" / f"{item['report_id']}.pdf"
            try:
                if not pdf_path.exists():
                    self.boundary.write_bytes(pdf_path, self._download_pdf(item["report_id"]))

                def pdf_text() -> str:
                    document = pymupdf.open(pdf_path)
                    try:
                        return "\n".join(page.get_text() for page in document)
                    finally:
                        document.close()

                try:
                    text = pdf_text()
                except Exception:  # noqa: BLE001
                    self.boundary.write_bytes(pdf_path, self._download_pdf(item["report_id"]))
                    text = pdf_text()
                strategy = _extract_section(text, _OP_ANALYSIS_PATTERNS)
                outlook = _extract_section(text, _OUTLOOK_PATTERNS)
                if not strategy and not outlook:
                    diagnostics.append({"report_id": item["report_id"], "status": "target_sections_missing"})
                    return None
                diagnostics.append({"report_id": item["report_id"], "status": "completed", "strategy_found": bool(strategy), "outlook_found": bool(outlook)})
                return {
                    "fund_symbol": fund_symbol,
                    "fund_name": fund_name,
                    "report_period": item["report_period"].isoformat(),
                    "report_label": item["report_label"],
                    "report_type": item["report_type"],
                    "announce_date": item["announce_date"],
                    "strategy_analysis": strategy,
                    "outlook": outlook,
                    "source": "eastmoney_pdf",
                    "source_uri": PDF_TEMPLATE.format(rid=item["report_id"]),
                    "source_hash": sha256_file(pdf_path),
                }
            except Exception as exc:
                diagnostics.append({"report_id": item["report_id"], "status": "failed", "error": f"{type(exc).__name__}:{exc}"})
                return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.pdf_workers) as pool:
            records = [record for record in pool.map(fetch_one, selected) if record]
        records.sort(key=lambda item: (item["report_period"], item["report_label"]))
        failed_reports = len(selected) - len(records)
        warning = (
            "quarterly_report_extract_empty"
            if not records
            else f"quarterly_report_partial:{failed_reports}/{len(selected)}"
            if failed_reports
            else None
        )
        payload = {
            "extractor_version": EXTRACTOR_VERSION,
            "diagnostics": diagnostics,
            "fund_symbol": fund_symbol,
            "fund_name": fund_name,
            "requested_as_of": as_of.isoformat(),
            "tenure_start": start.isoformat(),
            "fetched_at": datetime.now(UTC).isoformat(),
            "announcements_considered": len(selected),
            "failed_reports": failed_reports,
            "warning": warning,
            "records": records,
        }
        self.boundary.write_text(manifest_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        latest = fund_dir / "reports.json"
        latest_asof = json.loads(latest.read_text(encoding="utf-8")).get("requested_as_of", "") if latest.exists() else ""
        if as_of.isoformat() >= latest_asof:
            self.boundary.write_text(latest, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        return QuarterlyReportResult(manifest_path if records else None, len(records), "eastmoney", warning)
