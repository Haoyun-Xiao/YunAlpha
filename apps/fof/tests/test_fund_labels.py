from __future__ import annotations

import backtest_platform.server as server_module


def test_load_fund_labels_reads_standard_csv(tmp_path) -> None:
    path = tmp_path / "fund_labels.csv"
    path.write_text(
        "ts_code,fund_name,cluster_label,role_label,full_label,source_sheet\n"
        "008134.OF,鹏华优选价值A,防守压仓,压舱石,极致防守·压舱石,公募重点池\n"
        "000001.OF,测试基金,进攻弹性,卫星仓,弹性进攻·卫星仓,公募重点池\n",
        encoding="utf-8",
    )

    assert hasattr(server_module, "load_fund_labels")
    labels = server_module.load_fund_labels(path)

    assert labels["008134.OF"] == {
        "cluster_label": "防守压仓",
        "role_label": "压舱石",
        "full_label": "极致防守·压舱石",
    }
    assert labels["000001.OF"]["cluster_label"] == "进攻弹性"


def test_load_fund_labels_missing_file_returns_empty_mapping(tmp_path) -> None:
    assert hasattr(server_module, "load_fund_labels")
    labels = server_module.load_fund_labels(tmp_path / "missing.xlsx")

    assert labels == {}


def test_fund_data_store_adds_cluster_labels_to_asset_payload(tmp_path, monkeypatch) -> None:
    data_dir = tmp_path / "data"
    nav_dir = data_dir / "fund_nav"
    index_dir = data_dir / "index_nav"
    nav_dir.mkdir(parents=True)
    index_dir.mkdir()

    (data_dir / "fund_master.csv").write_text(
        "ts_code,fund_name,manager,fund_type,nav_file\n"
        "008134.OF,鹏华优选价值A,伍旋,混合型,fund_nav/008134.OF.csv\n",
        encoding="utf-8",
    )
    (nav_dir / "008134.OF.csv").write_text(
        "nav_date,adj_nav\n2021-01-01,1.0\n2021-01-04,1.1\n",
        encoding="utf-8",
    )
    (index_dir / "000300.SH.csv").write_text(
        "trade_date,close\n2021-01-01,1000\n2021-01-04,1010\n",
        encoding="utf-8",
    )
    label_path = data_dir / "fund_labels.csv"
    label_path.write_text(
        "ts_code,fund_name,cluster_label,role_label,full_label,source_sheet\n"
        "008134.OF,鹏华优选价值A,防守压仓,压舱石,极致防守·压舱石,公募重点池\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(server_module, "DATA_DIR", data_dir)
    monkeypatch.setattr(server_module, "FUND_MASTER", data_dir / "fund_master.csv")
    monkeypatch.setattr(server_module, "INDEX_NAV_DIR", index_dir)
    monkeypatch.setattr(server_module, "FUND_LABELS_FILE", label_path, raising=False)

    payload = server_module.FundDataStore().assets_payload()

    assert payload["funds"][0]["cluster_label"] == "防守压仓"
    assert payload["funds"][0]["role_label"] == "压舱石"
    assert payload["funds"][0]["full_label"] == "极致防守·压舱石"
