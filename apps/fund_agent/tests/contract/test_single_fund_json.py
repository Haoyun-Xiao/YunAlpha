import json
from datetime import date

from fund_research_agent.bootstrap import build_container
from fund_research_agent.workflows.report.fund_json import generate_single_fund


def test_000418_generates_twelve_machine_readable_contracts():
    container = build_container()
    identity = container.lake.resolve_fund("000418.OF", date(2026, 8, 23))[0]
    result = generate_single_fund(
        identity,
        container.lake.database,
        container.settings.var_root / "tmp/contract-single-fund-json",
        date(2026, 8, 23),
        container.boundary,
    )
    assert len(result["files"]) == 12
    assert result["holding_rows"] > 1000
    for path in result["files"].values():
        with open(path, encoding="utf-8") as stream:
            assert isinstance(json.load(stream), dict)
