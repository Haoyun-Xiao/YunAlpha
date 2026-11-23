from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

import backtest_platform.server as server_module
from backtest_platform.strategy_store import StrategyStore
from tests.test_strategy_store import sample_payload


@pytest.fixture()
def strategy_server(tmp_path):
    previous_store = getattr(server_module, "strategy_store", None)
    server_module.strategy_store = StrategyStore(tmp_path / "strategies")
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
        if previous_store is not None:
            server_module.strategy_store = previous_store


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


def test_strategy_api_create_list_read_update_and_delete(strategy_server) -> None:
    status, created = request_json(strategy_server, "POST", "/api/strategies", sample_payload("API策略"))
    assert status == 200
    strategy_id = created["id"]

    status, listed = request_json(strategy_server, "GET", "/api/strategies")
    assert status == 200
    assert listed["strategies"][0]["id"] == strategy_id
    assert listed["strategies"][0]["name"] == "API策略"
    assert "result_snapshot" not in listed["strategies"][0]

    status, loaded = request_json(strategy_server, "GET", f"/api/strategies/{strategy_id}")
    assert status == 200
    assert loaded["result_snapshot"]["metrics"]["annual_return"] == 0.1234

    status, updated = request_json(
        strategy_server,
        "PUT",
        f"/api/strategies/{strategy_id}",
        {"name": "API策略改名", "description": "只改元数据"},
    )
    assert status == 200
    assert updated["name"] == "API策略改名"
    assert updated["result_snapshot"] == loaded["result_snapshot"]

    status, deleted = request_json(strategy_server, "DELETE", f"/api/strategies/{strategy_id}")
    assert status == 200
    assert deleted == {"ok": True}

    status, missing = request_json(strategy_server, "GET", f"/api/strategies/{strategy_id}")
    assert status == 400
    assert "策略不存在" in missing["error"]


def test_strategy_api_rejects_empty_name(strategy_server) -> None:
    payload = sample_payload("")

    status, body = request_json(strategy_server, "POST", "/api/strategies", payload)

    assert status == 400
    assert "策略名称不能为空" in body["error"]


def test_strategy_api_rejects_error_snapshot_without_creating_strategy(strategy_server) -> None:
    payload = sample_payload("错误策略")
    payload["result_snapshot"] = {"error": "Unexpected token '<', '<!DOCTYPE' is not valid JSON"}

    status, body = request_json(strategy_server, "POST", "/api/strategies", payload)
    _, listed = request_json(strategy_server, "GET", "/api/strategies")

    assert status == 400
    assert "回测结果快照格式错误" in body["error"]
    assert listed["strategies"] == []
