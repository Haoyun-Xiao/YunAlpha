import json
from datetime import date

import duckdb

from fund_research_agent.domain.contracts import FundIdentity
from fund_research_agent.paths import WriteBoundary
from fund_research_agent.workflows.report.part_data import PART_FILENAMES, PartDataBuilder


def test_builds_all_part_contracts_from_raw_json(tmp_path, monkeypatch):
    monkeypatch.setenv("FUND_AGENT_NAV_OVERLAY", str(tmp_path / "no-overlay.parquet"))
    project = tmp_path / "project"
    legacy = tmp_path / "legacy"
    raw_dir = project / "var/raw"
    out_dir = project / "var/parts"
    raw_dir.mkdir(parents=True)
    legacy.mkdir()
    database = tmp_path / "lake.duckdb"
    connection = duckdb.connect(str(database))
    connection.execute("CREATE TABLE daily_bars(symbol VARCHAR,trade_date DATE,close DOUBLE)")
    connection.execute("CREATE TABLE fund_nav(symbol VARCHAR,nav_date DATE,adjusted_nav DOUBLE,fetched_at TIMESTAMP,source VARCHAR,announce_date DATE)")
    connection.execute("CREATE TABLE index_bars(symbol VARCHAR,trade_date DATE,close DOUBLE,fetched_at TIMESTAMP,source VARCHAR)")
    connection.execute(
        "CREATE TABLE valuation_metrics(symbol VARCHAR,trade_date DATE,pe_ttm DOUBLE,pb DOUBLE,total_mv DOUBLE)"
    )
    connection.close()
    identity = FundIdentity(
        fund_symbol="000001.OF",
        fund_name="测试基金A",
        manager_name="测试经理",
        manager_tenure_start=date(2020, 1, 1),
    )
    key = "000001.OF_测试基金A"
    documents = {
        "基金配置": {key: {"大类板块": [], "一级行业": [], "二级行业": [], "重仓个股": []}, "基金经理": "测试经理"},
        "季度盈亏": {key: {"季度盈亏(万元)": {}}, "基金经理": "测试经理"},
        "年度盈亏": {key: {"年度盈亏(万元)": {"2025年": {"大类": {"消费": 1}, "一级": {}, "个股": {}}}}, "基金经理": "测试经理"},
        "累计盈亏": {key: {"累计盈亏(万元)": {"全阶段累计": {"大类": {"消费": 1}, "一级": {}, "个股": {}}}}, "基金经理": "测试经理"},
        "配置特征": {key: {"配置特征": [{"报告期": "20251231", "股票仓位": "80%"}]}, "基金经理": "测试经理"},
        "配置偏好": {key: {"行业前五出现次数": [], "个股前十出现次数": []}, "基金经理": "测试经理"},
        "配置跟踪": {key: {"配置跟踪": {"最新": "2025Q4"}}, "基金经理": "测试经理"},
        "投资能力": {"format": "legacy_prompt_text", "prompt_text": "test"},
        "基金规模": {key: {"规模变动(亿元)": {"2025Q4": {"基金规模": 1.2, "管理规模": 1.2}}}, "基金经理": "测试经理"},
        "基金换手": {key: {"年度换手(倍)": {"2025年": {"基金换手率": 1.2}}}, "基金经理": "测试经理"},
        "季度运作报告": {"000001.OF_测试经理_测试基金A": {}},
        "数据看板": {"基金代码": "000001.OF", "基金简称": "测试基金A", "基金经理": "测试经理", "收益回撤": {"任职年化": 1}},
    }
    files = {}
    for kind, payload in documents.items():
        path = raw_dir / f"{kind}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        files[kind] = str(path)

    result = PartDataBuilder(database, WriteBoundary(project, legacy)).build(
        identity, files, out_dir, date(2026, 8, 23), []
    )

    assert result["valid"] is True
    assert result["source_hashes"]
    assert set(result["files"]) == set(PART_FILENAMES)
    assert all((out_dir / filename).is_file() for filename in PART_FILENAMES.values())
    part4 = json.loads((out_dir / PART_FILENAMES["part4"]).read_text())
    assert part4["年度列表"] == ["2025年"]
