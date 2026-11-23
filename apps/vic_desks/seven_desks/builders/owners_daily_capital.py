"""Continue the frozen A-share money-flow calculations from official Parquet."""
from datetime import date

WINDOWS = (5, 20, 60, 100)
FLOW = ('main', 'super_large', 'large', 'medium', 'small')


def windows(keys, numbers=WINDOWS):
    partition = f'PARTITION BY {keys} ' if keys else ''
    return ','.join(f'w{n} AS({partition}ORDER BY day_id RANGE BETWEEN {n-1} PRECEDING AND CURRENT ROW)' for n in numbers)


def net(column, prefix, n):
    return f'CASE WHEN count({column}) OVER w{n}={n} THEN sum({column}) OVER w{n} END {prefix}_{n}d'


def returns(column, n):
    return f'''CASE WHEN count({column}) OVER w{n}={n} AND min({column}) OVER w{n}>-1
      THEN exp(sum(ln(CASE WHEN {column}>-1 THEN 1+{column} END)) OVER w{n})-1 END return_{n}d'''


def flows(b):
    f = b.cutoff
    b.prior('fstock', 'revision/owners/stock_flow_complete')
    b.prior('fmarket', 'revision/owners/market_stock_flow_windows')
    b.prior('fsw', 'revision/owners/sw_flow_complete')
    b.prior('fsector', 'revision/owners/sector_flow_complete')
    b.prior('stock_mapping', 'local_owners/stock_context')
    b.source('fund_flow')
    b.table('ostock', f"""WITH fresh AS (
      SELECT s.*,r.amount turnover,r.daily_return,s.main_net_inflow/nullif(r.amount,0) main_turnover_ratio
      FROM src_fund_flow s JOIN oc c USING(trade_date) LEFT JOIN ostock_returns r USING(symbol,trade_date)
      WHERE s.trade_date>DATE {f} AND s.source='tushare.moneyflow_dc'
      QUALIFY row_number() OVER(PARTITION BY s.symbol,s.trade_date ORDER BY s.fetched_at DESC NULLS LAST,s.data_version)=1
    ), inputs AS (
      SELECT symbol,trade_date,{','.join(k+'_net_inflow' for k in FLOW)},source,data_version,fetched_at,turnover,daily_return,main_turnover_ratio
      FROM fstock WHERE trade_date IN(SELECT trade_date FROM oc WHERE trade_date<=DATE {f} ORDER BY trade_date DESC LIMIT 100)
      UNION ALL BY NAME SELECT * FROM fresh
    ), grid AS (
      SELECT i.*,c.day_id,
        CASE WHEN sign(main_net_inflow)=lag(sign(main_net_inflow)) OVER w THEN 0 ELSE 1 END changed
      FROM inputs i JOIN oc c USING(trade_date) WINDOW w AS(PARTITION BY symbol ORDER BY c.day_id)
    ), g AS (
      SELECT *,sum(changed) OVER(PARTITION BY symbol ORDER BY day_id) island FROM grid
    ), r AS (
      SELECT g.* EXCLUDE(changed,island),
        sign(g.main_net_inflow) * CASE WHEN min(day_id) OVER island_window<=
          (SELECT min(day_id) FROM (SELECT day_id FROM oc WHERE trade_date<=DATE {f} ORDER BY trade_date DESC LIMIT 100))
          AND head.main_net_inflow IS NOT NULL
          THEN coalesce(abs(head.consecutive_days),100)+row_number() OVER island_window-1
          ELSE row_number() OVER island_window END consecutive_days,
        {','.join(net('g.main_net_inflow','flow',n) for n in WINDOWS)},
        {','.join(net('turnover','turnover',n) for n in WINDOWS)},
        {','.join(returns('g.daily_return',n) for n in WINDOWS)},
        list(g.main_net_inflow) OVER(PARTITION BY symbol ORDER BY day_id ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) recent_20_flows,
        count(g.main_net_inflow) OVER w100 flow_100d_valid_days,100 flow_100d_expected_days
      FROM g LEFT JOIN (SELECT symbol,consecutive_days,main_net_inflow FROM fstock
        WHERE trade_date=(SELECT min(trade_date) FROM (SELECT trade_date FROM oc WHERE trade_date<=DATE {f} ORDER BY trade_date DESC LIMIT 100)))
      head USING(symbol)
      WINDOW {windows('symbol')},island_window AS(PARTITION BY symbol,island ORDER BY day_id ROWS UNBOUNDED PRECEDING)
    )
    SELECT * FROM r WHERE trade_date>DATE {f}""")
    stock = b.emit('stock_fund_flow_windows', 'SELECT * FROM ostock', ('trade_date','symbol'))
    bucket = """CASE WHEN main_turnover_ratio<-.1 THEN '<-10%' WHEN main_turnover_ratio<-.05 THEN '-10%至-5%'
       WHEN main_turnover_ratio<0 THEN '-5%至0' WHEN main_turnover_ratio<.05 THEN '0至5%'
       WHEN main_turnover_ratio<.1 THEN '5%至10%' WHEN main_turnover_ratio>=.1 THEN '>=10%' ELSE '缺失' END"""
    b.emit('local_flow_distribution_history', f'SELECT trade_date,{bucket} bucket,count(*) sample_count FROM ostock GROUP BY ALL',
           ('trade_date','bucket'))
    fields = ','.join(k+'_net' for k in FLOW)
    b.table('omarket', f"""WITH new AS (
      SELECT trade_date,{','.join(f'sum({k}_net_inflow) {k}_net' for k in FLOW)},
        sum(turnover) turnover,count(*) sample_count FROM ostock GROUP BY 1
    ), a AS (
      SELECT trade_date,{fields},turnover,sample_count FROM fmarket UNION ALL SELECT * FROM new
    ), r AS (
      SELECT a.*,{','.join(net(k+'_net',k+'_net',n) for k in FLOW for n in WINDOWS)}
      FROM a JOIN oc USING(trade_date) WINDOW {windows('')}
    )
    SELECT * FROM r WHERE trade_date>DATE {f}""")
    market = b.emit('local_market_stock_flow_windows','SELECT * FROM omarket')
    market_rows = b.sql('SELECT sample_count FROM omarket ORDER BY trade_date DESC LIMIT 1').fetchone()
    b.status('capital_flow','main_money',market or '2026-11-09',
             '沿用冻结版原生个股截面汇总；长窗要求连续交易日有源观测，截面覆盖随源变化。',
             sample_count=market_rows[0] if market_rows else None,
             window_dates={f'{n}d':str(b.sql(f'SELECT max(trade_date) FROM omarket WHERE main_net_{n}d IS NOT NULL').fetchone()[0] or '2026-11-09') for n in WINDOWS})
    b.status('capital_flow','stock_main_windows',stock or '2026-11-09',
             '逐股按日历计算5/20/60/100日；缺同口径收盘复权或成交额时相应收益/成交额窗留空。',
             latest_price_window_dates={f'{n}d':str(b.sql(
                 f'SELECT max(trade_date) FROM ostock WHERE return_{n}d IS NOT NULL').fetchone()[0] or '2026-11-09')
                 for n in WINDOWS})

    b.table('osw', f"""WITH n AS (
      SELECT f.trade_date,s.industry_code,max(s.industry_name) industry_name,
        sum(f.main_net_inflow) net_flow,{','.join(f'sum(f.flow_{n}d) flow_{n}d' for n in WINDOWS)},
        sum(f.turnover) turnover,{','.join(f'sum(f.turnover_{n}d) turnover_{n}d' for n in WINDOWS)},
        avg(f.daily_return) return_1d,{','.join(f'avg(f.return_{n}d) return_{n}d' for n in WINDOWS)},
        count(*) company_count FROM ostock f LEFT JOIN stock_mapping s USING(symbol) GROUP BY 1,2
    ), a AS (
      SELECT * EXCLUDE(consecutive_days) FROM fsw UNION ALL BY NAME SELECT * FROM n
    ), g AS (
      SELECT *,sum(changed) OVER(PARTITION BY industry_code ORDER BY trade_date) island FROM (
        SELECT *,CASE WHEN sign(net_flow)=lag(sign(net_flow)) OVER(PARTITION BY industry_code ORDER BY trade_date)
        THEN 0 ELSE 1 END changed FROM a
      )
    ), r AS (
      SELECT * EXCLUDE(changed,island,net_ratio,mean_daily_return,flow_100d,flow_100d_valid_days),
        net_flow/nullif(turnover,0) net_ratio,return_1d mean_daily_return,
        sign(net_flow)*row_number() OVER(PARTITION BY industry_code,island ORDER BY g.trade_date) consecutive_days,
        {net('net_flow','flow',100)},count(net_flow) OVER w100 flow_100d_valid_days
      FROM g JOIN oc USING(trade_date) WINDOW {windows('industry_code',(100,))}
    )
    SELECT * REPLACE(100 AS flow_100d_expected_days) FROM r WHERE trade_date>DATE {f}""")
    sw = b.emit('local_sw_flow', 'SELECT * FROM osw', ('trade_date','industry_code'))
    native = []
    for dataset, code, name, board, flow, ret, count in (
        ('sector_fund_flow','sector_code','sector_name','board_type','main_net_inflow','change_pct','NULL::BIGINT'),
        ('moneyflow_ind_ths','industry_code','industry_name',"'industry'",'net_amount','pct_change','company_count'),
        ('moneyflow_cnt_ths','concept_code','concept_name',"'concept'",'net_amount','pct_change','company_count'),
    ):
        if b.source(dataset):
            native.append(f"""SELECT {code} sector_id,{name} sector_name,{board} board_type,
              '{'eastmoney' if dataset=='sector_fund_flow' else 'ths'}' provider,trade_date,
              {flow} net_flow,{ret} change_pct,{count} company_count FROM src_{dataset} JOIN oc USING(trade_date)
              WHERE trade_date>DATE {f}
              QUALIFY row_number() OVER(PARTITION BY {code},{board},trade_date ORDER BY fetched_at DESC,source)=1""")
    if native:
        b.emit('sector_flow_windows', f"""WITH a AS (
          SELECT sector_id,sector_name,board_type,provider,trade_date,net_flow,change_pct,company_count FROM fsector
          UNION ALL {' UNION ALL '.join(native)}
        ), g AS (
          SELECT a.*,c.day_id,change_pct/100 return_1d,
            CASE WHEN sign(net_flow)=lag(sign(net_flow)) OVER(PARTITION BY provider,board_type,sector_id ORDER BY c.day_id)
              THEN 0 ELSE 1 END changed FROM a JOIN oc c USING(trade_date)
        ), islands AS (
          SELECT *,sum(changed) OVER(PARTITION BY provider,board_type,sector_id ORDER BY day_id) island FROM g
        ), r AS (
          SELECT * EXCLUDE(changed,island),{','.join(net('net_flow','flow',n) for n in WINDOWS)},
            {','.join(returns('return_1d',n) for n in WINDOWS)},
            sign(net_flow)*row_number() OVER(PARTITION BY provider,board_type,sector_id,island ORDER BY day_id) consecutive_days,
            count(net_flow) OVER w100 flow_100d_valid_days,100 flow_100d_expected_days,
            'provider_native_sector_not_SW_constituent_sum' quality_status
          FROM islands WINDOW {windows('provider,board_type,sector_id')}
        )
        SELECT * FROM r WHERE trade_date>DATE {f}""", ('trade_date','provider','board_type','sector_id'))
    provider = ["""SELECT symbol,trade_date,'fund_flow' provider,main_net_inflow net_flow_native,
              main_net_inflow net_flow_cny,'元' source_unit,'CNY' display_unit FROM ostock"""]
    for dataset, field, factor, unit in [('moneyflow','net_mf_amount',10000,'万元'),('stock_moneyflow_ths','net_amount',1,'元')]:
        if b.source(dataset):
            provider.append(f"""SELECT symbol,trade_date,'{dataset}' provider,{field} net_flow_native,{field}*{factor} net_flow_cny,
              '{unit}' source_unit,'CNY' display_unit FROM src_{dataset} WHERE trade_date>DATE {f}
              QUALIFY row_number() OVER(PARTITION BY symbol,trade_date ORDER BY fetched_at DESC,source)=1""")
    b.emit('local_stock_three_sources_cny',' UNION ALL '.join(provider), ('trade_date','symbol','provider'))
    b.status('capital_flow','sector_windows',sw or '2026-11-09','申万成分汇总与同花顺原生板块独立计算。')


def margin(b):
    f = b.cutoff
    b.prior('fmargin', 'local_owners/margin_exchange_complete')
    b.prior('fsecurity', 'owners/margin_security_flow')
    b.prior('stock_mapping', 'local_owners/stock_context')
    if b.source('margin_trading'):
        b.table('osecurity', f"""WITH m AS (
          SELECT * FROM src_margin_trading JOIN oc USING(trade_date) WHERE trade_date>DATE {f}
          QUALIFY row_number() OVER(PARTITION BY symbol,trade_date ORDER BY fetched_at DESC,source)=1
        ), a AS (
          SELECT symbol,trade_date,margin_balance FROM fsecurity WHERE trade_date=DATE {f}
          UNION ALL SELECT symbol,trade_date,margin_balance FROM m
        )
        SELECT m.symbol,m.trade_date,m.margin_balance,m.margin_buy,m.margin_repay,m.short_balance,
          m.short_sell_volume,m.short_repay_volume,m.margin_buy-m.margin_repay reported_financing_net_buy,m.source,
          m.margin_balance-a.margin_balance balance_change,
          CASE WHEN m.margin_repay IS NOT NULL THEN 'reported_security_buy_minus_repay_not_full_market'
               ELSE 'observed_security_balance_change_only;repay_missing_no_substitution' END quality_status
        FROM m LEFT JOIN a ON a.symbol=m.symbol AND a.trade_date=m.prev_date""")
        detail = b.emit('margin_security_flow','SELECT * FROM osecurity',('trade_date','symbol'))
        b.emit('local_margin_industry_complete', """WITH m AS (
          SELECT o.*,s.industry_code,s.industry_name,CASE right(o.symbol,2)
            WHEN 'SH' THEN 'SSE' WHEN 'SZ' THEN 'SZSE' WHEN 'BJ' THEN 'BSE' END exchange
          FROM osecurity o JOIN stock_mapping s USING(symbol)
        ), a AS (
          SELECT * FROM m UNION ALL SELECT * REPLACE('ALL' AS exchange) FROM m WHERE exchange IN('SSE','SZSE')
        )
        SELECT exchange,trade_date,industry_code,max(industry_name) industry_name,sum(margin_balance) margin_balance,
          sum(margin_buy) margin_buy,sum(balance_change) balance_change,count(*) sample_count
        FROM a GROUP BY 1,2,3""",('trade_date','exchange','industry_code'))
    else:
        detail = None
    exchange_dates = {}
    if detail:
        exchange_dates = dict(b.sql("SELECT right(symbol,2),cast(max(trade_date) AS VARCHAR) FROM osecurity GROUP BY 1").fetchall())
    b.status('capital_flow','margin_securities',detail or '2026-11-09',
             '正式个券源的已观测样本，分交易所披露日期；相邻交易日余额变化与净买分别计算，缺偿还留空。',
             exchange_dates=exchange_dates)
    b.source('margin_exchange_daily')
    b.table('omargin', f"""WITH m AS (
      SELECT s.*,c.day_id,c.prev_date FROM src_margin_exchange_daily s JOIN oc c USING(trade_date)
      WHERE s.trade_date>DATE {f}
      QUALIFY row_number() OVER(PARTITION BY exchange,trade_date ORDER BY fetched_at DESC,source)=1
    ), a AS (
      SELECT exchange,trade_date,margin_balance FROM fmargin WHERE trade_date=DATE {f}
      UNION ALL SELECT exchange,trade_date,margin_balance FROM m
    ), x AS (
      SELECT m.*,m.margin_buy-m.margin_repay financing_net_buy,m.margin_balance-p.margin_balance balance_change,
        m.margin_buy/nullif(t.amount,0) financing_buy_turnover_ratio,
        CASE WHEN m.margin_repay IS NOT NULL THEN 'reported_buy_minus_repay' ELSE 'net_buy_missing_no_substitution' END quality_status
      FROM m LEFT JOIN a p ON p.exchange=m.exchange AND p.trade_date=m.prev_date
      LEFT JOIN oexchange_turnover t ON t.trade_date=m.trade_date AND t.exchange=m.exchange
    )
    SELECT * FROM x UNION ALL BY NAME
    SELECT 'ALL' exchange,trade_date,
      CASE WHEN count(margin_balance)=2 THEN sum(margin_balance) END margin_balance,
      CASE WHEN count(margin_buy)=2 THEN sum(margin_buy) END margin_buy,
      CASE WHEN count(margin_repay)=2 THEN sum(margin_repay) END margin_repay,
      CASE WHEN count(short_balance)=2 THEN sum(short_balance) END short_balance,sum(total_balance) total_balance,
      CASE WHEN count(financing_net_buy)=2 THEN sum(financing_net_buy) END financing_net_buy,
      CASE WHEN count(balance_change)=2 THEN sum(balance_change) END balance_change,
      CASE WHEN count(financing_buy_turnover_ratio)=2 THEN sum(margin_buy)/nullif(sum(margin_buy/nullif(financing_buy_turnover_ratio,0)),0) END financing_buy_turnover_ratio,
      'SSE+SZSE only;missing_component_stays_null' quality_status
    FROM x WHERE exchange IN('SSE','SZSE') GROUP BY trade_date HAVING count(*)=2""")
    exchange = b.emit('margin_exchange_flow','SELECT * FROM omargin',('trade_date','exchange'))
    if exchange and b.source('valuation_metrics'):
        b.emit('local_margin_ratio_complete', """WITH d AS (
          SELECT symbol,trade_date,float_mv FROM src_valuation_metrics
          WHERE starts_with(symbol,'0') OR starts_with(symbol,'3') OR starts_with(symbol,'6') OR ends_with(symbol,'.BJ')
          QUALIFY row_number() OVER(PARTITION BY symbol,trade_date ORDER BY fetched_at DESC,source)=1
        ), v AS (
          SELECT CASE right(symbol,2) WHEN 'SH' THEN 'SSE' WHEN 'SZ' THEN 'SZSE' WHEN 'BJ' THEN 'BSE' END exchange,
            trade_date,sum(float_mv) float_market_value,count(float_mv) denominator_sample FROM d GROUP BY 1,2
        ), a AS (
          SELECT m.exchange,m.trade_date,m.margin_balance,v.float_market_value,v.denominator_sample
          FROM omargin m LEFT JOIN v USING(exchange,trade_date) WHERE m.exchange<>'ALL'
          UNION ALL SELECT 'ALL',trade_date,sum(margin_balance),
            CASE WHEN count(float_market_value)=2 THEN sum(float_market_value) END,sum(denominator_sample)
          FROM omargin LEFT JOIN v USING(exchange,trade_date) WHERE exchange IN('SSE','SZSE')
          GROUP BY trade_date HAVING count(*)=2 AND count(margin_balance)=2
        ) SELECT *,margin_balance/nullif(float_market_value,0) margin_float_ratio FROM a""",('trade_date','exchange'))
    b.status('capital_flow','margin_exchange',exchange or '2026-11-09',
             '正式交易所汇总未新增；东财个券样本不全且无偿还额，不能生成交易所总额或净买。'
             if not exchange else '按正式交易所披露汇总；沪深合计须两通道齐备。')


def cross(b):
    f = b.cutoff
    b.prior('fcross', 'owners/cross_border_daily')
    if b.source('cross_border_daily_stats',start=date(2024,8,19)):
        b.prior('fprices','local_owners/daily_prices')
        b.table('ocross', f"""WITH a AS (
          SELECT trade_date,sum(amount) ashare_turnover FROM (
            SELECT trade_date,sum(amount) amount FROM oexchange_turnover WHERE trade_date>DATE {f} GROUP BY 1
            UNION ALL SELECT trade_date,amount FROM fprices WHERE trade_date BETWEEN DATE '2024-08-19' AND DATE {f}
              AND (starts_with(symbol,'6') OR starts_with(symbol,'0') OR starts_with(symbol,'3') OR ends_with(symbol,'.BJ'))
              AND amount IS NOT NULL QUALIFY row_number() OVER(PARTITION BY symbol,trade_date ORDER BY amount DESC)=1
          )
          GROUP BY 1
        )
          SELECT s.*,a.ashare_turnover,
            CASE WHEN starts_with(channel,'north') AND currency='CNY' THEN turnover/nullif(a.ashare_turnover,0) END share_of_ashare_turnover,
            CASE WHEN starts_with(channel,'north') AND s.trade_date>=DATE '2024-08-19'
              THEN CASE WHEN turnover IS NULL THEN 'recent_turnover_missing;net_not_disclosed'
                ELSE 'reported_recent_turnover_observed_segment;net_not_disclosed' END
              ELSE 'reported_channel_daily' END quality_status
          FROM src_cross_border_daily_stats s LEFT JOIN a USING(trade_date) WHERE s.trade_date>DATE {f}
            OR (s.trade_date>=DATE '2024-08-19' AND starts_with(channel,'north') AND currency='CNY')
          QUALIFY row_number() OVER(PARTITION BY channel,currency,s.trade_date ORDER BY fetched_at DESC,source)=1""")
        cross_day = b.emit('cross_border_daily','SELECT * FROM ocross',('trade_date','channel','currency'))
    else:
        b.table('ocross','SELECT * FROM fcross WHERE false')
        cross_day = None
    top = None
    if b.source('cross_border_top_trades'):
        top = b.emit('cross_border_top_trades_detail', f"""SELECT * FROM src_cross_border_top_trades WHERE trade_date>DATE {f}
          QUALIFY row_number() OVER(PARTITION BY trade_date,channel,symbol ORDER BY fetched_at DESC,source)=1""",
          ('trade_date','channel','rank','symbol'))
    north,south = b.sql("""SELECT max(trade_date) FILTER(WHERE starts_with(channel,'north') AND turnover IS NOT NULL),
      max(trade_date) FILTER(WHERE starts_with(channel,'south') AND currency='HKD' AND net_amount IS NOT NULL)
      FROM (SELECT * FROM fcross UNION ALL BY NAME SELECT * FROM ocross)""").fetchone()
    b.status('capital_flow','cross_border',cross_day or '2026-11-09',
             '已接入此前漏接的正式北向全市场成交；日统计和十大成交分别按通道币种。北向净买停披，十大不代总量。',
             north_turnover_date=str(north) if north else None,south_hkd_net_date=str(south) if south else None,
             top_trades_date=top or '2026-11-09')


def dragon(b):
    f = b.cutoff
    b.prior('fdragon','owners/dragon_tiger_events')
    events = None
    if b.source('dragon_tiger'):
        b.table('odragon', f"""WITH e AS (
          SELECT symbol,trade_date,string_agg(DISTINCT reason,';' ORDER BY reason) reasons,count(*) reason_rows,
            count(DISTINCT struct_pack(b:=buy_amount,s:=sell_amount,n:=net_amount)) amount_variants,
            CASE WHEN amount_variants=1 THEN max(buy_amount) END buy_amount,
            CASE WHEN amount_variants=1 THEN max(sell_amount) END sell_amount,
            CASE WHEN amount_variants=1 THEN max(net_amount) END net_amount
          FROM (SELECT DISTINCT symbol,trade_date,reason,buy_amount,sell_amount,net_amount FROM src_dragon_tiger)
          WHERE trade_date>DATE {f} GROUP BY 1,2
        )
        SELECT e.*,r.daily_return,r.amount turnover,c.next_date,nr.daily_return next_trading_day_return,
          net_amount/nullif(r.amount,0) net_turnover_ratio,
          CASE WHEN amount_variants=1 THEN 'identical_reason_amounts_deduped' ELSE 'conflicting_reasons_amounts_null' END quality_status
        FROM e JOIN oc c USING(trade_date) LEFT JOIN ostock_returns r USING(symbol,trade_date)
        LEFT JOIN ostock_returns nr ON nr.symbol=e.symbol AND nr.trade_date=c.next_date""")
        events = b.emit('dragon_tiger_events', f"""SELECT * FROM odragon
          UNION ALL BY NAME
          SELECT p.* REPLACE(c.next_date AS next_date,nr.daily_return AS next_trading_day_return)
          FROM fdragon p JOIN oc c USING(trade_date)
          LEFT JOIN ostock_returns nr ON nr.symbol=p.symbol AND nr.trade_date=c.next_date WHERE p.trade_date=DATE {f}""",('trade_date','symbol'))
        b.emit('dragon_tiger_daily', """SELECT trade_date,sum(net_amount ORDER BY symbol) net_amount,count(*) event_count,
            count(net_amount)*1.0/count(*) coverage_ratio FROM odragon GROUP BY 1""")
        b.emit('local_dragon_regulars_history', f"""WITH x AS (
          SELECT symbol,trade_date,net_amount,next_trading_day_return FROM odragon UNION ALL
          SELECT symbol,trade_date,net_amount,next_trading_day_return FROM fdragon
        )
        SELECT t.trade_date anchor_date,x.symbol,count(*) event_count,sum(net_amount ORDER BY x.trade_date) observed_net_amount,
          count(net_amount) valid_amount_days,max(x.trade_date) latest_date,avg(next_trading_day_return ORDER BY x.trade_date) observed_next_return
        FROM oc t JOIN oc d ON d.day_id BETWEEN t.day_id-4 AND t.day_id JOIN x ON x.trade_date=d.trade_date
        WHERE t.trade_date>DATE {f} AND t.trade_date<=(SELECT max(trade_date) FROM odragon)
        GROUP BY 1,2""",('anchor_date','symbol'),'anchor_date')
        if b.source('sector_members',start=None,day='as_of_date'):
            b.emit('local_dragon_themes_history', """WITH m AS (
              SELECT * FROM src_sector_members QUALIFY row_number() OVER(
                PARTITION BY symbol,sector_code ORDER BY as_of_date DESC,sector_name)=1
            )
            SELECT e.trade_date,m.sector_code,max(m.sector_name) sector_name,count(DISTINCT e.symbol) stock_count,
              sum(net_amount ORDER BY e.symbol) observed_net_amount,count(net_amount) valid_amount_count
            FROM odragon e JOIN m USING(symbol) GROUP BY 1,2""",('trade_date','sector_code'))
        if b.source('stock_daily_research_inputs'):
            b.emit('local_dragon_turnover', """SELECT d.symbol,d.trade_date,d.turnover_rate
              FROM src_stock_daily_research_inputs d SEMI JOIN odragon e USING(symbol,trade_date)
              GROUP BY 1,2,3 QUALIFY count(*) OVER(PARTITION BY d.symbol,d.trade_date)=1""",('trade_date','symbol'))
    b.status('capital_flow','dragon_events',events or '2026-11-09',
             '同股同日多原因去重；金额冲突为空；后续收益只用下一交易日已到复权行情。')
    seat = None
    if b.source('dragon_tiger_seat_observations'):
        b.table('oseats', f"""WITH x AS (
          SELECT symbol,trade_date,trim(seat) seat,max(buy_amount_cny) buy_amount,max(sell_amount_cny) sell_amount,
            count(DISTINCT struct_pack(b:=buy_amount_cny,s:=sell_amount_cny)) amount_variants,count(*) observation_count
          FROM src_dragon_tiger_seat_observations WHERE trade_date>DATE {f} GROUP BY 1,2,3
        ) SELECT *,CASE WHEN amount_variants=1 THEN buy_amount-sell_amount END net_amount,
          CASE WHEN amount_variants=1 THEN 'exact_amount_dedup_across_sides_reasons'
            ELSE 'conflict_quarantined_net_null' END quality_status FROM x""")
        seat = b.emit('dragon_tiger_seat_daily','SELECT * FROM oseats',('trade_date','symbol','seat'))
        b.emit('dragon_tiger_seat_ranking',"""SELECT trade_date,seat,count(*) stock_count,sum(net_amount) net_amount,
           count(net_amount)*1.0/count(*) coverage_ratio,sum(observation_count) raw_observations FROM oseats GROUP BY 1,2""",
           ('trade_date','seat'))
    b.status('capital_flow','dragon_seats',seat or '2026-11-09',
             '同股日席位金额冲突留空。' if seat else '正式席位观察与原 Tushare 席位未新增；事件表不包含席位。')


def balance(b):
    f = b.cutoff
    b.prior('fipo','owners/ipo_funding_detail')
    b.prior('fholder','owners/holdertrade_daily')
    b.prior('fmargin','local_owners/margin_exchange_complete')
    b.table('oipo','SELECT * FROM fipo WHERE false')
    if b.source('market_ipo_funding'):
        b.table('oipo',f"""SELECT * FROM src_market_ipo_funding WHERE trade_date>DATE {f}
          QUALIFY row_number() OVER(PARTITION BY symbol,trade_date ORDER BY fetched_at DESC,source)=1""")
        b.emit('ipo_funding_detail','SELECT * FROM oipo',('trade_date','symbol'))
    b.table('oholder','SELECT * FROM fholder WHERE false')
    if b.source('stock_holdertrade_announcements',day='announce_date'):
        b.table('oholder_detail',f"""WITH e AS (SELECT * FROM src_stock_holdertrade_announcements WHERE announce_date>DATE {f}
          QUALIFY row_number() OVER(PARTITION BY symbol,announce_date,holder_name,trade_type,begin_date,close_date,change_vol
            ORDER BY event_id)=1)
          SELECT e.*,p.trade_date price_source_date,p.close estimated_price,
            e.change_vol*CASE WHEN e.avg_price>0 THEN e.avg_price ELSE p.close END estimated_amount,
            CASE WHEN e.trade_type='IN' THEN 1 WHEN e.trade_type='DE' THEN -1 END direction,
            CASE WHEN e.avg_price>0 THEN 'reported_average_price' WHEN p.close>0 THEN 'announcement_last_close_estimate'
              ELSE 'missing_price' END quality_status
          FROM e ASOF LEFT JOIN obars p ON e.symbol=p.symbol AND e.announce_date>=p.trade_date""")
        b.emit('holdertrade_valued','SELECT * FROM oholder_detail',('announce_date','event_id'),'announce_date')
        b.table('oholder', """SELECT announce_date obs_date,sum(estimated_amount) FILTER(WHERE direction=1) increase_amount,
          sum(estimated_amount) FILTER(WHERE direction=-1) decrease_amount,sum(estimated_amount*direction) net_increase_amount,
          count(*) event_count,count(estimated_amount)*1.0/count(*) coverage_ratio FROM oholder_detail GROUP BY 1""")
        b.emit('holdertrade_daily','SELECT * FROM oholder',('obs_date',),'obs_date')
    collected = b.emit('capital_balance_daily', f"""WITH etf AS (
      SELECT trade_date,sum(net_subscription) etf_net_subscription,count(net_subscription)*1.0/count(*) etf_coverage,
        max(available_date) etf_available_date FROM oetf WHERE category IN('broad','industry_theme','strategy_style') GROUP BY 1
    ), south AS (
      SELECT trade_date,CASE WHEN count(DISTINCT channel)=2 AND count(net_amount)=2 THEN sum(net_amount) END southbound_net_buy
      FROM ocross WHERE channel IN('south_sh','south_sz') AND currency='CNY' GROUP BY 1
    ), ipo AS (SELECT trade_date,sum(raised_amount) ipo_funding FROM oipo GROUP BY 1)
    SELECT c.trade_date,e.etf_net_subscription,e.etf_coverage,e.etf_available_date,m.financing_net_buy,s.southbound_net_buy,
      i.ipo_funding,h.decrease_amount holder_decrease,h.net_increase_amount holder_net_increase,
      NULL::DOUBLE strict_net_balance,'ETF_observed_rows_only_crosssection_unverified;strict_net_withdrawn' quality_status
    FROM oc c LEFT JOIN etf e USING(trade_date) LEFT JOIN omargin m ON m.trade_date=c.trade_date AND m.exchange='ALL'
    LEFT JOIN south s ON s.trade_date=c.trade_date LEFT JOIN ipo i ON i.trade_date=c.trade_date
    LEFT JOIN oholder h ON h.obs_date=c.trade_date WHERE c.trade_date>DATE {f}""")
    b.status('capital_flow','capital_balance',collected,
             '按正式交易日更新分项；无公告不补零，未知ETF完整度、无同口径人民币南向或缺任一分项，不发布严格净额。',
             status='partial',strict_total_date=None)
