from datetime import date

import pytest
from openpyxl import Workbook

from fund_research_agent.adapters.fund_universe import REQUIRED_COLUMNS, FundUniverseAdapter


def test_universe_resolves_current_manager_from_second_sheet(tmp_path):
    path = tmp_path / "funds.xlsx"
    workbook = Workbook()
    workbook.active.title = "ignore"
    sheet = workbook.create_sheet("基金全样本（公募主动）")
    sheet.append(REQUIRED_COLUMNS)
    sheet.append(
        (
            "000628.OF",
            "大成高鑫A",
            "刘旭",
            date(2015, 7, 29),
            "至今",
            date(2012, 8, 28),
            "大成基金",
            "持仓明细10",
        )
    )
    workbook.save(path)

    universe = FundUniverseAdapter(path)

    assert len(universe) == 1
    identity = universe.resolve("000628.OF", date(2026, 8, 23))[0]
    assert identity.fund_name == "大成高鑫A"
    assert identity.manager_name == "刘旭"
    assert identity.manager_tenure_start == date(2015, 7, 29)
    assert identity.manager_tenure_end is None


def test_universe_resolves_fund_name_inside_natural_language(tmp_path):
    path = tmp_path / "funds.xlsx"
    workbook = Workbook()
    workbook.active.title = "ignore"
    sheet = workbook.create_sheet("基金全样本（公募主动）")
    sheet.append(REQUIRED_COLUMNS)
    for symbol, name in (("001480.OF", "财通成长优选混合A"), ("021528.OF", "财通成长优选混合C")):
        sheet.append(
            (
                symbol,
                name,
                "金梓才",
                date(2015, 6, 29),
                "至今",
                date(2015, 6, 29),
                "财通基金",
                "持仓明细10",
            )
        )
    workbook.save(path)

    universe = FundUniverseAdapter(path)

    matches = universe.resolve("请给财通成长优选混合写一份完整报告", date(2026, 8, 28))
    assert [item.fund_symbol for item in matches] == ["001480.OF", "021528.OF"]
    assert all(item.manager_name == "金梓才" for item in matches)


def test_universe_resolves_manager_name_inside_request(tmp_path):
    path = tmp_path / "funds.xlsx"
    workbook = Workbook()
    workbook.active.title = "ignore"
    sheet = workbook.create_sheet("基金全样本（公募主动）")
    sheet.append(REQUIRED_COLUMNS)
    sheet.append(
        (
            "000628.OF",
            "大成高鑫A",
            "刘旭",
            date(2015, 7, 29),
            "至今",
            date(2012, 8, 28),
            "大成基金",
            "持仓明细10",
        )
    )
    workbook.save(path)

    matches = FundUniverseAdapter(path).resolve("给刘旭管理的基金写报告")

    assert len(matches) == 1
    assert matches[0].fund_symbol == "000628.OF"


@pytest.mark.parametrize(
    "query",
    [
        "000628.OF",
        "000628",
        "请研究000628.OF",
        "给000628写报告",
        "大成高鑫A",
        "大成高鑫-A",
        "大成高鑫A怎么样",
        "研究一下大成高鑫A",
        "给大成高鑫A写完整报告",
        "生成大成高鑫A的深度报告",
        "我想看大成高鑫A的基金报告",
        "分析大成高鑫A截至今天的表现",
        "刘旭",
        "刘旭管理的基金",
        "给刘旭管理的基金写报告",
        "研究刘旭的基金",
        "请分析基金经理刘旭",
        "我想看刘旭的基金报告",
        "大成基金刘旭路演观点",
        "比较刘旭管理基金的风险收益",
    ],
)
def test_universe_recognizes_twenty_common_natural_language_forms(tmp_path, query):
    path = tmp_path / "funds.xlsx"
    workbook = Workbook()
    workbook.active.title = "ignore"
    sheet = workbook.create_sheet("基金全样本（公募主动）")
    sheet.append(REQUIRED_COLUMNS)
    sheet.append(
        (
            "000628.OF",
            "大成高鑫A",
            "刘旭",
            date(2015, 7, 29),
            "至今",
            date(2012, 8, 28),
            "大成基金",
            "持仓明细10",
        )
    )
    workbook.save(path)

    matches = FundUniverseAdapter(path).resolve(query, date(2026, 8, 28))

    assert matches and matches[0].fund_symbol == "000628.OF"
