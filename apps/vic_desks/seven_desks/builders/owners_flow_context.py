from seven_desks.settings import CACHE as BUILD_ROOT, LAKE, SOURCE as REVIEW_SOURCE, FROZEN_VERSION
from seven_desks.store import version_root, connect
"""Precompute same-day denominators and retain source-native stock flow series."""
from pathlib import Path
import duckdb
P=Path(__file__).resolve().parents[2];OUT=(BUILD_ROOT / 'owners')
L=LAKE;S=L/'meta/review/seven-followup-20261113/owners'
c=connect(config={'threads':2});c.execute("SET memory_limit='3GB'")
for n in ['moneyflow','stock_moneyflow_ths','valuation_metrics']:
 c.execute(f"create view {n} as select * from read_parquet('{L}/curated/{n}/**/*.parquet',hive_partitioning=false,union_by_name=true)")
def emit(n,sql):
 c.execute(f"copy ({sql}) to '{OUT}/{n}.parquet' (format parquet,compression zstd)");print(n,flush=True)
emit('stock_three_sources',"""select symbol,trade_date,'moneyflow' provider,net_mf_amount net_flow_native,'provider_native_amount_unit_unverified' unit_status from moneyflow where trade_date between DATE '2026-07-01' and DATE '2026-11-09' union all select symbol,trade_date,'stock_moneyflow_ths',net_amount,'CNY_from_backfill_contract' from stock_moneyflow_ths where trade_date between DATE '2026-07-01' and DATE '2026-11-09' union all select symbol,trade_date,'fund_flow',main_net_inflow,'CNY' from read_parquet('"""+str(S/'stock_fund_flow_windows.parquet')+"""') where trade_date between DATE '2026-07-01' and DATE '2026-11-09'""")
emit('margin_float_ratio',"""with v as(select case when ends_with(symbol,'.SH') then 'SSE' when ends_with(symbol,'.SZ') then 'SZSE' when ends_with(symbol,'.BJ') then 'BSE' end exchange,trade_date,sum(float_mv) float_market_value,count(float_mv) denominator_sample from valuation_metrics where trade_date between DATE '2023-11-09' and DATE '2026-11-09' and (starts_with(symbol,'0') or starts_with(symbol,'3') or starts_with(symbol,'6') or ends_with(symbol,'.BJ')) group by all) select m.exchange,m.trade_date,m.margin_balance,v.float_market_value,v.denominator_sample,m.margin_balance/nullif(v.float_market_value,0) margin_float_ratio from read_parquet('"""+str(S/'margin_exchange_flow.parquet')+"""') m left join v using(exchange,trade_date) where m.trade_date>=DATE '2023-11-09'""")
