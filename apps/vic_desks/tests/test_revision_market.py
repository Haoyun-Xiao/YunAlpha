from seven_desks.settings import FROZEN, FROZEN_VERSION
from seven_desks.store import version_root
"""Frozen-input regression checks for market revision; no lake access."""
import json
from pathlib import Path

import duckdb
import pytest
from seven_desks.adapters import market
from seven_desks.builders.revision_market import POSITION_QUERY
from seven_desks.store import Store

ROOT = Path(__file__).resolve().parents[1]
OUT = version_root(FROZEN_VERSION) / 'data/revision/market/chip-cost-position.parquet'

@pytest.fixture(scope='module')
def store():
    if not OUT.exists():
        pytest.skip('build revision-market first')
    return Store(FROZEN_VERSION)

def get(result, key):
    return next(s for s in result['sections'] if s['id'] == key)

def test_suspension_does_not_extend_market_session_window():
    c = duckdb.connect()
    c.execute("CREATE VIEW calendar AS SELECT i seq, DATE '2020-01-01' + i::INTEGER trade_date FROM range(1,301) t(i)")
    c.execute("""CREATE VIEW src AS
       SELECT 'A' symbol, trade_date, 15.0 AS "close", CASE WHEN seq<49 THEN 1.0 ELSE 10.0 END cost_5pct, 20.0 cost_95pct
       FROM calendar WHERE seq NOT BETWEEN 100 AND 199
       UNION ALL
       SELECT 'B',trade_date,15.0,CASE WHEN seq<49 THEN 1.0 ELSE 10.0 END,20.0
       FROM calendar WHERE seq NOT BETWEEN 100 AND 129""")
    rows = c.execute('SELECT symbol,sample_count,cost_low,position_range FROM (' + POSITION_QUERY + ") WHERE trade_date=DATE '2020-10-27' ORDER BY symbol").fetchall()
    assert rows == [('A',152,10.0,None), ('B',222,10.0,50.0)]

def test_cost_position_window_and_bounds(store):
    row = duckdb.connect().execute("SELECT position_range,sample_count,cost_low,cost_high FROM read_parquet(?) WHERE symbol='600519.SH' AND trade_date=DATE '2026-11-06'", [str(OUT)]).fetchone()
    assert row[1:] == (252, 1178.1, 1963.5)
    assert row[0] == pytest.approx(100*(1453-1178.1)/(1963.5-1178.1))
    c = duckdb.connect()
    assert c.execute('SELECT count(*) FROM read_parquet(?) WHERE (sample_count<202 OR cost_low<=0 OR cost_high<=cost_low) AND position_range IS NOT NULL',[str(OUT)]).fetchone()[0] == 0
    assert c.execute('SELECT count(*) FROM read_parquet(?) WHERE position_range>100 OR position_range<0',[str(OUT)]).fetchone()[0] > 0
    result = market.panel('chips','stock',{'code':'600519.SH','obs':'2026-11-06'},store)
    summary = get(result,'stock-summary')['rows'][0]
    assert summary['position_range'] == pytest.approx(row[0])
    assert summary['cost_position_sample_count'] == 252
    candle = get(result,'candlestick')
    assert candle['action']['params']['obs'] == '$trade_date'
    assert {'cost_5pct','cost_95pct'} <= {v['key'] for v in candle['overlays']}

def test_sentiment_partial_coverage_and_prior_radar(store):
    result = market.panel('sentiment','overview',{'date':'2026-11-09'},store)
    rows = get(result,'radar')['rows']
    capital = next(r for r in rows if r['dimension_id']=='capital')
    assert (capital['valid_items'],capital['expected_items']) == (2,3)
    assert capital['missing_indicators'] == ['E3']
    assert capital['previous_date'] and capital['previous_score'] is not None
    assert '部分覆盖' in ' '.join(result['warnings'])
    e1 = next(r for r in get(result,'freshness')['rows'] if r['indicator_id']=='E1')
    assert e1['unresolved_share_count'] == 104
    assert '未合并' in e1['missing_reason']
    json.dumps(result,allow_nan=False)

def test_valuation_summary_uses_same_day_frozen_inputs(store):
    result = market.panel('valuation','overview',{'trade_date':'2026-11-09','win':'10'},store)
    summary = get(result,'temperature')['rows']
    assert len(summary) == 8
    assert all(row['source_date']=='2026-11-09' for row in summary)
    assert summary[0]['value'] == pytest.approx(54.10156, abs=1e-4)
    assert summary[1]['value'] == pytest.approx(13.5761)
    assert summary[2]['value'] == pytest.approx(1.3376)
    assert summary[3]['value'] == pytest.approx(35.9371)
    assert summary[5]['value'] == pytest.approx(.0928713948848177)
    pe_rows = [r for r in get(result,'matrix')['rows'] if r['metric']=='PE(TTM)' and r['selected_percentile'] is not None]
    assert summary[6]['value'] == min(r['selected_percentile'] for r in pe_rows)
    assert summary[7]['value'] == max(r['selected_percentile'] for r in pe_rows)
    assert len(get(result,'temperature-components')['rows']) == 4
