from seven_desks.settings import FROZEN, FROZEN_VERSION
from seven_desks.store import version_root
import numpy as np
import pandas as pd
from pathlib import Path
from seven_desks.builders.revision_contrarian import calculate,inverse_rank
from seven_desks.store import Store, CACHE
from seven_desks.adapters import contrarian


def test_rank_does_not_look_forward_and_requires_three_years():
    x=pd.Series(np.arange(1000,dtype=float))
    a=inverse_rank(x);b=inverse_rank(pd.concat([x,pd.Series([-1e9,1e9])],ignore_index=True))
    assert a.iloc[:755].isna().all()
    assert np.allclose(a.iloc[755:],b.iloc[755:1000])
    assert inverse_rank(pd.Series([5.0]*800)).iloc[-1]==50


def test_no_group_silent_renormalization_or_zero_imputation():
    n=1100;raw=pd.DataFrame({'trade_date':pd.bdate_range('2016-01-01',periods=n),'A1':np.linspace(10,20,n),'A2':1.,'A3':20.,'D2':50.,'F1':0.})
    prices=pd.DataFrame({'trade_date':raw.trade_date,'close':np.linspace(100,200,n)})
    good,_=calculate(raw,prices)
    assert good.temperature.notna().sum()>0
    raw['A2']=np.nan;raw['A3']=np.nan
    bad,_=calculate(raw,prices)
    assert bad.temperature.isna().all()
    assert bad.sentiment.notna().any()


def test_actual_frozen_inputs_revised_index_explains_its_meaning():
    s=Store(FROZEN_VERSION)
    for tab in ('overview','temperature','entity'):
        p=contrarian.panel('contrarian',tab,{'entity_id':'600519.SH'},s)
        assert p['sections']
    p=contrarian.panel('contrarian','temperature',{},s)
    detail=next(x for x in p['sections'] if x['id']=='temperature-items')
    assert len(detail['rows'])==6
    assert all(x['minimum_samples']==756 for x in detail['rows'])
    chart=next(x for x in p['sections'] if x['id']=='temperature')
    assert chart['formula_version']=='2.0.0'
    assert 'smart_money' not in [x['key'] for x in chart['series']]
    last=chart['rows'][-1]
    assert abs(last['temperature']-(last['valuation']+last['sentiment'])/2)<1e-10
    early=contrarian.panel('contrarian','temperature',{'date':'2018-01-02'},s)
    assert all(x['temperature'] is None for x in next(x for x in early['sections'] if x['id']=='temperature')['rows'])
