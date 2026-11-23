from seven_desks.settings import FROZEN, FROZEN_VERSION
from seven_desks.store import version_root
"""Regression: validated identical share-class portfolios count money once."""
import json
import duckdb
from seven_desks.store import CACHE,Store
from seven_desks.adapters import owners


def test_identical_portfolio_preserves_two_classes_and_one_amount():
    s=Store((__import__('os').environ.get('SEVEN_DESKS_TEST_VERSION') or json.loads((FROZEN / "active.json").read_text())['data_version']))
    p=s.path('ac_fixed/fund_holdings_corrected.parquet')
    c=duckdb.connect()
    try:
        row=c.execute("SELECT count(distinct fund_symbol),sum(corrected_market_value),sum(corrected_shares) FROM read_parquet(?) WHERE report_period='2025-12-31' AND fund_symbol IN ('012297.OF','012298.OF')",[p]).fetchone()
        assert row[0]==2
        assert abs(row[1]-33859088.38)<.01
        assert abs(row[2]-1729349)<.001
    finally:c.close()


def test_corrected_views_precede_original_files():
    s=Store((__import__('os').environ.get('SEVEN_DESKS_TEST_VERSION') or json.loads((FROZEN / "active.json").read_text())['data_version']));d=owners._Data(s)
    try:
        actual=d.q('SELECT sum(market_value) total FROM company_stock_holdings')[0]['total']
        expected=s.query('SELECT sum(market_value) total FROM read_parquet(?)',[s.path('ac_fixed/company_stock_holdings.parquet')])[0]['total']
        assert abs(actual-expected)<max(1,abs(expected)*1e-12)
    finally:d.close()
