from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from .model import JSON_TYPES, FundSpec, load_manifest

PERIOD_RE = re.compile(r"(?:19|20)\d{2}(?:Q[1-4]|(?:03|06|09|12)(?:30|31))")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _inventory(paths: list[Path]) -> list[dict[str, object]]:
    output = []
    ignored = {
        ".git",
        ".venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        "site",
        "output",
    }
    for root in paths:
        for path in sorted(
            item
            for item in root.rglob("*")
            if item.is_file() and not ignored.intersection(item.parts)
        ):
            stat = path.stat()
            output.append(
                {
                    "path": str(path),
                    "size": stat.st_size,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
                    "sha256": _sha256(path),
                }
            )
    return output


def _shape(value: Any) -> Any:
    if isinstance(value, dict):
        if len(value) > 20 and all(not isinstance(child, (dict, list)) for child in value.values()):
            return {"<MAP_KEY>": sorted({str(_shape(child)) for child in value.values()})}
        output = {}
        for key, child in value.items():
            normalized = re.sub(r"(?:19|20)\d{2}Q[1-4]", "<QUARTER>", str(key))
            normalized = re.sub(r"(?:19|20)\d{6}", "<DATE>", normalized)
            normalized = re.sub(r"(?:19|20)\d{2}年", "<YEAR>", normalized)
            normalized = re.sub(r"^\d{6}\.OF_.+$", "<FUND_ROOT>", normalized)
            output.setdefault(normalized, _shape(child))
        return output
    if isinstance(value, list):
        samples = []
        seen = set()
        for child in value:
            signature = json.dumps(_shape(child), ensure_ascii=False, sort_keys=True)
            if signature not in seen:
                samples.append(json.loads(signature))
                seen.add(signature)
        return {"type": "array", "item_shapes": samples[:5]}
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    return "string"


def _periods(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            found.extend(PERIOD_RE.findall(str(key)))
            found.extend(_periods(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_periods(child))
    elif isinstance(value, str):
        found.extend(PERIOD_RE.findall(value))
    return found


def _legacy_path(root: Path, spec: FundSpec, kind: str) -> Path:
    return root / f"{spec.agent_name}_{kind}.txt"


def _lake_boundaries(database: Path, specs: list[FundSpec]) -> dict[str, dict[str, str | None]]:
    con = duckdb.connect(str(database), read_only=True)
    result = {}
    for spec in specs:
        row = con.execute(
            """
            SELECT
              (SELECT min(found_date) FROM fund_profiles WHERE symbol = ?),
              (SELECT min(nav_date) FROM fund_nav WHERE symbol = ?),
              (SELECT min(report_period) FROM fund_holdings WHERE fund_symbol = ?),
              (SELECT max(report_period) FROM fund_holdings WHERE fund_symbol = ?)
            """,
            [spec.fund_symbol] * 4,
        ).fetchone()
        result[spec.fund_symbol] = {
            "fund_found": str(row[0]) if row[0] else None,
            "first_nav": str(row[1]) if row[1] else None,
            "first_holding": str(row[2]) if row[2] else None,
            "last_holding": str(row[3]) if row[3] else None,
        }
    con.close()
    return result


def audit_legacy(
    *,
    legacy_json: str | Path,
    legacy_source: str | Path,
    dashboard_source: str | Path,
    manifest: str | Path,
    database: str | Path,
    output: str | Path,
) -> dict[str, object]:
    legacy_json = Path(legacy_json)
    legacy_source = Path(legacy_source)
    dashboard_source = Path(dashboard_source)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    specs = load_manifest(manifest)
    boundaries = _lake_boundaries(Path(database), specs)
    contracts: dict[str, list[Any]] = defaultdict(list)
    rows = []
    missing = []
    for spec in specs:
        for kind in JSON_TYPES:
            path = _legacy_path(legacy_json, spec, kind)
            if not path.exists():
                missing.append(str(path))
                continue
            text = path.read_text(encoding="utf-8-sig")
            is_json = True
            try:
                value = json.loads(text)
                contracts[kind].append(_shape(value))
                periods = sorted(set(_periods(value)))
            except json.JSONDecodeError:
                is_json = False
                contracts[kind].append({"type": "legacy_text"})
                periods = sorted(set(PERIOD_RE.findall(text)))
            rows.append(
                {
                    "fund_symbol": spec.fund_symbol,
                    "agent_name": spec.agent_name,
                    "type": kind,
                    "file": path.name,
                    "is_json": is_json,
                    "bytes": path.stat().st_size,
                    "period_start": periods[0] if periods else None,
                    "period_end": periods[-1] if periods else None,
                    "period_count": len(periods),
                }
            )
    if missing:
        raise FileNotFoundError(f"legacy corpus is missing {len(missing)} required files")

    unique_contracts = {}
    for kind, shapes in contracts.items():
        counter = Counter(json.dumps(shape, ensure_ascii=False, sort_keys=True) for shape in shapes)
        unique_contracts[kind] = [
            {"fund_count": count, "shape": json.loads(shape)}
            for shape, count in counter.most_common()
        ]
    (output / "legacy-contracts.json").write_text(
        json.dumps(unique_contracts, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output / "legacy-periods.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output / "source-freeze.json").write_text(
        json.dumps(
            {
                "created_at": datetime.now(UTC).isoformat(),
                "legacy_json_files": _inventory([legacy_json]),
                "legacy_source_files": _inventory([legacy_source]),
                "dashboard_source_files": _inventory([dashboard_source]),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (output / "lake-boundaries.json").write_text(
        json.dumps(boundaries, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lineage = """# 基金报告 JSON 数据血缘

| 输出 | 旧生成证据 | 新数据湖来源 | 关键口径 |
|---|---|---|---|
| 基金配置/偏好/跟踪 | 01BI `基金JSON数据.py` + 持仓明细 Excel | `fund_holdings`, `fund_scale`, `industry_members`, `industry_taxonomy` | 配置按净值权重；特征加权按股票市值权重 |
| 季度/年度/累计盈亏 | 01BI 持仓盈亏估算矩阵 | `fund_holdings`, `daily_bars`, `corporate_actions` | 自然季度闭区间 VWAP；卖出盈亏归属下一季；含现金分红 |
| 配置特征 | 01BI `process_fund_data` | 持仓、估值、财务、分红、行业 | PE/PB 截断，ROE 截断，股票市值权重加权 |
| 基金规模 | 01BI `基金数据.xlsx` | `fund_scale` | 单基金规模；经理规模按报告期在管基金合计 |
| 基金换手 | 01BI `基金数据.xlsx` | `fund_annual_turnover` | 优先冻结的 Wind 年度换手，生成不读 Excel |
| 季度运作报告 | 旧 01BI 主生成链没有稳定来源；现有旧素材为后补 | `fund_reports` | 新复刻模块新增产物，按指定经理研究窗口过滤 |
| 数据看板 | `/path/to/local-user/项目/资产池数据看板` | `fund_nav` + 显式经理任期 | 同一截止日计算收益、波动与回撤 |

## 时间窗口

统一规则是指定经理任职季度至任职结束/`as_of`，再由各数据集首个有效报告期约束。配置和特征只使用半年报、年报。异常基金仅记录，不产生基金名特例。
"""
    (output / "DATA_LINEAGE.md").write_text(lineage, encoding="utf-8")
    summary = {"funds": len(specs), "files": len(rows), "missing": missing}
    (output / "audit-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary
