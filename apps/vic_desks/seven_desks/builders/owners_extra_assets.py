from seven_desks.settings import CACHE as BUILD_ROOT, LAKE, SOURCE as REVIEW_SOURCE, FROZEN_VERSION
from seven_desks.store import version_root, connect
from pathlib import Path
import duckdb
r=LAKE;out=(BUILD_ROOT / 'owners');src=r/'meta/review/seven-followup-20261113/owners'
c=connect(config={'threads':2});c.execute("SET memory_limit='3GB'")
def source(n):
 p=next((r/layer/n for layer in ['curated','derived'] if (r/layer/n).exists()));c.execute(f"create view {n} as select * from read_parquet('{p}/**/*.parquet',hive_partitioning=false,union_by_name=true)")
def emit(n,s):
 c.execute(f"COPY ({s}) TO '{out}/{n}.parquet' (FORMAT PARQUET,COMPRESSION ZSTD)");print(n,flush=True)
for n in ['daily_bars','adj_factors','top_holders','national_team_entity_aliases','fund_holder_structure_snapshot','fund_turnover','fund_profiles','stock_holdertrade_announcements']:source(n)
emit('weekly_prices',"""select b.symbol,cast(date_trunc('week',b.trade_date) as date) week_start,arg_max(b.close,b.trade_date) AS close,arg_max(b.close*f.factor,b.trade_date) adjusted_close,max(b.trade_date) price_date from daily_bars b left join adj_factors f on b.symbol=f.symbol and b.trade_date=f.trade_date and f.adjust_type='hfq' where b.trade_date between DATE '2023-11-09' and DATE '2026-11-09' group by all""")
emit('full_top_holders',"""select symbol,record_date,announce_date,holder_rank,holder_name,holding_shares,holding_pct from top_holders where holder_scope='float' and announce_date<=DATE '2026-11-09' and record_date>=DATE '2020-01-01' qualify row_number() over(partition by symbol,record_date,holder_name order by announce_date desc)=1""")
emit('etf_institution',"""select *,institution_pct/100 institution_ratio,total_shares_yi*1e8*institution_pct/100 institution_shares from fund_holder_structure_snapshot where report_period<=DATE '2026-11-09' qualify row_number() over(partition by symbol,report_period order by fetched_at desc)=1""")
emit('company_disclosed_trades',"""with p as(select symbol,management company_id from fund_profiles qualify row_number() over(partition by symbol order by fetched_at desc)=1),t as(select * from fund_turnover where report_period<=DATE '2026-11-09' and report_period>=DATE '2025-01-01' qualify row_number() over(partition by fund_symbol,report_period,side,holding_symbol order by fetched_at desc)=1) select p.company_id,t.report_period,t.side,t.holding_symbol symbol,max(t.holding_name) AS name,sum(t.amount) disclosed_amount,count(distinct fund_symbol) fund_count from t join p on t.fund_symbol=p.symbol group by all""")
emit('national_announcements',"""select t.symbol,t.announce_date,t.holder_name,a.target_subject_id entity_id,t.trade_type,t.change_vol,t.avg_price,t.change_vol*t.avg_price disclosed_amount from stock_holdertrade_announcements t join national_team_entity_aliases a on t.holder_name=a.observed_holder_name where starts_with(a.mapping_status,'verified') and t.announce_date between DATE '2023-01-01' and DATE '2026-11-09' qualify row_number() over(partition by t.symbol,t.announce_date,t.holder_name,t.trade_type,t.change_vol order by t.fetched_at desc)=1""")
