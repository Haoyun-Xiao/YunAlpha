"""Read 30-session stock flow and financing changes from the published lake."""
from datetime import date
from pathlib import Path
from math import isfinite
import duckdb
from crowding.resources import connect as bounded_connect
from crowding.lake import DEFAULT_LAKE_ROOT
from crowding.market import _parts


def funding_30d(symbols, *, as_of: date, lake_root=DEFAULT_LAKE_ROOT):
    root = Path(lake_root).expanduser().resolve()
    names = list(dict.fromkeys(symbols))
    rows = {s: {'big_order_30d': None, 'flow_days': 0,
                'margin_balance_change_ratio_30d': None} for s in names}
    meta = {'market_date': as_of.isoformat(), 'flow_end': as_of.isoformat(),
            'flow_start': None, 'margin_end': None, 'margin_start': None,
            'flow_expected_days': 30, 'unit': 'CNY',
            'note': '大单与特大单使用数据湖供应商分档口径；非两融标的未确认，不将缺融资替换为零。'}
    calendar = [str(p) for p in (root/'curated/trading_calendar').rglob('*.parquet')]
    if not names or not calendar:
        meta['reason'] = '股票或交易日历缺失'
        return {'rows': rows, 'metadata': meta}
    db = bounded_connect()
    try:
        days = [r[0] for r in db.execute('SELECT DISTINCT trade_date FROM read_parquet(?, hive_partitioning=false) WHERE is_trading AND trade_date <= ? ORDER BY trade_date DESC LIMIT 90', [calendar, as_of]).fetchall()]
        if len(days) < 31 or days[0] != as_of:
            meta['reason'] = '交易日历不足或行情日不在日历内'
            return {'rows': rows, 'metadata': meta}
        db.execute('CREATE TEMP TABLE requested(symbol VARCHAR)')
        db.executemany('INSERT INTO requested VALUES (?)', [(s,) for s in names])
        flow_days = days[:30]
        meta['flow_start'] = flow_days[-1].isoformat()
        paths = _parts(root, 'fund_flow', flow_days[-1], as_of)
        if paths:
            result = db.execute('''WITH numbered AS (
              SELECT p.symbol,p.trade_date,p.large_net_inflow,p.super_large_net_inflow,
                ROW_NUMBER() OVER (PARTITION BY p.symbol,p.trade_date ORDER BY p.fetched_at DESC NULLS LAST,p.source DESC NULLS LAST) rn
              FROM read_parquet(?,hive_partitioning=false,union_by_name=true) p
              JOIN requested r ON p.symbol=r.symbol WHERE p.trade_date IN (SELECT unnest(?))
            ) SELECT symbol,COUNT(*) FILTER (WHERE large_net_inflow IS NOT NULL AND super_large_net_inflow IS NOT NULL AND isfinite(large_net_inflow) AND isfinite(super_large_net_inflow)) n,
              SUM(large_net_inflow+super_large_net_inflow) FILTER (WHERE large_net_inflow IS NOT NULL AND super_large_net_inflow IS NOT NULL AND isfinite(large_net_inflow) AND isfinite(super_large_net_inflow)) AS flow_value
              FROM numbered WHERE rn=1 GROUP BY symbol''', [paths, flow_days]).fetchall()
            for sym, n, value in result:
                rows[sym]['flow_days'] = n
                if n == 30: rows[sym]['big_order_30d'] = value
        margin_files = _parts(root, 'margin_trading', days[-1], as_of)
        if margin_files:
            data = db.execute('''WITH numbered AS (
                SELECT p.symbol,p.trade_date,p.margin_balance,
                  ROW_NUMBER() OVER(PARTITION BY p.symbol,p.trade_date ORDER BY p.fetched_at DESC NULLS LAST,p.source DESC NULLS LAST) rn
                FROM read_parquet(?,hive_partitioning=false,union_by_name=true) p
                JOIN requested r ON p.symbol=r.symbol WHERE p.trade_date IN (SELECT unnest(?))
            ) SELECT symbol,trade_date,margin_balance FROM numbered WHERE rn=1 AND margin_balance IS NOT NULL''', [margin_files, days]).fetchall()
            by_symbol = {}
            for sym, day, amount in data:
                if amount is not None and isfinite(amount):
                    by_symbol.setdefault(sym, {})[day] = amount
            for sym, values in by_symbol.items():
                end = max(values)
                idx = days.index(end)
                if idx+30 >= len(days): continue
                start = days[idx+30]
                old, current = values.get(start), values[end]
                rows[sym]['margin_end'], rows[sym]['margin_start'] = end.isoformat(), start.isoformat()
                if old is not None and old > 0:
                    rows[sym]['margin_balance_change_ratio_30d'] = current/old-1
            meta['margin_end'] = 'per-stock latest published date'
            meta['margin_start'] = '30 sessions before each stock margin end'
    finally:
        db.close()
    return {'rows': rows, 'metadata': meta}
