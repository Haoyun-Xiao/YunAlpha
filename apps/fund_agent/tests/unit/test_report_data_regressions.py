from datetime import date
import json
from pathlib import Path
from unittest.mock import AsyncMock

import duckdb
import httpx
import pandas as pd
import pytest
import respx

from fund_research_agent.adapters.model import ModelGateway
from fund_research_agent.adapters.quarterly_reports import _extract_section, _OP_ANALYSIS_PATTERNS
from fund_research_agent.domain.errors import WriteBoundaryViolation
from fund_research_agent.domain.report_data import annual_return
from fund_research_agent.legacy_port.fund_json.lake import LakeRepository
from fund_research_agent.legacy_port.fund_json.render import feature_payload, pct, tracking_payload
from fund_research_agent.paths import WriteBoundary
from fund_research_agent.workflows.report.part_data import PartDataBuilder
from fund_research_agent.domain.contracts import FundIdentity


@pytest.fixture
def lake(tmp_path, monkeypatch):
    monkeypatch.setenv("FUND_AGENT_NAV_OVERLAY", str(tmp_path / "no-overlay.parquet"))
    path = tmp_path / "lake.duckdb"
    con = duckdb.connect(str(path))
    schemas = [
        "fund_profiles(symbol VARCHAR,name VARCHAR)",
        "fund_nav(symbol VARCHAR,nav_date DATE,adjusted_nav DOUBLE,announce_date DATE,fetched_at TIMESTAMP,source VARCHAR)",
        "fund_holdings(fund_symbol VARCHAR,holding_symbol VARCHAR,report_period DATE,announce_date DATE,shares DOUBLE,market_value DOUBLE,weight_nav DOUBLE,fetched_at TIMESTAMP)",
        "fund_scale(symbol VARCHAR,report_period DATE,scale_yi DOUBLE)",
        "fund_annual_turnover(fund_symbol VARCHAR,report_year INTEGER,report_period DATE,turnover_rate DOUBLE,source VARCHAR,fetched_at TIMESTAMP)",
        "fund_reports(fund_symbol VARCHAR,report_period DATE,report_label VARCHAR,announce_date DATE,strategy_analysis VARCHAR,outlook VARCHAR)",
        "instruments(symbol VARCHAR,name VARCHAR)",
        "industry_members(symbol VARCHAR,industry_code VARCHAR,as_of_date DATE,classification_system VARCHAR)",
        "industry_taxonomy(industry_code VARCHAR,industry_name VARCHAR,level VARCHAR,parent_industry_code VARCHAR)",
        "valuation_metrics(symbol VARCHAR,trade_date DATE,pe_ttm DOUBLE,pb DOUBLE,total_mv DOUBLE)",
        "financial_statement_items(symbol VARCHAR,report_period VARCHAR,item_code VARCHAR,item_value DOUBLE,announce_date DATE)",
        "corporate_actions(symbol VARCHAR,ex_date DATE,cash_dividend DOUBLE,source VARCHAR)",
        "daily_bars(symbol VARCHAR,trade_date DATE,close DOUBLE)",
    ]
    for schema in schemas:
        con.execute("CREATE TABLE " + schema)
    con.execute("INSERT INTO fund_profiles VALUES ('000001.OF','测试-A')")
    con.close()
    return path


def test_lake_nav_wins_over_stale_overlay_and_deduplicates(lake, tmp_path, monkeypatch):
    con = duckdb.connect(str(lake))
    con.execute(
        "INSERT INTO fund_nav VALUES ('000001.OF','2026-06-30',2,'2026-06-30','2026-07-01','official'),('000001.OF','2026-11-03',3,'2026-11-03','2026-11-04','official'),('000001.OF','2026-11-03',3,'2026-11-03','2026-11-05','official')"
    )
    con.execute(
        "COPY (SELECT '000001.OF' fund_symbol,DATE '2026-06-30' nav_date,1.5 adjusted_nav UNION ALL SELECT '000001.OF',DATE '2026-06-29',1.4) TO ? (FORMAT PARQUET)",
        [str(tmp_path / "overlay.parquet")],
    )
    con.close()
    monkeypatch.setenv("FUND_AGENT_NAV_OVERLAY", str(tmp_path / "overlay.parquet"))
    repo = LakeRepository(lake, as_of=date(2026, 11, 5))
    try:
        nav = repo.nav("000001.OF")
        assert (
            len(nav) == 3
            and nav.loc["2026-06-30"] == 2
            and nav.index.max() == pd.Timestamp("2026-11-03")
        )
        assert "nav_source_conflict" in repo.quality_flags
    finally:
        repo.close()


def test_native_quarterly_weight_survives_missing_scale(lake):
    repo = LakeRepository(lake, as_of=date(2026, 11, 5))
    try:
        frame = pd.DataFrame(
            [
                {
                    "fund_symbol": "000001.OF",
                    "holding_symbol": "600001.SH",
                    "report_period": pd.Timestamp("2026-09-30"),
                    "market_value": 1000.0,
                    "shares": 100.0,
                    "weight_nav": 8.5,
                }
            ]
        )
        enriched = repo.enrich_positions(frame)
        assert enriched.iloc[0]["占净值比"] == 0.085
        assert enriched.iloc[0]["权重来源"] == "native_weight_nav"
    finally:
        repo.close()


def test_turnover_falls_back_per_year_not_per_fund(lake):
    con = duckdb.connect(str(lake))
    con.execute(
        "INSERT INTO fund_annual_turnover VALUES ('000001.OF',2024,'2024-12-31',2,'wind',now()),('000001.OF',2025,'2025-12-31',3,'other',now())"
    )
    # Historical turnover is eligible only after the corresponding annual
    # report has been disclosed; freeze those publications with the rows.
    con.execute(
        "INSERT INTO fund_reports VALUES "
        "('000001.OF','2024-12-31','2024年年报','2025-03-31','',''),"
        "('000001.OF','2025-12-31','2025年年报','2026-03-31','','')"
    )
    con.close()
    repo = LakeRepository(lake, as_of=date(2026, 11, 5))
    try:
        assert repo.turnover("000001.OF") == {2024: 2.0, 2025: 3.0}
    finally:
        repo.close()


def test_future_announcement_cannot_leak_into_positions(lake):
    con = duckdb.connect(str(lake))
    con.execute(
        "INSERT INTO fund_holdings VALUES ('000001.OF','600001.SH','2026-09-30','2026-10-25',1,1,5,now())"
    )
    con.close()
    repo = LakeRepository(lake, as_of=date(2026, 9, 30))
    try:
        assert repo.positions("000001.OF").empty
    finally:
        repo.close()


def test_missing_features_remain_null_and_clipping_is_disclosed():
    frame = pd.DataFrame(
        [
            {
                "report_period": pd.Timestamp("2026-09-30"),
                "占净值比": 0.1,
                "股票市值权重": 1.0,
                "股票名称": "测试",
                "申万一级": "电子",
                "PE": None,
                "PB": 20.0,
                "PB_raw": 30.0,
                "SIZE": None,
                "ROE": None,
                "股息率": None,
            }
        ]
    )
    assert not feature_payload(frame)["配置特征"]
    row = feature_payload(frame, include_quarters=True)["配置特征"][0]
    assert row["加权平均PE"] is None and row["加权平均ROE"] is None
    assert row["指标覆盖"]["PB"]["裁剪影响净值权重"] == 0.1
    assert pct(0) == "0.00%" and pct(None) is None


def test_year_return_uses_previous_year_close():
    nav = pd.Series(
        [1.0, 1.1, 1.3], index=pd.to_datetime(["2025-12-31", "2026-01-05", "2026-11-03"])
    )
    value = annual_return(nav, 2026, date(2026, 11, 5))
    assert value["value"] == pytest.approx(0.3)
    assert value["from"] == "2025-12-31"
    first = annual_return(nav[nav.index.year == 2026], 2026, date(2026, 11, 5))
    assert first["basis"] == "partial_first_year"


def test_split_section_number_does_not_bleed_into_performance():
    text = "4.4\n报告期内基金的投资策略和运作分析\n增加电子配置。\n4.5\n报告期内基金的业绩表现\n收益20%。\n4.6\n预警说明"
    section = _extract_section(text, _OP_ANALYSIS_PATTERNS)
    assert "增加电子配置" in section and "收益20%" not in section and "预警" not in section


@pytest.mark.parametrize("label", ["2026Q4", "2026年四季报", "20261231"])
def test_part_contract_rejects_future_periods(label):
    identity = FundIdentity(fund_symbol="000001.OF", fund_name="测试", manager_name="经理")
    with pytest.raises(ValueError, match="超过 as_of"):
        PartDataBuilder._validate(
            "part2",
            {
                "基金代码": "000001.OF",
                "基金简称": "测试",
                "基金经理": "经理",
                "规模变动": {label: 1},
            },
            identity,
            date(2026, 6, 30),
        )


def test_file_symlink_cannot_escape_write_boundary(tmp_path):
    new = tmp_path / "new"
    old = tmp_path / "old"
    (new / "var").mkdir(parents=True)
    old.mkdir()
    protected = old / "data.txt"
    protected.write_text("keep")
    link = new / "var/link.txt"
    link.symlink_to(protected)
    with pytest.raises(WriteBoundaryViolation):
        WriteBoundary(new, old).write_text(link, "bad")
    assert protected.read_text() == "keep"


async def test_structured_cannot_accept_nested_object_from_truncated_outer_json(tmp_path):
    config = tmp_path / "model.yaml"
    config.write_text("roles: {}")
    model = ModelGateway(config)
    model.chat = AsyncMock(return_value={"content": '{"outer":{"inner":1}'})
    with pytest.raises(json.JSONDecodeError):
        await model.structured("evaluator", [])


@pytest.mark.parametrize("finish", ["", "length"])
async def test_stream_must_have_normal_finish_reason(tmp_path, finish):
    config = tmp_path / "model.yaml"
    config.write_text("base_url: https://example.invalid/v1\nroles: {}")
    model = ModelGateway(config)
    event = {"choices": [{"delta": {"content": "partial"}, "finish_reason": finish or None}]}
    with respx.mock:
        respx.post("https://example.invalid/v1/chat/completions").mock(
            return_value=httpx.Response(
                200, text="data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n"
            )
        )
        with pytest.raises((RuntimeError, httpx.RemoteProtocolError)):
            await model._stream_chat(
                {"model": "test"},
                "test",
                1,
                call_id="test",
                role_name="report",
                attempt=1,
                progress_state={},
            )


def test_coverage_uses_date_only_and_does_not_mark_current_quarter_stale(lake):
    from fund_research_agent.adapters.lake import ReadOnlyLakeAdapter

    con = duckdb.connect(str(lake))
    con.execute(
        "INSERT INTO fund_holdings VALUES ('000001.OF','600001.SH','2026-09-30','2026-10-25',1,1,5,now())"
    )
    con.execute("INSERT INTO fund_scale VALUES ('000001.OF','2026-09-30',10)")
    con.close()
    result = ReadOnlyLakeAdapter(lake).coverage("000001.OF", as_of=date(2026, 11, 5))
    assert result["holdings"]["expected_report_period"] == "2026-09-30"
    assert "holdings_latest_period_missing" not in result["eligibility"]["warnings"]
    assert "scale_latest_period_missing" not in result["eligibility"]["warnings"]


def test_scale_supplement_does_not_follow_an_outdated_holdings_date():
    from fund_research_agent.adapters.report_scale import ReportScaleProvider

    assert ReportScaleProvider.needs_supplement(
        {
            "scale": {"to": "2026-06-30", "expected_report_period": "2026-09-30"},
            "holdings": {"to": "2026-06-30"},
        }
    )
    assert not ReportScaleProvider.needs_supplement(
        {
            "scale": {"to": "2026-09-30", "expected_report_period": "2026-09-30"},
            "holdings": {"to": "2026-06-30"},
        }
    )
