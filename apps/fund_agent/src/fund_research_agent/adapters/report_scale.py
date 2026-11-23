"""Bounded, per-fund scale supplementation; never writes to the source lake."""

from __future__ import annotations

import json
import math
import re
from datetime import date
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from fund_research_agent.paths import WriteBoundary


def parse_scale_history(text: str) -> list[dict]:
    match = re.search(r'content:\s*"(.*?)",', text, re.S)
    if not match:
        return []
    html = json.loads('"' + match.group(1) + '"')
    rows = []
    for tr in BeautifulSoup(html, "html.parser").select("tr"):
        cells = [td.get_text(strip=True) for td in tr.select("td")]
        if len(cells) < 5 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", cells[0]):
            continue
        if cells[0][5:] not in {"03-31", "06-30", "09-30", "12-31"}:
            continue
        try:
            value = float(cells[4].replace(",", ""))
        except ValueError:
            continue
        if math.isfinite(value) and value >= 0:
            rows.append({"report_period": cells[0], "scale_yi": value})
    return rows


class ReportScaleProvider:
    def __init__(self, boundary: WriteBoundary):
        self.boundary = boundary

    @staticmethod
    def needs_supplement(coverage: dict) -> bool:
        scale = coverage.get("scale", {})
        expected = (
            scale.get("expected_report_period") or coverage.get("holdings", {}).get("to") or ""
        )
        return (scale.get("to") or "")[:10] < expected[:10]

    def ensure(
        self, symbols: list[str], as_of: date, output: Path, pdf_catalogue: Path | None = None
    ) -> dict:
        records, errors = [], []
        # Current public histories have no per-row announcement date. Historical
        # requests may only use the dated PDF catalogue below.
        if as_of >= date.today():
            with httpx.Client(
                timeout=20,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://fundf10.eastmoney.com/"},
            ) as client:
                for symbol in symbols:
                    try:
                        response = client.get(
                            "https://fundf10.eastmoney.com/FundArchivesDatas.aspx",
                            params={"type": "gmbd", "code": symbol.split(".")[0], "rt": "0.1"},
                        )
                        response.raise_for_status()
                        records.extend(
                            {
                                **row,
                                "symbol": symbol,
                                "source": "eastmoney_gmbd",
                                "available_at": date.today().isoformat(),
                                "scope": "share_class",
                            }
                            for row in parse_scale_history(response.text)
                            if row["report_period"] <= as_of.isoformat()
                        )
                    except (httpx.HTTPError, ValueError) as exc:
                        errors.append({"symbol": symbol, "error": str(exc)})
        # A PDF column is accepted only when its share-class label is explicit;
        # never guess that the first two nearby numbers mean A and C.
        if pdf_catalogue and pdf_catalogue.exists():
            import pymupdf

            catalogue = json.loads(pdf_catalogue.read_text(encoding="utf-8"))
            covered = {(row["symbol"], row["report_period"]) for row in records}
            for report in catalogue.get("records", []):
                if all((symbol, report["report_period"]) in covered for symbol in symbols):
                    continue
                pdf_id = report.get("source_uri", "").split("H2_")[-1].split("_1.pdf")[0]
                path = pdf_catalogue.parent / "pdf" / f"{pdf_id}.pdf"
                if not path.exists() or report.get("announce_date", "9999") > as_of.isoformat():
                    continue
                try:
                    with pymupdf.open(path) as document:
                        for page in document:
                            if "期末基金资产净值" not in page.get_text():
                                continue
                            for table in page.find_tables().tables:
                                rows = table.extract()
                                for row in rows:
                                    if not row or "期末基金资产净值" not in str(row[0]):
                                        continue
                                    for index, cell in enumerate(row[1:], 1):
                                        label = " ".join(
                                            str(r[index] or "") for r in rows[:4] if len(r) > index
                                        )
                                        matched = [
                                            symbol
                                            for symbol in symbols
                                            if symbol.split(".")[0] in label
                                        ]
                                        if len(matched) != 1 or cell is None:
                                            continue
                                        value = (
                                            float(str(cell).replace(",", "").replace("\n", ""))
                                            / 1e8
                                        )
                                        records.append(
                                            {
                                                "symbol": matched[0],
                                                "report_period": report["report_period"],
                                                "scale_yi": value,
                                                "source": "eastmoney_pdf_labelled_column",
                                                "available_at": report["announce_date"],
                                                "scope": "share_class",
                                            }
                                        )
                except (ValueError, RuntimeError) as exc:
                    errors.append({"pdf": pdf_id, "error": str(exc)})
        value = {"records": records, "errors": errors, "requested_as_of": as_of.isoformat()}
        self.boundary.write_text(output, json.dumps(value, ensure_ascii=False, indent=2))
        return value
