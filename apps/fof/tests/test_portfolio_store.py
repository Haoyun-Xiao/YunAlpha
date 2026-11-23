from __future__ import annotations

import json

import pytest

from backtest_platform.portfolio_store import PortfolioStore
from tests.test_strategy_store import sample_payload as sample_strategy_payload


def sample_portfolio_payload(name: str = "核心稳健组合") -> dict:
    return {
        "name": name,
        "benchmark": "000300.SH",
        "startDate": "2021-01-01",
        "endDate": "2026-06-30",
        "globalTemplate": "stable",
        "rebalancePlan": [
            {
                "date": "2021-01-04",
                "source": "initial",
                "holdings": [
                    {"fundId": "A", "sleeveId": "defensive", "weight": 50},
                    {"fundId": "B", "sleeveId": "balanced", "weight": 50},
                ],
            }
        ],
    }


def sample_backtest_snapshot() -> dict:
    return {
        "window": {"start": "2021-01-04", "end": "2026-06-30", "days": 1000},
        "funds": [{"id": "A", "weight": 0.5}, {"id": "B", "weight": 0.5}],
        "metrics": {"annual_return": 0.1, "max_drawdown": -0.2, "sharpe": 0.8},
        "series": {"portfolio": [], "benchmark": []},
    }


def test_portfolio_store_creates_lists_reads_and_updates(tmp_path) -> None:
    store = PortfolioStore(tmp_path)

    created = store.create(sample_portfolio_payload())
    listed = store.list()
    loaded = store.get(created["id"])
    updated = store.update(created["id"], {**loaded, "name": "改名组合", "lastBacktestSnapshot": sample_backtest_snapshot()})

    assert created["name"] == "核心稳健组合"
    assert created["lastBacktestSnapshot"] is None
    assert listed[0]["id"] == created["id"]
    assert listed[0]["name"] == "核心稳健组合"
    assert "rebalancePlan" not in listed[0]
    assert loaded["rebalancePlan"][0]["holdings"][0]["fundId"] == "A"
    assert updated["name"] == "改名组合"
    assert updated["lastBacktestSnapshot"]["metrics"]["annual_return"] == 0.1
    assert store.get(created["id"])["lastBacktestSnapshot"]["window"]["end"] == "2026-06-30"


def test_portfolio_store_preserves_cash_mode_fields_and_lists_summary(tmp_path) -> None:
    store = PortfolioStore(tmp_path)
    payload = sample_portfolio_payload("资金组合")
    payload["initialCash"] = 1_000_000
    payload["feeConfig"] = {"purchase": 0.0012, "redemption": 0.005, "transaction": 0.0002}
    payload["rebalancePlan"][0]["cashFlow"] = 50_000
    payload["lastBacktestSnapshot"] = {
        **sample_backtest_snapshot(),
        "cash_mode": True,
        "metrics": {
            "annual_return": 0.1,
            "max_drawdown": -0.2,
            "sharpe": 0.8,
            "final_value": 1_150_000,
            "total_profit": 100_000,
            "cash_balance": 12_345,
        },
    }

    created = store.create(payload)
    listed = store.list()[0]
    loaded = store.get(created["id"])

    assert loaded["initialCash"] == 1_000_000
    assert loaded["feeConfig"] == {"purchase": 0.0012, "redemption": 0.005, "transaction": 0.0002}
    assert loaded["rebalancePlan"][0]["cashFlow"] == 50_000
    assert listed["cashMode"] is True
    assert listed["initialCash"] == 1_000_000
    assert listed["summary"]["finalValue"] == 1_150_000
    assert listed["summary"]["totalProfit"] == 100_000
    assert listed["summary"]["cashBalance"] == 12_345


def test_portfolio_store_preserves_data_frequency_and_defaults_old_payloads_to_daily(tmp_path) -> None:
    store = PortfolioStore(tmp_path)
    weekly_payload = {**sample_portfolio_payload("周频组合"), "dataFrequency": "weekly"}

    created = store.create(weekly_payload)
    daily_created = store.create(sample_portfolio_payload("旧组合"))

    assert created["dataFrequency"] == "weekly"
    assert store.get(created["id"])["dataFrequency"] == "weekly"
    assert store.list()[0]["dataFrequency"] in {"weekly", "daily"}
    assert store.get(daily_created["id"])["dataFrequency"] == "daily"


def test_portfolio_store_preserves_rebalance_frequency_and_defaults_old_payloads_to_quarterly(tmp_path) -> None:
    store = PortfolioStore(tmp_path)
    monthly_payload = {**sample_portfolio_payload("月度调仓组合"), "rebalanceFrequency": "monthly"}

    created = store.create(monthly_payload)
    default_created = store.create(sample_portfolio_payload("旧调仓组合"))

    assert created["rebalanceFrequency"] == "monthly"
    assert store.get(created["id"])["rebalanceFrequency"] == "monthly"
    assert store.list()[0]["rebalanceFrequency"] in {"monthly", "quarterly"}
    assert store.get(default_created["id"])["rebalanceFrequency"] == "quarterly"


def test_portfolio_store_invalidates_snapshot_when_backtest_inputs_change(tmp_path) -> None:
    store = PortfolioStore(tmp_path)
    payload = {
        **sample_portfolio_payload("失效快照组合"),
        "backtestSnapshots": {"daily": sample_backtest_snapshot()},
        "lastBacktestSnapshot": sample_backtest_snapshot(),
    }
    created = store.create(payload)
    loaded = store.get(created["id"])

    renamed = store.update(created["id"], {**loaded, "name": "仅改名"})
    changed_plan = store.update(
        created["id"],
        {
            **renamed,
            "rebalancePlan": [
                {
                    "date": "2021-01-04",
                    "source": "initial",
                    "holdings": [{"fundId": "A", "sleeveId": "defensive", "weight": 100}],
                }
            ],
        },
    )

    assert renamed["lastBacktestSnapshot"]["metrics"]["annual_return"] == 0.1
    assert renamed["backtestSnapshots"]["daily"]["metrics"]["annual_return"] == 0.1
    assert changed_plan["lastBacktestSnapshot"] is None
    assert changed_plan["backtestSnapshots"] == {}


def test_portfolio_store_deletes_portfolio(tmp_path) -> None:
    store = PortfolioStore(tmp_path)
    created = store.create(sample_portfolio_payload())

    store.delete(created["id"])

    assert store.list() == []
    with pytest.raises(FileNotFoundError, match="组合不存在"):
        store.get(created["id"])


def test_portfolio_store_persists_custom_display_order(tmp_path) -> None:
    store = PortfolioStore(tmp_path)
    first = store.create(sample_portfolio_payload("组合一"))
    second = store.create(sample_portfolio_payload("组合二"))
    third = store.create(sample_portfolio_payload("组合三"))

    reordered = store.reorder([third["id"], first["id"], second["id"]])

    assert [item["id"] for item in reordered] == [third["id"], first["id"], second["id"]]
    assert [item["displayOrder"] for item in reordered] == [0, 1, 2]
    assert [item["id"] for item in store.list()] == [third["id"], first["id"], second["id"]]
    assert store.get(third["id"])["displayOrder"] == 0


@pytest.mark.parametrize(
    "payload,match",
    [
        ({**sample_portfolio_payload(), "name": ""}, "组合名称不能为空"),
        ({**sample_portfolio_payload(), "rebalancePlan": []}, "调仓计划不能为空"),
        ({**sample_portfolio_payload(), "benchmark": ""}, "基准不能为空"),
    ],
)
def test_portfolio_store_rejects_invalid_payload(tmp_path, payload, match) -> None:
    store = PortfolioStore(tmp_path)

    with pytest.raises(ValueError, match=match):
        store.create(payload)


def test_portfolio_store_rejects_invalid_id(tmp_path) -> None:
    store = PortfolioStore(tmp_path)

    with pytest.raises(ValueError, match="非法组合 ID"):
        store.get("../bad")


def test_portfolio_store_imports_legacy_strategies_as_portfolios(tmp_path) -> None:
    strategy_dir = tmp_path / "strategies"
    strategy_dir.mkdir()
    strategy = sample_strategy_payload("旧策略")
    strategy["id"] = "strategy_20260616_180534_208639"
    strategy_dir.joinpath(f"{strategy['id']}.json").write_text(
        json.dumps(strategy, ensure_ascii=False),
        encoding="utf-8",
    )
    store = PortfolioStore(tmp_path / "portfolios")

    imported = store.import_legacy_strategies(strategy_dir)
    listed = store.list()
    loaded = store.get("portfolio_from_strategy_20260616_180534_208639")

    assert imported == 1
    assert listed[0]["name"] == "旧策略"
    assert loaded["benchmark"] == "000300.SH"
    assert loaded["startDate"] == "2021-01-01"
    assert loaded["endDate"] == "2026-05-29"
    assert loaded["lastBacktestSnapshot"] == strategy["result_snapshot"]
    assert loaded["initialCash"] == 200_000
    assert loaded["feeConfig"] == {"purchase": 0.0, "redemption": 0.0, "transaction": 0.0}
    assert loaded["rebalancePlan"][0]["date"] == "2021-01-01"
    assert loaded["rebalancePlan"][0]["source"] == "initial"
    assert loaded["rebalancePlan"][0]["sleeveTargets"]["defensive"] == 100
    assert loaded["rebalancePlan"][0]["holdings"] == [{"fundId": "A", "sleeveId": "defensive", "weight": 100.0}]


def test_legacy_strategy_import_adds_cash_defaults_to_existing_imported_portfolio(tmp_path) -> None:
    strategy_dir = tmp_path / "strategies"
    strategy_dir.mkdir()
    strategy = sample_strategy_payload("旧策略")
    strategy["id"] = "strategy_20260616_180534_208639"
    strategy_dir.joinpath(f"{strategy['id']}.json").write_text(
        json.dumps(strategy, ensure_ascii=False),
        encoding="utf-8",
    )
    store = PortfolioStore(tmp_path / "portfolios")
    store.import_legacy_strategies(strategy_dir)
    portfolio_path = tmp_path / "portfolios" / "portfolio_from_strategy_20260616_180534_208639.json"
    portfolio = json.loads(portfolio_path.read_text(encoding="utf-8"))
    portfolio.pop("initialCash", None)
    portfolio.pop("feeConfig", None)
    portfolio_path.write_text(json.dumps(portfolio, ensure_ascii=False), encoding="utf-8")

    imported_again = store.import_legacy_strategies(strategy_dir)
    loaded = store.get("portfolio_from_strategy_20260616_180534_208639")

    assert imported_again == 0
    assert loaded["initialCash"] == 200_000
    assert loaded["feeConfig"] == {"purchase": 0.0, "redemption": 0.0, "transaction": 0.0}


def test_legacy_strategy_import_marker_prevents_deleted_portfolio_from_reappearing(tmp_path) -> None:
    strategy_dir = tmp_path / "strategies"
    strategy_dir.mkdir()
    strategy = sample_strategy_payload("旧策略")
    strategy["id"] = "strategy_20260616_180534_208639"
    strategy_dir.joinpath(f"{strategy['id']}.json").write_text(
        json.dumps(strategy, ensure_ascii=False),
        encoding="utf-8",
    )
    store = PortfolioStore(tmp_path / "portfolios")

    store.import_legacy_strategies(strategy_dir)
    store.delete("portfolio_from_strategy_20260616_180534_208639")
    imported_again = store.import_legacy_strategies(strategy_dir)

    assert imported_again == 0
    assert store.list() == []
