"""Exact latest-board price, ranking and risk rules from the accepted research chain."""
import numpy as np
import pandas as pd

def midrank(s, ascending=True):
    v = pd.to_numeric(s, errors="coerce")
    n = v.notna().sum()
    return (
        ((v.rank(method="average", ascending=ascending) - 0.5) / n * 100) if n else v * float("nan")
    )


def severity(v, yellow, red, less=False):
    if pd.isna(v):
        return np.nan
    if less:
        return 2.0 if v < red else 1.0 if v < yellow else 0.0
    return 2.0 if v > red else 1.0 if v > yellow else 0.0


def audit_severity(opinion):
    if pd.isna(opinion):
        return np.nan
    text = str(opinion)
    if "无保留" in text:
        return 1.0 if any(t in text for t in ["强调", "持续经营"]) else 0.0
    if any(t in text for t in ["保留", "否定", "无法表示"]):
        return 2.0
    return 1.0 if any(t in text for t in ["强调", "持续经营"]) else np.nan


def same_period_financials(frame):
    latest_period = frame.groupby("symbol").report_period.transform("max")
    latest = frame[frame.report_period == latest_period].pivot(
        index="symbol", columns="metric", values="item_value"
    )
    latest["financial_report_period"] = frame.groupby("symbol").report_period.max()
    return latest


def financial_latest(c, day):
    aliases = {
        "goodwill": ["goodwill"],
        "cash": ["money_cap", "monetary_funds"],
        "short_debt": ["st_borr", "short_term_borrowings"],
        "due_debt": ["non_cur_liab_due_1y", "noncurrent_liabilities_due_within_one_year"],
        "equity": ["total_hldr_eqy_exc_min_int", "parent_equity"],
        "ar": ["accounts_receiv", "accounts_receivable"],
        "revenue": ["revenue"],
        "ocf": ["n_cashflow_act", "net_cash_operate"],
        "profit": ["n_income_attr_p"],
        "deducted": ["net_profit_deducted"],
    }
    codes = [v for vs in aliases.values() for v in vs]
    q = (
        f"""SELECT symbol,report_period,item_code,max(announce_date) announce_date,
    CASE WHEN max(item_value)-min(item_value)<=greatest(1.,abs(max(item_value))*1e-6) THEN max(item_value) END item_value,
    count(distinct item_value)>1 input_variant
    FROM (SELECT * FROM market_cn_fin WHERE announce_date<='{day}'
     AND item_code IN ("""
        + ",".join("'" + x + "'" for x in codes)
        + """ )
     QUALIFY dense_rank() OVER(PARTITION BY symbol,report_period,item_code ORDER BY announce_date DESC)=1)
     GROUP BY 1,2,3"""
    )
    f = c.execute(q).df()
    mapping = {v: k for k, vs in aliases.items() for v in vs}
    f["metric"] = f.item_code.map(mapping)
    f = f.groupby(["symbol", "metric", "report_period"], as_index=False).agg(
        item_value=("item_value", "max"), min_value=("item_value", "min"),
        announce_date=("announce_date", "max"), input_variant=("input_variant", "max"),
    )
    f["item_value"] = f.item_value.where(f.item_value-f.min_value <= np.maximum(1,f.item_value.abs()*1e-6))
    f = f.drop(columns="min_value")
    # Ratios must use one common reporting period, never the most recent
    # available value independently for each item.
    latest = same_period_financials(f)
    annual = f[f.report_period.str.endswith("Q4")].copy()
    annual["year"] = annual.report_period.str[:4].astype(int)
    annual = (
        annual[annual.year.isin([day.year-2,day.year-1])]
        .pivot_table(
            index=["symbol", "year"], columns="metric", values="item_value", aggfunc="last"
        )
        .reset_index()
    )
    return latest, annual, f


def build(c, day):
    latest = (
        c.execute(f"select * from market_contrarian_prices where trade_date='{day}'")
        .df()
        .rename(columns={"is_stable": "stable"})
        .set_index("symbol")
    )
    v = (
        c.execute(f"select * from market_values where trade_date='{day}'")
        .df()
        .set_index("symbol")
    )
    x = latest.join(v[["pe_ttm", "pb", "ps_ttm", "total_mv", "float_mv"]], how="left")
    ind = (
        c.execute(
            f"select symbol,industry_name,industry_code from market_cn_industry where classification_system='sw' and as_of_date<='{day}' qualify row_number() over(partition by symbol order by as_of_date desc,industry_code)=1"
        )
        .df()
        .set_index("symbol")
    )
    x = x.join(ind)
    x['source_industry'] = x.industry_name
    x["industry_name"] = x.industry_name.fillna("未知行业")
    x["industry_code"] = x.industry_code.fillna("unknown")
    di = (
        c.execute(
            f"select symbol,dv_ttm from market_research_unique where trade_date='{day}'"
        )
        .df()
        .set_index("symbol")
    )
    x = x.join(di)
    for view,cols in [('pcf', 'pcf_ttm,operating_cash_flow_ttm_cny'),('ev','ev_ebitda,net_debt_cny,ebitda_ttm_cny,ev_cny'),
                      ('fscore','score')]:
        factors = c.execute(f"SELECT symbol,{cols} FROM market_cn_{view}").df().set_index('symbol')
        x = x.join(factors)
    x['market_value_cny'] = x.total_mv
    x['ocf_ttm_cny'] = x.operating_cash_flow_ttm_cny
    f, annual, evidence = financial_latest(c, day)
    x = x.join(f)
    audits = (
        c.execute(
            f"select symbol,opinion from market_cn_audits where announce_date<='{day}' qualify row_number() over(partition by symbol order by report_period desc,announce_date desc,opinion)=1"
        )
        .df()
        .set_index("symbol")
    )
    x = x.join(audits)
    x["audit_light"] = x.opinion.map(audit_severity)
    x["cash_debt_ratio"] = x.cash / (x.short_debt + x.due_debt).where(
        (x.short_debt + x.due_debt) > 0
    )
    x["cash_debt_light"] = x.cash_debt_ratio.map(lambda v: severity(v, 1, 0.5, True))
    x["goodwill_ratio"] = x.goodwill / x.equity.where(x.equity > 0)
    x["goodwill_light"] = x.goodwill_ratio.map(lambda v: severity(v, 0.3, 0.5))
    ratios = {}
    ded = {}
    argrowth = {}
    for symbol, g in annual.groupby("symbol"):
        z = g.set_index("year")
        vals = []
        dvals = []
        for year in (day.year-2,day.year-1):
            if year in z.index:
                rr = z.loc[year]
                pr = rr.get("profit", np.nan)
                oc = rr.get("ocf", np.nan)
                vals.append(oc / pr if pd.notna(pr) and pr > 0 else np.nan)
                dvals.append(rr.get("deducted", np.nan))
            else:
                vals.append(np.nan)
                dvals.append(np.nan)
        ratios[symbol] = (
            2.0
            if all(pd.notna(t) and t < 0.5 for t in vals)
            else 1.0
            if pd.notna(vals[1]) and vals[1] < 0.5
            else 0.0
            if pd.notna(vals[1])
            else np.nan
        )
        ded[symbol] = (
            2.0
            if all(pd.notna(t) and t < 0 for t in dvals)
            else 1.0
            if pd.notna(dvals[1]) and dvals[1] < 0
            else 0.0
            if pd.notna(dvals[1])
            else np.nan
        )
        if day.year-2 in z.index and day.year-1 in z.index:
            a, b = z.loc[day.year-2], z.loc[day.year-1]
            ar0, rev0 = a.get("ar", np.nan), a.get("revenue", np.nan)
            argrowth[symbol] = (
                b.get("ar", np.nan) / ar0 - b.get("revenue", np.nan) / rev0
                if ar0 > 0 and rev0 > 0
                else np.nan
            )
    x["cashflow_light"] = pd.Series(ratios)
    x["deducted_loss_light"] = pd.Series(ded)
    x["ar_growth_gap"] = pd.Series(argrowth)
    x["receivable_light"] = x.ar_growth_gap.map(lambda v: severity(v, 0.15, 0.3))
    # Undemonstrated corporate-action/event denominators must remain missing, not green.
    sells = (
        c.execute(
            f"select symbol,sum(change_ratio ORDER BY event_id) pct from (select * from market_cn_holders where announce_date between DATE '{day}'-INTERVAL '6 months' and DATE '{day}' and trade_type='DE' qualify row_number() over(partition by symbol,holder_name,begin_date,close_date order by announce_date desc,event_id)=1) group by symbol"
        )
        .df()
        .set_index("symbol")
    )
    x["observed_sell_ratio_6m_pct"] = sells.pct
    x["holder_sell_light"] = x.observed_sell_ratio_6m_pct.map(lambda v: severity(v, 1, 3))
    unlock = (
        c.execute(
            f"select symbol,sum(unlock_shares ORDER BY unlock_date,unlock_shares) shares from market_cn_unlocks where unlock_date>DATE '{day}' and unlock_date<=DATE '{day}'+INTERVAL '3 months' group by symbol"
        )
        .df()
        .set_index("symbol")
    )
    x["future_unlock_pct_snapshot"] = (
        unlock.shares * 10_000 / (x.float_mv / x.close).where(x.float_mv > 0) * 100
    )
    x["unlock_light"] = x.future_unlock_pct_snapshot.map(lambda v: severity(v, 10, 20))
    deducted = (
        c.execute(
            f"select symbol,extract(year from report_period)::int y,profit_dedt from market_cn_indicators where announce_date<=DATE '{day}' and report_period in (DATE '{day.year-2}-12-31',DATE '{day.year-1}-12-31') qualify row_number() over(partition by symbol,report_period order by announce_date desc,profit_dedt)=1"
        )
        .df()
        .pivot(index="symbol", columns="y", values="profit_dedt")
    )
    if day.year-1 in deducted:
        a = deducted[day.year-1]
        prior = deducted.get(day.year-2, pd.Series(np.nan, index=a.index))
        replacement = pd.Series(
            np.where(a.notna(), np.where((a < 0) & (prior < 0), 2, np.where(a < 0, 1, 0)), np.nan),
            index=a.index,
        )
        x["deducted_loss_light"] = x.deducted_loss_light.combine_first(replacement)
    lights = [k for k in x if k.endswith("_light")]
    x["red_count"] = x[lights].eq(2).sum(axis=1)
    x["yellow_count"] = x[lights].eq(1).sum(axis=1)
    x["lights_available"] = x[lights].notna().sum(axis=1)
    st = (
        c.execute(
            f"select distinct symbol from market_cn_st where trade_date='{day}'"
        )
        .df()
        .symbol.tolist()
    )
    x["universe_eligible"] = (
        (pd.to_datetime(x.list_date) <= pd.Timestamp(day) - pd.Timedelta(days=365))
        & (x.amount > 0)
        & ~x.index.isin(st)
    )
    x["amount_rank"] = midrank(x.amount)
    x["universe_eligible"] &= x.amount_rank >= 5
    return x, evidence


def ranking(x, taxonomy):
    # The serving version uses SW L2 at 15+ names, otherwise L1.
    tax = taxonomy.set_index('industry_code')
    def ancestor(code, level):
        visited = set()
        while code in tax.index and code not in visited:
            visited.add(code)
            row = tax.loc[code]
            if row.level == level:
                return row.industry_name
            code = row.parent_industry_code
        return '未知行业'
    for level in ('L1','L2'):
        x['industry_'+level.lower()] = x.industry_code.map(lambda code:ancestor(code,level))
    nonfinancial = ~x.industry_l1.isin(['银行','非银金融','未知行业'])
    x['score'] = x.score.where(nonfinancial)
    for key in ('pcf_ttm','ev_ebitda'):
        x[key] = x[key].where(nonfinancial)
    x['score_group'] = x.industry_l2.where(x.groupby('industry_l2').industry_l2.transform('size')>=15,x.industry_l1)
    x['industry_name'] = x.industry_l1
    valuation = []
    for key in ('pe_ttm','pb','ps_ttm','pcf_ttm','ev_ebitda'):
        v = x[key].where((x[key]>0)&x.universe_eligible)
        if key in ('ps_ttm','pcf_ttm','ev_ebitda'):
            v = v.mask(x.industry_l1.isin(['银行','非银金融']))
        x[key+'_valid'] = v
        field = key+'_cheap'
        x[field] = v.groupby(x.score_group).transform(lambda v:midrank(v,False))
        valuation.append(field)
    x['dividend_score'] = midrank(x.dv_ttm.where(x.universe_eligible))
    x['valuation_score'] = x[[*valuation,'dividend_score']].mean(axis=1)
    reversal = []
    for key in ('ret20','ret3y','drawdown52'):
        field = key+'_reverse'
        x[field] = midrank(x[key].where(x.universe_eligible),False)
        reversal.append(field)
    x['reversal_score'] = x[reversal].mean(axis=1)
    x['composite_score'] = ((.7*x.valuation_score+.3*x.reversal_score)
        * np.where(x.score.fillna(4).ge(7),1.1,np.where(x.score.fillna(4).le(3),.8,1.0))
        * np.where(x.yellow_count.ge(2),.9,1.0)
        * np.where(x.cycle_candidate,1.1,1.0)).clip(0,100)
    rules = {**{'low_'+key[:-6]:x[key]>=80 for key in valuation},
             'high_dividend':(x.dv_ttm>=3)&(x.dividend_score>=80),
             'short_reversal':x.ret20_reverse>=80,
             'long_loser':x.ret3y_reverse>=80,
             'deep_drawdown':x.drawdown52_reverse>=80,
             'cycle_reverse':x.cycle_candidate}
    boards = pd.concat([x.loc[x.universe_eligible & mask,['trade_date']].assign(board=board)
                        for board,mask in rules.items()])
    x['hit_count'] = boards.groupby('symbol').size().reindex(x.index,fill_value=0)
    return x.reset_index(),boards.reset_index()


def refresh_boards(x, base):
    from datetime import date
    from seven_desks.market_refresh import component, literal, parquet, read, watermark, write

    db = x.db
    root = 'revision/contrarian/'
    factor_rel, board_rel = root+'daily-factors.parquet',root+'daily-boards.parquet'
    prior = read(base/factor_rel) if (base/factor_rel).is_file() else None
    legacy = read(base/'contrarian/ui_factors.parquet')
    last = (prior if prior is not None else legacy).trade_date.max().date()
    if last >= x.days[-1]:
        return None
    qualified = db.execute("""SELECT trade_date FROM market_bars JOIN market_adj_unique USING(symbol,trade_date)
        JOIN market_values USING(symbol,trade_date) JOIN market_research_unique USING(symbol,trade_date)
        WHERE trade_date>? GROUP BY 1 HAVING count(*)>=5000 ORDER BY 1""",[last]).fetchall()
    new_days = [r[0] for r in qualified]
    if not new_days:
        return component(x,str(last),'没有新的同日全市场行情、后复权、估值、daily_basic完整截面',retained=True)
    x.view('industry_taxonomy',alias='market_taxonomy')
    for dataset, short, field, layer in [
        ('industry_members','industry','as_of_date','curated'),
        ('financial_statement_items','fin','announce_date','curated'),
        ('stock_financial_indicators','indicators','announce_date','curated'),
        ('audit_opinions','audits','announce_date','curated'),
        ('stock_holdertrade_announcements','holders','announce_date','curated'),
        ('share_unlock_schedule','unlocks',None,'curated'),
        ('special_treatment_status','st','trade_date','curated'),
        ('stock_financial_ttm_events','ttm','as_of_date','curated'),
    ]:
        floor = date(last.year-4,1,1) if short=='fin' else None
        x.view(dataset,floor,day=field,layer=layer,alias='market_cn_'+short)
    db.execute(f"""CREATE OR REPLACE TEMP TABLE market_contrarian_prices AS
      WITH historical AS (
        SELECT symbol,trade_date,close,amount,px,list_date FROM {parquet(base/'contrarian/price_features.parquet')}
        WHERE trade_date>=DATE {literal(last)}-INTERVAL 5 YEAR
        QUALIFY row_number() OVER(PARTITION BY symbol,trade_date ORDER BY px NULLS LAST)=1
      ), inputs AS (
        SELECT * FROM historical UNION ALL
        SELECT b.symbol,b.trade_date,b.close,b.amount,b.close*a.factor px,i.list_date FROM market_bars b
        JOIN market_stock_identity i USING(symbol) LEFT JOIN market_adj_unique a USING(symbol,trade_date)
        WHERE b.trade_date>(SELECT max(trade_date) FROM historical)
      ), dates AS (
        -- Keep the formal day identifier even when the whole A-share shard is
        -- absent; a missing session must not satisfy the exact 20/756-day test.
        SELECT trade_date,row_number() OVER(ORDER BY trade_date) day_id FROM (
          SELECT DISTINCT trade_date FROM historical UNION
          SELECT trade_date FROM market_sessions WHERE trade_date>(SELECT max(trade_date) FROM historical)
        )
      ), f AS (
        SELECT *,avg(px) OVER w20 ma20,count(px) OVER w20 n20,
          lag(px,20) OVER w p20,lag(day_id,20) OVER w id20,
          lag(px,756) OVER w p756,lag(day_id,756) OVER w id756,
          max(px) OVER w252 high252,count(px) OVER w252 n252,
          lag(px) OVER w p1,lag(px,2) OVER w p2,lag(px,3) OVER w p3
        FROM inputs JOIN dates USING(trade_date)
        WINDOW w AS(PARTITION BY symbol ORDER BY trade_date),
          w20 AS(PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW),
          w252 AS(PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW)
      )
      SELECT symbol,trade_date,close,amount,px,list_date,
        CASE WHEN day_id-id20=20 AND p20>0 THEN px/p20-1 END ret20,
        CASE WHEN day_id-id756=756 AND p756>0 THEN px/p756-1 END ret3y,
        CASE WHEN n252=252 THEN px/high252-1 END drawdown52,
        -- The stored ratio is rounded only after the original window mean;
        -- parallel window execution can vary its last IEEE-754 bit.
        CASE WHEN n20=20 AND ma20>0 THEN round(px/ma20-1,12) END bias20,
        CASE WHEN n20=20 THEN ((px>ma20)::INT+(px/ma20-1>-.05)::INT+(NOT(px<p1 AND p1<p2 AND p2<p3))::INT)>=2 END is_stable
      FROM f WHERE trade_date IN ({','.join('DATE '+literal(d) for d in new_days)})""")
    taxonomy = db.execute("""SELECT industry_code,min(industry_name) industry_name,min(parent_industry_code) parent_industry_code,
        min(level) AS level FROM market_taxonomy WHERE classification_system='SW2021' GROUP BY 1""").df()
    snapshots, memberships, financial, history = [], [], [], []
    for day in new_days:
        financial_ratios(db,day)
        xday,evidence = build(db,day)
        # Live page's cycle rule uses available ROE and same-report industry profit.
        xday = cycle(db,xday,taxonomy,day,evidence)
        history.append(historical_slice(xday,db))
        factors, member = ranking(xday, taxonomy)
        snapshots.append(factors)
        memberships.append(member)
        financial.append(evidence.assign(trade_date=pd.Timestamp(day)))
    factors = pd.concat(snapshots,ignore_index=True)
    add_time_percentiles(x,base,factors)
    boards = pd.concat(memberships,ignore_index=True)
    write(x,factor_rel,pd.concat([prior,factors]),['trade_date','symbol'])
    old_boards = read(base/board_rel) if (base/board_rel).is_file() else None
    write(x,board_rel,pd.concat([old_boards,boards]),['trade_date','board','symbol'])
    hist_rel = root+'daily-history.parquet'
    old_history = read(base/hist_rel) if (base/hist_rel).is_file() else None
    write(x,hist_rel,pd.concat([old_history,*history]),['trade_date','symbol'])
    rel = root+'daily-financial-inputs.parquet'
    old_financial = read(base/rel) if (base/rel).is_file() else None
    write(x,rel,pd.concat([old_financial,*financial]),['trade_date','symbol','report_period','metric'])
    return component(x,watermark(factors.trade_date),'同口径十榜、风险、七成估值三成反转及各项乘数重算',
                     board_counts=boards.loc[boards.trade_date.eq(factors.trade_date.max())].groupby('board').size().to_dict(),
                     missing_price_sessions=[str(d) for d in x.days if last<d<=new_days[-1] and d not in new_days],
                     report_inputs='财报按公告日期可见；旧历史和新近因子同样不保证严格PIT')


def cycle(db, x, taxonomy, day, evidence):
    from seven_desks.market_refresh import literal
    tx = taxonomy.set_index('industry_code')
    def l1(code):
        for _ in range(3):
            if code not in tx.index:
                break
            row = tx.loc[code]
            if row.level=='L1':
                return row.industry_name
            code = row.parent_industry_code
        return '未知行业'
    groups = x.industry_code.map(l1)
    # Last visible ROE series keeps the same count and midrank; no later report is read.
    db.execute(f"""CREATE OR REPLACE TEMP VIEW market_cn_roe_for_day AS
      SELECT symbol,report_period,roe FROM market_cn_indicators WHERE announce_date<=DATE {literal(day)}
        AND roe IS NOT NULL AND report_period>=DATE {literal(day)}-INTERVAL 6 YEAR
      QUALIFY row_number() OVER(PARTITION BY symbol,report_period ORDER BY announce_date DESC,roe)=1""")
    raw = db.execute('SELECT * FROM market_cn_roe_for_day ORDER BY symbol,report_period').df()
    raw = raw.loc[raw.report_period>=raw.groupby('symbol').report_period.transform('max')-pd.DateOffset(years=5)]
    grp = raw.groupby('symbol')
    raw['roe_five_year_pct'] = grp.roe.transform(midrank)
    raw['roe_sample_count'] = grp.roe.transform('size')
    recent = raw.groupby('symbol').tail(1).set_index('symbol')
    x['roe_five_year_pct'] = recent.roe_five_year_pct.where(recent.roe_sample_count>=8)
    x['roe_latest'] = recent.roe
    x['roe_sample_count'] = recent.roe_sample_count
    x['roe_report_period'] = recent.report_period
    # Same-report prior-year profit growth, with the newest announcement
    # among the used cohort as its conservative availability date.
    profit = evidence.loc[evidence.metric.eq('profit'),['symbol','report_period','announce_date','item_value']].copy()
    profit['previous_period'] = (profit.report_period.str[:4].astype(int)-1).astype(str)+profit.report_period.str[4:]
    comp = profit.merge(profit[['symbol','report_period','item_value','announce_date']].rename(
        columns={'report_period':'previous_period','item_value':'previous_profit','announce_date':'previous_announce'}),
        how='left',on=['symbol','previous_period'],validate='one_to_one')
    valid = comp.previous_profit.ne(0)&comp.previous_announce.le(comp.announce_date)
    comp['earnings_yoy'] = ((comp.item_value-comp.previous_profit)/comp.previous_profit.abs()).where(valid)
    comp['industry_name'] = comp.symbol.map(groups)
    cohorts = comp.groupby(['industry_name','report_period'],as_index=False).agg(
        earnings_yoy=('earnings_yoy','median'),as_of_date=('announce_date','max'))
    e = cohorts.loc[cohorts.as_of_date.le(pd.Timestamp(day))].sort_values(
        ['report_period','as_of_date']).groupby('industry_name').tail(1).set_index('industry_name')
    x['cycle_industry_yoy'] = groups.map(e.earnings_yoy)
    x['cycle_report_period'] = groups.map(e.report_period)
    x['cycle_candidate'] = x.universe_eligible & x.roe_five_year_pct.le(20) & x.cycle_industry_yoy.gt(0)
    return x


def financial_ratios(db, day):
    from seven_desks.market_refresh import literal
    d = 'DATE '+literal(day)
    db.execute(f"""CREATE OR REPLACE TEMP TABLE market_cn_pcf AS
      WITH e AS (
        SELECT symbol,value_cny,quality_status FROM market_cn_ttm
        WHERE metric='operating_cash_flow_ttm' AND as_of_date<={d}
        QUALIFY row_number() OVER(PARTITION BY symbol ORDER BY as_of_date DESC,report_period DESC,input_fingerprint)=1
      )
      SELECT v.symbol, e.value_cny operating_cash_flow_ttm_cny,
         CASE WHEN e.value_cny>0 AND v.total_mv>0 AND e.quality_status='candidate_restated_vintage_timing_unverified'
          THEN v.total_mv/e.value_cny END pcf_ttm
      FROM market_values v LEFT JOIN e USING(symbol) WHERE trade_date={d}""")
    latest = day.year if day.month>=9 else day.year-1
    cur, prior, annual = f'{latest}Q2',f'{latest-1}Q2',f'{latest-1}Q4'
    db.execute(f"""CREATE OR REPLACE TEMP TABLE market_cn_ev AS
      WITH raw AS (
        SELECT symbol,report_period,item_code,announce_date,min(item_value) v
        FROM market_cn_fin WHERE announce_date<={d} AND item_value IS NOT NULL
          AND ((statement_type='income' AND item_code='ebitda' AND report_period IN ('{cur}','{prior}','{annual}'))
          OR (statement_type='indicator' AND item_code='net_debt' AND report_period='{cur}'))
        GROUP BY 1,2,3,4 HAVING count(DISTINCT item_value)=1
        QUALIFY row_number() OVER(PARTITION BY symbol,report_period,item_code ORDER BY announce_date DESC)=1
      ), f AS (
        SELECT symbol,max(v) FILTER(WHERE item_code='net_debt') net_debt_cny,
           max(v) FILTER(WHERE item_code='ebitda' AND report_period='{cur}')
           +max(v) FILTER(WHERE item_code='ebitda' AND report_period='{annual}')
           -max(v) FILTER(WHERE item_code='ebitda' AND report_period='{prior}') ebitda_ttm_cny
        FROM raw GROUP BY 1
      )
      SELECT f.*, total_mv+net_debt_cny ev_cny,
        CASE WHEN total_mv>0 AND ev_cny>0 AND ebitda_ttm_cny>0 THEN ev_cny/ebitda_ttm_cny END ev_ebitda
      FROM f JOIN market_values v USING(symbol) WHERE trade_date={d}""")
    fscore(db,day)


def fscore(db, day):
    # Original nine annual components, with audited input aliases and no score
    # for a missing component or same-day source conflict.
    from seven_desks.market_refresh import literal
    fields = {'balancesheet:1':('total_assets','total_cur_assets','total_cur_liab','total_share','lt_borr'),
              'income:1':('n_income_attr_p','revenue','oper_cost'),'cashflow:1':('n_cashflow_act',)}
    period = [f'{day.year-i}Q4' for i in (1,2,3)]
    predicate = ' OR '.join(f"(statement_type={literal(k)} AND item_code IN ({','.join(map(literal,v))}))"
                            for k,v in fields.items())
    q = f"""WITH raw AS (
      SELECT symbol,item_code,report_period,announce_date,count(DISTINCT item_value) variants,min(item_value) value_
      FROM market_cn_fin WHERE announce_date<=DATE {literal(day)} AND isfinite(item_value)
        AND report_period IN ({','.join(map(literal,period))}) AND ({predicate}) GROUP BY 1,2,3,4
    ), chosen AS (
      SELECT *,max(variants) OVER(PARTITION BY symbol,report_period) conflict_count FROM raw
      QUALIFY row_number() OVER(PARTITION BY symbol,item_code,report_period ORDER BY announce_date DESC)=1
    ) SELECT * FROM chosen WHERE variants=1"""
    z = db.execute(q).df()
    f = z.pivot(index='symbol',columns=['report_period','item_code'],values='value_')
    keys = [k for values in fields.values() for k in values]
    def annual(i):
        return f.reindex(columns=pd.MultiIndex.from_product([[period[i]],keys])).droplevel(0,axis=1)
    c,p,b = map(annual,range(3))
    ca = (c.total_assets+p.total_assets)/2
    pa = (p.total_assets+b.total_assets)/2
    cra,pra = c.n_income_attr_p/ca.where(ca>0),p.n_income_attr_p/pa.where(pa>0)
    pairs = [(cra,0,'gt'),(c.n_cashflow_act,0,'gt'),(cra,pra,'gt'),
             (c.n_cashflow_act,c.n_income_attr_p,'gt'),
             (c.lt_borr/c.total_assets.where(c.total_assets>0),p.lt_borr/p.total_assets.where(p.total_assets>0),'lt'),
             (c.total_cur_assets/c.total_cur_liab.where(c.total_cur_liab>0),p.total_cur_assets/p.total_cur_liab.where(p.total_cur_liab>0),'gt'),
             (c.total_share,p.total_share,'le'),
             ((c.revenue-c.oper_cost)/c.revenue.where(c.revenue>0),(p.revenue-p.oper_cost)/p.revenue.where(p.revenue>0),'gt'),
             (c.revenue/ca.where(ca>0),p.revenue/pa.where(pa>0),'gt')]
    scores = pd.concat([getattr(a,op)(b).where(a.notna() & pd.notna(b)).astype(float)
                        for a,b,op in pairs],axis=1)
    bad = set(z.loc[z.conflict_count>1,'symbol'])
    score = scores.sum(axis=1,min_count=9).where(~scores.index.isin(bad)).rename('score').reset_index()
    db.register('market_cn_score_frame',score)
    db.execute('CREATE OR REPLACE TEMP TABLE market_cn_fscore AS SELECT * FROM market_cn_score_frame')
    db.unregister('market_cn_score_frame')


def historical_slice(rows,db):
    """Original historical eight-factor proxy, distinct from the live ten-board score."""
    end = rows.trade_date.max()
    st = set(r[0] for r in db.execute('SELECT symbol FROM market_cn_st WHERE trade_date=?',[end]).fetchall())
    f = rows.loc[(rows.list_date<=end-pd.DateOffset(years=1)) & rows.amount.gt(0) & ~rows.index.isin(st)].copy()
    f['amount_pct'] = f.amount.rank(method='min',pct=True)
    f = f.loc[f.amount_pct.ge(.05)].copy()
    f['industry'] = f.source_industry.fillna('未知行业')
    f['financial_sector'] = f.industry.str.contains('银行|非银',regex=True)
    mv,cf = f.total_mv,f.operating_cash_flow_ttm_cny
    f['pcf_ttm'] = (mv/cf).where(mv.gt(0)&cf.gt(0))
    valuation, reversal = [],[]
    for key in ('pe_ttm','pb','ps_ttm','pcf_ttm','dv_ttm','ret20','ret3y','drawdown52'):
        raw = f[key]
        if key in ('pe_ttm','pb','ps_ttm','pcf_ttm'):
            raw = raw.where(raw.gt(0))
        if key in ('ps_ttm','pcf_ttm'):
            raw = raw.mask(f.financial_sector)
        f[key+'_input'] = raw
        col = key+'_score'
        if key in ('pe_ttm','pb','ps_ttm','pcf_ttm'):
            f[col] = raw.groupby(f.industry).transform(lambda g:midrank(g,False))
        else:
            f[col] = midrank(raw,key=='dv_ttm')
        (reversal if key in ('ret20','ret3y','drawdown52') else valuation).append(col)
    f['historical_composite_proxy'] = .7*f[valuation].mean(axis=1)+.3*f[reversal].mean(axis=1)
    cols = ['trade_date','amount_pct','historical_composite_proxy',*valuation,*reversal]
    return f[cols].reset_index()


def add_time_percentiles(x,base,factors):
    from seven_desks.market_refresh import parquet
    db = x.db
    old_files = sorted((base/'contrarian/history').glob('factors_*.parquet'))[-5:]
    if (base/'revision/contrarian/daily-factors.parquet').is_file():
        old_files.append(base/'revision/contrarian/daily-factors.parquet')
    db.register('market_cn_new_values',factors[['symbol','trade_date','pe_ttm','pb']])
    files = ' UNION ALL '.join(f'SELECT symbol,trade_date,pe_ttm,pb FROM {parquet(p)}' for p in old_files)
    recent = db.execute(f"""SELECT * FROM ({files} UNION ALL SELECT * FROM market_cn_new_values)
        WHERE trade_date>=?::DATE-INTERVAL 4 YEAR ORDER BY symbol,trade_date""",
        [factors.trade_date.min()]).df()
    keys = ['symbol','trade_date']
    for col in ('pe_ttm','pb'):
        grouped = recent.groupby('symbol')[col]
        count = grouped.rolling(756,min_periods=500).count().droplevel(0)
        rank = grouped.rolling(756,min_periods=500).rank().droplevel(0)
        recent[col+'_time_pct3y'] = (rank-.5)/count*100
    rank = recent.loc[recent.trade_date>=factors.trade_date.min(),keys+['pe_ttm_time_pct3y','pb_time_pct3y']]
    db.unregister('market_cn_new_values')
    keys = ['symbol','trade_date']
    merged = factors[keys].merge(rank,on=keys,how='left',validate='one_to_one')
    for col in ('pe_ttm_time_pct3y','pb_time_pct3y'):
        factors[col] = merged[col]
