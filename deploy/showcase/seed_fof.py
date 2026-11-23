"""Create the public illustrative portfolio in the isolated deployment."""
from backtest_platform.server import (
    initialize_state, portfolio_store, run_backtest_payloads, store,
)


if __name__ == "__main__":
    initialize_state()
    store.ensure_loaded()
    if not portfolio_store.list():
        sample = portfolio_store.create({
            "name": "FOF 公开演示组合",
            "benchmark": "000300.SH",
            "startDate": "2022-01-01",
            "endDate": "2026-06-30",
            "initialCash": 200000,
            "rebalancePlan": [{
                "date": "2022-01-04",
                "source": "initial",
                "holdings": [
                    {"fundId": "519195.OF", "sleeveId": "balanced", "weight": 50},
                    {"fundId": "004814.OF", "sleeveId": "defensive", "weight": 50},
                ],
            }],
        })
        portfolio_store.save_backtest_snapshots(sample["id"], run_backtest_payloads(sample, "daily"), "daily")
