from seven_desks.settings import CACHE as BUILD_ROOT, LAKE, SOURCE as REVIEW_SOURCE, FROZEN_VERSION
from seven_desks.store import version_root, connect
"""Explicit local proxy formulas, reconstructed top ten, and retrospective samples."""
from pathlib import Path
from collections import deque
from bisect import bisect_left,bisect_right,insort
import duckdb
ROOT=Path(__file__).resolve().parents[2];OUT=(BUILD_ROOT / 'owners')
REVIEW=LAKE/'meta/review/seven-followup-20261113/owners'
c=connect(config={'threads':2});c.execute("SET memory_limit='3GB'")
for p in REVIEW.glob('*.parquet'):
 if p.name.startswith(('_','tmp_')):continue
 c.execute(f"CREATE VIEW {p.stem} AS SELECT * FROM read_parquet('{p}')")
def emit(n,sql):
 c.execute(f"COPY ({sql}) TO '{OUT}/{n}.parquet' (FORMAT PARQUET,COMPRESSION ZSTD)");print(n,flush=True)
rows=c.execute('select trade_date,volume_ratio,CASE WHEN benchmark_return IS NOT NULL THEN greatest(0,-benchmark_return) END pressure,intraday_recovery from national_etf_pool_daily order by trade_date').fetchall();hist=[deque() for _ in range(3)];ordered=[[] for _ in range(3)];result=[]
for row in rows:
 ranks=[];counts=[]
 for i,v in enumerate(row[1:]):
  hist[i].append(v)
  if v is not None:insort(ordered[i],v)
  if len(hist[i])>252:
   old=hist[i].popleft()
   if old is not None:ordered[i].pop(bisect_left(ordered[i],old))
  n=len(ordered[i]);counts.append(n)
  ranks.append((bisect_left(ordered[i],v)+bisect_right(ordered[i],v))/(2*n)*100 if v is not None and n>=120 else None)
 score=sum(ranks)/3 if all(v is not None for v in ranks) else None
 result.append((row[0],score,*ranks,*counts))
c.execute('create table temp_proxy(trade_date date,temperature_proxy double,volume_percentile double,pressure_percentile double,recovery_percentile double,volume_samples integer,pressure_samples integer,recovery_samples integer)');c.executemany('insert into temp_proxy values(?,?,?,?,?,?,?,?)',result)
emit('temperature_proxy','select *,\'252交易观测窗；放量、跌幅压力、日内修复滚动中位秩百分位等权；每项>=120样本；本地代理非原站公式\' formula from temp_proxy')
for n in ['fund_profiles','sector_members']:
 c.execute(f"create view {n} as select * from read_parquet('{LAKE}/curated/{n}/**/*.parquet',hive_partitioning=false,union_by_name=true)")
c.execute("create table profile as select symbol,management company_id from fund_profiles qualify row_number() over(partition by symbol order by fetched_at desc)=1")
c.execute("""create table t10 as select h.fund_symbol,p.company_id,h.report_period,h.holding_symbol symbol,h.shares,h.market_value,row_number() over(partition by h.fund_symbol,h.report_period order by h.market_value desc nulls last,h.holding_symbol) inferred_rank from fund_holdings_pit h join profile p on p.symbol=h.fund_symbol where h.report_period>=DATE '2019-01-01' qualify inferred_rank<=10""")
c.execute("""create table pairs as select distinct a.company_id,a.report_period,b.report_period prev_period from t10 a join t10 b on a.company_id=b.company_id and b.report_period=last_day(a.report_period-INTERVAL '3 months') where month(a.report_period) in(3,9)""")
c.execute("""create table cohort as with p as(select x.company_id,x.report_period,x.prev_period,count(distinct b.fund_symbol) prev_funds,count(distinct a.fund_symbol) matched_funds from pairs x join t10 b on b.company_id=x.company_id and b.report_period=x.prev_period left join t10 a on a.fund_symbol=b.fund_symbol and a.report_period=x.report_period group by all) select *,matched_funds*1.0/nullif(prev_funds,0) coverage_ratio from p""")
emit('quarter_top10_coverage','select * from cohort')
emit('quarter_top10_changes',"""with h as(select a.company_id,a.report_period,a.prev_period,t.fund_symbol,t.symbol,t.shares current_shares,b.shares previous_shares from pairs a join t10 t on t.company_id=a.company_id and t.report_period=a.report_period join (select distinct fund_symbol,report_period from t10) old on old.fund_symbol=t.fund_symbol and old.report_period=a.prev_period left join t10 b on b.fund_symbol=t.fund_symbol and b.symbol=t.symbol and b.report_period=a.prev_period union all select a.company_id,a.report_period,a.prev_period,t.fund_symbol,t.symbol,null,t.shares from pairs a join t10 t on t.company_id=a.company_id and t.report_period=a.prev_period join (select distinct fund_symbol,report_period from t10) cur on cur.fund_symbol=t.fund_symbol and cur.report_period=a.report_period left join t10 b on b.fund_symbol=t.fund_symbol and b.symbol=t.symbol and b.report_period=a.report_period where b.symbol is null),s as(select company_id,report_period,prev_period,symbol,sum(current_shares) current_shares,sum(previous_shares) previous_shares,count(distinct fund_symbol) fund_count from h group by all),v as(select s.*,cur.factor current_factor,prev.factor previous_factor,previous_shares*cur.factor/nullif(prev.factor,0) adjusted_previous_shares from s left join period_stock_context cur on cur.symbol=s.symbol and cur.report_period=s.report_period left join period_stock_context prev on prev.symbol=s.symbol and prev.report_period=s.prev_period) select v.*,current_shares/nullif(adjusted_previous_shares,0)-1 share_change_ratio,c.coverage_ratio,c.matched_funds,c.prev_funds,'visible_market_value_rank_top10;quarter_disclosure_scope_not_certified' quality_status from v join cohort c using(company_id,report_period,prev_period) where c.coverage_ratio>=.8 and c.matched_funds>=5""")
emit('consensus_following_period',"""with p as(select f.report_period,f.consensus,f.symbol,b.adjusted_close/a.adjusted_close-1 following_return from fund_stock_crowding f join period_stock_context a on f.symbol=a.symbol and f.report_period=a.report_period join period_stock_context b on f.symbol=b.symbol and b.report_period=last_day(f.report_period+INTERVAL '6 months') where f.consensus in('buy','sell') and a.adjusted_close>0) select report_period,consensus,avg(following_return) equal_weight_following_return,count(*) stock_count from p group by all""")
emit('dragon_themes',"""with m as(select * from sector_members where as_of_date<=DATE '2026-11-09' qualify row_number() over(partition by symbol,sector_code order by as_of_date desc)=1),e as(select * from dragon_tiger_events where trade_date=(select max(trade_date) from dragon_tiger_events)) select sector_code,sector_name,max(trade_date) trade_date,count(distinct symbol) stock_count,sum(net_amount) observed_net_amount,count(net_amount) valid_amount_count,'current_known_concept_mapping_approx;overlapping_constituents_not_additive' quality_status from e join m using(symbol) group by all""")
