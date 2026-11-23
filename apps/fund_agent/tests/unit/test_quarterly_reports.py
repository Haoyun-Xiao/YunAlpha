from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pymupdf

from fund_research_agent.adapters.quarterly_reports import QuarterlyReportProvider
from fund_research_agent.legacy_port.fund_json.calculations import pnl_by_quarter
from fund_research_agent.paths import WriteBoundary


def _pdf_bytes() -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text(
        (72, 72),
        "4.4 报告期内基金投资策略和运作分析\n本期提高电子配置。\n4.5 管理人对宏观经济、证券市场及行业走势展望\n保持审慎。",
        fontname="china-s",
    )
    value = document.tobytes()
    document.close()
    return value


def test_quarterly_report_provider_fetches_extracts_and_reuses_cache(
    tmp_path: Path, monkeypatch
):
    project = tmp_path / "project"
    legacy = tmp_path / "legacy"
    (project / "var").mkdir(parents=True)
    legacy.mkdir()
    boundary = WriteBoundary(project, legacy)
    provider = QuarterlyReportProvider(project / "var/cache/fund_reports", boundary)
    monkeypatch.setattr(
        provider,
        "_announcement_list",
        lambda _code: [
            {
                "year": 2024,
                "quarter": 1,
                "title": "2024年第一季度报告",
                "announce_date": "2024-04-20",
                "report_id": "abc",
            }
        ],
    )
    monkeypatch.setattr(provider, "_download_pdf", lambda _report_id: _pdf_bytes())

    result = provider.ensure("000001.OF", "测试基金", date(2024, 1, 1), date(2024, 6, 1))

    assert result.records == 1
    payload = json.loads(result.path.read_text(encoding="utf-8"))
    assert payload["records"][0]["report_label"] == "2024年一季报"
    assert "提高电子配置" in payload["records"][0]["strategy_analysis"]
    monkeypatch.setattr(provider, "_announcement_list", lambda _code: (_ for _ in ()).throw(AssertionError()))
    cached = provider.ensure("000001.OF", "测试基金", date(2024, 1, 1), date(2024, 6, 1))
    assert cached.source == "cache"


def test_incomplete_current_quarter_is_not_created_from_next_period_sale():
    frame = pd.DataFrame(
        [
            {
                "report_period": pd.Timestamp("2026-06-30"),
                "holding_symbol": "000001.SZ",
                "股票名称": "测试股票",
                "大类": "制造",
                "申万一级": "机械设备",
                "个股估算盈亏": 100_000,
                "下一期有持仓": False,
                "对应估算卖出盈亏": 200_000,
                "下一报告期": pd.Timestamp("2026-09-30"),
            }
        ]
    )

    result = pnl_by_quarter(frame, date(2026, 8, 26))

    assert "2026Q2" in result
    assert "2026Q3" not in result
