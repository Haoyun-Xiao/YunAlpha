"""Index synthesis and historical statistics from the existing research formulas."""
import numpy as np
import pandas as pd

from seven_desks.builders.market_daily_sentiment import midrank

def completed_month_forward_returns(frame, cutoff, horizons=(12,36)):
    """Calendar-month endpoint lookup excludes the unfinished cutoff month."""
    f=frame.copy();f['trade_date']=pd.to_datetime(f.trade_date)
    if 'month' not in f:f['month']=f.trade_date.dt.to_period('M')
    f['month_complete']=[m.end_time.normalize()<=pd.Timestamp(cutoff).normalize() for m in f.month]
    lookup=f.loc[f.month_complete].set_index(['index_symbol','month']).close
    for months in horizons:
        f[f'forward_{months}m_annualized']=[(lookup.get((r.index_symbol,r.month+months),np.nan)/r.close)**(12/months)-1 if r.month_complete and pd.notna(r.close) and r.close>0 else np.nan for r in f.itertuples()]
    return f



def synthetic_sql(lo,hi):
    return f"""with p as (
          select g.index_symbol,g.trade_date,g.snapshot_date,m.symbol,m.weight/g.weight_sum normalized_weight,
            v.total_mv,r.dv_ttm,case when sb.close>0 then 100*(coalesce(dc.cash_cumulative,0)-coalesce(d0.cash_cumulative,0))/sb.close end cash_dividend_yield,e.value_cny earnings,b.eq_value equity,e.as_of_date earnings_date,b.announce_date equity_date
          from market_index_calendar g join market_members m on g.index_symbol=m.index_symbol and g.snapshot_date=m.as_of_date
          left join market_values v on m.symbol=v.symbol and g.trade_date=v.trade_date
          left join market_research_unique r on m.symbol=r.symbol and g.trade_date=r.trade_date
          left join market_bars sb on m.symbol=sb.symbol and g.trade_date=sb.trade_date
          asof left join market_div_cumulative dc on m.symbol=dc.symbol and g.trade_date>=dc.ex_date
          asof left join market_div_cumulative d0 on m.symbol=d0.symbol and g.trade_date-interval 1 year>=d0.ex_date
          asof left join market_earnings e on m.symbol=e.symbol and g.trade_date>=e.as_of_date
          asof left join market_equity b on m.symbol=b.symbol and g.trade_date>=b.announce_date
          where g.trade_date between '{lo}' and '{hi}'
        ), a as(select index_symbol,trade_date,snapshot_date,count(*) member_count,count(total_mv) market_value_count,
          sum(case when total_mv>0 then normalized_weight else 0 end ORDER BY symbol) price_weight_coverage,
          sum(case when total_mv>0 and earnings is not null then total_mv end ORDER BY symbol)/nullif(sum(case when total_mv>0 then total_mv end ORDER BY symbol),0) earnings_mv_coverage,
          sum(case when total_mv>0 and equity is not null then total_mv end ORDER BY symbol)/nullif(sum(case when total_mv>0 then total_mv end ORDER BY symbol),0) equity_mv_coverage,
          sum(case when total_mv>0 and cash_dividend_yield is not null then total_mv end ORDER BY symbol)/nullif(sum(case when total_mv>0 then total_mv end ORDER BY symbol),0) dividend_mv_coverage,
          sum(case when total_mv>0 and earnings is not null then total_mv end ORDER BY symbol)/nullif(sum(case when total_mv>0 then earnings end ORDER BY symbol),0) pe_partial,
          sum(case when total_mv>0 and equity is not null then total_mv end ORDER BY symbol)/nullif(sum(case when total_mv>0 then equity end ORDER BY symbol),0) pb_partial,
          sum(case when total_mv>0 then total_mv*cash_dividend_yield end ORDER BY symbol)/nullif(sum(case when total_mv>0 and cash_dividend_yield is not null then total_mv end ORDER BY symbol),0) dy_partial,
          sum(case when total_mv>0 then total_mv*dv_ttm end ORDER BY symbol)/nullif(sum(case when total_mv>0 and dv_ttm is not null then total_mv end ORDER BY symbol),0) provider_dv_ttm_yield,
          max(earnings_date) latest_earnings_date,max(equity_date) latest_equity_date
          from p group by 1,2,3)
          select *,case when price_weight_coverage>=.95 and earnings_mv_coverage>=.95 and pe_partial>0 then pe_partial end pe_ttm,
          case when price_weight_coverage>=.95 and equity_mv_coverage>=.95 and pb_partial>0 then pb_partial end pb,
          case when price_weight_coverage>=.95 and dividend_mv_coverage>=.95 then dy_partial end dividend_yield from a"""


def weekly_statistics(frame, dates, weeks, metric, windows=(3,5,10,20), *, channels=True):
    vals = frame[metric].to_numpy(float)
    endpoints = dates.isin(weeks)
    for i, day in enumerate(dates):
        if i < len(dates)-len(frame.attrs['new_dates']):
            continue
        for years in windows:
            sample = vals[endpoints & (dates>day-pd.DateOffset(years=years)) & (dates<=day)]
            valid = sample[np.isfinite(sample)]
            minimum = (52*years*8+9)//10
            frame.at[day,f'{metric}_pct_{years}y'] = midrank(valid,vals[i],minimum)
            frame.at[day,f'{metric}_n_{years}y'] = len(valid)
            if years==5 and channels and len(valid)>=minimum:
                for q, value in zip((20,40,60,80),np.quantile(valid,[.2,.4,.6,.8])):
                    frame.at[day,f'{metric}_channel_{q}'] = value


def returns(panel, prices, cutoff):
    merged = panel.merge(prices[['symbol','trade_date','close']].rename(columns={'symbol':'index_symbol'}),
                         on=['index_symbol','trade_date'],how='left',validate='one_to_one')
    merged = merged.sort_values(['trade_date','index_symbol'])
    merged['month'] = merged.trade_date.dt.to_period('M')
    monthly = completed_month_forward_returns(merged.groupby(['index_symbol','month']).tail(1),cutoff)
    monthly['pe_band_5y'] = pd.cut(monthly.pe_ttm_pct_5y,[-np.inf,20,40,60,80,np.inf],labels=False,right=False)
    stats = []
    for (symbol,band),g in monthly.dropna(subset=['pe_band_5y']).groupby(['index_symbol','pe_band_5y']):
        for months in (12,36):
            v = g[f'forward_{months}m_annualized'].dropna()
            stats.append({'index_symbol':symbol,'band':int(band),'horizon_months':months,
                          'sample_count':len(v),'mean':v.mean(),'median':v.median(),
                          'win_rate':(v>0).mean() if len(v) else np.nan})
    annual = merged.assign(year=merged.trade_date.dt.year).groupby(['index_symbol','year']).tail(1)
    annual = annual.sort_values(['index_symbol','year']).drop(columns='month').copy()
    annual['eps_proxy'] = annual.close/annual.pe_ttm
    for key,out in [('close','price_return'),('eps_proxy','eps_growth'),('pe_ttm','pe_change')]:
        annual[out] = annual.groupby('index_symbol')[key].pct_change(fill_method=None)
    annual['dividend_yield_proxy'] = annual.dividend_yield/100
    annual['identity_residual'] = annual.price_return-((1+annual.eps_growth)*(1+annual.pe_change)-1)
    return monthly.drop(columns='month'),pd.DataFrame(stats),annual
