from __future__ import annotations

import re
from datetime import date, datetime, time
from pathlib import Path

from openpyxl import load_workbook

from fund_research_agent.domain.contracts import FundIdentity

REQUIRED_COLUMNS = (
    "基金代码",
    "基金简称",
    "基金经理",
    "任职日期",
    "离职日期",
    "成立日期",
    "基金公司",
    "是否有持仓明细数据",
)

SHARE_SUFFIX = re.compile(r"(?:[-_—\s]?[A-Z](?:类)?)$", re.IGNORECASE)


def _normalize_name(value: object) -> str:
    return re.sub(r"[\s\-_—·（）()]", "", str(value or "")).lower()


def _base_fund_name(value: object) -> str:
    return SHARE_SUFFIX.sub("", _normalize_name(value))


def _as_date(value: object) -> date | None:
    if value in (None, "", "至今", "在任"):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, time):
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"无法解析日期: {value!r}")


class FundUniverseAdapter:
    """只读使用公募主动基金全样本作为当前经理身份源。"""

    def __init__(self, workbook_path: Path, sheet_index: int = 1) -> None:
        self.workbook_path = workbook_path.resolve()
        if not self.workbook_path.is_file():
            raise FileNotFoundError(self.workbook_path)
        workbook = load_workbook(self.workbook_path, read_only=True, data_only=True)
        try:
            worksheet = workbook.worksheets[sheet_index]
            rows = worksheet.iter_rows(values_only=True)
            headers = tuple(str(value or "").strip() for value in next(rows))
            missing = [name for name in REQUIRED_COLUMNS if name not in headers]
            if missing:
                raise ValueError(f"基金全样本缺少列: {missing}")
            positions = {name: headers.index(name) for name in REQUIRED_COLUMNS}
            records: dict[str, dict[str, object]] = {}
            for row_number, row in enumerate(rows, start=2):
                symbol = str(row[positions["基金代码"]] or "").strip().upper()
                if not symbol:
                    continue
                if re.fullmatch(r"\d{6}", symbol):
                    symbol += ".OF"
                if symbol in records:
                    raise ValueError(f"基金全样本代码重复: {symbol} (row {row_number})")
                records[symbol] = {
                    "fund_symbol": symbol,
                    "fund_name": str(row[positions["基金简称"]] or "").strip(),
                    "manager_name": str(row[positions["基金经理"]] or "").strip(),
                    "manager_tenure_start": _as_date(row[positions["任职日期"]]),
                    "manager_tenure_end": _as_date(row[positions["离职日期"]]),
                    "found_date": _as_date(row[positions["成立日期"]]),
                    "company_full_name": str(row[positions["基金公司"]] or "").strip(),
                    "holdings_label": str(
                        row[positions["是否有持仓明细数据"]] or ""
                    ).strip(),
                }
        finally:
            workbook.close()
        self.records = records
        self._aliases: list[tuple[str, str, int]] = []
        for symbol, record in records.items():
            fund_name = _normalize_name(record["fund_name"])
            base_name = _base_fund_name(record["fund_name"])
            manager = _normalize_name(record["manager_name"])
            if fund_name:
                self._aliases.append((fund_name, symbol, 300))
            if base_name and base_name != fund_name:
                self._aliases.append((base_name, symbol, 250))
            if manager:
                self._aliases.append((manager, symbol, 100))

    def get(self, fund_symbol: str) -> dict[str, object] | None:
        return self.records.get(fund_symbol.strip().upper())

    def resolve(self, query: str, as_of: date | None = None) -> list[FundIdentity]:
        text = query.strip()
        # Chinese characters are ``\w`` in Python, so a word boundary incorrectly
        # rejects natural forms such as “给000628写报告”. Only adjacent digits are
        # unsafe for a six-digit fund code.
        symbol_match = re.search(r"(?<!\d)(\d{6}(?:\.OF)?)(?!\d)", text, re.IGNORECASE)
        if symbol_match:
            symbol = symbol_match.group(1).upper()
            if "." not in symbol:
                symbol += ".OF"
            candidates = [self.records[symbol]] if symbol in self.records else []
            confidence_by_symbol = {symbol: 1.0}
        else:
            normalized = _normalize_name(text)
            scored: dict[str, int] = {}
            for alias, symbol, priority in self._aliases:
                if not alias:
                    continue
                if alias == normalized:
                    score = priority + 1000
                elif alias in normalized:
                    score = priority + len(alias)
                elif len(normalized) >= 4 and normalized in alias:
                    score = priority - 50 + len(normalized)
                else:
                    continue
                scored[symbol] = max(scored.get(symbol, 0), score)
            symbols = sorted(
                scored,
                key=lambda symbol: (
                    -scored[symbol],
                    -len(str(self.records[symbol]["fund_name"])),
                    symbol,
                ),
            )[:20]
            candidates = [self.records[symbol] for symbol in symbols]
            confidence_by_symbol = {
                symbol: min(0.99, 0.70 + max(0, scored[symbol] - 100) / 2000)
                for symbol in symbols
            }
        cutoff = as_of or date.today()
        result = []
        for item in candidates:
            if item["manager_tenure_start"] and item["manager_tenure_start"] > cutoff:
                continue
            share_match = re.search(
                r"(?:[-_—\s]?)([A-Z])(?:类)?$", str(item["fund_name"]), re.IGNORECASE
            )
            result.append(
                FundIdentity(
                    fund_symbol=str(item["fund_symbol"]),
                    fund_name=str(item["fund_name"]),
                    share_class=share_match.group(1).upper() if share_match else None,
                    manager_name=str(item["manager_name"]),
                    manager_tenure_start=item["manager_tenure_start"],
                    manager_tenure_end=item["manager_tenure_end"],
                    company_full_name=str(item["company_full_name"]),
                    identity_confidence=confidence_by_symbol.get(
                        str(item["fund_symbol"]), 0.9
                    ),
                    identity_warnings=["基金经理及任期来自只读公募主动基金全样本"],
                )
            )
        return result

    def __len__(self) -> int:
        return len(self.records)
