from seven_desks.settings import CACHE as BUILD_ROOT, LAKE, SOURCE as REVIEW_SOURCE, FROZEN_VERSION
from seven_desks.store import version_root, connect
"""Offline summaries; all lake reads are read-only, all writes stay in this project."""
from pathlib import Path
import duckdb
ROOT=Path(__file__).resolve().parents[2]
OUT=(BUILD_ROOT / 'owners')
REVIEW=(REVIEW_SOURCE / 'owners')
c=connect(config={'threads':2});c.execute("SET memory_limit='3GB'")
for p in REVIEW.glob('*.parquet'):
 if p.name.startswith(('_','tmp_')):continue
 c.execute(f"CREATE VIEW {p.stem} AS SELECT * FROM read_parquet('{p}')")
def emit(n,sql):
 c.execute(f"COPY ({sql}) TO '{OUT}/{n}.parquet' (FORMAT PARQUET,COMPRESSION ZSTD)");print(n,flush=True)
emit('dragon_regulars',"""select symbol,count(*) event_count,sum(net_amount) observed_net_amount,count(net_amount) valid_amount_days,max(trade_date) latest_date,avg(next_trading_day_return) observed_next_return from dragon_tiger_events where trade_date in(select distinct trade_date from dragon_tiger_events order by trade_date desc limit 5) group by symbol""")
emit('flow_distribution',"""with x as(select *,case when main_turnover_ratio<-.1 then '<-10%' when main_turnover_ratio<-.05 then '-10%至-5%' when main_turnover_ratio<0 then '-5%至0' when main_turnover_ratio<.05 then '0至5%' when main_turnover_ratio<.1 then '5%至10%' when main_turnover_ratio>=.1 then '>=10%' else '缺失' end bucket from stock_fund_flow_windows where trade_date=(select max(trade_date) from stock_fund_flow_windows)) select max(trade_date) trade_date,bucket,count(*) sample_count from x group by bucket""")
emit('shadow_latest_components',"""with a as(select *,max(trade_date) over(partition by portfolio) latest_date from national_shadow_component_daily),x as(select *,segment_value_change/nullif(segment_value-segment_value_change,0) daily_return from a where trade_date=latest_date) select portfolio,symbol,trade_date,report_period,rebalance_date,weight,segment_value,segment_value_change,daily_return,valuation_price_date,quality_status from x""")
emit('shadow_summary',"""with w as(select * from(values('1y',366),('3y',1096)) t(range_id,days)),r as(select n.*,w.range_id from national_shadow_nav n cross join w where trade_date>DATE '2026-11-09'-days::integer),a as(select portfolio,range_id,min(trade_date) start_date,max(trade_date) end_date,arg_min(nav,trade_date) initial_nav,arg_max(nav,trade_date) final_nav,exp(sum(ln(1+daily_return)))-1 portfolio_return,exp(sum(ln(1+benchmark_return)))-1 benchmark_return,stddev_samp(daily_return)*sqrt(252) volatility,min(drawdown) historical_peak_drawdown,arg_max(component_count,trade_date) component_count,arg_max(rebalance_date,trade_date) rebalance_date,arg_max(fresh_price_weight,trade_date) fresh_price_weight from r group by all) select *,portfolio_return-benchmark_return excess_return from a""")
emit('shadow_contribution',"""with w as(select * from(values('1y',366),('3y',1096)) t(range_id,days)),a as(select portfolio,trade_date,rebalance_date,sum(segment_value) total_value,sum(segment_value_change) total_change from national_shadow_component_daily group by all),x as(select c.portfolio,c.symbol,c.trade_date,c.segment_value_change/nullif(a.total_value-a.total_change,0) daily_arithmetic_contribution from national_shadow_component_daily c join a using(portfolio,trade_date,rebalance_date)) select portfolio,symbol,range_id,sum(daily_arithmetic_contribution) arithmetic_contribution,count(daily_arithmetic_contribution) valid_days from x cross join w where trade_date>DATE '2026-11-09'-days::integer group by all""")
# Holding-period duration and company performance are small frozen side tables.
emit('company_duration_summary',"select company_id,report_period,avg(consecutive_halfyears) mean_visible_halfyears from company_stock_holding_duration group by all")
