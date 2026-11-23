from seven_desks.settings import FROZEN, FROZEN_VERSION
from seven_desks.store import version_root
"""Contract and interaction tests against the genuine frozen review snapshot.

The suite skips integration cases if the local snapshot has not been prepared;
it never fetches data or changes the formal lake.
"""
import json
import math
from pathlib import Path

import pytest

from seven_desks.adapters import market
from seven_desks.store import CACHE, Store


@pytest.fixture(scope="module")
def store():
    active = FROZEN / "active.json"
    if not active.exists():
        pytest.skip("Prepare a local seven-desks frozen snapshot first")
    version = (__import__('os').environ.get('SEVEN_DESKS_TEST_VERSION') or json.loads(active.read_text())["data_version"])
    value = Store(version)
    if not Path(value.path("market/chips/market-daily.parquet")).exists():
        pytest.skip("The active frozen version does not contain the market bundle")
    return value


def section(result, key):
    return next(row for row in result["sections"] if row["id"] == key)


def gauge(result):
    return {r["label"]: r["value"] for r in section(result, "gauge")["rows"]}


@pytest.mark.parametrize("board,tab", [(b, t) for b, tabs in market.TABS.items() for t, _ in tabs])
def test_every_real_panel_obeys_public_contract(store, board, tab):
    result = market.panel(board, tab, {"code": "600519.SH"} if board == "chips" and tab == "stock" else {}, store)
    assert result["title"] and result["description"]
    assert result["sections"]
    assert result["data_date"] <= market.CUTOFF
    json.dumps(result, allow_nan=False)
    for part in result["sections"]:
        assert part["kind"] in {"table", "line", "bar", "heatmap", "scatter", "kpi", "candlestick", "gauge", "radar", "bubble", "timeline"}
        assert isinstance(part["rows"], list)
        assert isinstance(part["columns"], list)
        if not part["rows"]:
            assert part["missing_reason"]
    assert any(c["key"] == "date" for c in result["controls"])
    assert len({c["key"] for c in result["controls"]}) == len(result["controls"])


def test_chip_numeric_filters_search_sort_and_paging_use_real_rows(store):
    result = market.panel("chips", "screen", {"wr_min": "20", "wr_max": "40", "c90_max": "15", "sort": "winner_rate", "order": "desc", "page_size": "20"}, store)
    rows = section(result, "stocks")["rows"]
    assert rows
    assert all(20 <= r["winner_rate"] <= 40 and r["concentration90"] <= 15 for r in rows)
    assert [r["winner_rate"] for r in rows] == sorted([r["winner_rate"] for r in rows], reverse=True)
    next_page = market.panel("chips", "screen", {"wr_min": "20", "wr_max": "40", "c90_max": "15", "sort": "winner_rate", "order": "desc", "page_size": "20", "page": "2"}, store)
    if section(result, "stocks")["pagination"]["pages"] > 1:
        assert not ({r["symbol"] for r in rows} & {r["symbol"] for r in section(next_page, "stocks")["rows"]})
    searched = market.panel("chips", "screen", {"q": "茅台"}, store)
    assert {r["symbol"] for r in section(searched, "stocks")["rows"]} == {"600519.SH"}
    empty = market.panel("chips", "screen", {"q": "not-a-security-code"}, store)
    assert not section(empty, "stocks")["rows"]
    assert section(empty, "stocks")["missing_reason"]


def test_signal_window_and_catalog_are_real_and_backtest_stays_fixed(store):
    one = market.panel("chips", "screen", {"preset": "break_up", "window": "d0", "page_size": "200"}, store)
    five = market.panel("chips", "screen", {"preset": "break_up", "window": "d5", "page_size": "200"}, store)
    a, b = section(one, "stocks"), section(five, "stocks")
    assert a["pagination"]["total"] > 0
    assert b["pagination"]["total"] > a["pagination"]["total"]
    assert {r["symbol"] for r in a["rows"]} <= {r["symbol"] for r in b["rows"]}
    assert section(one, "backtest")["rows"] == section(five, "backtest")["rows"]
    assert len(section(one, "catalog")["rows"]) == 8
    assert section(one, "signal-history")["series"][0]["key"] == "break_up"


def test_chip_aggregation_range_industry_and_entity_date_change_results(store):
    w = market.panel("chips", "overview", {"mode": "w", "range": "1y"}, store)
    med = market.panel("chips", "overview", {"mode": "med", "range": "3m", "metric": "dev"}, store)
    assert section(w, "summary")["rows"] != section(med, "summary")["rows"]
    assert len(section(w, "history")["rows"]) > len(section(med, "history")["rows"])
    assert section(med, "history")["series"][0]["key"] == "cost_deviation"
    chosen = market.panel("chips", "industries", {"l1": "801080.SI"}, store)
    assert section(chosen, "constituents")["rows"]
    assert all(r["industry_code"] == "801080.SI" for r in section(chosen, "constituents")["rows"])
    stock = market.panel("chips", "entity", {"code": "600519.SH", "obs": "2026-11-06"}, store)
    assert section(stock, "stock-summary")["rows"][0]["trade_date"] == "2026-11-06"
    assert all(r["trade_date"] <= "2026-11-06" for r in section(stock, "cost-bands")["rows"])
    candle = section(stock, "candlestick")
    assert candle["kind"] == "candlestick"
    assert candle["rows"]
    assert all(r["trade_date"] <= "2026-11-06" for r in candle["rows"])
    assert all(r["low"] <= min(r["open"], r["close"]) <= max(r["open"], r["close"]) <= r["high"] for r in candle["rows"] if all(r[k] is not None for k in ("open", "close", "low", "high")))
    assert section(stock, "stock-summary")["rows"][0]["turnover_rate"] is not None


def test_sentiment_custom_weights_change_only_browsing_temperature(store):
    original = market.panel("sentiment", "overview", {}, store)
    weighted = market.panel("sentiment", "overview", {"w_valuation": "1.5"}, store)
    a, b = gauge(original), gauge(weighted)
    assert a["慢变量"] == b["慢变量"]
    assert a["温度"] != b["温度"]
    contributions = section(weighted, "contributions")["rows"]
    assert math.isclose(sum(r["contribution"] or 0 for r in contributions), b["温度"], abs_tol=.051)
    insufficient = market.panel("sentiment", "overview", {"w_valuation": 0, "w_leverage": 0, "w_volume": 0}, store)
    assert gauge(insufficient)["温度"] is None
    assert gauge(insufficient)["慢变量"] == a["慢变量"]
    replay = market.panel("sentiment", "replay", {"w_valuation": 5}, store)
    plain_replay = market.panel("sentiment", "replay", {}, store)
    assert section(replay, "nodes")["rows"] == section(plain_replay, "nodes")["rows"]


def test_sentiment_date_indicator_and_event_index_switch_change_results(store):
    friday = market.panel("sentiment", "overview", {"date": "2026-11-06"}, store)
    weekend = market.panel("sentiment", "overview", {"date": "2026-11-08"}, store)
    assert friday["data_date"] == weekend["data_date"] == "2026-11-06"
    assert gauge(friday) == gauge(weekend)
    a = market.panel("sentiment", "indicators", {"item": "A1"}, store)
    b = market.panel("sentiment", "entity", {"entity_id": "A2"}, store)
    assert len(section(a, "indicators")["rows"]) == 23
    assert section(a, "indicator-raw")["rows"] != section(b, "indicator-raw")["rows"]
    etf = market.panel("sentiment", "events", {"event": "etf_support", "index": "000002.SH"}, store)
    hs300 = market.panel("sentiment", "events", {"event": "etf_support", "index": "000300.SH"}, store)
    assert section(etf, "event-list")["rows"]
    assert section(etf, "event-stats")["rows"] != section(hs300, "event-stats")["rows"]
    assert section(etf, "event-path")["rows"] == section(hs300, "event-path")["rows"]
    assert "固定上证A股" in section(etf, "event-path")["title"]
    old = market.panel("sentiment", "events", {"date": "2025-01-10"}, store)
    assert section(old, "event-path")["rows"]
    assert all(r["as_of_date"] == "2025-01-10" for r in section(old, "event-path")["rows"])
    for row in section(old, "event-list")["rows"]:
        assert row["event_date"] <= "2025-01-10"
        if row["event_date"] >= "2025-01-01":
            assert row["return_120d"] is None


def test_valuation_metric_index_window_horizon_and_interval_use_real_data(store):
    overview3 = market.panel("valuation", "overview", {"win": "3"}, store)
    overview20 = market.panel("valuation", "overview", {"win": "20"}, store)
    assert len(section(overview3, "matrix")["rows"]) == 36
    assert [r["selected_percentile"] for r in section(overview3, "matrix")["rows"]] != [r["selected_percentile"] for r in section(overview20, "matrix")["rows"]]
    pe = market.panel("valuation", "index", {"code": "000300.SH", "metric": "pe_ttm", "win": "5", "range": "3y"}, store)
    pb = market.panel("valuation", "entity", {"code": "399006.SZ", "metric": "pb", "win": "3", "range": "3y", "start": "2021-01-01", "end": "2024-01-01"}, store)
    assert section(pe, "channel")["series"][0]["key"] == "pe_ttm"
    assert section(pb, "channel")["series"][0]["key"] == "pb"
    assert section(pe, "channel")["rows"] != section(pb, "channel")["rows"]
    interval = section(pb, "interval")["rows"][0]
    assert interval["actual_start"] <= interval["requested_start"]
    assert interval["actual_end"] <= interval["requested_end"]
    assert math.isclose(interval["price_return"], (1 + interval["eps_growth"]) * (1 + interval["pe_change"]) - 1, abs_tol=1e-10)
    h12 = market.panel("valuation", "returns", {"win": "5", "horizon": "12"}, store)
    h36 = market.panel("valuation", "returns", {"win": "5", "horizon": "36"}, store)
    assert len(section(h12, "scatter")["rows"]) > len(section(h36, "scatter")["rows"])
    assert section(h12, "return-statistics")["rows"] == section(h36, "return-statistics")["rows"]
    assert sum(r["sample_count"] for r in section(h12, "return-distribution")["rows"]) == len(section(h12, "scatter")["rows"])


def test_date_and_weight_boundaries_do_not_introduce_future_or_nan():
    assert market._day("2099-01-01") == market.CUTOFF
    assert market._day("bad-date") == market.CUTOFF
    assert market._temperature({"dimension_" + k: 25.0 for k in market.DIMS}, {k: 1 for k in market.DIMS}) == 25
    assert market._temperature({"dimension_valuation": 25}, {k: 1 for k in market.DIMS}) is None
    dates = ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06", "2026-11-09"]
    events = [{"event_date": dates[1], "return_5d": .1, "return_20d": .2}]
    assert market._mature_event_rows(events, dates, dates[-1])[0]["return_5d"] is None
    assert events[0]["return_5d"] == .1


def test_historical_correlation_path_and_bands_are_truncated_before_observation(store):
    old = "2024-12-31"
    corr = market.panel("sentiment", "dimensions", {"date": old}, store)
    cells = section(corr, "correlations")["rows"]
    assert len(cells) == 36
    assert all(r["as_of_date"] == old and r["sample_count"] <= 250 for r in cells)
    for row in cells:
        if row["dimension_x"] == row["dimension_y"] and row["sample_count"] >= 2:
            assert math.isclose(row["correlation"], 1, abs_tol=1e-12)
    latest = market.panel("sentiment", "dimensions", {}, store)
    assert [r["correlation"] for r in cells] != [r["correlation"] for r in section(latest, "correlations")["rows"]]
    replay = market.panel("sentiment", "replay", {"date": old}, store)
    bands = section(replay, "band-returns")["rows"]
    assert len(bands) == 20
    assert all(r["as_of_date"] == old for r in bands)
    assert sum(r["sample_count"] for r in bands) > 0
    assert section(replay, "nodes")["rows"][-1]["trade_date"] == old
    assert len(section(replay, "node-indicators")["rows"]) == 23
    daily = market._read(store, "sentiment", "daily", where="trade_date<=?", args=[old])
    prices = market._visible_prices(store, "000002.SH", old)
    # Adding future observations must not change either selected-day computation.
    assert market._correlations(daily + [{"trade_date": "2025-01-02", **{"dimension_" + k: 9999 for k in market.DIMS}}], old) == market._correlations(daily, old)
    assert market._band_returns(daily + [{"trade_date": "2025-01-02", "temperature": 99}], prices + [{"trade_date": "2025-01-02", "close": 999999}], old) == market._band_returns(daily, prices, old)


def test_event_paths_only_count_completed_sessions_and_real_prices():
    dates = ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06", "2026-11-09"]
    events = [{"event_date": dates[0], "event_type": "ice_resonance"}, {"event_date": dates[4], "event_type": "ice_resonance"}]
    prices = [{"trade_date": d, "close": 100 + 10 * i} for i, d in enumerate(dates)]
    earlier = market._event_paths(events, prices, dates, dates[4], "ice_resonance")
    later = market._event_paths(events, prices, dates, dates[5], "ice_resonance")
    assert max(r["horizon"] for r in earlier) == 4
    assert max(r["horizon"] for r in later) == 5
    assert earlier[0]["sample_count"] == 2
    assert earlier[1]["sample_count"] == 1
    assert math.isclose(earlier[1]["mean"], .1, abs_tol=1e-12)
    # A missing intermediate price stays a missing one-session sample; it does
    # not silently change the horizon into the next available observation.
    gaps = market._event_paths(events[:1], [r for r in prices if r["trade_date"] != dates[1]], dates, dates[4], "ice_resonance")
    assert 1 not in {r["horizon"] for r in gaps}
    assert 2 in {r["horizon"] for r in gaps}


def test_available_auxiliary_inputs_are_shown_and_true_missing_sources_are_named(store):
    page = market.panel("sentiment", "indicators", {"date": "2026-11-06", "display_item": "MAIN20"}, store)
    rows = {r["indicator_id"]: r for r in section(page, "display-only")["rows"]}
    assert set(rows) == {"MAIN20", "PMI", "VIX", "VIX_TERM", "HS300_PE20"}
    assert rows["MAIN20"]["raw_value"] is not None
    assert rows["PMI"]["raw_value"] is not None
    assert rows["VIX"]["raw_value"] is not None
    assert rows["VIX"]["source_date"] < "2026-11-06"
    assert rows["VIX_TERM"]["raw_value"] is None and "VIX3M" in rows["VIX_TERM"]["missing_reason"]
    assert rows["HS300_PE20"]["raw_value"] is None and "4032" in rows["HS300_PE20"]["missing_reason"]
    assert section(page, "display-history")["rows"]
    overview = market.panel("chips", "overview", {"date": "2026-11-06"}, store)
    benchmark = section(overview, "benchmark")["rows"]
    assert benchmark and all(r["symbol"] == "000985.CSI" and r["trade_date"] <= "2026-11-06" for r in benchmark)


def test_completed_market_interactions(store):
    overview = market.panel("valuation", "overview", {}, store)
    bubble = section(overview, "map")
    assert (bubble["kind"], bubble["x"], bubble["y"], bubble["size_key"]) == ("bubble", "pe_ttm_pct_5y", "dividend_yield_pct_5y", "amount20")
    assert bubble["action"]["key"] == "symbol"
    assert all("group" in r and "band" in r for r in section(overview, "matrix")["rows"])
    v3 = market.panel("valuation", "index", {"win": "3"}, store)
    v5 = market.panel("valuation", "index", {"win": "5"}, store)
    c3, c5 = section(v3, "channel"), section(v5, "channel")
    assert "close" in c3["secondary_axis"] and any(r.get("close") for r in c3["rows"])
    assert any(a.get("pe_ttm_channel_20") != b.get("pe_ttm_channel_20") for a, b in zip(c3["rows"], c5["rows"]))
    assert all(r["group"] == "宽基" for r in section(v3, "peers")["rows"])
    assert any(r.get("price_return") is not None for r in section(v3, "peers")["rows"])
    med = section(market.panel("chips", "overview", {"mode": "med"}, store), "segments")
    assert any(r.get("winner_rate") is not None for r in med["rows"])
    sentiment = market.panel("sentiment", "overview", {"w_valuation": 2}, store)
    assert section(sentiment, "gauge")["kind"] == "gauge"
    assert section(sentiment, "radar")["kind"] == "radar"
    assert section(sentiment, "gauge")["actions"][0]["params"]["w_valuation"] == 1
    timeline = section(market.panel("sentiment", "events", {}, store), "timeline")
    assert timeline["kind"] == "timeline" and timeline["y"] == "event_type"
    assert timeline["action"]["params"]["event"] == "$event_code"
    nodes = section(market.panel("sentiment", "replay", {}, store), "nodes")
    assert nodes["action"]["params"]["node"] == "$trade_date"


def test_event_detail_never_exposes_unmatured_prices(store):
    result = market.panel("chips", "screen", {"date": "2020-01-20", "backtest_code": "000007.SZ", "page_size": "200"}, store)
    rows = section(result, "backtest-events")["rows"]
    assert rows
    for row in rows:
        for h in (5, 20):
            if row[f"end_date_{h}d"] is None:
                assert row[f"end_close_{h}d"] is None
                assert row[f"return_{h}d"] is None
                assert row[f"excess_{h}d"] is None
            else:
                assert str(row[f"end_date_{h}d"]) <= result["data_date"]
            if row[f"return_{h}d"] is not None and row[f"benchmark_end_{h}d"] is not None:
                assert row[f"excess_{h}d"] == pytest.approx(row[f"return_{h}d"] - (row[f"benchmark_end_{h}d"] / row["benchmark_start"] - 1))
