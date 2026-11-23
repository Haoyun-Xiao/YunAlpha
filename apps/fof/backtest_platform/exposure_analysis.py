from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd


FACTOR_NAMES = ["BETA", "RSTR", "LNCAP", "EARNYILD", "GROWTH", "LEVERAGE", "LIQUIDITY", "RESVOL", "BTOP", "NLSIZE"]
SCOPE_PORTFOLIO = "portfolio"
COMPLETE_REPORT_TYPES = {"annual", "semi_annual"}
CATEGORY_ORDER = ["TMT", "制造", "周期", "消费", "金融"]
CATEGORY_COLORS = {
    "TMT": "#a371f7",
    "制造": "#58a6ff",
    "周期": "#3fb950",
    "消费": "#d29922",
    "金融": "#f85149",
}


def build_exposure_snapshot(
    data_dir: Path,
    funds: list[dict[str, Any]],
    weights: dict[str, float],
    sleeves: list[dict[str, Any]] | None,
    end_date: str,
    *,
    start_date: str | None = None,
    weight_history: pd.DataFrame | None = None,
) -> dict[str, Any]:
    selected_ids = [str(item.get("id") or item.get("code") or "").strip() for item in funds]
    selected_ids = [fund_id for fund_id in selected_ids if fund_id]
    fund_names = {str(item.get("id") or item.get("code")): str(item.get("name") or item.get("id") or item.get("code")) for item in funds}
    end_ts = pd.to_datetime(end_date)
    resolved_dir = str(data_dir.resolve())
    data = _load_exposure_data(resolved_dir)
    reports, holdings = _load_disclosure_data(resolved_dir, tuple(sorted(set(selected_ids))))

    style_rows, snapshot_date = _latest_portraits(data["style"], selected_ids, end_ts)
    industry_rows, _ = _latest_portraits(data["industry"], selected_ids, end_ts)
    sleeve_defs = _normalize_sleeves(sleeves, selected_ids)

    factor_rows = _factor_rows(style_rows, selected_ids, fund_names, weights, sleeve_defs)
    industry_l1 = _industry_l1(industry_rows, selected_ids, weights, sleeve_defs)
    start_ts = pd.to_datetime(start_date) if start_date else None
    category_timeline = _category_timeline(
        data["industry"],
        selected_ids,
        weights,
        data["industry_category_map"],
        end_ts,
        start_ts=start_ts,
        weight_history=weight_history,
    )
    report_end_by_fund = _report_end_by_fund(reports, selected_ids, snapshot_date, end_ts)
    fund_holdings = _selected_holdings(holdings, report_end_by_fund, end_ts)
    fund_holdings = _attach_industry(fund_holdings, data["industry_map"], data["industry_category_map"], end_ts)
    drilldown = _drilldown_tables(fund_holdings, selected_ids, weights, sleeve_defs, fund_names)
    breakdown_sections = _breakdown_sections(fund_holdings, selected_ids, weights, sleeve_defs, fund_names)
    history_tables = _history_tables(
        data["industry"],
        reports,
        holdings,
        data["industry_map"],
        data["industry_category_map"],
        selected_ids,
        weights,
        end_ts,
        start_ts,
        weight_history,
    )
    fund_details = _fund_details(style_rows, industry_rows, fund_holdings, selected_ids, fund_names, report_end_by_fund)
    coverage = _coverage_payload(
        selected_ids,
        fund_names,
        weights,
        {
            "style": set(style_rows.get("ts_code", pd.Series(dtype=str)).dropna().astype(str)),
            "industry": set(industry_rows.get("ts_code", pd.Series(dtype=str)).dropna().astype(str)),
            "disclosure": set(fund_holdings.get("ts_code", pd.Series(dtype=str)).dropna().astype(str)),
        },
    )

    holding_report_dates = sorted({date.strftime("%Y-%m-%d") for date in report_end_by_fund.values() if pd.notna(date)})
    return {
        "snapshot_date": snapshot_date.strftime("%Y-%m-%d") if pd.notna(snapshot_date) else "",
        "holding_report_date": holding_report_dates[-1] if holding_report_dates else "",
        "badges": ["A股口径", "估算画像", "港股未纳入"],
        "factor_names": FACTOR_NAMES,
        "factor_rows": factor_rows,
        "industry_l1": industry_l1,
        "category_timeline": category_timeline,
        "drilldown": drilldown,
        "breakdown_sections": breakdown_sections,
        "history_tables": history_tables,
        "fund_details": fund_details,
        "coverage": coverage,
    }


@lru_cache(maxsize=32)
def _load_disclosure_data(data_dir: str, selected_ids: tuple[str, ...]) -> tuple[pd.DataFrame, pd.DataFrame]:
    query_dir = Path(data_dir) / "query"
    filters = [("ts_code", "in", list(selected_ids))] if selected_ids else None
    reports = pd.read_parquet(query_dir / "fund_portfolio_reports.parquet", filters=filters)
    holdings = pd.read_parquet(query_dir / "fund_holdings.parquet", filters=filters)
    for frame in [reports, holdings]:
        for column in ["end_date", "ann_date"]:
            if column in frame.columns:
                frame[column] = pd.to_datetime(frame[column], errors="coerce")
    return reports, holdings


@lru_cache(maxsize=2)
def _load_exposure_data(data_dir: str) -> dict[str, pd.DataFrame]:
    query_dir = Path(data_dir) / "query"
    style = pd.read_parquet(query_dir / "fund_style_portraits.parquet")
    industry = pd.read_parquet(query_dir / "fund_industry_portraits.parquet")
    industry_map = pd.read_parquet(query_dir / "stock_industry_map.parquet")
    industry_category_map = _load_industry_category_map(query_dir / "industry_category_map.csv")
    for frame in [style, industry]:
        for column in ["end_date", "ann_date", "factor_date"]:
            if column in frame.columns:
                frame[column] = pd.to_datetime(frame[column], errors="coerce")
    for column in ["in_date", "out_date"]:
        if column in industry_map.columns:
            industry_map[column] = pd.to_datetime(industry_map[column], errors="coerce")
    return {
        "style": style,
        "industry": industry,
        "industry_map": industry_map,
        "industry_category_map": industry_category_map,
    }


def _load_industry_category_map(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["l1_name", "category"])
    mapping = pd.read_csv(path)
    if not {"l1_name", "category"} <= set(mapping.columns):
        return pd.DataFrame(columns=["l1_name", "category"])
    return mapping[["l1_name", "category"]].dropna().drop_duplicates("l1_name", keep="last")


def _latest_portraits(df: pd.DataFrame, selected_ids: list[str], end_ts: pd.Timestamp) -> tuple[pd.DataFrame, pd.Timestamp]:
    subset = df[df["ts_code"].isin(selected_ids) & df["end_date"].le(end_ts)].copy()
    if "ann_date" in subset.columns:
        subset = subset[subset["ann_date"].le(end_ts)]
    if subset.empty:
        return subset, pd.NaT
    snapshot_date = subset["end_date"].max()
    latest = subset[subset["end_date"].eq(snapshot_date)].sort_values(["ts_code", "ann_date", "report_type"])
    latest = latest.drop_duplicates("ts_code", keep="last")
    missing_ids = [fund_id for fund_id in selected_ids if fund_id not in set(latest["ts_code"])]
    if missing_ids:
        fallback = subset[subset["ts_code"].isin(missing_ids)].sort_values(["ts_code", "end_date", "ann_date", "report_type"])
        latest = pd.concat([latest, fallback.drop_duplicates("ts_code", keep="last")], ignore_index=True)
    return latest, snapshot_date


def _normalize_sleeves(sleeves: list[dict[str, Any]] | None, selected_ids: list[str]) -> list[dict[str, Any]]:
    selected = set(selected_ids)
    normalized: list[dict[str, Any]] = []
    for sleeve in sleeves or []:
        fund_ids = [fund_id for fund_id in sleeve.get("funds", []) if fund_id in selected]
        if not fund_ids:
            continue
        normalized.append(
            {
                "id": str(sleeve.get("id") or sleeve.get("name") or f"sleeve_{len(normalized) + 1}"),
                "name": str(sleeve.get("name") or sleeve.get("id") or f"子集合{len(normalized) + 1}"),
                "funds": fund_ids,
            }
        )
    assigned = {fund_id for sleeve in normalized for fund_id in sleeve["funds"]}
    unassigned = [fund_id for fund_id in selected_ids if fund_id not in assigned]
    if unassigned or not normalized:
        normalized.append({"id": "ungrouped", "name": "未分组", "funds": unassigned or selected_ids})
    return normalized


def _factor_rows(
    style_rows: pd.DataFrame,
    selected_ids: list[str],
    fund_names: dict[str, str],
    weights: dict[str, float],
    sleeves: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [
        {
            "id": SCOPE_PORTFOLIO,
            "type": "portfolio",
            "level": 0,
            "name": "总组合",
            "values": _weighted_values(style_rows, selected_ids, weights, FACTOR_NAMES),
        }
    ]
    for sleeve in sleeves:
        sleeve_weights = _scope_weights(sleeve["funds"], weights)
        rows.append(
            {
                "id": sleeve["id"],
                "type": "sleeve",
                "level": 1,
                "name": sleeve["name"],
                "values": _weighted_values(style_rows, sleeve["funds"], sleeve_weights, FACTOR_NAMES),
            }
        )
        for fund_id in sleeve["funds"]:
            fund_row = style_rows[style_rows["ts_code"].eq(fund_id)]
            rows.append(
                {
                    "id": fund_id,
                    "type": "fund",
                    "level": 2,
                    "parent_id": sleeve["id"],
                    "name": fund_names.get(fund_id, fund_id),
                    "code": fund_id,
                    "values": _row_values(fund_row, FACTOR_NAMES),
                }
            )
    return rows


def _industry_l1(
    industry_rows: pd.DataFrame,
    selected_ids: list[str],
    weights: dict[str, float],
    sleeves: list[dict[str, Any]],
) -> dict[str, Any]:
    columns = [column for column in industry_rows.columns if column.startswith("ind_")]
    industries = [column.replace("ind_", "", 1) for column in columns]
    portfolio_values = _weighted_values(industry_rows, selected_ids, weights, columns)
    portfolio = {industry: portfolio_values.get(f"ind_{industry}") for industry in industries}
    scope_values: dict[str, dict[str, float | None]] = {
        SCOPE_PORTFOLIO: portfolio,
    }
    for sleeve in sleeves:
        sleeve_weights = _scope_weights(sleeve["funds"], weights)
        values = _weighted_values(industry_rows, sleeve["funds"], sleeve_weights, columns)
        scope_values[sleeve["id"]] = {industry: values.get(f"ind_{industry}") for industry in industries}
    ordered = sorted(industries, key=lambda item: abs(portfolio.get(item) or 0), reverse=True)
    matrix_rows = []
    for industry in ordered:
        row = {"industry": industry}
        for scope_id, values in scope_values.items():
            row[scope_id] = _clean_number(values.get(industry))
        matrix_rows.append(row)
    return {
        "names": ordered,
        "portfolio": {industry: _clean_number(portfolio.get(industry)) for industry in ordered},
        "matrix_rows": matrix_rows,
    }


def _category_timeline(
    industry: pd.DataFrame,
    selected_ids: list[str],
    weights: dict[str, float],
    industry_category_map: pd.DataFrame,
    end_ts: pd.Timestamp,
    *,
    start_ts: pd.Timestamp | None = None,
    weight_history: pd.DataFrame | None = None,
) -> dict[str, Any]:
    if industry.empty or not selected_ids:
        return {"categories": CATEGORY_ORDER, "years": [], "series": []}
    columns = [column for column in industry.columns if column.startswith("ind_")]
    if not columns:
        return {"categories": CATEGORY_ORDER, "years": [], "series": []}
    category_lookup = _category_lookup(industry_category_map)
    subset = industry[industry["ts_code"].isin(selected_ids) & industry["end_date"].le(end_ts)].copy()
    if "ann_date" in subset.columns:
        subset = subset[subset["ann_date"].le(end_ts)]
    subset = subset[subset["end_date"].dt.month.eq(12)]
    if start_ts is not None and pd.notna(start_ts):
        subset = subset[subset["end_date"].dt.year.ge(int(start_ts.year))]
    if subset.empty:
        return {"categories": CATEGORY_ORDER, "years": [], "series": []}

    years: list[int] = []
    values_by_year: list[dict[str, float]] = []
    for year, year_rows in subset.groupby(subset["end_date"].dt.year):
        latest = (
            year_rows.sort_values(["ts_code", "end_date", "ann_date", "report_type"])
            .drop_duplicates("ts_code", keep="last")
        )
        available_ids = [fund_id for fund_id in selected_ids if fund_id in set(latest["ts_code"])]
        year_weights = _available_weights(available_ids, weights)
        if not year_weights:
            continue
        totals = {category: 0.0 for category in CATEGORY_ORDER}
        indexed = latest.set_index("ts_code", drop=False)
        for fund_id in available_ids:
            fund_weight = max(float(year_weights.get(fund_id, 0.0)), 0.0)
            row = indexed.loc[fund_id]
            for column in columns:
                value = pd.to_numeric(pd.Series([row.get(column)]), errors="coerce").iloc[0]
                if pd.isna(value):
                    continue
                industry_name = column.replace("ind_", "", 1)
                category = category_lookup.get(industry_name, "制造")
                if category not in totals:
                    totals[category] = 0.0
                totals[category] += fund_weight * float(value)
        years.append(int(year))
        values_by_year.append({category: _clean_number(totals.get(category, 0.0)) or 0.0 for category in CATEGORY_ORDER})

    return {
        "categories": CATEGORY_ORDER,
        "years": years,
        "series": [
            {
                "name": category,
                "color": CATEGORY_COLORS.get(category, "#8b949e"),
                "values": [_clean_number(values.get(category, 0.0)) or 0.0 for values in values_by_year],
            }
            for category in CATEGORY_ORDER
        ],
    }


def _weighted_values(df: pd.DataFrame, fund_ids: list[str], weights: dict[str, float], columns: list[str]) -> dict[str, float | None]:
    if df.empty or not fund_ids:
        return {column: None for column in columns}
    indexed = df.set_index("ts_code", drop=False)
    values: dict[str, float | None] = {}
    for column in columns:
        total = 0.0
        available_weight = 0.0
        for fund_id in fund_ids:
            if fund_id not in indexed.index or column not in indexed.columns:
                continue
            value = pd.to_numeric(pd.Series([indexed.loc[fund_id, column]]), errors="coerce").iloc[0]
            if pd.isna(value):
                continue
            weight = float(weights.get(fund_id, 0.0))
            total += weight * float(value)
            available_weight += weight
        values[column] = _clean_number(total / available_weight) if available_weight > 0 else None
    return values


def _row_values(df: pd.DataFrame, columns: list[str]) -> dict[str, float | None]:
    if df.empty:
        return {column: None for column in columns}
    row = df.iloc[-1]
    return {column: _clean_number(row.get(column)) for column in columns}


def _coverage_payload(
    selected_ids: list[str],
    fund_names: dict[str, str],
    weights: dict[str, float],
    available_by_kind: dict[str, set[str]],
) -> dict[str, Any]:
    total_weight = sum(max(float(weights.get(fund_id, 0.0)), 0.0) for fund_id in selected_ids)
    payload: dict[str, Any] = {}
    for kind, available in available_by_kind.items():
        available_weight = sum(max(float(weights.get(fund_id, 0.0)), 0.0) for fund_id in selected_ids if fund_id in available)
        missing = [
            {
                "id": fund_id,
                "name": fund_names.get(fund_id, fund_id),
                "weight": _clean_number(weights.get(fund_id, 0.0)),
            }
            for fund_id in selected_ids
            if fund_id not in available
        ]
        payload[kind] = {
            "available_weight": _clean_number(available_weight),
            "total_weight": _clean_number(total_weight),
            "coverage_ratio": _clean_number(available_weight / total_weight if total_weight > 0 else None),
            "missing_funds": missing,
        }
    return payload


def _scope_weights(fund_ids: list[str], weights: dict[str, float]) -> dict[str, float]:
    total = sum(float(weights.get(fund_id, 0.0)) for fund_id in fund_ids)
    if total <= 0 and fund_ids:
        equal = 1.0 / len(fund_ids)
        return {fund_id: equal for fund_id in fund_ids}
    return {fund_id: float(weights.get(fund_id, 0.0)) / total for fund_id in fund_ids}


def _report_end_by_fund(
    reports: pd.DataFrame,
    selected_ids: list[str],
    snapshot_date: pd.Timestamp,
    as_of_date: pd.Timestamp,
) -> dict[str, pd.Timestamp]:
    result: dict[str, pd.Timestamp] = {}
    end_limit = snapshot_date if pd.notna(snapshot_date) else as_of_date
    subset = reports[reports["ts_code"].isin(selected_ids) & reports["end_date"].le(end_limit)].copy()
    if "ann_date" in subset.columns:
        subset = subset[subset["ann_date"].le(as_of_date)]
    for fund_id in selected_ids:
        fund_reports = subset[subset["ts_code"].eq(fund_id)]
        if fund_reports.empty:
            continue
        result[fund_id] = fund_reports.sort_values(["end_date", "ann_date"]).iloc[-1]["end_date"]
    return result


def _selected_holdings(
    holdings: pd.DataFrame,
    report_end_by_fund: dict[str, pd.Timestamp],
    as_of_date: pd.Timestamp,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for fund_id, report_end in report_end_by_fund.items():
        fund_rows = holdings[holdings["ts_code"].eq(fund_id) & holdings["end_date"].eq(report_end)].copy()
        if "ann_date" in fund_rows.columns:
            fund_rows = fund_rows[fund_rows["ann_date"].le(as_of_date)]
        if fund_rows.empty:
            continue
        by_ann = fund_rows.groupby("ann_date")["stk_mkv_ratio"].sum()
        if by_ann.sum() > 120:
            fund_rows = fund_rows[fund_rows["ann_date"].eq(by_ann.index.max())]
        frames.append(fund_rows)
    if not frames:
        return pd.DataFrame(columns=list(holdings.columns))
    selected = pd.concat(frames, ignore_index=True)
    numeric_cols = ["mkv", "amount", "stk_mkv_ratio", "stk_float_ratio"]
    for column in numeric_cols:
        selected[column] = pd.to_numeric(selected[column], errors="coerce").fillna(0.0)
    selected = (
        selected.sort_values(["ts_code", "end_date", "ann_date", "symbol"])
        .groupby(["ts_code", "end_date", "symbol"], as_index=False)
        .agg(
            {
                "ann_date": "max",
                "mkv": "sum",
                "amount": "sum",
                "stk_mkv_ratio": "sum",
                "stk_float_ratio": "sum",
                "symbol_std": "last",
                "ts_code_std": "last",
            }
        )
    )
    return selected


def _attach_industry(
    holdings: pd.DataFrame,
    industry_map: pd.DataFrame,
    industry_category_map: pd.DataFrame,
    as_of_date: pd.Timestamp,
) -> pd.DataFrame:
    if holdings.empty:
        return holdings.copy()
    eligible_map = industry_map.copy()
    if "in_date" in eligible_map.columns:
        eligible_map = eligible_map[eligible_map["in_date"].isna() | eligible_map["in_date"].le(as_of_date)]
    if "out_date" in eligible_map.columns:
        eligible_map = eligible_map[eligible_map["out_date"].isna() | eligible_map["out_date"].gt(as_of_date)]
    latest_map = eligible_map.sort_values(["ts_code", "in_date"]).drop_duplicates("ts_code", keep="last")
    cols = ["ts_code", "name", "l1_name", "l2_name", "l3_name"]
    mapped = holdings.merge(latest_map[cols], left_on="symbol", right_on="ts_code", how="left", suffixes=("", "_stock"))
    mapped = mapped.drop(columns=[column for column in ["ts_code_stock"] if column in mapped.columns])
    category_lookup = _category_lookup(industry_category_map)
    mapped["category_name"] = mapped["l1_name"].map(category_lookup).fillna("制造")
    mapped["is_a_share"] = mapped["symbol"].astype(str).str.endswith((".SH", ".SZ"))
    mapped["is_hk"] = mapped["symbol"].astype(str).str.endswith(".HK")
    return mapped


def _category_lookup(industry_category_map: pd.DataFrame) -> dict[str, str]:
    if industry_category_map.empty:
        return {}
    return dict(zip(industry_category_map["l1_name"].astype(str), industry_category_map["category"].astype(str)))


def _drilldown_tables(
    holdings: pd.DataFrame,
    selected_ids: list[str],
    weights: dict[str, float],
    sleeves: list[dict[str, Any]],
    fund_names: dict[str, str],
) -> dict[str, Any]:
    scopes: dict[str, dict[str, Any]] = {
        SCOPE_PORTFOLIO: {"funds": selected_ids, "weights": weights, "name": "总组合"},
    }
    fund_to_sleeve: dict[str, str] = {}
    for sleeve in sleeves:
        scopes[sleeve["id"]] = {"funds": sleeve["funds"], "weights": _scope_weights(sleeve["funds"], weights), "name": sleeve["name"]}
        for fund_id in sleeve["funds"]:
            fund_to_sleeve[fund_id] = sleeve["name"]
    return {
        "l2_by_scope": {scope_id: _l2_rows(holdings, spec["funds"], spec["weights"], fund_names) for scope_id, spec in scopes.items()},
        "holdings_by_scope": {
            scope_id: _holding_rows(holdings, spec["funds"], spec["weights"], fund_to_sleeve)
            for scope_id, spec in scopes.items()
        },
        "scopes": [{"id": scope_id, "name": spec["name"]} for scope_id, spec in scopes.items()],
    }


def _breakdown_sections(
    holdings: pd.DataFrame,
    selected_ids: list[str],
    weights: dict[str, float],
    sleeves: list[dict[str, Any]],
    fund_names: dict[str, str],
) -> list[dict[str, Any]]:
    fund_to_sleeve = _fund_to_sleeve_name(sleeves)
    scope_specs = [{"id": SCOPE_PORTFOLIO, "funds": selected_ids, "weights": weights}]
    for sleeve in sleeves:
        scope_specs.append({"id": sleeve["id"], "funds": sleeve["funds"], "weights": _scope_weights(sleeve["funds"], weights)})

    section_specs = [
        {
            "id": "category",
            "title": "大类",
            "subtitle": "按申万一级行业映射到大类汇总",
            "limit": None,
            "group_cols": ["category_name"],
            "meta_builder": lambda key: {"key": _string_or_dash(key[0]), "label": _string_or_dash(key[0])},
        },
        {
            "id": "l1",
            "title": "一级行业",
            "subtitle": "按申万一级行业汇总",
            "limit": None,
            "group_cols": ["l1_name"],
            "meta_builder": lambda key: {"key": _string_or_dash(key[0]), "label": _string_or_dash(key[0])},
        },
        {
            "id": "l2",
            "title": "二级行业",
            "subtitle": "展示前二十大，其余合并为其他",
            "limit": 20,
            "group_cols": ["l1_name", "l2_name"],
            "meta_builder": lambda key: {"key": f"{_string_or_dash(key[0])}::{_string_or_dash(key[1])}", "label": _string_or_dash(key[1]), "l1": _string_or_dash(key[0])},
        },
        {
            "id": "stock",
            "title": "个股",
            "subtitle": "展示前十大，其余合并为其他",
            "limit": 10,
            "group_cols": ["symbol", "name", "l1_name", "l2_name"],
            "meta_builder": lambda key: {
                "key": _string_or_dash(key[0]),
                "label": _string_or_dash(key[1], key[0]),
                "code": _string_or_dash(key[0]),
                "l1": _string_or_dash(key[2]),
                "l2": _string_or_dash(key[3]),
            },
        },
    ]

    sections = []
    for spec in section_specs:
        scope_items = {}
        for scope in scope_specs:
            scoped = _a_share_scope_holdings(holdings, scope["funds"], scope["weights"])
            scope_items[scope["id"]] = _breakdown_items(
                scoped,
                spec["group_cols"],
                spec["meta_builder"],
                fund_names,
                fund_to_sleeve,
                limit=spec["limit"],
            )
        sections.append({
            "id": spec["id"],
            "title": spec["title"],
            "subtitle": spec["subtitle"],
            "limit": spec["limit"],
            "items": scope_items.get(SCOPE_PORTFOLIO, []),
            "scopes": scope_items,
        })
    return sections


def _breakdown_items(
    scoped: pd.DataFrame,
    group_cols: list[str],
    meta_builder: Any,
    fund_names: dict[str, str],
    fund_to_sleeve: dict[str, str],
    limit: int | None,
) -> list[dict[str, Any]]:
    if scoped.empty:
        return []
    grouped_items: list[tuple[float, dict[str, Any], pd.DataFrame]] = []
    grouped = scoped.groupby(group_cols, dropna=False)
    for key, group in grouped:
        key_tuple = key if isinstance(key, tuple) else (key,)
        weight = float(group["weighted_ratio"].sum())
        item = {
            **meta_builder(key_tuple),
            "weight": _clean_number(weight),
            "sources": _breakdown_sources(group, fund_names, fund_to_sleeve),
        }
        grouped_items.append((abs(weight), item, group))
    grouped_items.sort(key=lambda item: item[0], reverse=True)
    if limit is None or len(grouped_items) <= limit:
        return [item for _, item, _ in grouped_items]

    visible = [item for _, item, _ in grouped_items[:limit]]
    other_groups = [group for _, _, group in grouped_items[limit:]]
    if other_groups:
        other = pd.concat(other_groups, ignore_index=True)
        visible.append(
            {
                "key": "other",
                "label": "其他",
                "weight": _clean_number(float(other["weighted_ratio"].sum())),
                "is_other": True,
                "sources": _breakdown_sources(other, fund_names, fund_to_sleeve),
            }
        )
    return visible


def _breakdown_sources(group: pd.DataFrame, fund_names: dict[str, str], fund_to_sleeve: dict[str, str]) -> list[dict[str, Any]]:
    fund_contrib = group.groupby("ts_code")["weighted_ratio"].sum().sort_values(ascending=False)
    return [
        {
            "fund_id": str(fund_id),
            "name": fund_names.get(str(fund_id), str(fund_id)),
            "sleeve": fund_to_sleeve.get(str(fund_id), "未分组"),
            "contribution": _clean_number(float(value)),
        }
        for fund_id, value in fund_contrib.items()
        if float(value) != 0
    ]


def _fund_to_sleeve_name(sleeves: list[dict[str, Any]]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for sleeve in sleeves:
        name = str(sleeve.get("name") or sleeve.get("id") or "未分组")
        for fund_id in sleeve.get("funds", []):
            mapping[str(fund_id)] = name
    return mapping


def _string_or_dash(*values: Any) -> str:
    for value in values:
        if value is None or pd.isna(value):
            continue
        text = str(value).strip()
        if text:
            return text
    return "-"


def _l2_rows(holdings: pd.DataFrame, fund_ids: list[str], weights: dict[str, float], fund_names: dict[str, str]) -> list[dict[str, Any]]:
    scoped = _a_share_scope_holdings(holdings, fund_ids, weights)
    if scoped.empty:
        return []
    grouped = scoped.groupby(["l1_name", "l2_name"], dropna=False)
    rows = []
    for (l1_name, l2_name), group in grouped:
        fund_contrib = group.groupby("ts_code")["weighted_ratio"].sum().sort_values(ascending=False)
        rows.append(
            {
                "industry": str(l2_name or "-"),
                "l1": str(l1_name or "-"),
                "weight": _clean_number(group["weighted_ratio"].sum()),
                "fund_count": int(group["ts_code"].nunique()),
                "main_funds": [
                    {"id": fund_id, "name": fund_names.get(fund_id, fund_id), "weight": _clean_number(value)}
                    for fund_id, value in fund_contrib.head(3).items()
                ],
            }
        )
    return sorted(rows, key=lambda item: abs(item["weight"] or 0), reverse=True)


def _holding_rows(holdings: pd.DataFrame, fund_ids: list[str], weights: dict[str, float], fund_to_sleeve: dict[str, str]) -> list[dict[str, Any]]:
    scoped = _a_share_scope_holdings(holdings, fund_ids, weights)
    if scoped.empty:
        return []
    grouped = scoped.groupby(["symbol", "name", "l1_name", "l2_name"], dropna=False)
    rows = []
    for (symbol, name, l1_name, l2_name), group in grouped:
        sleeve_contrib = group.assign(sleeve=group["ts_code"].map(fund_to_sleeve).fillna("未分组")).groupby("sleeve")["weighted_ratio"].sum()
        main_sleeve = str(sleeve_contrib.sort_values(ascending=False).index[0]) if not sleeve_contrib.empty else "-"
        rows.append(
            {
                "name": str(name or symbol),
                "code": str(symbol),
                "l1": str(l1_name or "-"),
                "l2": str(l2_name or "-"),
                "weight": _clean_number(group["weighted_ratio"].sum()),
                "fund_count": int(group["ts_code"].nunique()),
                "main_sleeve": main_sleeve,
            }
        )
    return sorted(rows, key=lambda item: abs(item["weight"] or 0), reverse=True)


def _a_share_scope_holdings(holdings: pd.DataFrame, fund_ids: list[str], weights: dict[str, float]) -> pd.DataFrame:
    scoped = holdings[holdings["ts_code"].isin(fund_ids) & holdings.get("is_a_share", False)].copy()
    if scoped.empty:
        return scoped
    scoped["weighted_ratio"] = scoped.apply(
        lambda row: float(weights.get(row["ts_code"], 0.0)) * float(row.get("stk_mkv_ratio", 0.0)) / 100.0,
        axis=1,
    )
    return scoped


def _history_tables(
    industry: pd.DataFrame,
    reports: pd.DataFrame,
    holdings: pd.DataFrame,
    industry_map: pd.DataFrame,
    industry_category_map: pd.DataFrame,
    selected_ids: list[str],
    weights: dict[str, float],
    end_ts: pd.Timestamp,
    start_ts: pd.Timestamp | None = None,
    weight_history: pd.DataFrame | None = None,
) -> dict[str, Any]:
    return {
        "industry_l1": _industry_history_table(industry, selected_ids, weights, end_ts, start_ts),
        "top_holdings": _top_holdings_history_table(reports, holdings, industry_map, industry_category_map, selected_ids, weights, end_ts, start_ts),
    }


def _industry_history_table(
    industry: pd.DataFrame,
    selected_ids: list[str],
    weights: dict[str, float],
    end_ts: pd.Timestamp,
    start_ts: pd.Timestamp | None,
) -> dict[str, Any]:
    columns = [column for column in industry.columns if column.startswith("ind_")]
    if industry.empty or not selected_ids or not columns:
        return {"periods": [], "rows": []}
    subset = industry[industry["ts_code"].isin(selected_ids) & industry["end_date"].le(end_ts)].copy()
    if "ann_date" in subset.columns:
        subset = subset[subset["ann_date"].le(end_ts)]
    subset = subset[subset["end_date"].dt.month.isin([6, 12])]
    if start_ts is not None and pd.notna(start_ts):
        subset = subset[subset["end_date"].ge(start_ts)]
    if subset.empty:
        return {"periods": [], "rows": []}
    subset["_report_priority"] = subset["report_type"].map(_report_priority) if "report_type" in subset.columns else 0
    latest = (
        subset.sort_values(["end_date", "ts_code", "_report_priority", "ann_date"])
        .drop_duplicates(["end_date", "ts_code"], keep="last")
    )
    period_dates = sorted(latest["end_date"].dropna().unique())
    periods = [_period_label(pd.Timestamp(period)) for period in period_dates]
    values_by_period: dict[str, dict[str, float | None]] = {}
    for period_date, period_label in zip(period_dates, periods):
        period_rows = latest[latest["end_date"].eq(period_date)]
        available_ids = [fund_id for fund_id in selected_ids if fund_id in set(period_rows["ts_code"])]
        period_weights = _available_weights(available_ids, weights)
        values = _weighted_values(period_rows, available_ids, period_weights, columns)
        values_by_period[period_label] = {column.replace("ind_", "", 1): value for column, value in values.items()}

    industries = [column.replace("ind_", "", 1) for column in columns]
    rows = []
    for industry_name in industries:
        period_values = {
            period: _clean_number(values_by_period.get(period, {}).get(industry_name) or 0.0)
            for period in periods
        }
        if any((value or 0.0) > 0 for value in period_values.values()):
            rows.append({"industry": industry_name, "values": period_values})
    rows.sort(key=lambda item: -sum(float(value or 0.0) for value in item["values"].values()))
    return {"periods": periods, "rows": rows}


def _top_holdings_history_table(
    reports: pd.DataFrame,
    holdings: pd.DataFrame,
    industry_map: pd.DataFrame,
    industry_category_map: pd.DataFrame,
    selected_ids: list[str],
    weights: dict[str, float],
    end_ts: pd.Timestamp,
    start_ts: pd.Timestamp | None,
) -> dict[str, Any]:
    if reports.empty or holdings.empty or not selected_ids:
        return {"rows": []}
    selected_reports = _selected_report_rows(reports, selected_ids, end_ts, start_ts)
    if selected_reports.empty:
        return {"rows": []}
    rows = []
    for period_date in sorted(selected_reports["end_date"].dropna().unique(), reverse=True):
        period_reports = selected_reports[selected_reports["end_date"].eq(period_date)]
        available_ids = [fund_id for fund_id in selected_ids if fund_id in set(period_reports["ts_code"])]
        period_weights = _available_weights(available_ids, weights)
        if not period_weights:
            continue
        frames = []
        for _, report in period_reports.iterrows():
            fund_id = str(report["ts_code"])
            fund_rows = holdings[holdings["ts_code"].eq(fund_id) & holdings["end_date"].eq(period_date)].copy()
            if "ann_date" in fund_rows.columns and pd.notna(report.get("ann_date")):
                fund_rows = fund_rows[fund_rows["ann_date"].eq(report["ann_date"])]
            if not fund_rows.empty:
                frames.append(fund_rows)
        if not frames:
            continue
        period_holdings = pd.concat(frames, ignore_index=True)
        for column in ["mkv", "amount", "stk_mkv_ratio", "stk_float_ratio"]:
            if column in period_holdings.columns:
                period_holdings[column] = pd.to_numeric(period_holdings[column], errors="coerce").fillna(0.0)
        period_holdings = _attach_industry(period_holdings, industry_map, industry_category_map, pd.Timestamp(period_date))
        scoped = _a_share_scope_holdings(period_holdings, available_ids, period_weights)
        if scoped.empty:
            continue
        grouped = scoped.groupby(["symbol", "name"], dropna=False)["weighted_ratio"].sum().sort_values(ascending=False)
        top_rows = []
        for rank, ((symbol, name), weight) in enumerate(grouped.head(10).items(), start=1):
            top_rows.append(
                {
                    "rank": rank,
                    "name": _string_or_dash(name, symbol),
                    "code": str(symbol),
                    "weight": _clean_number(weight),
                }
            )
        if top_rows:
            rows.append({"period": _period_label(pd.Timestamp(period_date)), "holdings": top_rows})
    return {"rows": rows}


def _selected_report_rows(
    reports: pd.DataFrame,
    selected_ids: list[str],
    end_ts: pd.Timestamp,
    start_ts: pd.Timestamp | None = None,
) -> pd.DataFrame:
    subset = reports[reports["ts_code"].isin(selected_ids) & reports["end_date"].le(end_ts)].copy()
    if "ann_date" in subset.columns:
        subset = subset[subset["ann_date"].le(end_ts)]
    if start_ts is not None and pd.notna(start_ts):
        subset = subset[subset["end_date"].ge(start_ts)]
    if subset.empty:
        return subset
    subset["_report_priority"] = subset["report_type"].map(_report_priority) if "report_type" in subset.columns else 0
    return (
        subset.sort_values(["ts_code", "end_date", "_report_priority", "ann_date"])
        .drop_duplicates(["ts_code", "end_date"], keep="last")
    )


def _available_weights(fund_ids: list[str], weights: dict[str, float]) -> dict[str, float]:
    available_weight = sum(max(float(weights.get(fund_id, 0.0)), 0.0) for fund_id in fund_ids)
    if available_weight <= 0:
        return {}
    return {fund_id: max(float(weights.get(fund_id, 0.0)), 0.0) / available_weight for fund_id in fund_ids}


def _report_priority(report_type: Any) -> int:
    text = str(report_type or "")
    if text in COMPLETE_REPORT_TYPES:
        return 3
    if text.endswith("_quarterly"):
        return 2
    return 1


def _period_label(date: pd.Timestamp) -> str:
    month_to_quarter = {3: "Q1", 6: "Q2", 9: "Q3", 12: "Q4"}
    quarter = month_to_quarter.get(int(date.month))
    return f"{date.year}{quarter}" if quarter else date.strftime("%Y-%m-%d")


def _fund_details(
    style_rows: pd.DataFrame,
    industry_rows: pd.DataFrame,
    holdings: pd.DataFrame,
    selected_ids: list[str],
    fund_names: dict[str, str],
    report_end_by_fund: dict[str, pd.Timestamp],
) -> dict[str, Any]:
    details = {}
    industry_cols = [column for column in industry_rows.columns if column.startswith("ind_")]
    for fund_id in selected_ids:
        fund_style = style_rows[style_rows["ts_code"].eq(fund_id)]
        fund_industry = industry_rows[industry_rows["ts_code"].eq(fund_id)]
        fund_holdings = holdings[holdings["ts_code"].eq(fund_id)].copy()
        a_share = fund_holdings[fund_holdings.get("is_a_share", False)].copy()
        if not a_share.empty:
            a_share["own_ratio"] = pd.to_numeric(a_share["stk_mkv_ratio"], errors="coerce").fillna(0) / 100.0
        industry_values = _row_values(fund_industry, industry_cols)
        top_industries = sorted(
            [
                {"industry": column.replace("ind_", "", 1), "weight": value}
                for column, value in industry_values.items()
                if value is not None
            ],
            key=lambda item: abs(item["weight"] or 0),
            reverse=True,
        )[:5]
        top_l2_industries = []
        if not a_share.empty:
            l2_group = a_share.groupby("l2_name", dropna=False)["own_ratio"].sum().sort_values(ascending=False)
            top_l2_industries = [
                {"industry": _string_or_dash(l2_name), "weight": _clean_number(weight)}
                for l2_name, weight in l2_group.head(10).items()
            ]
        holdings_rows = []
        if not a_share.empty:
            for _, row in a_share.sort_values("own_ratio", ascending=False).head(10).iterrows():
                holdings_rows.append(
                    {
                        "name": str(row.get("name") or row.get("symbol")),
                        "code": str(row.get("symbol")),
                        "l1": str(row.get("l1_name") or "-"),
                        "l2": str(row.get("l2_name") or "-"),
                        "weight": _clean_number(row.get("own_ratio")),
                    }
                )
        details[fund_id] = {
            "id": fund_id,
            "name": fund_names.get(fund_id, fund_id),
            "code": fund_id,
            "snapshot_date": _date_from_row(fund_style, "end_date"),
            "holding_report_date": report_end_by_fund.get(fund_id).strftime("%Y-%m-%d") if fund_id in report_end_by_fund else "",
            "a_share_status": "已覆盖" if not a_share.empty else "暂无A股持仓",
            "factor_values": _row_values(fund_style, FACTOR_NAMES),
            "top_industries": top_industries,
            "top_l2_industries": top_l2_industries,
            "top_holdings": holdings_rows,
            "ignored_hk_rows": int(fund_holdings.get("is_hk", pd.Series(dtype=bool)).sum()) if not fund_holdings.empty else 0,
        }
    return details


def _date_from_row(df: pd.DataFrame, column: str) -> str:
    if df.empty or column not in df.columns or pd.isna(df.iloc[-1][column]):
        return ""
    return pd.to_datetime(df.iloc[-1][column]).strftime("%Y-%m-%d")


def _clean_number(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)
