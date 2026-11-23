from __future__ import annotations

import pytest

from backtest_platform.strategy_store import StrategyStore


def sample_payload(name: str = "测试策略") -> dict:
    return {
        "name": name,
        "description": "保存一次有价值的回测",
        "config": {
            "sleeves": [
                {"id": "defensive", "name": "防守", "weight": 60, "internalMode": "equal", "funds": [{"id": "A", "weight": 60}]}
            ],
            "weightMode": "manual",
            "benchmark": "000300.SH",
            "startDate": "2021-01-01",
            "endDate": "2026-05-29",
            "backtestPayload": {
                "funds": [{"id": "A", "weight": 60}],
                "weightMode": "manual",
                "benchmark": "000300.SH",
                "startDate": "2021-01-01",
                "endDate": "2026-05-29",
            },
        },
        "result_snapshot": {
            "window": {"start": "2021-01-01", "end": "2026-05-29", "days": 100},
            "funds": [{"id": "A", "name": "基金A", "weight": 0.6}],
            "metrics": {
                "annual_return": 0.1234,
                "max_drawdown": -0.2,
                "annual_volatility": 0.18,
                "sharpe": 0.8,
                "calmar": 0.62,
            },
            "series": {"portfolio": [], "benchmark": [], "drawdown": []},
            "annual_returns": [],
            "annual_contributions": [],
            "fund_contributions": [],
        },
    }


def test_strategy_store_creates_lists_and_reads_full_strategy(tmp_path) -> None:
    store = StrategyStore(tmp_path)

    created = store.create(sample_payload())
    listed = store.list()
    loaded = store.get(created["id"])

    assert created["name"] == "测试策略"
    assert created["summary"] == {
        "annual_return": 0.1234,
        "max_drawdown": -0.2,
        "annual_volatility": 0.18,
        "sharpe": 0.8,
        "calmar": 0.62,
        "fund_count": 1,
        "window": "2021-01-01 至 2026-05-29",
    }
    assert [item["id"] for item in listed] == [created["id"]]
    assert "result_snapshot" not in listed[0]
    assert loaded["config"]["benchmark"] == "000300.SH"
    assert loaded["result_snapshot"]["metrics"]["annual_return"] == 0.1234


def test_strategy_store_update_only_changes_metadata(tmp_path) -> None:
    store = StrategyStore(tmp_path)
    created = store.create(sample_payload())

    updated = store.update(created["id"], {"name": "改名策略", "description": "新备注"})
    loaded = store.get(created["id"])

    assert updated["name"] == "改名策略"
    assert updated["description"] == "新备注"
    assert updated["created_at"] == created["created_at"]
    assert updated["updated_at"] >= created["updated_at"]
    assert loaded["config"] == created["config"]
    assert loaded["result_snapshot"] == created["result_snapshot"]


def test_strategy_store_deletes_strategy(tmp_path) -> None:
    store = StrategyStore(tmp_path)
    created = store.create(sample_payload())

    store.delete(created["id"])

    assert store.list() == []
    with pytest.raises(FileNotFoundError):
        store.get(created["id"])


@pytest.mark.parametrize(
    "payload,match",
    [
        ({**sample_payload(), "name": ""}, "策略名称不能为空"),
        ({**sample_payload(), "config": None}, "策略配置不能为空"),
        ({**sample_payload(), "result_snapshot": None}, "回测结果快照不能为空"),
    ],
)
def test_strategy_store_rejects_invalid_payload(tmp_path, payload, match) -> None:
    store = StrategyStore(tmp_path)

    with pytest.raises(ValueError, match=match):
        store.create(payload)


def test_strategy_store_rejects_invalid_id(tmp_path) -> None:
    store = StrategyStore(tmp_path)

    with pytest.raises(ValueError, match="非法策略 ID"):
        store.get("../bad")


def test_strategy_store_rejects_error_snapshot_without_writing_file(tmp_path) -> None:
    store = StrategyStore(tmp_path)
    payload = sample_payload("错误快照")
    payload["result_snapshot"] = {"error": "Unexpected token '<', '<!DOCTYPE' is not valid JSON"}

    with pytest.raises(ValueError, match="回测结果快照格式错误"):
        store.create(payload)

    assert list(tmp_path.glob("strategy_*.json")) == []
