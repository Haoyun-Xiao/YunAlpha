import json
from dataclasses import dataclass
from datetime import date

import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from fund_research_agent.paths import WriteBoundary
from fund_research_agent.workflows.index_enhancement import prompts
from fund_research_agent.workflows.index_enhancement.materials import REQUIRED, validate_facts
from fund_research_agent.workflows.index_enhancement.render import build_content, markdown_html
from fund_research_agent.workflows.index_enhancement.workflow import (
    IndexEnhancementWorkflow,
    validate_text,
)


def fixture_data(tmp_path):
    values = {key: "已有事实" for key in REQUIRED}
    values.update(
        {
            "fund_name": "景顺长城沪深300指数增强A",
            "fund_code": "000311",
            "found_date": "2013年10月29日",
            "manager_since": "2013年10月29日",
            "manager": "黎海威",
            "benchmark": "95%×沪深300指数收益率＋1.5%（指年收益率，评价时按期间折算）",
            "stock_range": "基金资产的90%－95%",
            "constituent_min": "非现金基金资产的80%",
            "cash_min": "扣除保证金后不低于基金资产净值的5%",
        }
    )
    text = "\n".join(values.values())
    path = tmp_path / "source.json"
    path.write_text(json.dumps([{"page": 1, "text": text}], ensure_ascii=False))
    extracted = {
        "facts": [
            {"key": k, "value": v, "quote": v, "page": 1, "source_id": "S1"}
            for k, v in values.items()
        ]
    }
    materials = {
        "fund_symbol": "000311.OF",
        "sources": [
            {
                "source_id": "S1",
                "text_path": str(path),
                "published_at": "2026-07-09",
                "url": "https://source.test/report.pdf",
            }
        ],
    }
    snapshot = {"fund_profiles": [{"name": "景顺长城沪深300指数增强A", "symbol": "000311.OF"}]}
    return extracted, materials, snapshot


def test_facts_require_real_quote_and_correct_contract(tmp_path):
    extracted, materials, snapshot = fixture_data(tmp_path)
    assert validate_facts(extracted, materials, snapshot)["valid"]
    benchmark = next(f for f in extracted["facts"] if f["key"] == "benchmark")
    benchmark["value"] = "95%沪深300＋5%活期存款"
    result = validate_facts(extracted, materials, snapshot)
    assert "benchmark_contract_changed" in result["errors"]
    benchmark["quote"] = "原文没有这句话"
    assert any(
        e.startswith("quote_not_found")
        for e in validate_facts(extracted, materials, snapshot)["errors"]
    )


def test_prototype_output_is_single_chapter_and_escapes_markup(tmp_path):
    extracted, materials, snapshot = fixture_data(tmp_path)
    facts = validate_facts(extracted, materials, snapshot)
    data = {
        "verified_facts": facts,
        "sources": [
            {
                "source_id": "S1",
                "title": "正式概要",
                "published_at": "2026-07-09",
                "url": "https://source.test",
            }
        ],
        "as_of": "2026-10-26",
    }
    content = build_content(data, "## 一、产品定位\n\n**增强** [S1]")
    assert "1.5%" in content["markdown"] and "5%活期存款" not in content["markdown"]
    assert "Part2" not in content["markdown"]
    assert content["timeline"][0][0] == "2013-10-29"
    assert "<script>" not in markdown_html("<script>alert(1)</script>")


@dataclass
class Role:
    max_tokens: int = 16000


class FakeModel:
    def __init__(self):
        self.calls = 0

    def descriptor(self, _role):
        return {"provider": "fake", "model": "fake"}

    def role(self, _role):
        return Role()

    async def chat(self, _role, messages, **_kwargs):
        self.calls += 1
        return {"content": "cached-content", "usage": {"total_tokens": 1}}


async def test_model_cache_reuses_and_invalidates_changed_inputs(tmp_path):
    boundary = WriteBoundary(tmp_path, tmp_path / "legacy", write_root=tmp_path)
    model = FakeModel()
    wf = IndexEnhancementWorkflow(tmp_path / "run", tmp_path, model, boundary)
    assert await wf.call("part1", "report", "prompt", {"input": 1}) == "cached-content"
    await wf.call("part1", "report", "prompt", {"input": 1})
    assert model.calls == 1
    await wf.call("part1", "report", "prompt", {"input": 2})
    await wf.call("part1", "report", "changed-prompt", {"input": 2})
    assert model.calls == 3


async def test_real_graph_checkpoint_resumes_render_without_repeating_generation(tmp_path):
    boundary = WriteBoundary(tmp_path, tmp_path / "legacy", write_root=tmp_path)
    wf = IndexEnhancementWorkflow(
        tmp_path / "run", tmp_path, FakeModel(), boundary, as_of=date(2026, 10, 26)
    )
    seen = []

    def node(name, result):
        async def run(_state):
            seen.append(name)
            return result

        return run

    wf.prepare = node("prepare", {})
    wf.extract = node("extract", {"facts": {}})
    wf.check_facts = node("validate", {})
    wf.handbook = node("handbook", {"handbook": "完整手册"})
    wf.check_handbook = node("validate_handbook", {})
    wf.part_input = node("input", {"part_input": {}})
    wf.write_part1 = node("part1", {"part1": "正文"})
    wf.audit = node(
        "audit", {"audit": {"passed": False, "issues": ["确定问题"], "deterministic_errors": []}}
    )
    wf.revise = node("revise", {"part1": "修订正文", "revisions": 1})
    first = True

    async def render(_state):
        nonlocal first
        seen.append("render")
        if first:
            first = False
            raise RuntimeError("injected_render_failure")
        return {"outputs": {"html": "ok"}}

    wf.render = render
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "checkpoint.sqlite")) as saver:
        graph = wf.graph(saver)
        config = {"configurable": {"thread_id": "single-chapter"}}
        with pytest.raises(RuntimeError, match="injected_render_failure"):
            await graph.ainvoke({"revisions": 0}, config)
        result = await graph.ainvoke(None, config)
    assert result["outputs"]["html"] == "ok"
    assert seen.count("part1") == 1 and seen.count("revise") == 1
    assert seen.count("render") == 2


def test_handbook_and_part1_have_distinct_required_sections():
    text = "\n\n".join(f"## {h}\n\n" + "研究内容[S1]" * 100 for h in prompts.HANDBOOK_SECTIONS)
    assert not validate_text(text, "handbook", ["S1"])
    assert validate_text(text, "part1", ["S1"])


def test_futures_turnover_limit_preserves_period_and_closing_exception():
    body = "\n\n".join(
        f"## {number}、{label}\n\n" + "研究事实[S1]" * 50
        for number, label in zip("一二三四", prompts.SECTIONS, strict=True)
    )
    incomplete = body + "。股指期货合约成交金额不超过上一交易日基金资产净值的20%[S1]。"
    assert "futures_turnover_limit_missing_qualifiers" in validate_text(incomplete, "part1", ["S1"])
    complete = (
        body
        + "。在任何交易日内交易（不包括平仓）的股指期货合约成交金额不超过上一交易日基金资产净值的20%[S1]。"
    )
    assert not validate_text(complete, "part1", ["S1"])


async def test_actual_tool_use_comes_from_periodic_report_not_old_prospectus(tmp_path):
    boundary = WriteBoundary(tmp_path, tmp_path / "legacy", write_root=tmp_path)
    wf = IndexEnhancementWorkflow(tmp_path / "run", tmp_path, FakeModel(), boundary)
    pages = tmp_path / "periodic.json"
    pages.write_text(json.dumps([{"page": 77, "text": "权证投资明细 本报告期末未持有权证"}]))
    wf.materials = {
        "sources": [
            {
                "source_id": "S2",
                "kind": "prospectus",
                "title": "更新招募书（旧持仓）",
                "published_at": "2026-06-23",
                "url": "https://source.test/old",
            },
            {
                "source_id": "S4",
                "kind": "periodic",
                "title": "2026年中期报告",
                "published_at": "2026-08-28",
                "text_path": str(pages),
                "url": "https://source.test/new",
            },
        ]
    }
    state = {"facts": {}, "handbook": "完整手册"}
    result = await wf.part_input(state)
    evidence = result["part_input"]["tool_usage_evidence"]
    assert len(evidence) == 1 and evidence[0]["source_id"] == "S4"
    assert evidence[0]["page"] == 77
