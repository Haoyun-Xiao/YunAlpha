import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from fund_research_agent.domain.contracts import ReportRequest, ToolResultEnvelope, ToolStatus
from fund_research_agent.paths import WriteBoundary
from fund_research_agent.persistence.database import Database
from fund_research_agent.runtime_deep.tool_bridge import BridgeRunContext, DeepToolBridge
from fund_research_agent.tools.builtin import (
    ArtifactIdInput,
    EmptyInput,
    ForgetInput,
    FundQuery,
    JobIdInput,
    MaterialQuery,
    RememberInput,
)
from fund_research_agent.tools.registry import ToolRegistry, ToolSpec


async def _unused(_value):  # pragma: no cover
    return None


async def test_report_authorization_accepts_long_fund_request(tmp_path):
    _db, gateway, bridge, _context = await make_bridge(
        tmp_path, "请新写一份华安媒体互联网A的完整基金深度研究报告，截止2026年9月16日。"
    )
    result = json.loads(await bridge.select("report_request")[0].ainvoke({"fund_query": "大成高鑫"}))
    assert result["status"] == "accepted"
    generated = [args for name, args, _ in gateway.calls if name == "report.generate"]
    assert generated[0]["force_refresh"] is True


def bridge_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for name, model in (
        ("fund.resolve", FundQuery),
        ("web.search_manager_materials", MaterialQuery),
        ("report.status", JobIdInput),
        ("artifact.read", ArtifactIdInput),
        ("memory.remember", RememberInput),
        ("memory.forget", ForgetInput),
        ("memory.list", EmptyInput),
    ):
        registry.register(ToolSpec(name, name, model, _unused))
    return registry


class FakeGateway:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.calls: list[tuple[str, dict, bool]] = []

    async def execute(self, run_id, name, arguments, *, explicit_report=False, **_kwargs):
        self.calls.append((name, arguments, explicit_report))
        if name == "fund.resolve":
            query = str(arguments.get("query") or "")
            if "008327" in query:
                data = [{"fund_symbol": "008327.OF", "fund_name": "东财通信C"}]
            elif "东财通信" in query:
                data = [
                    {"fund_symbol": "008326.OF", "fund_name": "东财通信A"},
                    {"fund_symbol": "008327.OF", "fund_name": "东财通信C"},
                ]
            elif "中泰星元" in query:
                data = [{"fund_symbol": "006567.OF", "fund_name": "中泰星元", "manager_name": "姜诚"}]
            else:
                data = [
                    {
                        "fund_symbol": "000628.OF",
                        "fund_name": "大成高鑫A",
                        "manager_name": "刘旭",
                        "manager_tenure_start": "2015-07-29",
                    }
                ]
        elif name == "fund.inspect_coverage":
            data = {"eligibility": {"eligible_for_full_report": True}}
        elif name == "report.generate":
            request = ReportRequest.model_validate(arguments)
            job_id, _ = await self.database.create_report_job(request, "deep-agent-test", "v1")
            job = await self.database.get_report_job(job_id)
            data = {"status": "accepted", "job_id": job_id, "trace_id": job.trace_id}
        elif name == "fund.research_snapshot":
            data = {"fund_symbol": arguments["fund_symbol"], "annual_returns": []}
        else:
            data = {"ok": True, "arguments": arguments}
        return ToolResultEnvelope(status=ToolStatus.SUCCESS, data=data)


async def make_bridge(tmp_path: Path, user_text: str):
    project = tmp_path / "project"
    legacy = tmp_path / "legacy"
    (project / "var").mkdir(parents=True)
    legacy.mkdir()
    database = Database(project / "var/state.sqlite", WriteBoundary(project, legacy))
    await database.migrate(Path(__file__).resolve().parents[2] / "sql")
    session_id = await database.create_session("deep")
    run_id = await database.create_agent_run(session_id)
    context = BridgeRunContext(run_id, session_id, user_text, await database.get_session_context(session_id))
    gateway = FakeGateway(database)
    return database, gateway, DeepToolBridge(bridge_registry(), gateway, database, context), context


async def seed_completed_report(
    database: Database,
    *,
    as_of: date,
    fund_symbol: str = "000628.OF",
    fund_name: str = "大成高鑫A",
    manager_name: str = "刘旭",
) -> str:
    request = ReportRequest(fund_query=fund_symbol, as_of=as_of, web_mode="auto")
    job_id, _created = await database.create_report_job(
        request, f"seed-{fund_symbol}-{as_of}", "v1"
    )
    now = datetime.now(UTC).isoformat()
    document_id = f"report:seed-{fund_symbol}-{as_of}"
    async with database.connection() as connection:
        await connection.execute(
            "UPDATE report_jobs SET status='completed',current_stage='completed',progress=1,"
            "completed_at=?,updated_at=? WHERE job_id=?",
            (now, now, job_id),
        )
        await connection.execute(
            "INSERT INTO knowledge_documents(document_id,logical_key,source_type,fund_symbol,"
            "fund_name,manager_name,as_of,title,artifact_id,job_id,source_uri,source_hash,"
            "is_current,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)",
            (
                document_id,
                f"report:{fund_symbol}:{manager_name}:{as_of}",
                "report_md",
                fund_symbol,
                fund_name,
                manager_name,
                as_of.isoformat(),
                "seed-report",
                None,
                job_id,
                "/seed/report.md",
                f"seed-hash-{fund_symbol}-{as_of}",
                now,
                now,
            ),
        )
        await connection.execute("UPDATE knowledge_documents SET pipeline_version='report-data-v2',data_as_of=?,manager_tenure_start='2015-07-29' WHERE document_id=?", (as_of.isoformat(), document_id))
        await connection.commit()
    return job_id


async def test_resolve_updates_active_fund_and_followup_tool_uses_it(tmp_path: Path):
    database, gateway, bridge, context = await make_bridge(tmp_path, "分析大成高鑫")

    await bridge.select("fund_resolve")[0].ainvoke({"query": "大成高鑫"})
    result = json.loads(await bridge.select("fund_research_snapshot")[0].ainvoke({}))

    assert result["data"]["fund_symbol"] == "000628.OF"
    assert context.active_fund == "000628.OF"
    assert (await database.get_session_context(context.session_id))["active_funds"] == ["000628.OF"]
    assert gateway.calls[-1][1]["fund_symbol"] == "000628.OF"


async def test_subagent_resolve_does_not_mutate_main_session_active_fund(tmp_path: Path):
    database, _gateway, bridge, context = await make_bridge(tmp_path, "比较几只基金")

    await bridge.select_for_subagent("fund_resolve")[0].ainvoke({"query": "大成高鑫"})

    assert context.active_fund is None
    assert (await database.get_session_context(context.session_id))["active_funds"] == []


async def test_report_request_uses_active_fund_and_forces_three_formats(tmp_path: Path):
    database, gateway, bridge, context = await make_bridge(tmp_path, "给他写一份完整报告")
    await database.update_session_context(
        context.session_id, active_funds=["000628.OF"], active_manager="刘旭"
    )
    context.session_context = await database.get_session_context(context.session_id)

    result = json.loads(
        await bridge.select("report_request")[0].ainvoke(
            {"output_formats": ["markdown"], "as_of": date(2026, 8, 29)}
        )
    )

    assert result["status"] == "accepted"
    assert result["output_formats"] == ["markdown", "html", "docx"]
    generated = next(call for call in gateway.calls if call[0] == "report.generate")
    assert generated[1]["output_formats"] == ["markdown", "html", "docx"]
    assert generated[2] is True
    assert context.report_blocks[0]["job_id"] == result["job_id"]


async def test_report_request_reuses_recent_completed_report_without_new_job(tmp_path: Path):
    database, gateway, bridge, context = await make_bridge(
        tmp_path, "给大成高鑫写一份报告"
    )
    old_job_id = await seed_completed_report(
        database, as_of=date.today() - timedelta(days=30)
    )

    result = json.loads(
        await bridge.select("report_request")[0].ainvoke({"fund_query": "大成高鑫"})
    )

    assert result["status"] == "reused"
    assert result["job_id"] == old_job_id
    assert result["reused_existing"] is True
    assert not any(name == "fund.inspect_coverage" for name, _args, _flag in gateway.calls)
    assert not any(name == "report.generate" for name, _args, _flag in gateway.calls)
    state = await database.get_deep_session_context(context.session_id)
    assert state["active_report_job_id"] == old_job_id
    assert state["active_report_document_id"].startswith("report:seed-")


async def test_explicit_rewrite_bypasses_recent_report_reuse(tmp_path: Path):
    database, gateway, bridge, _context = await make_bridge(
        tmp_path, "给大成高鑫新写一篇报告"
    )
    old_job_id = await seed_completed_report(
        database, as_of=date.today() - timedelta(days=30)
    )

    result = json.loads(
        await bridge.select("report_request")[0].ainvoke({"fund_query": "大成高鑫"})
    )

    generated = next(call for call in gateway.calls if call[0] == "report.generate")
    assert result["status"] == "accepted"
    assert result["job_id"] != old_job_id
    assert result["reused_existing"] is False
    assert generated[1]["force_refresh"] is True


async def test_report_and_memory_writes_require_current_turn_authorization(tmp_path: Path):
    _database, gateway, bridge, _context = await make_bridge(tmp_path, "分析这只基金")

    report = json.loads(await bridge.select("report_request")[0].ainvoke({"fund_query": "000628.OF"}))
    memory = json.loads(
        await bridge.select("memory_remember")[0].ainvoke(
            {"kind": "preference", "content": "我更关注回撤", "scope": "global"}
        )
    )

    assert report["status"] == "policy_denied"
    assert memory["status"] == "policy_denied"
    assert not any(name in {"report.generate", "memory.remember"} for name, _args, _flag in gateway.calls)


async def test_new_fund_in_same_session_replaces_old_active_fund_for_report(tmp_path: Path):
    database, gateway, bridge, context = await make_bridge(
        tmp_path, "给中泰星元写一份完整报告"
    )
    await database.update_session_context(
        context.session_id, active_funds=["000628.OF"], active_manager="刘旭"
    )
    context.session_context = await database.get_deep_session_context(context.session_id)

    result = json.loads(
        await bridge.select("report_request")[0].ainvoke({"fund_query": "中泰星元"})
    )

    assert result["status"] == "accepted"
    assert result["fund_symbol"] == "006567.OF"
    state = await database.get_deep_session_context(context.session_id)
    assert state["active_funds"] == ["006567.OF"]
    generated = next(call for call in gateway.calls if call[0] == "report.generate")
    assert generated[1]["fund_query"] == "006567.OF"


async def test_ambiguous_fund_report_returns_candidates_without_spending_job(tmp_path: Path):
    _database, gateway, bridge, _context = await make_bridge(
        tmp_path, "给东财通信写一份完整报告"
    )

    result = json.loads(
        await bridge.select("report_request")[0].ainvoke({"fund_query": "东财通信"})
    )

    assert result["status"] == "clarification_required"
    assert len(result["candidates"]) == 2
    assert not any(name == "report.generate" for name, _args, _flag in gateway.calls)


async def test_report_clarification_can_consume_one_turn_structured_authorization(tmp_path: Path):
    database, gateway, bridge, context = await make_bridge(
        tmp_path, "给东财通信写一份完整报告"
    )
    first = json.loads(
        await bridge.select("report_request")[0].ainvoke({"fund_query": "东财通信"})
    )
    assert first["status"] == "clarification_required"
    pending = (await database.get_deep_session_context(context.session_id))["pending_action"]
    assert pending["type"] == "report_request"

    followup_context = BridgeRunContext(
        context.run_id,
        context.session_id,
        "C类",
        await database.get_deep_session_context(context.session_id),
    )
    followup = DeepToolBridge(
        bridge_registry(), gateway, database, followup_context
    )
    result = json.loads(
        await followup.select("report_request")[0].ainvoke({"fund_query": "008327.OF"})
    )

    assert result["status"] == "accepted"
    assert result["fund_symbol"] == "008327.OF"
    assert (await database.get_deep_session_context(context.session_id))["pending_action"] is None


async def test_report_question_defaults_to_active_indexed_document(tmp_path: Path):
    database, gateway, bridge, context = await make_bridge(
        tmp_path, "这篇报告对行业配置怎么看"
    )
    await database.update_session_context(
        context.session_id,
        active_funds=["000628.OF"],
        active_report_document_id="doc_report_000628",
    )
    context.session_context = await database.get_deep_session_context(context.session_id)

    await bridge.select("kb_search")[0].ainvoke(
        {"query": "这篇报告对行业配置怎么看", "limit": 5}
    )

    search = next(call for call in gateway.calls if call[0] == "kb.search")
    assert search[1]["fund_symbol"] == "000628.OF"
    assert search[1]["document_id"] == "doc_report_000628"


async def test_destructive_report_and_memory_tools_require_current_turn_authorization(tmp_path: Path):
    _database, gateway, bridge, _context = await make_bridge(tmp_path, "查询当前状态")

    resume = json.loads(
        await bridge.select("report_resume")[0].ainvoke({"job_id": "report_1"})
    )
    cancel = json.loads(
        await bridge.select("report_cancel")[0].ainvoke({"job_id": "report_1"})
    )
    forget = json.loads(
        await bridge.select("memory_forget")[0].ainvoke({"memory_id": "memory_1"})
    )

    assert resume["status"] == "policy_denied"
    assert cancel["status"] == "policy_denied"
    assert forget["status"] == "policy_denied"
    assert not any(
        name in {"report.resume", "report.cancel", "memory.forget"}
        for name, _args, _flag in gateway.calls
    )


async def test_legacy_session_is_readonly_and_deep_session_is_single_run(tmp_path: Path):
    project = tmp_path / "project"
    legacy = tmp_path / "legacy"
    (project / "var").mkdir(parents=True)
    legacy.mkdir()
    database = Database(project / "var/state.sqlite", WriteBoundary(project, legacy))
    await database.migrate(Path(__file__).resolve().parents[2] / "sql")
    old = await database.create_session("old", runtime_kind="legacy_readonly")
    new = await database.create_session("new")

    with pytest.raises(ValueError, match="legacy_session_readonly"):
        await database.create_agent_run(old)
    run_id = await database.create_agent_run(new)
    with pytest.raises(ValueError, match="session_run_active"):
        await database.create_agent_run(new)

    await database.update_agent_run(run_id, status="completed")
    assert (await database.create_agent_run(new)).startswith("run_")


async def test_report_request_does_not_reuse_pre_fix_pipeline(tmp_path: Path):
    database, gateway, bridge, _ = await make_bridge(tmp_path, "给大成高鑫写一份报告")
    old_id = await seed_completed_report(database, as_of=date.today())
    async with database.connection() as connection:
        await connection.execute("UPDATE knowledge_documents SET pipeline_version='legacy' WHERE job_id=?", (old_id,))
        await connection.commit()
    result = json.loads(await bridge.select("report_request")[0].ainvoke({"fund_query": "大成高鑫"}))
    assert result["status"] == "accepted" and result["job_id"] != old_id


async def test_reuse_is_based_on_actual_nav_date_not_request_date(tmp_path: Path):
    database, gateway, bridge, _ = await make_bridge(tmp_path, "给大成高鑫写一份报告")
    old_id = await seed_completed_report(database, as_of=date.today())
    async with database.connection() as connection:
        await connection.execute("UPDATE knowledge_documents SET data_as_of=? WHERE job_id=?", ((date.today()-timedelta(days=100)).isoformat(), old_id))
        await connection.commit()
    result = json.loads(await bridge.select("report_request")[0].ainvoke({"fund_query": "大成高鑫"}))
    assert result["status"] == "accepted"


async def test_switching_fund_clears_old_report_context(tmp_path: Path):
    database, gateway, bridge, context=await make_bridge(tmp_path,'分析大成高鑫')
    await database.update_session_context(context.session_id,active_funds=['007119.OF'],active_report_job_id='old-job',active_report_document_id='old-document')
    context.session_context=await database.get_session_context(context.session_id)
    await bridge.select('fund_resolve')[0].ainvoke({'query':'大成高鑫'})
    state=await database.get_session_context(context.session_id)
    assert state['active_funds']==['000628.OF']
    assert state['active_report_job_id'] is None and state['active_report_document_id'] is None
