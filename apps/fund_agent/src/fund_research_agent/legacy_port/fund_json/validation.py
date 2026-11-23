from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from statistics import median
from typing import Any

from .model import JSON_TYPES, FundSpec, load_manifest

PERCENT_RE = re.compile(r"^[↑↓]?\s*(-?\d+(?:\.\d+)?)%$")
IDENTITY_KEYS = (
    "报告期",
    "股票代码",
    "股票名称",
    "申万一级",
    "申万二级",
    "名称",
    "个股",
    "行业",
    "大类",
)


def _number(value: Any) -> tuple[float, bool] | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value), False
    if isinstance(value, str):
        match = PERCENT_RE.match(value.strip())
        if match:
            return float(match.group(1)), True
        try:
            parsed = float(value)
            return (parsed, False) if math.isfinite(parsed) else None
        except ValueError:
            return None
    return None


def _flatten(
    value: Any, path: tuple[str, ...] = (), *, normalize_period_keys: bool = False
) -> dict[tuple[str, ...], Any]:
    if isinstance(value, dict):
        output = {}
        for key, child in value.items():
            if path == () and key == "基金经理":
                continue
            key = str(key)
            if path == () and re.match(r"^\d{6}\.OF_", key):
                key = "<FUND_ROOT>"
            if normalize_period_keys:
                key = re.sub(r"(?:19|20)\d{6}", "<DATE>", key)
                key = re.sub(r"(?:19|20)\d{2}Q[1-4]", "<QUARTER>", key)
            output.update(
                _flatten(child, path + (key,), normalize_period_keys=normalize_period_keys)
            )
        return output
    if isinstance(value, list):
        output = {}
        seen: dict[str, int] = {}
        for index, child in enumerate(value):
            identity = None
            if isinstance(child, dict):
                if "股票代码" in child:
                    values = [str(child[key]) for key in ("报告期", "股票代码") if key in child]
                else:
                    values = [str(child[key]) for key in IDENTITY_KEYS if key in child]
                identity = "|".join(values) if values else None
            identity = identity or f"#{index}"
            occurrence = seen.get(identity, 0)
            seen[identity] = occurrence + 1
            if occurrence:
                identity = f"{identity}@{occurrence}"
            output.update(
                _flatten(child, path + (identity,), normalize_period_keys=normalize_period_keys)
            )
        return output
    return {path: value}


def _read(path: Path, kind: str) -> tuple[Any, bool]:
    text = path.read_text(encoding="utf-8-sig")
    if kind == "投资能力":
        return text.replace("\r\n", "\n"), False
    try:
        return json.loads(text), True
    except json.JSONDecodeError:
        if kind == "季度运作报告":
            return text.replace("\r\n", "\n"), False
        raise


def _all_text(value: Any) -> str:
    if isinstance(value, dict):
        return "\n".join(f"{key}\n{_all_text(child)}" for key, child in value.items())
    if isinstance(value, list):
        return "\n".join(_all_text(child) for child in value)
    return value if isinstance(value, str) else ""


def _shingle_similarity(left: str, right: str, width: int = 8) -> float:
    if left == right:
        return 1.0
    left = re.sub(r"\s+", "", left)
    right = re.sub(r"\s+", "", right)
    if not left or not right:
        return 0.0
    a = {left[index : index + width] for index in range(max(1, len(left) - width + 1))}
    b = {right[index : index + width] for index in range(max(1, len(right) - width + 1))}
    return len(a & b) / len(a | b) if a | b else 0.0


def _pnl_metrics(pairs: list[tuple[Any, Any]]) -> dict[str, float | None]:
    relative = []
    signs = []
    for old, new in pairs:
        left = _number(old)
        right = _number(new)
        if left is None or right is None or left[1] or right[1] or abs(left[0]) < 10:
            continue
        relative.append(abs(right[0] - left[0]) / abs(left[0]))
        signs.append((left[0] >= 0) == (right[0] >= 0))
    return {
        "material_values": len(relative),
        "median_relative_error": median(relative) if relative else None,
        "sign_agreement": sum(signs) / len(signs) if signs else None,
    }


def _compare(kind: str, old: Any, new: Any) -> dict[str, Any]:
    if kind == "投资能力":
        similarity = SequenceMatcher(None, old, new).ratio()
        required = ("象限一", "象限二", "象限三", "象限四", "从未进入前五的行业")
        complete = all(label in new for label in required)
        return {
            "old_leaves": 1,
            "common_leaves": 1,
            "missing_leaves": 0,
            "extra_leaves": 0,
            "coverage": 1.0,
            "exact_rate": float(old == new),
            "close_rate": similarity,
            "text_similarity": similarity,
            "passed": complete,
        }
    if kind == "季度运作报告":
        old_text = " ".join(_all_text(old).split())
        new_text = " ".join(_all_text(new).split())
        similarity = _shingle_similarity(old_text, new_text) if old_text else 1.0
        old_labels = set(re.findall(r"20\d{2}年(?:一|二|三|四|半|年)[^\s]{0,3}报", old_text))
        new_labels = set(re.findall(r"20\d{2}年(?:一|二|三|四|半|年)[^\s]{0,3}报", new_text))
        label_coverage = len(old_labels & new_labels) / len(old_labels) if old_labels else 1.0
        return {
            "old_leaves": len(old_labels),
            "common_leaves": len(old_labels & new_labels),
            "missing_leaves": len(old_labels - new_labels),
            "extra_leaves": len(new_labels - old_labels),
            "coverage": label_coverage,
            "exact_rate": float(old_text == new_text),
            "close_rate": similarity,
            "text_similarity": similarity,
            "passed": bool(new_labels),
        }
    if kind == "配置跟踪":
        new_text = json.dumps(new, ensure_ascii=False)
        required = ("大类配置(", "前五大行业(", "前十大个股(", "配置变化(")
        complete = all(label in new_text for label in required)
        old_flat = _flatten(old, normalize_period_keys=True)
        new_flat = _flatten(new, normalize_period_keys=True)
        common = set(old_flat) & set(new_flat)
        return {
            "old_leaves": len(old_flat),
            "common_leaves": len(common),
            "missing_leaves": len(set(old_flat) - set(new_flat)),
            "extra_leaves": len(set(new_flat) - set(old_flat)),
            "coverage": len(common) / len(old_flat) if old_flat else 1.0,
            "exact_rate": sum(old_flat[path] == new_flat[path] for path in common) / len(old_flat)
            if old_flat
            else 1.0,
            "close_rate": len(common) / len(old_flat) if old_flat else 1.0,
            "passed": complete,
        }
    if kind == "配置偏好":

        def find_lists(document: Any) -> tuple[set[str], set[str]]:
            root = next((value for key, value in document.items() if key != "基金经理"), {})
            industries = {
                str(item.get("申万一级")) for item in root.get("行业前五出现次数", [])[:10]
            }
            stocks = {str(item.get("股票名称")) for item in root.get("个股前十出现次数", [])[:20]}
            return industries, stocks

        old_industries, old_stocks = find_lists(old)
        new_industries, new_stocks = find_lists(new)
        industry_overlap = (
            len(old_industries & new_industries) / len(old_industries) if old_industries else 1.0
        )
        stock_overlap = len(old_stocks & new_stocks) / len(old_stocks) if old_stocks else 1.0
        return {
            "old_leaves": len(old_industries) + len(old_stocks),
            "common_leaves": len(old_industries & new_industries) + len(old_stocks & new_stocks),
            "missing_leaves": len(old_industries - new_industries) + len(old_stocks - new_stocks),
            "extra_leaves": len(new_industries - old_industries) + len(new_stocks - old_stocks),
            "coverage": min(industry_overlap, stock_overlap),
            "exact_rate": 0.0,
            "close_rate": min(industry_overlap, stock_overlap),
            "industry_overlap": industry_overlap,
            "stock_overlap": stock_overlap,
            "passed": industry_overlap >= 0.7 and stock_overlap >= 0.65,
        }
    normalize_period_keys = kind == "配置跟踪"
    old_flat = _flatten(old, normalize_period_keys=normalize_period_keys)
    new_flat = _flatten(new, normalize_period_keys=normalize_period_keys)
    common = sorted(set(old_flat) & set(new_flat))
    missing = sorted(set(old_flat) - set(new_flat))
    extra = sorted(set(new_flat) - set(old_flat))
    exact = 0
    close = 0
    percentage_checks = []
    weight_percentage_checks = []
    generic_checks = []
    text_checks = []
    pairs = []
    for path in common:
        left, right = old_flat[path], new_flat[path]
        pairs.append((left, right))
        if left == right:
            exact += 1
            close += 1
            numeric = _number(left)
            if numeric is not None:
                if numeric[1]:
                    percentage_checks.append(True)
                    if path[-1] in {"股票仓位", "前十大个股集中度"}:
                        weight_percentage_checks.append(True)
                else:
                    generic_checks.append(True)
            continue
        ln, rn = _number(left), _number(right)
        if ln is not None and rn is not None:
            if ln[1] and rn[1]:
                ok = abs(ln[0] - rn[0]) <= 1.0
                percentage_checks.append(ok)
                if path[-1] in {"股票仓位", "前十大个股集中度"}:
                    weight_percentage_checks.append(ok)
            else:
                denominator = max(abs(ln[0]), 2.0)
                ok = abs(ln[0] - rn[0]) / denominator <= 0.15
                generic_checks.append(ok)
            close += int(ok)
        elif isinstance(left, str) and isinstance(right, str):
            similarity = SequenceMatcher(
                None, " ".join(left.split()), " ".join(right.split())
            ).ratio()
            text_checks.append(similarity)
            close += int(similarity >= 0.98)
    coverage = len(common) / len(old_flat) if old_flat else 1.0
    result: dict[str, Any] = {
        "old_leaves": len(old_flat),
        "common_leaves": len(common),
        "missing_leaves": len(missing),
        "extra_leaves": len(extra),
        "coverage": coverage,
        "exact_rate": exact / len(old_flat) if old_flat else 1.0,
        "close_rate": close / len(old_flat) if old_flat else 1.0,
        "percentage_within_1pp": sum(percentage_checks) / len(percentage_checks)
        if percentage_checks
        else None,
        "weight_percentage_within_1pp": sum(weight_percentage_checks)
        / len(weight_percentage_checks)
        if weight_percentage_checks
        else None,
        "generic_within_15pct": sum(generic_checks) / len(generic_checks)
        if generic_checks
        else None,
        "text_similarity": median(text_checks) if text_checks else None,
        "missing_examples": ["/".join(item) for item in missing[:10]],
        "extra_examples": ["/".join(item) for item in extra[:10]],
    }
    if kind in {"季度盈亏", "年度盈亏", "累计盈亏"}:
        result.update(_pnl_metrics(pairs))
        result["passed"] = bool(
            coverage >= 0.75
            and (result["median_relative_error"] is None or result["median_relative_error"] <= 0.15)
            and (result["sign_agreement"] is None or result["sign_agreement"] >= 0.95)
        )
    elif kind == "基金配置":
        percentage_gate = (
            (result["percentage_within_1pp"] or 0) >= 0.85
            if coverage >= 0.75
            else (result["percentage_within_1pp"] or 0) >= 0.95
        )
        result["passed"] = bool(coverage >= 0.5 and percentage_gate)
    elif kind == "配置特征":
        result["passed"] = bool(
            coverage >= 0.95 and (result["weight_percentage_within_1pp"] or 0) >= 0.9
        )
    elif kind == "基金规模":
        fund_errors = []
        manager_errors = []
        for path in common:
            left, right = _number(old_flat[path]), _number(new_flat[path])
            if not left or not right or left[1] or right[1]:
                continue
            error = abs(left[0] - right[0]) / max(abs(left[0]), 0.01)
            (fund_errors if path[-1] == "基金规模" else manager_errors).append(error)
        result["fund_scale_within_2pct"] = (
            sum(value <= 0.02 for value in fund_errors) / len(fund_errors) if fund_errors else None
        )
        result["manager_scale_within_5pct"] = (
            sum(value <= 0.05 for value in manager_errors) / len(manager_errors)
            if manager_errors
            else None
        )
        result["passed"] = bool(coverage >= 0.95 and (result["fund_scale_within_2pct"] or 0) >= 0.9)
        result["manager_scale_status"] = (
            "pass"
            if (result["manager_scale_within_5pct"] or 0) >= 0.8
            else "lake_manager_tenure_coverage_gap"
        )
    elif kind == "基金换手":
        deviations = []
        for left, right in pairs:
            ln, rn = _number(left), _number(right)
            if ln and rn and not ln[1] and not rn[1]:
                deviations.append(abs(ln[0] - rn[0]))
        result["max_absolute_error"] = max(deviations, default=None)
        result["passed"] = coverage >= 0.95 and (not deviations or max(deviations) <= 0.02)
    elif kind == "数据看板":
        historical = []
        for path in common:
            if not any(re.search(r"20(?:1[6-9]|2[0-5])年收益", part) for part in path):
                continue
            left, right = _number(old_flat[path]), _number(new_flat[path])
            if left and right:
                historical.append(abs(left[0] - right[0]))
        result["historical_annual_within_0_2pp"] = (
            sum(value <= 0.2 for value in historical) / len(historical) if historical else None
        )
        result["passed"] = bool(
            coverage >= 0.95 and (result["historical_annual_within_0_2pp"] or 0) >= 0.9
        )
    else:
        result["passed"] = coverage >= 0.95 and result["close_rate"] >= 0.8
    return result


def _internal_consistency(candidate: Path, spec: FundSpec) -> tuple[bool, list[str]]:
    errors = []
    try:
        q = json.loads((candidate / f"{spec.agent_name}_季度盈亏.txt").read_text())
        a = json.loads((candidate / f"{spec.agent_name}_年度盈亏.txt").read_text())
        c = json.loads((candidate / f"{spec.agent_name}_累计盈亏.txt").read_text())
        quarterly = q[spec.root_key]["季度盈亏(万元)"]
        annual = a[spec.root_key]["年度盈亏(万元)"]
        cumulative = c[spec.root_key]["累计盈亏(万元)"]["全阶段累计"]
        rebuilt_annual: dict[str, dict[str, dict[str, int]]] = {}
        rebuilt_cumulative = {"大类": {}, "一级": {}, "个股": {}}
        for quarter, levels in quarterly.items():
            year = f"{quarter[:4]}年"
            yearly = rebuilt_annual.setdefault(year, {"大类": {}, "一级": {}, "个股": {}})
            for level, values in levels.items():
                for label, value in values.items():
                    yearly[level][label] = yearly[level].get(label, 0) + value
                    rebuilt_cumulative[level][label] = (
                        rebuilt_cumulative[level].get(label, 0) + value
                    )
        if rebuilt_annual != annual:
            errors.append("年度盈亏不等于季度盈亏汇总")
        if rebuilt_cumulative != cumulative:
            errors.append("累计盈亏不等于季度盈亏汇总")
    except Exception as exc:
        errors.append(f"勾稽检查异常: {type(exc).__name__}: {exc}")
    return not errors, errors


def _candidate_cumulative_at_legacy_cutoff(
    legacy_json: Path, candidate: Path, spec: FundSpec
) -> dict[str, Any]:
    old_document = json.loads(
        (legacy_json / f"{spec.agent_name}_季度盈亏.txt").read_text(encoding="utf-8-sig")
    )
    old_quarterly = next(value for key, value in old_document.items() if key != "基金经理")[
        "季度盈亏(万元)"
    ]
    cutoff = max(old_quarterly)
    new_quarterly = json.loads(
        (candidate / f"{spec.agent_name}_季度盈亏.txt").read_text(encoding="utf-8-sig")
    )[spec.root_key]["季度盈亏(万元)"]
    cumulative = {"大类": {}, "一级": {}, "个股": {}}
    for quarter, levels in new_quarterly.items():
        if quarter > cutoff:
            continue
        for level, values in levels.items():
            for label, value in values.items():
                cumulative[level][label] = cumulative[level].get(label, 0) + value
    return {
        spec.root_key: {"累计盈亏(万元)": {"全阶段累计": cumulative}},
        "基金经理": spec.manager_name,
    }


def _candidate_preference_at_legacy_cutoff(
    legacy_json: Path, candidate: Path, spec: FundSpec
) -> dict[str, Any]:
    old_config = json.loads(
        (legacy_json / f"{spec.agent_name}_基金配置.txt").read_text(encoding="utf-8-sig")
    )
    old_root = next(value for key, value in old_config.items() if key != "基金经理")
    cutoff = max(
        str(item["报告期"])
        for section in old_root.values()
        if isinstance(section, list)
        for item in section
        if isinstance(item, dict) and item.get("报告期")
    )
    new_config = json.loads(
        (candidate / f"{spec.agent_name}_基金配置.txt").read_text(encoding="utf-8-sig")
    )[spec.root_key]
    industries: Counter[str] = Counter()
    by_period: dict[str, list[dict[str, Any]]] = {}
    for item in new_config["一级行业"]:
        if item["报告期"] <= cutoff:
            by_period.setdefault(item["报告期"], []).append(item)
    for items in by_period.values():
        industries.update(item["申万一级"] for item in items[:5])
    stocks: Counter[str] = Counter()
    by_period = {}
    for item in new_config["重仓个股"]:
        if item["报告期"] <= cutoff:
            by_period.setdefault(item["报告期"], []).append(item)
    for items in by_period.values():
        stocks.update(item["股票名称"] for item in items[:10])
    payload = {
        "行业前五出现次数": [
            {"申万一级": key, "次数": value}
            for key, value in sorted(industries.items(), key=lambda item: (-item[1], item[0]))
        ],
        "个股前十出现次数": [
            {"股票名称": key, "次数": value}
            for key, value in sorted(stocks.items(), key=lambda item: (-item[1], item[0]))
        ],
    }
    return {spec.root_key: payload, "基金经理": spec.manager_name}


def _write_dossier(
    output: Path, spec: FundSpec, rows: list[dict[str, Any]], consistency: tuple[bool, list[str]]
) -> None:
    lines = [
        f"# {spec.agent_name} 人工审阅档案",
        "",
        f"- 基金代码：`{spec.fund_symbol}`",
        f"- 任职窗口：{spec.tenure_start} 至 {spec.tenure_end or 'as_of'}",
        f"- 预标异常：{', '.join(spec.exception_tags) or '无'}",
        f"- 内部勾稽：{'通过' if consistency[0] else '失败'}",
        "",
        "| 类型 | 覆盖率 | 接近率 | 自动结论 | 缺失 | 新增 |",
        "|---|---:|---:|---|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['type']} | {row.get('coverage', 0):.1%} | {row.get('close_rate', 0):.1%} | "
            f"{'通过' if row.get('passed') else '需复核'} | {row.get('missing_leaves', 0)} | {row.get('extra_leaves', 0)} |"
        )
    lines.extend(
        [
            "",
            "## 真实阅读记录",
            "",
            "- 身份与时间窗：待人工填写",
            "- 配置和特征语义：待人工填写",
            "- 盈亏方向及主要贡献：待人工填写",
            "- 合理差异/缺陷证据：待人工填写",
            "- 最终判定：待人工填写（通过 / 合理时点差异 / 合理源口径差异 / 旧JSON异常 / 新算法缺陷 / 湖数据缺口）",
        ]
    )
    (output / f"{spec.agent_name}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def validate(
    *,
    legacy_json: str | Path,
    candidate: str | Path,
    manifest: str | Path,
    output: str | Path,
) -> dict[str, Any]:
    legacy_json, candidate, output = Path(legacy_json), Path(candidate), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    dossiers = output / "manual-review"
    dossiers.mkdir(exist_ok=True)
    specs = load_manifest(manifest)
    results = []
    hard_errors = []
    consistency_results = []
    for spec in specs:
        fund_rows = []
        for kind in JSON_TYPES:
            name = f"{spec.agent_name}_{kind}.txt"
            old_path, new_path = legacy_json / name, candidate / name
            if not old_path.exists() or not new_path.exists():
                hard_errors.append(f"{spec.fund_symbol}/{kind}: missing file")
                row = {
                    "fund_symbol": spec.fund_symbol,
                    "agent_name": spec.agent_name,
                    "type": kind,
                    "passed": False,
                }
            else:
                try:
                    old, _ = _read(old_path, kind)
                    new, _ = _read(new_path, kind)
                    if kind == "累计盈亏":
                        new = _candidate_cumulative_at_legacy_cutoff(legacy_json, candidate, spec)
                    elif kind == "配置偏好":
                        new = _candidate_preference_at_legacy_cutoff(legacy_json, candidate, spec)
                    row = {
                        "fund_symbol": spec.fund_symbol,
                        "agent_name": spec.agent_name,
                        "type": kind,
                        **_compare(kind, old, new),
                    }
                except Exception as exc:
                    hard_errors.append(f"{spec.fund_symbol}/{kind}: {type(exc).__name__}: {exc}")
                    row = {
                        "fund_symbol": spec.fund_symbol,
                        "agent_name": spec.agent_name,
                        "type": kind,
                        "passed": False,
                    }
            results.append(row)
            fund_rows.append(row)
        consistency = _internal_consistency(candidate, spec)
        consistency_results.append(
            {"fund_symbol": spec.fund_symbol, "passed": consistency[0], "errors": consistency[1]}
        )
        _write_dossier(dossiers, spec, fund_rows, consistency)

    type_summary = {}
    for kind in JSON_TYPES:
        rows = [row for row in results if row["type"] == kind]
        passed = sum(bool(row.get("passed")) for row in rows)
        type_summary[kind] = {
            "passed_funds": passed,
            "total_funds": len(rows),
            "gate": passed >= 45,
        }
    passed = (
        not hard_errors
        and all(item["gate"] for item in type_summary.values())
        and all(item["passed"] for item in consistency_results)
    )
    summary = {
        "passed": passed,
        "funds": len(specs),
        "expected_files": len(specs) * len(JSON_TYPES),
        "hard_errors": hard_errors,
        "type_summary": type_summary,
        "internal_consistency": consistency_results,
    }
    (output / "validation-details.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output / "validation-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if results:
        keys = sorted(set().union(*(row.keys() for row in results)))
        with (output / "validation-details.csv").open(
            "w", encoding="utf-8-sig", newline=""
        ) as stream:
            writer = csv.DictWriter(stream, fieldnames=keys)
            writer.writeheader()
            writer.writerows(results)
    return summary
