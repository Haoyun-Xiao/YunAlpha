from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import pandas as pd


TRADING_DAYS = 252
PERIODS_PER_YEAR = {
    "daily": 252,
    "weekly": 52,
    "monthly": 12,
}
FREQUENCY_LABELS = {
    "daily": "日频",
    "weekly": "周频",
    "monthly": "月频",
}
PERIOD_RETURN_WINDOWS = (
    ("1m", "近1月", pd.DateOffset(months=1)),
    ("3m", "近3月", pd.DateOffset(months=3)),
    ("6m", "近6月", pd.DateOffset(months=6)),
    ("1y", "近1年", pd.DateOffset(years=1)),
    ("3y", "近3年", pd.DateOffset(years=3)),
    ("all", "运作以来", None),
)


@dataclass(frozen=True)
class FundSeries:
    id: str
    name: str
    manager: str
    nav: pd.Series


@dataclass(frozen=True)
class BacktestRequest:
    funds: list[dict[str, Any]]
    sleeves: list[dict[str, Any]] | None = None
    weight_mode: str = "equal"
    benchmark: str = "000300.SH"
    start_date: str | None = "2021-01-01"
    end_date: str | None = "2026-06-30"
    rebalance_plan: list[dict[str, Any]] | None = None
    initial_cash: float | None = None
    fee_config: dict[str, Any] | None = None
    data_frequency: str = "daily"


def run_fund_backtest(
    request: BacktestRequest,
    funds: dict[str, FundSeries],
    benchmarks: dict[str, FundSeries],
) -> dict[str, Any]:
    return _run_fund_backtest_core(request, funds, benchmarks)["result"]


def run_fund_backtest_core(
    request: BacktestRequest,
    funds: dict[str, FundSeries],
    benchmarks: dict[str, FundSeries],
) -> dict[str, Any]:
    return _run_fund_backtest_core(request, funds, benchmarks)


def run_fund_backtest_analysis_window(
    request: BacktestRequest,
    funds: dict[str, FundSeries],
    benchmarks: dict[str, FundSeries],
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    core = _run_fund_backtest_core(request, funds, benchmarks)
    return _analysis_window_result(core, request, funds, start_date, end_date)


def _run_fund_backtest_core(
    request: BacktestRequest,
    funds: dict[str, FundSeries],
    benchmarks: dict[str, FundSeries],
) -> dict[str, Any]:
    selected_ids = _selected_fund_ids(request.funds, request.rebalance_plan)
    missing = [fund_id for fund_id in selected_ids if fund_id not in funds]
    if missing:
        raise ValueError("未找到基金: " + ", ".join(missing))
    if request.benchmark not in benchmarks:
        raise ValueError(f"未找到基准: {request.benchmark}")

    has_rebalance_plan = isinstance(request.rebalance_plan, list) and len(request.rebalance_plan) > 0
    weights = (
        _latest_plan_weights(request.rebalance_plan, selected_ids)
        if has_rebalance_plan
        else _resolve_weights(request.funds, request.weight_mode, selected_ids)
    )
    frequency = _normalize_data_frequency(request.data_frequency)
    frequency_label = FREQUENCY_LABELS[frequency]
    periods_per_year = PERIODS_PER_YEAR[frequency]
    nav_frame = _resample_nav_frame(pd.concat({fund_id: funds[fund_id].nav for fund_id in selected_ids}, axis=1), frequency)
    benchmark_nav = _resample_nav_series(benchmarks[request.benchmark].nav.rename("benchmark"), frequency)

    start_ts = pd.to_datetime(request.start_date) if request.start_date else benchmark_nav.index.min()
    end_ts = pd.to_datetime(request.end_date) if request.end_date else benchmark_nav.index.max()
    effective_end = min(end_ts, max(funds[fund_id].nav.index.max() for fund_id in selected_ids), benchmark_nav.index.max())
    calendar = benchmark_nav[(benchmark_nav.index >= start_ts) & (benchmark_nav.index <= effective_end)].index
    if len(calendar) < 2:
        raise ValueError("共同净值区间不足，无法回测")

    fund_nav = _align_funds_to_calendar(nav_frame, calendar)
    benchmark_series = benchmark_nav.reindex(benchmark_nav.index.union(calendar)).sort_index().ffill().reindex(calendar)
    available_mask = fund_nav.notna()
    if not available_mask.any(axis=1).any():
        raise ValueError("所选基金在回测区间内均无可用净值")
    first_available_row = available_mask.any(axis=1).idxmax()
    fund_nav = fund_nav.loc[first_available_row:]
    benchmark_series = benchmark_series.loc[first_available_row:]
    warnings = _availability_warnings(fund_nav, selected_ids, funds)
    warnings.extend(_sample_size_warnings(fund_nav, frequency, frequency_label))

    cash_mode = request.initial_cash is not None
    account_series: pd.Series | None = None
    final_positions: list[dict[str, Any]] = []
    cash_metrics: dict[str, Any] = {}
    if cash_mode:
        if float(request.initial_cash or 0.0) <= 0:
            raise ValueError("初始资金必须大于 0")
        cash_plan = request.rebalance_plan or _single_target_rebalance_plan(weights, fund_nav.index.min())
        (
            portfolio_nav,
            account_series,
            contribution_records,
            rebalance_dates,
            begin_weights,
            end_weights,
            final_target,
            cash_state,
        ) = _cash_constrained_rebalanced_nav(
            fund_nav,
            cash_plan,
            selected_ids,
            float(request.initial_cash or 0.0),
            _normalize_fee_config(request.fee_config),
            frequency,
            frequency_label,
        )
        warnings.extend(cash_state["warnings"])
        weights = {fund_id: float(cash_state["final_weights"].get(fund_id, 0.0)) for fund_id in selected_ids}
        cash_metrics = {
            "initial_cash": _finite(float(request.initial_cash or 0.0)),
            "final_value": _finite(cash_state["final_value"]),
            "net_contribution": _finite(cash_state["net_contribution"]),
            "total_profit": _finite(cash_state["final_value"] - cash_state["net_contribution"]),
            "cash_balance": _finite(cash_state["cash"]),
            "total_fees": _finite(cash_state["total_fees"]),
        }
        final_positions = _final_position_rows(
            cash_state["positions"],
            fund_nav.loc[fund_nav.index[-1]],
            cash_state["final_value"],
            funds,
            cash_state["sleeve_by_fund"],
        )
    elif has_rebalance_plan:
        portfolio_nav, contribution_records, rebalance_dates, begin_weights, end_weights, final_target = _planned_rebalanced_nav(
            fund_nav,
            request.rebalance_plan or [],
            selected_ids,
            warnings,
            frequency,
            frequency_label,
        )
        weights = {fund_id: float(final_target.get(fund_id, 0.0)) for fund_id in selected_ids}
    else:
        target_weights = pd.Series(weights, dtype=float)
        portfolio_nav, contribution_records, rebalance_dates, begin_weights, end_weights = _quarterly_rebalanced_nav(
            fund_nav,
            target_weights,
        )
    portfolio_returns = portfolio_nav.pct_change().fillna(0.0)

    benchmark_norm = benchmark_series / benchmark_series.iloc[0]
    drawdown = _drawdown(portfolio_nav)
    benchmark_drawdown = _drawdown(benchmark_norm)
    annual_contributions = _annual_contributions(portfolio_nav, contribution_records)
    period_returns = _period_returns(portfolio_nav, benchmark_norm)
    period_contributions = _period_contributions(portfolio_nav, contribution_records)
    fund_contributions = _fund_contributions(fund_nav, weights, contribution_records)

    result = {
        "window": {
            "start": fund_nav.index.min().strftime("%Y-%m-%d"),
            "end": fund_nav.index.max().strftime("%Y-%m-%d"),
            "days": int(len(fund_nav)),
        },
        "funds": [
            {
                "id": fund_id,
                "name": funds[fund_id].name,
                "manager": funds[fund_id].manager,
                "weight": weights[fund_id],
            }
            for fund_id in selected_ids
        ],
        "weights": weights,
        "warnings": warnings,
        "frequency": frequency,
        "frequency_label": frequency_label,
        "periods_per_year": periods_per_year,
        "rebalance_dates": [date.strftime("%Y-%m-%d") for date in rebalance_dates],
        "metrics": {**_metrics(portfolio_nav, portfolio_returns, periods_per_year), **cash_metrics},
        "series": {
            "portfolio": _series_points(portfolio_nav),
            "benchmark": _series_points(benchmark_norm),
            "drawdown": _series_points(drawdown),
            "benchmark_drawdown": _series_points(benchmark_drawdown),
        },
        "annual_returns": _annual_returns(portfolio_nav, benchmark_norm),
        "annual_contributions": annual_contributions,
        "period_returns": period_returns,
        "period_contributions": period_contributions,
        "fund_contributions": fund_contributions,
        "attribution": _build_attribution(
            request,
            funds,
            fund_nav,
            benchmark_norm,
            portfolio_nav,
            portfolio_returns,
            contribution_records,
            begin_weights,
            end_weights,
            annual_contributions,
            periods_per_year,
        ),
    }
    if cash_mode:
        result["cash_mode"] = True
        result["final_positions"] = final_positions
        result["transactions"] = cash_state["transactions"]
        result["series"]["account_value"] = _series_points(account_series if account_series is not None else pd.Series(dtype=float))
    return {
        "result": result,
        "fund_nav": fund_nav,
        "benchmark_norm": benchmark_norm,
        "portfolio_nav": portfolio_nav,
        "portfolio_returns": portfolio_returns,
        "contribution_records": contribution_records,
        "begin_weights": begin_weights,
        "end_weights": end_weights,
        "periods_per_year": periods_per_year,
    }


def _analysis_window_result(
    core: dict[str, Any],
    request: BacktestRequest,
    funds: dict[str, FundSeries],
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    portfolio_nav = core["portfolio_nav"].dropna().sort_index()
    requested_start = pd.to_datetime(start_date, errors="coerce")
    requested_end = pd.to_datetime(end_date, errors="coerce")
    if pd.isna(requested_start) or pd.isna(requested_end):
        raise ValueError("区间日期非法")
    if requested_start > requested_end:
        raise ValueError("区间开始日期不能晚于结束日期")

    window_index = portfolio_nav[(portfolio_nav.index >= requested_start) & (portfolio_nav.index <= requested_end)].index
    if len(window_index) < 2:
        raise ValueError("区间净值点不足，无法计算归因")
    first_date = pd.Timestamp(window_index[0])
    last_date = pd.Timestamp(window_index[-1])
    base_nav = float(portfolio_nav.loc[first_date])
    if base_nav <= 0:
        raise ValueError("区间起点净值无效")

    window_portfolio_nav = portfolio_nav.loc[window_index] / base_nav
    benchmark_norm = core["benchmark_norm"].reindex(core["benchmark_norm"].index.union(window_index)).sort_index().ffill()
    benchmark_base = float(benchmark_norm.loc[first_date])
    if benchmark_base <= 0:
        raise ValueError("区间起点基准净值无效")
    window_benchmark = benchmark_norm.loc[window_index] / benchmark_base

    fund_nav = core["fund_nav"].reindex(window_index)
    contribution_records = _window_contribution_records(core["contribution_records"], first_date, last_date, base_nav)
    begin_weights = core["begin_weights"].reindex(window_index).ffill().fillna(0.0)
    end_weights = core["end_weights"].reindex(window_index).ffill().fillna(0.0)
    portfolio_returns = window_portfolio_nav.pct_change(fill_method=None).fillna(0.0)
    annual_contributions = _annual_contributions(window_portfolio_nav, contribution_records)
    attribution = _build_attribution(
        request,
        funds,
        fund_nav,
        window_benchmark,
        window_portfolio_nav,
        portfolio_returns,
        contribution_records,
        begin_weights,
        end_weights,
        annual_contributions,
        core["periods_per_year"],
    )
    result = core["result"]
    return {
        "frequency": result.get("frequency"),
        "frequency_label": result.get("frequency_label"),
        "window": {
            "start": first_date.strftime("%Y-%m-%d"),
            "end": last_date.strftime("%Y-%m-%d"),
            "days": int(len(window_index)),
        },
        "warnings": list(result.get("warnings") or []),
        "attribution": attribution,
    }


def _window_contribution_records(
    contribution_records: pd.DataFrame,
    first_date: pd.Timestamp,
    last_date: pd.Timestamp,
    base_nav: float,
) -> pd.DataFrame:
    if contribution_records.empty:
        return contribution_records.copy()
    records = contribution_records[
        (contribution_records["date"] > first_date)
        & (contribution_records["date"] <= last_date)
    ].copy()
    if not records.empty:
        records["contribution"] = records["contribution"] / base_nav
    return records


def _normalize_data_frequency(value: Any) -> str:
    frequency = str(value or "daily").strip().lower()
    if frequency not in PERIODS_PER_YEAR:
        raise ValueError("数据频率必须是 daily、weekly 或 monthly")
    return frequency


def _resample_nav_frame(nav_frame: pd.DataFrame, frequency: str) -> pd.DataFrame:
    if frequency == "daily":
        return nav_frame
    return pd.concat(
        {column: _resample_nav_series(nav_frame[column], frequency) for column in nav_frame.columns},
        axis=1,
    )


def _resample_nav_series(series: pd.Series, frequency: str) -> pd.Series:
    clean = series.dropna().sort_index().astype(float)
    if frequency == "daily" or clean.empty:
        return clean
    period_freq = "W-FRI" if frequency == "weekly" else "M"
    return clean.groupby(clean.index.to_period(period_freq)).tail(1)


def _sample_size_warnings(fund_nav: pd.DataFrame, frequency: str, frequency_label: str) -> list[str]:
    sample_count = max(len(fund_nav.index) - 1, 0)
    if frequency == "weekly" and sample_count < 52:
        return [f"{frequency_label}相关性样本仅 {sample_count} 期，互补性和相关性解释力偏弱。"]
    if frequency == "monthly" and sample_count < 24:
        return [f"{frequency_label}相关性样本仅 {sample_count} 期，互补性和相关性解释力偏弱。"]
    return []


def _selected_fund_ids(items: list[dict[str, Any]], rebalance_plan: list[dict[str, Any]] | None = None) -> list[str]:
    ids: list[str] = []
    for item in items:
        fund_id = str(item.get("id") or item.get("code") or "").strip()
        if fund_id and fund_id not in ids:
            ids.append(fund_id)
    for node in rebalance_plan or []:
        for holding in node.get("holdings", []) if isinstance(node, dict) else []:
            fund_id = str(holding.get("fundId") or holding.get("id") or holding.get("code") or "").strip()
            if fund_id and fund_id not in ids:
                ids.append(fund_id)
    if not ids:
        raise ValueError("请至少选择 1 只基金")
    return ids


def _resolve_weights(
    items: list[dict[str, Any]],
    weight_mode: str,
    selected_ids: list[str],
) -> dict[str, float]:
    if weight_mode == "equal":
        weight = 1.0 / len(selected_ids)
        return {fund_id: weight for fund_id in selected_ids}

    raw: dict[str, float] = {}
    for item in items:
        fund_id = str(item.get("id") or item.get("code") or "").strip()
        if fund_id not in selected_ids:
            continue
        try:
            value = float(item.get("weight", 0.0))
        except (TypeError, ValueError):
            value = 0.0
        raw[fund_id] = max(value, 0.0)

    total = sum(raw.get(fund_id, 0.0) for fund_id in selected_ids)
    if total <= 0:
        raise ValueError("权重之和必须大于 0")
    return {fund_id: raw.get(fund_id, 0.0) / total for fund_id in selected_ids}


def _metrics(nav: pd.Series, returns: pd.Series, periods_per_year: int = TRADING_DAYS) -> dict[str, Any]:
    total_return = float(nav.iloc[-1] / nav.iloc[0] - 1.0)
    years = max((nav.index[-1] - nav.index[0]).days / 365.25, 1 / periods_per_year)
    annual_return = float((1.0 + total_return) ** (1.0 / years) - 1.0)
    annual_vol = float(returns.std(ddof=0) * math.sqrt(periods_per_year))
    max_drawdown = float(_drawdown(nav).min())
    sharpe = annual_return / annual_vol if annual_vol > 0 else None
    calmar = annual_return / abs(max_drawdown) if max_drawdown < 0 else None
    win_rate = float((returns.iloc[1:] > 0).mean()) if len(returns) > 1 else None
    return {
        "total_return": _finite(total_return),
        "annual_return": _finite(annual_return),
        "annual_volatility": _finite(annual_vol),
        "max_drawdown": _finite(max_drawdown),
        "sharpe": _finite(sharpe),
        "calmar": _finite(calmar),
        "win_rate": _finite(win_rate),
        "annual_win_rate": _finite(_annual_win_rate(nav)),
        **_drawdown_profile(nav),
    }


def _drawdown_profile(nav: pd.Series) -> dict[str, Any]:
    clean = nav.dropna().sort_index()
    if clean.empty:
        return {"drawdown_period": "", "drawdown_months": None, "recovery_months": None}

    nav_dd = clean
    cummax = nav_dd.expanding().max()
    drawdown = nav_dd / cummax - 1.0
    trough_date = pd.Timestamp(drawdown.idxmin())
    peak_value = cummax.loc[trough_date]
    peak_date = pd.Timestamp(nav_dd.loc[:trough_date][nav_dd.loc[:trough_date] == peak_value].index[-1])
    drawdown_months = (trough_date.year - peak_date.year) * 12 + (trough_date.month - peak_date.month)

    peak_nav = float(nav_dd.loc[peak_date])
    recovery_dates = nav_dd.loc[trough_date:][nav_dd.loc[trough_date:] >= peak_nav].index
    recovery_months = round((pd.Timestamp(recovery_dates[0]) - trough_date).days / 30, 1) if len(recovery_dates) else None

    return {
        "drawdown_period": f"{peak_date.year}年{peak_date.month}月 - {trough_date.year}年{trough_date.month}月",
        "drawdown_months": int(drawdown_months),
        "recovery_months": _finite(recovery_months),
    }


def _annual_win_rate(nav: pd.Series) -> float | None:
    clean = nav.dropna().sort_index()
    if len(clean) < 2:
        return None

    start = pd.Timestamp(clean.index[0])
    end = pd.Timestamp(clean.index[-1])
    end_year_is_complete = end.month == 12 and end.day >= 28
    complete_years = [
        int(year)
        for year in sorted(clean.index.year.unique())
        if year < end.year or (year == end.year and end_year_is_complete)
    ]

    returns: list[float] = []
    for year in complete_years:
        year_nav = clean[clean.index.year == year]
        if year_nav.empty:
            continue
        if year == start.year:
            if start.month != 1 or start.day > 7:
                continue
            base = float(year_nav.iloc[0])
        else:
            previous_nav = clean[clean.index.year < year]
            if previous_nav.empty:
                continue
            base = float(previous_nav.iloc[-1])
        if base <= 0:
            continue
        returns.append(float(year_nav.iloc[-1] / base - 1.0))

    if not returns:
        return None
    return sum(1 for value in returns if value > 0) / len(returns)


def _align_funds_to_calendar(nav_frame: pd.DataFrame, calendar: pd.DatetimeIndex) -> pd.DataFrame:
    aligned = pd.DataFrame(index=calendar)
    for fund_id in nav_frame.columns:
        raw = nav_frame[fund_id].dropna().sort_index()
        if raw.empty:
            aligned[fund_id] = pd.NA
            continue
        series = raw.reindex(raw.index.union(calendar)).sort_index().ffill().reindex(calendar)
        series.loc[series.index < raw.index.min()] = pd.NA
        series.loc[series.index > raw.index.max()] = pd.NA
        aligned[fund_id] = series.astype(float)
    return aligned


def _availability_warnings(
    fund_nav: pd.DataFrame,
    selected_ids: list[str],
    funds: dict[str, FundSeries],
) -> list[str]:
    warnings: list[str] = []
    window_start = fund_nav.index.min()
    window_end = fund_nav.index.max()
    for fund_id in selected_ids:
        available = fund_nav[fund_id].dropna()
        name = funds[fund_id].name
        if available.empty:
            warnings.append(f"{name}({fund_id}) 在本次回测区间内没有可用净值，已全程不参与组合。")
            continue
        if available.index.min() > window_start:
            warnings.append(
                f"{name}({fund_id}) 从 {available.index.min().strftime('%Y-%m-%d')} 起才有净值；"
                "缺失时段由其他可用基金承担，下一次季度调仓后再加入组合。"
            )
        if available.index.max() < window_end:
            warnings.append(
                f"{name}({fund_id}) 在 {available.index.max().strftime('%Y-%m-%d')} 后缺少净值；"
                "后续调仓点会从可用基金中重新分配权重。"
            )
    return warnings


def _latest_plan_weights(rebalance_plan: list[dict[str, Any]] | None, selected_ids: list[str]) -> dict[str, float]:
    if not rebalance_plan:
        return {fund_id: 0.0 for fund_id in selected_ids}
    latest = max(
        [node for node in rebalance_plan if isinstance(node, dict)],
        key=lambda node: str(node.get("date") or ""),
        default={},
    )
    weights = {fund_id: 0.0 for fund_id in selected_ids}
    for holding in latest.get("holdings", []) if isinstance(latest, dict) else []:
        fund_id = str(holding.get("fundId") or holding.get("id") or holding.get("code") or "").strip()
        if fund_id in weights:
            weights[fund_id] = _holding_weight_fraction(holding.get("weight"))
    return weights


def _single_target_rebalance_plan(weights: dict[str, float], date: pd.Timestamp) -> list[dict[str, Any]]:
    return [
        {
            "date": pd.Timestamp(date).strftime("%Y-%m-%d"),
            "source": "initial",
            "holdings": [
                {"fundId": fund_id, "weight": float(weight) * 100.0}
                for fund_id, weight in weights.items()
            ],
        }
    ]


def _normalize_fee_config(config: dict[str, Any] | None) -> dict[str, float]:
    config = config if isinstance(config, dict) else {}
    purchase = _non_negative_float(config.get("purchase"), 0.0)
    redemption = _non_negative_float(config.get("redemption"), 0.0)
    transaction = _non_negative_float(config.get("transaction"), 0.0)
    return {"purchase": purchase, "redemption": redemption, "transaction": transaction}


def _non_negative_float(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if number < 0:
        raise ValueError("费率不能为负")
    return number


def _cash_constrained_rebalanced_nav(
    fund_nav: pd.DataFrame,
    rebalance_plan: list[dict[str, Any]],
    selected_ids: list[str],
    initial_cash: float,
    fee_config: dict[str, float],
    frequency: str = "daily",
    frequency_label: str = "日频",
) -> tuple[pd.Series, pd.Series, pd.DataFrame, list[pd.Timestamp], pd.DataFrame, pd.DataFrame, pd.Series, dict[str, Any]]:
    returns = fund_nav.pct_change(fill_method=None)
    trade_dates = pd.DatetimeIndex(fund_nav.index).sort_values()
    warnings: list[str] = []
    plan_targets = _normalize_cash_rebalance_plan(rebalance_plan, trade_dates, selected_ids, warnings, frequency, frequency_label)
    plan_by_date = {item[0]: item[1:] for item in plan_targets}
    scheduled_rebalance_dates = _rebalance_dates(trade_dates)
    rebalance_dates = sorted(set([date for date, *_ in plan_targets] + scheduled_rebalance_dates))
    quarterly_set = set(pd.Timestamp(date) for date in scheduled_rebalance_dates)

    cash = float(initial_cash)
    unit_shares = float(initial_cash)
    net_contribution = float(initial_cash)
    total_fees = 0.0
    positions: dict[str, dict[str, float]] = {}
    transactions: list[dict[str, Any]] = [
        _transaction_row(
            first_date := pd.Timestamp(trade_dates[0]),
            "initial_cash",
            "初始资金",
            cash_change=initial_cash,
            cash_balance=cash,
        )
    ]
    current_target = _cash_target_before_or_on(plan_targets, trade_dates[0])[0]
    current_sleeves = _cash_target_before_or_on(plan_targets, trade_dates[0])[2]

    nav_values: dict[pd.Timestamp, float] = {pd.Timestamp(trade_dates[0]): 1.0}
    account_values: dict[pd.Timestamp, float] = {}
    contribution_rows: list[dict[str, Any]] = []
    begin_weight_rows: list[dict[str, Any]] = []
    end_weight_rows: list[dict[str, Any]] = []

    if first_date in plan_by_date:
        current_target, cash_flow, current_sleeves = plan_by_date[first_date]
        cash_ref = {"cash": cash}
        total_fees += _sell_positions_for_cash_flow(
            positions,
            cash_ref,
            abs(min(cash_flow, 0.0)),
            fund_nav.loc[first_date],
            fee_config,
            selected_ids,
            warnings,
            transactions,
            first_date,
        )
        cash = cash_ref["cash"]
        flow_unit_nav = _account_value(cash, positions, fund_nav.loc[first_date]) / unit_shares if unit_shares > 0 else 1.0
        cash, unit_shares, net_contribution = _apply_cash_flow(
            cash,
            cash_flow,
            flow_unit_nav,
            unit_shares,
            net_contribution,
            first_date,
        )
        if abs(cash_flow) > 1e-12:
            transactions.append(_cash_flow_transaction(first_date, cash_flow, cash))
    transaction_start = len(transactions)
    transaction_weight_before = _position_weights(cash, positions, fund_nav.loc[first_date], selected_ids)
    total_fees += _rebalance_cash_positions(
        positions,
        cash_ref := {"cash": cash},
        current_target,
        fund_nav.loc[first_date],
        fee_config,
        selected_ids,
        warnings,
        transactions,
        first_date,
    )
    cash = cash_ref["cash"]
    first_value = _account_value(cash, positions, fund_nav.loc[first_date])
    account_values[first_date] = first_value
    first_weights = _position_weights(cash, positions, fund_nav.loc[first_date], selected_ids)
    _apply_transaction_weight_changes(transactions, transaction_start, transaction_weight_before, first_weights)
    begin_weight_rows.append(_weight_row(first_date, first_weights, pd.Index(selected_ids)))
    end_weight_rows.append(_weight_row(first_date, first_weights, pd.Index(selected_ids)))

    previous_weights = first_weights
    previous_nav = 1.0
    previous_date = first_date
    for raw_date in trade_dates[1:]:
        date = pd.Timestamp(raw_date)
        daily_returns = returns.loc[date].reindex(previous_weights.index)
        valid_returns = daily_returns.dropna()
        active_weights = previous_weights.reindex(valid_returns.index).dropna()
        active_total = float(active_weights.sum())
        if active_total > 0 and not valid_returns.empty:
            for fund_id, weight in active_weights.items():
                contribution_rows.append(
                    {
                        "date": date,
                        "fund_id": fund_id,
                        "weight_begin": float(weight),
                        "fund_return": float(valid_returns[fund_id]),
                        "contribution": float(previous_nav * weight * valid_returns[fund_id]),
                    }
                )
            begin_weight_rows.append(_weight_row(date, active_weights, pd.Index(selected_ids)))
        else:
            begin_weight_rows.append(_weight_row(date, previous_weights, pd.Index(selected_ids)))

        account_before_flow = _account_value(cash, positions, fund_nav.loc[date])
        unit_nav_before_flow = account_before_flow / unit_shares if unit_shares > 0 else previous_nav
        transaction_start: int | None = None
        transaction_weight_before: pd.Series | None = None
        if date in plan_by_date:
            current_target, cash_flow, current_sleeves = plan_by_date[date]
            transaction_start = len(transactions)
            transaction_weight_before = _position_weights(cash, positions, fund_nav.loc[date], selected_ids)
            cash_ref = {"cash": cash}
            total_fees += _sell_positions_for_cash_flow(
                positions,
                cash_ref,
                abs(min(cash_flow, 0.0)),
                fund_nav.loc[date],
                fee_config,
                selected_ids,
                warnings,
                transactions,
                date,
            )
            cash = cash_ref["cash"]
            account_before_flow = _account_value(cash, positions, fund_nav.loc[date])
            unit_nav_before_flow = account_before_flow / unit_shares if unit_shares > 0 else previous_nav
            cash, unit_shares, net_contribution = _apply_cash_flow(
                cash,
                cash_flow,
                unit_nav_before_flow,
                unit_shares,
                net_contribution,
                date,
            )
            if abs(cash_flow) > 1e-12:
                transactions.append(_cash_flow_transaction(date, cash_flow, cash))
            total_fees += _rebalance_cash_positions(
                positions,
                cash_ref := {"cash": cash},
                current_target,
                fund_nav.loc[date],
                fee_config,
                selected_ids,
                warnings,
                transactions,
                date,
            )
            cash = cash_ref["cash"]
        elif date in quarterly_set:
            transaction_start = len(transactions)
            transaction_weight_before = _position_weights(cash, positions, fund_nav.loc[date], selected_ids)
            total_fees += _rebalance_cash_positions(
                positions,
                cash_ref := {"cash": cash},
                current_target,
                fund_nav.loc[date],
                fee_config,
                selected_ids,
                warnings,
                transactions,
                date,
            )
            cash = cash_ref["cash"]

        account_after = _account_value(cash, positions, fund_nav.loc[date])
        unit_nav_after = account_after / unit_shares if unit_shares > 0 else previous_nav
        nav_values[date] = unit_nav_after
        account_values[date] = account_after
        end_weights = _position_weights(cash, positions, fund_nav.loc[date], selected_ids)
        if transaction_start is not None and transaction_weight_before is not None:
            _apply_transaction_weight_changes(transactions, transaction_start, transaction_weight_before, end_weights)
        end_weight_rows.append(_weight_row(date, end_weights, pd.Index(selected_ids)))
        previous_weights = end_weights
        previous_nav = unit_nav_after
        previous_date = date

    final_date = pd.Timestamp(previous_date)
    final_value = _account_value(cash, positions, fund_nav.loc[final_date])
    final_weights = _position_weights(cash, positions, fund_nav.loc[final_date], selected_ids).reindex(selected_ids).fillna(0.0)
    final_target = _cash_target_before_or_on(plan_targets, trade_dates[-1])[0].reindex(selected_ids).fillna(0.0)
    return (
        pd.Series(nav_values).sort_index(),
        pd.Series(account_values).sort_index(),
        pd.DataFrame(contribution_rows),
        sorted(dict.fromkeys(pd.Timestamp(date) for date in rebalance_dates if date >= trade_dates[0] and date <= trade_dates[-1])),
        pd.DataFrame(begin_weight_rows).set_index("date").sort_index(),
        pd.DataFrame(end_weight_rows).set_index("date").sort_index(),
        final_target,
        {
            "cash": cash,
            "positions": positions,
            "total_fees": total_fees,
            "net_contribution": net_contribution,
            "final_value": final_value,
            "final_weights": final_weights.to_dict(),
            "sleeve_by_fund": current_sleeves,
            "warnings": warnings,
            "transactions": transactions,
        },
    )


def _normalize_cash_rebalance_plan(
    rebalance_plan: list[dict[str, Any]],
    trade_dates: pd.DatetimeIndex,
    selected_ids: list[str],
    warnings: list[str] | None = None,
    frequency: str = "daily",
    frequency_label: str = "日频",
) -> list[tuple[pd.Timestamp, pd.Series, float, dict[str, str]]]:
    scheduled: dict[pd.Timestamp, tuple[pd.Series, float, dict[str, str]]] = {}
    selected_set = set(selected_ids)
    for node in rebalance_plan:
        if not isinstance(node, dict):
            raise ValueError("调仓计划格式错误")
        raw_date = pd.to_datetime(node.get("date"), errors="coerce")
        if pd.isna(raw_date):
            raise ValueError("调仓日期非法")
        holdings = node.get("holdings")
        if not isinstance(holdings, list) or not holdings:
            raise ValueError("调仓日必须至少有一只基金")
        weights: dict[str, float] = {}
        sleeves: dict[str, str] = {}
        for holding in holdings:
            fund_id = str(holding.get("fundId") or holding.get("id") or holding.get("code") or "").strip()
            if not fund_id:
                raise ValueError("调仓明细缺少基金")
            if fund_id in weights:
                raise ValueError(f"{raw_date.strftime('%Y-%m-%d')} 存在重复基金: {fund_id}")
            if fund_id not in selected_set:
                selected_set.add(fund_id)
            weight = _holding_weight_fraction(holding.get("weight"))
            if weight < 0:
                raise ValueError("权重不能为负")
            weights[fund_id] = weight
            sleeves[fund_id] = str(holding.get("sleeveId") or "").strip()
        total = sum(weights.values())
        if abs(total - 1.0) > 0.0001:
            raise ValueError(f"{raw_date.strftime('%Y-%m-%d')} 权重合计必须等于 100%，当前为 {total * 100:.2f}%")
        future_dates = trade_dates[trade_dates >= pd.Timestamp(raw_date)]
        if len(future_dates) == 0:
            continue
        actual_date = pd.Timestamp(future_dates[0])
        _append_frequency_mapping_warning(warnings, frequency, frequency_label, raw_date, actual_date)
        try:
            cash_flow = float(node.get("cashFlow") or 0.0)
        except (TypeError, ValueError):
            cash_flow = 0.0
        scheduled[actual_date] = (pd.Series(weights, dtype=float).reindex(selected_ids).fillna(0.0), cash_flow, sleeves)
    if not scheduled:
        raise ValueError("调仓计划在回测区间内没有有效调仓日")
    return [(date, *values) for date, values in sorted(scheduled.items(), key=lambda item: item[0])]


def _cash_target_before_or_on(
    plan_targets: list[tuple[pd.Timestamp, pd.Series, float, dict[str, str]]],
    date: pd.Timestamp,
) -> tuple[pd.Series, float, dict[str, str]]:
    target, cash_flow, sleeves = plan_targets[0][1], plan_targets[0][2], plan_targets[0][3]
    for plan_date, plan_target, plan_cash_flow, plan_sleeves in plan_targets:
        if plan_date <= date:
            target, cash_flow, sleeves = plan_target, plan_cash_flow, plan_sleeves
        else:
            break
    return target, cash_flow, sleeves


def _apply_cash_flow(
    cash: float,
    cash_flow: float,
    unit_nav: float,
    unit_shares: float,
    net_contribution: float,
    date: pd.Timestamp,
) -> tuple[float, float, float]:
    if abs(cash_flow) <= 1e-12:
        return cash, unit_shares, net_contribution
    if cash_flow < 0 and cash + cash_flow < -1e-8:
        raise ValueError(f"{date.strftime('%Y-%m-%d')} 现金不足，无法完成资金转出")
    unit_nav = unit_nav if unit_nav > 0 else 1.0
    return cash + cash_flow, unit_shares + cash_flow / unit_nav, net_contribution + cash_flow


def _rebalance_cash_positions(
    positions: dict[str, dict[str, float]],
    cash_ref: dict[str, float],
    target_weights: pd.Series,
    nav_row: pd.Series,
    fee_config: dict[str, float],
    selected_ids: list[str],
    warnings: list[str],
    transactions: list[dict[str, Any]],
    date: pd.Timestamp,
) -> float:
    target = _target_for_available(target_weights, nav_row)
    if target.empty:
        return 0.0
    total_fees = 0.0
    total_value = _account_value(cash_ref["cash"], positions, nav_row)
    target_values = {fund_id: total_value * float(weight) for fund_id, weight in target.items()}
    held_ids = [fund_id for fund_id, pos in positions.items() if pos.get("quantity", 0.0) > 1e-9]

    for fund_id in sorted(set(held_ids) | set(target_values)):
        nav = _nav_value(nav_row, fund_id)
        if nav is None:
            continue
        current_quantity = float(positions.get(fund_id, {}).get("quantity", 0.0))
        current_value = current_quantity * nav
        target_value = float(target_values.get(fund_id, 0.0))
        if current_value <= target_value + 1e-8:
            continue
        sell_quantity = min(current_quantity, _floor_share((current_value - target_value) / nav))
        if sell_quantity <= 0:
            continue
        amount = sell_quantity * nav
        fee = amount * (fee_config["redemption"] + fee_config["transaction"])
        cash_ref["cash"] += amount - fee
        total_fees += fee
        _reduce_position(positions, fund_id, sell_quantity)
        transactions.append(
            _transaction_row(
                date,
                "sell",
                "卖出",
                fund_id=fund_id,
                price=nav,
                quantity=sell_quantity,
                amount=amount,
                fee=fee,
                cash_change=amount - fee,
                cash_balance=cash_ref["cash"],
            )
        )

    buy_orders: list[tuple[str, float, float]] = []
    for fund_id, target_value in target_values.items():
        nav = _nav_value(nav_row, fund_id)
        if nav is None:
            continue
        current_quantity = float(positions.get(fund_id, {}).get("quantity", 0.0))
        current_value = current_quantity * nav
        buy_budget = target_value - current_value
        if buy_budget <= 1e-8:
            continue
        buy_orders.append((fund_id, nav, buy_budget))

    total_buy_budget = sum(order[2] for order in buy_orders)
    available_cash_for_buys = max(0.0, cash_ref["cash"])
    purchase_rate = fee_config["purchase"] + fee_config["transaction"]
    buy_scale = 1.0
    if total_buy_budget > 0:
        required_cash = total_buy_budget * (1.0 + purchase_rate)
        if available_cash_for_buys <= 1e-8:
            buy_scale = 0.0
        elif required_cash > available_cash_for_buys:
            buy_scale = available_cash_for_buys / required_cash

    for fund_id, nav, buy_budget in buy_orders:
        if buy_scale <= 0:
            warnings.append(f"{date.strftime('%Y-%m-%d')} 现金不足，{fund_id} 未能买入目标金额。")
            continue
        spend_budget = min(buy_budget * buy_scale * (1.0 + purchase_rate), cash_ref["cash"])
        quantity = _floor_share(spend_budget / (nav * (1.0 + purchase_rate)))
        if quantity <= 0:
            continue
        amount = quantity * nav
        fee = amount * purchase_rate
        total_cost = amount + fee
        if total_cost > cash_ref["cash"] + 1e-8:
            continue
        cash_ref["cash"] -= total_cost
        total_fees += fee
        current = positions.get(fund_id, {"quantity": 0.0, "cost_amount": 0.0})
        positions[fund_id] = {
            "quantity": float(current.get("quantity", 0.0)) + quantity,
            "cost_amount": float(current.get("cost_amount", 0.0)) + total_cost,
        }
        transactions.append(
            _transaction_row(
                date,
                "buy",
                "买入",
                fund_id=fund_id,
                price=nav,
                quantity=quantity,
                amount=amount,
                fee=fee,
                cash_change=-total_cost,
                cash_balance=cash_ref["cash"],
            )
        )

    for fund_id in list(positions):
        if positions[fund_id].get("quantity", 0.0) <= 1e-9:
            positions.pop(fund_id, None)
    return total_fees


def _sell_positions_for_cash_flow(
    positions: dict[str, dict[str, float]],
    cash_ref: dict[str, float],
    required_cash: float,
    nav_row: pd.Series,
    fee_config: dict[str, float],
    selected_ids: list[str],
    warnings: list[str],
    transactions: list[dict[str, Any]],
    date: pd.Timestamp,
) -> float:
    if required_cash <= 1e-12 or cash_ref["cash"] >= required_cash - 1e-8:
        return 0.0
    fee_rate = fee_config["redemption"] + fee_config["transaction"]
    if fee_rate >= 1.0:
        raise ValueError("赎回费率过高，无法完成资金赎回")

    holdings: list[tuple[str, float, float, float]] = []
    for fund_id in selected_ids:
        nav = _nav_value(nav_row, fund_id)
        quantity = float(positions.get(fund_id, {}).get("quantity", 0.0))
        if nav is not None and quantity > 1e-9:
            holdings.append((fund_id, nav, quantity, quantity * nav))
    total_market_value = sum(item[3] for item in holdings)
    max_net_cash = total_market_value * (1.0 - fee_rate)
    if cash_ref["cash"] + max_net_cash < required_cash - 1e-8:
        raise ValueError(f"{date.strftime('%Y-%m-%d')} 现金和可赎回持仓不足，无法完成资金赎回")

    total_fees = 0.0
    shortfall = max(0.0, required_cash - cash_ref["cash"])
    gross_needed = shortfall / (1.0 - fee_rate)
    for fund_id, nav, quantity, market_value in holdings:
        target_amount = min(market_value, gross_needed * market_value / total_market_value)
        sell_quantity = min(quantity, _floor_share(target_amount / nav))
        total_fees += _sell_cash_position(positions, cash_ref, fund_id, nav, sell_quantity, fee_rate, transactions, date)

    for fund_id, nav, _, _ in holdings:
        if cash_ref["cash"] >= required_cash - 1e-8:
            break
        quantity = float(positions.get(fund_id, {}).get("quantity", 0.0))
        if quantity <= 1e-9:
            continue
        remaining_cash = required_cash - cash_ref["cash"]
        sell_quantity = min(quantity, _ceil_share(remaining_cash / ((1.0 - fee_rate) * nav)))
        total_fees += _sell_cash_position(positions, cash_ref, fund_id, nav, sell_quantity, fee_rate, transactions, date)

    if cash_ref["cash"] < required_cash - 1e-8:
        raise ValueError(f"{date.strftime('%Y-%m-%d')} 现金和可赎回持仓不足，无法完成资金赎回")
    warnings.append(f"{date.strftime('%Y-%m-%d')} 现金不足，已按持仓比例卖出基金补足赎回金额。")
    return total_fees


def _sell_cash_position(
    positions: dict[str, dict[str, float]],
    cash_ref: dict[str, float],
    fund_id: str,
    nav: float,
    sell_quantity: float,
    fee_rate: float,
    transactions: list[dict[str, Any]],
    date: pd.Timestamp,
) -> float:
    if sell_quantity <= 0:
        return 0.0
    amount = sell_quantity * nav
    fee = amount * fee_rate
    cash_ref["cash"] += amount - fee
    _reduce_position(positions, fund_id, sell_quantity)
    existing = next(
        (
            row
            for row in reversed(transactions)
            if row.get("date") == date.strftime("%Y-%m-%d")
            and row.get("type") == "sell"
            and row.get("fundId") == fund_id
            and row.get("price") == nav
        ),
        None,
    )
    if existing is not None:
        existing["quantity"] = _finite(float(existing.get("quantity") or 0.0) + sell_quantity)
        existing["amount"] = _finite(float(existing.get("amount") or 0.0) + amount)
        existing["fee"] = _finite(float(existing.get("fee") or 0.0) + fee)
        existing["cash_change"] = _finite(float(existing.get("cash_change") or 0.0) + amount - fee)
        existing["cash_balance"] = _finite(cash_ref["cash"])
        return fee
    transactions.append(
        _transaction_row(
            date,
            "sell",
            "卖出",
            fund_id=fund_id,
            price=nav,
            quantity=sell_quantity,
            amount=amount,
            fee=fee,
            cash_change=amount - fee,
            cash_balance=cash_ref["cash"],
        )
    )
    return fee


def _apply_transaction_weight_changes(
    transactions: list[dict[str, Any]],
    start_index: int,
    before_weights: pd.Series,
    after_weights: pd.Series,
) -> None:
    for row in transactions[start_index:]:
        if row.get("type") not in {"buy", "sell"}:
            continue
        fund_id = str(row.get("fundId") or "")
        before = float(before_weights.get(fund_id, 0.0) or 0.0)
        after = float(after_weights.get(fund_id, 0.0) or 0.0)
        row["weight_before"] = _finite(before)
        row["weight_after"] = _finite(after)
        row["weight_change"] = _weight_change_text(before, after)


def _cash_flow_transaction(date: pd.Timestamp, cash_flow: float, cash_balance: float) -> dict[str, Any]:
    return _transaction_row(
        date,
        "cash_flow",
        "资金转入" if cash_flow > 0 else "资金转出",
        amount=abs(cash_flow),
        cash_change=cash_flow,
        cash_balance=cash_balance,
    )


def _transaction_row(
    date: pd.Timestamp,
    type_: str,
    action: str,
    *,
    fund_id: str | None = None,
    price: float | None = None,
    quantity: float | None = None,
    amount: float | None = None,
    fee: float | None = None,
    cash_change: float | None = None,
    cash_balance: float | None = None,
    weight_before: float | None = None,
    weight_after: float | None = None,
) -> dict[str, Any]:
    return {
        "date": pd.Timestamp(date).strftime("%Y-%m-%d"),
        "type": type_,
        "action": action,
        "fundId": fund_id or "",
        "price": _finite(price),
        "quantity": _finite(quantity),
        "amount": _finite(amount),
        "fee": _finite(fee or 0.0),
        "cash_change": _finite(cash_change),
        "cash_balance": _finite(cash_balance),
        "weight_before": _finite(weight_before),
        "weight_after": _finite(weight_after),
        "weight_change": _weight_change_text(weight_before, weight_after),
    }


def _weight_change_text(weight_before: float | None, weight_after: float | None) -> str:
    if weight_before is None or weight_after is None:
        return ""
    return f"{weight_before * 100:.2f}%→{weight_after * 100:.2f}%"


def _floor_share(value: float) -> float:
    return math.floor(max(value, 0.0) * 100.0 + 1e-9) / 100.0


def _ceil_share(value: float) -> float:
    return math.ceil(max(value, 0.0) * 100.0 - 1e-9) / 100.0


def _nav_value(nav_row: pd.Series, fund_id: str) -> float | None:
    value = nav_row.get(fund_id)
    if value is None or pd.isna(value):
        return None
    return float(value)


def _account_value(cash: float, positions: dict[str, dict[str, float]], nav_row: pd.Series) -> float:
    total = float(cash)
    for fund_id, position in positions.items():
        nav = _nav_value(nav_row, fund_id)
        if nav is None:
            continue
        total += float(position.get("quantity", 0.0)) * nav
    return float(total)


def _position_weights(
    cash: float,
    positions: dict[str, dict[str, float]],
    nav_row: pd.Series,
    selected_ids: list[str],
) -> pd.Series:
    total = _account_value(cash, positions, nav_row)
    weights = {}
    for fund_id in selected_ids:
        nav = _nav_value(nav_row, fund_id)
        quantity = float(positions.get(fund_id, {}).get("quantity", 0.0))
        weights[fund_id] = (quantity * nav / total) if nav is not None and total > 0 else 0.0
    return pd.Series(weights, dtype=float)


def _reduce_position(positions: dict[str, dict[str, float]], fund_id: str, sell_quantity: float) -> None:
    position = positions.get(fund_id)
    if not position:
        return
    quantity = float(position.get("quantity", 0.0))
    if quantity <= 0:
        return
    cost_amount = float(position.get("cost_amount", 0.0))
    cost_reduction = cost_amount * min(sell_quantity / quantity, 1.0)
    position["quantity"] = max(0.0, quantity - sell_quantity)
    position["cost_amount"] = max(0.0, cost_amount - cost_reduction)


def _final_position_rows(
    positions: dict[str, dict[str, float]],
    nav_row: pd.Series,
    total_value: float,
    funds: dict[str, FundSeries],
    sleeve_by_fund: dict[str, str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for fund_id, position in positions.items():
        quantity = float(position.get("quantity", 0.0))
        nav = _nav_value(nav_row, fund_id)
        if quantity <= 1e-9 or nav is None:
            continue
        cost_amount = float(position.get("cost_amount", 0.0))
        market_value = quantity * nav
        profit = market_value - cost_amount
        rows.append(
            {
                "fundId": fund_id,
                "name": funds[fund_id].name,
                "manager": funds[fund_id].manager,
                "sleeveId": sleeve_by_fund.get(fund_id, ""),
                "quantity": _finite(quantity),
                "cost_amount": _finite(cost_amount),
                "cost_basis": _finite(cost_amount / quantity if quantity > 0 else None),
                "market_price": _finite(nav),
                "market_value": _finite(market_value),
                "profit": _finite(profit),
                "profit_pct": _finite(profit / cost_amount if cost_amount > 0 else None),
                "weight": _finite(market_value / total_value if total_value > 0 else 0.0),
            }
        )
    return sorted(rows, key=lambda row: row["market_value"] or 0.0, reverse=True)


def _planned_rebalanced_nav(
    fund_nav: pd.DataFrame,
    rebalance_plan: list[dict[str, Any]],
    selected_ids: list[str],
    warnings: list[str] | None = None,
    frequency: str = "daily",
    frequency_label: str = "日频",
) -> tuple[pd.Series, pd.DataFrame, list[pd.Timestamp], pd.DataFrame, pd.DataFrame, pd.Series]:
    returns = fund_nav.pct_change(fill_method=None)
    trade_dates = pd.DatetimeIndex(fund_nav.index).sort_values()
    plan_targets = _normalize_rebalance_plan(rebalance_plan, trade_dates, selected_ids, warnings, frequency, frequency_label)
    plan_by_date = {date: target for date, target in plan_targets}
    scheduled_rebalance_dates = _rebalance_dates(trade_dates)
    rebalance_dates = sorted(set([date for date, _ in plan_targets] + scheduled_rebalance_dates))

    current_target = _target_before_or_on(plan_targets, trade_dates[0])
    current_weights = _target_for_available(current_target, fund_nav.loc[trade_dates[0]])
    if current_weights.empty:
        raise ValueError("回测起始日没有任何可用基金净值")
    portfolio_value = 1.0
    nav_values: dict[pd.Timestamp, float] = {pd.Timestamp(trade_dates[0]): portfolio_value}
    contribution_rows: list[dict[str, Any]] = []
    begin_weight_rows: list[dict[str, Any]] = [
        _weight_row(pd.Timestamp(trade_dates[0]), current_weights, pd.Index(selected_ids))
    ]
    end_weight_rows: list[dict[str, Any]] = [
        _weight_row(pd.Timestamp(trade_dates[0]), current_weights, pd.Index(selected_ids))
    ]
    quarterly_set = set(pd.Timestamp(date) for date in scheduled_rebalance_dates)

    for date in trade_dates[1:]:
        date = pd.Timestamp(date)
        daily_returns = returns.loc[date].reindex(current_weights.index)
        valid_returns = daily_returns.dropna()
        if valid_returns.empty:
            begin_weight_rows.append(_weight_row(date, current_weights, pd.Index(selected_ids)))
            nav_values[date] = portfolio_value
            if date in plan_by_date:
                current_target = plan_by_date[date]
                next_target = _target_for_available(current_target, fund_nav.loc[date])
                if not next_target.empty:
                    current_weights = next_target
            elif date in quarterly_set:
                next_target = _target_for_available(current_target, fund_nav.loc[date])
                if not next_target.empty:
                    current_weights = next_target
            end_weight_rows.append(_weight_row(date, current_weights, pd.Index(selected_ids)))
            continue
        active_weights = current_weights.reindex(valid_returns.index).dropna()
        active_total = float(active_weights.sum())
        if active_total <= 0:
            begin_weight_rows.append(_weight_row(date, current_weights, pd.Index(selected_ids)))
            nav_values[date] = portfolio_value
            end_weight_rows.append(_weight_row(date, current_weights, pd.Index(selected_ids)))
            continue
        active_weights = active_weights / active_total
        begin_weight_rows.append(_weight_row(date, active_weights, pd.Index(selected_ids)))
        day_value_before = portfolio_value
        daily_return = float((active_weights * valid_returns).sum())
        for fund_id, weight in active_weights.items():
            contribution_rows.append(
                {
                    "date": date,
                    "fund_id": fund_id,
                    "weight_begin": float(weight),
                    "fund_return": float(valid_returns[fund_id]),
                    "contribution": float(day_value_before * weight * valid_returns[fund_id]),
                }
            )
        portfolio_value *= 1.0 + daily_return
        nav_values[date] = portfolio_value

        drifted = active_weights * (1.0 + valid_returns)
        drift_total = float(drifted.sum())
        current_weights = drifted / drift_total if drift_total > 0 else active_weights
        if date in plan_by_date:
            current_target = plan_by_date[date]
            next_target = _target_for_available(current_target, fund_nav.loc[date])
            if not next_target.empty:
                current_weights = next_target
        elif date in quarterly_set:
            next_target = _target_for_available(current_target, fund_nav.loc[date])
            if not next_target.empty:
                current_weights = next_target
        end_weight_rows.append(_weight_row(date, current_weights, pd.Index(selected_ids)))

    final_target = _target_before_or_on(plan_targets, trade_dates[-1]).reindex(selected_ids).fillna(0.0)
    return (
        pd.Series(nav_values).sort_index(),
        pd.DataFrame(contribution_rows),
        sorted(dict.fromkeys(pd.Timestamp(date) for date in rebalance_dates if date >= trade_dates[0] and date <= trade_dates[-1])),
        pd.DataFrame(begin_weight_rows).set_index("date").sort_index(),
        pd.DataFrame(end_weight_rows).set_index("date").sort_index(),
        final_target,
    )


def _normalize_rebalance_plan(
    rebalance_plan: list[dict[str, Any]],
    trade_dates: pd.DatetimeIndex,
    selected_ids: list[str],
    warnings: list[str] | None = None,
    frequency: str = "daily",
    frequency_label: str = "日频",
) -> list[tuple[pd.Timestamp, pd.Series]]:
    scheduled: dict[pd.Timestamp, pd.Series] = {}
    selected_set = set(selected_ids)
    for node in rebalance_plan:
        if not isinstance(node, dict):
            raise ValueError("调仓计划格式错误")
        raw_date = pd.to_datetime(node.get("date"), errors="coerce")
        if pd.isna(raw_date):
            raise ValueError("调仓日期非法")
        holdings = node.get("holdings")
        if not isinstance(holdings, list) or not holdings:
            raise ValueError("调仓日必须至少有一只基金")
        weights: dict[str, float] = {}
        for holding in holdings:
            fund_id = str(holding.get("fundId") or holding.get("id") or holding.get("code") or "").strip()
            if not fund_id:
                raise ValueError("调仓明细缺少基金")
            if fund_id in weights:
                raise ValueError(f"{raw_date.strftime('%Y-%m-%d')} 存在重复基金: {fund_id}")
            if fund_id not in selected_set:
                selected_set.add(fund_id)
            weight = _holding_weight_fraction(holding.get("weight"))
            if weight < 0:
                raise ValueError("权重不能为负")
            weights[fund_id] = weight
        total = sum(weights.values())
        if abs(total - 1.0) > 0.0001:
            raise ValueError(f"{raw_date.strftime('%Y-%m-%d')} 权重合计必须等于 100%，当前为 {total * 100:.2f}%")
        future_dates = trade_dates[trade_dates >= pd.Timestamp(raw_date)]
        if len(future_dates) == 0:
            continue
        actual_date = pd.Timestamp(future_dates[0])
        _append_frequency_mapping_warning(warnings, frequency, frequency_label, raw_date, actual_date)
        scheduled[actual_date] = pd.Series(weights, dtype=float).reindex(selected_ids).fillna(0.0)
    if not scheduled:
        raise ValueError("调仓计划在回测区间内没有有效调仓日")
    return sorted(scheduled.items(), key=lambda item: item[0])


def _append_frequency_mapping_warning(
    warnings: list[str] | None,
    frequency: str,
    frequency_label: str,
    raw_date: pd.Timestamp,
    actual_date: pd.Timestamp,
) -> None:
    return


def _target_before_or_on(plan_targets: list[tuple[pd.Timestamp, pd.Series]], date: pd.Timestamp) -> pd.Series:
    target = plan_targets[0][1]
    for plan_date, plan_target in plan_targets:
        if plan_date <= date:
            target = plan_target
        else:
            break
    return target


def _holding_weight_fraction(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return number / 100.0 if number > 1 else number


def _quarterly_rebalanced_nav(
    fund_nav: pd.DataFrame,
    target_weights: pd.Series,
) -> tuple[pd.Series, pd.DataFrame, list[pd.Timestamp], pd.DataFrame, pd.DataFrame]:
    returns = fund_nav.pct_change(fill_method=None)
    trade_dates = pd.DatetimeIndex(fund_nav.index).sort_values()
    scheduled_rebalance_dates = _rebalance_dates(trade_dates)
    rebalance_dates = [trade_dates[0]] + [date for date in scheduled_rebalance_dates if date > trade_dates[0]]

    current_weights = _target_for_available(target_weights, fund_nav.loc[trade_dates[0]])
    if current_weights.empty:
        raise ValueError("回测起始日没有任何可用基金净值")
    portfolio_value = 1.0
    nav_values: dict[pd.Timestamp, float] = {pd.Timestamp(trade_dates[0]): portfolio_value}
    contribution_rows: list[dict[str, Any]] = []
    begin_weight_rows: list[dict[str, Any]] = [
        _weight_row(pd.Timestamp(trade_dates[0]), current_weights, target_weights.index)
    ]
    end_weight_rows: list[dict[str, Any]] = [
        _weight_row(pd.Timestamp(trade_dates[0]), current_weights, target_weights.index)
    ]
    scheduled_set = set(pd.Timestamp(date) for date in scheduled_rebalance_dates)

    for date in trade_dates[1:]:
        daily_returns = returns.loc[date].reindex(current_weights.index)
        valid_returns = daily_returns.dropna()
        if valid_returns.empty:
            begin_weight_rows.append(_weight_row(pd.Timestamp(date), current_weights, target_weights.index))
            nav_values[pd.Timestamp(date)] = portfolio_value
            if pd.Timestamp(date) in scheduled_set:
                next_target = _target_for_available(target_weights, fund_nav.loc[date])
                if not next_target.empty:
                    current_weights = next_target
            end_weight_rows.append(_weight_row(pd.Timestamp(date), current_weights, target_weights.index))
            continue
        active_weights = current_weights.reindex(valid_returns.index).dropna()
        active_total = float(active_weights.sum())
        if active_total <= 0:
            begin_weight_rows.append(_weight_row(pd.Timestamp(date), current_weights, target_weights.index))
            nav_values[pd.Timestamp(date)] = portfolio_value
            end_weight_rows.append(_weight_row(pd.Timestamp(date), current_weights, target_weights.index))
            continue
        active_weights = active_weights / active_total
        begin_weight_rows.append(_weight_row(pd.Timestamp(date), active_weights, target_weights.index))
        day_value_before = portfolio_value
        daily_return = float((active_weights * valid_returns).sum())
        for fund_id, weight in active_weights.items():
            contribution_rows.append(
                {
                    "date": pd.Timestamp(date),
                    "fund_id": fund_id,
                    "weight_begin": float(weight),
                    "fund_return": float(valid_returns[fund_id]),
                    "contribution": float(day_value_before * weight * valid_returns[fund_id]),
                }
            )
        portfolio_value *= 1.0 + daily_return
        nav_values[pd.Timestamp(date)] = portfolio_value

        drifted = active_weights * (1.0 + valid_returns)
        drift_total = float(drifted.sum())
        current_weights = drifted / drift_total if drift_total > 0 else active_weights
        if pd.Timestamp(date) in scheduled_set:
            next_target = _target_for_available(target_weights, fund_nav.loc[date])
            if not next_target.empty:
                current_weights = next_target
        end_weight_rows.append(_weight_row(pd.Timestamp(date), current_weights, target_weights.index))

    return (
        pd.Series(nav_values).sort_index(),
        pd.DataFrame(contribution_rows),
        sorted(dict.fromkeys(pd.Timestamp(date) for date in rebalance_dates)),
        pd.DataFrame(begin_weight_rows).set_index("date").sort_index(),
        pd.DataFrame(end_weight_rows).set_index("date").sort_index(),
    )


def _weight_row(date: pd.Timestamp, weights: pd.Series, columns: pd.Index) -> dict[str, Any]:
    row: dict[str, Any] = {"date": pd.Timestamp(date)}
    full = weights.reindex(columns).fillna(0.0)
    for fund_id, value in full.items():
        row[str(fund_id)] = float(value)
    return row


def _target_for_available(target_weights: pd.Series, nav_row: pd.Series) -> pd.Series:
    available = nav_row.reindex(target_weights.index).dropna().index
    if len(available) == 0:
        return pd.Series(dtype=float)
    current = target_weights.reindex(available).fillna(0.0)
    total = float(current.sum())
    if total <= 0:
        return pd.Series(1.0 / len(available), index=available)
    return current / total


def _rebalance_dates(index: pd.DatetimeIndex) -> list[pd.Timestamp]:
    trade_dates = pd.DatetimeIndex(index).dropna().sort_values().unique()
    if len(trade_dates) == 0:
        return []
    quarter_ends = pd.date_range(start=trade_dates[0], end=trade_dates[-1], freq="QE-JAN")
    dates = []
    for quarter_end in quarter_ends:
        future = trade_dates[trade_dates >= quarter_end]
        if len(future) > 0:
            dates.append(pd.Timestamp(future[0]))
    return sorted(set(dates))


def _drawdown(nav: pd.Series) -> pd.Series:
    running_max = nav.cummax()
    return nav / running_max - 1.0


def _annual_returns(portfolio_nav: pd.Series, benchmark_nav: pd.Series) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for year, group in portfolio_nav.groupby(portfolio_nav.index.year):
        benchmark_group = benchmark_nav[benchmark_nav.index.year == year]
        if group.empty or benchmark_group.empty:
            continue
        portfolio_base = _calendar_year_base(portfolio_nav, int(year))
        benchmark_base = _calendar_year_base(benchmark_nav, int(year))
        if portfolio_base is None or benchmark_base is None or portfolio_base <= 0 or benchmark_base <= 0:
            continue
        portfolio_return = float(group.iloc[-1] / portfolio_base - 1.0)
        benchmark_return = float(benchmark_group.iloc[-1] / benchmark_base - 1.0)
        rows.append(
            {
                "year": int(year),
                "portfolio": _finite(portfolio_return),
                "benchmark": _finite(benchmark_return),
                "excess": _finite(portfolio_return - benchmark_return),
            }
        )
    return rows


def _annual_contributions(portfolio_nav: pd.Series, contribution_records: pd.DataFrame) -> list[dict[str, Any]]:
    if contribution_records.empty:
        return []
    rows: list[dict[str, Any]] = []
    records = contribution_records.copy()
    records["year"] = records["date"].dt.year
    for year, group in records.groupby("year"):
        nav_group = portfolio_nav[portfolio_nav.index.year == year]
        if group.empty or nav_group.empty:
            continue
        year_base = _calendar_year_base(portfolio_nav, int(year))
        if year_base is None:
            continue
        year_base = float(year_base)
        if year_base <= 0:
            continue
        funds = {
            fund_id: _finite(float(fund_group["contribution"].sum()) / year_base)
            for fund_id, fund_group in group.groupby("fund_id")
        }
        rows.append({"year": int(year), "funds": funds, "total": _finite(sum(v or 0.0 for v in funds.values()))})
    return rows


def _period_returns(portfolio_nav: pd.Series, benchmark_nav: pd.Series) -> list[dict[str, Any]]:
    portfolio_clean = portfolio_nav.dropna().sort_index()
    benchmark_clean = benchmark_nav.dropna().sort_index()
    if portfolio_clean.empty or benchmark_clean.empty:
        return []
    rows: list[dict[str, Any]] = []
    end_date = portfolio_clean.index[-1]
    for period_id, label, offset in PERIOD_RETURN_WINDOWS:
        start_date = _period_start_date(portfolio_clean, end_date, offset)
        portfolio_base = float(portfolio_clean.loc[start_date])
        benchmark_start = _period_start_date(benchmark_clean, end_date, offset)
        benchmark_base = float(benchmark_clean.loc[benchmark_start])
        if portfolio_base <= 0 or benchmark_base <= 0:
            continue
        portfolio_return = float(portfolio_clean.iloc[-1] / portfolio_base - 1.0)
        benchmark_return = float(benchmark_clean.iloc[-1] / benchmark_base - 1.0)
        rows.append(
            {
                "period": period_id,
                "label": label,
                "start": start_date.strftime("%Y-%m-%d"),
                "end": end_date.strftime("%Y-%m-%d"),
                "portfolio": _finite(portfolio_return),
                "benchmark": _finite(benchmark_return),
                "excess": _finite(portfolio_return - benchmark_return),
            }
        )
    return rows


def _period_contributions(portfolio_nav: pd.Series, contribution_records: pd.DataFrame) -> list[dict[str, Any]]:
    clean = portfolio_nav.dropna().sort_index()
    if clean.empty:
        return []
    records = contribution_records.copy() if not contribution_records.empty else pd.DataFrame()
    rows: list[dict[str, Any]] = []
    end_date = clean.index[-1]
    for period_id, label, offset in PERIOD_RETURN_WINDOWS:
        start_date = _period_start_date(clean, end_date, offset)
        base = float(clean.loc[start_date])
        if base <= 0:
            continue
        if records.empty:
            funds: dict[str, float | None] = {}
        else:
            group = records[(records["date"] > start_date) & (records["date"] <= end_date)]
            funds = {
                fund_id: _finite(float(fund_group["contribution"].sum()) / base)
                for fund_id, fund_group in group.groupby("fund_id")
            }
        rows.append(
            {
                "period": period_id,
                "label": label,
                "start": start_date.strftime("%Y-%m-%d"),
                "end": end_date.strftime("%Y-%m-%d"),
                "funds": funds,
                "total": _finite(sum(value or 0.0 for value in funds.values())),
            }
        )
    return rows


def _period_start_date(nav: pd.Series, end_date: pd.Timestamp, offset: pd.DateOffset | None) -> pd.Timestamp:
    clean = nav.dropna().sort_index()
    if clean.empty or offset is None:
        return pd.Timestamp(clean.index[0])
    target = pd.Timestamp(end_date) - offset
    candidates = clean[clean.index >= target]
    if candidates.empty:
        return pd.Timestamp(clean.index[0])
    return pd.Timestamp(candidates.index[0])


def _fund_contributions(
    fund_nav: pd.DataFrame,
    weights: dict[str, float],
    contribution_records: pd.DataFrame,
) -> list[dict[str, Any]]:
    total_returns = fund_nav.iloc[-1] / fund_nav.iloc[0] - 1.0
    contribution_by_fund = (
        contribution_records.groupby("fund_id")["contribution"].sum().to_dict()
        if not contribution_records.empty
        else {}
    )
    rows = []
    for fund_id in fund_nav.columns:
        rows.append(
            {
                "id": fund_id,
                "weight": _finite(weights[fund_id]),
                "fund_return": _finite(float(total_returns[fund_id])),
                "contribution": _finite(float(contribution_by_fund.get(fund_id, 0.0))),
            }
        )
    return sorted(rows, key=lambda row: row["contribution"] or 0.0, reverse=True)


def _calendar_year_base(nav: pd.Series, year: int) -> float | None:
    clean = nav.dropna().sort_index()
    if clean.empty:
        return None
    year_start = pd.Timestamp(year=year, month=1, day=1)
    previous = clean[clean.index < year_start]
    if not previous.empty:
        return float(previous.iloc[-1])
    current = clean[clean.index.year == year]
    if current.empty:
        return None
    return float(current.iloc[0])


def _annual_nav_returns(nav: pd.Series) -> list[dict[str, Any]]:
    clean = nav.dropna().sort_index()
    rows: list[dict[str, Any]] = []
    for year, group in clean.groupby(clean.index.year):
        base = _calendar_year_base(clean, int(year))
        if base is None or base <= 0 or group.empty:
            continue
        rows.append({"year": int(year), "return": _finite(float(group.iloc[-1] / base - 1.0))})
    return rows


def _build_attribution(
    request: BacktestRequest,
    funds: dict[str, FundSeries],
    fund_nav: pd.DataFrame,
    benchmark_nav: pd.Series,
    portfolio_nav: pd.Series,
    portfolio_returns: pd.Series,
    contribution_records: pd.DataFrame,
    begin_weights: pd.DataFrame,
    end_weights: pd.DataFrame,
    annual_contributions: list[dict[str, Any]],
    periods_per_year: int = TRADING_DAYS,
) -> dict[str, Any]:
    selected_ids = list(fund_nav.columns)
    sleeves = _normalize_sleeves(request.sleeves, selected_ids)
    contribution_returns = _contribution_return_frame(contribution_records, portfolio_nav.index, selected_ids)
    fund_contribution_values = (
        contribution_records.groupby("fund_id")["contribution"].sum().to_dict()
        if not contribution_records.empty
        else {}
    )
    total_contribution = float(sum(fund_contribution_values.values()))
    portfolio_detail = _drawdown_detail(portfolio_nav)
    peak = portfolio_detail["peak_date"]
    trough = portfolio_detail["trough_date"]
    peak_nav = float(portfolio_nav.loc[peak]) if peak in portfolio_nav.index else 1.0

    sleeve_navs, sleeve_returns, sleeve_contributions, sleeve_weights = _sleeve_series(
        sleeves,
        contribution_returns,
        begin_weights,
    )
    parent_component_returns, parent_value_contributions = _parent_contribution_frames(
        sleeves,
        contribution_returns,
        sleeve_navs,
        sleeve_weights,
    )
    component_returns = {
        "portfolio": portfolio_returns.reindex(portfolio_nav.index).fillna(0.0),
        **{
            sleeve["id"]: sleeve_contributions[sleeve["id"]].reindex(portfolio_nav.index).fillna(0.0)
            for sleeve in sleeves
        },
        **{
            fund_id: contribution_returns[fund_id].reindex(portfolio_nav.index).fillna(0.0)
            for fund_id in selected_ids
        },
    }

    rows: list[dict[str, Any]] = []
    rows.append(
        _attribution_row(
            row_id="portfolio",
            row_type="portfolio",
            level=0,
            parent_id=None,
            name="组合整体",
            code="",
            nav=portfolio_nav,
            avg_weight=1.0,
            current_weight=1.0,
            return_contribution=total_contribution,
            return_contribution_to_parent=None,
            total_contribution=total_contribution,
            component_return=component_returns["portfolio"],
            portfolio_returns=portfolio_returns,
            drawdown_drag=_drawdown_drag(contribution_records, selected_ids, peak, trough, peak_nav),
            drawdown_contribution_to_parent=None,
            periods_per_year=periods_per_year,
        )
    )

    for sleeve in sleeves:
        sleeve_id = sleeve["id"]
        fund_ids = sleeve["funds"]
        sleeve_contribution = float(sum(fund_contribution_values.get(fund_id, 0.0) for fund_id in fund_ids))
        sleeve_weight_series = sleeve_weights[sleeve_id]
        sleeve_current_weight = float(end_weights[fund_ids].sum(axis=1).iloc[-1])
        rows.append(
            _attribution_row(
                row_id=sleeve_id,
                row_type="sleeve",
                level=1,
                parent_id="portfolio",
                name=sleeve["name"],
                code="",
                nav=sleeve_navs[sleeve_id],
                avg_weight=float(sleeve_weight_series.mean()),
                current_weight=sleeve_current_weight,
                return_contribution=sleeve_contribution,
                return_contribution_to_parent=None,
                total_contribution=total_contribution,
                component_return=component_returns[sleeve_id],
                portfolio_returns=portfolio_returns,
                drawdown_drag=_drawdown_drag(contribution_records, fund_ids, peak, trough, peak_nav),
                drawdown_contribution_to_parent=None,
                periods_per_year=periods_per_year,
            )
        )
        sleeve_detail = _drawdown_detail(sleeve_navs[sleeve_id])
        sleeve_peak = sleeve_detail["peak_date"]
        sleeve_trough = sleeve_detail["trough_date"]
        sleeve_peak_nav = (
            float(sleeve_navs[sleeve_id].loc[sleeve_peak])
            if sleeve_peak is not None and sleeve_peak in sleeve_navs[sleeve_id].index
            else 1.0
        )
        sleeve_period_return = (
            float(sleeve_navs[sleeve_id].iloc[-1] / sleeve_navs[sleeve_id].iloc[0] - 1.0)
            if len(sleeve_navs[sleeve_id].dropna()) >= 2
            else None
        )
        for fund_id in fund_ids:
            fund_series = fund_nav[fund_id].dropna()
            contribution = float(fund_contribution_values.get(fund_id, 0.0))
            drawdown_drag = _drawdown_drag(contribution_records, [fund_id], peak, trough, peak_nav)
            parent_contribution = float(parent_value_contributions[fund_id].sum())
            parent_drawdown_contribution = _drawdown_contribution_from_series(
                parent_value_contributions[fund_id],
                sleeve_peak,
                sleeve_trough,
                sleeve_peak_nav,
            )
            rows.append(
                _attribution_row(
                    row_id=fund_id,
                    row_type="fund",
                    level=2,
                    parent_id=sleeve_id,
                    name=funds[fund_id].name,
                    code=fund_id,
                    nav=fund_series / fund_series.iloc[0] if not fund_series.empty else fund_series,
                    avg_weight=float(begin_weights[fund_id].mean()),
                    current_weight=float(end_weights[fund_id].iloc[-1]),
                    return_contribution=contribution,
                    return_contribution_to_parent=parent_contribution,
                    return_contribution_share_to_parent=(
                        parent_contribution / sleeve_period_return
                        if sleeve_period_return is not None and sleeve_period_return > 1e-12
                        else None
                    ),
                    total_contribution=total_contribution,
                    component_return=component_returns[fund_id],
                    portfolio_returns=portfolio_returns,
                    drawdown_drag=drawdown_drag,
                    volatility_contribution_to_parent=_volatility_contribution(
                        parent_component_returns[fund_id],
                        sleeve_returns[sleeve_id],
                        periods_per_year,
                    ),
                    drawdown_contribution_to_parent=parent_drawdown_contribution,
                    drawdown_drag_to_parent=parent_drawdown_contribution,
                    periods_per_year=periods_per_year,
                )
            )

    correlation = _correlation_payload(sleeves, sleeve_returns, fund_nav, benchmark_nav, funds)
    conclusions = _attribution_conclusions(rows, correlation)
    return {
        "window": {
            "start": portfolio_nav.index.min().strftime("%Y-%m-%d"),
            "end": portfolio_nav.index.max().strftime("%Y-%m-%d"),
            "drawdown_peak": peak.strftime("%Y-%m-%d") if peak is not None else None,
            "drawdown_trough": trough.strftime("%Y-%m-%d") if trough is not None else None,
            "drawdown_recovery": (
                portfolio_detail["recovery_date"].strftime("%Y-%m-%d")
                if portfolio_detail["recovery_date"] is not None
                else None
            ),
        },
        "conclusions": conclusions,
        "rows": rows,
        "annual_sleeve_returns": {
            sleeve["id"]: _annual_nav_returns(sleeve_navs[sleeve["id"]])
            for sleeve in sleeves
        },
        "correlation": correlation,
        "fund_details": _fund_details(rows, annual_contributions),
    }


def _normalize_sleeves(raw_sleeves: list[dict[str, Any]] | None, selected_ids: list[str]) -> list[dict[str, Any]]:
    selected = set(selected_ids)
    sleeves: list[dict[str, Any]] = []
    assigned: set[str] = set()
    if raw_sleeves:
        for index, sleeve in enumerate(raw_sleeves):
            fund_ids = [str(fund_id) for fund_id in sleeve.get("funds", []) if str(fund_id) in selected and str(fund_id) not in assigned]
            if not fund_ids:
                continue
            sleeve_id = str(sleeve.get("id") or f"sleeve_{index + 1}")
            sleeves.append(
                {
                    "id": sleeve_id,
                    "name": str(sleeve.get("name") or sleeve_id),
                    "funds": fund_ids,
                }
            )
            assigned.update(fund_ids)
    missing = [fund_id for fund_id in selected_ids if fund_id not in assigned]
    if missing or not sleeves:
        sleeves.append({"id": "ungrouped", "name": "未分组", "funds": missing or selected_ids})
    return sleeves


def _contribution_return_frame(
    contribution_records: pd.DataFrame,
    index: pd.DatetimeIndex,
    fund_ids: list[str],
) -> pd.DataFrame:
    frame = pd.DataFrame(0.0, index=index, columns=fund_ids)
    if contribution_records.empty:
        return frame
    records = contribution_records.copy()
    records["component_return"] = records["weight_begin"] * records["fund_return"]
    pivot = records.pivot_table(index="date", columns="fund_id", values="component_return", aggfunc="sum")
    frame.update(pivot.reindex(index=index, columns=fund_ids).fillna(0.0))
    return frame.astype(float)


def _sleeve_series(
    sleeves: list[dict[str, Any]],
    contribution_returns: pd.DataFrame,
    begin_weights: pd.DataFrame,
) -> tuple[dict[str, pd.Series], dict[str, pd.Series], dict[str, pd.Series], dict[str, pd.Series]]:
    sleeve_navs: dict[str, pd.Series] = {}
    sleeve_returns: dict[str, pd.Series] = {}
    sleeve_contributions: dict[str, pd.Series] = {}
    sleeve_weights: dict[str, pd.Series] = {}
    for sleeve in sleeves:
        sleeve_id = sleeve["id"]
        fund_ids = sleeve["funds"]
        weight_series = begin_weights[fund_ids].sum(axis=1).astype(float)
        component_return = contribution_returns[fund_ids].sum(axis=1).astype(float)
        internal_return = pd.Series(0.0, index=component_return.index)
        active = weight_series.abs() > 1e-12
        internal_return.loc[active] = component_return.loc[active] / weight_series.loc[active]
        nav = (1.0 + internal_return.fillna(0.0)).cumprod()
        if not nav.empty:
            nav = nav / nav.iloc[0]
        sleeve_weights[sleeve_id] = weight_series
        sleeve_contributions[sleeve_id] = component_return
        sleeve_returns[sleeve_id] = internal_return
        sleeve_navs[sleeve_id] = nav
    return sleeve_navs, sleeve_returns, sleeve_contributions, sleeve_weights


def _parent_contribution_frames(
    sleeves: list[dict[str, Any]],
    contribution_returns: pd.DataFrame,
    sleeve_navs: dict[str, pd.Series],
    sleeve_weights: dict[str, pd.Series],
) -> tuple[dict[str, pd.Series], dict[str, pd.Series]]:
    parent_component_returns: dict[str, pd.Series] = {}
    parent_value_contributions: dict[str, pd.Series] = {}
    for sleeve in sleeves:
        sleeve_id = sleeve["id"]
        weight_series = sleeve_weights[sleeve_id].reindex(contribution_returns.index).fillna(0.0).astype(float)
        nav = sleeve_navs[sleeve_id].reindex(contribution_returns.index).ffill().fillna(1.0)
        nav_before = nav.shift(1)
        if not nav_before.empty:
            nav_before.iloc[0] = nav.iloc[0]
        nav_before = nav_before.fillna(1.0)
        active = weight_series.abs() > 1e-12
        for fund_id in sleeve["funds"]:
            internal_component = pd.Series(0.0, index=contribution_returns.index)
            internal_component.loc[active] = contribution_returns[fund_id].loc[active] / weight_series.loc[active]
            parent_component_returns[fund_id] = internal_component.astype(float)
            parent_value_contributions[fund_id] = (nav_before * internal_component).astype(float)
    return parent_component_returns, parent_value_contributions


def _attribution_row(
    *,
    row_id: str,
    row_type: str,
    level: int,
    parent_id: str | None,
    name: str,
    code: str,
    nav: pd.Series,
    avg_weight: float,
    current_weight: float,
    return_contribution: float,
    return_contribution_to_parent: float | None,
    return_contribution_share_to_parent: float | None = None,
    total_contribution: float,
    component_return: pd.Series,
    portfolio_returns: pd.Series,
    drawdown_drag: float,
    volatility_contribution_to_parent: float | None = None,
    drawdown_contribution_to_parent: float | None = None,
    drawdown_drag_to_parent: float | None = None,
    periods_per_year: int = TRADING_DAYS,
) -> dict[str, Any]:
    detail = _drawdown_detail(nav)
    nav_returns = nav.pct_change(fill_method=None).fillna(0.0) if len(nav.dropna()) >= 2 else pd.Series(dtype=float)
    period_return = float(nav.iloc[-1] / nav.iloc[0] - 1.0) if len(nav.dropna()) >= 2 else None
    drawdown_contribution_to_parent = (
        drawdown_drag_to_parent
        if drawdown_contribution_to_parent is None and drawdown_drag_to_parent is not None
        else drawdown_contribution_to_parent
    )
    return {
        "id": row_id,
        "type": row_type,
        "level": level,
        "parent_id": parent_id,
        "name": name,
        "code": code,
        "average_weight": _finite(avg_weight),
        "current_weight": _finite(current_weight),
        "period_return": _finite(period_return),
        "return_contribution": _finite(return_contribution),
        "return_contribution_to_parent": _finite(return_contribution_to_parent),
        "return_contribution_share_to_parent": _finite(return_contribution_share_to_parent),
        "return_contribution_share": _finite(return_contribution / total_contribution if total_contribution > 1e-12 else None),
        "annual_volatility": _finite(_annualized_volatility(nav_returns, periods_per_year)),
        "volatility_contribution": _finite(_volatility_contribution(component_return, portfolio_returns, periods_per_year)),
        "volatility_contribution_to_parent": _finite(volatility_contribution_to_parent),
        "drawdown_contribution": _finite(drawdown_drag),
        "drawdown_contribution_to_parent": _finite(drawdown_contribution_to_parent),
        "drawdown_drag": _finite(drawdown_drag),
        "drawdown_drag_to_parent": _finite(drawdown_drag_to_parent),
        "max_drawdown": _finite(detail["max_drawdown"]),
        "drawdown_period": _format_date_range(detail["peak_date"], detail["trough_date"]),
        "drawdown_days": detail["drawdown_days"],
        "recovery_days": detail["recovery_days"],
    }


def _drawdown_detail(nav: pd.Series) -> dict[str, Any]:
    clean = nav.dropna().sort_index()
    if clean.empty:
        return {
            "max_drawdown": None,
            "peak_date": None,
            "trough_date": None,
            "recovery_date": None,
            "drawdown_days": None,
            "recovery_days": None,
        }
    cummax = clean.cummax()
    drawdown = clean / cummax - 1.0
    trough_date = pd.Timestamp(drawdown.idxmin())
    peak_value = cummax.loc[trough_date]
    peak_date = pd.Timestamp(clean.loc[:trough_date][clean.loc[:trough_date] == peak_value].index[-1])
    peak_nav = float(clean.loc[peak_date])
    recovery_index = clean.loc[trough_date:][clean.loc[trough_date:] >= peak_nav].index
    recovery_date = pd.Timestamp(recovery_index[0]) if len(recovery_index) else None
    return {
        "max_drawdown": float(drawdown.min()),
        "peak_date": peak_date,
        "trough_date": trough_date,
        "recovery_date": recovery_date,
        "drawdown_days": int((trough_date - peak_date).days),
        "recovery_days": int((recovery_date - trough_date).days) if recovery_date is not None else None,
    }


def _format_date_range(start: pd.Timestamp | None, end: pd.Timestamp | None) -> str:
    if start is None or end is None:
        return ""
    return f"{start.strftime('%Y/%m/%d')} - {end.strftime('%Y/%m/%d')}"


def _annualized_volatility(returns: pd.Series, periods_per_year: int = TRADING_DAYS) -> float:
    clean = returns.dropna()
    if clean.empty:
        return 0.0
    return float(clean.std(ddof=0) * math.sqrt(periods_per_year))


def _volatility_contribution(
    component_return: pd.Series,
    portfolio_returns: pd.Series,
    periods_per_year: int = TRADING_DAYS,
) -> float | None:
    aligned = pd.concat([component_return, portfolio_returns], axis=1).dropna()
    if len(aligned) < 2:
        return None
    variance = float(aligned.iloc[:, 1].var(ddof=0))
    if abs(variance) <= 1e-12:
        return 0.0
    parent_volatility = _annualized_volatility(aligned.iloc[:, 1], periods_per_year)
    covariance = float(((aligned.iloc[:, 0] - aligned.iloc[:, 0].mean()) * (aligned.iloc[:, 1] - aligned.iloc[:, 1].mean())).mean())
    return covariance / variance * parent_volatility


def _drawdown_drag(
    contribution_records: pd.DataFrame,
    fund_ids: list[str],
    peak: pd.Timestamp | None,
    trough: pd.Timestamp | None,
    peak_nav: float,
) -> float:
    if contribution_records.empty or peak is None or trough is None or peak_nav <= 0:
        return 0.0
    records = contribution_records[
        contribution_records["fund_id"].isin(fund_ids)
        & (contribution_records["date"] > peak)
        & (contribution_records["date"] <= trough)
    ]
    return float(records["contribution"].sum() / peak_nav)


def _drawdown_contribution_from_series(
    contribution_values: pd.Series,
    peak: pd.Timestamp | None,
    trough: pd.Timestamp | None,
    peak_nav: float,
) -> float:
    if contribution_values.empty or peak is None or trough is None or peak_nav <= 0:
        return 0.0
    values = contribution_values[(contribution_values.index > peak) & (contribution_values.index <= trough)]
    return float(values.sum() / peak_nav)


def _correlation_payload(
    sleeves: list[dict[str, Any]],
    sleeve_returns: dict[str, pd.Series],
    fund_nav: pd.DataFrame,
    benchmark_nav: pd.Series,
    funds: dict[str, FundSeries],
) -> dict[str, Any]:
    benchmark_returns = benchmark_nav.pct_change(fill_method=None)
    sleeve_labels = [sleeve["name"] for sleeve in sleeves]
    sleeve_excess = pd.DataFrame(
        {
            sleeve["id"]: sleeve_returns[sleeve["id"]].reindex(benchmark_returns.index) - benchmark_returns
            for sleeve in sleeves
        }
    )
    fund_returns = fund_nav.pct_change(fill_method=None)
    fund_excess = fund_returns.sub(benchmark_returns, axis=0)
    fund_labels = [funds[fund_id].name for fund_id in fund_nav.columns]
    fund_corr = _correlation_matrix(fund_excess)
    sleeve_corr = _correlation_matrix(sleeve_excess)
    sleeve_fund_matrices: list[dict[str, Any]] = []
    for sleeve in sleeves:
        sleeve_fund_ids = [fund_id for fund_id in sleeve.get("funds", []) if fund_id in fund_excess.columns]
        sleeve_fund_corr = _correlation_matrix(fund_excess[sleeve_fund_ids]) if sleeve_fund_ids else pd.DataFrame()
        sleeve_fund_matrices.append(
            {
                "id": sleeve["id"],
                "name": sleeve["name"],
                "labels": [funds[fund_id].name for fund_id in sleeve_fund_ids],
                "matrix": _matrix_values(sleeve_fund_corr),
                "average": _finite(_upper_average(sleeve_fund_corr)) if sleeve_fund_ids else None,
            }
        )
    highest, lowest, average = _correlation_pairs(fund_corr, fund_labels)
    return {
        "sleeves": {
            "labels": sleeve_labels,
            "matrix": _matrix_values(sleeve_corr),
            "average": _finite(_upper_average(sleeve_corr)),
        },
        "sleeve_fund_matrices": sleeve_fund_matrices,
        "fund_summary": {
            "average": _finite(average),
            "highest_pair": highest,
            "lowest_pair": lowest,
            "homogeneity_label": _homogeneity_label(average),
        },
        "fund_matrix": {
            "labels": fund_labels,
            "matrix": _matrix_values(fund_corr),
        },
    }


def _correlation_matrix(frame: pd.DataFrame) -> pd.DataFrame:
    matrix = frame.corr()
    for column in matrix.columns:
        matrix.loc[column, column] = 1.0
    return matrix


def _matrix_values(matrix: pd.DataFrame) -> list[list[float | None]]:
    return [
        [_finite(float(value)) for value in row]
        for row in matrix.to_numpy()
    ]


def _upper_average(matrix: pd.DataFrame) -> float | None:
    values: list[float] = []
    for i in range(len(matrix.columns)):
        for j in range(i + 1, len(matrix.columns)):
            value = float(matrix.iloc[i, j])
            if math.isfinite(value):
                values.append(value)
    return sum(values) / len(values) if values else None


def _correlation_pairs(matrix: pd.DataFrame, labels: list[str]) -> tuple[dict[str, Any], dict[str, Any], float | None]:
    pairs: list[tuple[float, str, str]] = []
    for i in range(len(matrix.columns)):
        for j in range(i + 1, len(matrix.columns)):
            value = float(matrix.iloc[i, j])
            if math.isfinite(value):
                pairs.append((value, labels[i], labels[j]))
    if not pairs:
        empty = {"left": "", "right": "", "value": None}
        return empty, empty, None
    highest = max(pairs, key=lambda item: item[0])
    lowest = min(pairs, key=lambda item: item[0])
    average = sum(item[0] for item in pairs) / len(pairs)
    return (
        {"left": highest[1], "right": highest[2], "value": _finite(highest[0])},
        {"left": lowest[1], "right": lowest[2], "value": _finite(lowest[0])},
        average,
    )


def _homogeneity_label(value: float | None) -> str:
    if value is None:
        return "不足"
    if value >= 0.6:
        return "偏高"
    if value >= 0.25:
        return "中等"
    return "较低"


def _attribution_conclusions(rows: list[dict[str, Any]], correlation: dict[str, Any]) -> dict[str, Any]:
    sleeve_rows = [row for row in rows if row["type"] == "sleeve"]
    return_sources = [row for row in sleeve_rows if (row["return_contribution"] or 0.0) > 0]
    volatility_sources = [row for row in sleeve_rows if (row["volatility_contribution"] or 0.0) > 0]
    drawdown_drags = [row for row in sleeve_rows if (row["drawdown_drag"] or 0.0) < 0]
    main_return = max(return_sources, key=lambda row: row["return_contribution"] or 0.0, default=None)
    main_vol = max(volatility_sources, key=lambda row: row["volatility_contribution"] or 0.0, default=None)
    main_drag = min(drawdown_drags, key=lambda row: row["drawdown_drag"] or 0.0, default=None)
    return {
        "main_return_source": _conclusion_from_row(main_return, "return_contribution_share"),
        "main_volatility_source": _conclusion_from_row(main_vol, "volatility_contribution"),
        "main_drawdown_drag": _conclusion_from_row(main_drag, "drawdown_drag"),
        "highest_correlation_pair": correlation["fund_summary"]["highest_pair"],
    }


def _conclusion_from_row(row: dict[str, Any] | None, value_key: str) -> dict[str, Any]:
    if row is None:
        return {"id": "", "name": "", "value": None}
    return {"id": row["id"], "name": row["name"], "value": row.get(value_key)}


def _fund_details(rows: list[dict[str, Any]], annual_contributions: list[dict[str, Any]]) -> dict[str, Any]:
    fund_rows = [row for row in rows if row["type"] == "fund"]
    details: dict[str, Any] = {}
    for row in fund_rows:
        annual_values = [
            {"year": annual["year"], "value": (annual.get("funds") or {}).get(row["id"], 0.0)}
            for annual in annual_contributions
        ]
        best = max(annual_values, key=lambda item: item["value"], default=None)
        worst = min(annual_values, key=lambda item: item["value"], default=None)
        details[row["id"]] = {
            "self": {
                "period_return": row["period_return"],
                "max_drawdown": row["max_drawdown"],
                "drawdown_period": row["drawdown_period"],
                "drawdown_days": row["drawdown_days"],
                "recovery_days": row["recovery_days"],
            },
            "impact": {
                "average_weight": row["average_weight"],
                "current_weight": row["current_weight"],
                "return_contribution": row["return_contribution"],
                "return_contribution_to_parent": row["return_contribution_to_parent"],
                "return_contribution_share_to_parent": row["return_contribution_share_to_parent"],
                "volatility_contribution": row["volatility_contribution"],
                "volatility_contribution_to_parent": row["volatility_contribution_to_parent"],
                "drawdown_contribution": row["drawdown_contribution"],
                "drawdown_contribution_to_parent": row["drawdown_contribution_to_parent"],
                "drawdown_drag": row["drawdown_drag"],
            },
            "annual": {
                "best_year": best,
                "worst_year": worst,
                "positive_years": sum(1 for item in annual_values if item["value"] > 0),
                "negative_years": sum(1 for item in annual_values if item["value"] < 0),
            },
        }
    return details


def _series_points(series: pd.Series) -> list[dict[str, Any]]:
    return [
        {"date": idx.strftime("%Y-%m-%d"), "value": _finite(float(value))}
        for idx, value in series.items()
    ]


def _finite(value: float | None) -> float | None:
    if value is None or math.isnan(value) or math.isinf(value):
        return None
    return float(value)
