"""Rebuild market desk products from named, read-only lake inputs."""
from datetime import date, timedelta
import hashlib
import shutil
import subprocess
import json
from pathlib import Path

import numpy as np
import pandas as pd

from seven_desks.daily_inputs import literal
from seven_desks.builders import market_daily_sentiment as mood, market_daily_valuation as val
from seven_desks.builders.revision_contrarian import calculate as contrarian_temperature
from seven_desks.prepare_valuation import OFFICIAL

MARKET_PRICES = "market/ui-extra/index-prices.parquet"
INDEX_CODES = (*OFFICIAL, "000510.SH", "000852.SH", "000985.CSI", "932000.CSI",
               "000688.SH", "000922.CSI")
SYNTHETIC = tuple(code for code in INDEX_CODES if code not in OFFICIAL)


def read(path):
    f = pd.read_parquet(path)
    for key in ('trade_date', 'event_date', 'source_date'):
        if key in f:
            f[key] = pd.to_datetime(f[key])
    return f


def parquet(path):
    return f"read_parquet({literal(path)}, hive_partitioning=false, union_by_name=true)"


def write(x, rel, frame, keys, day='trade_date', data_date=None):
    if frame.duplicated(keys).any():
        raise ValueError(f'duplicate market output: {rel}, {keys}')
    if frame.empty:
        return
    frame = frame.sort_values(keys).reset_index(drop=True)
    dest = x.staging / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(dest, index=False, compression='zstd')
    info = {'rows': len(frame)}
    if day:
        info.update(first_date=frame[day].min().date().isoformat(),
                    data_date=frame[day].max().date().isoformat())
    if data_date:
        info['data_date'] = str(data_date)[:10]
    x.output[rel] = info


def write_meta(x, rel, data, day):
    dest = x.staging/rel
    dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps(data,ensure_ascii=False,sort_keys=True)+'\n')
    x.output[rel] = {'data_date':str(day)[:10]}


def watermark(values):
    last = pd.Series(values).max()
    return None if pd.isna(last) else str(last)[:10]


def component(x, day, reason=None, *, retained=False, **metadata):
    return {'data_date': day, 'status': ('retained' if retained or day is None else
                                        'current' if day == str(x.days[-1]) else 'lagged'),
            'reason': reason, **metadata}


def recent_inputs(x, since):
    """Read sufficient stock observations for the original 250-row breadth windows."""
    warm = since.date() - timedelta(days=550)
    x.view('instruments')
    x.db.execute("""CREATE OR REPLACE TEMP VIEW market_stock_identity AS
        SELECT symbol,min(list_date) list_date,max(delist_date) delist_date FROM instruments
        WHERE asset_type IN ('stock','cdr') GROUP BY symbol""")
    x.view('daily_bars', warm, day='trade_date', alias='market_bars_source')
    x.view('adj_factors', warm, layer='derived', day='trade_date', alias='market_adj_source')
    x.view('price_limits', since.date()-timedelta(days=45), day='trade_date', alias='market_limits_source')
    x.view('valuation_metrics', warm, day='trade_date', alias='market_value_source')
    x.db.execute("""CREATE OR REPLACE TEMP VIEW market_bars AS
        SELECT symbol,trade_date,min(open) AS open,min(high) AS high,min(low) AS low,
          min(close) AS close,min(volume) AS volume,min(amount) AS amount
        FROM market_bars_source JOIN market_stock_identity USING(symbol)
        WHERE isfinite(close) AND close>0 GROUP BY 1,2
        HAVING count(DISTINCT struct_pack(o:=open,h:=high,l:=low,c:=close,v:=volume,a:=amount))=1""")
    x.db.execute("""CREATE OR REPLACE TEMP VIEW market_adj AS
        SELECT symbol,trade_date,factor FROM market_adj_source
        WHERE adjust_type='hfq' AND factor>0 AND isfinite(factor)
        QUALIFY fetched_at IS NOT DISTINCT FROM max(fetched_at) OVER(PARTITION BY symbol,trade_date)""")
    x.db.execute("""CREATE OR REPLACE TEMP VIEW market_adj_unique AS
        SELECT symbol,trade_date,min(factor) factor FROM market_adj GROUP BY 1,2
        HAVING count(DISTINCT factor)=1""")
    x.db.execute("""CREATE OR REPLACE TEMP VIEW market_values AS
        SELECT symbol,trade_date,min(pe_ttm) pe_ttm,min(pb) pb,min(ps_ttm) ps_ttm,
          min(total_mv) total_mv,min(float_mv) float_mv
        FROM market_value_source JOIN market_stock_identity USING(symbol) GROUP BY 1,2
        HAVING count(DISTINCT struct_pack(pe:=pe_ttm,p:=pb,ps:=ps_ttm,m:=total_mv,f:=float_mv))=1""")
    x.db.execute("""CREATE OR REPLACE TEMP VIEW market_limits AS
        SELECT symbol,trade_date,min(upper_limit) FILTER(WHERE upper_limit>0) upper_limit,
            min(lower_limit) FILTER(WHERE lower_limit>0) lower_limit
        FROM market_limits_source GROUP BY 1,2
        HAVING count(DISTINCT struct_pack(u:=upper_limit,l:=lower_limit))=1""")


def index_prices(x, base, lookback):
    old = read(base / MARKET_PRICES)
    x.view('index_bars', lookback.date(), day='trade_date', alias='market_index_source')
    new = x.db.execute(f"""SELECT symbol,trade_date,min(open) AS open,min(high) AS high,
        min(low) AS low,min(close) AS close,min(volume) AS volume,min(amount) AS amount
        FROM market_index_source WHERE frequency='1d' AND isfinite(close) AND close>0
        AND symbol IN ({','.join(map(literal, (*INDEX_CODES, '000002.SH')))})
        GROUP BY 1,2 HAVING count(DISTINCT struct_pack(o:=open,h:=high,l:=low,c:=close,v:=volume,a:=amount))=1
        ORDER BY 1,2""").df()
    new["trade_date"] = pd.to_datetime(new.trade_date)
    new = new.loc[new.trade_date.gt(new.symbol.map(old.groupby('symbol').trade_date.max())).fillna(False)]
    result = pd.concat([old, new], ignore_index=True).sort_values(['symbol', 'trade_date'])
    if len(new):
        write(x, MARKET_PRICES, result, ['symbol', 'trade_date'])
    return result


def breadth(x, start):
    # Same windows, denominators and thresholds as seven_priority_indicators:
    # MA windows count stock observations, while one-day returns require adjacency.
    return x.db.execute(f"""
      WITH z AS (
        SELECT b.*,a.factor,close*a.factor px,high*a.factor hi,low*a.factor lo,
          l.upper_limit,l.lower_limit,
          lag(session_id) OVER w prev_id,session_id,
          lag(close*a.factor) OVER w prior_px,lag(close) OVER w prior_close,
          lag(l.upper_limit) OVER w prior_limit
        FROM market_bars b JOIN market_sessions USING(trade_date)
        LEFT JOIN market_adj_unique a USING(symbol,trade_date)
        LEFT JOIN market_limits l USING(symbol,trade_date)
        WINDOW w AS(PARTITION BY symbol ORDER BY trade_date)
      ), f AS (
        SELECT *, CASE WHEN session_id=prev_id+1 THEN px/nullif(prior_px,0)-1 END ret,
          count(px) OVER w20 n20,avg(px) OVER w20 ma20,
          count(px) OVER w60 n60,avg(px) OVER w60 ma60,
          count(px) OVER w250 n250,avg(px) OVER w250 ma250,
          count(hi) OVER prior60 nh,max(hi) OVER prior60 mh,min(lo) OVER prior60 ml
        FROM z WINDOW
          w20 AS(PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW),
          w60 AS(PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW),
          w250 AS(PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 249 PRECEDING AND CURRENT ROW),
          prior60 AS(PARTITION BY symbol ORDER BY trade_date ROWS BETWEEN 60 PRECEDING AND 1 PRECEDING)
      )
      SELECT trade_date,
        count(*) FILTER(WHERE n20=20 AND px>ma20)/nullif(count(*) FILTER(WHERE n20=20 AND px IS NOT NULL),0) d1,
        count(*) FILTER(WHERE n60=60 AND px>ma60)/nullif(count(*) FILTER(WHERE n60=60 AND px IS NOT NULL),0) d2,
        count(*) FILTER(WHERE n250=250 AND px>ma250)/nullif(count(*) FILTER(WHERE n250=250 AND px IS NOT NULL),0) d3,
        (count(*) FILTER(WHERE nh=60 AND hi>mh)-count(*) FILTER(WHERE nh=60 AND lo<ml))
          /nullif(count(*) FILTER(WHERE nh=60 AND hi IS NOT NULL),0) d4,
        sum(amount ORDER BY symbol) FILTER(WHERE ret>0)/nullif(sum(amount ORDER BY symbol),0) up_amount,
        CASE WHEN count(upper_limit)>0 THEN count(*) FILTER(WHERE close>=upper_limit-.001)
          -count(*) FILTER(WHERE close<=lower_limit+.001) END d6,
        avg((close/prior_close-1)*100 ORDER BY symbol) FILTER(WHERE session_id=prev_id+1 AND prior_close>=prior_limit-.00001) d7,
        count(*) FILTER(WHERE ret>0)/nullif(count(ret),0) advance,
        sum(amount ORDER BY symbol) amount,count(*) quote_count,count(px) adjusted_count,count(upper_limit) limit_count
      FROM f WHERE trade_date>=DATE {literal(start.date())}
      GROUP BY 1 ORDER BY 1
    """).df().set_index('trade_date')


def sources_until(x, dataset, day, start=None, layer='curated'):
    return x.view(dataset, start, day=day, layer=layer, alias='market_'+dataset)


def sentiment(x, base, prices, prior, since, end):
    db = x.db
    old_detail = read(base/'market/sentiment/indicators.parquet')
    stage_dates = pd.DatetimeIndex([d for d in x.days if since <= pd.Timestamp(d) <= end], dtype='datetime64[us]', name='trade_date')
    tail = pd.DataFrame(index=stage_dates)
    buffered = since - pd.Timedelta(days=390)
    dates = pd.DatetimeIndex(prior.index.union(stage_dates), name='trade_date')
    b = breadth(x, since-pd.Timedelta(days=45))
    # Do not pretend a broad slice exists on a day with only one provider shard.
    for col in ('d1','d2','d3','d4','up_amount','advance'):
        b[col] = b[col].where((b.quote_count >= 5000) & (b.adjusted_count >= 5000))
    b['d6'] = b.d6.where(b.limit_count >= 4000)
    b['d7'] = b.d7.where(b.quote_count >= 5000)
    b['amount'] = b.amount.where(b.quote_count >= 5000)
    sources_until(x,'seven_priority_indicators','trade_date',buffered.date(),'derived')
    priority = db.execute("""SELECT trade_date,metric_id,min(value) AS "value"
        FROM market_seven_priority_indicators WHERE metric_id IN
        ('ashare_advance_ratio','ashare_total_amount','ashare_up_amount_ratio')
        GROUP BY 1,2 HAVING count(DISTINCT value)<=1 ORDER BY 1,2""").df().pivot(
        index='trade_date',columns='metric_id',values='value')
    for id,col in [('D1','d1'),('D2','d2'),('D3','d3'),('D4','d4')]:
        tail[id] = b[col]*100
    tail['D6'], tail['D7'] = b.d6, b.d7
    event_data = pd.DataFrame(index=dates)
    for col,new in [('ashare_advance_ratio','advance'),('ashare_total_amount','amount'),('ashare_up_amount_ratio','up_amount')]:
        event_data[col] = pd.concat([priority[col].loc[lambda s:s.index < since], b[new].loc[lambda s:s.index >= since]]).reindex(dates)
    tail['D5'] = (event_data.ashare_up_amount_ratio*100).rolling(10,min_periods=10).mean()
    official = db.execute("""SELECT trade_date,min(pe_ttm) A1,min(pb) A2 FROM market_index_valuation_metrics
        WHERE symbol='000300.SH' GROUP BY 1 HAVING count(DISTINCT struct_pack(a:=pe_ttm,b:=pb))=1 ORDER BY 1""").df().set_index('trade_date')
    tail['A1'], tail['A2'] = official.A1, official.A2
    tail['A3'] = db.execute("""SELECT trade_date,median(pe_ttm) FILTER(WHERE pe_ttm>0) v
        FROM market_values GROUP BY 1 HAVING count(*)>=5000""").df().set_index('trade_date').v
    sources_until(x,'margin_exchange_daily','trade_date',buffered.date())
    margin = db.execute("""SELECT trade_date,sum(margin_balance ORDER BY exchange) bal,
        sum(margin_buy ORDER BY exchange) buy FROM (
          SELECT exchange,trade_date,min(margin_balance) margin_balance,min(margin_buy) margin_buy
          FROM market_margin_exchange_daily GROUP BY 1,2
          HAVING count(DISTINCT struct_pack(b:=margin_balance,m:=margin_buy))=1
        ) GROUP BY 1 HAVING count(*) FILTER(WHERE exchange IN ('SSE','SZSE'))=2""").df().set_index('trade_date').reindex(dates)
    shsz = db.execute("""SELECT trade_date,sum(amount ORDER BY symbol) amount,sum(float_mv ORDER BY symbol) mv
        FROM market_bars b LEFT JOIN market_values USING(symbol,trade_date)
        WHERE b.symbol ~ '.*[.](SH|SZ)$' GROUP BY 1 HAVING count(*)>=4500""").df().set_index('trade_date').reindex(dates)
    tail['B1'] = margin.bal
    tail['B3'] = (margin.bal/margin.bal.shift(20)-1)*100
    tail['B2'] = (margin.buy/shsz.amount*100).rolling(5,min_periods=5).mean()
    tail['C1'] = (shsz.amount/shsz.mv*100).rolling(5,min_periods=5).mean()
    tail['C3'] = shsz.amount.rolling(20,min_periods=20).mean()/shsz.amount.rolling(250,min_periods=250).mean()
    turn = db.execute("""SELECT trade_date,median(turnover_rate) v FROM market_research_unique
        JOIN market_stock_identity USING(symbol) GROUP BY 1""").df().set_index('trade_date').v
    tail['C2'] = turn.reindex(dates).rolling(5,min_periods=5).mean()
    return finish_sentiment(x, base, prices, prior, old_detail, tail, since, end, dates, event_data, official, b)


def finish_sentiment(x, base, prices, prior, old_detail, tail, since, end, dates, event_data, official, b):
    stage_dates = tail.index
    db = x.db
    x.view('fund_profiles', alias='market_fund_profiles')
    x.view('fund_classification_snapshot', alias='market_fund_classes')
    x.view('fund_product_share_observations', alias='market_fund_relations')
    funds = db.execute("""SELECT DISTINCT p.symbol,p.name,p.found_date FROM market_fund_profiles p
        JOIN market_fund_classes f USING(symbol) WHERE found_date IS NOT NULL AND found_date<=?
        AND classification_text IN ('股票型','混合型-偏股','混合型-灵活','混合型-偏债','指数型-股票')
        ORDER BY symbol,found_date,name""", [end.date()]).df()
    funds['family'] = funds.name.str.replace(r'[- ]?[A-HＣＡＢCE]+(?:类)?$', '', regex=True)
    launches = mood.equity_launch_counts(funds, db.execute("""SELECT DISTINCT master_code,share_code,
        announce_date,valid_from,valid_to FROM market_fund_relations""").df())
    launches = launches.loc[launches.trade_date <= end].set_index('trade_date')
    monthly = (tail.index.to_period('M')-1).to_timestamp(how='end').normalize()
    for field, col in [('E1','verified_product_count_with_unresolved_shares'),
                       ('E1_share_count','share_count'),('E1_name_approx_count','name_approx_product_count')]:
        tail[field] = launches[col].reindex(monthly).to_numpy()
    launch_rel = 'market/sentiment/fund-launch-counts-monthly.parquet'
    old_launch = read(base/launch_rel)
    new_launch = launches.loc[launches.index > old_launch.trade_date.max()]
    if len(new_launch):
        write(x, launch_rel, pd.concat([old_launch,new_launch.reset_index()]), ['trade_date'])

    sources_until(x,'etf_daily_metrics','trade_date',(since-pd.Timedelta(days=55)).date())
    x.view('etf_profiles', alias='market_etf_profiles')
    etf = db.execute(f"""WITH m AS (
        SELECT e.symbol,e.trade_date,s.session_id,min(e.shares_outstanding) shares,min(e.unit_nav) nav
        FROM market_etf_daily_metrics e JOIN market_sessions s USING(trade_date)
        WHERE EXISTS(SELECT 1 FROM market_etf_profiles p WHERE p.symbol=e.symbol
          AND p.reported_index_code IN ({','.join(map(literal, INDEX_CODES))}))
        GROUP BY 1,2,3 HAVING count(DISTINCT struct_pack(s:=shares_outstanding,n:=unit_nav))=1
      ) SELECT t.trade_date,sum((t.shares-p.shares)*t.nav ORDER BY t.symbol) v
        FROM m t JOIN m p ON t.symbol=p.symbol AND t.session_id=p.session_id+1 GROUP BY 1""").df().set_index('trade_date').v
    tail['etf_daily'] = etf
    etf_daily = pd.concat([prior.etf_daily, tail.etf_daily])
    tail['E2'] = etf_daily.rolling(20,min_periods=20).sum()
    sources_until(x,'stock_holdertrade_announcements','announce_date',(since-pd.Timedelta(days=65)).date())
    holders = db.execute("""SELECT DISTINCT event_id,announce_date,trade_type,change_vol,avg_price
        FROM market_stock_holdertrade_announcements ORDER BY announce_date,event_id""").df()
    cap_rel = 'market/sentiment/capital-announcement-daily.parquet'
    old_cap = read(base/cap_rel).set_index('trade_date')
    cap = mood.capital_announcement_daily(holders, dates[dates>=since-pd.Timedelta(days=65)])
    cap = pd.concat([old_cap.loc[old_cap.index<since], cap.loc[cap.index>=since]])
    for col, key in [('net_amount','E3'),('known_net_amount','E3_known_net_20d'),
                     ('event_count','E3_event_count_20d'),('valid_amount_count','E3_valid_amount_count_20d')]:
        tail[key] = cap[col].rolling(20,min_periods=20).sum()
    tail['E3_coverage_ratio'] = (tail.E3_valid_amount_count_20d/tail.E3_event_count_20d).where(
        tail.E3_event_count_20d > 0, 1).where(tail.E3_event_count_20d.notna())
    write(x, cap_rel, cap.reset_index(), ['trade_date'])
    x.view('fund_asset_allocation_snapshot', alias='market_fund_alloc')
    allocation = db.execute("""SELECT report_period+INTERVAL 26 DAY available,
        avg(stock_nav_ratio_pct ORDER BY a.symbol) v FROM market_fund_alloc a WHERE EXISTS(
          SELECT 1 FROM market_fund_classes f WHERE f.symbol=a.symbol AND classification_text='混合型-偏股')
        GROUP BY 1 ORDER BY 1""").df().set_index('available').v
    alloc = pd.merge_asof(pd.DataFrame({'trade_date':stage_dates}), allocation.reset_index(),
                         left_on='trade_date', right_on='available').set_index('trade_date')
    tail['E4'] = alloc.v
    for code, key in [('000002.SH','F1'),('000300.SH','F2')]:
        p = prices.loc[prices.symbol.eq(code)].set_index('trade_date').close
        if key=='F1':
            p = (p/p.rolling(250,min_periods=250).mean()-1)*100
        else:
            p = p.pct_change(fill_method=None).clip(upper=0).rolling(20,min_periods=20).std(ddof=1)*np.sqrt(252)*100
        tail[key] = p
    sources_until(x,'macro_indicators','obs_date',(since-pd.Timedelta(days=400)).date())
    macro = db.execute("""SELECT obs_date,indicator_id,source,min(value) v FROM market_macro_indicators
        WHERE isfinite(value) GROUP BY 1,2,3 HAVING count(DISTINCT value)=1 ORDER BY 1""").df()
    vix = macro.loc[macro.indicator_id.eq('VIXCLS') & macro.source.eq('fred.VIXCLS')]
    mapped = pd.merge_asof(pd.DataFrame({'trade_date':stage_dates}),vix,
                            left_on='trade_date',right_on='obs_date',allow_exact_matches=False).set_index('trade_date')
    tail['F3'], tail['vix_source_date'] = mapped.v, mapped.obs_date

    raw = pd.concat([prior, tail])
    sources = {}
    for key, *_ in mood.SPECS:
        s = pd.Series(tail.index.where(tail[key].notna()), index=tail.index)
        if key in ('B1','B2','B3','D1','D2','D3','D4','D5','D6','D7','E2'):
            # A carried value consumes the original three-session budget across runs.
            recent = old_detail.loc[old_detail.indicator_id.eq(key)].set_index('trade_date')
            observed = recent.raw_value.where(recent.source_date.eq(recent.index))
            values = pd.concat([observed, tail[key]]).ffill(limit=3)
            raw.loc[stage_dates,key] = values.reindex(stage_dates)
            s = pd.concat([recent.source_date.where(recent.source_date.eq(recent.index)),s]).ffill(limit=3).reindex(stage_dates)
        if key=='E1':
            s = pd.Series(monthly,index=stage_dates)
        elif key=='E4':
            s = alloc.available
        elif key=='F3':
            s = mapped.obs_date
        sources[key] = s.reindex(raw.index)
    before = official.index < raw.index.min()
    prealloc_days = prices.loc[(prices.symbol=='000300.SH') & (prices.trade_date<raw.index.min()),'trade_date']
    prealloc = allocation.reindex(prealloc_days).combine_first(allocation).sort_index().ffill().reindex(prealloc_days)
    history = {'A1':official.A1[before], 'A2':official.A2[before], 'E4':prealloc}
    scored, details = mood.score(raw.copy(), history, since, sources)
    together = pd.concat([prior.reset_index(), scored.loc[scored.trade_date>=since]], ignore_index=True)
    write(x, 'market/sentiment/daily.parquet', together, ['trade_date'])
    write(x, 'market/sentiment/indicators.parquet', pd.concat([old_detail,details]), ['trade_date','indicator_id'])
    sentiment_events(x, base, prices, together, event_data, since)
    sentiment_display(x, base, stage_dates, official, macro)
    dates_by_indicator = {s:watermark(g.loc[g.raw_value.notna(),'source_date'])
                          for s,g in pd.concat([old_detail,details]).groupby('indicator_id')}
    usable = together.loc[together.temperature.notna()]
    latest = usable.iloc[-1]
    return component(x, watermark(usable.trade_date),
        '按原规则保留缺项、三日限填和至少四维门槛' if latest.dimension_count < 6 or details.raw_value.isna().any() else None,
        indicator_dates=dates_by_indicator, calculated_through=str(end.date()),
        valid_dimensions=int(latest.dimension_count),
        broad_price_dates=b.loc[(b.index>=since)&(b.quote_count>=5000)].index.strftime('%Y-%m-%d').tolist())


def sentiment_events(x, base, prices, daily, event_data, since):
    rel = 'market/sentiment/events.parquet'
    old = read(base/rel)
    closes = prices.pivot(index='trade_date',columns='symbol',values='close').reindex(daily.trade_date)
    flags = mood.event_flags(daily, event_data, closes)
    records = []
    for name, flags in flags.items():
        prior = old.loc[old.event_type.eq(name),'event_date']
        last = daily.trade_date.searchsorted(prior.max()) if len(prior) else -10**9
        for i in np.flatnonzero(pd.Series(flags).fillna(False).to_numpy() & daily.trade_date.ge(since).to_numpy()):
            if i-last < 10:
                continue
            last = i
            for code in ('000002.SH','000300.SH'):
                records.append(dict(dashboard='sentiment', event_type=name, entity_id=code,
                                    event_date=daily.trade_date.iloc[i],quality_status='approximate',
                                    formula_version=mood.VERSION))
    out = pd.concat([old,pd.DataFrame(records)],ignore_index=True)
    for code in ('000002.SH','000300.SH'):
        sel = out.entity_id.eq(code)
        for h in (5,20,60,120):
            px = closes[code]
            out.loc[sel,f'return_{h}d'] = (px.shift(-h)/px-1).reindex(out.loc[sel,'event_date']).to_numpy()
    write(x, rel, out, ['event_date','entity_id','event_type'], day='event_date')


def sentiment_display(x, base, days, official, macro):
    rel = 'market/ui-extra/sentiment-display.parquet'
    old = read(base/rel)
    pairs = macro.loc[((macro.indicator_id=='VIXCLS') & (macro.source=='fred.VIXCLS')) |
                      ((macro.indicator_id=='VIX3M') & (macro.source=='cboe.VIX3M'))].pivot(
                      index='obs_date',columns='indicator_id',values='v')
    rows = []
    definitions = old.groupby('indicator_id').tail(1).set_index('indicator_id')
    for day in days:
        for key in ('PMI','VIX','HS300_PE20','VIX_TERM'):
            r = definitions.loc[key].to_dict()
            r.update(trade_date=day, indicator_id=key, source_date=pd.NaT, raw_value=np.nan)
            if key in ('PMI','VIX'):
                code = 'pmi_manufacturing' if key=='PMI' else 'VIXCLS'
                eligible = macro.loc[macro.indicator_id.eq(code) &
                    (macro.obs_date.le(day) if key=='PMI' else macro.obs_date.lt(day))]
                if len(eligible):
                    row = eligible.iloc[-1]
                    r.update(source_date=row.obs_date,raw_value=row.v,missing_reason=None)
            elif key=='VIX_TERM':
                before = pairs.loc[pairs.index<day]
                row = before.iloc[-1] if len(before) else pd.Series(dtype=float)
                ratio = row.get('VIX3M',np.nan)/row.get('VIXCLS',np.nan)
                r.update(source_date=row.name,raw_value=ratio,source='Cboe VIX3M / FRED VIXCLS', unit='倍',
                         availability='两指数同一美股日期，严格早于A股观察日；仅展示',
                         missing_reason=None if np.isfinite(ratio) else '前一个美股观测日缺同日两期配对')
            else:
                sample = official.loc[(official.index>day-pd.DateOffset(years=20)) & (official.index<=day),'A1']
                value = official.A1.get(day,np.nan)
                rank = mood.midrank(sample,value,4032)
                r.update(source_date=day if np.isfinite(value) else pd.NaT,raw_value=rank,
                         missing_reason=None if np.isfinite(rank) else f'当日正式PE或4032个有效日样本不足（{sample.count()}）')
            rows.append(r)
    write(x, rel, pd.concat([old,pd.DataFrame(rows)]), ['trade_date','indicator_id'])


def _refresh(x, base_data):
    """Use an existing DailyInputs after calendar and the shared basic refresh."""
    base = base_data
    output_before = set(x.output)
    prior = read(base/'market/sentiment/daily.parquet').set_index('trade_date')
    end = pd.Timestamp(x.days[-1])
    if end < prior.index.max():
        raise ValueError('不能把市场研究历史回退到更早交易日')
    since = prior.index.max()+pd.Timedelta(days=1)
    calc_since = min(since, end-pd.Timedelta(days=45))
    recent_inputs(x, calc_since)
    x.view('trading_calendar', (calc_since-pd.Timedelta(days=560)).date(),
           day='trade_date', alias='market_calendar_source')
    x.db.execute("""CREATE OR REPLACE TEMP TABLE market_sessions AS SELECT trade_date,
        row_number() OVER(ORDER BY trade_date) session_id FROM (
            SELECT DISTINCT trade_date FROM market_calendar_source WHERE is_trading)""")
    sources_until(x,'stock_daily_research_inputs','trade_date',(calc_since-pd.Timedelta(days=60)).date())
    sources_until(x,'index_valuation_metrics','trade_date')
    x.db.execute('''CREATE OR REPLACE TEMP VIEW market_research_unique AS
        SELECT symbol,trade_date,min(dv_ttm) dv_ttm,min(turnover_rate) turnover_rate
        FROM market_stock_daily_research_inputs GROUP BY 1,2
        HAVING count(DISTINCT struct_pack(d:=dv_ttm,t:=turnover_rate))=1''')
    prices = index_prices(x, base, calc_since-pd.Timedelta(days=45))
    meta = base.parent/'manifest.json'
    prior_boards = json.loads(meta.read_text()).get('daily_boards',{}) if meta.is_file() else {}
    boards = {b:{k:prior_boards[b][k] for k in keys if k in prior_boards.get(b,{})}
              for b,keys in {'sentiment':('temperature',), 'contrarian':('temperature','factor_boards'),
                             'valuation':('official_index_pe_pb','dividends_and_historical_statistics')}.items()
              if b in prior_boards}
    if since <= end:
        boards['sentiment'] = {'temperature':sentiment(x,base,prices,prior,since,end)}
    from seven_desks.builders.market_daily_chips import chips
    boards['chips'] = {'cost_and_signals':chips(x,base,prices,prior_boards.get('chips',{}).get('cost_and_signals'))}
    daily = read(x.staging/'market/sentiment/daily.parquet' if 'market/sentiment/daily.parquet' in x.output
                 else base/'market/sentiment/daily.parquet')
    rel = 'revision/contrarian/temperature.parquet'
    current_temp = read(base/rel)
    if daily.trade_date.max() > current_temp.trade_date.max():
        temperatures, details = contrarian_temperature(daily,prices.loc[prices.symbol.eq('000985.CSI')])
        for name, frame, keys in [(rel,temperatures,['trade_date']),
            ('revision/contrarian/temperature-items.parquet',details,['trade_date','indicator_id'])]:
            old = read(base/name)
            write(x,name,pd.concat([old,frame.loc[frame.trade_date>old.trade_date.max()]]),keys)
        effective = temperatures.loc[temperatures.temperature.notna()]
        boards.setdefault('contrarian',{})['temperature'] = component(x,watermark(effective.trade_date),
            '六项原口径重算；中证全指缺数时P3留空，两组各至少两项',
            calculated_through=watermark(temperatures.trade_date),
            benchmark_date=watermark(prices.loc[prices.symbol.eq('000985.CSI'),'trade_date']))
    from seven_desks.builders.market_daily_contrarian import refresh_boards
    factor_board = refresh_boards(x,base)
    if factor_board:
        boards.setdefault('contrarian',{})['factor_boards'] = factor_board
    valuation_boards, official_cutoff = valuation(x,base,prices)
    if valuation_boards:
        boards['valuation'] = valuation_boards
    return {'boards':boards,'outputs':sorted(set(x.output)-output_before),
            'valuation_cutoff':official_cutoff}


def valuation(x, base, prices):
    db = x.db
    root = 'market/valuation/'
    panel = read(base/(root+'daily.parquet'))
    previous = read(base/(root+'synthetic-'+str(x.days[-1].year)+'.parquet')) if (
        base/(root+'synthetic-'+str(x.days[-1].year)+'.parquet')).exists() else None
    manifest_dates = panel.loc[panel.dividend_source.notna(),'trade_date']
    last = manifest_dates.max()
    since = last+pd.Timedelta(days=1)
    end = pd.Timestamp(x.days[-1])
    old_counts = panel.loc[panel.index_symbol.isin(OFFICIAL) & panel.pe_ttm.notna() & panel.pb.notna()
                           ].groupby('trade_date').index_symbol.nunique()
    old_official = watermark(old_counts.loc[old_counts.eq(len(OFFICIAL))].index)
    results = {}
    pending = db.execute(f"""WITH available AS (
      SELECT symbol,trade_date FROM market_index_valuation_metrics
      WHERE symbol IN ({','.join(map(literal, OFFICIAL))}) AND trade_date>?
         AND isfinite(pe_ttm) AND isfinite(pb) AND pe_ttm>0 AND pb>0
      GROUP BY 1,2 HAVING count(DISTINCT struct_pack(a:=pe_ttm,b:=pb))=1
    ), full_days AS (SELECT trade_date FROM available GROUP BY 1 HAVING count(*)={len(OFFICIAL)})
    SELECT min(trade_date) FROM full_days""", [old_official]).fetchone()[0]
    if pending is not None:
        since = min(since,pd.Timestamp(pending))
    if since > end:
        return results, old_official

    sources_until(x,'index_constituents','as_of_date')
    sources_until(x,'stock_financial_ttm_events','as_of_date')
    sources_until(x,'financial_statement_items','announce_date')
    sources_until(x,'dividend_cash','ex_date', (since-pd.Timedelta(days=370)).date(),'derived')
    # Use the original dividend_cash derivation on the underlying formal events for
    # the unpublished tail. The source priority is from build_dividend_yield_table.py.
    div_last = db.execute('SELECT max(ex_date) FROM market_dividend_cash').fetchone()[0]
    sources_until(x,'corporate_actions','ex_date',div_last)
    db.execute(f"""CREATE OR REPLACE TEMP TABLE market_div_cumulative AS
      WITH history AS (
        SELECT symbol,ex_date,min(cash_dividend) cash_dividend FROM market_dividend_cash
        GROUP BY 1,2 HAVING count(DISTINCT cash_dividend)=1
      ), additions AS (
        SELECT symbol,ex_date,cash_dividend FROM market_corporate_actions
        WHERE action_type='cash_dividend' AND isfinite(cash_dividend) AND cash_dividend>0
            AND ex_date>DATE {literal(div_last)}
        QUALIFY row_number() OVER(PARTITION BY symbol,ex_date ORDER BY
            CASE source WHEN 'tushare.dividend' THEN 0 WHEN 'eastmoney' THEN 1
            WHEN 'tdx_protocol' THEN 2 ELSE 9 END,
            fetched_at DESC NULLS LAST,source,cash_dividend)=1
      )
      SELECT symbol,ex_date,sum(cash_dividend) OVER(PARTITION BY symbol ORDER BY ex_date) cash_cumulative
      FROM (SELECT * FROM history UNION ALL SELECT * FROM additions)""")
    db.execute(f"""CREATE OR REPLACE TEMP TABLE market_members AS
      SELECT index_symbol,as_of_date,symbol,min(weight) weight FROM market_index_constituents
      WHERE index_symbol IN ({','.join(map(literal, INDEX_CODES))}) AND weight>=0
      GROUP BY 1,2,3 HAVING count(DISTINCT weight)=1""")
    db.execute(f"""CREATE OR REPLACE TEMP TABLE market_index_calendar AS
      WITH snapshots AS (
        SELECT index_symbol,as_of_date,sum(weight ORDER BY symbol) weight_sum,count(*) members
        FROM market_members GROUP BY 1,2 HAVING sum(weight)>0 AND count(*)>=20
      ), grid AS (
        SELECT unnest([{','.join(map(literal, INDEX_CODES))}]) index_symbol
      )
      SELECT g.index_symbol,t.trade_date,s.as_of_date snapshot_date,s.weight_sum,s.members
      FROM (grid g CROSS JOIN market_sessions t) ASOF LEFT JOIN snapshots s
      ON g.index_symbol=s.index_symbol AND t.trade_date>=s.as_of_date
      WHERE t.trade_date>=DATE {literal(since.date())}""")
    db.execute("""CREATE OR REPLACE TEMP TABLE market_earnings AS
      SELECT symbol,as_of_date,value_cny,quality_status FROM market_stock_financial_ttm_events
      WHERE metric='parent_net_profit_ttm'
      QUALIFY row_number() OVER(PARTITION BY symbol,as_of_date ORDER BY report_period DESC,
        fetched_at DESC NULLS LAST,input_fingerprint)=1""")
    db.execute("""CREATE OR REPLACE TEMP TABLE market_equity AS
      WITH events AS (
        SELECT symbol,report_period,announce_date,min(item_value) eq_value
        FROM market_financial_statement_items
        WHERE statement_type='balance' AND item_code='parent_equity' AND item_value IS NOT NULL
        GROUP BY 1,2,3 HAVING count(DISTINCT item_value)=1
      ), newest AS (
        SELECT *, max(report_period) OVER(PARTITION BY symbol ORDER BY announce_date) latest_period FROM events
      )
      SELECT symbol,announce_date,eq_value FROM newest WHERE report_period=latest_period
      QUALIFY row_number() OVER(PARTITION BY symbol,announce_date ORDER BY report_period DESC)=1""")
    synth = db.execute(val.synthetic_sql(since.date(),end.date())).df()
    if len(synth):
        for year, g in synth.groupby(synth.trade_date.dt.year):
            rel = root+f'synthetic-{year}.parquet'
            old = previous if year == end.year else read(base/rel) if (base/rel).exists() else None
            write(x,rel,pd.concat([old.loc[old.trade_date<since] if old is not None else None,g],
                                  ignore_index=True),['index_symbol','trade_date'])

    official = db.execute(f"""SELECT symbol index_symbol,trade_date,min(pe_ttm) official_pe,min(pb) official_pb
        FROM market_index_valuation_metrics WHERE symbol IN ({','.join(map(literal, INDEX_CODES))})
        GROUP BY 1,2 HAVING count(DISTINCT struct_pack(p:=pe_ttm,b:=pb))=1 ORDER BY 1,2""").df()
    for key in ('official_pe','official_pb'):
        official[key] = official[key].where(official[key]>0)
    grid = db.execute('SELECT index_symbol,trade_date FROM market_index_calendar ORDER BY 1,2').df()
    new = grid.merge(synth,on=['index_symbol','trade_date'],how='left',validate='one_to_one').merge(
        official,on=['index_symbol','trade_date'],how='left',validate='one_to_one')
    new['synthetic_pe_ttm'],new['synthetic_pb'] = new.pe_ttm,new.pb
    official_mask = new.index_symbol.isin(OFFICIAL)
    for key, source, synthetic in [('pe_ttm','official_pe','synthetic.total_mv_parent_profit_ttm'),
                                   ('pb','official_pb','synthetic.total_mv_parent_equity')]:
        new.loc[official_mask,key] = new.loc[official_mask,source]
        new[key.split('_')[0]+'_source'] = np.where(official_mask,'official.index_valuation_metrics',synthetic)
    new['dividend_source'] = 'synthetic.dividend_cash_trailing12m_over_close_mv_weighted'
    weeks = pd.DatetimeIndex(pd.concat([panel.trade_date,official.trade_date,grid.trade_date]).unique()).sort_values()
    weeks = weeks.to_series().groupby(weeks.to_period('W-FRI')).max()
    pieces = []
    for code,g in new.groupby('index_symbol'):
        old = panel.loc[panel.index_symbol.eq(code)&panel.trade_date.lt(since)]
        history = pd.concat([old,g],ignore_index=True).set_index('trade_date').sort_index()
        history.attrs['new_dates'] = list(g.trade_date)
        for key, official_key in [('pe_ttm','official_pe'),('pb','official_pb'),('dividend_yield',None)]:
            if code in OFFICIAL and official_key:
                warm = official.loc[official.index_symbol.eq(code)&official.trade_date.lt(history.index.min())].set_index('trade_date')[[official_key]]
                joined = pd.concat([warm.rename(columns={official_key:key}),history])
                joined.attrs['new_dates'] = history.attrs['new_dates']
                val.weekly_statistics(joined,joined.index,weeks,key)
                keys = [k for k in joined.columns if k.startswith(key+'_')]
                history.loc[g.trade_date,keys] = joined.loc[g.trade_date,keys]
            else:
                val.weekly_statistics(history,history.index,weeks,key)
        pieces.append(history.reset_index().loc[lambda f:f.trade_date>=since])
    new = pd.concat(pieces,ignore_index=True)
    panel = pd.concat([panel.loc[panel.trade_date<since],new],ignore_index=True)
    write(x,root+'daily.parquet',panel,['index_symbol','trade_date'])
    dv_date = watermark(panel.loc[panel.dividend_yield.notna(),'trade_date'])
    index_cols = ['index_symbol','trade_date','pe_ttm_pct_5y','pb_pct_5y','dividend_yield_pct_5y']
    turnover = prices.sort_values(['symbol','trade_date']).copy()
    turnover['amount20'] = turnover.groupby('symbol').amount.transform(lambda a:a.rolling(20,min_periods=1).mean())
    maps = new[index_cols].merge(turnover[['symbol','trade_date','amount20']].rename(columns={'symbol':'index_symbol'}),
                                how='left',on=['index_symbol','trade_date'])
    old = read(base/(root+'map-daily.parquet'))
    write(x,root+'map-daily.parquet',pd.concat([old.loc[old.trade_date<since],maps]),['index_symbol','trade_date'])

    pivot = new.pivot(index='trade_date',columns='index_symbol',values='pe_ttm')
    ratios = pd.DataFrame({'small_large_pe_ratio':pivot['000852.SH']/pivot['000300.SH'],
                           'growth_value_pe_ratio':pivot['399006.SZ']/pivot['000016.SH']}).reset_index()
    old = read(base/(root+'relative-valuations.parquet'))
    write(x,root+'relative-valuations.parquet',pd.concat([old.loc[old.trade_date<since],ratios]),['trade_date'])
    structure = read(base/(root+'stock-structure.parquet'))
    ashare = db.execute("""SELECT trade_date,median(pe_ttm) FILTER(WHERE pe_ttm>0) positive_pe_median
           FROM market_values GROUP BY 1 HAVING count(*)>=5000 ORDER BY 1""").df()
    ashare = pd.concat([structure[['trade_date','positive_pe_median']],ashare]).drop_duplicates('trade_date',keep='last')
    ashare = ashare.set_index('trade_date').sort_index()
    ashare.attrs['new_dates'] = list(new.trade_date.unique())
    val.weekly_statistics(ashare,ashare.index,weeks,'positive_pe_median',(5,),channels=False)
    temp = new.loc[new.index_symbol.eq('000300.SH')].copy()
    temp['ashare_pe_pct_5y'] = temp.trade_date.map(ashare.positive_pe_median_pct_5y)
    temp['inverse_dividend_pct_5y'] = 100-temp.dividend_yield_pct_5y
    parts = temp[['pe_ttm_pct_5y','pb_pct_5y','ashare_pe_pct_5y','inverse_dividend_pct_5y']]
    temp['sample_count'] = parts.count(axis=1)
    temp['temperature'] = parts.mean(axis=1).where(temp.sample_count>=3)
    old = read(base/(root+'temperature.parquet'))
    write(x,root+'temperature.parquet',pd.concat([old.loc[old.trade_date<since],temp]),['trade_date'])

    monthly, stats, annual = val.returns(panel,prices,end)
    for name, frame, keys, day in [('monthly-return-samples',monthly,['index_symbol','trade_date'],'trade_date'),
          ('annual-return-decomposition',annual,['index_symbol','trade_date'],'trade_date'),
          ('return-statistics',stats,['index_symbol','band','horizon_months'],None)]:
        write(x,root+name+'.parquet',frame,keys,day,data_date=end.date() if day is None else None)
    sw_latest = industry_valuation(x,base,since)
    official_days = panel.loc[panel.index_symbol.isin(OFFICIAL)&panel.pe_ttm.notna()&panel.pb.notna()
                              ].groupby('trade_date').index_symbol.nunique()
    official_cutoff = watermark(official_days.loc[official_days.eq(len(OFFICIAL))].index)
    results['official_index_pe_pb'] = component(x,official_cutoff,'读取同日发布的六个正式指数PE/PB')
    results['dividends_and_historical_statistics'] = component(x,dv_date,
        '股息按除权日近12个月现金派息、市值加权和95%覆盖重算；历史收益只取完整月',
        synthetic_date=watermark(panel.loc[panel.index_symbol.isin(SYNTHETIC)&
            (panel.pe_ttm.notna()|panel.pb.notna()),'trade_date']), industry_date=sw_latest,
        corporate_action_date=watermark([db.execute('SELECT max(ex_date) FROM market_div_cumulative').fetchone()[0]]),
        last_complete_month=watermark(monthly.loc[monthly.month_complete,'trade_date']))
    write_meta(x,'revision/market/valuation-daily-rebuilt.json',results,end)
    return results, official_cutoff


def industry_valuation(x, base, since):
    rel = 'market/valuation/industry-static-valuations.parquet'
    old = read(base/rel)
    sources_until(x,'sw_daily','trade_date',(since-pd.DateOffset(years=3,days=10)).date())
    x.view('industry_taxonomy',alias='market_taxonomy')
    new = x.db.execute("""SELECT d.index_symbol,d.trade_date,min(d.industry_name) industry_name,
         min(pe) static_pe,min(pb) pb FROM market_sw_daily d
      WHERE EXISTS(SELECT 1 FROM market_taxonomy t WHERE t.index_symbol=d.index_symbol
        AND t.classification_system='SW2021' AND t.level='L1')
      GROUP BY 1,2 HAVING count(DISTINCT struct_pack(p:=pe,b:=pb))=1 ORDER BY 1,2""").df()
    new['static_pe'] = new.static_pe.where(new.static_pe>0)
    parts = []
    for _,g in new.groupby('index_symbol'):
        g = g.set_index('trade_date').sort_index()
        dates = g.index
        weeks = dates.to_series().groupby(dates.to_period('W-FRI')).max()
        for day in dates[dates > old.trade_date.max()]:
            sample = g.static_pe.loc[dates.isin(weeks)&(dates>day-pd.DateOffset(years=3))&(dates<=day)]
            g.at[day,'sample_count'] = sample.count()
            g.at[day,'static_pe_pct_3y'] = mood.midrank(sample,g.at[day,'static_pe'],125)
        parts.append(g.reset_index().loc[lambda f:f.trade_date>old.trade_date.max()])
    updates = pd.concat(parts,ignore_index=True)
    if len(updates):
        write(x,rel,pd.concat([old,updates]),['index_symbol','trade_date'])
    return watermark(pd.concat([old,updates]).trade_date)


REPLAY = 'revision/market/replay'
REPLAY_STATE = REPLAY+'/state.json'


class ReplayBase:
    """Resolve only this extension's changed files against its original inputs."""
    def __init__(self, current, state):
        self.current = current
        self.originals = set(state['originals'])
        self.parent = current/REPLAY/'base'

    def __truediv__(self, rel):
        rel = str(rel)
        if rel in self.originals:
            return self.current/REPLAY/'base/data'/rel
        return self.current/rel


def _clone(source, destination):
    destination.parent.mkdir(parents=True,exist_ok=True)
    try:
        subprocess.run(['cp','-c',str(source),str(destination)],check=True,capture_output=True)
    except subprocess.CalledProcessError:
        shutil.copyfile(source,destination)


def _builder_hash():
    root = Path(__file__).parent
    files = [Path(__file__), root/'daily_inputs.py', root/'prepare_valuation.py',root/'builders/revision_contrarian.py',
             root/'builders/revision_market.py',*sorted((root/'builders').glob('market_daily_*.py'))]
    return hashlib.sha256(b''.join(p.name.encode()+p.read_bytes() for p in files)).hexdigest()


def _fingerprint(inputs):
    return hashlib.sha256(json.dumps(inputs,sort_keys=True).encode()).hexdigest()



def _original_market_base(base):
    """Bootstrap older published candidates that predate replay receipts."""
    selected = base
    seen = set()
    marker = 'revision/market/valuation-daily-rebuilt.json'
    while (selected/marker).is_file() and not (selected/REPLAY_STATE).is_file():
        meta = selected.parent/'manifest.json'
        manifest = json.loads(meta.read_text()) if meta.is_file() else {}
        previous = manifest.get('base_version')
        if not previous or previous in seen or len(previous)!=16 or any(c not in '0123456789abcdef' for c in previous):
            raise ValueError('已发布市场候选缺少可追溯原母版；请保留其 base_version 版本用于一次性建立重放种子')
        seen.add(previous)
        selected = selected.parent.parent/previous/'data'
        if not selected.is_dir():
            raise ValueError('市场修订需要原母版 '+previous+'；本次候选不具备可用的重放种子')
    return selected


def refresh(x, base_data):
    """Replay the original incremental tail when its bounded formal inputs change.

    The anchor travels with the version. It never points back to a runtime path.
    Unchanged source ranges avoid all market calculations and output rewrites.
    """
    base = Path(base_data)
    state_file = base/REPLAY_STATE
    state = json.loads(state_file.read_text()) if state_file.is_file() else None
    if state and date.fromisoformat(state['target'])>x.days[-1]:
        raise ValueError('不能把已发布市场候选回退到更早交易日')
    code = _builder_hash()
    view = x.view
    requests = {}
    market_files = {}

    def tracked(dataset,start=None,end=None,*,layer='curated',alias=None,day=None):
        before = x.files
        # Capture only this request's enumerated files, including files the shared
        # runner already registered. Restore the shared evidence after the call.
        registered = {}
        x.files = registered
        try:
            available = view(dataset,start,end,layer=layer,alias=alias,day=day)
        finally:
            x.files = before
            before.update(registered)
        request = dict(dataset=dataset,start=str(start) if start else None,
                       end=str(end) if end else None,layer=layer,day=day)
        requests[json.dumps(request,sort_keys=True)] = request
        market_files.update({str(p.relative_to(x.root)):[*stat] for p,stat in registered.items()})
        return available

    if state:
        for i,request in enumerate(state['requests']):
            args = {**request,'start':date.fromisoformat(request['start']) if request['start'] else None,
                    'end':date.fromisoformat(request['end']) if request['end'] else None,
                    'alias':'market_replay_probe_'+str(i)}
            tracked(**args)
        if (state['target']==str(x.days[-1]) and state['code']==code
                and state['fingerprint']==_fingerprint(market_files)):
            return {**state['result'],'outputs':[]}
        # Fresh requests will record the actual new bound/warmup used this run.
        requests.clear()
        market_files.clear()

    original_base = ReplayBase(base,state) if state else _original_market_base(base)
    previous_outputs = dict(x.output)
    x.view = tracked
    try:
        result = _refresh(x,original_base)
        x.verify()
    finally:
        x.view = view
    # Snapshot only files this extension overwrites; untouched long histories
    # stay inherited in the ordinary version. Explicit absence prevents a future
    # run from treating its own generated factors as original inputs.
    original_files = dict(state['originals']) if state else {}
    owned = sorted(rel for rel in x.output if (
        rel.startswith(('market/','revision/contrarian/','revision/market/')) and
        not rel.startswith(REPLAY+'/')))
    result['outputs'] = owned
    for rel in owned:
        if rel not in original_files:
            original = original_base/rel
            original_files[rel] = original.is_file()
            if original.is_file():
                target = x.staging/REPLAY/'base/data'/rel
                _clone(original,target)
                x.output[str(target.relative_to(x.staging))] = {'data_date':str(x.days[-1])}
    if not state:
        meta = original_base.parent/'manifest.json'
        dest = x.staging/REPLAY/'base/manifest.json'
        dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_text(meta.read_text() if meta.is_file() else '{}')
        x.output[str(dest.relative_to(x.staging))] = {'data_date':str(x.days[-1])}
    # If a family loses all current samples, restore its original file instead
    # of inheriting stale previously generated results.
    if state:
        for rel in state['result']['outputs']:
            if rel not in x.output:
                original = original_base/rel
                (x.staging/rel).parent.mkdir(parents=True,exist_ok=True)
                if original.is_file():
                    _clone(original,x.staging/rel)
                elif rel.endswith('.parquet'):
                    read(base/rel).iloc[:0].to_parquet(x.staging/rel,index=False)
                else:
                    (x.staging/rel).parent.mkdir(parents=True,exist_ok=True)
                    (x.staging/rel).write_text('{}\n')
                x.output[rel] = {'data_date':str(x.days[-1])}
    owned_outputs = sorted(set(result['outputs']) | (set(x.output)-set(previous_outputs)))
    replay_result = {**result,'outputs':[r for r in owned_outputs if not r.startswith(REPLAY+'/')]}
    new_state = dict(schema=1,target=str(x.days[-1]),code=code,
                     requests=sorted(requests.values(),key=lambda r:json.dumps(r,sort_keys=True)),
                     inputs=dict(sorted(market_files.items())),fingerprint=_fingerprint(market_files),
                     fingerprint_kind='relative_path-size-mtime_ns',
                     originals=original_files,result=replay_result)
    write_meta(x,REPLAY_STATE,new_state,x.days[-1])
    return {**result,'outputs':sorted(set(owned_outputs)|{REPLAY_STATE})}
