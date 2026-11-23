from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from fund_research_agent.domain.contracts import ReportRequest
from fund_research_agent.paths import WriteBoundary
from fund_research_agent.persistence.database import Database


async def database(tmp_path: Path) -> Database:
    project = tmp_path / "project"
    legacy = tmp_path / "legacy"
    (project / "var").mkdir(parents=True)
    legacy.mkdir()
    result = Database(project / "var/agent.sqlite", WriteBoundary(project, legacy))
    await result.migrate(Path(__file__).resolve().parents[2] / "sql")
    return result


async def test_job_idempotency_and_atomic_lease(tmp_path: Path):
    db = await database(tmp_path)
    request = ReportRequest(fund_query="000628.OF", as_of=date(2026, 8, 23), web_mode="replay")
    first, created = await db.create_report_job(request, "same", "v1")
    second, created_again = await db.create_report_job(request, "same", "v1")
    assert created and not created_again and first == second
    claimed = await db.claim_report_job("worker-a", 60)
    assert claimed and claimed.job_id == first and claimed.attempt == 1
    assert await db.claim_report_job("worker-b", 60) is None


async def test_repeated_cancel_finishes_unleased_legacy_cancelling_job(tmp_path: Path):
    db = await database(tmp_path)
    request = ReportRequest(
        fund_query="000628.OF", as_of=date(2026, 8, 23), web_mode="replay"
    )
    job_id, _created = await db.create_report_job(request, "cancel", "v1")
    async with db.connection() as connection:
        await connection.execute(
            "UPDATE report_jobs SET status='cancelling',current_stage='queued',lease_owner=NULL "
            "WHERE job_id=?",
            (job_id,),
        )
        await connection.commit()

    await db.request_cancel(job_id)

    job = await db.get_report_job(job_id)
    assert job is not None
    assert job.status.value == "cancelled"
    assert job.current_stage == "cancelled"
    assert job.completed_at is not None


async def test_expired_running_job_is_recovered_before_next_claim(tmp_path: Path):
    db = await database(tmp_path)
    request = ReportRequest(
        fund_query="000628.OF", as_of=date(2026, 8, 23), web_mode="replay"
    )
    job_id, _created = await db.create_report_job(request, "recover-once", "v1")
    first = await db.claim_report_job("worker-a", 60)
    assert first is not None and first.attempt == 1
    expired = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    async with db.connection() as connection:
        await connection.execute(
            "UPDATE report_jobs SET status='generating_parts',current_stage='generating_parts',"
            "lease_expires_at=? WHERE job_id=?",
            (expired, job_id),
        )
        await connection.commit()

    second = await db.claim_report_job("worker-b", 60)

    assert second is not None
    assert second.job_id == job_id
    assert second.attempt == 2


async def test_second_expired_lease_fails_instead_of_hanging(tmp_path: Path):
    db = await database(tmp_path)
    request = ReportRequest(
        fund_query="000628.OF", as_of=date(2026, 8, 23), web_mode="replay"
    )
    job_id, _created = await db.create_report_job(request, "recover-limit", "v1")
    expired = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    async with db.connection() as connection:
        await connection.execute(
            "UPDATE report_jobs SET status='generating_parts',current_stage='generating_parts',"
            "attempt=2,lease_owner='dead-worker',lease_expires_at=? WHERE job_id=?",
            (expired, job_id),
        )
        await connection.commit()

    assert await db.claim_report_job("worker-c", 60) is None
    job = await db.get_report_job(job_id)
    assert job is not None
    assert job.status.value == "failed"
    assert "worker_lease_expired_after_retry" in job.errors


async def test_session_context_and_report_link_are_persistent(tmp_path: Path):
    db = await database(tmp_path)
    session_id = await db.create_session("context")
    await db.add_message(session_id, "user", "第一条")
    await db.add_message(session_id, "assistant", "第二条")
    state = await db.update_session_context(
        session_id,
        active_funds=["000628.OF"],
        active_manager="刘旭",
        active_report_job_id="pending",
        last_intent="GENERATE_REPORT",
    )
    assert state["active_funds"] == ["000628.OF"]
    assert state["active_manager"] == "刘旭"
    assert [item["content"] for item in await db.recent_messages(session_id, 2)] == [
        "第一条",
        "第二条",
    ]

    request = ReportRequest(
        fund_query="000628.OF", as_of=date(2026, 8, 23), web_mode="replay"
    )
    job_id, _created = await db.create_report_job(request, "linked", "v1")
    run_id = await db.create_agent_run(session_id)
    await db.link_report_job(job_id, session_id, run_id)
    job = await db.get_report_job(job_id)
    assert job is not None
    assert job.session_id == session_id
    assert job.source_run_id == run_id


async def test_service_heartbeat_is_persistent(tmp_path: Path):
    db = await database(tmp_path)
    await db.upsert_service_heartbeat(
        "report-worker", "worker-test", {"pid": 123, "hostname": "test-host"}
    )

    heartbeat = await db.get_service_heartbeat("report-worker")

    assert heartbeat is not None
    assert heartbeat["instance_id"] == "worker-test"
    assert heartbeat["metadata"] == {"pid": 123, "hostname": "test-host"}


async def test_handled_model_failure_is_not_misclassified_as_worker_crash(tmp_path):
    from fund_research_agent.domain.contracts import ReportStatus
    db=await database(tmp_path)
    job,_=await db.create_report_job(ReportRequest(fund_query='000628.OF',as_of=date.today()),'model-error','report-data-v2')
    await db.claim_report_job('worker-a',60)
    await db.update_report_job(job,status=ReportStatus.RECOVERABLE,stage='recoverable',errors=['model_output_incomplete:length'])
    assert await db.claim_report_job('worker-b',60) is None
    current=await db.get_report_job(job)
    assert current.status==ReportStatus.RECOVERABLE and current.lease_owner is None
    await db.resume_report_job(job)
    assert (await db.claim_report_job('worker-b',60)).job_id==job
