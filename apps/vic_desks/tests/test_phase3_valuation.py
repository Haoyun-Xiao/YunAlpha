"""Focused checks for the new valuation snapshot and the original snapshot boundary."""
import json
import os

import math
import pandas as pd
import pytest

from seven_desks.adapters.market import panel
from seven_desks.prepare_valuation import weekly_rank
from seven_desks.server import BOARDS, adapter, metadata, response, verify_algorithm
from seven_desks.settings import FROZEN_VERSION
from seven_desks.store import Store


def section(payload, name):
    return next(item for item in payload["sections"] if item["id"] == name)


def test_weekly_rank_excludes_future_and_gates_missing_windows():
    dates = pd.date_range("2018-11-16", periods=157, freq="W-FRI")
    data = pd.Series(7., index=dates)
    assert weekly_rank(data, dates[124], 3, dates) == (50., 125)
    assert weekly_rank(data, dates[123], 3, dates) == (None, 124)
    assert weekly_rank(data, dates[124], 5, dates) == (None, 125)


@pytest.fixture(scope="module")
def candidate():
    version = os.environ.get("SEVEN_VALUATION_TEST_VERSION")
    if not version:
        pytest.skip("set SEVEN_VALUATION_TEST_VERSION to an already-built candidate")
    verify_algorithm(version)
    return Store(version)


def test_latest_overview_has_new_pe_pb_but_no_stale_dividends(candidate):
    latest = panel("valuation", "overview", {"win": "5"}, candidate)
    assert latest["data_date"] == "2026-11-13"
    rows = {row["label"]: row["value"] for row in section(latest, "temperature")["rows"]}
    assert (rows["沪深300 PE"], rows["沪深300 PB"]) == (13.4709, 1.3276)
    assert rows["中证全指股息率"] is None
    assert section(latest, "temperature")["actions"][0]["params"] == {"date": "2026-11-09"}
    old = panel("valuation", "overview", {"date": "2026-11-09"}, candidate)
    assert section(old, "map")["rows"]
    assert section(old, "temperature")["actions"][0]["params"] == {"date": "2026-11-13"}
    matrix = section(latest, "matrix")["rows"]
    assert len(matrix) == 36
    hs300 = next(row for row in matrix if row["symbol"] == "000300.SH" and row["metric"] == "PE(TTM)")
    assert hs300["samples_5y"] > 208 and hs300["pct_20y"] is None
    before = panel("valuation", "overview", {"date": "2026-11-08"}, candidate)
    assert before["data_date"] == "2026-11-06"


def test_detail_structural_coverage_and_published_bands(candidate):
    missing = panel("valuation", "entity", {"code": "000300.SH", "metric": "dividend_yield"}, candidate)
    provenance = section(missing, "provenance")
    assert provenance["rows"][0]["dividend_yield"] is None
    assert provenance["actions"][0]["params"] == {
        "date": "2026-11-09", "metric": "dividend_yield", "code": "000300.SH",
    }
    detail = panel("valuation", "entity", {"code": "000300.SH", "metric": "pb", "win": "5", "range": "3y",
                                           "start": "2023-01-01", "end": "2026-11-13"}, candidate)
    assert section(detail, "interval")["rows"][0]["actual_end"] == "2026-11-13"
    assert "2026-11-09" in section(detail, "annual")["description"]
    assert {row["observation_frequency"] for row in section(detail, "alternative-observations")["rows"]} == {"daily", "monthly_plus_latest"}
    peers = section(detail, "peers")["rows"]
    hs300 = next(row for row in peers if row["symbol"] == "000300.SH")
    dates = metadata("valuation", candidate.version)["calendar_dates"]
    cutoff = dates.index("2026-11-13")
    a = candidate.read("market/ui-extra/index-prices.parquet", where="symbol=? AND trade_date=?", params=["000300.SH", dates[cutoff-20]])[0]
    b = candidate.read("market/valuation/phase3-prices.parquet", where="symbol=? AND trade_date=?", params=["000300.SH", "2026-11-13"])[0]
    assert math.isclose(hs300["price_return"], b["close"]/a["close"]-1)
    returns = panel("valuation", "returns", {"code": "000300.SH", "date": "2026-11-09", "win": "5", "horizon": "36"}, candidate)
    stats = section(returns, "return-statistics")
    assert len(stats["rows"]) == 10
    assert any(row.get("source", "").startswith("valuation-return-stats-") for row in stats["rows"])
    later = panel("valuation", "returns", {"code": "000300.SH", "win": "5"}, candidate)
    assert "最后完整月样本 2026-10-30" in section(later, "return-statistics")["description"]
    structure = panel("valuation", "structure", {"sort": "static_pe", "range": "10y"}, candidate)
    sample = section(structure, "market-structure")["rows"][0]
    assert (sample["eligible_count"], sample["sample_count"], sample["missing_row_count"], sample["valid_pb_count"]) == (5580, 5571, 9, 5512)
    assert sum(row["stock_count"] for row in section(structure, "pe-distribution")["rows"]) == sample["sample_count"]


def same_observations(a, b):
    # Parallel SQL sums can differ in their last floating point bits.
    if isinstance(a, float) and isinstance(b, float):
        return math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-10)
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same_observations(a[key], b[key]) for key in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same_observations(x, y) for x, y in zip(a, b))
    return a == b


def test_frozen_available_and_other_board_queries_hold(candidate):
    verify_algorithm(FROZEN_VERSION)
    for board in (name for name in BOARDS if name != "valuation"):
        tab = metadata(board, candidate.version)["tabs"][0]["id"]
        old = adapter(board).panel(board, tab, {"date": "2026-11-06", "trade_date": "2026-11-06"}, Store(FROZEN_VERSION))
        new = json.loads(response(board, tab, {"data_version": candidate.version, "date": "2026-11-06", "verify": "phase3"}))
        assert old["sections"] and same_observations(old["sections"], new["sections"]), board
        assert new["cutoff"] == "2026-11-09"
