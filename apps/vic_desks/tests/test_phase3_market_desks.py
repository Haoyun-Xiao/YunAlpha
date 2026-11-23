"""Small regressions for exact-date supplements and local filter behavior."""
import json
import math
import os

import pytest

from seven_desks.adapters import market, owners
from seven_desks.server import response, verify_algorithm
from seven_desks.store import Store


def section(result, key):
    return next(part for part in result["sections"] if part["id"] == key)


@pytest.fixture(scope="module")
def store():
    version = os.environ.get("SEVEN_MARKET_TEST_VERSION")
    if not version:
        pytest.skip("build the phase3 market candidate first")
    verify_algorithm(version)
    return Store(version)


def test_vix_paired_source_strictly_precedes_session_and_does_not_change_temperature(store):
    params = {"date": "2026-11-09", "display_item": "VIX_TERM", "w_valuation": "2"}
    result = market.panel("sentiment", "indicators", params, store)
    raw = next(row for row in section(result, "display-only")["rows"] if row["indicator_id"] == "VIX_TERM")
    assert raw["source_date"] == "2026-11-06"
    assert math.isclose(raw["raw_value"], 24.60/25.64)
    assert all(row["source_date"] < row["trade_date"] for row in section(result, "display-history")["rows"] if row["raw_value"] is not None)
    holder = section(result, "holder-amount-coverage")["rows"][0]
    assert 0 < holder["with_price_count"] < holder["event_count"]
    assert (holder["window_end"], holder["source_as_of"]) == ("2026-11-09", "2026-11-06")
    assert not any(row.get("raw_value") == raw["raw_value"] for row in section(result, "indicators")["rows"])
    heat = market.panel("sentiment", "overview", params, store)
    assert section(heat, "gauge")["rows"][0]["value"] is not None


def test_chip_drilldown_does_not_convert_source_zero_or_unobserved_dates(store):
    params = {"date": "2026-11-09", "obs": "2026-11-06", "code": "600519.SH", "range": "3m"}
    result = market.panel("chips", "entity", params, store)
    sample = section(result, "chip-native")["rows"][0]
    price = section(result, "stock-summary")["rows"][0]
    assert (sample["trade_date"], sample["winner_rate"], sample["weight_avg"]) == ("2026-11-06", 80.28, 1386.52)
    assert price["close"] == 1453.0
    assert sample["his_low"] == 0 and sample["native_position"] is None
    assert all(row["trade_date"] <= "2026-11-06" for row in section(result, "candlestick")["rows"])
    filt = {"date": "2026-11-09", "preset": "break_up", "window": "d5", "wr_min": "20", "wr_max": "40"}
    rows = section(market.panel("chips", "screen", filt, store), "stocks")["rows"]
    assert rows and all(20 <= row["winner_rate"] <= 40 for row in rows)


def capital(tab, store, **params):
    return market.capital_additions(tab, params, store, owners.panel("capital_flow", tab, params, store))


def test_capital_opens_verified_channels_and_keeps_other_quantities_separate(store):
    overview = capital("overview", store, date="2026-11-09", win="20")
    north = section(overview, "north_current")["rows"][0]
    assert north == {"trade_date": "2026-11-09", "north_sh": 102043180000.0, "north_sz": 114193220000.0, "total": 216236400000.0}
    assert section(overview, "balance")["net_balance"] is None
    balances = section(overview, "formal_balance")["rows"]
    equity = next(row for row in balances if row["line"] == "股票ETF")
    assert equity["strict_valid_days"] == 0 and equity["observed_complete_window_estimate"] > 0
    cross = capital("cross", store, date="2026-11-09", north_channel="north_sz")
    assert [item["key"] for item in section(cross, "cross_turnover")["series"]] == ["north_sz"]
    assert section(cross, "cross_turnover_legacy")["rows"] and section(cross, "south_trend")["rows"]
    margin = capital("margin", store, date="2026-11-09", exchange="SSE", code="600519.SH")
    record = section(margin, "margin_observed_security")["rows"][0]
    assert record["financing_net_buy"] == record["financing_buy"] - record["financing_repay"] == 7267752.0
    old = capital("cross", store, date="2024-08-16")
    assert "legacy" not in " ".join(s["id"] for s in old["sections"])


def test_candidate_retains_valuation_release_date(store):
    payload = json.loads(response("valuation", "overview", {"data_version": store.version}))
    assert payload["data_date"] == "2026-11-13"
    assert section(payload, "temperature")["actions"][0]["params"]["date"] == "2026-11-09"
