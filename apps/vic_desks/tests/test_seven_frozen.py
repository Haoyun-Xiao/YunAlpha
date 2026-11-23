from seven_desks.settings import FROZEN, FROZEN_VERSION
from seven_desks.store import version_root
"""Actual immutable Store, with no fallback to current builder directories."""
import json
import pytest
from seven_desks.store import CACHE,Store
from seven_desks.adapters import owners


@pytest.fixture(scope='module')
def frozen_store():return Store((__import__('os').environ.get('SEVEN_DESKS_TEST_VERSION') or json.loads((FROZEN / "active.json").read_text())['data_version']))


@pytest.mark.parametrize('board,tab',[(b,t) for b,(_,tabs) in owners.BOARDS.items() for t,_ in tabs])
def test_every_ownership_page_uses_real_frozen_files(frozen_store,board,tab):
    r=owners.panel(board,tab,{'corp':'178','code':'600519.SH','ids':'178,94'},frozen_store)
    assert r['sections']
    assert all(s['rows'] or s.get('missing_reason') for s in r['sections'])
    json.dumps(r,allow_nan=False)


def test_frozen_comparison_recomputes_same_weight_overlap(frozen_store):
    r=owners.panel('fund_holdings','compare',{'ids':'178,94'},frozen_store)
    matrix=next(s for s in r['sections'] if s['id']=='overlap')['rows'];assert len(matrix)==4
    a,b=owners._ids('178,94');d=owners._Data(frozen_store)
    try:rows=d.q("SELECT company_id,symbol,weight FROM company_stock_holdings WHERE company_id IN (?,?) AND report_period='2026-06-30'",[a,b])
    finally:d.close()
    aa={x['symbol']:x['weight'] for x in rows if x['company_id']==a};bb={x['symbol']:x['weight'] for x in rows if x['company_id']==b}
    assert abs(matrix[2]['value']-sum(min(aa.get(k,0),bb.get(k,0)) for k in set(aa)|set(bb)))<1e-10
