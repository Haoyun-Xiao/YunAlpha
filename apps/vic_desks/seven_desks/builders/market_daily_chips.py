"""Incremental native cost profiles with original market-session cooldown."""
import numpy as np
import pandas as pd
from seven_desks.builders.market_daily_sentiment import rolling_midrank
from seven_desks.builders.revision_market import POSITION_QUERY

def apply_chip_cooldown(frame, state):
    """Re-evaluate raw rules and continue accepted-event state across partitions."""
    f=frame.copy()
    rules=chip_rules(f)
    for name,mask in rules.items():
        mask=mask&(f.listing_sessions>=60)&(f.prev_seq==f.seq-1)
        if name in ['low_dense','high_div']:mask=mask&(f.history_count>=200)
        accepted=np.zeros(len(f),dtype=bool)
        mask_values=mask.fillna(False).to_numpy();sequences=f.seq.to_numpy(dtype=np.int64)
        for symbol,idx in f.groupby('symbol',sort=False).indices.items():
            last=state.get((symbol,name),-10**9)
            # Checkpoint rows are sorted explicitly; input physical order is irrelevant.
            idx=idx[mask_values[idx]]
            idx=idx[np.argsort(sequences[idx],kind='stable')]
            for i in idx:
                seq=int(sequences[i])
                if seq-last>=10:
                    accepted[i]=True;last=seq
            state[(symbol,name)]=last
        f[name]=accepted
    return f


def chip_rules(f):
    """Explicit equality boundaries; breakout conditions use yesterday's winner."""
    return {
    'break_up':(f.close>f.prev95)&(f.prev_close<=f.prev95)&(f.prev_con90<15)&(f.prev_wr<85),
    'low_dense':(f.concentration90<12)&(f.cost_deviation.abs()<=5)&(f.position_1y<=30),
    'washout':(f.winner_rate<3)&(f.wr20>30),
    'release':(f.winner_rate>=70)&(f.wr10<20),
    'all_profit':(f.winner_rate>97)&(f.wr20<60),
    'high_div':(f.concentration90>45)&(f.winner_rate>80)&(f.position_1y>=80),
    'winner_drop':(f.wr5>70)&(f.wr5-f.winner_rate>=50),
    'break_down':(f.close<f.prev5)&(f.prev_close>=f.prev5)&(f.prev_con90<15)&(f.prev_wr>15)}


ROOT = 'market/chips/'
SIGNALS = ('break_up','low_dense','washout','release','all_profit','high_div','winner_drop','break_down')


def chips(x, base, prices, prior_component):
    from seven_desks.market_refresh import component, literal, read, watermark, write, write_meta
    db = x.db
    market = read(base/(ROOT+'market-daily.parquet'))
    last = market.trade_date.max()
    if last.date() >= x.days[-1]:
        return prior_component or component(x,watermark(market.trade_date))
    start = (last-pd.Timedelta(days=420)).date()
    x.view('stock_chip_metrics',start,day='trade_date',alias='market_chip_raw')
    counts = db.execute("""SELECT trade_date,count(DISTINCT symbol) n FROM market_chip_raw
          WHERE source='tushare.cyq_perf' AND isfinite(winner_rate) AND trade_date>?
          GROUP BY 1 ORDER BY 1""",[last.date()]).fetchall()
    # Require full native market slices before promoting market-level indicators.
    floor = int(market.tail(20).sample_count.median()*.9)
    if not any(n>=floor for _,n in counts):
        if prior_component and not counts:
            return prior_component
        return component(x,str(last.date()),'未发布覆盖完整的新原生筹码截面；股价不替代成本或获利比例',
                         retained=True,native_source_date=str(counts[-1][0]) if counts else str(last.date()))
    db.execute("""CREATE OR REPLACE TEMP VIEW market_chip_unique AS
      SELECT symbol,trade_date,min(cost_5pct) cost_5pct,min(cost_15pct) cost_15pct,
        min(cost_50pct) cost_50pct,min(cost_85pct) cost_85pct,min(cost_95pct) cost_95pct,
        min(weight_avg) weight_avg,min(winner_rate) winner_rate,min(his_low) his_low,min(his_high) his_high
      FROM market_chip_raw WHERE source='tushare.cyq_perf' GROUP BY 1,2
      HAVING count(DISTINCT struct_pack(c5:=cost_5pct,c15:=cost_15pct,c50:=cost_50pct,c85:=cost_85pct,
         c95:=cost_95pct,wa:=weight_avg,wr:=winner_rate,lo:=his_low,hi:=his_high))=1""")
    latest = read(base/(ROOT+'stock-latest.parquet'))
    seq = int(latest.seq.max())
    offset = db.execute('SELECT session_id FROM market_sessions WHERE trade_date=?',[last.date()]).fetchone()
    if offset is None:
        raise ValueError('筹码母版最后交易日不在正式日历内')
    db.execute(f"""CREATE OR REPLACE TEMP VIEW market_chip_cal AS
        SELECT trade_date,(session_id+{seq-int(offset[0])})::INTEGER seq FROM market_sessions""")
    non_null = db.execute("""SELECT ch.trade_date,count(*) FROM market_chip_unique ch JOIN market_bars b USING(symbol,trade_date)
        WHERE ch.trade_date>? AND isfinite(ch.winner_rate) GROUP BY 1 HAVING count(*)>=? ORDER BY 1""",
        [last.date(),floor]).fetchall()
    if not non_null:
        return component(x,str(last.date()),'原生筹码已有较新发布，当日股价完整截面未齐',retained=True,
                         native_source_date=str(counts[-1][0]))
    days = ','.join('DATE '+literal(d) for d,_ in non_null)
    f = db.execute(f"""WITH b AS (
      SELECT b.symbol,b.trade_date,b.close,cal.seq,i.list_date,v.float_mv,ch.cost_5pct,ch.cost_15pct,
        ch.cost_50pct,ch.cost_85pct,ch.cost_95pct,ch.weight_avg,ch.winner_rate,
        100*(ch.cost_95pct-ch.cost_5pct)/nullif(ch.cost_95pct+ch.cost_5pct,0) concentration90,
        100*(ch.cost_85pct-ch.cost_15pct)/nullif(ch.cost_85pct+ch.cost_15pct,0) concentration70,
        100*(b.close/nullif(ch.weight_avg,0)-1) cost_deviation,b.close*a.factor hfq_close,
        min(b.close) OVER w252 low_1y,max(b.close) OVER w252 high_1y,count(*) OVER w252 history_count,
        cal.seq-lc.seq listing_sessions
      FROM market_bars b JOIN market_stock_identity i USING(symbol)
      JOIN market_chip_cal cal USING(trade_date)
      LEFT JOIN market_chip_cal lc ON lc.trade_date=(SELECT min(trade_date) FROM market_chip_cal WHERE trade_date>=i.list_date)
      LEFT JOIN market_chip_unique ch ON ch.symbol=b.symbol AND ch.trade_date=b.trade_date
      LEFT JOIN market_values v ON v.symbol=b.symbol AND v.trade_date=b.trade_date
      LEFT JOIN market_adj_unique a ON a.symbol=b.symbol AND a.trade_date=b.trade_date
      WINDOW w252 AS(PARTITION BY b.symbol ORDER BY b.trade_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW)
    ), z AS (
      SELECT *,100*(close-low_1y)/nullif(high_1y-low_1y,0) position_1y,
        lag(close) OVER w prev_close,lag(cost_95pct) OVER w prev95,lag(cost_5pct) OVER w prev5,
        lag(concentration90) OVER w prev_con90,lag(winner_rate) OVER w prev_wr,
        CASE WHEN seq-lag(seq,5) OVER w=5 THEN lag(winner_rate,5) OVER w END wr5,
        CASE WHEN seq-lag(seq,10) OVER w=10 THEN lag(winner_rate,10) OVER w END wr10,
        CASE WHEN seq-lag(seq,20) OVER w=20 THEN lag(winner_rate,20) OVER w END wr20,
        CASE WHEN lead(seq,5) OVER w-seq=5 THEN lead(hfq_close,5) OVER w/nullif(hfq_close,0)-1 END return_5d,
        CASE WHEN lead(seq,20) OVER w-seq=20 THEN lead(hfq_close,20) OVER w/nullif(hfq_close,0)-1 END return_20d,
        lag(seq) OVER w prev_seq FROM b WINDOW w AS(PARTITION BY symbol ORDER BY trade_date)
    ) SELECT * FROM z WHERE trade_date IN ({days}) AND winner_rate IS NOT NULL ORDER BY symbol,trade_date""").df()
    events_rel = ROOT+'events.parquet'
    old_events = read(base/events_rel)
    previous = old_events.groupby(['entity_id','event_type']).event_date.max()
    seqs = db.execute('SELECT trade_date,seq FROM market_chip_cal').df().set_index('trade_date').seq
    state = {key:int(seqs[day]) for key,day in previous.items() if day in seqs.index}
    fresh = apply_chip_cooldown(f,state)
    for year,g in fresh.groupby(fresh.trade_date.dt.year):
        rel = ROOT+f'daily-{year}.parquet'
        old = read(base/rel) if (base/rel).is_file() else None
        write(x,rel,pd.concat([old,g]),['symbol','trade_date'])
    latest = fresh.loc[fresh.trade_date.eq(fresh.trade_date.max())]
    write(x,ROOT+'stock-latest.parquet',latest,['symbol','trade_date'])
    db.register('market_chip_fresh',fresh)
    db.register('market_chip_index',prices.loc[prices.symbol.eq('000985.CSI'),['trade_date','close']])
    emit_chip_tables(x,base,market,fresh,old_events,days)
    write_meta(x,'revision/market/chip-daily-extended.json',{'source':'stock_chip_metrics'},fresh.trade_date.max())
    return component(x,watermark(fresh.trade_date),'原生筹码、八类信号、120日分位和252日成本位置按原公式更新',
                     native_source_date=str(counts[-1][0]))


def emit_chip_tables(x,base,market,fresh,old_events,days):
    from seven_desks.market_refresh import parquet, read, write
    db = x.db
    metrics=('winner_rate','cost_deviation','concentration90','concentration70')
    agg = ','.join(f"""median({c}) {c}_median,avg({c} ORDER BY symbol) {c}_equal,
        sum({c}*float_mv ORDER BY symbol)/nullif(sum(float_mv ORDER BY symbol) FILTER(WHERE {c} IS NOT NULL),0) {c}"""
        for c in metrics)
    new = db.execute(f"""SELECT trade_date,count(*) sample_count,
        sum(float_mv ORDER BY symbol) FILTER(WHERE float_mv>0) weight_sum,{agg},
        avg((winner_rate<3)::INT) deep_trapped_ratio,avg((winner_rate>97)::INT) all_profit_ratio,
        count(float_mv)::DOUBLE/count(*) coverage_ratio
        FROM market_chip_fresh WHERE listing_sessions>=60 GROUP BY 1 ORDER BY 1""").df()
    all_market = pd.concat([market,new],ignore_index=True)
    for col in ('winner_rate','winner_rate_median','winner_rate_equal'):
        px = rolling_midrank(all_market[col],120,120)[0]
        all_market.loc[all_market.trade_date>market.trade_date.max(),col+'_percentile_120d'] = px[len(market):]
    write(x,ROOT+'market-daily.parquet',all_market,['trade_date'])
    db.execute("""CREATE OR REPLACE TEMP VIEW market_chip_bench AS SELECT trade_date,
       CASE WHEN lead(seq,5) OVER w-seq=5 THEN lead(close,5) OVER w/close-1 END b5,
       CASE WHEN lead(seq,20) OVER w-seq=20 THEN lead(close,20) OVER w/close-1 END b20
       FROM market_chip_index JOIN market_chip_cal USING(trade_date) WINDOW w AS(ORDER BY trade_date)""")
    flags = ' UNION ALL '.join(f"""SELECT 'chips' dashboard,'{k}' event_type,c.symbol entity_id,
       trade_date event_date,return_5d,return_20d,return_5d-b5 excess_5d,return_20d-b20 excess_20d,
       'approximate' quality_status,'seven-market-20261113-v2' formula_version
       FROM market_chip_fresh c LEFT JOIN market_chip_bench USING(trade_date) WHERE {k}""" for k in SIGNALS)
    events = pd.concat([old_events,db.execute(flags).df()],ignore_index=True)
    write(x,ROOT+'events.parquet',events,['event_date','event_type','entity_id'],day='event_date')
    db.register('market_chip_events',events)
    for name, query, keys in [
        ('winner-distribution',"""SELECT trade_date,CASE WHEN winner_rate<3 THEN '00-03' WHEN winner_rate<20 THEN '03-20'
         WHEN winner_rate<40 THEN '20-40' WHEN winner_rate<60 THEN '40-60' WHEN winner_rate<80 THEN '60-80'
         WHEN winner_rate<=97 THEN '80-97' ELSE '97-100' END bucket,count(*) stock_count,sum(float_mv ORDER BY symbol) float_mv
         FROM market_chip_fresh WHERE listing_sessions>=60 GROUP BY 1,2""",['trade_date','bucket']),
        ('segments',"""SELECT trade_date,CASE WHEN starts_with(symbol,'688') THEN 'STAR' WHEN starts_with(symbol,'30') THEN 'ChiNext'
         WHEN ends_with(symbol,'.BJ') THEN 'BSE' ELSE 'Main' END board,
         CASE WHEN float_mv<5e9 THEN 'small' WHEN float_mv<2e10 THEN 'medium' WHEN float_mv>=2e10 THEN 'large' ELSE 'missing' END size_bucket,
         count(*) sample_count,sum(winner_rate*float_mv ORDER BY symbol)/nullif(sum(float_mv ORDER BY symbol),0) winner_rate,
         avg(winner_rate ORDER BY symbol) winner_rate_equal FROM market_chip_fresh WHERE listing_sessions>=60 GROUP BY 1,2,3""",
         ['trade_date','board','size_bucket']),
        ('range-position',"""SELECT p.symbol,p.trade_date,r.his_low,r.his_high,100*(p.close-r.his_low)/nullif(r.his_high-r.his_low,0)
         position_range FROM market_chip_fresh p JOIN market_chip_unique r USING(symbol,trade_date)""",['trade_date','symbol']),
    ]:
        rel = ROOT+name+'.parquet'
        write(x,rel,pd.concat([read(base/rel),db.execute(query).df()]),keys)
    counts = db.execute("""SELECT event_date trade_date,event_type,count(*) trigger_count,count(excess_20d) complete_20d_count
            FROM market_chip_events GROUP BY 1,2""").df()
    write(x,ROOT+'signal-daily-counts.parquet',counts,['trade_date','event_type'])
    recent = db.execute("""SELECT entity_id symbol,event_type,max(event_date) last_signal_date,max(c.seq) last_signal_session,
        (? - max(c.seq)<5) hit_last5,(? - max(c.seq)<20) hit_last20,?::DATE trade_date
        FROM market_chip_events e JOIN market_chip_cal c ON e.event_date=c.trade_date GROUP BY 1,2""",
        [int(fresh.seq.max()),int(fresh.seq.max()),fresh.trade_date.max()]).df()
    write(x,ROOT+'recent-signals-latest.parquet',recent,['trade_date','event_type','symbol'])
    db.execute("""CREATE OR REPLACE TEMP VIEW calendar AS SELECT trade_date,seq FROM market_chip_cal""")
    db.execute("""CREATE OR REPLACE TEMP VIEW src AS SELECT symbol,trade_date,close,cost_5pct,cost_95pct
        FROM market_bars b JOIN market_chip_unique USING(symbol,trade_date)""")
    rel = 'revision/market/chip-cost-position.parquet'
    x.emit(rel,f"""SELECT * FROM (SELECT * FROM {parquet(base/rel)} UNION ALL BY NAME
        SELECT * FROM ({POSITION_QUERY}) WHERE trade_date IN ({days})) ORDER BY symbol,trade_date""")
    write_industries(x,base)
    backtest = db.execute("""WITH groups AS (
      SELECT *, 'all' sample_group FROM market_chip_events
      UNION ALL SELECT *, 'last_year' sample_group FROM market_chip_events
      WHERE event_date>=?::DATE-INTERVAL 1 YEAR
    ) SELECT event_type,sample_group,count(*) triggers,count(excess_5d) n5,
           avg(excess_5d ORDER BY event_date,entity_id) mean_excess_5d,median(excess_5d) median_excess_5d,
           count(excess_20d) n20,avg(excess_20d ORDER BY event_date,entity_id) mean_excess_20d,
           median(excess_20d) median_excess_20d,avg((excess_20d>0)::INT) win_rate_20d
      FROM groups GROUP BY 1,2""",[fresh.trade_date.max()]).df()
    write(x,ROOT+'backtest.parquet',backtest,['sample_group','event_type'],None,data_date=fresh.trade_date.max())
    rel = 'revision/market/phase3-chip-native.parquet'
    native = db.execute(f"""SELECT symbol,trade_date,winner_rate,weight_avg,cost_5pct,cost_15pct,cost_50pct,
         cost_85pct,cost_95pct,his_low,his_high,min(source) AS "source",min(data_version) source_version
         FROM market_chip_raw WHERE source='tushare.cyq_perf' AND trade_date IN ({days}) GROUP BY ALL""").df()
    native = native.merge(fresh[['symbol','trade_date']],on=['symbol','trade_date'],how='inner')
    write(x,rel,pd.concat([read(base/rel),native]),['symbol','trade_date'])
    bars = db.execute("""SELECT c.symbol,c.trade_date,b.open,b.high,b.low,b.close,b.volume,b.amount,r.turnover_rate
        FROM market_chip_fresh c JOIN market_bars b USING(symbol,trade_date)
        LEFT JOIN market_research_unique r USING(symbol,trade_date)""").df()
    for year,g in bars.groupby(bars.trade_date.dt.year):
        rel = f'market/ui-extra/chips/bars-{year}.parquet'
        old = read(base/rel) if (base/rel).is_file() else None
        write(x,rel,pd.concat([old,g]),['symbol','trade_date'])


def write_industries(x,base):
    from seven_desks.market_refresh import read, write
    x.view('industry_members',day='as_of_date',alias='market_chip_classification')
    x.view('industry_taxonomy',alias='market_chip_taxonomy')
    cols = ('winner_rate','cost_deviation','concentration90')
    stats = ','.join(f"""median({c}) {c}_median,avg({c} ORDER BY f.symbol) {c}_equal,
        sum({c}*float_mv ORDER BY f.symbol)/nullif(sum(float_mv ORDER BY f.symbol)
        FILTER(WHERE {c} IS NOT NULL),0) {c}""" for c in cols)
    new = x.db.execute(f"""WITH tx AS (
        SELECT industry_code,min(index_symbol) index_symbol,min(level) AS level,min(parent_industry_code) parent
        FROM market_chip_taxonomy WHERE classification_system='SW2021' GROUP BY 1
      ), assignments AS (
        SELECT symbol,industry_code FROM market_chip_classification WHERE classification_system='sw'
        QUALIFY row_number() OVER(PARTITION BY symbol ORDER BY as_of_date DESC,industry_code)=1
      ), m AS (
        SELECT symbol,coalesce(CASE WHEN a.level='L1' THEN a.index_symbol END,
          CASE WHEN b.level='L1' THEN b.index_symbol END, CASE WHEN d.level='L1' THEN d.index_symbol END) industry_code
        FROM assignments j JOIN tx a USING(industry_code) LEFT JOIN tx b ON b.industry_code=a.parent
        LEFT JOIN tx d ON d.industry_code=b.parent
      )
      SELECT f.trade_date,m.industry_code,count(*) sample_count,{stats}
      FROM market_chip_fresh f JOIN m USING(symbol)
      WHERE m.industry_code IS NOT NULL AND listing_sessions>=60 GROUP BY 1,2""").df()
    rel = ROOT+'industry-daily.parquet'
    all_industry = pd.concat([read(base/rel),new]).sort_values(['industry_code','trade_date'])
    write(x,rel,all_industry,['industry_code','trade_date'])
    weekly = all_industry.groupby(['industry_code',all_industry.trade_date.dt.to_period('W')]).tail(1).copy()
    weekly['column_normalized_winner'] = weekly.winner_rate/weekly.groupby('trade_date').winner_rate.transform('max')
    write(x,ROOT+'industry-weekly.parquet',weekly,['industry_code','trade_date'])
