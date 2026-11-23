from pathlib import Path

import pytest

from fund_research_agent.adapters.model import ModelGateway
from fund_research_agent.config import load_settings
from fund_research_agent.memory.service import MemoryService
from fund_research_agent.paths import WriteBoundary
from fund_research_agent.persistence.database import Database
from fund_research_agent.runtime_deep import DeepAgentRuntime
from fund_research_agent.tools.gateway import ToolGateway
from fund_research_agent.tools.registry import ToolRegistry


async def make_database(tmp_path: Path) -> tuple[Database, str]:
    project = tmp_path / "project"
    legacy = tmp_path / "legacy"
    (project / "var/state").mkdir(parents=True)
    legacy.mkdir()
    database = Database(
        project / "var/state/agent.sqlite", WriteBoundary(project, legacy)
    )
    await database.migrate(Path(__file__).resolve().parents[2] / "sql")
    session_id = await database.create_session("recovery")
    return database, session_id


async def test_api_restart_marks_running_chat_recoverable_and_resume_is_atomic(tmp_path: Path):
    database, session_id = await make_database(tmp_path)
    run_id = await database.create_agent_run(session_id)

    recovered = await database.recover_interrupted_agent_runs()

    assert recovered == [run_id]
    assert (await database.get_agent_run(run_id))["status"] == "recoverable"
    events = await database.list_events(run_id)
    assert events[-1]["payload"]["reason"] == "api_restarted"

    resumed = await database.resume_agent_run(run_id)
    assert resumed["run_id"] == run_id
    assert (await database.get_agent_run(run_id))["status"] == "running"
    with pytest.raises(ValueError, match="run_not_recoverable"):
        await database.resume_agent_run(run_id)


async def test_new_message_supersedes_interrupted_run_without_losing_history(tmp_path: Path):
    database, session_id = await make_database(tmp_path)
    old_run = await database.create_agent_run(session_id)
    await database.add_message(session_id, "user", "你好")
    await database.update_agent_run(
        old_run, status="recoverable", stop_reason="model_or_runtime_failure"
    )

    new_run = await database.create_agent_run(session_id)

    assert (await database.get_agent_run(new_run))["status"] == "running"
    previous = await database.get_agent_run(old_run)
    assert previous["status"] == "stopped"
    assert previous["stop_reason"] == "superseded_by_new_message"
    with pytest.raises(ValueError, match="run_not_recoverable"):
        await database.resume_agent_run(old_run)
    async with database.connection() as connection:
        row = await (await connection.execute(
            "SELECT content FROM messages WHERE session_id=?", (session_id,)
        )).fetchone()
    assert row["content"] == "你好"


async def test_running_request_still_blocks_new_message(tmp_path: Path):
    database, session_id = await make_database(tmp_path)
    active_run = await database.create_agent_run(session_id)

    with pytest.raises(ValueError, match=f"session_run_active:{active_run}"):
        await database.create_agent_run(session_id)

    assert (await database.get_agent_run(active_run))["status"] == "running"


async def test_sse_event_cursor_skips_acknowledged_events(tmp_path: Path):
    database, session_id = await make_database(tmp_path)
    run_id = await database.create_agent_run(session_id)
    acknowledged = await database.add_event(run_id, "run_started", {"step": 1})
    await database.add_event(run_id, "checkpoint_saved", {"step": 2})
    await database.add_event(run_id, "done", {"step": 3})

    received = [
        event
        async for event in database.stream_events(
            run_id, after_id=acknowledged, poll_seconds=0.001
        )
    ]

    assert [event["event_type"] for event in received] == ["checkpoint_saved", "done"]
    assert all(event["event_id"] > acknowledged for event in received)


async def test_corrupt_checkpoint_database_fails_closed_without_legacy_fallback(tmp_path: Path):
    project = tmp_path / "project"
    legacy = tmp_path / "legacy"
    (project / "var/state").mkdir(parents=True)
    (project / "src/fund_research_agent/deep_skills").mkdir(parents=True)
    legacy.mkdir()
    checkpoint = project / "var/state/checkpoints.sqlite"
    checkpoint.write_text("not a sqlite database", encoding="utf-8")
    boundary = WriteBoundary(project, legacy)
    database = Database(project / "var/state/agent.sqlite", boundary)
    await database.migrate(Path(__file__).resolve().parents[2] / "sql")
    settings = load_settings().model_copy(
        update={
            "project_root": project,
            "legacy_root": legacy,
            "var_root": project / "var",
            "state_db": project / "var/state/agent.sqlite",
            "checkpoint_db": checkpoint,
        }
    )
    registry = ToolRegistry()
    gateway = ToolGateway(registry, database, boundary)
    runtime = DeepAgentRuntime(
        settings,
        database,
        ModelGateway(Path(__file__).resolve().parents[2] / "config/model_roles.yaml"),
        registry,
        gateway,
        MemoryService(database),
    )

    with pytest.raises(Exception, match="database|file is not a database"):
        await runtime.start()

    assert runtime._checkpointer is None
