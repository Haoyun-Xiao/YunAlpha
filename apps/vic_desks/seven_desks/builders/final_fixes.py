from seven_desks.settings import CACHE as BUILD_ROOT, LAKE, SOURCE as REVIEW_SOURCE, FROZEN_VERSION
from seven_desks.store import version_root, connect
"""Read-only local fixes: daily turnover and the approved support-temperature proxy."""
from pathlib import Path
import duckdb
from collections import deque
from bisect import bisect_left,bisect_right,insort
ROOT=Path(__file__).resolve().parents[2];OUT=(BUILD_ROOT / 'owners')
SRC=LAKE/'meta/review/seven-followup-20261113/owners'
c=connect(config={'threads':2,'memory_limit':'2GB'})
c.execute(f"COPY(SELECT d.symbol,d.trade_date,d.turnover_rate FROM read_parquet('{LAKE}/curated/stock_daily_research_inputs/**/*.parquet',hive_partitioning=false) d SEMI JOIN read_parquet('{SRC}/dragon_tiger_events.parquet') e USING(symbol,trade_date) WHERE d.trade_date<='2026-11-09') TO '{OUT}/dragon_turnover.parquet' (FORMAT PARQUET,COMPRESSION ZSTD)")
rows=c.execute(f"SELECT trade_date,volume_ratio,CASE WHEN benchmark_return IS NOT NULL THEN greatest(0,-benchmark_return) END,intraday_recovery FROM read_parquet('{SRC}/national_etf_pool_daily.parquet') WHERE trade_date<='2026-11-09' ORDER BY trade_date").fetchall();hist=[deque() for _ in range(3)];ordered=[[] for _ in range(3)];result=[]
for row in rows:
 ranks=[];counts=[]
 for i,v in enumerate(row[1:]):
  hist[i].append(v)
  if v is not None:insort(ordered[i],v)
  if len(hist[i])>252:
   old=hist[i].popleft()
   if old is not None:ordered[i].pop(bisect_left(ordered[i],old))
  n=len(ordered[i]);counts.append(n);ranks.append((bisect_left(ordered[i],v)+bisect_right(ordered[i],v))/(2*n)*100 if v is not None and n>=120 else None)
 result.append((row[0],sum(ranks)/3 if all(v is not None for v in ranks) else None,*ranks,*counts))
c.execute('CREATE TABLE x(trade_date DATE,temperature_proxy DOUBLE,volume_percentile DOUBLE,pressure_percentile DOUBLE,recovery_percentile DOUBLE,volume_samples INTEGER,pressure_samples INTEGER,recovery_samples INTEGER)');c.executemany('INSERT INTO x VALUES(?,?,?,?,?,?,?,?)',result)
c.execute(f"COPY(SELECT *, '252交易日；放量/跌幅压力/日内修复中位秩分位等权；每项至少120；独立近似' formula FROM x) TO '{OUT}/temperature_proxy.parquet' (FORMAT PARQUET)")
c.close()
