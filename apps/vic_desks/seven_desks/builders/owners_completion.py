from seven_desks.settings import CACHE as BUILD_ROOT, LAKE, SOURCE as REVIEW_SOURCE, FROZEN_VERSION
from seven_desks.store import version_root, connect
"""Offline completion of the ownership desks; formal lake is strictly read-only.

Usage: python -m seven_desks.builders.owners_completion [flows|national|funds|events|all]
All new outputs are atomically written to this project's output/seven-desks/owners.
"""
from pathlib import Path
import json
import sys
import duckdb

PROJECT=Path(__file__).resolve().parents[2]
OUT=(BUILD_ROOT / 'owners')
LAKE=LAKE
REVIEW=LAKE/'meta/review/seven-followup-20261113/owners'
END='2026-11-09'
c=connect(config={'threads':2,'memory_limit':'4GB'})
c.execute('SET preserve_insertion_order=false')
for folder,prefix in [(REVIEW,''),(OUT,'l_')]:
 for p in folder.glob('*.parquet'):
  if p.name.startswith(('_','tmp_')):continue
  c.execute(f"CREATE VIEW {prefix+p.stem} AS SELECT * FROM read_parquet('{p}',hive_partitioning=false)")

def raw(n):
 p=next(LAKE/l/n for l in ['curated','derived'] if (LAKE/l/n).exists())
 c.execute(f"CREATE OR REPLACE VIEW raw_{n} AS SELECT * FROM read_parquet('{p}/**/*.parquet',hive_partitioning=false,union_by_name=true)")
 return 'raw_'+n

def emit(n,sql):
 p=OUT/(n+'.parquet');tmp=p.with_suffix('.parquet.tmp')
 if '--resume' in sys.argv and p.exists() and '--rebuild='+n not in sys.argv:print('reuse',n,flush=True);return
 c.execute(f"COPY ({sql}) TO '{tmp}' (FORMAT PARQUET,COMPRESSION ZSTD)")
 tmp.replace(p)
 c.execute(f"CREATE OR REPLACE VIEW l_{n} AS SELECT * FROM read_parquet('{p}')")
 print(n,c.execute(f'SELECT count(*) FROM l_{n}').fetchone()[0],flush=True)

def flows():
 raw('trading_calendar')
 c.execute("CREATE TEMP TABLE cal AS SELECT DISTINCT trade_date,row_number() OVER(ORDER BY trade_date) day_id FROM raw_trading_calendar WHERE is_trading AND trade_date BETWEEN DATE '2016-01-01' AND DATE '2026-11-09'")
 emit('stock_flow_complete',"""WITH x AS(SELECT f.*,c.day_id,
 CASE WHEN sign(main_net_inflow)=lag(sign(main_net_inflow)) OVER(PARTITION BY symbol ORDER BY day_id) THEN 0 ELSE 1 END sign_change
 FROM stock_fund_flow_windows f JOIN cal c USING(trade_date)),g AS(SELECT *,sum(sign_change) OVER(PARTITION BY symbol ORDER BY day_id) island FROM x),a AS(
 SELECT * EXCLUDE(sign_change,island),sign(main_net_inflow)*row_number() OVER(PARTITION BY symbol,island ORDER BY day_id) consecutive_days,
 CASE WHEN count(main_net_inflow) OVER w60=60 THEN sum(main_net_inflow) OVER w60 END flow_60d,
 CASE WHEN count(turnover) OVER w5=5 THEN sum(turnover) OVER w5 END turnover_5d,
 CASE WHEN count(turnover) OVER w20=20 THEN sum(turnover) OVER w20 END turnover_20d,
 CASE WHEN count(turnover) OVER w60=60 THEN sum(turnover) OVER w60 END turnover_60d,
 CASE WHEN count(daily_return) OVER w5=5 THEN exp(sum(ln(1+daily_return)) OVER w5)-1 END return_5d,
 CASE WHEN count(daily_return) OVER w20=20 THEN exp(sum(ln(1+daily_return)) OVER w20)-1 END return_20d,
 CASE WHEN count(daily_return) OVER w60=60 THEN exp(sum(ln(1+daily_return)) OVER w60)-1 END return_60d,
 list(main_net_inflow) OVER(PARTITION BY symbol ORDER BY day_id ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) recent_20_flows
 FROM g WINDOW w5 AS(PARTITION BY symbol ORDER BY day_id RANGE BETWEEN 4 PRECEDING AND CURRENT ROW),w20 AS(PARTITION BY symbol ORDER BY day_id RANGE BETWEEN 19 PRECEDING AND CURRENT ROW),w60 AS(PARTITION BY symbol ORDER BY day_id RANGE BETWEEN 59 PRECEDING AND CURRENT ROW))
 SELECT * FROM a""")
 emit('sw_flow_complete',"""WITH a AS(SELECT f.trade_date,s.industry_code,max(s.industry_name) industry_name,
 sum(f.main_net_inflow) net_flow,sum(f.flow_5d) flow_5d,sum(f.flow_20d) flow_20d,sum(f.flow_60d) flow_60d,
 sum(f.turnover) turnover,sum(f.turnover_5d) turnover_5d,sum(f.turnover_20d) turnover_20d,sum(f.turnover_60d) turnover_60d,
 avg(f.daily_return) return_1d,avg(f.return_5d) return_5d,avg(f.return_20d) return_20d,avg(f.return_60d) return_60d,count(*) company_count
 FROM l_stock_flow_complete f LEFT JOIN l_stock_context s USING(symbol) GROUP BY ALL),x AS(SELECT *,CASE WHEN sign(net_flow)=lag(sign(net_flow)) OVER(PARTITION BY industry_code ORDER BY trade_date) THEN 0 ELSE 1 END changed FROM a),g AS(SELECT *,sum(changed) OVER(PARTITION BY industry_code ORDER BY trade_date) island FROM x)
 SELECT * EXCLUDE(changed,island),net_flow/nullif(turnover,0) net_ratio,return_1d mean_daily_return,sign(net_flow)*row_number() OVER(PARTITION BY industry_code,island ORDER BY trade_date) consecutive_days FROM g""")
 emit('sector_flow_complete',"""WITH x AS(SELECT s.*,c.day_id,CASE WHEN sign(net_flow)=lag(sign(net_flow)) OVER(PARTITION BY provider,board_type,sector_id ORDER BY c.day_id) THEN 0 ELSE 1 END changed FROM sector_flow_windows s JOIN cal c USING(trade_date)),g AS(SELECT *,sum(changed) OVER(PARTITION BY provider,board_type,sector_id ORDER BY day_id) island FROM x)
 SELECT * EXCLUDE(changed,island),CASE WHEN count(net_flow) OVER w=60 THEN sum(net_flow) OVER w END flow_60d,change_pct/100 return_1d,CASE WHEN count(change_pct) OVER w5=5 THEN exp(sum(ln(1+change_pct/100)) OVER w5)-1 END return_5d,CASE WHEN count(change_pct) OVER w20=20 THEN exp(sum(ln(1+change_pct/100)) OVER w20)-1 END return_20d,CASE WHEN count(change_pct) OVER w=60 THEN exp(sum(ln(1+change_pct/100)) OVER w)-1 END return_60d,sign(net_flow)*row_number() OVER(PARTITION BY provider,board_type,sector_id,island ORDER BY day_id) consecutive_days FROM g WINDOW w AS(PARTITION BY provider,board_type,sector_id ORDER BY day_id RANGE BETWEEN 59 PRECEDING AND CURRENT ROW),w5 AS(PARTITION BY provider,board_type,sector_id ORDER BY day_id RANGE BETWEEN 4 PRECEDING AND CURRENT ROW),w20 AS(PARTITION BY provider,board_type,sector_id ORDER BY day_id RANGE BETWEEN 19 PRECEDING AND CURRENT ROW)""")
 emit('market_stock_flow',"""SELECT trade_date,sum(main_net_inflow) main_net,sum(super_large_net_inflow) super_large_net,sum(large_net_inflow) large_net,sum(medium_net_inflow) medium_net,sum(small_net_inflow) small_net,sum(turnover) turnover,count(*) sample_count FROM l_stock_flow_complete GROUP BY trade_date""")
 emit('market_stock_flow_windows',"""SELECT *,sum(main_net) OVER w5 main_net_5d,sum(main_net) OVER w20 main_net_20d,sum(main_net) OVER w60 main_net_60d,
 sum(super_large_net) OVER w5 super_large_net_5d,sum(super_large_net) OVER w20 super_large_net_20d,sum(super_large_net) OVER w60 super_large_net_60d,
 sum(large_net) OVER w5 large_net_5d,sum(large_net) OVER w20 large_net_20d,sum(large_net) OVER w60 large_net_60d,
 sum(medium_net) OVER w5 medium_net_5d,sum(medium_net) OVER w20 medium_net_20d,sum(medium_net) OVER w60 medium_net_60d,
 sum(small_net) OVER w5 small_net_5d,sum(small_net) OVER w20 small_net_20d,sum(small_net) OVER w60 small_net_60d
 FROM l_market_stock_flow WINDOW w5 AS(ORDER BY trade_date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW),w20 AS(ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW),w60 AS(ORDER BY trade_date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW)""")
 emit('flow_distribution_history',"""SELECT trade_date,CASE WHEN main_turnover_ratio<-.1 THEN '<-10%' WHEN main_turnover_ratio<-.05 THEN '-10%至-5%' WHEN main_turnover_ratio<0 THEN '-5%至0' WHEN main_turnover_ratio<.05 THEN '0至5%' WHEN main_turnover_ratio<.1 THEN '5%至10%' WHEN main_turnover_ratio>=.1 THEN '>=10%' ELSE '缺失' END bucket,count(*) sample_count FROM stock_fund_flow_windows GROUP BY ALL""")
 emit('margin_exchange_complete',"""SELECT * FROM margin_exchange_flow UNION ALL BY NAME SELECT 'ALL' exchange,trade_date,
 CASE WHEN count(margin_balance)=2 THEN sum(margin_balance) END margin_balance,
 CASE WHEN count(margin_buy)=2 THEN sum(margin_buy) END margin_buy,
 CASE WHEN count(margin_repay)=2 THEN sum(margin_repay) END margin_repay,
 CASE WHEN count(short_balance)=2 THEN sum(short_balance) END short_balance,
 sum(total_balance) total_balance,
 CASE WHEN count(financing_net_buy)=2 THEN sum(financing_net_buy) END financing_net_buy,
 CASE WHEN count(balance_change)=2 THEN sum(balance_change) END balance_change,
 CASE WHEN count(financing_buy_turnover_ratio)=2 THEN sum(margin_buy)/nullif(sum(margin_buy/nullif(financing_buy_turnover_ratio,0)),0) END financing_buy_turnover_ratio,
 'SSE+SZSE only;missing_component_stays_null' quality_status
 FROM margin_exchange_flow WHERE exchange IN('SSE','SZSE') GROUP BY trade_date HAVING count(*)=2""")
 emit('margin_industry_complete',"""WITH x AS(SELECT m.*,s.industry_code,s.industry_name,CASE WHEN ends_with(m.symbol,'.SH') THEN 'SSE' WHEN ends_with(m.symbol,'.SZ') THEN 'SZSE' WHEN ends_with(m.symbol,'.BJ') THEN 'BSE' END exchange FROM margin_security_flow m JOIN l_stock_context s USING(symbol)),u AS(SELECT * FROM x UNION ALL SELECT * REPLACE('ALL' AS exchange) FROM x WHERE exchange IN('SSE','SZSE'))
 SELECT exchange,trade_date,industry_code,max(industry_name) industry_name,sum(margin_balance) margin_balance,sum(margin_buy) margin_buy,sum(balance_change) balance_change,count(*) sample_count FROM u GROUP BY ALL""")
 emit('margin_ratio_complete',"""SELECT * FROM l_margin_float_ratio UNION ALL SELECT 'ALL',trade_date,CASE WHEN count(margin_balance)=2 THEN sum(margin_balance) END margin_balance,CASE WHEN count(float_market_value)=2 THEN sum(float_market_value) END float_market_value,sum(denominator_sample) denominator_sample,CASE WHEN count(margin_balance)=2 AND count(float_market_value)=2 THEN sum(margin_balance)/nullif(sum(float_market_value),0) END margin_float_ratio FROM l_margin_float_ratio WHERE exchange IN('SSE','SZSE') GROUP BY trade_date HAVING count(*)=2""")

def national():
 raw('daily_bars');raw('adj_factors')
 c.execute("CREATE TEMP VIEW prices AS SELECT b.symbol,b.trade_date,b.open,b.high,b.low,b.close,b.amount,f.factor,b.close*f.factor adjusted_close FROM raw_daily_bars b LEFT JOIN raw_adj_factors f ON b.symbol=f.symbol AND b.trade_date=f.trade_date AND f.adjust_type='hfq' WHERE b.trade_date BETWEEN DATE '2015-01-01' AND DATE '2026-11-09'")
 emit('daily_prices',"SELECT * FROM prices")
 emit('stock_period_returns',"""WITH x AS(SELECT symbol,report_period,adjusted_close,least(last_day(report_period+INTERVAL '3 months'),DATE '2026-11-09') end_date FROM period_stock_context),a AS(SELECT x.*,p.trade_date return_price_date,p.adjusted_close end_adjusted_close FROM x ASOF LEFT JOIN prices p ON x.symbol=p.symbol AND x.end_date>=p.trade_date)
 SELECT symbol,report_period,end_date,return_price_date,end_adjusted_close/nullif(adjusted_close,0)-1 post_period_return FROM a""")
 emit('national_change_heat',"""WITH x AS(SELECT 'medium' AS scope,c.*,s.industry_code,s.industry_name FROM national_changes c LEFT JOIN l_stock_context s USING(symbol) UNION ALL SELECT 'narrow',c.*,s.industry_code,s.industry_name FROM national_changes c LEFT JOIN l_stock_context s USING(symbol) WHERE c.entity_id IN('hj_inv','hj_am','zj'))
 SELECT scope,report_period,entity_id,industry_code,max(industry_name) industry_name,change_status,count(*) subject_records,count(DISTINCT symbol) stock_count,sum(current_value) current_value,sum(estimated_change_value) estimated_change_value FROM x GROUP BY ALL""")
 emit('national_change_counts',"""WITH x AS(SELECT 'medium' AS scope,* FROM national_changes UNION ALL SELECT 'narrow',* FROM national_changes WHERE entity_id IN('hj_inv','hj_am','zj')) SELECT scope,report_period,count(DISTINCT symbol) FILTER(WHERE change_status='new_visible') new_count,count(DISTINCT symbol) FILTER(WHERE change_status='exit_visible') exit_count,count(DISTINCT symbol) FILTER(WHERE change_status='increase') increase_count,count(DISTINCT symbol) FILTER(WHERE change_status='decrease') decrease_count,sum(estimated_change_value) estimated_change_value FROM x GROUP BY ALL""")
 emit('national_entity_changes',"""SELECT entity_id,report_period,count(*) FILTER(WHERE change_status='new_visible') new_count,count(*) FILTER(WHERE change_status='exit_visible') exit_count,count(*) FILTER(WHERE change_status='increase') increase_count,count(*) FILTER(WHERE change_status='decrease') decrease_count,sum(estimated_change_value) estimated_change_value FROM national_changes GROUP BY ALL""")
 emit('national_band_distribution',"""SELECT scope,report_period,CASE WHEN float_market_value<1e9 THEN 'lt10' WHEN float_market_value<5e9 THEN '10_50' WHEN float_market_value<2e10 THEN '50_200' WHEN float_market_value<1e11 THEN '200_1000' WHEN float_market_value>=1e11 THEN 'ge1000' ELSE 'unknown' END band,sum(market_value) market_value,count(*) stock_count FROM l_national_scope_stock GROUP BY ALL""")
 emit('national_stock_universe',"""WITH exits AS(SELECT 'medium' AS scope,c.* FROM national_changes c WHERE change_status='exit_visible' UNION ALL SELECT 'narrow',c.* FROM national_changes c WHERE change_status='exit_visible' AND entity_id IN('hj_inv','hj_am','zj')),a AS(SELECT scope,report_period,symbol,sum(previous_shares) previous_shares,count(DISTINCT entity_id) previous_subject_count,string_agg(DISTINCT entity_id,',') previous_subjects FROM exits GROUP BY ALL)
 SELECT h.*,r.post_period_return,false exited_all FROM l_national_scope_stock h LEFT JOIN l_stock_period_returns r USING(symbol,report_period)
 UNION ALL BY NAME SELECT a.scope,a.report_period,a.symbol,0.0 shares,NULL::DOUBLE holding_pct,NULL::DOUBLE market_value,0::BIGINT subject_count,'' subjects,0.0 account_count,s.industry_code,s.industry_name,s.name,s.float_market_value,CASE WHEN starts_with(a.symbol,'688') THEN '科创板' WHEN starts_with(a.symbol,'3') THEN '创业板' WHEN ends_with(a.symbol,'.BJ') THEN '北交所' ELSE '主板' END market_board,r.post_period_return,true exited_all FROM a LEFT JOIN l_stock_context s ON s.symbol=a.symbol LEFT JOIN l_stock_period_returns r ON r.symbol=a.symbol AND r.report_period=a.report_period ANTI JOIN l_national_scope_stock h ON h.scope=a.scope AND h.report_period=a.report_period AND h.symbol=a.symbol""")
 emit('etf_trading_complete',"""WITH x AS(SELECT e.*,p.amount turnover,avg(p.amount) OVER w20 previous_20d_average_turnover,count(p.amount) OVER w20 turnover_history_samples,
 CASE WHEN sign(net_subscription)=lag(sign(net_subscription)) OVER(PARTITION BY e.symbol ORDER BY e.day_id) THEN 0 ELSE 1 END changed FROM etf_daily_flow e LEFT JOIN prices p USING(symbol,trade_date) WINDOW w20 AS(PARTITION BY e.symbol ORDER BY e.day_id RANGE BETWEEN 20 PRECEDING AND 1 PRECEDING)),g AS(SELECT *,sum(changed) OVER(PARTITION BY symbol ORDER BY day_id) island FROM x)
 SELECT * EXCLUDE(changed,island),CASE WHEN turnover_history_samples=20 THEN turnover/nullif(previous_20d_average_turnover,0) END volume_ratio,sign(net_subscription)*row_number() OVER(PARTITION BY symbol,island ORDER BY day_id) consecutive_days FROM g""")
 emit('etf_quarterly_flows',"""SELECT symbol,max(name) AS name,cast(date_trunc('quarter',trade_date) AS DATE) quarter_start,sum(net_subscription) net_subscription,count(net_subscription) valid_days,count(*) observed_days FROM etf_daily_flow GROUP BY ALL""")

def funds():
 raw('fund_profiles');raw('fund_asset_allocation_snapshot');raw('index_bars')
 c.execute("CREATE TEMP VIEW profile AS SELECT symbol,management company_id FROM raw_fund_profiles QUALIFY row_number() OVER(PARTITION BY symbol ORDER BY fetched_at DESC)=1")
 emit('company_asset_snapshot',"""WITH a AS(SELECT * FROM raw_fund_asset_allocation_snapshot WHERE report_period<=DATE '2026-11-09' QUALIFY row_number() OVER(PARTITION BY symbol,report_period ORDER BY fetched_at DESC)=1),keys AS(SELECT DISTINCT fund_symbol,report_period FROM fund_holdings_pit WHERE month(report_period) IN(6,12)) SELECT p.company_id,k.report_period,sum(a.share_nav) net_assets,sum(a.stock_market_value) reported_stock_market_value,count(a.share_nav) net_asset_fund_count,count(*) holding_fund_count,sum(a.stock_market_value)/nullif(sum(a.share_nav),0) stock_allocation FROM keys k JOIN profile p ON k.fund_symbol=p.symbol LEFT JOIN a ON a.symbol=k.fund_symbol AND a.report_period=k.report_period GROUP BY ALL""")
 emit('company_rank_complete',"""WITH r AS(SELECT company_id,max(equal_weight_return) FILTER(WHERE calendar_days=365) return_1y,max(equal_weight_return) FILTER(WHERE calendar_days=730) return_2y,max(scale_weight_return) FILTER(WHERE calendar_days=365) weighted_return_1y,max(equal_weight_excess) FILTER(WHERE calendar_days=365) excess_1y FROM company_return_windows GROUP BY company_id)
 SELECT p.*,p.visible_market_value-old.visible_market_value period_value_change,a.net_assets,a.stock_allocation,p.visible_market_value/nullif(a.net_assets,0) holding_nav_ratio,a.net_asset_fund_count*1.0/nullif(a.holding_fund_count,0) net_asset_coverage,d.mean_visible_halfyears,r.return_1y,r.return_2y,r.weighted_return_1y,r.excess_1y,i.implied_return FROM company_period p LEFT JOIN company_period old ON old.company_id=p.company_id AND old.report_period=last_day(p.report_period-INTERVAL '6 months') LEFT JOIN l_company_asset_snapshot a ON a.company_id=p.company_id AND a.report_period=p.report_period LEFT JOIN l_company_duration_summary d ON d.company_id=p.company_id AND d.report_period=p.report_period LEFT JOIN r ON r.company_id=p.company_id LEFT JOIN company_implied_halfyear_return i ON i.company_id=p.company_id AND i.report_period=p.report_period""")
 emit('company_holding_complete',"""SELECT h.*,n.name,c.change_status,c.share_change_ratio,c.estimated_change_value,d.consecutive_halfyears,h.weight-b.weight influence_pp,r.post_period_return FROM company_stock_holdings h LEFT JOIN company_changes c ON c.entity_id=h.company_id AND c.report_period=h.report_period AND c.symbol=h.symbol LEFT JOIN company_stock_holding_duration d ON d.company_id=h.company_id AND d.symbol=h.symbol AND d.report_period=h.report_period LEFT JOIN company_stock_holdings b ON b.company_id=h.company_id AND b.symbol=h.symbol AND b.report_period=last_day(h.report_period-INTERVAL '6 months') LEFT JOIN l_stock_period_returns r ON r.symbol=h.symbol AND r.report_period=h.report_period LEFT JOIN l_names n ON n.symbol=h.symbol""")
 emit('fund_industry_complete',"""WITH changes AS(SELECT c.report_period,s.industry_code,sum(c.estimated_change_value) estimated_net_change FROM company_changes c LEFT JOIN period_stock_context s USING(symbol,report_period) WHERE c.change_status IN('new_visible','increase','decrease','exit_visible','unchanged') GROUP BY ALL)
 SELECT h.*,h.weight-p.weight weight_change,ch.estimated_net_change FROM l_fund_industry_market h LEFT JOIN l_fund_industry_market p ON p.industry_code=h.industry_code AND p.report_period=last_day(h.report_period-INTERVAL '6 months') LEFT JOIN changes ch ON ch.report_period=h.report_period AND ch.industry_code=h.industry_code""")
 emit('fund_heat_complete',"""WITH changes AS(SELECT report_period,symbol,count(*) FILTER(WHERE change_status='new_visible') new_count,count(*) FILTER(WHERE change_status='increase') increase_count,count(*) FILTER(WHERE change_status='decrease') decrease_count,count(*) FILTER(WHERE change_status='exit_visible') exit_count FROM company_changes WHERE change_status IN('new_visible','increase','decrease','exit_visible','unchanged') GROUP BY ALL)
 SELECT f.*,n.name,s.industry_name,s.float_shares*s.close float_market_value,c.new_count,c.increase_count,c.decrease_count,c.exit_count FROM fund_stock_score f LEFT JOIN changes c USING(report_period,symbol) LEFT JOIN period_stock_context s USING(symbol,report_period) LEFT JOIN l_names n USING(symbol)""")
 emit('benchmark_daily',"""SELECT trade_date,close,100*close/first_value(close) OVER(ORDER BY trade_date) benchmark_nav FROM raw_index_bars WHERE symbol='000300.SH' AND frequency='1d' AND trade_date BETWEEN DATE '2016-01-01' AND DATE '2026-11-09' QUALIFY row_number() OVER(PARTITION BY trade_date ORDER BY fetched_at DESC)=1""")
 emit('fund_band_complete',"""SELECT report_period,size_band,count(*) company_count,sum(visible_market_value) market_value,sum(fund_count) fund_count,sum(estimated_net_change) estimated_change_value,sum(new_count) new_count,sum(increase_count) increase_count,sum(decrease_count) decrease_count,sum(exit_count) exit_count FROM l_company_rank_complete GROUP BY ALL""")

def events():
 raw('trading_calendar');raw('sector_members');raw('share_unlock_disclosures')
 c.execute("CREATE TEMP TABLE cal AS SELECT DISTINCT trade_date,row_number() OVER(ORDER BY trade_date) day_id FROM raw_trading_calendar WHERE is_trading AND trade_date BETWEEN DATE '2016-01-01' AND DATE '2026-11-09'")
 emit('dragon_regulars_history',"""WITH x AS(SELECT e.*,c.day_id FROM dragon_tiger_events e JOIN cal c USING(trade_date)) SELECT t.trade_date anchor_date,x.symbol,count(*) event_count,sum(net_amount) observed_net_amount,count(net_amount) valid_amount_days,max(x.trade_date) latest_date,avg(next_trading_day_return) observed_next_return FROM cal t JOIN x ON x.day_id BETWEEN t.day_id-4 AND t.day_id GROUP BY ALL""")
 emit('dragon_themes_history',"""WITH m AS(SELECT * FROM raw_sector_members WHERE as_of_date<=DATE '2026-11-09' QUALIFY row_number() OVER(PARTITION BY symbol,sector_code ORDER BY as_of_date DESC)=1) SELECT e.trade_date,m.sector_code,max(m.sector_name) sector_name,count(DISTINCT symbol) stock_count,sum(net_amount) observed_net_amount,count(net_amount) valid_amount_count FROM dragon_tiger_events e JOIN m USING(symbol) GROUP BY ALL""")
 emit('dragon_next_distribution',"""SELECT trade_date,CASE WHEN next_trading_day_return<-.05 THEN '<-5%' WHEN next_trading_day_return<0 THEN '-5%至0' WHEN next_trading_day_return<.05 THEN '0至5%' WHEN next_trading_day_return>=.05 THEN '>=5%' ELSE '缺失' END bucket,count(*) event_count FROM dragon_tiger_events GROUP BY ALL""")
 emit('repurchase_versions',"""WITH x AS(SELECT * FROM repurchase_announcements QUALIFY row_number() OVER(PARTITION BY symbol,announce_date ORDER BY end_date DESC NULLS LAST,event_id DESC)=1) SELECT *,lead(announce_date) OVER(PARTITION BY symbol ORDER BY announce_date) valid_to FROM x""")
 emit('unlock_versions',"""WITH x AS(SELECT symbol,unlock_date,holder_name,announce_date,share_type,float_share,share_ratio FROM raw_share_unlock_disclosures WHERE announce_date<=DATE '2026-11-09' AND unlock_date>=DATE '2016-01-01' QUALIFY row_number() OVER(PARTITION BY symbol,unlock_date,holder_name,share_type,announce_date ORDER BY fetched_at DESC)=1)
 SELECT *,lead(announce_date) OVER(PARTITION BY symbol,unlock_date,holder_name,share_type ORDER BY announce_date) valid_to FROM x""")
 emit('unlock_history_weekly',"""SELECT cast(date_trunc('week',unlock_date) AS DATE) week_start,sum(float_share) unlock_shares,count(*) disclosure_count FROM unlock_valued WHERE unlock_date<=DATE '2026-11-09' GROUP BY ALL""")

if __name__=='__main__':
 group=sys.argv[1] if len(sys.argv)>1 else 'all'
 for name,fn in [('flows',flows),('national',national),('funds',funds),('events',events)]:
  if group in ('all',name):fn()
 c.close()
