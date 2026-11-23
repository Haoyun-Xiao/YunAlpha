from __future__ import annotations

import socket
from pathlib import Path

import pytest

from backtest_platform import server as server_module
from backtest_platform.server import create_server


def test_create_server_does_not_switch_to_another_port() -> None:
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    preferred_port = blocker.getsockname()[1]
    try:
        with pytest.raises(OSError):
            create_server(preferred_port)
    finally:
        blocker.close()


def test_data_store_loads_all_index_nav_csv_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    index_dir = tmp_path / "index_nav"
    index_dir.mkdir()
    (index_dir / "000300.SH.csv").write_text(
        "ts_code,trade_date,close\n000300.SH,20260101,1.0\n000300.SH,20260102,1.1\n",
        encoding="utf-8",
    )
    (index_dir / "000905.SH.csv").write_text(
        "ts_code,trade_date,close\n000905.SH,20260101,2.0\n000905.SH,20260102,2.2\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(server_module, "INDEX_NAV_DIR", index_dir)

    store = server_module.FundDataStore()
    store._load_benchmark()

    assert sorted(store.benchmarks) == ["000300.SH", "000905.SH"]
    assert store.benchmarks["000300.SH"].name == "沪深300"
    assert store.benchmarks["000905.SH"].name == "中证500"


def test_assets_payload_exposes_index_and_fund_benchmark_candidates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_dir = tmp_path / "data"
    fund_nav_dir = data_dir / "fund_nav"
    index_nav_dir = data_dir / "index_nav"
    fund_nav_dir.mkdir(parents=True)
    index_nav_dir.mkdir()
    fund_master = data_dir / "fund_master.csv"
    fund_master.write_text(
        "ts_code,fund_name,manager,fund_company,fund_type,investment_type,inception_date,nav_file\n"
        "A.OF,测试基金A,经理A,基金公司,主动权益,,2020-01-01,fund_nav/A.OF.csv\n",
        encoding="utf-8",
    )
    (fund_nav_dir / "A.OF.csv").write_text(
        "ts_code,nav_date,adj_nav\nA.OF,20260101,1.0\nA.OF,20260102,1.1\n",
        encoding="utf-8",
    )
    (index_nav_dir / "000300.SH.csv").write_text(
        "ts_code,trade_date,close\n000300.SH,20260101,1.0\n000300.SH,20260102,1.1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(server_module, "DATA_DIR", data_dir)
    monkeypatch.setattr(server_module, "FUND_MASTER", fund_master)
    monkeypatch.setattr(server_module, "FUND_LABELS_FILE", data_dir / "missing_labels.csv")
    monkeypatch.setattr(server_module, "INDEX_NAV_DIR", index_nav_dir)

    store = server_module.FundDataStore()
    payload = store.assets_payload()
    benchmarks = {item["id"]: item for item in payload["benchmarks"]}

    assert benchmarks["000300.SH"]["kind"] == "index"
    assert benchmarks["A.OF"]["kind"] == "fund"
    assert benchmarks["A.OF"]["manager"] == "经理A"
    assert store.benchmarks["A.OF"].name == "测试基金A"
