import time
from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from fund_research_agent.adapters.model import ModelGateway
from fund_research_agent.config import load_settings
from fund_research_agent.domain.contracts import ToolResultEnvelope, ToolStatus
from fund_research_agent.memory.service import MemoryService
from fund_research_agent.paths import WriteBoundary
from fund_research_agent.persistence.database import Database
from fund_research_agent.runtime_deep import DeepAgentRuntime
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
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, bool]] = []

    async def execute(self, _run_id, name, arguments, *, explicit_report=False, **_kwargs):
        self.calls.append((name, arguments, explicit_report))
        if name == "fund.resolve":
            data = [{
                "fund_symbol": "000628.OF",
                "fund_name": "大成高鑫A",
                "manager_name": "刘旭",
            }]
        elif name == "fund.research_snapshot":
            data = {"fund_symbol": arguments["fund_symbol"], "annual_returns": []}
        elif name == "fund.metric":
            data = {"fund_symbol": arguments["fund_symbol"], "metric": arguments["metric"]}
        else:
            data = {"ok": True}
        return ToolResultEnvelope(status=ToolStatus.SUCCESS, data=data)


class ScriptedChatModel(BaseChatModel):
    responses: list[AIMessage]
    disable_streaming: bool = True

    def __init__(self, responses: list[AIMessage]):
        super().__init__(responses=list(responses), disable_streaming=True)

    @property
    def _llm_type(self) -> str:
        return "scripted-deep-agent-test"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        del tools, tool_choice, kwargs
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager=None,
        **kwargs: Any,
    ) -> ChatResult:
        del messages, stop, run_manager, kwargs
        if not self.responses:
            raise AssertionError("scripted model exhausted")
        return ChatResult(generations=[ChatGeneration(message=self.responses.pop(0))])


class SlowChatModel(ScriptedChatModel):
    delay_seconds: float = 0.2

    def _generate(self, *args: Any, **kwargs: Any) -> ChatResult:
        time.sleep(self.delay_seconds)
        return super()._generate(*args, **kwargs)


async def make_runtime(tmp_path: Path, model: BaseChatModel, *, deadline: int = 180):
    project = tmp_path / "project"
    legacy = tmp_path / "legacy"
    (project / "var").mkdir(parents=True)
    (project / "src/fund_research_agent/deep_skills").mkdir(parents=True)
    legacy.mkdir()
    database = Database(
        project / "var/state/agent.sqlite", WriteBoundary(project, legacy)
    )
    await database.migrate(Path(__file__).resolve().parents[2] / "sql")
    settings = load_settings().model_copy(
        update={
            "project_root": project,
            "legacy_root": legacy,
            "var_root": project / "var",
            "state_db": project / "var/state/agent.sqlite",
            "checkpoint_db": project / "var/state/checkpoints.sqlite",
            "runtime": load_settings().runtime.model_copy(
                update={"chat_deadline_seconds": deadline}
            ),
        }
    )
    gateway = FakeGateway()
    runtime = DeepAgentRuntime(
        settings,
        database,
        ModelGateway(Path(__file__).resolve().parents[2] / "config/model_roles.yaml"),
        bridge_registry(),
        gateway,  # type: ignore[arg-type]
        MemoryService(database),
        chat_model=model,
    )
    session_id = await database.create_session("thread")
    return runtime, database, gateway, session_id


async def test_deep_runtime_persists_thread_and_reuses_verified_active_fund(tmp_path: Path):
    project = tmp_path / "project"
    legacy = tmp_path / "legacy"
    (project / "var").mkdir(parents=True)
    (project / "src/fund_research_agent/deep_skills").mkdir(parents=True)
    legacy.mkdir()
    boundary = WriteBoundary(project, legacy)
    database = Database(project / "var/state/agent.sqlite", boundary)
    await database.migrate(Path(__file__).resolve().parents[2] / "sql")
    registry = bridge_registry()
    fake_gateway = FakeGateway()
    settings = load_settings().model_copy(
        update={
            "project_root": project,
            "legacy_root": legacy,
            "var_root": project / "var",
            "state_db": project / "var/state/agent.sqlite",
            "checkpoint_db": project / "var/state/checkpoints.sqlite",
        }
    )
    model = ScriptedChatModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "fund_resolve",
                        "args": {"query": "大成高鑫"},
                        "id": "call_resolve",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="已确认大成高鑫A。"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "fund_research_snapshot",
                        "args": {},
                        "id": "call_snapshot",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="它的研究快照已返回。"),
        ]
    )
    # The full Runtime consumes the governed gateway protocol. Keep the fake
    # behavior while retaining the same bridge type used in production.
    runtime = DeepAgentRuntime(
        settings,
        database,
        ModelGateway(Path(__file__).resolve().parents[2] / "config/model_roles.yaml"),
        registry,
        fake_gateway,  # type: ignore[arg-type]
        MemoryService(database),
        chat_model=model,
    )
    session_id = await database.create_session("thread")

    first_run = await database.create_agent_run(session_id)
    first = await runtime.run(session_id, "分析大成高鑫", run_id=first_run)
    second_run = await database.create_agent_run(session_id)
    second = await runtime.run(session_id, "它的风险怎么样", run_id=second_run)

    assert first["answer"] == "已确认大成高鑫A。"
    assert second["answer"] == "它的研究快照已返回。"
    state = await database.get_session_context(session_id)
    assert state["active_funds"] == ["000628.OF"]
    snapshot_call = next(call for call in fake_gateway.calls if call[0] == "fund.research_snapshot")
    assert snapshot_call[1]["fund_symbol"] == "000628.OF"
    assert (await database.get_agent_run(first_run))["checkpoint_id"]
    assert (await database.get_agent_run(second_run))["checkpoint_id"]
    first_events = await database.list_events(first_run)
    visible = [
        event["payload"]["content"]
        for event in first_events
        if event["event_type"] == "message_delta"
    ]
    assert visible == ["已确认大成高鑫A。"]
    progress = [
        event["payload"]
        for event in first_events
        if event["event_type"] == "agent_progress"
    ]
    assert progress[0]["phase"] == "understanding"
    assert progress[-1]["phase"] == "completed"
    assert all("content" not in item for item in progress)
    await runtime.stop()


async def test_deep_runtime_executes_parallel_tools_and_keeps_both_results(tmp_path: Path):
    model = ScriptedChatModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "fund_research_snapshot",
                        "args": {"fund_symbol": "000628.OF"},
                        "id": "parallel_a",
                        "type": "tool_call",
                    },
                    {
                        "name": "fund_research_snapshot",
                        "args": {"fund_symbol": "007119.OF"},
                        "id": "parallel_b",
                        "type": "tool_call",
                    },
                ],
            ),
            AIMessage(content="两只基金的结构化快照已完成。"),
        ]
    )
    runtime, database, gateway, session_id = await make_runtime(tmp_path, model)

    result = await runtime.run(session_id, "比较两只基金")

    assert result["answer"] == "两只基金的结构化快照已完成。"
    snapshots = [call for call in gateway.calls if call[0] == "fund.research_snapshot"]
    assert {call[1]["fund_symbol"] for call in snapshots} == {"000628.OF", "007119.OF"}
    await runtime.stop()


async def test_deep_runtime_allows_model_to_correct_invalid_tool_arguments(tmp_path: Path):
    model = ScriptedChatModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "fund_metric",
                        "args": {"fund_symbol": "000628.OF", "metric": "unsupported"},
                        "id": "bad_args",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "fund_metric",
                        "args": {"fund_symbol": "000628.OF", "metric": "latest_nav"},
                        "id": "corrected_args",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="已按正确参数查询最新净值。"),
        ]
    )
    runtime, _database, gateway, session_id = await make_runtime(tmp_path, model)

    result = await runtime.run(session_id, "查询最新净值")

    assert result["answer"] == "已按正确参数查询最新净值。"
    metrics = [call for call in gateway.calls if call[0] == "fund.metric"]
    assert len(metrics) == 1
    assert metrics[0][1]["metric"] == "latest_nav"
    await runtime.stop()


async def test_deep_runtime_timeout_is_recoverable_without_restart(tmp_path: Path):
    model = SlowChatModel([AIMessage(content="迟到的回答")])
    runtime, database, _gateway, session_id = await make_runtime(
        tmp_path, model, deadline=0
    )

    result = await runtime.run(session_id, "测试超时")

    assert result["status"] == "recoverable"
    assert result["stop_reason"] == "chat_deadline_exceeded"
    run = await database.get_agent_run(result["run_id"])
    assert run["status"] == "recoverable"
    events = await database.list_events(result["run_id"])
    assert events[-1]["event_type"] == "run_recoverable"
    await runtime.stop()


async def test_deep_runtime_context_projection_excludes_legacy_router_state(tmp_path: Path):
    runtime, database, _gateway, session_id = await make_runtime(
        tmp_path, ScriptedChatModel([AIMessage(content="完成")])
    )
    await database.update_session_context(
        session_id,
        active_funds=["000628.OF"],
        last_intent="legacy_intent",
        pending_intent="legacy_pending",
    )

    context = await database.get_deep_session_context(session_id)

    assert context["active_funds"] == ["000628.OF"]
    assert "last_intent" not in context
    assert "pending_intent" not in context
    await runtime.stop()


def test_deep_runtime_detects_compaction_event_inside_model_update():
    event = DeepAgentRuntime._find_compaction(
        {
            "messages": [],
            "private": {
                "_summarization_event": {
                    "cutoff_index": 18,
                    "file_path": "/conversation_history/thread.md",
                    "summary_message": "not exposed",
                }
            },
        }
    )

    assert event == {
        "cutoff_index": 18,
        "file_path": "/conversation_history/thread.md",
    }


async def test_fixed_subagents_have_bounded_business_tools(tmp_path: Path):
    runtime, database, gateway, session_id = await make_runtime(
        tmp_path, ScriptedChatModel([AIMessage(content="unused")])
    )
    run_id = await database.create_agent_run(session_id)
    context = BridgeRunContext(
        run_id,
        session_id,
        "比较三只基金",
        await database.get_deep_session_context(session_id),
    )
    bridge = DeepToolBridge(bridge_registry(), gateway, database, context)  # type: ignore[arg-type]

    profiles = runtime._subagents(bridge)
    tool_matrix = {
        profile["name"]: {tool.name for tool in profile["tools"]} for profile in profiles
    }

    assert set(tool_matrix) == {
        "data-researcher",
        "qualitative-researcher",
        "risk-researcher",
        "evidence-reviewer",
    }
    for tools in tool_matrix.values():
        assert not tools & {
            "report_request",
            "report_resume",
            "report_cancel",
            "memory_remember",
            "memory_forget",
            "write_file",
            "execute",
        }
    await runtime.stop()
