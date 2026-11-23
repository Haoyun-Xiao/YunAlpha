"""Ownership corrections from immutable 23f054e1b2ce868d inputs only.

Run: python3 -m seven_desks.builders.revision_owners
No ingestion, service restart, lake write or version activation is performed.
"""
from __future__ import annotations
from seven_desks.settings import CACHE as BUILD_ROOT, LAKE, SOURCE as REVIEW_SOURCE, FROZEN_VERSION
from seven_desks.store import version_root, connect
import hashlib
import json
from pathlib import Path
import duckdb

PROJECT = Path(__file__).resolve().parents[2]
BASELINE = FROZEN_VERSION
BASE = (version_root(FROZEN_VERSION) / 'data')
OUT = (BUILD_ROOT / 'revision-owners')
CHANGE_FIELDS = ('new_count','increase_count','decrease_count','exit_count','estimated_change_value')


def build(base=BASE, out=OUT):
    base, out = Path(base), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    c = connect(config={'threads':2,'memory_limit':'4GB'})
    manifest = []
    def source(name, rel):
        p = base / rel
        manifest.append({'path':str(p.relative_to(base)), 'sha256':hashlib.sha256(p.read_bytes()).hexdigest(), 'bytes':p.stat().st_size})
        c.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{p}',hive_partitioning=false)")
    def emit(name, sql):
        p=out/(name+'.parquet'); tmp=p.with_suffix('.parquet.tmp')
        c.execute(f"COPY ({sql}) TO '{tmp}' (FORMAT PARQUET,COMPRESSION ZSTD)")
        tmp.replace(p)
        c.execute(f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM '{p}'")
    def rows(sql):
        q=c.execute(sql);return [dict(zip([x[0] for x in q.description],v)) for v in q.fetchall()]
    for name, rel in {
        'aliases':'owners/revision-v3/company_aliases.parquet',
        'old_holdings':'ac_fixed/company_stock_holdings.parquet',
        'old_rank':'ac_fixed/company_rank_complete.parquet',
        'old_changes':'ac_fixed/company_changes.parquet',
        'old_heat':'ac_fixed/fund_heat_complete.parquet',
        'old_industry':'ac_fixed/fund_industry_complete.parquet',
        'old_universe':'ac_fixed/company_holding_universe.parquet',
        'old_returns':'owners/company_return_windows.parquet',
        'old_nav':'owners/company_nav_daily.parquet',
        'old_quarter':'local_owners/quarter_top10_coverage.parquet',
        'old_quarter_changes':'local_owners/quarter_top10_changes.parquet',
        'old_accounts':'owners/national_accounts.parquet',
        'full_holders':'local_owners/full_top_holders.parquet',
        'old_national_changes':'owners/national_changes.parquet',
    }.items(): source(name, rel)
    # Only exact, unique evidence mappings; conflicts remain unchanged and are exported.
    emit('company_mapping_conflicts', "SELECT * FROM aliases WHERE canonical_variants<>1 OR observed_name IN (SELECT observed_name FROM aliases GROUP BY 1 HAVING count(DISTINCT canonical_company)<>1)")
    emit('company_aliases', "SELECT observed_name,min(canonical_company) canonical_company FROM aliases WHERE canonical_variants=1 GROUP BY 1 HAVING count(DISTINCT canonical_company)=1")
    c.execute("CREATE MACRO canon(n) AS coalesce((SELECT canonical_company FROM company_aliases WHERE observed_name=n),n)")
    emit('company_mapping_audit', "SELECT r.company_id observed_name,canon(r.company_id) canonical_company,CASE WHEN r.company_id IN(SELECT observed_name FROM company_aliases) THEN 'verified_exact_evidence' ELSE 'unmapped_original_name_preserved' END mapping_status,count(*) periods,sum(visible_market_value) historical_visible_value FROM old_rank r GROUP BY 1,2,3")
    # Aggregate the already A/C-corrected rows: do not reintroduce raw share-class amounts.
    emit('company_stock_holdings', """WITH a AS (
      SELECT canon(company_id) company_id,report_period,symbol,sum(shares) shares,sum(market_value) market_value,
      sum(fund_count)::BIGINT fund_count,sum(value_count)::BIGINT value_count,sum(row_count)::BIGINT row_count,
      max(latest_announce) latest_announce,any_value(disclosure_scope) disclosure_scope,any_value(close) "close",
      any_value(price_date) price_date,any_value(factor) factor,any_value(adjusted_close) adjusted_close,
      any_value(industry_code) industry_code,any_value(industry_name) industry_name,any_value(float_shares) float_shares
      FROM old_holdings GROUP BY 1,2,3)
      SELECT *,shares/nullif(float_shares,0) float_holding_ratio,
      market_value/nullif(sum(market_value) OVER(PARTITION BY company_id,report_period),0) weight,
      row_number() OVER(PARTITION BY company_id,report_period ORDER BY market_value DESC NULLS LAST,symbol) holding_rank,
      'exact_unique_evidence_company_mapping;frozen_verified_ac_amounts_preserved' quality_status FROM a""")
    emit('company_period_base', """SELECT canon(company_id) company_id,report_period,
      sum(fund_count)::BIGINT fund_count,sum(comparable_fund_count)::BIGINT comparable_fund_count,
      sum(comparable_fund_count)/nullif(sum(fund_count),0) change_comparable_coverage_ratio,
      bool_and(new_count IS NOT NULL) all_components_qualified,count(*) source_company_count,
      max(latest_announce) latest_announce,min(coverage_ratio) coverage_ratio
      FROM old_rank GROUP BY 1,2""")
    emit('company_changes', """WITH a AS (
      SELECT canon(entity_id) entity_id,report_period,min(prev_period) prev_period,symbol,
      bool_or(current_present) current_present,bool_or(previous_present) previous_present,
      sum(current_shares) current_shares,sum(previous_shares) previous_shares,
      sum(current_value) current_value,sum(previous_value) previous_value,
      sum(previous_adjusted_shares) previous_adjusted_shares,any_value(close) "close",
      sum(unknown_split_events) unknown_split_events,
      bool_and(change_status IN ('new_visible','exit_visible','increase','decrease','unchanged')) input_valid
      FROM old_changes GROUP BY 1,2,4),b AS (
      SELECT a.*,p.fund_count current_observed_funds,p.comparable_fund_count,
      p.change_comparable_coverage_ratio comparable_coverage_ratio,
      (coalesce(current_shares,0)-coalesce(previous_adjusted_shares,0))/nullif(previous_adjusted_shares,0) share_change_ratio,
      CASE WHEN NOT p.all_components_qualified OR p.change_comparable_coverage_ratio<.8 THEN 'unknown_company_cohort_coverage_below_80pct'
           WHEN NOT input_valid THEN 'unknown_input_disclosure_or_split'
           WHEN NOT previous_present THEN 'new_visible' WHEN NOT current_present THEN 'exit_visible'
           WHEN current_shares>previous_adjusted_shares*1.02 THEN 'increase'
           WHEN current_shares<previous_adjusted_shares*.98 THEN 'decrease' ELSE 'unchanged' END change_status
      FROM a JOIN company_period_base p ON p.company_id=a.entity_id AND p.report_period=a.report_period)
      SELECT *,CASE WHEN change_status IN ('new_visible','exit_visible','increase','decrease','unchanged') THEN
      (coalesce(current_shares,0)-coalesce(previous_adjusted_shares,0))*close END estimated_change_value,
      'same_frozen_fund_cohort;exact_company_merge;unknown_disclosure_preserved' comparison_status FROM b""")
    emit('company_rank_complete', """WITH h AS (SELECT company_id,report_period,sum(market_value) visible_market_value,
      count(*) stock_count,sum(weight*weight) hhi,sum(weight) FILTER(WHERE holding_rank<=10) top10_weight
      FROM company_stock_holdings GROUP BY 1,2),ch AS (
      SELECT entity_id company_id,report_period,
      count(*) FILTER(WHERE change_status='new_visible') new_count,count(*) FILTER(WHERE change_status='increase') increase_count,
      count(*) FILTER(WHERE change_status='decrease') decrease_count,count(*) FILTER(WHERE change_status='exit_visible') exit_count,
      sum(estimated_change_value) estimated_net_change,sum(abs(estimated_change_value)) gross_estimated_change
      FROM company_changes GROUP BY 1,2),r AS (
      SELECT canon(company_id) company_id,report_period,first(x ORDER BY visible_market_value DESC) prior_row
      FROM old_rank x GROUP BY 1,2)
      SELECT h.*,p.* EXCLUDE(company_id,report_period),
      CASE WHEN p.all_components_qualified THEN ch.new_count END new_count,
      CASE WHEN p.all_components_qualified THEN ch.increase_count END increase_count,
      CASE WHEN p.all_components_qualified THEN ch.decrease_count END decrease_count,
      CASE WHEN p.all_components_qualified THEN ch.exit_count END exit_count,
      CASE WHEN p.all_components_qualified THEN ch.estimated_net_change END estimated_net_change,
      CASE WHEN p.all_components_qualified THEN ch.gross_estimated_change END gross_estimated_change,
      CASE WHEN visible_market_value>=500e9 THEN 'top' WHEN visible_market_value>=100e9 THEN 'large'
           WHEN visible_market_value>=10e9 THEN 'medium' ELSE 'small' END size_band,
      visible_market_value-lag(visible_market_value) OVER(PARTITION BY h.company_id ORDER BY h.report_period) period_value_change,
      CASE WHEN source_company_count=1 THEN prior_row.net_assets END net_assets,
      CASE WHEN source_company_count=1 THEN prior_row.stock_allocation END stock_allocation,
      CASE WHEN source_company_count=1 THEN prior_row.holding_nav_ratio END holding_nav_ratio,
      CASE WHEN source_company_count=1 THEN prior_row.net_asset_coverage END net_asset_coverage,
      CASE WHEN source_company_count=1 THEN prior_row.mean_visible_halfyears END mean_visible_halfyears,
      CASE WHEN source_company_count=1 THEN prior_row.return_1y END return_1y,
      CASE WHEN source_company_count=1 THEN prior_row.return_2y END return_2y,
      CASE WHEN source_company_count=1 THEN prior_row.weighted_return_1y END weighted_return_1y,
      CASE WHEN source_company_count=1 THEN prior_row.excess_1y END excess_1y,
      CASE WHEN source_company_count=1 THEN prior_row.implied_return END implied_return,
      CASE WHEN source_company_count=1 THEN prior_row.stock_turnover END stock_turnover,
      CASE WHEN source_company_count=1 THEN prior_row.estimated_amount_turnover END estimated_amount_turnover,
      CASE WHEN source_company_count=1 THEN prior_row.asset_denominator_status ELSE 'merged_company_assets_not_recomputed' END asset_denominator_status,
      'exact_company_merge;AC_preserved;merged_derived_metrics_null' quality_status
      FROM h JOIN company_period_base p USING(company_id,report_period) LEFT JOIN ch USING(company_id,report_period) JOIN r USING(company_id,report_period)""")
    emit('company_period', 'SELECT * FROM company_rank_complete')
    emit('company_industry', """SELECT company_id,report_period,industry_code,max(industry_name) industry_name,
      sum(market_value) market_value,sum(weight) weight,count(*) stock_count,
      'exact_reaggregate_frozen_AC_corrected_holdings' quality_status
      FROM company_stock_holdings GROUP BY 1,2,3""")
    emit('company_overlap', """WITH pairs AS(SELECT a.company_id company_a,b.company_id company_b,a.report_period,
      count(*) shared_stocks,sum(least(a.weight,b.weight)) weight_overlap FROM company_stock_holdings a
      JOIN company_stock_holdings b ON a.report_period=b.report_period AND a.symbol=b.symbol AND a.company_id<b.company_id GROUP BY 1,2,3)
      SELECT a.company_id company_a,b.company_id company_b,a.report_period,
      coalesce(p.shared_stocks,0) shared_stocks,coalesce(p.weight_overlap,0) weight_overlap,
      coalesce(p.shared_stocks,0)::DOUBLE/(a.stock_count+b.stock_count-coalesce(p.shared_stocks,0)) jaccard
      FROM company_period a JOIN company_period b ON a.report_period=b.report_period AND a.company_id<b.company_id
      LEFT JOIN pairs p ON p.company_a=a.company_id AND p.company_b=b.company_id AND p.report_period=a.report_period""")
    # Coverage denominators are separate from snapshot size and never become market totals.
    emit('fund_change_coverage', """SELECT report_period,count(*) company_count,
      count(new_count) qualified_company_count,count(new_count)::DOUBLE/count(*) company_coverage_ratio,
      sum(fund_count) share_class_count,sum(comparable_fund_count) comparable_fund_count,
      sum(fund_count) FILTER(WHERE new_count IS NOT NULL) qualified_company_fund_count,
      sum(comparable_fund_count) FILTER(WHERE new_count IS NOT NULL) qualified_comparable_fund_count,
      sum(fund_count) FILTER(WHERE new_count IS NOT NULL)/nullif(sum(fund_count),0) fund_coverage_ratio,
      sum(visible_market_value) market_value,sum(visible_market_value) FILTER(WHERE new_count IS NOT NULL) qualified_company_market_value,
      sum(visible_market_value) FILTER(WHERE new_count IS NOT NULL)/nullif(sum(visible_market_value),0) amount_coverage_ratio,
      sum(new_count) sample_new_count,sum(increase_count) sample_increase_count,sum(decrease_count) sample_decrease_count,
      sum(exit_count) sample_exit_count,sum(estimated_net_change) sample_estimated_change_value,
      count(new_count)=count(*) full_market_changes_available,
      'sample_amount_coverage_uses_qualified_company_visible_holdings_not_comparable_cohort_notional' coverage_basis
      FROM company_period GROUP BY 1""")
    emit('fund_market_history', """SELECT v.*,s.stock_count,
      CASE WHEN full_market_changes_available THEN sample_new_count END new_count,
      CASE WHEN full_market_changes_available THEN sample_increase_count END increase_count,
      CASE WHEN full_market_changes_available THEN sample_decrease_count END decrease_count,
      CASE WHEN full_market_changes_available THEN sample_exit_count END exit_count,
      CASE WHEN full_market_changes_available THEN sample_estimated_change_value END estimated_change_value
      FROM fund_change_coverage v JOIN (SELECT report_period,count(DISTINCT symbol) stock_count FROM company_stock_holdings GROUP BY 1) s USING(report_period)""")
    emit('fund_band_complete', """SELECT report_period,size_band,count(*) company_count,sum(visible_market_value) market_value,sum(fund_count) fund_count,
      CASE WHEN count(new_count)=count(*) THEN sum(estimated_net_change) END estimated_change_value,
      CASE WHEN count(new_count)=count(*) THEN sum(new_count) END new_count,
      CASE WHEN count(new_count)=count(*) THEN sum(increase_count) END increase_count,
      CASE WHEN count(new_count)=count(*) THEN sum(decrease_count) END decrease_count,
      CASE WHEN count(new_count)=count(*) THEN sum(exit_count) END exit_count,
      count(new_count) qualified_company_count,count(new_count)::DOUBLE/count(*) company_coverage_ratio,
      sum(fund_count) FILTER(WHERE new_count IS NOT NULL)/nullif(sum(fund_count),0) fund_coverage_ratio,
      sum(visible_market_value) FILTER(WHERE new_count IS NOT NULL)/nullif(sum(visible_market_value),0) amount_coverage_ratio,
      sum(estimated_net_change) sample_estimated_change_value
      FROM company_period GROUP BY 1,2""")
    emit('fund_industry_complete', """WITH a AS(SELECT report_period,industry_code,max(industry_name) industry_name,
      sum(market_value) market_value,count(DISTINCT company_id) company_count,count(*) company_stock_observations
      FROM company_stock_holdings GROUP BY 1,2),b AS(SELECT *,market_value/nullif(sum(market_value) OVER(PARTITION BY report_period),0) weight FROM a)
      SELECT b.*,o.market_weight,b.weight-o.market_weight overweight,
      b.weight-lag(b.weight) OVER(PARTITION BY b.industry_code ORDER BY b.report_period) weight_change,
      NULL::DOUBLE estimated_net_change,'partial_change_coverage_not_market_total' quality_status
      FROM b LEFT JOIN old_industry o USING(report_period,industry_code)""")
    # Current holdings and original unknown/exit rows are joined on canonical business keys.
    emit('company_holding_universe', """WITH extras AS(SELECT canon(company_id) company_id,report_period,symbol,
      max(name) "name",CASE WHEN count(DISTINCT company_id)=1 THEN max(consecutive_halfyears) END consecutive_halfyears,
      max(post_period_return) post_period_return FROM old_universe GROUP BY 1,2,3),base AS(
      SELECT coalesce(h.company_id,ch.entity_id) company_id,coalesce(h.report_period,ch.report_period) report_period,
      coalesce(h.symbol,ch.symbol) symbol,h.* EXCLUDE(company_id,report_period,symbol),ch.change_status,ch.share_change_ratio,
      ch.estimated_change_value FROM company_stock_holdings h FULL OUTER JOIN company_changes ch
      ON h.company_id=ch.entity_id AND h.report_period=ch.report_period AND h.symbol=ch.symbol)
      SELECT b.*,e.name,e.consecutive_halfyears,e.post_period_return,
      weight-lag(weight) OVER(PARTITION BY b.company_id,b.symbol ORDER BY b.report_period) influence_pp
      FROM base b LEFT JOIN extras e USING(company_id,report_period,symbol)""")
    # Static breadth changes with company merging; scarce directions remain explicitly sample-only.
    emit('fund_heat_complete', """WITH s AS(SELECT report_period,symbol,count(DISTINCT company_id) company_count FROM company_stock_holdings GROUP BY 1,2),a AS(
      SELECT o.* REPLACE(s.company_count AS company_count),v.company_coverage_ratio change_company_coverage_ratio,
      v.fund_coverage_ratio change_fund_coverage_ratio,v.amount_coverage_ratio change_amount_coverage_ratio
      FROM old_heat o JOIN s USING(report_period,symbol) JOIN fund_change_coverage v USING(report_period))
      SELECT * REPLACE((rank() OVER(PARTITION BY report_period ORDER BY company_count NULLS LAST)-1+(count(*) OVER(PARTITION BY report_period,company_count)-1)/2.0)/nullif(count(*) OVER(PARTITION BY report_period)-1,0) AS company_percentile,NULL::BIGINT AS buyers,NULL::BIGINT AS sellers,NULL::BIGINT AS new_count,
      NULL::BIGINT AS increase_count,NULL::BIGINT AS decrease_count,NULL::BIGINT AS exit_count,
      NULL::BIGINT AS top10_entrants,NULL::BIGINT AS rank_risers,NULL::DOUBLE AS composite_score_proxy,
      (100.0*(rank() OVER(PARTITION BY report_period ORDER BY company_count NULLS LAST)-1+(count(*) OVER(PARTITION BY report_period,company_count)-1)/2.0)/nullif(count(*) OVER(PARTITION BY report_period)-1,0)+100*float_percentile)/2 AS crowding_score,NULL::VARCHAR AS consensus,
      'static_crowding_reranked;composite_unknown_partial_change_coverage' AS score_status,
      'static_company_count_reaggregated;sample_directions_not_market_signals' AS quality_status) FROM a""")
    # Existing NAV curves cannot be pooled from aggregates; suppress merged-company curves.
    for name, old in [('company_return_windows','old_returns'),('company_nav_daily','old_nav')]:
        emit(name, f"SELECT o.* REPLACE(canon(o.company_id) AS company_id) FROM {old} o WHERE o.company_id NOT IN (SELECT observed_name FROM company_mapping_audit WHERE canonical_company IN(SELECT canonical_company FROM company_mapping_audit GROUP BY 1 HAVING count(*)>1))")
    # Quarterly ratios on merged identities would have an invalid denominator; keep only untouched identities.
    for name, old in [('quarter_top10_coverage','old_quarter'),('quarter_top10_changes','old_quarter_changes')]:
        emit(name, f"SELECT o.* REPLACE(canon(o.company_id) AS company_id) FROM {old} o WHERE o.company_id NOT IN (SELECT observed_name FROM company_mapping_audit WHERE canonical_company IN(SELECT canonical_company FROM company_mapping_audit GROUP BY 1 HAVING count(*)>1))")
    emit('national_alias_evidence', "SELECT holder_name,min(entity_id) entity_id,min(relationship_type) relationship_type FROM old_accounts GROUP BY 1 HAVING count(DISTINCT entity_id)=1")
    emit('national_account_candidates', """SELECT h.*,a.entity_id FROM full_holders h JOIN national_alias_evidence a USING(holder_name)
      WHERE h.announce_date<=DATE '2026-11-09' AND NOT EXISTS(SELECT 1 FROM old_accounts o
      WHERE o.symbol=h.symbol AND o.record_date=h.record_date AND o.holder_name=h.holder_name)""")
    emit('national_coverage', """SELECT entity_id,record_date report_period,count(DISTINCT holder_name) verified_holder_names,
      count(DISTINCT symbol) visible_stock_count,count(*) account_stock_rows,
      'verified_exact_alias_visible_top10_float_holders;not_complete_entity_universe' coverage_basis
      FROM old_accounts GROUP BY 1,2""")
    # Evidence has no same-Q3 additions. No inference from target stock counts is permitted.
    emit('national_changes', 'SELECT * FROM old_national_changes')
    flow_specs=[('stock_flow_complete','symbol','main_net_inflow',True),('sw_flow_complete','industry_code','net_flow',True),('sector_flow_complete','provider,board_type,sector_id','net_flow',False),('etf_trading_complete','symbol','net_subscription',False)]
    for name, partition, value, has_turnover in flow_specs:
        source('old_'+name, 'local_owners/'+name+'.parquet')
        if name=='sw_flow_complete':
            c.execute("CREATE VIEW sw_calendar_input AS SELECT s.*,cal.day_id FROM old_sw_flow_complete s JOIN (SELECT DISTINCT trade_date,day_id FROM old_stock_flow_complete) cal USING(trade_date)")
        extras=[f'CASE WHEN count({value}) OVER w=100 THEN sum({value}) OVER w END flow_100d']
        if has_turnover:extras.append('CASE WHEN count(turnover) OVER w=100 THEN sum(turnover) OVER w END turnover_100d')
        ret='daily_return' if name=='stock_flow_complete' else 'return_1d' if name!='etf_trading_complete' else None
        if ret:extras.append(f'CASE WHEN count({ret}) OVER w=100 AND min({ret}) OVER w> -1 THEN exp(sum(ln(1+{ret})) OVER w)-1 END return_100d')
        extras.extend([f'count({value}) OVER w flow_100d_valid_days','100 flow_100d_expected_days'])
        inp='sw_calendar_input' if name=='sw_flow_complete' else 'old_'+name
        select='* EXCLUDE(day_id)' if name=='sw_flow_complete' else '*'
        emit(name, f"SELECT {select},{','.join(extras)} FROM {inp} WINDOW w AS(PARTITION BY {partition} ORDER BY day_id RANGE BETWEEN 99 PRECEDING AND CURRENT ROW)")
    source('old_market_flow','local_owners/market_stock_flow_windows.parquet')
    cols=['main_net','super_large_net','large_net','medium_net','small_net']
    emit('market_stock_flow_windows', 'SELECT *,'+','.join(f'CASE WHEN count({k}) OVER w=100 THEN sum({k}) OVER w END {k}_100d' for k in cols)+' FROM old_market_flow WINDOW w AS(ORDER BY trade_date ROWS BETWEEN 99 PRECEDING AND CURRENT ROW)')
    metadata_path=base/'local_owners/metadata.json'
    manifest.append({'path':'local_owners/metadata.json','sha256':hashlib.sha256(metadata_path.read_bytes()).hexdigest(),'bytes':metadata_path.stat().st_size})
    metadata=json.loads(metadata_path.read_text())
    metadata['company_ids']=[x[0] for x in c.execute('SELECT DISTINCT company_id FROM company_period ORDER BY 1').fetchall()]
    (out/'metadata.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2))
    checks={
      'amount_conservation':rows("SELECT a.report_period,a.v before_value,b.v after_value,b.v-a.v residual FROM (SELECT report_period,sum(market_value) v FROM old_holdings GROUP BY 1)a JOIN (SELECT report_period,sum(market_value) v FROM company_stock_holdings GROUP BY 1)b USING(report_period) ORDER BY 1"),
      'q2_coverage':rows("SELECT * FROM fund_market_history WHERE report_period=DATE '2026-06-30'"),
      'mapping_conflicts':rows('SELECT * FROM company_mapping_conflicts'),
      'quarter_national_candidates':rows("SELECT record_date,count(*) n FROM national_account_candidates WHERE record_date=last_day(record_date) AND month(record_date) IN(3,6,9,12) GROUP BY 1"),
      'q3_national_candidates':rows("SELECT entity_id,count(*) n FROM national_account_candidates WHERE record_date=DATE '2026-09-30' GROUP BY 1"),
      'national_unknowns_preserved':rows("SELECT change_status,count(*) n FROM national_changes WHERE change_status LIKE 'unknown%' GROUP BY 1"),
    }
    (out/'validation.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2,default=str))
    (out/'input-manifest.json').write_text(json.dumps({'baseline':BASELINE,'cutoff':'2026-11-09','inputs':manifest},ensure_ascii=False,indent=2))
    handoff={'baseline':BASELINE,'namespace':'revision/owners','artifacts':sorted(p.name for p in out.glob('*.parquet')),
      'gaps':['社保组合仅10个冻结已核实名；Q3完整股东快照与精确别名连接无新增，无法证明原站586只账户集合。','公司归并后收益、资产分母、季度门槛缺少逐基金重算输入，合并公司对应指标/曲线保留空或移除。','公司变动金额覆盖为合格公司静态可见市值覆盖，并非可比基金逐股金额覆盖。','公司合并后拥挤度按公司数重排；依赖买卖样本的综合分停用。','龙虎榜2条多原因金额冲突继续为空；不同来源与币种不可叠加。'],
      'mapping_conflicts':checks['mapping_conflicts'],'q3_national_candidates':checks['q3_national_candidates']}
    (out/'handoff.json').write_text(json.dumps(handoff,ensure_ascii=False,indent=2,default=str))
    c.close()
    return checks

if __name__=='__main__':
    result=build()
    print(json.dumps({'output':str(OUT),'q2_coverage':result['q2_coverage']},ensure_ascii=False,default=str,indent=2))
