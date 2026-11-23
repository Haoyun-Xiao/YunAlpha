"""Published 20-session stock turnover, independently of adjusted prices."""
from datetime import date
from pathlib import Path
import duckdb
from crowding.resources import connect as bounded_connect
from crowding.lake import DEFAULT_LAKE_ROOT
from crowding.market import _parts


def turnover_20d(symbols, *, as_of: date, lake_root=DEFAULT_LAKE_ROOT):
    root = Path(lake_root).expanduser().resolve()
    symbols = list(dict.fromkeys(symbols))
    result = {s: {'avg_turnover': None, 'observed_days': 0} for s in symbols}
    calendars = [str(p) for p in (root / 'curated' / 'trading_calendar').rglob('*.parquet')]
    meta = {'market_date': as_of.isoformat(), 'expected_days': 20, 'source': 'daily_bars.amount', 'units': 'CNY', 'window_start': None}
    if not calendars or not symbols:
        meta['reason'] = '交易日历或股票列表缺失'
        return {'rows': result, 'metadata': meta}
    conn = bounded_connect()
    try:
        days = [r[0] for r in conn.execute('SELECT DISTINCT trade_date FROM read_parquet(?, hive_partitioning=false) WHERE is_trading AND trade_date <= ? ORDER BY trade_date DESC LIMIT 20', [calendars, as_of]).fetchall()]
        if len(days) != 20 or days[0] != as_of:
            meta['reason'] = '行情日或近20个交易日历不完整'
            return {'rows': result, 'metadata': meta}
        meta['window_start'] = days[-1].isoformat()
        paths = _parts(root, 'daily_bars', days[-1], as_of)
        if not paths:
            meta['reason'] = '近20日行情分区缺失'
            return {'rows': result, 'metadata': meta}
        conn.execute('CREATE TEMP TABLE requested(symbol VARCHAR)')
        conn.executemany('INSERT INTO requested VALUES (?)', [(s,) for s in symbols])
        rows = conn.execute('''WITH numbered AS (
            SELECT p.symbol, p.trade_date, p.amount,
              row_number() OVER (PARTITION BY p.symbol,p.trade_date ORDER BY p.fetched_at DESC NULLS LAST, p.source DESC NULLS LAST) AS rn
            FROM read_parquet(?, hive_partitioning=false, union_by_name=true) p
            JOIN requested r ON p.symbol=r.symbol WHERE p.trade_date IN (SELECT unnest(?))
        ) SELECT symbol, count(*) FILTER (WHERE amount IS NOT NULL AND isfinite(amount) AND amount >= 0) AS n,
            avg(amount) FILTER (WHERE amount IS NOT NULL AND isfinite(amount) AND amount >= 0) AS average
          FROM numbered WHERE rn=1 GROUP BY symbol''', [paths, days]).fetchall()
        for symbol, n, average in rows:
            result[symbol] = {'avg_turnover': average if n == 20 else None, 'observed_days': n}
    finally:
        conn.close()
    return {'rows': result, 'metadata': meta}
