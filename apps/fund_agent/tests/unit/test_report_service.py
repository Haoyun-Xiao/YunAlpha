from datetime import date

from fund_research_agent.domain.contracts import ReportRequest
from fund_research_agent.workflows.report.service import ReportJobService


def test_report_fingerprint_normalizes_natural_language_to_fund_symbol():
    first = ReportRequest(
        fund_query="大成高鑫股票A 000628.OF",
        as_of=date(2026, 8, 23),
        web_mode="live",
    )
    second = ReportRequest(
        fund_query="刘旭管理的大成高鑫（000628.of）",
        as_of=date(2026, 8, 23),
        web_mode="live",
    )

    assert ReportJobService.fingerprint(first) == ReportJobService.fingerprint(second)
