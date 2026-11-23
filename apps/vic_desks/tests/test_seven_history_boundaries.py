from seven_desks.settings import FROZEN, FROZEN_VERSION
from seven_desks.store import version_root
"""No non-displayed forward outcome may leak through a historical API payload."""
import json
from pathlib import Path
from seven_desks.store import Store, CACHE
from seven_desks.adapters import market,contrarian,owners


def test_bound_store_never_falls_back_for_missing_dimensions(tmp_path):
    class Missing:
        def path(self,p):return str(tmp_path/p)
    assert market._dimensions(Missing())==({}, {})


def test_chip_scrub_preserves_observed_prices_only():
    assert market._without_forward([{'close':10.,'return_5d':.1,'return_20d':.3,'excess_20d':.2}])==[{'close':10.}]


def test_historical_toplist_does_not_return_unmatured_next_day():
    r=owners.panel('capital_flow','toplist',{'date':'2026-09-30','code':'001246.SZ'})
    for sec in r['sections']:
        for row in sec['rows']:
            if row.get('next_date') and row['next_date']>'2026-09-30':
                assert row.get('next_trading_day_return') is None


def test_contrarian_historical_absolute_and_excess_outcomes_both_masked():
    s=Store((__import__('os').environ.get('SEVEN_DESKS_TEST_VERSION') or json.loads((FROZEN / "active.json").read_text())['data_version']))
    rows=contrarian._events(s,'2026-09-30',{})
    for r in rows:
        for h in (20,60):
            if not r.get('end'+str(h)) or r['end'+str(h)]>'2026-09-30':
                assert r.get('forward'+str(h)) is None
                assert r.get('excess'+str(h)) is None
