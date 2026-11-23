from seven_desks.settings import FROZEN, FROZEN_VERSION
from seven_desks.store import version_root
"""Behaviour checks using genuine frozen source and local prepared supplements."""
import json
from pathlib import Path
import pytest
from seven_desks.store import Store, CACHE
from seven_desks.adapters import contrarian as desk


@pytest.fixture(scope='module')
def store():
    if not (FROZEN / "active.json").exists():pytest.skip('No prepared snapshot')
    version=(__import__('os').environ.get('SEVEN_DESKS_TEST_VERSION') or json.loads((FROZEN / "active.json").read_text())['data_version'])
    result=Store(version)
    return result


def part(response,key):return next(p for p in response['sections'] if p['id']==key)


@pytest.mark.parametrize('tab',[x[0] for x in desk.TABS]+['entity'])
def test_all_components_are_real_json_and_missing_are_explained(store,tab):
    x=desk.panel('contrarian',tab,{'entity_id':'600519.SH'} if tab=='entity' else {},store)
    assert x['sections'] and x['not_done']
    assert x['data_date']=='2026-11-09'
    json.dumps(x,allow_nan=False)
    assert len({c['key'] for c in x['controls']})==len(x['controls'])
    for p in x['sections']:
        assert p['columns'] and p['quality']
        if not p['rows']:assert p['missing_reason']


def test_both_industry_levels_actually_change_all_industry_components(store):
    one=desk.panel('contrarian','industries',{'level':'L1'},store)
    two=desk.panel('contrarian','industries',{'level':'L2'},store)
    assert len(part(one,'coordinates')['rows'])==31
    assert len(part(two,'coordinates')['rows'])>31
    assert {r['industry_name'] for r in part(one,'coordinates')['rows']}!={r['industry_name'] for r in part(two,'coordinates')['rows']}
    assert any(r.get('overweight') is not None for r in part(two,'coordinates')['rows'])
    assert part(two,'industry-history')['rows']
    chosen=part(two,'industry-candidates')['rows'][0]['industry_l2'] if part(two,'industry-candidates')['rows'] else None
    if chosen:assert all(r['industry_l2']==chosen for r in part(two,'industry-candidates')['rows'])
    old=desk.panel('contrarian','industries',{'level':'L1','date':'2024-12-31'},store)
    assert old['data_date']=='2024-12-31'
    assert all(str(r.get('as_of_date',''))[:10]<='2024-12-31' for r in part(old,'coordinates')['rows'] if r.get('as_of_date'))
    assert all(str(r['allocation_available'])[:10]<='2024-12-31' for r in part(old,'coordinates')['rows'] if r.get('allocation_available'))


def test_candidate_red_sort_pagination_and_actual_matrix_intersection(store):
    low=desk.panel('contrarian','candidates',{'board':'low_pe_ttm','red':'0'},store)
    red=desk.panel('contrarian','candidates',{'board':'low_pe_ttm','red':'1','page_size':'200'},store)
    assert part(red,'candidates')['pagination']['total']>part(low,'candidates')['pagination']['total']
    assert any(r['red_count']>0 for r in part(red,'candidates')['rows'])
    sorted_page=desk.panel('contrarian','candidates',{'sort':'name','order':'asc'},store)
    names=[r['name'] for r in part(sorted_page,'candidates')['rows']];assert names==sorted(names)
    a=desk.panel('contrarian','candidates',{'intersection_a':'low_pe_ttm','intersection_b':'low_pb','red':'1'},store)
    assert part(a,'candidates')['rows']
    assert all(r['hit_low_pe_ttm'] and r['hit_low_pb'] for r in part(a,'candidates')['rows'])
    clicked=desk.panel('contrarian','entity',{'entity_id':'intersection:low_pe_ttm|low_pb','red':'1'},store)
    assert part(a,'candidates')['pagination']['total']==part(clicked,'candidates')['pagination']['total']
    first=desk.panel('contrarian','candidates',{'page':'1','page_size':'50'},store)
    later=desk.panel('contrarian','candidates',{'page':'2','page_size':'50'},store)
    assert not ({r['symbol'] for r in part(first,'candidates')['rows']} & {r['symbol'] for r in part(later,'candidates')['rows']})


def test_temperature_groups_details_and_historical_forward_counts_are_real(store):
    old=desk.panel('contrarian','temperature',{'date':'2024-12-31'},store)
    new=desk.panel('contrarian','temperature',{},store)
    if desk._revised(store):
        assert len(part(old,'temperature-items')['rows'])==6
        assert all(r.get('smart_money') is None for r in part(new,'temperature')['rows'])
        assert part(new,'temperature')['formula_version']=='2.0.0'
    else:
        assert len(part(old,'temperature-items')['rows'])==11
        assert any(r['smart_money'] is not None for r in part(new,'temperature')['rows'])
    assert all(str(r['trade_date'])[:10]<='2024-12-31' for r in part(old,'temperature')['rows'])
    assert sum(r['sample_count'] for r in part(old,'forward-bands')['rows'])<sum(r['sample_count'] for r in part(new,'forward-bands')['rows'])
    assert all(str(r['end'])[:10]<='2024-12-31' for r in part(old,'panic')['rows'])
    assert all(r['peak_temperature']>=80 for r in part(new,'panic')['rows'])


def test_eight_risk_lights_remain_complete_and_unknown_not_safe(store):
    x=desk.panel('contrarian','traps',{},store)
    assert len(part(x,'risk-rules')['rows'])==8
    assert len(part(x,'red-ratios')['rows'])==10
    keys={c['key'] for c in part(x,'lights-table')['columns']}
    assert set(desk.LIGHTS)<=keys
    old=desk.panel('contrarian','traps',{'date':'2024-12-31'},store)
    assert all(r['lights_available']==0 and all(r.get(k) is None for k in desk.LIGHTS) for r in part(old,'lights-table')['rows'])
    assert all(r['red'] is None for r in part(old,'risk-rules')['rows'])


def test_events_by_stock_signal_category_and_historical_maturity(store):
    events=desk.panel('contrarian','signals',{'date':'2026-09-30','by':'signal'},store)
    stocks=desk.panel('contrarian','signals',{'date':'2026-09-30','by':'stock'},store)
    assert part(events,'events')['pagination']['total']>part(stocks,'events')['pagination']['total']
    assert len({r['symbol'] for r in part(stocks,'events')['rows']})==len(part(stocks,'events')['rows'])
    assert len(part(events,'weeks')['rows'])<=13
    for r in part(events,'events')['rows']:
        assert str(r['event_date'])[:10]<='2026-09-30'
        if not r.get('end20') or r['end20']>'2026-09-30':assert r['excess20'] is None
        if not r.get('end60') or r['end60']>'2026-09-30':assert r['excess60'] is None
    chosen=desk.panel('contrarian','signals',{'kind':'repurchase_announcement'},store)
    assert all(r['kind']=='repurchase_announcement' for r in part(chosen,'events')['rows'])


def test_historical_entity_holding_and_finance_do_not_borrow_latest(store):
    p={'date':'2024-12-31','entity_id':'600519.SH','holding_start':'2024-01-01','holding_end':'2024-12-31'}
    x=desk.panel('contrarian','entity',p,store)
    assert len(part(x,'lights')['rows'])==8
    assert all(str(r['trade_date'])[:10]<='2024-12-31' for r in part(x,'history')['rows'])
    assert all(str(r['announce_date'])[:10]<='2024-12-31' for r in part(x,'financial')['rows'])
    row=part(x,'holding')['rows'][0]
    assert row['holding_end']<='2024-12-31' and row['holding_start']<row['holding_end']
    assert abs(row['excess_return']-(row['price_return']-row['benchmark_return']))<1e-12
    assert part(x,'detail')['rows'][0]['score'] is None
    for r in part(x,'detail')['rows']:
        assert 'forward20' not in r and 'forward60' not in r
    candidates=desk.panel('contrarian','candidates',{'date':'2024-12-31'},store)
    assert all('forward20' not in r and 'forward60' not in r for r in part(candidates,'candidates')['rows'])


def test_factor_details_use_actual_cashflow_ev_and_nine_fscore_inputs(store):
    pcf=desk.panel('contrarian','candidates',{'board':'low_pcf_ttm'},store)
    row=next(r for r in part(pcf,'candidates')['rows'] if r.get('market_value_cny') and r.get('ocf_ttm_cny') and r.get('pcf_ttm'))
    assert abs(row['market_value_cny']/row['ocf_ttm_cny']-row['pcf_ttm'])<1e-6
    ev=desk.panel('contrarian','candidates',{'board':'low_ev_ebitda'},store)
    row=next(r for r in part(ev,'candidates')['rows'] if r.get('market_value_cny') and r.get('ebitda_ttm_cny') and r.get('ev_cny'))
    assert abs((row['market_value_cny']+row['net_debt_cny'])-row['ev_cny'])<1
    assert abs(row['ev_cny']/row['ebitda_ttm_cny']-row['ev_ebitda'])<1e-6
    detail=desk.panel('contrarian','entity',{'entity_id':row['symbol']},store)
    assert part(detail,'fscore')['rows']
    assert len([c for c in part(detail,'fscore')['columns'] if c['key'] in ['positive_roa','positive_cfo','rising_roa','cfo_exceeds_income','lower_long_borrowings_proxy','higher_current_ratio','no_share_increase','higher_gross_margin','higher_asset_turnover']])==9


def test_heatmap_click_contract_applies_real_combined_intersection(store):
    original=desk.panel('contrarian','candidates',{},store)
    matrix=part(original,'matrix');action=matrix['action']
    assert action=={'type':'filter','key':'intersection','field':'intersection_key'}
    cell=next(r for r in matrix['rows'] if r['intersection_key']=='low_pb|low_pe_ttm')
    filtered=desk.panel('contrarian','candidates',{action['key']:cell[action['field']]},store)
    assert part(filtered,'candidates')['pagination']['total']==cell['intersection']
    assert all(r['hit_low_pe_ttm'] and r['hit_low_pb'] for r in part(filtered,'candidates')['rows'])


def test_historical_pe_pb_rolling_midrank_and_roe_are_visible_at_selected_date(store):
    day='2024-12-31'
    snapshot=desk._snapshot(store,day);desk._memberships(store,day,snapshot)
    selected=next(r for r in snapshot if r['symbol']=='600519.SH')
    history=[]
    for year in range(2016,2025):
        history+=desk.query(store,f'history/factors_{year}.parquet','SELECT trade_date,pe_ttm,pb FROM {t} WHERE symbol=? AND trade_date<=? ORDER BY trade_date',['600519.SH',day])
    history.sort(key=lambda r:str(r['trade_date']))
    for key in ('pe_ttm','pb'):
        values=[r[key] for r in history[-756:] if desk._num(r.get(key))]
        current=history[-1][key]
        expected=100*(sum(v<current for v in values)+.5*sum(v==current for v in values))/len(values)
        assert len(values)>=500
        assert abs(selected[key+'_time_pct3y']-expected)<1e-10
    cycle=desk.panel('contrarian','candidates',{'date':day,'board':'cycle_reverse'},store)
    rows=part(cycle,'candidates')['rows'];assert rows
    assert all(r['roe_five_year_pct']<=20 and r['roe_sample_count']>=8 and r['cycle_industry_yoy']>0 for r in rows)
    assert all(str(r['roe_report_period'])[:10]<=day for r in rows)


def test_audit_and_two_year_original_financial_inputs_are_causal(store):
    day='2024-12-31'
    x=desk.panel('contrarian','entity',{'date':day,'entity_id':'603612.SH'},store)
    assert part(x,'audit-inputs')['rows']
    assert all(str(r['announce_date'])[:10]<=day for r in part(x,'audit-inputs')['rows'])
    x=desk.panel('contrarian','entity',{'date':day,'entity_id':'600519.SH'},store)
    annual=part(x,'annual-financials')['rows'];assert len(annual)==2
    assert all(r['profit'] is not None and r['ocf'] is not None and r['deducted'] is not None for r in annual)
    for r in annual:
        assert str(r['profit_announce_date'])[:10]<=day
        assert str(r['ocf_announce_date'])[:10]<=day
        assert str(r['deducted_announce_date'])[:10]<=day
        assert abs(r['cfo_profit_ratio']-r['ocf']/r['profit'])<1e-12
    assert len(part(x,'balance-financials')['rows'])==2
    assert len(part(x,'receivable-comparison')['rows'])==2
    assert desk._audit_light('标准无保留意见')==0
    assert desk._audit_light('带强调事项段的无保留意见')==1
    assert desk._audit_light('保留意见')==2
    assert desk._audit_light('未审计') is None
