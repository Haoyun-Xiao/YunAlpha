from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from backtest_platform.exposure_analysis import build_exposure_snapshot
from backtest_platform.workspace import DATA_DIR


def _write_fixture_data(data_dir: Path) -> None:
    query_dir = data_dir / "query"
    query_dir.mkdir(parents=True)
    pd.DataFrame(
        [
            {"ts_code": "A", "end_date": "2026-03-31", "report_type": "q1_quarterly", "ann_date": "2026-04-20", "factor_date": "2026-03-31", "matched_pct": 1.0, "is_simulated": True, "BETA": 1.0, "RSTR": 0.2, "LNCAP": 0.0, "EARNYILD": 0.0, "GROWTH": 0.5, "LEVERAGE": 0.0, "LIQUIDITY": 0.0, "RESVOL": 0.0, "BTOP": 0.0, "NLSIZE": 0.0},
            {"ts_code": "B", "end_date": "2026-03-31", "report_type": "q1_quarterly", "ann_date": "2026-04-20", "factor_date": "2026-03-31", "matched_pct": 1.0, "is_simulated": True, "BETA": -1.0, "RSTR": 0.4, "LNCAP": 0.0, "EARNYILD": 0.0, "GROWTH": -0.5, "LEVERAGE": 0.0, "LIQUIDITY": 0.0, "RESVOL": 0.0, "BTOP": 0.0, "NLSIZE": 0.0},
        ]
    ).assign(end_date=lambda df: pd.to_datetime(df["end_date"]), ann_date=lambda df: pd.to_datetime(df["ann_date"]), factor_date=lambda df: pd.to_datetime(df["factor_date"])).to_parquet(query_dir / "fund_style_portraits.parquet", index=False)
    pd.DataFrame(
        [
            {"ts_code": "A", "end_date": "2024-12-31", "report_type": "annual", "ann_date": "2025-03-30", "is_simulated": False, "ind_电子": 0.2, "ind_医药生物": 0.8},
            {"ts_code": "B", "end_date": "2024-12-31", "report_type": "annual", "ann_date": "2025-03-30", "is_simulated": False, "ind_电子": 0.4, "ind_医药生物": 0.6},
            {"ts_code": "A", "end_date": "2025-12-31", "report_type": "annual", "ann_date": "2026-03-30", "is_simulated": False, "ind_电子": 0.6, "ind_医药生物": 0.4},
            {"ts_code": "B", "end_date": "2025-12-31", "report_type": "annual", "ann_date": "2026-03-30", "is_simulated": False, "ind_电子": 0.1, "ind_医药生物": 0.9},
            {"ts_code": "A", "end_date": "2026-03-31", "report_type": "q1_quarterly", "ann_date": "2026-04-20", "is_simulated": True, "ind_电子": 0.7, "ind_医药生物": 0.3},
            {"ts_code": "B", "end_date": "2026-03-31", "report_type": "q1_quarterly", "ann_date": "2026-04-20", "is_simulated": True, "ind_电子": 0.2, "ind_医药生物": 0.8},
        ]
    ).assign(end_date=lambda df: pd.to_datetime(df["end_date"]), ann_date=lambda df: pd.to_datetime(df["ann_date"])).to_parquet(query_dir / "fund_industry_portraits.parquet", index=False)
    pd.DataFrame(
        [
            {"ts_code": "A", "ts_code_std": "A", "ann_date": "2026-03-30", "end_date": "2025-12-31", "n_holdings": 3, "top10_weight_sum": 60.0, "top1_weight": 30.0, "mkv_sum": 100.0, "report_month": 12, "ann_month": 3, "is_top10_only_like": False, "report_type": "annual"},
            {"ts_code": "B", "ts_code_std": "B", "ann_date": "2026-03-30", "end_date": "2025-12-31", "n_holdings": 2, "top10_weight_sum": 60.0, "top1_weight": 40.0, "mkv_sum": 100.0, "report_month": 12, "ann_month": 3, "is_top10_only_like": False, "report_type": "annual"},
        ]
    ).assign(ann_date=lambda df: pd.to_datetime(df["ann_date"]), end_date=lambda df: pd.to_datetime(df["end_date"])).to_parquet(query_dir / "fund_portfolio_reports.parquet", index=False)
    pd.DataFrame(
        [
            {"ts_code": "A", "ann_date": "2026-03-30", "end_date": "2025-12-31", "symbol": "000001.SZ", "mkv": 30.0, "amount": 1.0, "stk_mkv_ratio": 30.0, "stk_float_ratio": 0.0, "symbol_std": "000001_SZ", "ts_code_std": "A"},
            {"ts_code": "A", "ann_date": "2026-03-30", "end_date": "2025-12-31", "symbol": "000002.SZ", "mkv": 20.0, "amount": 1.0, "stk_mkv_ratio": 20.0, "stk_float_ratio": 0.0, "symbol_std": "000002_SZ", "ts_code_std": "A"},
            {"ts_code": "A", "ann_date": "2026-03-30", "end_date": "2025-12-31", "symbol": "0700.HK", "mkv": 50.0, "amount": 1.0, "stk_mkv_ratio": 50.0, "stk_float_ratio": 0.0, "symbol_std": "0700_HK", "ts_code_std": "A"},
            {"ts_code": "B", "ann_date": "2026-03-30", "end_date": "2025-12-31", "symbol": "000001.SZ", "mkv": 40.0, "amount": 1.0, "stk_mkv_ratio": 40.0, "stk_float_ratio": 0.0, "symbol_std": "000001_SZ", "ts_code_std": "B"},
            {"ts_code": "B", "ann_date": "2026-03-30", "end_date": "2025-12-31", "symbol": "000003.SZ", "mkv": 20.0, "amount": 1.0, "stk_mkv_ratio": 20.0, "stk_float_ratio": 0.0, "symbol_std": "000003_SZ", "ts_code_std": "B"},
        ]
    ).assign(ann_date=lambda df: pd.to_datetime(df["ann_date"]), end_date=lambda df: pd.to_datetime(df["end_date"])).to_parquet(query_dir / "fund_holdings.parquet", index=False)
    pd.DataFrame(
        [
            {"ts_code": "000001.SZ", "name": "平安银行", "l1_name": "银行", "l2_name": "股份制银行", "l3_name": "股份制银行", "l1_code": "1", "l2_code": "11", "l3_code": "111", "in_date": "2000-01-01", "out_date": pd.NaT, "is_new": "Y"},
            {"ts_code": "000002.SZ", "name": "万科A", "l1_name": "房地产", "l2_name": "房地产开发", "l3_name": "住宅开发", "l1_code": "2", "l2_code": "22", "l3_code": "222", "in_date": "2000-01-01", "out_date": pd.NaT, "is_new": "Y"},
            {"ts_code": "000003.SZ", "name": "医药A", "l1_name": "医药生物", "l2_name": "化学制药", "l3_name": "化学制剂", "l1_code": "3", "l2_code": "33", "l3_code": "333", "in_date": "2000-01-01", "out_date": pd.NaT, "is_new": "Y"},
        ]
    ).assign(in_date=lambda df: pd.to_datetime(df["in_date"])).to_parquet(query_dir / "stock_industry_map.parquet", index=False)
    pd.DataFrame(
        [
            {"category": "金融", "l1_name": "银行"},
            {"category": "金融", "l1_name": "房地产"},
            {"category": "消费", "l1_name": "医药生物"},
            {"category": "TMT", "l1_name": "电子"},
        ]
    ).to_csv(query_dir / "industry_category_map.csv", index=False)


def test_build_exposure_snapshot_aggregates_sleeves_and_filters_hk(tmp_path: Path) -> None:
    _write_fixture_data(tmp_path)
    result = build_exposure_snapshot(
        tmp_path,
        funds=[
            {"id": "A", "name": "基金A", "weight": 0.25},
            {"id": "B", "name": "基金B", "weight": 0.75},
        ],
        weights={"A": 0.25, "B": 0.75},
        sleeves=[
            {"id": "defensive", "name": "防守组", "funds": ["A"]},
            {"id": "aggressive", "name": "进攻组", "funds": ["B"]},
        ],
        end_date="2026-05-29",
    )

    assert result["snapshot_date"] == "2026-03-31"
    portfolio = next(row for row in result["factor_rows"] if row["id"] == "portfolio")
    aggressive = next(row for row in result["factor_rows"] if row["id"] == "aggressive")
    fund_b = next(row for row in result["factor_rows"] if row["id"] == "B")
    assert portfolio["values"]["BETA"] == pytest.approx(-0.5)
    assert aggressive["values"]["BETA"] == pytest.approx(-1.0)
    assert fund_b["parent_id"] == "aggressive"
    assert result["industry_l1"]["matrix_rows"][0].keys() >= {"industry", "portfolio", "defensive", "aggressive"}
    assert result["drilldown"]["holdings_by_scope"]["portfolio"][0]["code"] == "000001.SZ"
    assert all(not item["code"].endswith(".HK") for item in result["drilldown"]["holdings_by_scope"]["portfolio"])
    assert result["fund_details"]["A"]["ignored_hk_rows"] == 1
    assert result["fund_details"]["A"]["top_l2_industries"][0]["industry"] == "股份制银行"
    assert result["fund_details"]["A"]["top_l2_industries"][0]["weight"] == pytest.approx(0.3)
    assert result["category_timeline"]["categories"] == ["TMT", "制造", "周期", "消费", "金融"]
    assert result["category_timeline"]["years"] == [2024, 2025]
    tmt = next(item for item in result["category_timeline"]["series"] if item["name"] == "TMT")
    consumption = next(item for item in result["category_timeline"]["series"] if item["name"] == "消费")
    assert tmt["values"] == pytest.approx([0.35, 0.225])
    assert consumption["values"] == pytest.approx([0.65, 0.775])


def test_exposure_snapshot_reports_missing_fund_coverage_without_changing_available_weight_normalization(tmp_path: Path) -> None:
    _write_fixture_data(tmp_path)
    result = build_exposure_snapshot(
        tmp_path,
        funds=[
            {"id": "A", "name": "基金A", "weight": 0.25},
            {"id": "B", "name": "基金B", "weight": 0.25},
            {"id": "C", "name": "基金C", "weight": 0.5},
        ],
        weights={"A": 0.25, "B": 0.25, "C": 0.5},
        sleeves=[
            {"id": "defensive", "name": "防守组", "funds": ["A", "C"]},
            {"id": "aggressive", "name": "进攻组", "funds": ["B"]},
        ],
        end_date="2026-05-29",
    )

    portfolio = next(row for row in result["factor_rows"] if row["id"] == "portfolio")
    assert portfolio["values"]["BETA"] == pytest.approx(0.0)
    assert result["coverage"]["style"]["available_weight"] == pytest.approx(0.5)
    assert result["coverage"]["style"]["missing_funds"] == [{"id": "C", "name": "基金C", "weight": 0.5}]
    assert result["coverage"]["industry"]["missing_funds"] == [{"id": "C", "name": "基金C", "weight": 0.5}]
    assert result["coverage"]["disclosure"]["missing_funds"] == [{"id": "C", "name": "基金C", "weight": 0.5}]


def test_category_timeline_uses_latest_backtest_weights_for_all_years(tmp_path: Path) -> None:
    _write_fixture_data(tmp_path)
    weight_history = pd.DataFrame(
        [
            {"A": 1.0, "B": 0.0},
            {"A": 0.0, "B": 1.0},
        ],
        index=pd.to_datetime(["2024-12-31", "2025-12-31"]),
    )

    result = build_exposure_snapshot(
        tmp_path,
        funds=[
            {"id": "A", "name": "基金A", "weight": 0.25},
            {"id": "B", "name": "基金B", "weight": 0.75},
        ],
        weights={"A": 0.25, "B": 0.75},
        sleeves=None,
        end_date="2026-05-29",
        start_date="2024-01-01",
        weight_history=weight_history,
    )

    assert result["category_timeline"]["years"] == [2024, 2025]
    tmt = next(item for item in result["category_timeline"]["series"] if item["name"] == "TMT")
    consumption = next(item for item in result["category_timeline"]["series"] if item["name"] == "消费")
    assert tmt["values"] == pytest.approx([0.35, 0.225])
    assert consumption["values"] == pytest.approx([0.65, 0.775])


def test_exposure_breakdown_sections_sum_fund_contributions_to_clicked_slice(tmp_path: Path) -> None:
    _write_fixture_data(tmp_path)
    result = build_exposure_snapshot(
        tmp_path,
        funds=[
            {"id": "A", "name": "基金A", "weight": 0.25},
            {"id": "B", "name": "基金B", "weight": 0.75},
        ],
        weights={"A": 0.25, "B": 0.75},
        sleeves=[
            {"id": "defensive", "name": "防守组", "funds": ["A"]},
            {"id": "aggressive", "name": "进攻组", "funds": ["B"]},
        ],
        end_date="2026-05-29",
    )

    sections = {section["id"]: section for section in result["breakdown_sections"]}
    assert list(sections) == ["category", "l1", "l2", "stock"]

    finance = next(item for item in sections["category"]["items"] if item["label"] == "金融")
    assert finance["weight"] == pytest.approx(0.425)
    assert sum(source["contribution"] for source in finance["sources"]) == pytest.approx(finance["weight"])
    assert set(sections["category"]["scopes"]) == {"portfolio", "defensive", "aggressive"}
    defensive_finance = next(item for item in sections["category"]["scopes"]["defensive"] if item["label"] == "金融")
    assert defensive_finance["weight"] == pytest.approx(0.5)
    assert [(source["fund_id"], source["sleeve"], source["contribution"]) for source in defensive_finance["sources"]] == [
        ("A", "防守组", pytest.approx(0.5)),
    ]
    aggressive_finance = next(item for item in sections["category"]["scopes"]["aggressive"] if item["label"] == "金融")
    assert aggressive_finance["weight"] == pytest.approx(0.4)
    assert [(source["fund_id"], source["sleeve"], source["contribution"]) for source in aggressive_finance["sources"]] == [
        ("B", "进攻组", pytest.approx(0.4)),
    ]
    bank = next(item for item in sections["l1"]["items"] if item["label"] == "银行")
    assert bank["weight"] == pytest.approx(0.375)
    assert sum(source["contribution"] for source in bank["sources"]) == pytest.approx(bank["weight"])
    assert [(source["fund_id"], source["sleeve"], source["contribution"]) for source in bank["sources"]] == [
        ("B", "进攻组", pytest.approx(0.3)),
        ("A", "防守组", pytest.approx(0.075)),
    ]

    stock = next(item for item in sections["stock"]["items"] if item["code"] == "000001.SZ")
    assert stock["label"] == "平安银行"
    assert stock["weight"] == pytest.approx(bank["weight"])
    assert sum(source["contribution"] for source in stock["sources"]) == pytest.approx(stock["weight"])


def test_exposure_breakdown_uses_latest_available_quarterly_disclosure(tmp_path: Path) -> None:
    _write_fixture_data(tmp_path)
    query_dir = tmp_path / "query"
    reports = pd.read_parquet(query_dir / "fund_portfolio_reports.parquet")
    holdings = pd.read_parquet(query_dir / "fund_holdings.parquet")
    q1_report = pd.DataFrame(
        [
            {
                "ts_code": "A",
                "ts_code_std": "A",
                "ann_date": pd.Timestamp("2026-04-20"),
                "end_date": pd.Timestamp("2026-03-31"),
                "n_holdings": 1,
                "top10_weight_sum": 80.0,
                "top1_weight": 80.0,
                "mkv_sum": 100.0,
                "report_month": 3,
                "ann_month": 4,
                "is_top10_only_like": True,
                "report_type": "q1_quarterly",
            }
        ]
    )
    q1_holding = pd.DataFrame(
        [
            {
                "ts_code": "A",
                "ann_date": pd.Timestamp("2026-04-20"),
                "end_date": pd.Timestamp("2026-03-31"),
                "symbol": "000002.SZ",
                "mkv": 80.0,
                "amount": 1.0,
                "stk_mkv_ratio": 80.0,
                "stk_float_ratio": 0.0,
                "symbol_std": "000002_SZ",
                "ts_code_std": "A",
            }
        ]
    )
    pd.concat([reports, q1_report], ignore_index=True).to_parquet(query_dir / "fund_portfolio_reports.parquet", index=False)
    pd.concat([holdings, q1_holding], ignore_index=True).to_parquet(query_dir / "fund_holdings.parquet", index=False)

    result = build_exposure_snapshot(
        tmp_path,
        funds=[{"id": "A", "name": "基金A", "weight": 1.0}],
        weights={"A": 1.0},
        sleeves=None,
        end_date="2026-05-29",
    )

    stock_section = next(section for section in result["breakdown_sections"] if section["id"] == "stock")
    assert result["holding_report_date"] == "2026-03-31"
    assert stock_section["items"][0]["code"] == "000002.SZ"
    assert stock_section["items"][0]["label"] == "万科A"
    assert stock_section["items"][0]["weight"] == pytest.approx(0.8)


def test_industry_category_map_covers_current_sw_l1_names() -> None:
    if not (DATA_DIR / "query/industry_category_map.csv").is_file():
        pytest.skip("FOF 冻结输入尚未迁入")
    industry_map = pd.read_parquet(DATA_DIR / "query/stock_industry_map.parquet")
    category_map = pd.read_csv(DATA_DIR / "query/industry_category_map.csv")
    l1_names = set(industry_map["l1_name"].dropna().astype(str))
    mapped_names = set(category_map["l1_name"].dropna().astype(str))

    assert l1_names <= mapped_names
    assert set(category_map["category"]) == {"消费", "TMT", "制造", "周期", "金融"}
    assert category_map.set_index("l1_name").loc["食品饮料", "category"] == "消费"
    assert category_map.set_index("l1_name").loc["电子", "category"] == "TMT"
    assert category_map.set_index("l1_name").loc["电力设备", "category"] == "制造"
    assert category_map.set_index("l1_name").loc["基础化工", "category"] == "周期"
    assert category_map.set_index("l1_name").loc["银行", "category"] == "金融"
    assert category_map.set_index("l1_name").loc["综合", "category"] == "制造"


def test_exposure_breakdown_sections_group_tail_items_into_other(tmp_path: Path) -> None:
    _write_fixture_data(tmp_path)
    query_dir = tmp_path / "query"
    holdings = pd.read_parquet(query_dir / "fund_holdings.parquet")
    industry_map = pd.read_parquet(query_dir / "stock_industry_map.parquet")
    extra_holdings = []
    extra_industries = []
    for idx in range(12):
        code = f"300{idx:03d}.SZ"
        extra_holdings.append(
            {
                "ts_code": "A",
                "ann_date": pd.Timestamp("2026-03-30"),
                "end_date": pd.Timestamp("2025-12-31"),
                "symbol": code,
                "mkv": 1.0,
                "amount": 1.0,
                "stk_mkv_ratio": float(idx + 1),
                "stk_float_ratio": 0.0,
                "symbol_std": code.replace(".", "_"),
                "ts_code_std": "A",
            }
        )
        extra_industries.append(
            {
                "ts_code": code,
                "name": f"股票{idx}",
                "l1_name": "电子",
                "l2_name": f"电子细分{idx}",
                "l3_name": f"电子三级{idx}",
                "l1_code": "9",
                "l2_code": f"9{idx}",
                "l3_code": f"9{idx}1",
                "in_date": pd.Timestamp("2000-01-01"),
                "out_date": pd.NaT,
                "is_new": "Y",
            }
        )
    pd.concat([holdings, pd.DataFrame(extra_holdings)], ignore_index=True).to_parquet(query_dir / "fund_holdings.parquet", index=False)
    pd.concat([industry_map, pd.DataFrame(extra_industries)], ignore_index=True).to_parquet(query_dir / "stock_industry_map.parquet", index=False)

    result = build_exposure_snapshot(
        tmp_path,
        funds=[{"id": "A", "name": "基金A", "weight": 1.0}],
        weights={"A": 1.0},
        sleeves=[{"id": "defensive", "name": "防守组", "funds": ["A"]}],
        end_date="2026-05-29",
    )

    stock_section = next(section for section in result["breakdown_sections"] if section["id"] == "stock")
    l2_section = next(section for section in result["breakdown_sections"] if section["id"] == "l2")
    assert len(stock_section["items"]) == 11
    assert stock_section["items"][-1]["label"] == "其他"
    assert stock_section["items"][-1]["is_other"] is True
    assert sum(source["contribution"] for source in stock_section["items"][-1]["sources"]) == pytest.approx(stock_section["items"][-1]["weight"])
    assert len(l2_section["items"]) <= 21


def test_exposure_snapshot_caches_disclosure_reads_for_same_fund_set(tmp_path: Path, monkeypatch) -> None:
    _write_fixture_data(tmp_path)
    original_read_parquet = pd.read_parquet
    calls: list[str] = []

    def counted_read_parquet(path, *args, **kwargs):
        calls.append(Path(path).name)
        return original_read_parquet(path, *args, **kwargs)

    monkeypatch.setattr(pd, "read_parquet", counted_read_parquet)
    for _ in range(2):
        build_exposure_snapshot(
            tmp_path,
            funds=[{"id": "A", "name": "基金A", "weight": 0.25}, {"id": "B", "name": "基金B", "weight": 0.75}],
            weights={"A": 0.25, "B": 0.75},
            sleeves=None,
            end_date="2026-05-29",
        )

    assert calls.count("fund_portfolio_reports.parquet") == 1
    assert calls.count("fund_holdings.parquet") == 1


def test_exposure_snapshot_uses_only_announced_data_as_of_end_date(tmp_path: Path) -> None:
    query_dir = tmp_path / "query"
    query_dir.mkdir(parents=True)
    pd.DataFrame(
        [
            {"ts_code": "A", "end_date": "2025-12-31", "report_type": "annual", "ann_date": "2026-03-25", "factor_date": "2025-12-31", "BETA": 0.1, "RSTR": 0.0, "LNCAP": 0.0, "EARNYILD": 0.0, "GROWTH": 0.0, "LEVERAGE": 0.0, "LIQUIDITY": 0.0, "RESVOL": 0.0, "BTOP": 0.0, "NLSIZE": 0.0},
            {"ts_code": "A", "end_date": "2026-03-31", "report_type": "q1_quarterly", "ann_date": "2026-04-20", "factor_date": "2026-03-31", "BETA": 9.9, "RSTR": 0.0, "LNCAP": 0.0, "EARNYILD": 0.0, "GROWTH": 0.0, "LEVERAGE": 0.0, "LIQUIDITY": 0.0, "RESVOL": 0.0, "BTOP": 0.0, "NLSIZE": 0.0},
        ]
    ).assign(end_date=lambda df: pd.to_datetime(df["end_date"]), ann_date=lambda df: pd.to_datetime(df["ann_date"]), factor_date=lambda df: pd.to_datetime(df["factor_date"])).to_parquet(query_dir / "fund_style_portraits.parquet", index=False)
    pd.DataFrame(
        [
            {"ts_code": "A", "end_date": "2025-12-31", "report_type": "annual", "ann_date": "2026-03-25", "ind_电子": 0.2},
            {"ts_code": "A", "end_date": "2026-03-31", "report_type": "q1_quarterly", "ann_date": "2026-04-20", "ind_电子": 0.9},
        ]
    ).assign(end_date=lambda df: pd.to_datetime(df["end_date"]), ann_date=lambda df: pd.to_datetime(df["ann_date"])).to_parquet(query_dir / "fund_industry_portraits.parquet", index=False)
    pd.DataFrame(
        [
            {"ts_code": "A", "ts_code_std": "A", "ann_date": "2026-03-25", "end_date": "2025-12-31", "n_holdings": 1, "top10_weight_sum": 10.0, "top1_weight": 10.0, "mkv_sum": 10.0, "report_month": 12, "ann_month": 3, "is_top10_only_like": False, "report_type": "annual"},
            {"ts_code": "A", "ts_code_std": "A", "ann_date": "2026-04-20", "end_date": "2026-03-31", "n_holdings": 1, "top10_weight_sum": 10.0, "top1_weight": 10.0, "mkv_sum": 10.0, "report_month": 3, "ann_month": 4, "is_top10_only_like": True, "report_type": "q1_quarterly"},
        ]
    ).assign(ann_date=lambda df: pd.to_datetime(df["ann_date"]), end_date=lambda df: pd.to_datetime(df["end_date"])).to_parquet(query_dir / "fund_portfolio_reports.parquet", index=False)
    pd.DataFrame(
        [
            {"ts_code": "A", "ann_date": "2026-03-25", "end_date": "2025-12-31", "symbol": "000001.SZ", "mkv": 1.0, "amount": 1.0, "stk_mkv_ratio": 10.0, "stk_float_ratio": 0.0, "symbol_std": "000001_SZ", "ts_code_std": "A"},
            {"ts_code": "A", "ann_date": "2026-04-20", "end_date": "2026-03-31", "symbol": "000002.SZ", "mkv": 1.0, "amount": 1.0, "stk_mkv_ratio": 10.0, "stk_float_ratio": 0.0, "symbol_std": "000002_SZ", "ts_code_std": "A"},
        ]
    ).assign(ann_date=lambda df: pd.to_datetime(df["ann_date"]), end_date=lambda df: pd.to_datetime(df["end_date"])).to_parquet(query_dir / "fund_holdings.parquet", index=False)
    pd.DataFrame(
        [
            {"ts_code": "000001.SZ", "name": "旧持仓", "l1_name": "银行", "l2_name": "股份制银行", "l3_name": "股份制银行", "l1_code": "1", "l2_code": "11", "l3_code": "111", "in_date": "2000-01-01", "out_date": pd.NaT, "is_new": "Y"},
            {"ts_code": "000002.SZ", "name": "未来持仓", "l1_name": "电子", "l2_name": "半导体", "l3_name": "半导体", "l1_code": "2", "l2_code": "22", "l3_code": "222", "in_date": "2000-01-01", "out_date": pd.NaT, "is_new": "Y"},
        ]
    ).assign(in_date=lambda df: pd.to_datetime(df["in_date"])).to_parquet(query_dir / "stock_industry_map.parquet", index=False)

    result = build_exposure_snapshot(
        tmp_path,
        funds=[{"id": "A", "name": "基金A", "weight": 1.0}],
        weights={"A": 1.0},
        sleeves=None,
        end_date="2026-04-01",
    )

    portfolio = next(row for row in result["factor_rows"] if row["id"] == "portfolio")
    holdings = result["drilldown"]["holdings_by_scope"]["portfolio"]
    assert result["snapshot_date"] == "2025-12-31"
    assert portfolio["values"]["BETA"] == pytest.approx(0.1)
    assert holdings[0]["code"] == "000001.SZ"


def test_exposure_snapshot_returns_historical_industry_and_top_holding_tables(tmp_path: Path) -> None:
    _write_fixture_data(tmp_path)
    query_dir = tmp_path / "query"
    reports = pd.read_parquet(query_dir / "fund_portfolio_reports.parquet")
    holdings = pd.read_parquet(query_dir / "fund_holdings.parquet")
    extra_reports = pd.DataFrame(
        [
            {"ts_code": "A", "ts_code_std": "A", "ann_date": pd.Timestamp("2025-03-30"), "end_date": pd.Timestamp("2024-12-31"), "n_holdings": 2, "top10_weight_sum": 50.0, "top1_weight": 50.0, "mkv_sum": 100.0, "report_month": 12, "ann_month": 3, "is_top10_only_like": False, "report_type": "annual"},
            {"ts_code": "B", "ts_code_std": "B", "ann_date": pd.Timestamp("2025-03-30"), "end_date": pd.Timestamp("2024-12-31"), "n_holdings": 2, "top10_weight_sum": 60.0, "top1_weight": 40.0, "mkv_sum": 100.0, "report_month": 12, "ann_month": 3, "is_top10_only_like": False, "report_type": "annual"},
            {"ts_code": "A", "ts_code_std": "A", "ann_date": pd.Timestamp("2026-04-20"), "end_date": pd.Timestamp("2026-03-31"), "n_holdings": 1, "top10_weight_sum": 99.0, "top1_weight": 99.0, "mkv_sum": 100.0, "report_month": 3, "ann_month": 4, "is_top10_only_like": True, "report_type": "q1_quarterly"},
        ]
    )
    extra_holdings = pd.DataFrame(
        [
            {"ts_code": "A", "ann_date": pd.Timestamp("2025-03-30"), "end_date": pd.Timestamp("2024-12-31"), "symbol": "000002.SZ", "mkv": 50.0, "amount": 1.0, "stk_mkv_ratio": 50.0, "stk_float_ratio": 0.0, "symbol_std": "000002_SZ", "ts_code_std": "A"},
            {"ts_code": "B", "ann_date": pd.Timestamp("2025-03-30"), "end_date": pd.Timestamp("2024-12-31"), "symbol": "000003.SZ", "mkv": 40.0, "amount": 1.0, "stk_mkv_ratio": 40.0, "stk_float_ratio": 0.0, "symbol_std": "000003_SZ", "ts_code_std": "B"},
            {"ts_code": "B", "ann_date": pd.Timestamp("2025-03-30"), "end_date": pd.Timestamp("2024-12-31"), "symbol": "000001.SZ", "mkv": 20.0, "amount": 1.0, "stk_mkv_ratio": 20.0, "stk_float_ratio": 0.0, "symbol_std": "000001_SZ", "ts_code_std": "B"},
            {"ts_code": "A", "ann_date": pd.Timestamp("2026-04-20"), "end_date": pd.Timestamp("2026-03-31"), "symbol": "000002.SZ", "mkv": 99.0, "amount": 1.0, "stk_mkv_ratio": 99.0, "stk_float_ratio": 0.0, "symbol_std": "000002_SZ", "ts_code_std": "A"},
        ]
    )
    pd.concat([reports, extra_reports], ignore_index=True).to_parquet(query_dir / "fund_portfolio_reports.parquet", index=False)
    pd.concat([holdings, extra_holdings], ignore_index=True).to_parquet(query_dir / "fund_holdings.parquet", index=False)

    result = build_exposure_snapshot(
        tmp_path,
        funds=[
            {"id": "A", "name": "基金A", "weight": 0.25},
            {"id": "B", "name": "基金B", "weight": 0.75},
        ],
        weights={"A": 0.25, "B": 0.75},
        sleeves=[
            {"id": "defensive", "name": "防守组", "funds": ["A"]},
            {"id": "aggressive", "name": "进攻组", "funds": ["B"]},
        ],
        end_date="2026-04-01",
        weight_history=pd.DataFrame(
            [
                {"A": 1.0, "B": 0.0},
                {"A": 1.0, "B": 0.0},
            ],
            index=pd.to_datetime(["2024-12-31", "2025-12-31"]),
        ),
    )

    history = result["history_tables"]
    industry = history["industry_l1"]
    assert industry["periods"] == ["2024Q4", "2025Q4"]
    electronics = next(row for row in industry["rows"] if row["industry"] == "电子")
    assert electronics["values"]["2024Q4"] == pytest.approx(0.35)
    assert electronics["values"]["2025Q4"] == pytest.approx(0.225)

    holdings_history = history["top_holdings"]
    assert [row["period"] for row in holdings_history["rows"]] == ["2025Q4", "2024Q4"]
    assert holdings_history["rows"][0]["holdings"][0] == {
        "rank": 1,
        "name": "平安银行",
        "code": "000001.SZ",
        "weight": pytest.approx(0.375),
    }
    assert holdings_history["rows"][1]["holdings"][0]["name"] == "医药A"
    assert holdings_history["rows"][1]["holdings"][0]["weight"] == pytest.approx(0.3)
    assert all(row["period"] != "2026Q1" for row in holdings_history["rows"])
