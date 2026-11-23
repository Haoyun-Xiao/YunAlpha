"""Supplemental stock metrics computed from published lake tables."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from crowding.resources import connect

from crowding.lake import DEFAULT_LAKE_ROOT


def valuation_percentiles(symbols: list[str], *, as_of: date,
                          lake_root: str | Path = DEFAULT_LAKE_ROOT,
                          years: int = 3) -> dict[str, dict]:
    """Return current PE/PB and their percentile over the trailing window."""
    root = Path(lake_root).expanduser().resolve()
    paths = [str(p) for p in (root / 'curated' / 'valuation_metrics').rglob('*.parquet')]
    if not paths or not symbols:
        return {}
    start_year = as_of.year - years
    try:
        start = as_of.replace(year=start_year)
    except ValueError:
        start = as_of.replace(year=start_year, day=28)
    conn = connect()
    try:
        rows = conn.execute('''
          WITH requested AS (SELECT UNNEST(?::VARCHAR[]) AS symbol),
          v AS (
            SELECT p.symbol,p.trade_date,p.pe_ttm,p.pb,
              ROW_NUMBER() OVER(PARTITION BY p.symbol,p.trade_date ORDER BY p.fetched_at DESC NULLS LAST) rn
            FROM read_parquet(?,hive_partitioning=false,union_by_name=true) p
            JOIN requested r ON r.symbol=p.symbol
            WHERE p.trade_date BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
          ), latest AS (
            SELECT symbol,MAX_BY(pe_ttm,trade_date) current_pe,MAX_BY(pb,trade_date) current_pb
            FROM v WHERE rn=1 GROUP BY symbol
          )
          SELECT x.symbol,l.current_pe,l.current_pb,
            COUNT(x.pe_ttm) FILTER(WHERE x.pe_ttm>0) pe_n,
            COUNT(x.pb) FILTER(WHERE x.pb>0) pb_n,
            AVG(CASE WHEN x.pe_ttm IS NOT NULL AND x.pe_ttm>0 AND l.current_pe>0 AND x.pe_ttm<=l.current_pe THEN 1.0 ELSE 0.0 END)
              FILTER (WHERE x.pe_ttm IS NOT NULL AND x.pe_ttm>0) pe_pct,
            AVG(CASE WHEN x.pb IS NOT NULL AND x.pb>0 AND l.current_pb>0 AND x.pb<=l.current_pb THEN 1.0 ELSE 0.0 END)
              FILTER (WHERE x.pb IS NOT NULL AND x.pb>0) pb_pct
          FROM v x JOIN latest l ON l.symbol=x.symbol
          WHERE x.rn=1 GROUP BY x.symbol,l.current_pe,l.current_pb
        ''', [symbols, paths, start, as_of]).fetchall()
    finally:
        conn.close()
    result = {}
    for symbol, pe, pb, pe_n, pb_n, pe_pct, pb_pct in rows:
        result[symbol] = {
            'pe_ttm': float(pe) if pe is not None else None,
            'pb': float(pb) if pb is not None else None,
            'pe_3y_pct': float(pe_pct) * 100 if pe is not None and pe>0 and pe_pct is not None and pe_n >= 250 else None,
            'pb_3y_pct': float(pb_pct) * 100 if pb is not None and pb>0 and pb_pct is not None and pb_n >= 250 else None,
            'pe_samples': int(pe_n or 0), 'pb_samples': int(pb_n or 0),
        }
    return result


def previous_period_deltas(current_snapshot: dict, previous_snapshot: dict) -> dict[str, dict]:
    """Build change fields on the exact same price/free-float basis."""
    old = {row['symbol']: row for row in previous_snapshot.get('rows', [])}
    result = {}
    for row in current_snapshot.get('rows', []):
        symbol = row.get('symbol')
        prior = old.get(symbol)
        if not symbol or not prior:
            result[symbol] = {'fund_change': None, 'ratio_change': None,
                              'previous_fund_count': 0 if prior is None else prior.get('fund_count'),
                              'previous_holding_mv': None}
            continue
        current_count, previous_count = row.get('fund_count'), prior.get('fund_count')
        current_ratio, previous_ratio = row.get('hold_float_ratio'), prior.get('hold_float_ratio')
        result[symbol] = {
            'fund_change': current_count - previous_count
            if current_count is not None and previous_count is not None else None,
            'ratio_change': (current_ratio - previous_ratio) * 100
            if current_ratio is not None and previous_ratio is not None else None,
            'delta_hold_float_ratio': current_ratio - previous_ratio
            if current_ratio is not None and previous_ratio is not None else None,
            'change_quality': 'estimated_same_market_float_basis',
            'previous_fund_count': previous_count,
            'previous_holding_mv': prior.get('holding_mv'),
        }
    return result
