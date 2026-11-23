from seven_desks.settings import CACHE as BUILD_ROOT, LAKE, SOURCE as REVIEW_SOURCE, FROZEN_VERSION
from seven_desks.store import version_root, connect
"""Local-only two-group research index over an explicitly frozen input snapshot.

This deliberately does not reproduce VicAlpha's undocumented eleven indicators.
No upstream calls, lake writes, or optimisation against the original scores.
"""
from pathlib import Path
import argparse
import hashlib
import json
import numpy as np
import pandas as pd
from seven_desks.store import Store, CACHE, clean

FORMULA_ID = 'local_contrarian_environment'
FORMULA_VERSION = '2.0.0'
SPECS = [('V1','沪深300 PE','valuation','A1'),('V2','沪深300 PB','valuation','A2'),
         ('V3','全A正PE中位数','valuation','A3'),('P1','站上60日线占比','sentiment','D2'),
         ('P2','上证A股年线偏离','sentiment','F1'),('P3','中证全指252日回撤','sentiment','drawdown252')]

def inverse_rank(series, window=1260, minimum=756):
    def f(a):
        x=a[-1]; valid=a[np.isfinite(a)]
        if not np.isfinite(x):return np.nan
        return 100*(1-((valid<x).sum()+.5*(valid==x).sum())/len(valid))
    return pd.to_numeric(series,errors='coerce').rolling(window,min_periods=minimum).apply(f,raw=True)

def calculate(raw, prices):
    raw=raw.copy().sort_values('trade_date').reset_index(drop=True)
    px=prices.set_index('trade_date')['close'].reindex(raw.trade_date)
    # 252 trading sessions, not 252 calendar days; no ffill of absent prices.
    peak=px.rolling(252,min_periods=252).max()
    raw['drawdown252']=(px/peak-1).to_numpy()
    daily=raw[['trade_date']].copy();details=[]
    for key,label,group,col in SPECS:
        value=pd.to_numeric(raw[col],errors='coerce')
        if col in ('A1','A2','A3'):value=value.where(value>0)
        fear=inverse_rank(value);daily[key]=fear
        count=value.rolling(1260,min_periods=1).count()
        details.append(pd.DataFrame({'trade_date':raw.trade_date,'indicator_id':key,'indicator':label,'group':group,
             'raw_value':value,'percentile':100-fear,'fear_score':fear,'direction':'低值表示更便宜或承压',
             'minimum_samples':756,'window_sessions':1260,'valid_samples':count,'source_column':col}))
    for group in ('valuation','sentiment'):
        cols=[k for k,_,g,_ in SPECS if g==group];n=daily[cols].notna().sum(axis=1)
        daily[group]=daily[cols].mean(axis=1).where(n>=2);daily[group+'_valid_items']=n
    daily['valid_items']=daily[[x[0] for x in SPECS]].notna().sum(axis=1)
    daily['valid_groups']=daily[['valuation','sentiment']].notna().sum(axis=1)
    daily['coverage']=daily.valid_items/6
    daily['temperature']=((daily.valuation+daily.sentiment)/2).where(daily.valid_groups==2)
    daily['smart_money']=np.nan # backward-compatible nullable field; excluded, never zero.
    daily['band']=pd.cut(daily.temperature,[-np.inf,20,40,60,80,np.inf],right=False,
                        labels=['低','较低','中等','较高','高']).astype('object')
    daily['formula_id']=FORMULA_ID;daily['formula_version']=FORMULA_VERSION
    daily['quality']='独立本地研究指数；高分=估值便宜且市场承压；非原站温度；非择时承诺'
    return daily,pd.concat(details,ignore_index=True)

def validation(daily, prices):
    dates=daily.trade_date.to_numpy();close=prices.set_index('trade_date')['close'].reindex(dates)
    result=[]
    for h in (20,60,120):
        ret=close.shift(-h)/close-1
        future_low=pd.concat([close.shift(-k)/close-1 for k in range(1,h+1)],axis=1)
        downside=future_low.min(axis=1).where(future_low.notna().sum(axis=1)==h)
        for split in ('development_before_2023','validation_since_2023'):
            eligible=pd.Series(dates<pd.Timestamp('2023-01-01') if split.startswith('development') else dates>=pd.Timestamp('2023-01-01'),index=dates)
            for lo in (0,20,40,60,80):
                score=pd.Series(daily.temperature.to_numpy(),index=dates)
                mask=eligible & (score>=lo)&(score<lo+20)&ret.notna()&downside.notna()
                values=ret[mask];risk=downside[mask]
                result.append(dict(split=split,band=f'{lo}–{lo+20}',horizon=h,sample_count=int(mask.sum()),mean_return=values.mean(),median_return=values.median(),win_rate=(values>0).mean() if len(values) else None,mean_path_low=risk.mean(),overlapping_samples=True))
    return clean(result)

def build(base_version=FROZEN_VERSION):
    s=Store(base_version);out=CACHE/'revision-contrarian';out.mkdir(parents=True,exist_ok=True)
    rels=['market/sentiment/daily.parquet','market/ui-extra/index-prices.parquet']
    raw=pd.DataFrame(s.read(rels[0])); raw.trade_date=pd.to_datetime(raw.trade_date)
    prices=pd.DataFrame(s.read(rels[1],where="symbol='000985.CSI'"));prices.trade_date=pd.to_datetime(prices.trade_date)
    raw=raw[raw.trade_date<=pd.Timestamp('2026-11-09')];prices=prices[prices.trade_date<=pd.Timestamp('2026-11-09')]
    daily,detail=calculate(raw,prices)
    for frame,name in [(daily,'temperature'),(detail,'temperature-items')]:
        tmp=out/(name+'.tmp.parquet');frame.to_parquet(tmp,index=False);tmp.replace(out/(name+'.parquet'))
    report=validation(daily,prices);(out/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
    manifest=dict(base_version=base_version,formula_id=FORMULA_ID,formula_version=FORMULA_VERSION,cutoff='2026-11-09',
        input_hashes={r:hashlib.sha256(Path(s.path(r)).read_bytes()).hexdigest() for r in rels},
        parameters=dict(window=1260,min_samples=756,group_minimum=2,group_weights=[.5,.5],smoothing='none'),
        observations=len(daily),valid_observations=int(daily.temperature.notna().sum()),latest=clean(daily.iloc[-1].to_dict()),
        limitations=['市场成分和行业含当前分类回看，非严格历史PIT','日度前向样本重叠，不可作为独立统计显著性','既有数据上的回顾验证，并非真实前瞻样本外','未调整权重追平原站或提高历史收益'])
    (out/'manifest.json').write_text(json.dumps(clean(manifest),ensure_ascii=False,indent=2,allow_nan=False))
    print(json.dumps({'valid':manifest['valid_observations'],'latest':manifest['latest']},ensure_ascii=False))

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--base-version',default=FROZEN_VERSION);build(a.parse_args().base_version)
