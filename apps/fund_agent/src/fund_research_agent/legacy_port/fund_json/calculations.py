from __future__ import annotations

from datetime import date

import pandas as pd

from .lake import LakeRepository
from .model import FundSpec
from .periods import last_completed_quarter, next_quarter_end, previous_quarter_end, quarter_label


def calculate_holdings(repo: LakeRepository, spec: FundSpec, as_of: date) -> pd.DataFrame:
    positions = repo.positions(spec.fund_symbol)
    if positions.empty:
        return positions
    positions = positions[positions["report_period"] <= pd.Timestamp(as_of)].copy()
    positions = repo.enrich_positions(positions)
    keys = {
        (row.holding_symbol, pd.Timestamp(row.report_period))
        for row in positions[["holding_symbol", "report_period"]].itertuples(index=False)
    }
    intervals = positions[["holding_symbol", "report_period"]].rename(
        columns={"report_period": "period"}
    )
    intervals["start_date"] = intervals["period"].map(previous_quarter_end)
    intervals["end_date"] = intervals["period"]
    average = repo.interval_vwap(intervals).rename(columns={"vwap": "区间均价"})
    dividend = repo.interval_dividends(intervals).rename(columns={"dps": "每股股利1"})

    sell_intervals = intervals.copy()
    sell_intervals["start_date"] = sell_intervals["period"]
    sell_intervals["end_date"] = sell_intervals["period"].map(next_quarter_end)
    sell_average = repo.interval_vwap(sell_intervals).rename(columns={"vwap": "卖出区间均价"})
    sell_dividend = repo.interval_dividends(sell_intervals).rename(columns={"dps": "每股股利2"})

    for frame in (average, dividend, sell_average, sell_dividend):
        positions = positions.merge(
            frame,
            left_on=["holding_symbol", "report_period"],
            right_on=["holding_symbol", "period"],
            how="left",
        ).drop(columns=["period"], errors="ignore")

    lookup = positions.set_index(["holding_symbol", "report_period"])[
        ["shares", "market_value"]
    ].to_dict("index")
    previous_shares: list[float] = []
    previous_values: list[float] = []
    next_has: list[bool] = []
    next_periods: list[pd.Timestamp] = []
    for row in positions.itertuples():
        previous = previous_quarter_end(row.report_period)
        following = next_quarter_end(row.report_period)
        prior = lookup.get((row.holding_symbol, previous), {})
        previous_shares.append(float(prior.get("shares") or 0.0))
        previous_values.append(float(prior.get("market_value") or 0.0))
        next_has.append((row.holding_symbol, following) in keys)
        next_periods.append(following)
    positions["上期数量"] = previous_shares
    positions["上期市值"] = previous_values
    positions["本期交易"] = positions["shares"].fillna(0) - positions["上期数量"]
    positions["下一期有持仓"] = next_has
    positions["下一报告期"] = next_periods
    positions["股息收益1"] = (
        (positions["shares"].fillna(0) + positions["上期数量"])
        / 2
        * positions["每股股利1"].fillna(0)
    )
    positions["股息收益2"] = (
        (positions["shares"].fillna(0) + positions["上期数量"])
        / 2
        * positions["每股股利2"].fillna(0)
    )
    positions["个股估算盈亏"] = (
        positions["股息收益1"]
        + positions["market_value"].fillna(0)
        - positions["上期市值"]
        - positions["本期交易"] * positions["区间均价"].fillna(0)
    )
    positions["对应估算卖出盈亏"] = pd.NA
    sold = ~positions["下一期有持仓"]
    positions.loc[sold, "对应估算卖出盈亏"] = (
        positions.loc[sold, "卖出区间均价"].fillna(0) * positions.loc[sold, "shares"].fillna(0)
        - positions.loc[sold, "market_value"].fillna(0)
        + positions.loc[sold, "股息收益2"]
    )

    first = pd.Timestamp(spec.tenure_start) + pd.offsets.QuarterEnd(0)
    last_date = min(spec.tenure_end, as_of) if spec.tenure_end else as_of
    last = last_completed_quarter(last_date)
    positions = positions[
        (positions["report_period"] >= first) & (positions["report_period"] <= last)
    ]
    return positions.sort_values(["report_period", "holding_symbol"]).reset_index(drop=True)


def pnl_by_quarter(df: pd.DataFrame, as_of: date) -> dict[str, dict[str, dict[str, int]]]:
    if df.empty:
        return {}
    buckets: dict[str, dict[str, dict[str, float]]] = {}

    def add(period: pd.Timestamp, row: object, value: float) -> None:
        q = quarter_label(period)
        bucket = buckets.setdefault(q, {"大类": {}, "一级": {}, "个股": {}})
        labels = {
            "大类": row.大类 or "未知大类",
            "一级": row.申万一级 or "未知行业",
            "个股": row.股票名称 or row.holding_symbol,
        }
        for level, label in labels.items():
            bucket[level][label] = bucket[level].get(label, 0.0) + float(value)

    for row in df.itertuples():
        if pd.notna(row.个股估算盈亏):
            add(row.report_period, row, row.个股估算盈亏)
        if (
            not row.下一期有持仓
            and pd.notna(row.对应估算卖出盈亏)
            and row.下一报告期 <= last_completed_quarter(as_of)
        ):
            add(row.下一报告期, row, row.对应估算卖出盈亏)
    return {
        q: {
            level: {
                label: round(value / 10000)
                for label, value in sorted(values.items(), key=lambda item: item[1], reverse=True)
                if abs(value / 10000) >= 1
            }
            for level, values in levels.items()
        }
        for q, levels in sorted(buckets.items())
    }


def annual_and_cumulative(
    quarterly: dict[str, dict[str, dict[str, int]]],
) -> tuple[dict[str, dict[str, dict[str, int]]], dict[str, dict[str, int]]]:
    annual: dict[str, dict[str, dict[str, int]]] = {}
    cumulative = {"大类": {}, "一级": {}, "个股": {}}
    for quarter, levels in quarterly.items():
        year = f"{quarter[:4]}年"
        yearly = annual.setdefault(year, {"大类": {}, "一级": {}, "个股": {}})
        for level, values in levels.items():
            for label, value in values.items():
                yearly[level][label] = yearly[level].get(label, 0) + value
                cumulative[level][label] = cumulative[level].get(label, 0) + value
    for levels in annual.values():
        for level, values in levels.items():
            levels[level] = dict(sorted(values.items(), key=lambda item: item[1], reverse=True))
    for level, values in cumulative.items():
        cumulative[level] = dict(sorted(values.items(), key=lambda item: item[1], reverse=True))
    return annual, cumulative
