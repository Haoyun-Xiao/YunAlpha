from seven_desks.settings import FROZEN, FROZEN_VERSION
from seven_desks.store import version_root
from seven_desks.adapters import owners


def section(r,key):return next(s for s in r['sections'] if s['id']==key)


def test_change_heat_state_is_effective():
    a=owners.panel('national_team','changes',{'heat_state':'all'})
    b=owners.panel('national_team','changes',{'heat_state':'new_visible'})
    assert section(a,'changes_heat')['rows']!=section(b,'changes_heat')['rows']
    assert any(c['key']=='heat_state' for c in b['controls'])


def test_stock_etf_total_and_margin_repay_are_exposed():
    e=owners.panel('capital_flow','etf',{})
    r=section(e,'stock_etf_total')['rows'][0]
    assert r['etf_count']>=r['valid_flow_count']>0
    m=owners.panel('capital_flow','margin',{'exchange':'SSE'})
    assert any(c['key']=='margin_repay' for c in section(m,'margin_kpi')['columns'])


def test_dragon_turnover_has_real_values():
    t=owners.panel('capital_flow','toplist',{})
    assert any(r.get('turnover_rate') is not None for r in section(t,'dragon_events')['rows'])


def test_national_overview_does_not_duplicate_full_etf_tab():
    r=owners.panel('national_team','overview',{})
    ids={s['id'] for s in r['sections']}
    assert 'etf_overview' in ids
    assert not {'etf_groups','etf_pool','etf_weekly','etf_quarter_compare'} & ids


def test_currency_unknown_does_not_enter_cny_balance():
    d=owners._Data(None)
    try:
        row=d.q("SELECT southbound_net_buy,strict_net_balance FROM capital_balance_daily WHERE trade_date='2026-11-06'")[0]
        assert row['southbound_net_buy'] is None and row['strict_net_balance'] is None
        channels={r['channel'] for r in d.q('SELECT DISTINCT channel FROM southbound_daily')}
        assert channels<={'south_sh','south_sz'}
    finally:d.close()
