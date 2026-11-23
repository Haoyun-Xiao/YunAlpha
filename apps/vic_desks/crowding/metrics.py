"""Pure, reproducible metrics for the public-fund crowding board.

This module deliberately contains only deterministic calculations.  It does not
read the lake, call a provider, or decide whether a missing value is zero.

The input convention is a mapping per stock with these canonical fields:

``symbol``
    Stable stock identifier.
``holding_mv``
    Estimated public-fund holding market value in CNY at the market date.
``hold_float_ratio``
    Public-fund shares divided by the stock's float shares, as a decimal
    (``0.125`` means 12.5%).
``avg_turnover_20d``
    Average total turnover amount over the previous 20 trading days, in CNY.
``float_mv``
    Float market value in CNY, used for the money-flow ratio.
``delta_hold_float_ratio``
    Change in ``hold_float_ratio`` versus the previous holding period.
``period_return`` and ``drawdown_52w``
    Holding-period return and drawdown from the 52-week high, as decimals.
``big_order_30d`` and ``margin_balance_change_ratio_30d``
    30-day large/extra-large order net amount in CNY and 30-day margin-balance
    change as a decimal ratio. The original note does not establish whether
    its financing change is an absolute amount or a ratio, so this choice is
    an independent implementation and must not be presented as verified.

The formulas are an explicitly labelled independent implementation of the
public data note.  They should not be described as recovered server code.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from bisect import bisect_left, bisect_right
from math import floor, isfinite, log
from typing import Any, Union


Number = Union[int, float]
Row = Mapping[str, Any]
MutableRow = dict[str, Any]

DEFAULT_DIMENSION_WEIGHTS: dict[str, float] = {
    "crowding": 0.30,
    "liquidity": 0.20,
    "adding": 0.15,
    "price": 0.20,
    "money": 0.15,
}


def _number(value: Any) -> float | None:
    """Return a finite number, preserving missing/invalid values as None."""

    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) else None


def _symbol(row: Row) -> str:
    value = row.get("symbol")
    return str(value) if value is not None else ""


def _ranked_symbols(rows: Iterable[Row], field: str, limit: int) -> set[str]:
    """Select a deterministic descending top-N set, ignoring missing values."""

    ranked = [
        (value, _symbol(row))
        for row in rows
        if (value := _number(row.get(field))) is not None and _symbol(row)
    ]
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return {symbol for _, symbol in ranked[: max(0, int(limit))]}


def build_crowding_pool(
    rows: Sequence[Row],
    *,
    top_market_value: int = 200,
    top_float_ratio: int = 150,
    minimum_holding_mv: Number = 1_000_000_000,
    value_key: str = "holding_mv",
) -> list[MutableRow]:
    """Build the board's public-fund crowding pool.

    The public note describes the pool as the union of the top-200 holding-MV
    set and the top-150 float-ratio set, followed by the CNY 1bn holding-MV
    floor.  The function keeps the two membership flags in each returned row
    so a caller can audit exactly why a stock entered the pool.
    """

    mv_top = _ranked_symbols(rows, value_key, top_market_value)
    float_top = _ranked_symbols(rows, "hold_float_ratio", top_float_ratio)
    minimum = _number(minimum_holding_mv)
    if minimum is None:
        raise ValueError("minimum_holding_mv must be finite")

    result: list[MutableRow] = []
    for row in rows:
        symbol = _symbol(row)
        holding_mv = _number(row.get(value_key))
        in_mv_top = symbol in mv_top
        in_float_top = symbol in float_top
        if not symbol or holding_mv is None or holding_mv < minimum:
            continue
        if not (in_mv_top or in_float_top):
            continue
        item = dict(row)
        item["in_top_market_value"] = in_mv_top
        item["in_top_float_ratio"] = in_float_top
        item["in_crowding_pool"] = True
        result.append(item)
    result.sort(key=lambda row: (-(_number(row.get(value_key)) or 0.0), _symbol(row)))
    return result


def liquidation_days(row: Row) -> float | None:
    """Estimate days needed to liquidate holding value at 20-day turnover.

    No liquidity haircut is applied, matching the public note.  Missing or
    non-positive turnover returns None; a zero turnover is not converted to an
    infinite number so the UI can show a missing value explicitly.
    """

    holding_mv = _number(row.get("holding_mv"))
    avg_turnover = _number(row.get("avg_turnover_20d"))
    if holding_mv is None or avg_turnover is None or avg_turnover <= 0:
        return None
    return holding_mv / avg_turnover


def _percentile(value: float | None, ordered: Sequence[float]) -> float | None:
    """Midrank percentile on a 0--100 scale, with deterministic tie handling."""

    if value is None or not ordered:
        return None
    if len(ordered) == 1:
        return 50.0
    left = bisect_left(ordered, value)
    right = bisect_right(ordered, value)
    return 100.0 * (left + (right - left - 1) / 2) / (len(ordered) - 1)


def _raw_dimensions(row: Row) -> dict[str, float | None]:
    holding_mv = _number(row.get("holding_mv"))
    float_ratio = _number(row.get("hold_float_ratio"))
    float_mv = _number(row.get("float_mv"))
    large_flow = _number(row.get("big_order_30d"))
    margin_change = _number(row.get("margin_balance_change_ratio_30d"))
    crowding = None
    if float_ratio is not None and holding_mv is not None and holding_mv > 0:
        # Original response stores fr_pct as percentage points (e.g. 19.37),
        # while the lake adapter exposes a decimal (0.1937). Empirical rank
        # comparison against captured rows confirms this unit convention.
        crowding = float_ratio * 100 + 0.4 * log(holding_mv)
    money = None
    if float_mv is not None and float_mv > 0 and large_flow is not None and margin_change is not None:
        money = -(large_flow / float_mv + 0.05 * margin_change)
    return {
        "crowding": crowding,
        "liquidity": liquidation_days(row),
        "adding": _number(row.get("delta_hold_float_ratio")),
        "price": (
            -(_number(row.get("period_return")) + 0.5 * _number(row.get("drawdown_52w")))
            if _number(row.get("period_return")) is not None
            and _number(row.get("drawdown_52w")) is not None
            else None
        ),
        "money": money,
    }


def _round_score(value: float) -> float:
    # Retain one decimal as observed in the original stock table.
    return max(0.0, min(100.0, floor(value * 10 + 0.5) / 10))


def compute_risk_scores(
    rows: Sequence[Row],
    *,
    weights: Mapping[str, Number] | None = None,
    minimum_scoring_mv: Number = 100_000_000,
) -> list[MutableRow]:
    """Add independent five-dimension risk scores to rows.

    The scoring cohort is holding market value >= CNY 100m and rows with a
    usable market quote (``has_quote`` absent means true). Percentiles are
    calculated per dimension from rows where that dimension is present. A row's
    missing dimensions are omitted and the available weights are renormalized;
    rows with no dimension remain unscored. Output includes raw values,
    percentile values, the weights actually used, and a ``risk_score``.
    """

    selected = dict(DEFAULT_DIMENSION_WEIGHTS)
    if weights is not None:
        for name, value in weights.items():
            if name not in selected:
                raise ValueError(f"unknown risk dimension: {name}")
            numeric = _number(value)
            if numeric is None or numeric < 0:
                raise ValueError(f"invalid weight for {name}")
            selected[name] = numeric
    if sum(selected.values()) <= 0:
        raise ValueError("at least one risk weight must be positive")
    minimum = _number(minimum_scoring_mv)
    if minimum is None or minimum < 0:
        raise ValueError("minimum_scoring_mv must be non-negative")

    raw = [_raw_dimensions(row) for row in rows]
    scoring = [
        index
        for index, row in enumerate(rows)
        if (_number(row.get("holding_mv")) or -1) >= minimum
        and row.get("has_quote", True) is not False
    ]
    cohorts = {
        name: sorted(raw[index][name] for index in scoring if raw[index][name] is not None)
        for name in selected
    }

    result: list[MutableRow] = []
    for index, row in enumerate(rows):
        item = dict(row)
        item["risk_dimensions"] = {}
        item["risk_percentiles"] = {}
        item["risk_weights_used"] = {}
        item["risk_score"] = None
        item["risk_status"] = "outside_scoring_set" if index not in scoring else "missing_dimensions"
        if index in scoring:
            for name in selected:
                value = raw[index][name]
                percentile = _percentile(value, cohorts[name])
                item["risk_dimensions"][name] = value
                item["risk_percentiles"][name] = percentile
            available = {
                name: selected[name]
                for name in selected
                if item["risk_percentiles"][name] is not None and selected[name] > 0
            }
            if available:
                denominator = sum(available.values())
                item["risk_weights_used"] = {
                    name: value / denominator for name, value in available.items()
                }
                score = sum(
                    item["risk_percentiles"][name] * weight / denominator
                    for name, weight in available.items()
                )
                item["risk_score"] = _round_score(score)
                item["risk_status"] = "scored" if len(available) == len(selected) else "partial"
        result.append(item)
    return result


def assign_score_quintiles(
    rows: Sequence[Row], *, score_key: str = "risk_score", buckets: int = 5
) -> list[MutableRow]:
    """Assign deterministic low-to-high score buckets (Q1..Q5)."""

    if buckets < 1:
        raise ValueError("buckets must be positive")
    ranked = sorted(
        ((_number(row.get(score_key)), _symbol(row), index) for index, row in enumerate(rows)),
        key=lambda item: (item[0] is None, item[0] if item[0] is not None else 0, item[1]),
    )
    bucket_by_index: dict[int, int] = {}
    valid = [item for item in ranked if item[0] is not None]
    for position, (_, _, index) in enumerate(valid):
        bucket_by_index[index] = min(buckets, floor(position * buckets / len(valid)) + 1)
    result = []
    for index, row in enumerate(rows):
        item = dict(row)
        bucket = bucket_by_index.get(index)
        item["risk_quintile"] = f"Q{bucket}" if bucket is not None else None
        result.append(item)
    return result


def aggregate_quintile_returns(
    rows: Sequence[Row], *, bucket_key: str = "risk_quintile", return_key: str = "period_return",
    value_key: str | None = None,
) -> dict[str, float | None]:
    """Aggregate bucket returns, using equal or value weights and preserving gaps."""

    grouped: dict[str, list[tuple[float, float | None]]] = {}
    for row in rows:
        bucket = row.get(bucket_key)
        value = _number(row.get(return_key))
        if bucket is None or value is None:
            continue
        weight = _number(row.get(value_key)) if value_key else None
        grouped.setdefault(str(bucket), []).append((value, weight))
    result: dict[str, float | None] = {}
    for bucket, values in grouped.items():
        weighted = [(value, weight) for value, weight in values if weight is not None and weight >= 0]
        if value_key and weighted and sum(weight for _, weight in weighted) > 0:
            total = sum(weight for _, weight in weighted)
            result[bucket] = sum(value * weight for value, weight in weighted) / total
        elif not value_key:
            result[bucket] = sum(value for value, _ in values) / len(values)
        else:
            result[bucket] = None
    return result
