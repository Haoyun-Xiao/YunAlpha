from datetime import date

from fund_research_agent.bootstrap import build_container
from fund_research_agent.legacy_port.fund_json.lake import LakeRepository


def test_acceptance_funds_resolve_from_local_bridge():
    lake = build_container().lake
    first = lake.resolve_fund("000628.OF", date(2026, 8, 23))[0]
    second = lake.resolve_fund("000418.OF", date(2026, 8, 23))[0]
    assert (first.manager_name, second.manager_name) == ("刘旭", "周寒颖")
    assert lake.coverage("000418.OF")["nav"]["rows"] > 1000


def test_fund_resolve_extracts_code_from_natural_language():
    lake = build_container().lake
    result = lake.resolve_fund("刘旭管理的大成高鑫A（000628.OF）")
    bare = lake.resolve_fund("000628")

    assert [item.fund_symbol for item in result] == ["000628.OF"]
    assert [item.fund_symbol for item in bare] == ["000628.OF"]


def test_fund_resolve_supports_non_contiguous_common_abbreviation():
    container = build_container()

    result = container.lake.resolve_fund("东财通信C")

    assert any(item.fund_symbol == "008327.OF" for item in result)
    coverage = container.lake.coverage("008327.OF")
    assert coverage["eligibility"]["identity_source"] == "lake"
    assert coverage["eligibility"]["eligible_for_full_report"] is False
    assert "identity_not_in_report_universe" in coverage["eligibility"]["blocking_reasons"]


def test_select_only_policy():
    lake = build_container().lake
    result = lake.run_select("SELECT count(*) AS n FROM fund_profiles")
    assert result["rows"][0][0] > 20_000


def test_lof_of_symbol_reads_exchange_suffix_holdings():
    container = build_container()
    coverage = container.lake.coverage("162703.OF", date(2025, 3, 25), date(2026, 8, 26))
    repository = LakeRepository(container.lake.database)
    try:
        positions = repository.positions("162703.OF")
    finally:
        repository.close()

    assert coverage["holdings"]["rows"] > 0
    assert not positions.empty
    assert positions["report_period"].min().year < 2015
