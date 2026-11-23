from pathlib import Path

from pydantic import BaseModel

from fund_research_agent.domain.contracts import ToolStatus
from fund_research_agent.paths import WriteBoundary
from fund_research_agent.persistence.database import Database
from fund_research_agent.tools.gateway import ToolGateway, ToolPolicy
from fund_research_agent.tools.registry import ToolRegistry, ToolSpec


class EmptyInput(BaseModel):
    pass


async def test_policy_denial_is_returned_and_recorded_instead_of_escaping(tmp_path: Path):
    project = tmp_path / "project"
    legacy = tmp_path / "legacy"
    (project / "var").mkdir(parents=True)
    legacy.mkdir()
    database = Database(project / "var/agent.sqlite", WriteBoundary(project, legacy))
    await database.migrate(Path(__file__).resolve().parents[2] / "sql")
    session_id = await database.create_session("policy")
    run_id = await database.create_agent_run(session_id)
    registry = ToolRegistry()

    async def must_not_run(_value):  # pragma: no cover - policy must stop it
        raise AssertionError("handler must not run")

    registry.register(
        ToolSpec(
            "report.generate",
            "expensive report",
            EmptyInput,
            must_not_run,
            permission="expensive",
        )
    )
    gateway = ToolGateway(registry, database, ToolPolicy(read_only=True))

    result = await gateway.execute(run_id, "report.generate", {})

    assert result.status == ToolStatus.ERROR
    assert result.error_code == "policy_denied"
    assert "明确请求" in str(result.error_message)
    events = await database.list_events(run_id)
    assert [event["event_type"] for event in events][-2:] == [
        "tool_call_started",
        "tool_error",
    ]
    async with database.connection() as connection:
        row = await (
            await connection.execute(
                "SELECT status,error_code FROM tool_calls WHERE run_id=?", (run_id,)
            )
        ).fetchone()
    assert dict(row) == {"status": "error", "error_code": "policy_denied"}
