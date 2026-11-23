"""Pure calculations ported from seven_compute_market_20261113.py."""
import math

import numpy as np
import pandas as pd

VERSION = "seven-market-20261113-v2"

def midrank(values,current,min_count=1):
    """Inclusive empirical midrank; missing values never enter the denominator."""
    a=np.asarray(values,dtype=float);a=a[np.isfinite(a)]
    if not np.isfinite(current) or len(a)<min_count:return np.nan
    return 100.0*(np.sum(a<current)+0.5*np.sum(a==current))/len(a)

def rolling_midrank(series,window,minimum):
    v=series.to_numpy(dtype=float);out=np.full(len(v),np.nan);counts=np.zeros(len(v),dtype=int)
    for i,x in enumerate(v):
        tail=v[max(0,i-window+1):i+1];counts[i]=np.isfinite(tail).sum()
        out[i]=midrank(tail,x,minimum)
    return out,counts

def cooldown(mask,seq,gap=10):
    """Greedy first trigger, then suppress the following nine market sessions."""
    out=np.zeros(len(mask),dtype=bool);last=-10**9
    for i,(hit,s) in enumerate(zip(mask,seq)):
        if hit and s-last>=gap: out[i]=True;last=s
    return out

def capital_announcement_daily(events, trading_dates):
    """Complete signed amount only when every announced event has a valid price.

    Known subtotals and event-count coverage are separate audit fields. Zero is
    used only for genuine no-announcement sessions inside the source date span.
    """
    dates=pd.DatetimeIndex(trading_dates)
    e=events.copy();e['announce_date']=pd.to_datetime(e.announce_date)
    positions=dates.searchsorted(e.announce_date)
    e=e[positions<len(dates)].copy();positions=positions[positions<len(dates)]
    e['trade_date']=dates[positions]
    sign=e.trade_type.map({'IN':1.0,'DE':-1.0})
    price=pd.to_numeric(e.avg_price,errors='coerce');volume=pd.to_numeric(e.change_vol,errors='coerce')
    valid=sign.notna()&np.isfinite(price)&(price>0)&np.isfinite(volume)&(volume>=0)
    e['known_amount']=(sign*volume*price).where(valid)
    e['valid_amount_count']=valid.astype(int)
    a=e.groupby('trade_date').agg(event_count=('event_id','size'),valid_amount_count=('valid_amount_count','sum'),known_net_amount=('known_amount',lambda x:x.sum(min_count=1)))
    a=a.reindex(dates);inside=(dates>=e.trade_date.min())&(dates<=e.trade_date.max())
    no_events=inside&a.event_count.isna()
    a.loc[no_events,['event_count','valid_amount_count','known_net_amount']]=0
    a['missing_amount_count']=a.event_count-a.valid_amount_count
    a['coverage_ratio']=(a.valid_amount_count/a.event_count).where(a.event_count>0,1.0).where(a.event_count.notna())
    a['net_amount']=a.known_net_amount.where(a.missing_amount_count==0)
    a['quality_status']=np.where(a.net_amount.notna(),'ok',np.where(a.event_count.notna(),'partial','missing'))
    a.index.name='trade_date'
    return a


def equity_launch_counts(eligible,relations):
    """Verified relationship counts; unidentified funds retain their share-code identity.

    A later report relationship is never moved back into an earlier monthly count.
    Name-only collapsing is a separate approximate series, never the E1 primary input.
    """
    e=eligible.drop_duplicates('symbol').copy()
    e['found_date']=pd.to_datetime(e.found_date);e['month']=e.found_date.dt.to_period('M')
    e['share_code']=e.symbol.str.slice(0,6)
    r=relations.copy()
    for col in ['announce_date','valid_from','valid_to']:r[col]=pd.to_datetime(r[col])
    for col in ['master_code','share_code']:r[col]=r[col].astype(str).str.zfill(6)
    result=[]
    for month,g in e.groupby('month'):
        end=month.end_time.normalize()
        visible=r[r.announce_date.notna()&(r.announce_date<=end)]
        visible=visible[(visible.valid_from.isna()|(visible.valid_from<=end))&(visible.valid_to.isna()|(visible.valid_to>=month.start_time))]
        unique=visible.groupby('share_code').master_code.nunique()
        visible=visible[visible.share_code.isin(unique[unique==1].index)]
        mapping=visible.drop_duplicates('share_code').set_index('share_code').master_code
        keys=g.share_code.map(mapping).fillna(g.share_code)
        proven=g.share_code.isin(mapping.index)
        # A known older master is not a newly formed product when a share class opens.
        master_found=e.set_index('share_code').found_date.to_dict()
        is_new=[not(p and k in master_found and master_found[k].to_period('M')<month) for k,p in zip(keys,proven)]
        product_count=keys[pd.Series(is_new,index=g.index)].nunique()
        result.append({'trade_date':end,'share_count':g.symbol.nunique(),
          'verified_product_count_with_unresolved_shares':product_count,
          'verified_relation_share_count':int(proven.sum()),'unresolved_share_count':int((~proven).sum()),
          'name_approx_product_count':g.drop_duplicates(['family','found_date']).shape[0],
          'quality_status':'partial' if (~proven).any() else 'approximate',
          'identity_rule':'visible_verified_master_else_unmerged_share_code',
          'classification_pit_status':'current_classification_snapshot_not_historical_pit'})
    return pd.DataFrame(result).sort_values('trade_date')

# A1..F3 preserve the page's ordering; D6,D7 and F3 have lower weights.
SPECS=[
('A1','valuation','leading',1,2520,500,True,False),('A2','valuation','leading',1,2520,500,True,False),('A3','valuation','coincident',1,756,250,False,False),
('B1','leverage','lagging',1,756,250,True,False),('B2','leverage','coincident',1,756,250,True,False),('B3','leverage','leading',1,756,250,True,False),
('C1','volume','coincident',1,756,250,False,False),('C2','volume','coincident',1,756,250,False,False),('C3','volume','leading',1,756,250,True,False),
('D1','breadth','coincident',1,756,250,False,False),('D2','breadth','coincident',1,756,250,False,False),('D3','breadth','lagging',1,756,250,False,False),('D4','breadth','coincident',1,756,250,False,False),('D5','breadth','coincident',1,756,250,False,False),('D6','breadth','coincident',.25,756,120,False,False),('D7','breadth','coincident',.25,756,120,False,False),
('E1','capital','leading',1,756,250,True,False),('E2','capital','coincident',1,756,250,True,True),('E3','capital','leading',1,756,250,True,True),('E4','capital','lagging',1,2520,500,True,False),
('F1','volatility','coincident',1,756,250,True,False),('F2','volatility','coincident',1,756,250,True,True),('F3','volatility','coincident',.5,756,250,False,True)]


def score(f, history, since, sources):
    # Preserve already accepted rows and recompute only the new observation slices.
    details = []
    new = f.index >= since
    for id, dim, kind, weight, win, minimum, slow, reverse in SPECS:
        raw = f[id]
        source_dates = sources[id]
        joined = pd.concat([history[id], raw]) if id in history else raw
        pct, n = rolling_midrank(joined, win, minimum)
        pct, n = pct[-len(raw):], n[-len(raw):]
        hot = 100-pct if reverse else pct
        f[id+'_pct'] = hot
        details.append(pd.DataFrame({'trade_date': f.index[new], 'indicator_id': id,
            'raw_value': raw[new].to_numpy(), 'percentile': pct[new], 'heat_score': hot[new],
            'sample_count': n[new], 'dimension': dim, 'timing_type': kind, 'weight': weight,
            'slow': slow, 'window_sessions': win, 'minimum_samples': minimum, 'reverse': reverse,
            'source_date': source_dates[new].to_numpy(), 'pit_status': 'final_snapshot_approximate_availability',
            'coverage_ratio': f.E3_coverage_ratio[new].to_numpy() if id == 'E3' else np.nan}))
    det = pd.concat(details, ignore_index=True)
    def weighted(ids):
        values=f[[x[0]+'_pct' for x in ids]].to_numpy(float);weights=np.array([x[3] for x in ids]);valid=np.isfinite(values)
        return np.divide(np.nansum(values*weights,axis=1),np.sum(valid*weights,axis=1),out=np.full(len(f),np.nan),where=np.sum(valid*weights,axis=1)>0)
    dims=['valuation','leverage','volume','breadth','capital','volatility']
    for dim in dims:f['dimension_'+dim]=weighted([s for s in SPECS if s[1]==dim and s[0]!='E4'])
    a=f[['dimension_'+d for d in dims]];f['dimension_count']=a.notna().sum(axis=1);f['temperature']=a.mean(axis=1).where(f.dimension_count>=4)
    slow=[s for s in SPECS if s[6]];f['slow_count']=f[[s[0]+'_pct' for s in slow]].notna().sum(axis=1);f['slow_temperature']=f[[s[0]+'_pct' for s in slow]].mean(axis=1).where(f.slow_count>=6)
    for kind in ['leading','coincident','lagging']:f[kind]=weighted([s for s in SPECS if s[2]==kind and s[0]!='E4'])
    f['ice_dimensions']=(a<=15).sum(axis=1);f['boil_dimensions']=(a>=85).sum(axis=1)
    f['temperature_band']=pd.cut(f.temperature,[-np.inf,20,35,65,80,np.inf],labels=False,right=False)
    return f.reset_index(), det


def event_flags(f, p, closes):
    sa=closes['000002.SH'].to_numpy();sa20=pd.Series(sa).pct_change(20,fill_method=None).to_numpy()
    # EMA is seeded at the first valid breadth value; 10 sessions from below 40 to above 61.5.
    ema=f.D1.ewm(span=10,adjust=False,min_periods=10).mean()
    amt=p.ashare_total_amount.reindex(pd.DatetimeIndex(f.trade_date)).to_numpy()
    prior_amount=pd.Series(amt).shift().rolling(20,min_periods=20).mean()
    flags={
      'capitulation':((1-p.ashare_advance_ratio.reindex(pd.DatetimeIndex(f.trade_date)).to_numpy())>.85)&(amt>prior_amount*1.2)&(f.D1<15),
      'breadth_thrust':(ema>61.5)&(ema.shift(1)<=61.5)&(ema.shift(1).rolling(10,min_periods=1).min()<40),
      'leverage_flush':(f.B3<-5)&(pd.Series(rolling_midrank(f.B1,252,120)[0])<10),
      'etf_support':(f.etf_daily>0)&(pd.Series(rolling_midrank(f.etf_daily,756,250)[0])>99)&(sa20<=-.05),
      'ice_resonance':f.ice_dimensions>=4,'boil_resonance':f.boil_dimensions>=4}
    # Page did not specify divergence pivot windows. An explicit 20-session proxy is kept separate.
    s=pd.Series(sa);flags['top_breadth_divergence_proxy']=(s>s.shift().rolling(20,min_periods=20).max())&(f.D1<f.D1.shift().rolling(20,min_periods=20).max()-10)
    flags['bottom_breadth_divergence_proxy']=(s<s.shift().rolling(20,min_periods=20).min())&(f.D1>f.D1.shift().rolling(20,min_periods=20).min()+10)
    return flags
