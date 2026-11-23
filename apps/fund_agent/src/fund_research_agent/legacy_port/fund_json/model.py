from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path

JSON_TYPES = (
    "基金配置",
    "季度盈亏",
    "年度盈亏",
    "累计盈亏",
    "配置特征",
    "配置偏好",
    "配置跟踪",
    "投资能力",
    "基金规模",
    "基金换手",
    "季度运作报告",
    "数据看板",
)


@dataclass(frozen=True)
class FundSpec:
    fund_symbol: str
    fund_name: str
    manager_name: str
    tenure_start: date
    tenure_end: date | None
    enabled: bool = True
    exception_tags: tuple[str, ...] = ()

    @property
    def agent_name(self) -> str:
        return f"{self.manager_name}_{self.fund_name}"

    @property
    def root_key(self) -> str:
        return f"{self.fund_symbol}_{self.fund_name}"


def _date(value: str) -> date | None:
    value = value.strip()
    return date.fromisoformat(value) if value else None


def load_manifest(path: str | Path, *, enabled_only: bool = True) -> list[FundSpec]:
    specs: list[FundSpec] = []
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            enabled = row["enabled"].strip().lower() in {"1", "true", "yes"}
            spec = FundSpec(
                fund_symbol=row["fund_symbol"].strip(),
                fund_name=row["fund_name"].strip(),
                manager_name=row["manager_name"].strip(),
                tenure_start=_date(row["tenure_start"]) or date.min,
                tenure_end=_date(row["tenure_end"]),
                enabled=enabled,
                exception_tags=tuple(filter(None, row.get("exception_tags", "").split(";"))),
            )
            if enabled_only and not enabled:
                continue
            specs.append(spec)
    symbols = [item.fund_symbol for item in specs]
    if len(symbols) != len(set(symbols)):
        raise ValueError("manifest contains duplicate fund_symbol values")
    return specs
