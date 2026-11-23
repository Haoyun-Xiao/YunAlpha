"""Read-only daily continuations of the published owners desk calculations."""
from datetime import date, timedelta
from pathlib import Path

from seven_desks.daily_inputs import literal
from seven_desks.builders import owners_daily_capital, owners_daily_institution

FROZEN = date(2026, 11, 9)
PREFIX = 'revision/owners_daily/'


class Build:
    def __init__(self, x, base_data):
        self.x, self.db, self.base = x, x.db, Path(base_data)
        self.cutoff = literal(FROZEN)
        self.end = literal(x.bound)
        self.target = x.days[-1]
        self.outputs = {}
        self.boards = {'capital_flow': {}, 'national_team': {}, 'fund_holdings': {}}

    def sql(self, statement):
        return self.db.execute(statement)

    def table(self, name, sql):
        self.sql(f'CREATE OR REPLACE TEMP TABLE {name} AS ({sql})')

    def prior(self, name, rel):
        path = self.base / (rel + '.parquet')
        self.sql(f'CREATE OR REPLACE TEMP VIEW {name} AS SELECT * FROM read_parquet({literal(path)},hive_partitioning=false)')

    def source(self, dataset, *, start=FROZEN, day='trade_date', layer='curated'):
        return self.x.view(dataset, start, day=day, layer=layer, alias='src_' + dataset)

    def emit(self, name, sql, keys=('trade_date',), day='trade_date'):
        rel = PREFIX + name + '.parquet'
        parent = self.base / rel
        if parent.is_file():
            old = f'read_parquet({literal(parent)},hive_partitioning=false)'
            latest = self.sql(f'SELECT max({day}) FROM {old}').fetchone()[0]
            if latest > self.x.bound:
                raise ValueError(f'不能回退已发布的 owners 数据日：{rel}')
            # A missing upstream date in the new run must not erase a date
            # already published in the selected immutable version.
            # An unclassified industry can still be the key of a real group.
            match = ' AND '.join(f'p.{key} IS NOT DISTINCT FROM f.{key}' for key in keys)
            sql = (f'WITH fresh AS ({sql}) SELECT * FROM fresh UNION ALL BY NAME '
                   f'SELECT p.* FROM {old} p ANTI JOIN fresh f ON {match}')
        datum = self.x.emit(rel, f'SELECT * FROM ({sql}) ORDER BY {",".join(keys)}', day)
        if datum:
            self.outputs[rel] = self.x.output[rel]
        return datum

    def status(self, board, key, value, reason=None, *, status=None, **details):
        day = str(value) if value else None
        status = status or ('current' if day == str(self.target) else 'lagged')
        self.boards[board][key] = dict(status=status, data_date=day, reason=reason, **details)

    def market_inputs(self):
        # Derive session adjacency independently of the orchestrator's short quote window.
        self.source('trading_calendar', start=date(2015, 1, 1))
        self.table('oc', """SELECT trade_date,row_number() OVER w day_id,
            lag(trade_date) OVER w prev_date,lead(trade_date) OVER w next_date
            FROM (SELECT DISTINCT trade_date FROM src_trading_calendar WHERE is_trading)
            WINDOW w AS(ORDER BY trade_date)""")
        self.source('daily_bars', start=FROZEN - timedelta(days=30))
        self.source('adj_factors', start=FROZEN - timedelta(days=30), layer='derived')
        self.table('obars', """WITH quotes AS (
          SELECT symbol,trade_date,open,high,low,close,amount
          FROM src_daily_bars JOIN oc USING(trade_date)
          WHERE isfinite(close) AND close>0
          QUALIFY row_number() OVER(PARTITION BY symbol,trade_date
              ORDER BY fetched_at DESC NULLS LAST,source,data_version,close,amount)=1
        ), factors AS (
          SELECT symbol,trade_date,factor FROM src_adj_factors
          WHERE adjust_type='hfq' AND isfinite(factor) AND factor>0
          QUALIFY row_number() OVER(PARTITION BY symbol,trade_date
              ORDER BY fetched_at DESC NULLS LAST,source,data_version,factor)=1
        )
        SELECT q.*,f.factor,q.close*f.factor adjusted_close FROM quotes q LEFT JOIN factors f USING(symbol,trade_date)""")
        self.emit('local_daily_prices', f'SELECT * FROM obars WHERE trade_date>DATE {self.cutoff}', ('trade_date','symbol'))
        self.table('ostock_returns', """SELECT b.*,c.prev_date,c.next_date,
          CASE WHEN p.adjusted_close>0 THEN b.adjusted_close/p.adjusted_close-1 END daily_return
          FROM obars b JOIN oc c USING(trade_date) LEFT JOIN obars p ON p.symbol=b.symbol AND p.trade_date=c.prev_date""")
        self.table('oexchange_turnover', """SELECT trade_date,CASE right(symbol,2) WHEN 'SH' THEN 'SSE'
          WHEN 'SZ' THEN 'SZSE' WHEN 'BJ' THEN 'BSE' END exchange,sum(amount) amount FROM obars
          WHERE starts_with(symbol,'6') OR starts_with(symbol,'0') OR starts_with(symbol,'3') OR ends_with(symbol,'.BJ')
          GROUP BY ALL""")
        self.source('index_bars', start=FROZEN - timedelta(days=80))
        self.table('obenchmark', """WITH b AS (
          SELECT trade_date,close,high,low,amount FROM src_index_bars JOIN oc USING(trade_date)
          WHERE symbol='000300.SH' AND frequency='1d' AND isfinite(close) AND close>0
          QUALIFY row_number() OVER(PARTITION BY trade_date ORDER BY fetched_at DESC NULLS LAST,source,close)=1
        )
        SELECT *,close/lag(close) OVER w-1 daily_return,(close-low)/nullif(high-low,0) intraday_recovery,
          close/lag(close,20) OVER w-1 return_20d,low/lag(close) OVER w-1 intraday_drop FROM b
          WINDOW w AS(ORDER BY trade_date)""")


def refresh(x, base_data):
    """Return board component marks and generated files relative to base_data."""
    b = Build(x, base_data)
    if b.target <= FROZEN:
        return {'boards': {}, 'outputs': {}}
    # Floating-point rollups and Parquet bytes must be stable across same-day runs.
    threads = b.sql("SELECT current_setting('threads')").fetchone()[0]
    b.sql('SET threads=1')
    try:
        b.market_inputs()
        owners_daily_capital.flows(b)
        owners_daily_capital.margin(b)
        owners_daily_capital.cross(b)
        owners_daily_capital.dragon(b)
        owners_daily_institution.etfs_and_signals(b)
        owners_daily_capital.balance(b)
        owners_daily_institution.shadow(b)
        owners_daily_institution.funds(b)
    finally:
        b.sql(f'SET threads={threads}')
    return {'boards': b.boards, 'outputs': b.outputs}
