"""Bounded formal Parquet inputs for the daily dashboard refresh (no SDK or lake writes)."""
from collections import defaultdict
from datetime import date, timedelta
import json
from pathlib import Path
import re

import duckdb


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


class DailyInputs:
    def __init__(self, root, bound, staging):
        self.root, self.bound, self.staging = root, bound, staging
        self.db = duckdb.connect(config={'threads': 2, 'memory_limit': '2GB', 'temp_directory': str(staging/'spill')})
        self.stats = {}
        self.days = []
        self.files = {}
        self.output = {}

    def view(self, dataset, start=None, end=None, *, layer='curated', alias=None, day=None):
        folder = self.root/layer/dataset
        end = end or self.bound
        sources = []
        for path in sorted(folder.glob('*')):
            name = path.name
            match = re.fullmatch(r'[^=]+=(\d{4})(?:-(\d\d)(?:-(\d\d))?)?', name)
            if match:
                year, month, number = match.groups()
                lower = date(int(year), int(month or 1), int(number or 1))
                upper = lower if number else (date(lower.year+(lower.month==12), lower.month%12+1, 1)-timedelta(days=1) if month else date(lower.year,12,31))
                if lower>end or (start and upper<start):
                    continue
            sources.extend(path.rglob('*.parquet') if path.is_dir() else [path] if path.suffix=='.parquet' else [])
        if not sources:
            return False
        for path in sources:
            st = path.stat()
            self.files[path]=(st.st_size,st.st_mtime_ns)
        clause = ''
        if day is not None:
            clause = f' WHERE "{day}"<=DATE {literal(end)}'
            if start:
                clause += f' AND "{day}">=DATE {literal(start)}'
        self.db.execute(f'CREATE OR REPLACE TEMP VIEW {alias or dataset} AS SELECT * FROM read_parquet('
                        f'[{",".join(literal(p) for p in sources)}],hive_partitioning=false,union_by_name=true)' + clause)
        return True

    def calendar(self, start=None):
        start=start or self.bound-timedelta(days=180)
        if not self.view('trading_calendar',start,day='trade_date'):
            raise ValueError('正式交易日历缺失')
        self.db.execute("""CREATE TEMP TABLE sessions AS SELECT trade_date,row_number() OVER(ORDER BY trade_date)::BIGINT session_id
                          FROM (SELECT DISTINCT trade_date FROM trading_calendar WHERE is_trading)""")
        self.days=[row[0] for row in self.db.execute('SELECT trade_date FROM sessions ORDER BY 1').fetchall()]
        if not self.days:
            raise ValueError('目标范围没有正式交易日')
        return self.days[-1]

    def emit(self, rel, sql, date_col='trade_date', *, coverage=None):
        dest=self.staging/rel
        name='output_'+str(len(self.output))
        self.db.execute(f'CREATE OR REPLACE TEMP TABLE {name} AS ({sql})')
        n, first, last = self.db.execute(f'SELECT count(*),min({date_col}),max({date_col}) FROM {name}').fetchone()
        if not n:
            return None
        dest.parent.mkdir(parents=True, exist_ok=True)
        self.db.execute(f'COPY {name} TO {literal(dest)} (FORMAT PARQUET,COMPRESSION ZSTD)')
        if coverage is not None:
            coverage(self.db, name, last)
        self.output[rel]={'rows':n,'first_date':str(first),'data_date':str(last)}
        return str(last)

    def verify(self):
        for path,(size,modified) in self.files.items():
            st=path.stat()
            if (st.st_size,st.st_mtime_ns)!=(size,modified):
                raise RuntimeError(f'正式输入构建期间更新：{path.relative_to(self.root)}')


def quote_tables(x, start):
    if not x.view('instruments', alias='instruments') or not x.view('daily_bars',start,day='trade_date'):
        return
    x.db.execute("""CREATE TEMP VIEW stocks AS SELECT DISTINCT symbol FROM instruments WHERE asset_type IN ('stock','cdr')""")
    x.db.execute("""CREATE TEMP VIEW valid_bars AS
        SELECT b.symbol,b.trade_date,min(open) AS open,min(high) AS high,min(low) AS low,min(close) AS close,
               min(amount) AS amount,min(volume) AS volume
        FROM daily_bars b JOIN sessions USING(trade_date) JOIN stocks USING(symbol)
        WHERE isfinite(close) AND close>0 AND isfinite(amount) AND amount>=0
        GROUP BY 1,2 HAVING count(DISTINCT struct_pack(o:=open,h:=high,l:=low,c:=close,a:=amount,v:=volume))=1""")
    x.db.execute("""CREATE TEMP VIEW complete_quote_dates AS SELECT trade_date FROM valid_bars GROUP BY 1 HAVING count(*)>=5000""")
    x.emit('revision/daily/stock-quotes.parquet',"""SELECT * FROM valid_bars WHERE trade_date IN (SELECT * FROM complete_quote_dates)
           ORDER BY trade_date,symbol""")
    if x.view('index_bars',start,day='trade_date'):
        rel='revision/daily/index-quotes.parquet'
        x.emit(rel,"""SELECT symbol,trade_date,min(open) AS open,min(high) AS high,
            min(low) AS low,min(close) AS close,min(amount) AS amount
            FROM index_bars JOIN sessions USING(trade_date)
            WHERE frequency='1d' AND isfinite(close) AND close>0 GROUP BY 1,2
            HAVING count(DISTINCT struct_pack(o:=open,h:=high,l:=low,c:=close,a:=amount))=1 ORDER BY trade_date,symbol""")
        if rel in x.output:
            x.output[rel]['symbol_dates']={symbol:str(d) for symbol,d in x.db.execute(
                'SELECT symbol,max(trade_date) FROM read_parquet(?) GROUP BY 1',[str(x.staging/rel)]).fetchall()}


def stock_valuation(x,start):
    if not x.view('valuation_metrics',start,day='trade_date') or not x.view('instruments'):
        return
    x.db.execute("""CREATE TEMP VIEW stock_value_rows AS
      WITH available AS (
        SELECT v.symbol,v.trade_date,min(v.pe_ttm) pe_ttm,min(v.pb) pb,min(v.ps_ttm) ps_ttm,min(v.total_mv) total_mv
        FROM valuation_metrics v JOIN sessions USING(trade_date) WHERE EXISTS (
            SELECT 1 FROM instruments i WHERE v.symbol=i.symbol AND i.asset_type IN ('stock','cdr')
              AND i.list_date<=v.trade_date AND (i.delist_date IS NULL OR i.delist_date>=v.trade_date))
        GROUP BY 1,2 HAVING count(DISTINCT struct_pack(pe:=v.pe_ttm,pb:=v.pb,ps:=v.ps_ttm,mv:=v.total_mv))=1
      ), complete AS (SELECT trade_date FROM available GROUP BY 1 HAVING count(*)>=5000)
      SELECT * FROM available JOIN complete USING(trade_date)""")
    x.emit('revision/daily/valuation-structure.parquet',"""
      SELECT trade_date,count(*) sample_count,count(*) FILTER(WHERE pe_ttm>0) positive_pe_count,
             median(pe_ttm) FILTER(WHERE pe_ttm>0) positive_pe_median,count(pb) valid_pb_count,
             count(*) FILTER(WHERE pb<0) negative_pb_count,(count(*) FILTER(WHERE pb<1))/nullif(count(pb),0) below_book_ratio
      FROM stock_value_rows GROUP BY 1 ORDER BY 1""")
    x.emit('revision/daily/valuation-pe-distribution.parquet',"""
      SELECT trade_date,CASE WHEN pe_ttm IS NULL THEN 'missing' WHEN pe_ttm<=0 THEN 'loss_or_zero' WHEN pe_ttm<10 THEN '0-10'
        WHEN pe_ttm<20 THEN '10-20' WHEN pe_ttm<30 THEN '20-30' WHEN pe_ttm<50 THEN '30-50' WHEN pe_ttm<100 THEN '50-100'
        WHEN pe_ttm<200 THEN '100-200' ELSE '200+' END bucket,
        count(*) stock_count,sum(total_mv) market_value FROM stock_value_rows GROUP BY 1,2 ORDER BY 1,2""")


def stock_flows(x, start):
    if not x.view('fund_flow',x.days[0],day='trade_date'):
        return
    fields={'main_net':'main_net_inflow','super_large_net':'super_large_net_inflow',
            'large_net':'large_net_inflow','medium_net':'medium_net_inflow','small_net':'small_net_inflow'}
    select=','.join(f'min({col}) AS {label}' for label,col in fields.items())
    checks=' AND '.join(f'count(DISTINCT {col})<=1' for col in fields.values())
    x.db.execute(f"""CREATE TEMP TABLE fund_flow_rows AS SELECT symbol,trade_date,{select}
        FROM fund_flow WHERE source='tushare.moneyflow_dc' GROUP BY 1,2 HAVING {checks}""")
    x.db.execute("""CREATE TEMP TABLE fund_flow_dates AS SELECT trade_date FROM fund_flow_rows
                   GROUP BY 1 HAVING count(main_net)>=5000""")
    sums=','.join(f'sum({key}) AS {key}' for key in fields)
    windows=','.join(f'CASE WHEN count({key}) OVER w{n}={n} THEN sum({key}) OVER w{n} END AS {key}_{n}d'
                     for key in fields for n in (5,20,60,100))
    wins=','.join(f'w{n} AS(ORDER BY session_id RANGE BETWEEN {n-1} PRECEDING AND CURRENT ROW)' for n in (5,20,60,100))
    x.emit('revision/daily/capital-market.parquet',f"""WITH a AS (
        SELECT s.trade_date,s.session_id,count(main_net) sample_count,{sums} FROM sessions s
        LEFT JOIN fund_flow_rows f ON f.trade_date=s.trade_date AND f.trade_date IN (SELECT * FROM fund_flow_dates)
        GROUP BY 1,2
      ), b AS (SELECT * EXCLUDE(session_id),{windows} FROM a WINDOW {wins})
      SELECT * FROM b WHERE trade_date>=DATE {literal(start)} AND sample_count>=5000 ORDER BY trade_date""")
    x.emit('revision/daily/capital-stock.parquet',f"""WITH a AS (
        SELECT s.session_id,f.* FROM fund_flow_rows f JOIN sessions s USING(trade_date)
        WHERE f.trade_date IN (SELECT * FROM fund_flow_dates)
    ), b AS (SELECT symbol,trade_date,main_net AS main_net_inflow,
      {','.join(f'CASE WHEN count(main_net) OVER w{n}={n} THEN sum(main_net) OVER w{n} END flow_{n}d' for n in (5,20,60,100))}
      FROM a WINDOW {wins.replace('ORDER BY','PARTITION BY symbol ORDER BY')})
    SELECT * FROM b WHERE trade_date>=DATE {literal(start)} ORDER BY trade_date,symbol""")


def market_money(x,start):
    if not x.view('market_money_flow',x.days[0],day='trade_date'):
        return
    x.db.execute("""CREATE TEMP VIEW dc_market_money AS
      SELECT trade_date,min(net_amount) main_net,
             CASE WHEN count(DISTINCT extra_large_buy_amount) FILTER(WHERE isfinite(extra_large_buy_amount))=1
                  THEN min(extra_large_buy_amount) FILTER(WHERE isfinite(extra_large_buy_amount)) END super_large_net
      FROM market_money_flow WHERE isfinite(net_amount)
      GROUP BY 1 HAVING count(DISTINCT net_amount)=1""")
    # The market endpoint already reports CNY; the stock endpoint reports 万元.
    x.emit('revision/daily/capital-market-native.parquet',"""SELECT m.* FROM dc_market_money m
        JOIN sessions USING(trade_date) ORDER BY trade_date""")
    x.emit('revision/daily/sentiment-money.parquet',f"""WITH w AS (
        SELECT trade_date,CASE WHEN count(main_net) OVER p=20 THEN sum(main_net) OVER p END raw_value
        FROM sessions LEFT JOIN dc_market_money USING(trade_date)
        WINDOW p AS(ORDER BY session_id RANGE BETWEEN 19 PRECEDING AND CURRENT ROW)
      )
      SELECT trade_date,trade_date source_date,raw_value
      FROM w WHERE trade_date>=DATE {literal(start)} AND raw_value IS NOT NULL ORDER BY trade_date""")


def etf_flows(x, start, parent):
    if not x.view('etf_daily_metrics',x.days[0],day='trade_date'):
        return
    classification = parent/'revision/owners/etf_trading_complete.parquet'
    x.db.execute(f"""CREATE TEMP TABLE etf_labels AS SELECT DISTINCT ON(symbol) symbol,name,category,reported_index_code,
                     classification_status FROM read_parquet({literal(classification)}) ORDER BY symbol,trade_date DESC""")
    x.emit('revision/daily/etf-flow.parquet', f"""WITH metrics AS (
       SELECT symbol,trade_date,min(shares_outstanding) shares_outstanding,min(unit_nav) unit_nav,
              min(fund_size) fund_size,min(close) AS "close"
       FROM etf_daily_metrics GROUP BY 1,2
       HAVING count(DISTINCT struct_pack(sh:=shares_outstanding,nav:=unit_nav,fs:=fund_size,c:=close))=1
     ), cal AS(SELECT trade_date,session_id,lead(trade_date) OVER(ORDER BY session_id) available_date FROM sessions),
     rows AS (
       SELECT m.*, p.shares_outstanding previous_shares, c.session_id,c.available_date,
              CASE WHEN m.shares_outstanding>0 AND p.shares_outstanding>0 AND m.unit_nav>0
                       AND c.available_date<=DATE {literal(x.bound)}
                   THEN (m.shares_outstanding-p.shares_outstanding)*m.unit_nav END net_subscription
       FROM metrics m JOIN cal c USING(trade_date) LEFT JOIN cal prev ON c.session_id=prev.session_id+1
       LEFT JOIN metrics p ON p.symbol=m.symbol AND p.trade_date=prev.trade_date
     ), rolled AS (
       SELECT r.*,l.name,l.category,l.reported_index_code,l.classification_status,
        {','.join(f'CASE WHEN count(net_subscription) OVER w{n}={n} THEN sum(net_subscription) OVER w{n} END AS flow_{n}d' for n in (5,20,60))}
       FROM rows r JOIN etf_labels l USING(symbol)
       WINDOW {','.join(f'w{n} AS(PARTITION BY symbol ORDER BY session_id RANGE BETWEEN {n-1} PRECEDING AND CURRENT ROW)' for n in (5,20,60))}
     )
     SELECT * EXCLUDE(session_id) FROM rolled
     WHERE trade_date>=DATE {literal(start)} ORDER BY trade_date,symbol""")


def official_valuation(x, data, current):
    """Reuse the valuation builder only for contiguous dates with matching formal inputs."""
    from seven_desks.prepare_valuation import OFFICIAL, build_tables
    last=date.fromisoformat(current)
    needed=[d for d in x.days if d>last]
    if not needed or 'revision/daily/valuation-structure.parquet' not in x.output:
        return current
    if (x.days[0]>last or not x.view('index_valuation_metrics',date(2004,1,1),day='trade_date')
            or not x.view('index_bars',last,day='trade_date')):
        return current
    db=x.db
    codes=','.join(map(literal,OFFICIAL))
    valid=set(row[0] for row in db.execute(f"""SELECT trade_date FROM (
        SELECT symbol,trade_date FROM index_valuation_metrics WHERE symbol IN ({codes})
          AND isfinite(pe_ttm) AND isfinite(pb) AND pe_ttm>0 AND pb>0
        GROUP BY 1,2 HAVING count(DISTINCT struct_pack(pe:=pe_ttm,b:=pb))=1
        ) GROUP BY 1 HAVING count(*)={len(OFFICIAL)}""").fetchall())
    stock_dates=set(r[0] for r in db.execute('SELECT DISTINCT trade_date FROM stock_value_rows').fetchall())
    through=last
    for d in needed:
        if d not in valid or d not in stock_dates:
            break
        through=d
    if through==last:
        return current
    folder=x.staging/'valuation-inputs'
    folder.mkdir()
    for name, sql in {
        'official':f"""SELECT symbol,trade_date,min(pe_ttm) pe_ttm,min(pb) pb FROM index_valuation_metrics
                      WHERE symbol IN ({codes}) AND trade_date<=DATE {literal(through)}
                      GROUP BY 1,2 HAVING count(DISTINCT struct_pack(pe:=pe_ttm,b:=pb))=1""",
        'prices':f"""SELECT symbol,trade_date,frequency,min(close) AS close,min(amount) AS amount FROM index_bars
                    WHERE frequency='1d' AND trade_date>DATE {literal(last)} AND trade_date<=DATE {literal(through)}
                    GROUP BY 1,2,3 HAVING count(DISTINCT struct_pack(c:=close,a:=amount))=1""",
        'calendar':f"SELECT trade_date,true is_trading FROM sessions WHERE trade_date<=DATE {literal(through)}",
        'stock':f"SELECT * FROM stock_value_rows WHERE trade_date>DATE {literal(last)} AND trade_date<=DATE {literal(through)}",
        'identity':"SELECT DISTINCT symbol,asset_type,list_date,delist_date FROM instruments",
    }.items():
        db.execute(f'COPY ({sql}) TO {literal(folder/(name+".parquet"))} (FORMAT PARQUET,COMPRESSION ZSTD)')
    from shutil import copyfile
    base=data/'market/valuation'
    copyfile(base/'phase3-comparisons.parquet',folder/'comparisons.parquet')
    copyfile(base/'phase3-returns.parquet',folder/'published.parquet')
    tables=build_tables(folder,base,base_cutoff=current,through=str(through),strict=False)
    for name in ('daily','temperature','stock-structure','relative-valuations','pe-histogram','phase3-prices'):
        frame=tables[name]
        rel=f'market/valuation/{name}.parquet'
        dest=x.staging/rel
        dest.parent.mkdir(parents=True,exist_ok=True)
        frame['trade_date']=frame.trade_date.astype('datetime64[ns]').dt.date
        frame.to_parquet(dest,index=False,compression='zstd')
        x.output[rel]={'rows':len(frame),'first_date':str(frame.trade_date.min()),'data_date':str(frame.trade_date.max())}
    return str(through)


def value_screens(x, data):
    """Daily PE/PB/PS ranks, without inferring risk flags, EV or historical signals."""
    required={'revision/daily/stock-quotes.parquet','revision/daily/valuation-structure.parquet'}
    if not required<=x.output.keys():
        return
    frozen=data/'contrarian/ui_factors.parquet'
    x.emit('revision/daily/value-screens.parquet',f"""WITH names AS (
      SELECT symbol,min(name) AS name,min(list_date) list_date FROM instruments
      WHERE asset_type IN ('stock','cdr') AND name IS NOT NULL
      GROUP BY 1 HAVING count(DISTINCT name)=1 AND count(DISTINCT list_date)=1
     ), groups AS (
      SELECT symbol,min(industry_l1) industry_name,min(score_group) score_group
      FROM read_parquet({literal(frozen)}) GROUP BY 1 HAVING count(DISTINCT score_group)=1
     ), sample AS (
      SELECT q.symbol,q.trade_date,q.close,q.amount,n.name,n.list_date,
             g.industry_name,g.score_group,v.pe_ttm,v.pb,v.ps_ttm,
             percent_rank() OVER(PARTITION BY q.trade_date ORDER BY q.amount) amount_rank
      FROM valid_bars q JOIN complete_quote_dates USING(trade_date)
      JOIN stock_value_rows v USING(symbol,trade_date) JOIN names n USING(symbol) JOIN groups g USING(symbol)
      WHERE q.amount>0
     ), long_values AS (
      SELECT * FROM sample UNPIVOT(metric_value FOR metric IN (pe_ttm,pb,ps_ttm))
      WHERE isfinite(metric_value) AND metric_value>0 AND amount_rank>=.05
        AND trade_date-list_date>=365 AND upper(name) NOT LIKE '%ST%'
     ), ranked AS (
      SELECT *,100*(1-(rank() OVER w-1 + (count(*) OVER tied-1)/2.0)/nullif(count(*) OVER grp-1,0)) AS valuation_score,
        count(*) OVER grp group_count
      FROM long_values WINDOW grp AS(PARTITION BY trade_date,score_group,metric),
        w AS(PARTITION BY trade_date,score_group,metric ORDER BY metric_value),
        tied AS(PARTITION BY trade_date,score_group,metric,metric_value)
     )
     SELECT trade_date,symbol,name,industry_name,score_group,metric,metric_value,valuation_score,group_count,close,amount
     FROM ranked WHERE group_count>=10 ORDER BY trade_date,symbol,metric""")
