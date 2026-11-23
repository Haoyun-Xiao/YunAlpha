from __future__ import annotations

import json
import threading
from base64 import b64encode
from io import BytesIO
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pandas as pd
import pytest

import backtest_platform.server as server_module
from backtest_platform.fund_backtest import FundSeries
from backtest_platform.portfolio_store import PortfolioStore
from tests.test_portfolio_store import sample_backtest_snapshot, sample_portfolio_payload


@pytest.fixture()
def portfolio_server(tmp_path):
    previous_portfolio_store = getattr(server_module, "portfolio_store", None)
    previous_store = getattr(server_module, "store", None)
    previous_data_dir = getattr(server_module, "DATA_DIR", None)
    previous_strategy_dir = getattr(server_module, "STRATEGY_DIR", None)

    dates = pd.bdate_range("2021-01-04", "2021-03-31")
    nav_a = [1.0, 1.1, 1.2] + [1.2] * (len(dates) - 3)
    nav_b = [1.0, 1.0, 1.1] + [1.1] * (len(dates) - 3)

    class FakeDataStore:
        def __init__(self) -> None:
            self.funds = {
                "A": FundSeries("A", "基金A", "经理A", pd.Series(nav_a, index=dates)),
                "B": FundSeries("B", "基金B", "经理B", pd.Series(nav_b, index=dates)),
            }
            self.meta = {
                "A": type("Meta", (), {"name": "基金A"})(),
                "B": type("Meta", (), {"name": "基金B"})(),
            }
            self.benchmarks = {
                "000300.SH": FundSeries("000300.SH", "沪深300", "", pd.Series([1.0] * len(dates), index=dates))
            }

        def ensure_loaded(self) -> None:
            return None

    server_module.portfolio_store = PortfolioStore(tmp_path / "portfolios")
    server_module.store = FakeDataStore()
    server_module.DATA_DIR = tmp_path
    server_module.STRATEGY_DIR = tmp_path / "strategies"
    server_module.STRATEGY_DIR.mkdir()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server_module.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{httpd.server_port}"
    try:
        yield base_url
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)
        if previous_portfolio_store is not None:
            server_module.portfolio_store = previous_portfolio_store
        if previous_store is not None:
            server_module.store = previous_store
        if previous_data_dir is not None:
            server_module.DATA_DIR = previous_data_dir
        if previous_strategy_dir is not None:
            server_module.STRATEGY_DIR = previous_strategy_dir


def request_json(base_url: str, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = Request(
        base_url + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_build_backtest_request_default_end_date_uses_current_data_cutoff() -> None:
    request = server_module.build_backtest_request({"funds": [{"id": "A"}]})

    assert request.end_date == "2026-06-30"


def test_portfolio_api_create_list_read_update_delete(portfolio_server) -> None:
    payload = {**sample_portfolio_payload("API组合"), "dataFrequency": "weekly"}
    status, created = request_json(portfolio_server, "POST", "/api/portfolios", payload)
    assert status == 200
    portfolio_id = created["id"]
    assert created["dataFrequency"] == "weekly"

    status, listed = request_json(portfolio_server, "GET", "/api/portfolios")
    assert status == 200
    assert listed["portfolios"][0]["id"] == portfolio_id
    assert listed["portfolios"][0]["name"] == "API组合"
    assert listed["portfolios"][0]["dataFrequency"] == "weekly"
    assert "rebalancePlan" not in listed["portfolios"][0]

    status, loaded = request_json(portfolio_server, "GET", f"/api/portfolios/{portfolio_id}")
    assert status == 200
    assert loaded["rebalancePlan"][0]["holdings"][0]["fundId"] == "A"
    assert loaded["dataFrequency"] == "weekly"

    status, updated = request_json(
        portfolio_server,
        "PUT",
        f"/api/portfolios/{portfolio_id}",
        {**loaded, "name": "API组合改名"},
    )
    assert status == 200
    assert updated["name"] == "API组合改名"

    status, deleted = request_json(portfolio_server, "DELETE", f"/api/portfolios/{portfolio_id}")
    assert status == 200
    assert deleted == {"ok": True}


def test_portfolio_api_reorders_portfolios(portfolio_server) -> None:
    ids = []
    for name in ["API组合一", "API组合二", "API组合三"]:
        status, created = request_json(portfolio_server, "POST", "/api/portfolios", sample_portfolio_payload(name))
        assert status == 200
        ids.append(created["id"])

    status, body = request_json(portfolio_server, "POST", "/api/portfolios/order", {"ids": [ids[2], ids[0], ids[1]]})

    assert status == 200
    assert [item["id"] for item in body["portfolios"]] == [ids[2], ids[0], ids[1]]
    assert [item["displayOrder"] for item in body["portfolios"]] == [0, 1, 2]

    status, listed = request_json(portfolio_server, "GET", "/api/portfolios")
    assert status == 200
    assert [item["id"] for item in listed["portfolios"]] == [ids[2], ids[0], ids[1]]


def test_portfolio_api_backtest_saves_last_snapshot(portfolio_server) -> None:
    payload = sample_portfolio_payload("可回测组合")
    payload["dataFrequency"] = "daily"
    payload["endDate"] = "2021-03-31"
    payload["rebalancePlan"] = [
        {
            "date": "2021-01-04",
            "source": "initial",
            "holdings": [
                {"fundId": "A", "sleeveId": "defensive", "weight": 50},
                {"fundId": "B", "sleeveId": "balanced", "weight": 50},
            ],
        }
    ]
    status, created = request_json(portfolio_server, "POST", "/api/portfolios", payload)
    assert status == 200

    status, body = request_json(portfolio_server, "POST", f"/api/portfolios/{created['id']}/backtest")
    assert status == 200
    assert "lastBacktestSnapshot" not in body["portfolio"]
    assert body["result"]["metrics"]["total_return"] == pytest.approx(0.15)
    assert body["result"]["frequency"] == "daily"

    _, loaded = request_json(portfolio_server, "GET", f"/api/portfolios/{created['id']}")
    assert loaded["lastBacktestSnapshot"]["metrics"]["total_return"] == pytest.approx(0.15)
    assert loaded["lastBacktestSnapshot"]["frequency"] == "daily"
    assert loaded["lastBacktestSnapshot"]["window"]["end"] == "2021-03-31"


def test_portfolio_api_backtest_saves_all_frequency_snapshots(portfolio_server, monkeypatch) -> None:
    def fake_backtest(payload: dict, **_: object) -> dict:
        frequency = payload.get("dataFrequency") or "daily"
        return {
            "frequency": frequency,
            "frequency_label": {"daily": "日频", "weekly": "周频", "monthly": "月频"}[frequency],
            "window": {"start": "2021-01-04", "end": "2021-01-06", "days": 2},
            "funds": [],
            "weights": {},
            "warnings": [],
            "metrics": {"total_return": 0.01, "annual_return": 0.02, "max_drawdown": -0.01},
            "series": {"portfolio": [], "benchmark": []},
            "rebalance_dates": [],
        }

    monkeypatch.setattr(server_module, "run_backtest_payload", fake_backtest)
    payload = {**sample_portfolio_payload("三频缓存组合"), "dataFrequency": "weekly"}
    status, created = request_json(portfolio_server, "POST", "/api/portfolios", payload)
    assert status == 200

    status, body = request_json(portfolio_server, "POST", f"/api/portfolios/{created['id']}/backtest")

    assert status == 200
    assert body["result"]["frequency"] == "weekly"
    assert "backtestSnapshots" not in body["portfolio"]
    assert "lastBacktestSnapshot" not in body["portfolio"]
    assert body["portfolio"]["hasBacktest"] is True
    assert body["portfolio"]["availableFrequencies"] == ["daily", "weekly", "monthly"]

    _, loaded = request_json(portfolio_server, "GET", f"/api/portfolios/{created['id']}")
    assert set(loaded["backtestSnapshots"]) == {"daily", "weekly", "monthly"}
    assert loaded["lastBacktestSnapshot"]["frequency"] == "weekly"


def test_portfolio_api_update_without_snapshots_preserves_existing_backtest_cache(portfolio_server, monkeypatch) -> None:
    def fake_backtest(payload: dict, **_: object) -> dict:
        frequency = payload.get("dataFrequency") or "daily"
        return {
            "frequency": frequency,
            "frequency_label": {"daily": "日频", "weekly": "周频", "monthly": "月频"}[frequency],
            "window": {"start": "2021-01-04", "end": "2021-01-06", "days": 2},
            "funds": [],
            "weights": {},
            "warnings": [],
            "metrics": {"total_return": 0.01, "annual_return": 0.02, "max_drawdown": -0.01},
            "series": {"portfolio": [], "benchmark": []},
            "rebalance_dates": [],
        }

    monkeypatch.setattr(server_module, "run_backtest_payload", fake_backtest)
    payload = {**sample_portfolio_payload("保留快照组合"), "dataFrequency": "daily"}
    status, created = request_json(portfolio_server, "POST", "/api/portfolios", payload)
    assert status == 200
    status, _ = request_json(portfolio_server, "POST", f"/api/portfolios/{created['id']}/backtest")
    assert status == 200

    status, loaded = request_json(portfolio_server, "GET", f"/api/portfolios/{created['id']}")
    assert status == 200
    assert set(loaded["backtestSnapshots"]) == {"daily", "weekly", "monthly"}
    slim_payload = {
        key: value
        for key, value in loaded.items()
        if key not in {"backtestSnapshots", "lastBacktestSnapshot", "latestHoldings"}
    }
    slim_payload["name"] = "保留快照组合改名"

    status, updated = request_json(portfolio_server, "PUT", f"/api/portfolios/{created['id']}", slim_payload)

    assert status == 200
    assert "backtestSnapshots" not in updated
    status, reloaded = request_json(portfolio_server, "GET", f"/api/portfolios/{created['id']}")
    assert status == 200
    assert reloaded["name"] == "保留快照组合改名"
    assert set(reloaded["backtestSnapshots"]) == {"daily", "weekly", "monthly"}
    assert reloaded["lastBacktestSnapshot"]["frequency"] == "daily"


def test_portfolio_api_update_backtest_inputs_clears_existing_backtest_cache(portfolio_server, monkeypatch) -> None:
    def fake_backtest(payload: dict, **_: object) -> dict:
        frequency = payload.get("dataFrequency") or "daily"
        return {
            "frequency": frequency,
            "frequency_label": {"daily": "日频", "weekly": "周频", "monthly": "月频"}[frequency],
            "window": {"start": "2021-01-04", "end": "2021-01-06", "days": 2},
            "funds": [],
            "weights": {},
            "warnings": [],
            "metrics": {"total_return": 0.01, "annual_return": 0.02, "max_drawdown": -0.01},
            "series": {"portfolio": [], "benchmark": []},
            "rebalance_dates": [],
        }

    monkeypatch.setattr(server_module, "run_backtest_payload", fake_backtest)
    payload = {**sample_portfolio_payload("清空快照组合"), "dataFrequency": "daily"}
    status, created = request_json(portfolio_server, "POST", "/api/portfolios", payload)
    assert status == 200
    status, _ = request_json(portfolio_server, "POST", f"/api/portfolios/{created['id']}/backtest")
    assert status == 200

    status, loaded = request_json(portfolio_server, "GET", f"/api/portfolios/{created['id']}")
    assert status == 200
    changed_payload = {
        key: value
        for key, value in loaded.items()
        if key not in {"backtestSnapshots", "lastBacktestSnapshot", "latestHoldings"}
    }
    changed_payload["benchmark"] = "000905.SH"

    status, updated = request_json(portfolio_server, "PUT", f"/api/portfolios/{created['id']}", changed_payload)

    assert status == 200
    assert updated["hasBacktest"] is False
    assert updated["availableFrequencies"] == []
    status, reloaded = request_json(portfolio_server, "GET", f"/api/portfolios/{created['id']}")
    assert status == 200
    assert reloaded["lastBacktestSnapshot"] is None
    assert reloaded["backtestSnapshots"] == {}


def test_portfolio_api_reads_single_cached_snapshot(portfolio_server, monkeypatch) -> None:
    def fake_backtest_core(request, funds, benchmarks) -> dict:
        frequency = request.data_frequency
        result = {
            "frequency": frequency,
            "frequency_label": {"daily": "日频", "weekly": "周频", "monthly": "月频"}[frequency],
            "window": {"start": "2021-01-04", "end": "2021-01-06", "days": 2},
            "funds": [],
            "weights": {},
            "warnings": [],
            "metrics": {"total_return": 0.01, "annual_return": 0.02, "max_drawdown": -0.01},
            "series": {"portfolio": [], "benchmark": []},
            "rebalance_dates": [],
        }
        return {"result": result, "end_weights": pd.DataFrame()}

    monkeypatch.setattr(server_module, "run_fund_backtest_core", fake_backtest_core)
    payload = {**sample_portfolio_payload("单频读取组合"), "dataFrequency": "daily"}
    status, created = request_json(portfolio_server, "POST", "/api/portfolios", payload)
    assert status == 200
    status, _ = request_json(portfolio_server, "POST", f"/api/portfolios/{created['id']}/backtest")
    assert status == 200

    status, body = request_json(portfolio_server, "GET", f"/api/portfolios/{created['id']}/snapshots/weekly")

    assert status == 200
    assert body["snapshot"]["frequency"] == "weekly"
    assert body["snapshot"]["frequency_label"] == "周频"
    assert body["snapshot"]["metrics"]["total_return"] == 0.01


def test_portfolio_api_reads_legacy_last_snapshot_as_daily_snapshot(portfolio_server) -> None:
    snapshot = {
        **sample_backtest_snapshot(),
        "rebalance_dates": [],
    }
    payload = {
        **sample_portfolio_payload("旧单快照组合"),
        "lastBacktestSnapshot": snapshot,
    }
    status, created = request_json(portfolio_server, "POST", "/api/portfolios", payload)
    assert status == 200

    status, body = request_json(portfolio_server, "GET", f"/api/portfolios/{created['id']}/snapshots/daily")

    assert status == 200
    assert body["snapshot"]["metrics"]["annual_return"] == 0.1


def test_portfolio_analysis_window_returns_temporary_attribution_without_saving(portfolio_server, monkeypatch) -> None:
    calls: list[dict] = []

    def fake_analysis_window(request, funds, benchmarks, start_date: str, end_date: str) -> dict:
        calls.append({"request": request, "start_date": start_date, "end_date": end_date, "fund_count": len(funds), "benchmark_count": len(benchmarks)})
        return {
            "frequency": request.data_frequency,
            "frequency_label": "周频",
            "window": {"start": start_date, "end": end_date, "days": 5},
            "warnings": ["样本较短"],
            "attribution": {
                "window": {"start": start_date, "end": end_date},
                "rows": [{"id": "portfolio", "type": "portfolio", "name": "组合"}],
                "correlation": {"sleeves": {"labels": ["防守压仓"], "matrix": [[1.0]], "average": 1.0}},
            },
        }

    monkeypatch.setattr(server_module, "run_fund_backtest_analysis_window", fake_analysis_window)
    payload = {
        **sample_portfolio_payload("临时区间组合"),
        "dataFrequency": "daily",
        "lastBacktestSnapshot": sample_backtest_snapshot(),
    }
    status, created = request_json(portfolio_server, "POST", "/api/portfolios", payload)
    assert status == 200

    status, body = request_json(
        portfolio_server,
        "POST",
        f"/api/portfolios/{created['id']}/analysis-window",
        {"frequency": "weekly", "startDate": "2021-01-15", "endDate": "2021-02-05"},
    )

    assert status == 200
    assert len(calls) == 1
    call = calls[0]
    assert call["request"].data_frequency == "weekly"
    assert call["request"].start_date == "2021-01-01"
    assert call["request"].end_date == "2026-06-30"
    assert call["start_date"] == "2021-01-15"
    assert call["end_date"] == "2021-02-05"
    assert call["fund_count"] == 2
    assert call["benchmark_count"] == 1
    assert body["frequency"] == "weekly"
    assert body["window"] == {"start": "2021-01-15", "end": "2021-02-05", "days": 5}
    assert body["warnings"] == ["样本较短"]
    assert body["attribution"]["rows"][0]["name"] == "组合"

    _, loaded = request_json(portfolio_server, "GET", f"/api/portfolios/{created['id']}")
    assert loaded["lastBacktestSnapshot"] == payload["lastBacktestSnapshot"]
    assert loaded["dataFrequency"] == "daily"


def test_portfolio_api_reads_active_portfolio_without_backtest_snapshots(portfolio_server, monkeypatch) -> None:
    def fake_backtest(payload: dict, **_: object) -> dict:
        frequency = payload.get("dataFrequency") or "daily"
        return {
            "frequency": frequency,
            "frequency_label": {"daily": "日频", "weekly": "周频", "monthly": "月频"}[frequency],
            "window": {"start": "2021-01-04", "end": "2021-01-06", "days": 2},
            "funds": [],
            "weights": {},
            "warnings": [],
            "metrics": {"total_return": 0.01, "annual_return": 0.02, "max_drawdown": -0.01},
            "series": {"portfolio": [], "benchmark": []},
            "rebalance_dates": [],
        }

    monkeypatch.setattr(server_module, "run_backtest_payload", fake_backtest)
    payload = {**sample_portfolio_payload("轻量打开组合"), "dataFrequency": "weekly"}
    status, created = request_json(portfolio_server, "POST", "/api/portfolios", payload)
    assert status == 200
    status, _ = request_json(portfolio_server, "POST", f"/api/portfolios/{created['id']}/backtest")
    assert status == 200

    status, body = request_json(portfolio_server, "GET", f"/api/portfolios/{created['id']}/active")

    assert status == 200
    assert body["id"] == created["id"]
    assert "backtestSnapshots" not in body
    assert "lastBacktestSnapshot" not in body
    assert body["hasBacktest"] is True
    assert body["availableFrequencies"] == ["daily", "weekly", "monthly"]
    assert isinstance(body["latestHoldings"], list)


def test_ensure_legacy_strategy_portfolios_runs_once_per_process(tmp_path, monkeypatch) -> None:
    class FakePortfolioStore:
        def __init__(self) -> None:
            self.calls = 0

        def import_legacy_strategies(self, strategy_dir) -> int:
            self.calls += 1
            return 0

    fake_store = FakePortfolioStore()
    monkeypatch.setattr(server_module, "portfolio_store", fake_store)
    monkeypatch.setattr(server_module, "STRATEGY_DIR", tmp_path)
    monkeypatch.setattr(server_module, "_legacy_strategy_portfolios_checked", False, raising=False)

    server_module.ensure_legacy_strategy_portfolios()
    server_module.ensure_legacy_strategy_portfolios()

    assert fake_store.calls == 1


def test_run_backtest_payloads_keeps_required_frequency_when_optional_frequency_fails(monkeypatch) -> None:
    calls: list[str] = []

    def fake_backtest_core(request, funds, benchmarks) -> dict:
        frequency = request.data_frequency
        calls.append(frequency)
        if frequency == "monthly":
            raise ValueError("月频样本不足")
        return {"result": {"frequency": frequency, "warnings": []}, "end_weights": pd.DataFrame()}

    monkeypatch.setattr(server_module, "run_fund_backtest_core", fake_backtest_core)

    snapshots = server_module.run_backtest_payloads({"dataFrequency": "daily"}, "daily")

    assert calls == ["daily", "weekly", "monthly"]
    assert set(snapshots) == {"daily", "weekly"}
    assert snapshots["daily"]["warnings"] == ["月频回测失败：月频样本不足"]


def test_run_backtest_payloads_still_requires_active_frequency(monkeypatch) -> None:
    def fake_backtest_core(request, funds, benchmarks) -> dict:
        frequency = request.data_frequency
        if frequency == "daily":
            raise ValueError("日频样本不足")
        return {"result": {"frequency": frequency, "warnings": []}, "end_weights": pd.DataFrame()}

    monkeypatch.setattr(server_module, "run_fund_backtest_core", fake_backtest_core)

    with pytest.raises(RuntimeError, match="日频回测失败：日频样本不足"):
        server_module.run_backtest_payloads({"dataFrequency": "daily"}, "daily")


def test_run_backtest_payloads_reuses_one_exposure_snapshot(monkeypatch) -> None:
    class FakeStore:
        funds = {}
        benchmarks = {}

        def ensure_loaded(self) -> None:
            return None

    def fake_run_fund_backtest_core(request, funds, benchmarks):
        result = {
            "frequency": request.data_frequency,
            "frequency_label": {"daily": "日频", "weekly": "周频", "monthly": "月频"}[request.data_frequency],
            "window": {"start": "2021-01-01", "end": "2021-01-31", "days": 1},
            "funds": [{"id": "A", "name": "基金A"}],
            "weights": {"A": 1.0},
            "warnings": [],
            "metrics": {},
            "series": {"portfolio": [], "benchmark": []},
            "rebalance_dates": [],
        }
        return {"result": result, "end_weights": pd.DataFrame({"A": [1.0]}, index=pd.to_datetime(["2021-01-31"]))}

    exposure_calls: list[dict[str, object]] = []

    def fake_exposure(data_dir, funds, weights, sleeves, end_date, **kwargs):
        exposure_calls.append({"end_date": end_date, **kwargs})
        return {"snapshot_date": end_date, "factor_rows": []}

    monkeypatch.setattr(server_module, "store", FakeStore())
    monkeypatch.setattr(server_module, "run_fund_backtest_core", fake_run_fund_backtest_core)
    monkeypatch.setattr(server_module, "build_exposure_snapshot", fake_exposure)

    snapshots = server_module.run_backtest_payloads(
        {
            "funds": [{"id": "A"}],
            "benchmark": "000300.SH",
            "startDate": "2021-01-01",
            "endDate": "2021-01-31",
            "dataFrequency": "weekly",
        },
        "weekly",
    )

    assert [item["end_date"] for item in exposure_calls] == ["2021-01-31"]
    assert exposure_calls[0]["start_date"] == "2021-01-01"
    assert isinstance(exposure_calls[0]["weight_history"], pd.DataFrame)
    assert {snapshot["exposure"]["snapshot_date"] for snapshot in snapshots.values()} == {"2021-01-31"}


def test_portfolio_api_cash_mode_backtest_saves_snapshot_and_latest_real_holdings(portfolio_server) -> None:
    payload = sample_portfolio_payload("资金回测组合")
    payload["startDate"] = "2021-01-04"
    payload["endDate"] = "2021-03-31"
    payload["initialCash"] = 1000.0
    payload["feeConfig"] = {"purchase": 0.001, "redemption": 0.0}
    payload["rebalancePlan"] = [
        {
            "date": "2021-01-04",
            "source": "initial",
            "cashFlow": 0,
            "holdings": [{"fundId": "A", "sleeveId": "defensive", "weight": 100}],
        }
    ]

    status, created = request_json(portfolio_server, "POST", "/api/portfolios", payload)
    assert status == 200
    assert created["initialCash"] == 1000.0
    assert created["feeConfig"] == {"purchase": 0.001, "redemption": 0.0, "transaction": 0.0}

    status, body = request_json(portfolio_server, "POST", f"/api/portfolios/{created['id']}/backtest")
    assert status == 200
    result = body["result"]
    holding = body["portfolio"]["latestHoldings"][0]

    assert result["cash_mode"] is True
    assert result["metrics"]["final_value"] == pytest.approx(1198.801)
    assert result["metrics"]["total_fees"] == pytest.approx(0.999)
    assert holding["fundId"] == "A"
    assert holding["quantity"] == pytest.approx(999.0)
    assert holding["targetWeight"] == pytest.approx(1.0)
    assert holding["marketValue"] == pytest.approx(1198.8)
    assert holding["costAmount"] == pytest.approx(999.999)
    assert holding["profit"] == pytest.approx(198.801)

    status, listed = request_json(portfolio_server, "GET", "/api/portfolios")
    assert status == 200
    assert listed["portfolios"][0]["cashMode"] is True
    assert listed["portfolios"][0]["summary"]["finalValue"] == pytest.approx(1198.801)


def test_portfolio_latest_holdings_use_drifted_weight_and_latest_nav_fields(portfolio_server) -> None:
    payload = sample_portfolio_payload("最新持仓")
    payload["rebalancePlan"] = [
        {
            "date": "2021-01-04",
            "source": "initial",
            "holdings": [
                {"fundId": "A", "sleeveId": "defensive", "weight": 50},
                {"fundId": "B", "sleeveId": "balanced", "weight": 50},
            ],
        }
    ]
    status, created = request_json(portfolio_server, "POST", "/api/portfolios", payload)
    assert status == 200

    status, loaded = request_json(portfolio_server, "GET", f"/api/portfolios/{created['id']}")

    assert status == 200
    rows = {row["fundId"]: row for row in loaded["latestHoldings"]}
    assert rows["A"]["weight"] == pytest.approx(0.6 / 1.15)
    assert rows["B"]["weight"] == pytest.approx(0.55 / 1.15)
    assert rows["A"]["targetWeight"] == pytest.approx(0.5)
    assert rows["B"]["targetWeight"] == pytest.approx(0.5)
    assert rows["A"]["latestNav"] == pytest.approx(1.2)
    assert rows["A"]["navDate"] == "2021-03-31"
    assert rows["A"]["dailyReturn"] == pytest.approx(0.0)
    assert rows["A"]["ytdReturn"] == pytest.approx(0.2)


def test_rebalance_plan_import_accepts_single_sheet_excel(portfolio_server) -> None:
    frame = pd.DataFrame(
        [
            {"调仓日期": "2021-01-04", "子组合": "防守", "基金代码": "A", "基金名称": "基金A", "权重%": 60},
            {"调仓日期": "2021-01-04", "子组合": "稳健", "基金代码": "B", "基金名称": "基金B", "权重%": 40},
        ]
    )
    buffer = BytesIO()
    frame.to_excel(buffer, index=False)

    status, body = request_json(
        portfolio_server,
        "POST",
        "/api/rebalance-plan/import",
        {"filename": "plan.xlsx", "contentBase64": b64encode(buffer.getvalue()).decode("ascii")},
    )

    assert status == 200
    assert body["rebalancePlan"] == [
        {
            "date": "2021-01-04",
            "source": "excel_imported",
            "holdings": [
                {"fundId": "A", "sleeveId": "defensive", "weight": 60.0},
                {"fundId": "B", "sleeveId": "balanced", "weight": 40.0},
            ],
        }
    ]
