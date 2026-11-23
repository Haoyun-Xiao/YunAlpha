"""Read-only market inputs for the crowding board, with explicit coverage.

Dates are market-wide published daily-bar sessions, not per-stock observations.
Amounts are CNY and returns are decimal fractions. Missing observations never
become zeros; strict windows deliberately leave incomplete metrics unavailable.
"""
from __future__ import annotations

from datetime import date, timedelta
from bisect import bisect_right
from math import isfinite
from statistics import median
from pathlib import Path
from typing import Any, Iterable

import duckdb
from crowding.resources import connect as bounded_connect

from crowding.lake import DEFAULT_LAKE_ROOT


def _parts(root: Path, dataset: str, start: date, end: date, *, derived: bool = False) -> list[str]:
    base = root / ('derived' if derived else 'curated') / dataset
    if not base.exists():
        return []
    paths = []
    for part in base.glob('trade_date=*'):
        value = part.name.split('=', 1)[1]
        try:
            first = date.fromisoformat(value if len(value) == 10 else value + '-01')
            last = first if len(value) == 10 else (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        except ValueError:
            continue
        if first <= end and last >= start:
            paths.extend(str(p) for p in part.rglob('*.parquet'))
    return sorted(paths)


def market_data(symbols: Iterable[str], *, lake_root: str | Path = DEFAULT_LAKE_ROOT,
                as_of: date | None = None, period_start: date | None = None,
                series_start: date | None = None) -> dict[str, Any]:
    """Return stock-level inputs, dates and coverage without writing to the lake.

    The 52-week high is the maximum *adjusted intraday high* during 364 calendar
    days ending on the market date. The history close is qfq, normalized to
    the market-date factor; missing bars or factors remain missing. Return compares
    adjusted closes at the last
    published market session on or before period_start and at market_date.
    30-day financing change compares the current balance to 30 sessions earlier.

    Mixed-source factors require a Sina event cache confirmed by a canonical
    Sina row and consistent same-day backward-asof overlaps (0.2% tolerance).
    Unverified mixtures return no adjusted metric. This cannot detect a basis
    break within one source or prove a provider cache contains every event.
    """
    root = Path(lake_root).expanduser().resolve()
    names = list(dict.fromkeys(symbols))
    bound = as_of or date.today()
    bars_dir = root / 'curated' / 'daily_bars'
    sessions = []
    if bars_dir.exists():
        for p in bars_dir.glob('trade_date=*'):
            try:
                d = date.fromisoformat(p.name.split('=', 1)[1])
            except ValueError:
                continue
            if d <= bound and any(p.rglob('*.parquet')):
                sessions.append(d)
    sessions.sort()
    calendar_paths = list((root / 'curated' / 'trading_calendar').rglob('*.parquet'))
    calendar_source = 'daily_bars partition dates (calendar unavailable)'
    if sessions and calendar_paths:
        c = bounded_connect()
        try:
            calendar = {row[0] for row in c.execute(
                "SELECT trade_date FROM read_parquet(?, hive_partitioning=false) WHERE is_trading AND trade_date <= ?",
                [[str(p) for p in calendar_paths], bound]).fetchall()}
        finally:
            c.close()
        published_sessions = [d for d in sessions if d in calendar]
        if published_sessions:
            sessions = sorted(d for d in calendar if d <= published_sessions[-1])
            calendar_source = 'trading_calendar.is_trading'
        else:
            sessions = []
    rows = {s: {'symbol': s, 'avg_turnover_20d': None, 'high_52w_adjusted': None,
                'drawdown_52w': None, 'period_return': None, 'high_20d_return': None,
                'turnover_5d_amount': None, 'big_order_30d': None,
                'margin_balance': None, 'margin_balance_change_amount_30d': None,
                'margin_balance_change_ratio_30d': None, 'coverage': {}} for s in names}
    if not sessions or not names:
        return {'rows': list(rows.values()), 'metadata': {'market_date': None, 'read_only': True, 'calendar_source': calendar_source}}
    end = sessions[-1]
    start52 = end - timedelta(days=364)
    baseline = next((d for d in reversed(sessions) if period_start is not None and period_start <= end and d <= period_start), None)
    start = min(start52, baseline or start52, sessions[max(0, len(sessions)-31)])
    win20, win30 = sessions[-20:], sessions[-30:]
    win52 = [d for d in sessions if start52 <= d <= end]
    series_days = [d for d in sessions if series_start is not None and series_start <= d <= end]
    dates = set(win20 + win30 + win52 + series_days + ([d for d in sessions if baseline <= d <= end] if baseline else []))
    dates.add(sessions[-31] if len(sessions) >= 31 else sessions[0])
    conn = bounded_connect()
    conn.execute('CREATE TEMP TABLE requested(symbol VARCHAR)')
    conn.executemany('INSERT INTO requested VALUES (?)', [(s,) for s in names])

    def read(ds: str, fields: str, dates_filter: set[date], derived: bool = False, extra: str = '') -> list[tuple]:
        paths = _parts(root, ds, min(dates_filter), max(dates_filter), derived=derived)
        if not paths:
            return []
        # Resolve physical duplicates deterministically using the lake's latest-fetch rule.
        return conn.execute(f'''SELECT {fields} FROM (SELECT p.*, ROW_NUMBER() OVER
            (PARTITION BY p.symbol,p.trade_date ORDER BY p.fetched_at DESC NULLS LAST, p.source DESC NULLS LAST) rn
            FROM read_parquet(?, hive_partitioning=false, union_by_name=true) p
            JOIN requested r ON p.symbol=r.symbol WHERE p.trade_date IN (SELECT UNNEST(?)) {extra})
            WHERE rn=1''', [paths, sorted(dates_filter)]).fetchall()

    try:
        bars = {(s, d): (close, high, amount) for s, d, close, high, amount in read('daily_bars', 'symbol,trade_date,close,high,amount', dates)}
        factors = {(s, d): (f, source) for s, d, f, source in read('adj_factors', 'symbol,trade_date,factor,source', dates, True, "AND p.adjust_type='hfq'")}
        blocklist = root / 'meta' / 'quality' / 'adj-factor-blocklist.parquet'
        if blocklist.exists():
            blocked = conn.execute('SELECT symbol, trade_date FROM read_parquet(?, hive_partitioning=false)', [str(blocklist)]).fetchall()
            for key in blocked:
                factors.pop(key, None)
        # The Sina cache is a sparse event series, valid by backward-asof. Only
        # use it as a bridge if an actual canonical Sina row confirms its level.
        # Each other source must have same-day overlap whose ratios agree to 0.2%.
        normalized = {}
        factors_by_symbol = {symbol: [] for symbol in names}
        for (symbol, d), (value, source) in factors.items():
            factors_by_symbol[symbol].append((d, value, source))
        for symbol in names:
            cache = root / 'meta' / 'adj_factors_cache' / f"{symbol.replace('.', '_')}_hfq.parquet"
            if not cache.is_file():
                continue
            events = conn.execute("SELECT trade_date, factor FROM read_parquet(?, hive_partitioning=false) WHERE trade_date <= ? ORDER BY trade_date", [str(cache), end]).fetchall()
            events = [(d, f) for d, f in events if f is not None and isfinite(f) and f > 0]
            event_days = [d for d, _ in events]
            ratios = {}
            anchor = False
            for d, value, source in factors_by_symbol[symbol]:
                if not source or value is None or not isfinite(value) or value <= 0:
                    continue
                i = bisect_right(event_days, d) - 1
                if i < 0:
                    continue
                cache_value = events[i][1]
                if source == 'sina' and abs(value / cache_value - 1) <= 0.000001:
                    anchor = True
                ratios.setdefault(source, []).append(cache_value / value)
            if not anchor:
                continue
            scales = {}
            for source, values in ratios.items():
                scale = median(values)
                if all(abs(v / scale - 1) <= 0.002 for v in values):
                    scales[source] = scale
            for d, value, source in factors_by_symbol[symbol]:
                if source in scales and bisect_right(event_days, d) > 0 and value is not None and isfinite(value) and value > 0:
                    normalized[(symbol, d)] = value * scales[source]

        def comparable(symbol: str, days: list[date]) -> tuple[dict[date, float], str]:
            available = {d: factors[(symbol, d)] for d in days if (symbol, d) in factors}
            valid = {d: (v, src) for d, (v, src) in available.items()
                     if v is not None and isfinite(v) and v > 0 and src}
            sources = {src for _, src in valid.values()}
            if len(sources) == 1:
                return {d: v for d, (v, _) in valid.items()}, 'same_source'
            if sources and all((symbol, d) in normalized for d in valid):
                return {d: normalized[(symbol, d)] for d in valid}, 'sina_cache_overlap'
            return {}, 'unverified_source_basis' if sources else 'missing_factor'

        flows = {(s, d): (large + extra if large is not None and extra is not None else None)
                 for s, d, large, extra in read('fund_flow', 'symbol,trade_date,large_net_inflow,super_large_net_inflow', set(win30))}
        margin_dates = ({end, sessions[-31]} if len(sessions) >= 31 else {end}) | set(series_days)
        margins = {(s, d): v for s, d, v in read('margin_trading', 'symbol,trade_date,margin_balance', margin_dates)}
        for s, row in rows.items():
            c = row['coverage']
            series_factors, series_basis = comparable(s, series_days)
            end_factor = series_factors.get(end)
            row['price_margin_series'] = [
                {'date': d.isoformat(),
                 'close': (bars[(s, d)][0] * series_factors[d] / end_factor
                           if end_factor is not None and d in series_factors
                           and (s, d) in bars and bars[(s, d)][0] is not None
                           and bars[(s, d)][0] > 0 else None),
                 'margin_balance': margins.get((s, d))}
                for d in series_days
            ]
            c['price_series'] = {
                'adjustment': 'qfq', 'factor_basis': series_basis,
                'observed': sum(item['close'] is not None for item in row['price_margin_series']),
                'expected': len(series_days),
                'missing_bar_dates': [d.isoformat() for d in series_days if (s, d) not in bars or bars[(s, d)][0] is None],
                'missing_factor_dates': [d.isoformat() for d in series_days if (s, d) not in factors],
            }
            amounts = [bars.get((s, d), (None, None, None))[2] for d in win20]
            c['turnover_20d'] = {'observed': sum(v is not None for v in amounts), 'expected': 20,
                                 'start_date': win20[0].isoformat(), 'end_date': end.isoformat(),
                                 'missing_bar_dates': [d.isoformat() for d in win20 if (s, d) not in bars],
                                 'null_amount_dates': [d.isoformat() for d in win20 if (s, d) in bars and bars[(s, d)][2] is None]}
            if len(win20) == 20 and all(v is not None and v >= 0 for v in amounts):
                row['avg_turnover_20d'] = sum(amounts) / 20
            amount5 = amounts[-5:]
            if len(amount5) == 5 and all(v is not None and v >= 0 for v in amount5):
                row['turnover_5d_amount'] = sum(amount5)
            high_factors, high_basis = comparable(s, win52)
            adjusted_highs = []
            for d in win52:
                bar, factor = bars.get((s, d)), high_factors.get(d)
                if bar and bar[1] is not None and bar[1] > 0 and factor is not None and factor > 0:
                    adjusted_highs.append(bar[1] * factor)
            c['high_52w'] = {'observed': len(adjusted_highs), 'expected': len(win52), 'calendar_start': start52.isoformat(), 'history_complete': bool(sessions[0] <= start52), 'factor_basis': high_basis}
            current, high_factor = bars.get((s, end)), high_factors.get(end)
            high_close = current[0] * high_factor if current and current[0] is not None and current[0] > 0 and high_factor is not None else None
            if sessions[0] <= start52 and len(adjusted_highs) == len(win52) and adjusted_highs and high_close is not None:
                row['high_52w_adjusted'] = max(adjusted_highs)
                row['drawdown_52w'] = high_close / row['high_52w_adjusted'] - 1
            recent_factors, recent_basis = comparable(s, win20)
            recent_closes = [bars[(s, d)][0] * recent_factors[d] for d in win20
                             if (s, d) in bars and bars[(s, d)][0] is not None
                             and bars[(s, d)][0] > 0 and d in recent_factors]
            recent_end = (current[0] * recent_factors[end]
                          if current and current[0] is not None and current[0] > 0
                          and end in recent_factors else None)
            c['high_20d'] = {'observed': len(recent_closes), 'expected': 20,
                             'factor_basis': recent_basis, 'price': 'adjusted_close'}
            if recent_end is not None and len(win20) == len(recent_closes) == 20:
                row['high_20d_return'] = recent_end / max(recent_closes) - 1
            return_days = [d for d in sessions if baseline <= d <= end] if baseline else []
            return_factors, return_basis = comparable(s, return_days)
            old, old_factor = (bars.get((s, baseline)), return_factors.get(baseline)) if baseline else (None, None)
            end_factor = return_factors.get(end)
            if current and current[0] is not None and current[0] > 0 and end_factor is not None and old and old[0] is not None and old[0] > 0 and old_factor is not None:
                row['period_return'] = current[0] * end_factor / (old[0] * old_factor) - 1
            c['period_return'] = {'start_date': baseline.isoformat() if baseline else None, 'end_date': end.isoformat(), 'complete': row['period_return'] is not None, 'factor_basis': return_basis}
            flow = [flows.get((s, d)) for d in win30]
            c['big_order_30d'] = {'observed': sum(v is not None for v in flow), 'expected': 30}
            if len(win30) == 30 and all(v is not None for v in flow):
                row['big_order_30d'] = sum(flow)
            row['margin_balance'] = margins.get((s, end))
            old_margin = margins.get((s, sessions[-31])) if len(sessions) >= 31 else None
            if row['margin_balance'] is not None and old_margin is not None:
                row['margin_balance_change_amount_30d'] = row['margin_balance'] - old_margin
                if old_margin > 0:
                    row['margin_balance_change_ratio_30d'] = row['margin_balance_change_amount_30d'] / old_margin
            c['margin_30d'] = {'start_date': sessions[-31].isoformat() if len(sessions) >= 31 else None, 'end_date': end.isoformat(), 'complete': row['margin_balance_change_ratio_30d'] is not None}
    finally:
        conn.close()
    return {'rows': list(rows.values()), 'metadata': {'market_date': end.isoformat(), 'as_of': bound.isoformat(),
        'period_start_requested': period_start.isoformat() if period_start else None,
        'flow_source': 'fund_flow.large_net_inflow + super_large_net_inflow, EastMoney / Tushare moneyflow_dc; provider-specific definition',
        'units': {'amounts': 'CNY', 'returns_and_margin_balance_change_ratio_30d': 'decimal', 'high_52w_adjusted': 'CNY, hfq'},
        'adjustment': 'hfq factors require the same source or consistent same-day Sina-cache overlap; missing or quarantined factors remain missing',
        'read_only': True, 'calendar_source': calendar_source}}
