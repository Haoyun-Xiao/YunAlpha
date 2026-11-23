"""Continue disclosure-based ownership series without substituting price-only returns."""
from datetime import date, timedelta

from seven_desks.builders.owners_daily_capital import net, windows


def etfs_and_signals(b):
    f = b.cutoff
    b.prior('fetf','revision/owners/etf_trading_complete')
    b.prior('fetf_class','owners/revision-v3-supplement/etf_classification')
    b.prior('fpool','owners/national_etf_pool_daily')
    b.prior('fevents','owners/national_etf_events')
    b.prior('fcategory','owners/etf_category_flow')
    b.source('etf_daily_metrics')
    b.table('oetf', f"""WITH raw AS(
      SELECT e.symbol,e.trade_date,e.shares_outstanding,e.fund_size,e.unit_nav,e.close,
        p.name,p.category,p.reported_index_code,p.classification_status,
        lag(e.shares_outstanding) OVER w previous_shares,lag(e.unit_nav) OVER w previous_nav,
        lag(e.trade_date) OVER w previous_observed_date,c.prev_date,c.day_id,c.next_date available_date
      FROM (
        SELECT * FROM src_etf_daily_metrics
        QUALIFY row_number() OVER(PARTITION BY symbol,trade_date ORDER BY fetched_at DESC,source,data_version)=1
      ) e LEFT JOIN fetf_class p USING(symbol) JOIN oc c USING(trade_date)
      WHERE (p.list_date IS NULL OR e.trade_date>=p.list_date)
      WINDOW w AS(PARTITION BY e.symbol ORDER BY e.trade_date)
    ), fresh AS (
      SELECT *,
        CASE WHEN previous_observed_date=prev_date THEN shares_outstanding-previous_shares END shares_change,
        CASE WHEN unit_nav>0 THEN unit_nav WHEN previous_observed_date=prev_date AND previous_nav>0 THEN previous_nav END nav_used,
        CASE WHEN unit_nav>0 THEN trade_date WHEN previous_observed_date=prev_date AND previous_nav>0 THEN prev_date END nav_source_date,
        CASE WHEN previous_observed_date IS NULL THEN 'no_previous_observation'
          WHEN previous_observed_date<>prev_date THEN 'gap_no_flow' WHEN unit_nav>0 THEN 'estimated_current_nav'
          WHEN previous_nav>0 THEN 'estimated_previous_trading_nav' ELSE 'missing_nav' END||
          ';explicit_equity_evidence;unknown_excluded;current_classification_not_pit' quality_status
      FROM raw WHERE trade_date>DATE {f}
    ), all_rows AS (
      SELECT f.* EXCLUDE(flow_5d,flow_20d,flow_60d,flow_100d,flow_120d,flow_250d,flow_100d_valid_days,flow_100d_expected_days,
        previous_20d_average_turnover,turnover_history_samples,volume_ratio,consecutive_days)
      FROM fetf f
      WHERE trade_date IN(SELECT trade_date FROM oc WHERE trade_date<=DATE {f} ORDER BY trade_date DESC LIMIT 250)
      UNION ALL BY NAME SELECT fresh.*,p.amount turnover,
        CASE WHEN available_date IS NOT NULL THEN shares_change*nav_used END net_subscription
      FROM fresh LEFT JOIN obars p USING(symbol,trade_date)
    ), prev AS (
      SELECT *,avg(turnover) OVER vol previous_20d_average_turnover,count(turnover) OVER vol turnover_history_samples,
        CASE WHEN sign(net_subscription)=lag(sign(net_subscription)) OVER(PARTITION BY symbol ORDER BY day_id) THEN 0 ELSE 1 END sign_change
      FROM all_rows WINDOW vol AS(PARTITION BY symbol ORDER BY day_id RANGE BETWEEN 20 PRECEDING AND 1 PRECEDING)
    ), runs AS (
      SELECT *,sum(sign_change) OVER(PARTITION BY symbol ORDER BY day_id) island FROM prev
    ), r AS (
      SELECT * EXCLUDE(sign_change,island),{','.join(net('net_subscription','flow',n) for n in (5,20,60,100,120,250))},
        count(net_subscription) OVER w100 flow_100d_valid_days,100 flow_100d_expected_days,
        CASE WHEN turnover_history_samples=20 THEN turnover/nullif(previous_20d_average_turnover,0) END volume_ratio,
        sign(net_subscription)*row_number() OVER(PARTITION BY symbol,island ORDER BY day_id) consecutive_days
      FROM runs WINDOW {windows('symbol',(5,20,60,100,120,250))}
    )
    SELECT * FROM r WHERE trade_date>DATE {f}""")
    etf = b.emit('etf_daily_flow', 'SELECT * FROM oetf WHERE available_date IS NOT NULL', ('trade_date','symbol'))
    for board in ('capital_flow','national_team'):
        b.status(board,'etf',etf or '2026-11-09','申赎按相邻交易日份额变化×当日NAV，原口径允许前一交易日NAV；T+1才可见。')
    b.emit('etf_category_flow', f"""WITH fresh AS (
      SELECT trade_date,coalesce(category,'unclassified') category,sum(net_subscription) net_subscription,
        sum(fund_size) fund_size,count(*) etf_count,count(net_subscription) valid_flow_count,
        count(net_subscription)*1.0/count(*) coverage_ratio,max(available_date) available_date
      FROM oetf WHERE available_date IS NOT NULL GROUP BY 1,2
    ), a AS (
      SELECT category,trade_date,net_subscription,fund_size,etf_count,valid_flow_count,coverage_ratio,NULL::DATE available_date
      FROM fcategory UNION ALL BY NAME SELECT * FROM fresh
    ), r AS (
      SELECT *,sum(net_subscription) OVER(PARTITION BY category ORDER BY trade_date) cumulative_flow,
        'estimated_flow;current_classification_not_pit;crosssection_unverified' quality_status FROM a
    ) SELECT * FROM r WHERE trade_date>DATE {f}""",('trade_date','category'))
    b.emit('etf_index_flow', """SELECT trade_date,reported_index_code,sum(net_subscription) net_subscription,
       sum(fund_size) fund_size,count(*) etf_count,count(net_subscription)*1.0/count(*) coverage_ratio,
       max(available_date) available_date,'estimated_flow;current_classification_not_pit;crosssection_unverified' quality_status
       FROM oetf WHERE category='broad' AND reported_index_code IS NOT NULL AND available_date IS NOT NULL GROUP BY 1,2""",
       ('trade_date','reported_index_code'))

    b.emit('local_etf_period_asof', f"""WITH a AS (
      SELECT symbol,trade_date,name,category,net_subscription,NULL::DATE available_date FROM fetf
      WHERE trade_date>=date_trunc('quarter',DATE {f})
      UNION ALL SELECT symbol,trade_date,name,category,net_subscription,available_date FROM oetf
      WHERE available_date IS NOT NULL
    ), w AS (
      SELECT *,cast(date_trunc('week',trade_date) AS DATE) week_start,
        cast(date_trunc('quarter',trade_date) AS DATE) quarter_start FROM a
    ), r AS (
      SELECT * EXCLUDE(net_subscription),sum(net_subscription) OVER ww week_net_subscription,
        sum(net_subscription) OVER wq quarter_net_subscription,count(net_subscription) OVER ww week_valid_days,
        count(*) OVER ww week_observed_days,count(net_subscription) OVER wq quarter_valid_days,
        count(*) OVER wq quarter_observed_days
      FROM w WINDOW ww AS(PARTITION BY symbol,week_start ORDER BY trade_date),
        wq AS(PARTITION BY symbol,quarter_start ORDER BY trade_date)
    ) SELECT * FROM r WHERE trade_date>DATE {f}""",('trade_date','symbol'))

    # Historical turnover is the same published, classified pool. Refresh
    # the freeze day's next-session flow when its T+1 publication arrives.
    b.table('opool', f"""WITH today AS (
      SELECT e.trade_date,sum(net_subscription) net_subscription,sum(turnover) turnover,count(*) etf_count,
        count(net_subscription)*1.0/count(*) flow_coverage,max(available_date) net_flow_available_date
      FROM oetf e WHERE category='broad' GROUP BY 1
    ), a AS (
      SELECT p.trade_date,net_subscription,turnover,etf_count,flow_coverage,c.next_date net_flow_available_date
      FROM fpool p JOIN oc c USING(trade_date)
      UNION ALL BY NAME SELECT * FROM today
    ), g AS (
      SELECT a.*,c.day_id,c.next_date,avg(a.turnover) OVER w previous_20d_avg_turnover,
        count(a.turnover) OVER w previous_20d_count
      FROM a JOIN oc c USING(trade_date) WINDOW w AS(ORDER BY day_id RANGE BETWEEN 20 PRECEDING AND 1 PRECEDING)
    )
    SELECT g.*,n.net_subscription next_trading_net_subscription,n.net_flow_available_date next_flow_available_date,
      i.daily_return benchmark_return,i.intraday_recovery,i.return_20d,i.intraday_drop,
      CASE WHEN previous_20d_count=20 THEN g.turnover/nullif(previous_20d_avg_turnover,0) END volume_ratio,
      'current_broad_pool_approx;explicit_equity_evidence;crosssection_unverified' quality_status
    FROM g LEFT JOIN a n ON n.trade_date=g.next_date LEFT JOIN obenchmark i ON i.trade_date=g.trade_date
    WHERE g.trade_date>=DATE {f}""")
    pool = b.emit('national_etf_pool_daily','SELECT * FROM opool')
    b.emit('national_etf_events', """SELECT *,
      CASE WHEN (benchmark_return<=-.01 OR intraday_drop<=-.015)
        THEN volume_ratio>=2.5 OR next_trading_net_subscription>=threshold_yi*1e8 ELSE false END support_event,
      CASE WHEN return_20d>=.06 THEN net_subscription<=-threshold_yi*1e8*.6 ELSE false END balance_event,
      CASE WHEN NOT (benchmark_return<=-.01 OR intraday_drop<=-.015) OR volume_ratio>=2.5 THEN trade_date
        ELSE next_flow_available_date END support_available_date,net_flow_available_date balance_available_date
      FROM opool CROSS JOIN (VALUES (100),(200),(300)) t(threshold_yi)""",('trade_date','threshold_yi'))
    # Match the local published 252-observation *midrank* calculation, with
    # nonnegative pressure and >=120 usable values for each input separately.
    temperature = b.emit('local_temperature_proxy', f"""WITH a AS (
      SELECT trade_date,volume_ratio,
        CASE WHEN benchmark_return IS NOT NULL THEN greatest(0,-benchmark_return) END pressure,intraday_recovery
      FROM fpool WHERE trade_date<DATE {f}
      UNION ALL SELECT trade_date,volume_ratio,CASE WHEN benchmark_return IS NOT NULL
        THEN greatest(0,-benchmark_return) END pressure,intraday_recovery FROM opool
    ), g AS (SELECT *,row_number() OVER(ORDER BY trade_date) seq FROM a), r AS (
      SELECT x.trade_date,
        {','.join(f'''count(h.{col}) {key}_samples,
           CASE WHEN count(h.{col})>=120 AND x.{col} IS NOT NULL THEN
             100.0*(count(*) FILTER(WHERE h.{col}<x.{col})+.5*count(*) FILTER(WHERE h.{col}=x.{col}))/count(h.{col})
           END {key}_percentile''' for key,col in (('volume','volume_ratio'),('pressure','pressure'),('recovery','intraday_recovery')))}
      FROM g x JOIN g h ON h.seq BETWEEN x.seq-251 AND x.seq WHERE x.trade_date>=DATE {f}
      GROUP BY x.trade_date,x.volume_ratio,x.pressure,x.intraday_recovery
    ) SELECT *,(volume_percentile+pressure_percentile+recovery_percentile)/3 temperature_proxy,
        '252交易观测窗；三项滚动中位秩等权、各至少120有效样本；本地代理' formula FROM r""")
    b.status('national_team','support_signals',pool,
             '原规则按沪深300和宽基ETF复算；事件只在规则所需数据已披露后可见。')
    temperature = b.db.execute("SELECT max(trade_date) FROM read_parquet(?) WHERE temperature_proxy IS NOT NULL",
                         [str(b.x.staging/'revision/owners_daily/local_temperature_proxy.parquet')]).fetchone()[0] if temperature else None
    b.status('national_team','support_temperature',temperature,
             '沿用本地原252观测窗放量/跌幅压力/日内修复的等权中位秩公式。')


def shadow(b):
    f = b.cutoff
    b.prior('fweights','owners/national_shadow_rebalance_weights')
    b.prior('fcomponents','owners/national_shadow_component_daily')
    b.prior('fshadow','owners/national_shadow_nav')
    b.table('ocomponents', f"""WITH latest AS (
      SELECT * FROM fweights WHERE weight IS NOT NULL AND rebalance_date<=DATE {f}
      QUALIFY rebalance_date=max(rebalance_date) OVER(PARTITION BY portfolio)
    ), marks AS (
      SELECT symbol,trade_date,adjusted_close FROM obars WHERE trade_date>DATE {f} AND adjusted_close>0
      UNION ALL
      SELECT DISTINCT symbol,valuation_price_date,adjusted_close FROM fcomponents
      WHERE trade_date=DATE {f} AND adjusted_close>0
    ), grid AS (
      SELECT w.*,c.trade_date,NULL::DATE next_rebalance FROM latest w CROSS JOIN oc c
      WHERE c.trade_date>=DATE {f} AND c.trade_date<=(SELECT max(trade_date) FROM obenchmark)
    ), p AS (
      SELECT g.*,b.adjusted_close,b.trade_date valuation_price_date,
        g.weight*b.adjusted_close/g.rebalance_adjusted_close segment_value
      FROM grid g ASOF LEFT JOIN marks b ON g.symbol=b.symbol AND g.trade_date>=b.trade_date
    ), r AS (
      SELECT *,segment_value-lag(segment_value) OVER(PARTITION BY portfolio,rebalance_date,symbol ORDER BY trade_date) segment_value_change
      FROM p
    )
    SELECT * FROM r WHERE trade_date>DATE {f}""")
    b.table('oshadow', f"""WITH s AS (
      SELECT portfolio,rebalance_date,trade_date,sum(segment_value ORDER BY symbol) segment_value,
        sum(weight ORDER BY symbol) priced_weight,count(*) component_count,
        sum(weight ORDER BY symbol) FILTER(WHERE valuation_price_date=trade_date) fresh_price_weight
      FROM ocomponents GROUP BY 1,2,3
    ), a AS (
      SELECT * FROM s UNION ALL SELECT portfolio,rebalance_date,trade_date,segment_value,priced_weight,component_count,
        fresh_price_weight FROM fshadow WHERE trade_date=DATE {f}
    ), r AS (
      SELECT a.*,segment_value/lag(segment_value) OVER(PARTITION BY portfolio,rebalance_date ORDER BY trade_date)-1 daily_return
      FROM a
    ), prior AS (
      SELECT portfolio,arg_max(nav,trade_date) anchor_nav,max(nav) past_peak FROM fshadow WHERE trade_date<=DATE {f} GROUP BY 1
    ), x AS (
      SELECT r.*,p.past_peak,p.anchor_nav*exp(sum(ln(1+daily_return)) OVER(PARTITION BY r.portfolio ORDER BY trade_date)) nav
      FROM r JOIN prior p USING(portfolio) WHERE trade_date>DATE {f}
    )
    SELECT x.* EXCLUDE(past_peak),nav/greatest(past_peak,max(nav) OVER(PARTITION BY portfolio ORDER BY trade_date))-1 drawdown,
      i.daily_return benchmark_return,x.daily_return-i.daily_return arithmetic_excess,
      'PIT_deadline;visible_fully_invested_shadow;stale_price_carry' quality_status
    FROM x LEFT JOIN obenchmark i USING(trade_date)""")
    shadow_day = b.emit('national_shadow_nav','SELECT * FROM oshadow',('trade_date','portfolio'))
    # Persist updated components once, then use them for attribution and the
    # panel's date-aware queries without saving a second full component history.
    b.emit('national_shadow_component_daily','SELECT * FROM ocomponents',('trade_date','portfolio','symbol'))
    without_new_prices = [str(row[0]) for row in b.sql('''SELECT trade_date FROM oshadow
        GROUP BY 1 HAVING count(fresh_price_weight)=0 ORDER BY 1''').fetchall()]
    b.status('national_team','shadow_portfolio',shadow_day or '2026-11-09',
             '沿用已验证披露截止/11月2日调仓权重，逐股同口径后复权及末价结转；无新估值日单列。',
             days_without_fresh_prices=without_new_prices)
    b.status('national_team','holdings','2026-09-30','按报告期展示经核对账户和可见持仓。',status='retained')


def funds(b):
    f = b.cutoff
    b.prior('funiverse','owners/active_fund_universe')
    b.prior('fcanonical','revision/owners/company_aliases')
    b.prior('fnav','revision/owners/company_nav_daily')
    b.prior('fweight_evidence','owners/fund_return_weight_evidence')
    b.prior('fofficial_bench','local_owners/benchmark_daily')
    b.source('fund_nav',start=date(2026,11,6),day='nav_date')
    b.table('onav_observations', f"""SELECT n.symbol,coalesce(a.canonical_company,p.company_id) company_id,n.nav_date,
      announce_date,adjusted_nav,asset_nav
      FROM src_fund_nav n JOIN funiverse p USING(symbol)
      LEFT JOIN fcanonical a ON a.observed_name=p.company_id
      WHERE active_equity AND announce_date<=DATE {b.end}
      QUALIFY row_number() OVER(PARTITION BY n.symbol,nav_date ORDER BY announce_date DESC,
        fetched_at DESC NULLS LAST,source,data_version,adjusted_nav)=1""")
    b.table('onav_weights', f"""WITH sz AS (
      SELECT symbol,asset_nav,greatest(nav_date,announce_date) available_date,nav_date scale_nav_date,
        announce_date scale_announce_date FROM onav_observations WHERE asset_nav>0
      UNION ALL
      SELECT DISTINCT symbol,previous_public_asset_nav,scale_available_date,scale_nav_date,scale_announce_date
      FROM fweight_evidence WHERE previous_public_asset_nav>0
    ), weights AS(
      SELECT * FROM sz QUALIFY row_number() OVER(PARTITION BY symbol,available_date ORDER BY scale_nav_date DESC,asset_nav)=1
    ), r AS (
      SELECT n.symbol,n.company_id,n.nav_date,n.announce_date,n.adjusted_nav,p.adjusted_nav previous_adjusted_nav,c.prev_date,
        CASE WHEN n.adjusted_nav>0 AND p.adjusted_nav>0 THEN n.adjusted_nav/p.adjusted_nav-1 END daily_return
      FROM onav_observations n JOIN oc c ON n.nav_date=c.trade_date
      LEFT JOIN onav_observations p ON p.symbol=n.symbol AND p.nav_date=c.prev_date WHERE n.nav_date>DATE {f}
    )
    SELECT r.*,s.asset_nav previous_public_asset_nav,s.scale_nav_date,s.scale_announce_date,
      s.available_date scale_available_date,
      'adjusted_nav_only;prior_trading_day_public_asset_nav;current_active_equity_universe_approx' quality_status
    FROM r ASOF LEFT JOIN weights s ON r.symbol=s.symbol AND r.prev_date>=s.available_date""")
    b.emit('fund_return_weight_evidence','SELECT * FROM onav_weights',('nav_date','symbol'),'nav_date')
    b.table('ocompany_nav', """WITH a AS (
      SELECT company_id,nav_date trade_date,avg(daily_return ORDER BY symbol) equal_weight_return,
        sum(daily_return*previous_public_asset_nav ORDER BY symbol)/
          nullif(sum(previous_public_asset_nav ORDER BY symbol) FILTER(WHERE daily_return IS NOT NULL),0) scale_weight_return,
        count(daily_return) fund_count,count(daily_return) FILTER(WHERE previous_public_asset_nav>0) weighted_fund_count,
        count(*) observed_fund_count,count(daily_return)*1.0/count(*) coverage_ratio,
        weighted_fund_count*1.0/nullif(fund_count,0) scale_coverage_ratio,max(scale_available_date) latest_weight_information_date
      FROM onav_weights GROUP BY 1,2
    ), prior AS (
      SELECT company_id,arg_max(equal_weight_nav,trade_date) enav,arg_max(scale_weight_nav,trade_date) snav,
        max(equal_weight_nav) epeak,max(scale_weight_nav) speak,max(valid_equal_return_days) edays,max(observed_days) odays
      FROM fnav GROUP BY 1
    ), r AS (
      SELECT a.*,p.epeak,p.speak,
        coalesce(enav,100)*exp(sum(ln(1+equal_weight_return)) OVER w-
          CASE WHEN enav IS NULL THEN first_value(ln(1+equal_weight_return) IGNORE NULLS) OVER w ELSE 0 END) equal_weight_nav,
        coalesce(snav,100)*exp(sum(ln(1+scale_weight_return)) OVER w-
          CASE WHEN snav IS NULL THEN first_value(ln(1+scale_weight_return) IGNORE NULLS) OVER w ELSE 0 END) scale_weight_nav,
        coalesce(edays,0)+count(equal_weight_return) OVER w valid_equal_return_days,
        coalesce(odays,0)+count(*) OVER w observed_days
      FROM a LEFT JOIN prior p USING(company_id) WINDOW w AS(PARTITION BY company_id ORDER BY trade_date)
    )
    SELECT * EXCLUDE(epeak,speak),
      equal_weight_nav/greatest(epeak,max(equal_weight_nav) OVER w)-1 equal_weight_drawdown,
      scale_weight_nav/greatest(speak,max(scale_weight_nav) OVER w)-1 scale_weight_drawdown,
      'current_active_equity_universe_approx;observed_fund_coverage_only;missing_return_days_not_filled' quality_status
    FROM r WINDOW w AS(PARTITION BY company_id ORDER BY trade_date)""")
    nav = b.emit('company_nav_daily','SELECT * FROM ocompany_nav',('trade_date','company_id'))
    b.emit('local_benchmark_daily', f"""SELECT trade_date,close,
      (SELECT arg_max(benchmark_nav,trade_date)/arg_max(close,trade_date) FROM fofficial_bench)*close benchmark_nav
      FROM obenchmark WHERE trade_date>DATE {f}""")
    eq,weighted = b.sql("""SELECT max(trade_date) FILTER(WHERE equal_weight_return IS NOT NULL),
      max(trade_date) FILTER(WHERE scale_weight_return IS NOT NULL) FROM ocompany_nav""").fetchone()
    b.status('fund_holdings','company_nav',eq or '2026-11-09',
             '真实复权NAV、相邻交易日；规模权重只用前一交易日已公开的净资产，不用行情或存续份额估算。',
             weighted_date=str(weighted) if weighted else None, latest_nav_observation=nav)
    b.prior('fdisclosure','owners/fund_holdings_pit')
    full,qtr,last = b.sql("""SELECT max(report_period) FILTER(WHERE month(report_period) IN(6,12)),
      max(report_period) FILTER(WHERE month(report_period) IN(3,9)),max(announce_date) FROM fdisclosure""").fetchone()
    latest = last
    if b.source('fund_holdings',start=last,day='report_period'):
        latest = b.sql(f"""SELECT max(announce_date) FROM src_fund_holdings
           WHERE announce_date<=DATE {b.end}""").fetchone()[0] or last
    reason = '正式湖截至观察日未见新公告；季报前十不替半年报/年报全持仓。'
    if latest>last:
        reason = '正式湖有后续公告；需先核实报告范围及A/C份额归并，保留已验证金额与覆盖。'
    b.status('fund_holdings','holdings',full,reason,status='retained',latest_announcement=str(latest))
    b.status('fund_holdings','quarter_top10',qtr,reason,status='retained',latest_announcement=str(latest))
