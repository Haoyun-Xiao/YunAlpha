from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite

from fund_research_agent.domain.contracts import (
    TERMINAL_REPORT_STATUSES,
    MemoryRecord,
    ReportJob,
    ReportRequest,
    ReportStatus,
)
from fund_research_agent.paths import WriteBoundary


def utc_now() -> datetime:
    return datetime.now(UTC)


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


class Database:
    def __init__(self, path: Path, boundary: WriteBoundary) -> None:
        self.path = boundary.assert_writable(path)
        self.boundary = boundary

    async def connect(self) -> aiosqlite.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = await aiosqlite.connect(self.path)
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA foreign_keys=ON")
        await connection.execute("PRAGMA journal_mode=WAL")
        await connection.execute("PRAGMA busy_timeout=30000")
        return connection

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[aiosqlite.Connection]:
        connection = await self.connect()
        try:
            yield connection
        finally:
            await connection.close()

    async def migrate(self, sql_dir: Path) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with self.connection() as connection:
            await connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            rows = await (await connection.execute("SELECT version FROM schema_migrations")).fetchall()
            applied = {str(row[0]) for row in rows}
            for migration in sorted(sql_dir.glob("*.sql")):
                if migration.name in applied:
                    continue
                await connection.executescript(migration.read_text(encoding="utf-8"))
                await connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (migration.name, utc_now().isoformat()),
                )
                await connection.commit()

    async def create_session(
        self,
        title: str = "新研究会话",
        user_id: str = "local",
        runtime_kind: str = "deep_agents",
    ) -> str:
        session_id = f"session_{uuid.uuid4().hex}"
        now = utc_now().isoformat()
        async with self.connection() as connection:
            await connection.execute(
                "INSERT INTO sessions(session_id,user_id,title,created_at,updated_at,runtime_kind) "
                "VALUES(?,?,?,?,?,?)",
                (session_id, user_id, title, now, now, runtime_kind),
            )
            if runtime_kind == "deep_agents":
                await connection.execute(
                    "INSERT INTO agent_threads(session_id,thread_id,runtime_name,runtime_version,"
                    "bootstrapped_at,updated_at) VALUES(?,?,?,?,?,?)",
                    (session_id, session_id, "deep_agents", "0.7.11", now, now),
                )
            await connection.commit()
        return session_id

    async def list_sessions(self) -> list[dict[str, Any]]:
        async with self.connection() as connection:
            rows = await (
                await connection.execute("SELECT * FROM sessions ORDER BY updated_at DESC")
            ).fetchall()
        return [dict(row) for row in rows]

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        async with self.connection() as connection:
            session = await (
                await connection.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,))
            ).fetchone()
            if session is None:
                return None
            messages = await (
                await connection.execute(
                    "SELECT * FROM messages WHERE session_id=? ORDER BY created_at", (session_id,)
                )
            ).fetchall()
            latest_run = await (
                await connection.execute(
                    "SELECT * FROM agent_runs WHERE session_id=? ORDER BY created_at DESC LIMIT 1",
                    (session_id,),
                )
            ).fetchone()
            latest_events = []
            if latest_run is not None:
                latest_events = await (
                    await connection.execute(
                        "SELECT * FROM events WHERE run_id=? ORDER BY event_id",
                        (latest_run["run_id"],),
                    )
                ).fetchall()
        result = dict(session)
        result["messages"] = [dict(row) | {"metadata": json.loads(row["metadata_json"])} for row in messages]
        result["context"] = (
            await self.get_deep_session_context(session_id)
            if result.get("runtime_kind") == "deep_agents"
            else await self.get_session_context(session_id)
        )
        result["latest_run"] = (
            dict(latest_run)
            | {
                "events": [
                    dict(event) | {"payload": json.loads(event["payload_json"])}
                    for event in latest_events
                ]
            }
            if latest_run is not None
            else None
        )
        return result

    async def recent_messages(self, session_id: str, limit: int = 12) -> list[dict[str, Any]]:
        async with self.connection() as connection:
            rows = await (
                await connection.execute(
                    "SELECT * FROM messages WHERE session_id=? ORDER BY created_at DESC LIMIT ?",
                    (session_id, limit),
                )
            ).fetchall()
        return [
            dict(row) | {"metadata": json.loads(row["metadata_json"])}
            for row in reversed(rows)
        ]

    async def get_session_context(self, session_id: str) -> dict[str, Any]:
        async with self.connection() as connection:
            row = await (
                await connection.execute(
                    "SELECT * FROM session_context WHERE session_id=?", (session_id,)
                )
            ).fetchone()
        if row is None:
            return {
                "session_id": session_id,
                "active_funds": [],
                "active_manager": None,
                "active_report_job_id": None,
                "active_report_document_id": None,
                "last_intent": None,
                "last_metric_terms": [],
                "last_years": [],
                "pending_intent": None,
                "pending_request": {},
                "updated_at": None,
            }
        data = dict(row)
        return {
            "session_id": session_id,
            "active_funds": json.loads(data["active_funds_json"]),
            "active_manager": data["active_manager"],
            "active_report_job_id": data["active_report_job_id"],
            "active_report_document_id": data["active_report_document_id"],
            "last_intent": data["last_intent"],
            "last_metric_terms": json.loads(data["last_metric_terms_json"]),
            "last_years": json.loads(data["last_years_json"]),
            "pending_intent": data["pending_intent"],
            "pending_request": json.loads(data["pending_request_json"]),
            "updated_at": data["updated_at"],
        }

    async def get_deep_session_context(self, session_id: str) -> dict[str, Any]:
        """Return only verified entity state consumed by the Deep Agents runtime.

        Legacy routing fields remain in ``session_context`` so historical sessions
        can still be rendered, but the new runtime must not depend on them.
        """
        state = await self.get_session_context(session_id)
        async with self.connection() as connection:
            thread = await (
                await connection.execute(
                    "SELECT pending_action_json FROM agent_threads WHERE session_id=?",
                    (session_id,),
                )
            ).fetchone()
        return {
            "session_id": session_id,
            "active_funds": state["active_funds"],
            "active_manager": state["active_manager"],
            "active_report_job_id": state["active_report_job_id"],
            "active_report_document_id": state["active_report_document_id"],
            "pending_action": (
                json.loads(thread["pending_action_json"])
                if thread is not None and thread["pending_action_json"]
                else None
            ),
            "updated_at": state["updated_at"],
        }

    async def set_agent_pending_action(
        self, session_id: str, action: dict[str, Any] | None
    ) -> None:
        async with self.connection() as connection:
            await connection.execute(
                "UPDATE agent_threads SET pending_action_json=?,updated_at=? WHERE session_id=?",
                (
                    json_text(action) if action is not None else None,
                    utc_now().isoformat(),
                    session_id,
                ),
            )
            await connection.commit()

    async def update_session_context(self, session_id: str, **updates: Any) -> dict[str, Any]:
        state = await self.get_session_context(session_id)
        allowed = {
            "active_funds",
            "active_manager",
            "active_report_job_id",
            "active_report_document_id",
            "last_intent",
            "last_metric_terms",
            "last_years",
            "pending_intent",
            "pending_request",
        }
        unexpected = set(updates) - allowed
        if unexpected:
            raise ValueError(f"未知 SessionContext 字段: {sorted(unexpected)}")
        state.update(updates)
        now = utc_now().isoformat()
        async with self.connection() as connection:
            await connection.execute(
                "INSERT INTO session_context(session_id,active_funds_json,active_manager,"
                "active_report_job_id,active_report_document_id,last_intent,"
                "last_metric_terms_json,last_years_json,pending_intent,pending_request_json,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(session_id) DO UPDATE SET "
                "active_funds_json=excluded.active_funds_json,active_manager=excluded.active_manager,"
                "active_report_job_id=excluded.active_report_job_id,"
                "active_report_document_id=excluded.active_report_document_id,"
                "last_intent=excluded.last_intent,"
                "last_metric_terms_json=excluded.last_metric_terms_json,"
                "last_years_json=excluded.last_years_json,"
                "pending_intent=excluded.pending_intent,"
                "pending_request_json=excluded.pending_request_json,updated_at=excluded.updated_at",
                (
                    session_id,
                    json_text(state["active_funds"]),
                    state["active_manager"],
                    state["active_report_job_id"],
                    state["active_report_document_id"],
                    state["last_intent"],
                    json_text(state["last_metric_terms"]),
                    json_text(state["last_years"]),
                    state["pending_intent"],
                    json_text(state["pending_request"]),
                    now,
                ),
            )
            await connection.commit()
        return await self.get_session_context(session_id)

    async def link_report_job(self, job_id: str, session_id: str, source_run_id: str) -> None:
        async with self.connection() as connection:
            await connection.execute(
                "UPDATE report_jobs SET session_id=COALESCE(session_id,?),source_run_id=COALESCE(source_run_id,?),updated_at=? WHERE job_id=?",
                (session_id, source_run_id, utc_now().isoformat(), job_id),
            )
            await connection.execute("INSERT INTO report_job_sessions(job_id,session_id,source_run_id,created_at) VALUES(?,?,?,?) ON CONFLICT(job_id,session_id) DO UPDATE SET source_run_id=excluded.source_run_id", (job_id, session_id, source_run_id, utc_now().isoformat()))
            await connection.commit()

    async def publish_report_to_sessions(self, job_id: str, document_id: str, fallback_session: str | None = None, *, fund_symbol: str | None = None) -> None:
        async with self.connection() as connection:
            rows = await (await connection.execute("SELECT session_id FROM report_job_sessions WHERE job_id=?", (job_id,))).fetchall()
            sessions = {row["session_id"] for row in rows}
            if fallback_session:
                sessions.add(fallback_session)
            for session in sessions:
                # Compare-and-set: a completed older task cannot replace a newer active report.
                await connection.execute("UPDATE session_context SET active_report_document_id=?,updated_at=? WHERE session_id=? AND active_report_job_id=? AND (? IS NULL OR json_array_length(active_funds_json)=0 OR EXISTS (SELECT 1 FROM json_each(active_funds_json) WHERE value=?))", (document_id, utc_now().isoformat(), session, job_id, fund_symbol, fund_symbol))
            await connection.commit()
        for session in sessions:
            await self.add_event(session, "report_published", {"job_id": job_id, "document_id": document_id})

    async def report_document_for_job(self, job_id: str) -> dict[str, Any] | None:
        async with self.connection() as connection:
            row = await (
                await connection.execute(
                    "SELECT document_id,fund_symbol,fund_name,manager_name,as_of,artifact_id "
                    "FROM knowledge_documents WHERE job_id=? AND is_current=1 "
                    "ORDER BY updated_at DESC LIMIT 1",
                    (job_id,),
                )
            ).fetchone()
        return dict(row) if row is not None else None

    async def recent_completed_report(
        self,
        fund_symbol: str,
        earliest_as_of: date,
        *,
        manager_name: str | None = None,
        manager_tenure_start: date | None = None,
    ) -> dict[str, Any] | None:
        from fund_research_agent.domain.report_data import PIPELINE_VERSION
        where = [
            "d.fund_symbol=?",
            "d.data_as_of>=?",
            "d.pipeline_version=?",
            "d.is_current=1",
            "d.job_id IS NOT NULL",
            "r.status IN ('completed','completed_with_warnings')",
        ]
        parameters: list[Any] = [fund_symbol, earliest_as_of.isoformat(), PIPELINE_VERSION]
        if manager_name:
            where.append("d.manager_name=?")
            parameters.append(manager_name)
        if manager_tenure_start:
            where.append("d.manager_tenure_start=?")
            parameters.append(str(manager_tenure_start))
        async with self.connection() as connection:
            row = await (
                await connection.execute(
                    "SELECT d.document_id,d.fund_symbol,d.fund_name,d.manager_name,d.as_of,"
                    "d.artifact_id,d.job_id,r.trace_id,r.status,r.updated_at "
                    "FROM knowledge_documents d JOIN report_jobs r ON r.job_id=d.job_id "
                    f"WHERE {' AND '.join(where)} "
                    "ORDER BY d.data_as_of DESC,d.updated_at DESC LIMIT 1",
                    parameters,
                )
            ).fetchone()
        return dict(row) if row is not None else None

    async def upsert_service_heartbeat(
        self,
        service_name: str,
        instance_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        now = utc_now().isoformat()
        async with self.connection() as connection:
            await connection.execute(
                "INSERT INTO service_heartbeats(service_name,instance_id,updated_at,metadata_json) "
                "VALUES(?,?,?,?) ON CONFLICT(service_name) DO UPDATE SET "
                "instance_id=excluded.instance_id,updated_at=excluded.updated_at,"
                "metadata_json=excluded.metadata_json",
                (service_name, instance_id, now, json_text(metadata or {})),
            )
            await connection.commit()

    async def get_service_heartbeat(self, service_name: str) -> dict[str, Any] | None:
        async with self.connection() as connection:
            row = await (
                await connection.execute(
                    "SELECT * FROM service_heartbeats WHERE service_name=?", (service_name,)
                )
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["metadata"] = json.loads(result.pop("metadata_json"))
        return result

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        message_id = f"msg_{uuid.uuid4().hex}"
        now = utc_now().isoformat()
        async with self.connection() as connection:
            await connection.execute(
                "INSERT INTO messages(message_id,session_id,role,content,metadata_json,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (message_id, session_id, role, content, json_text(metadata or {}), now),
            )
            await connection.execute(
                "UPDATE sessions SET updated_at=? WHERE session_id=?", (now, session_id)
            )
            await connection.commit()
        return message_id

    async def create_agent_run(self, session_id: str | None) -> str:
        run_id = f"run_{uuid.uuid4().hex}"
        now = utc_now().isoformat()
        connection = await self.connect()
        try:
            await connection.execute("BEGIN IMMEDIATE")
            if session_id is not None:
                session = await (
                    await connection.execute(
                        "SELECT runtime_kind FROM sessions WHERE session_id=?", (session_id,)
                    )
                ).fetchone()
                if session is None:
                    raise ValueError("session_not_found")
                if session["runtime_kind"] != "deep_agents":
                    raise ValueError("legacy_session_readonly")
                active = await (
                    await connection.execute(
                        "SELECT run_id FROM agent_runs WHERE session_id=? "
                        "AND status='running' ORDER BY created_at DESC LIMIT 1",
                        (session_id,),
                    )
                ).fetchone()
                if active is not None:
                    raise ValueError(f"session_run_active:{active['run_id']}")
                # A recoverable run is paused, not executing. A new user
                # message replaces it; retain its history and checkpoint for audit.
                await connection.execute(
                    "UPDATE agent_runs SET status='stopped',"
                    "stop_reason='superseded_by_new_message',updated_at=? "
                    "WHERE session_id=? AND status='recoverable'",
                    (now, session_id),
                )
            await connection.execute(
                "INSERT INTO agent_runs(run_id,session_id,status,created_at,updated_at) VALUES(?,?,?,?,?)",
                (run_id, session_id, "running", now, now),
            )
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()
        await self.add_event(run_id, "run_started", {"session_id": session_id})
        return run_id

    async def update_agent_run(
        self,
        run_id: str,
        *,
        status: str,
        route: dict[str, Any] | None = None,
        stop_reason: str | None = None,
        usage: dict[str, Any] | None = None,
        decision: dict[str, Any] | None = None,
        checkpoint_id: str | None = None,
    ) -> None:
        async with self.connection() as connection:
            await connection.execute(
                "UPDATE agent_runs SET status=?,route_json=COALESCE(?,route_json),"
                "stop_reason=COALESCE(?,stop_reason),usage_json=COALESCE(?,usage_json),"
                "decision_json=COALESCE(?,decision_json),checkpoint_id=COALESCE(?,checkpoint_id),"
                "updated_at=? "
                "WHERE run_id=?",
                (
                    status,
                    json_text(route) if route is not None else None,
                    stop_reason,
                    json_text(usage) if usage is not None else None,
                    json_text(decision) if decision is not None else None,
                    checkpoint_id,
                    utc_now().isoformat(),
                    run_id,
                ),
            )
            await connection.commit()

    async def get_agent_run(self, run_id: str) -> dict[str, Any] | None:
        async with self.connection() as connection:
            row = await (
                await connection.execute("SELECT * FROM agent_runs WHERE run_id=?", (run_id,))
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["route"] = json.loads(result.pop("route_json") or "null")
        result["decision"] = json.loads(result.pop("decision_json") or "null")
        result["usage"] = json.loads(result.pop("usage_json") or "{}")
        return result

    async def recover_interrupted_agent_runs(self) -> list[str]:
        """Mark process-local chat work recoverable after an API restart."""
        now = utc_now().isoformat()
        async with self.connection() as connection:
            rows = await (
                await connection.execute(
                    "SELECT run_id FROM agent_runs WHERE status='running'"
                )
            ).fetchall()
            run_ids = [str(row["run_id"]) for row in rows]
            if run_ids:
                await connection.execute(
                    "UPDATE agent_runs SET status='recoverable',stop_reason='api_restarted',"
                    "updated_at=? WHERE status='running'",
                    (now,),
                )
                await connection.commit()
        for run_id in run_ids:
            await self.add_event(run_id, "run_recoverable", {"reason": "api_restarted"})
        return run_ids

    async def resume_agent_run(self, run_id: str) -> dict[str, Any]:
        connection = await self.connect()
        try:
            await connection.execute("BEGIN IMMEDIATE")
            row = await (
                await connection.execute(
                    "SELECT * FROM agent_runs WHERE run_id=?", (run_id,)
                )
            ).fetchone()
            if row is None:
                raise ValueError("run_not_found")
            if row["status"] != "recoverable":
                raise ValueError(f"run_not_recoverable:{row['status']}")
            active = await (
                await connection.execute(
                    "SELECT run_id FROM agent_runs WHERE session_id=? AND status='running' "
                    "AND run_id<>? LIMIT 1",
                    (row["session_id"], run_id),
                )
            ).fetchone()
            if active is not None:
                raise ValueError(f"session_run_active:{active['run_id']}")
            await connection.execute(
                "UPDATE agent_runs SET status='running',stop_reason=NULL,updated_at=? WHERE run_id=?",
                (utc_now().isoformat(), run_id),
            )
            await connection.commit()
            return dict(row)
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def update_agent_thread_checkpoint(
        self, session_id: str, checkpoint_id: str | None
    ) -> None:
        async with self.connection() as connection:
            await connection.execute(
                "UPDATE agent_threads SET last_checkpoint_id=?,updated_at=? WHERE session_id=?",
                (checkpoint_id, utc_now().isoformat(), session_id),
            )
            await connection.commit()

    async def add_event(self, run_id: str, event_type: str, payload: dict[str, Any]) -> int:
        async with self.connection() as connection:
            cursor = await connection.execute(
                "INSERT INTO events(run_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
                (run_id, event_type, json_text(payload), utc_now().isoformat()),
            )
            await connection.commit()
            return int(cursor.lastrowid)

    async def list_events(self, run_id: str, after_id: int = 0) -> list[dict[str, Any]]:
        async with self.connection() as connection:
            rows = await (
                await connection.execute(
                    "SELECT * FROM events WHERE run_id=? AND event_id>? ORDER BY event_id",
                    (run_id, after_id),
                )
            ).fetchall()
        return [
            dict(row) | {"payload": json.loads(row["payload_json"])}
            for row in rows
        ]

    async def stream_events(
        self, run_id: str, after_id: int = 0, poll_seconds: float = 0.5
    ) -> AsyncIterator[dict[str, Any]]:
        cursor = after_id
        idle = 0
        while idle < 720:
            events = await self.list_events(run_id, cursor)
            if events:
                idle = 0
                for event in events:
                    cursor = int(event["event_id"])
                    yield event
                    if event["event_type"] == "done":
                        return
            else:
                idle += 1
            await asyncio.sleep(poll_seconds)

    async def create_report_job(
        self,
        request: ReportRequest,
        fingerprint: str,
        workflow_version: str,
    ) -> tuple[str, bool]:
        now = utc_now().isoformat()
        async with self.connection() as connection:
            existing = await (
                await connection.execute(
                    "SELECT job_id,status FROM report_jobs WHERE input_fingerprint=? "
                    "AND status NOT IN ('failed','failed_validation','cancelled') "
                    "ORDER BY created_at DESC LIMIT 1",
                    (fingerprint,),
                )
            ).fetchone()
            active_statuses = {
                "queued", "resolving", "checking_coverage", "preparing_json",
                "acquiring_materials", "building_handbook", "reusing_handbook",
                "validating_handbook", "splitting_part_data", "context_preflight",
                "generating_parts", "summarizing", "auditing", "rewriting",
                "rendering", "indexing", "recoverable", "cancelling",
            }
            if existing and (not request.force_refresh or existing["status"] in active_statuses):
                return str(existing["job_id"]), False
            job_id = f"report_{uuid.uuid4().hex}"
            trace_id = f"trace_{uuid.uuid4().hex}"
            await connection.execute(
                "INSERT INTO report_jobs(job_id,trace_id,request_json,status,current_stage,progress,"
                "input_fingerprint,workflow_version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    trace_id,
                    request.model_dump_json(),
                    ReportStatus.QUEUED.value,
                    ReportStatus.QUEUED.value,
                    0,
                    fingerprint,
                    workflow_version,
                    now,
                    now,
                ),
            )
            await connection.commit()
        await self.add_event(job_id, "report_stage_started", {"stage": "queued", "progress": 0})
        return job_id, True

    async def get_report_job(self, job_id: str) -> ReportJob | None:
        async with self.connection() as connection:
            row = await (
                await connection.execute("SELECT * FROM report_jobs WHERE job_id=?", (job_id,))
            ).fetchone()
        if row is None:
            return None
        return self._report_job_from_row(row)

    async def get_report_job_by_trace(self, trace_id: str) -> ReportJob | None:
        async with self.connection() as connection:
            row = await (
                await connection.execute(
                    "SELECT * FROM report_jobs WHERE trace_id=?", (trace_id,)
                )
            ).fetchone()
        return self._report_job_from_row(row) if row is not None else None

    def _report_job_from_row(self, row: aiosqlite.Row) -> ReportJob:
        return ReportJob(
            job_id=row["job_id"],
            trace_id=row["trace_id"],
            request=ReportRequest.model_validate_json(row["request_json"]),
            status=ReportStatus(row["status"]),
            current_stage=row["current_stage"],
            progress=float(row["progress"]),
            warnings=json.loads(row["warnings_json"]),
            errors=json.loads(row["errors_json"]),
            input_fingerprint=row["input_fingerprint"],
            workflow_version=row["workflow_version"],
            attempt=int(row["attempt"]),
            lease_owner=row["lease_owner"],
            lease_expires_at=row["lease_expires_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
            artifact_ids=json.loads(row["artifact_ids_json"]),
            session_id=row["session_id"],
            source_run_id=row["source_run_id"],
        )

    async def update_report_job(
        self,
        job_id: str,
        *,
        status: ReportStatus,
        stage: str | None = None,
        progress: float | None = None,
        warnings: list[str] | None = None,
        errors: list[str] | None = None,
        artifact_ids: list[str] | None = None,
    ) -> None:
        completed_at = utc_now().isoformat() if status.value in {
            "completed", "completed_with_warnings", "failed", "failed_validation", "cancelled"
        } else None
        async with self.connection() as connection:
            await connection.execute(
                "UPDATE report_jobs SET status=?,current_stage=COALESCE(?,current_stage),"
                "progress=COALESCE(?,progress),warnings_json=COALESCE(?,warnings_json),"
                "errors_json=COALESCE(?,errors_json),artifact_ids_json=COALESCE(?,artifact_ids_json),"
                "updated_at=?,completed_at=? WHERE job_id=?",
                (
                    status.value,
                    stage,
                    progress,
                    json_text(warnings) if warnings is not None else None,
                    json_text(errors) if errors is not None else None,
                    json_text(artifact_ids) if artifact_ids is not None else None,
                    utc_now().isoformat(),
                    completed_at,
                    job_id,
                ),
            )
            if status in TERMINAL_REPORT_STATUSES or status == ReportStatus.RECOVERABLE:
                await connection.execute("UPDATE report_jobs SET lease_owner=NULL,lease_expires_at=NULL WHERE job_id=?", (job_id,))
            await connection.commit()

    async def claim_report_job(self, worker_id: str, lease_seconds: int) -> ReportJob | None:
        await self.recover_expired_report_jobs()
        now = utc_now()
        lease = (now + timedelta(seconds=lease_seconds)).isoformat()
        connection = await self.connect()
        try:
            await connection.execute("BEGIN IMMEDIATE")
            row = await (
                await connection.execute(
                    "SELECT * FROM report_jobs WHERE "
                    "(status='queued' OR (status='recoverable' AND errors_json='[]' AND "
                    "(lease_expires_at IS NULL OR lease_expires_at<?))) "
                    "AND (lease_owner IS NULL OR lease_expires_at<?) "
                    "AND cancel_requested=0 ORDER BY created_at LIMIT 1",
                    (now.isoformat(), now.isoformat()),
                )
            ).fetchone()
            if row is None:
                await connection.rollback()
                return None
            await connection.execute(
                "UPDATE report_jobs SET lease_owner=?,lease_expires_at=?,attempt=attempt+1,"
                "updated_at=? WHERE job_id=?",
                (worker_id, lease, now.isoformat(), row["job_id"]),
            )
            await connection.commit()
            updated = await (
                await connection.execute(
                    "SELECT * FROM report_jobs WHERE job_id=?", (row["job_id"],)
                )
            ).fetchone()
            return self._report_job_from_row(updated)
        finally:
            await connection.close()

    async def recover_expired_report_jobs(self, max_attempts: int = 2) -> list[str]:
        """Recover a crashed worker once, then fail clearly instead of hanging forever."""
        now = utc_now()
        terminal = tuple(status.value for status in TERMINAL_REPORT_STATUSES)
        placeholders = ",".join("?" for _ in terminal)
        connection = await self.connect()
        recovered: list[tuple[str, str, str]] = []
        try:
            await connection.execute("BEGIN IMMEDIATE")
            rows = await (
                await connection.execute(
                    "SELECT job_id,status,current_stage,attempt,errors_json FROM report_jobs "
                    "WHERE lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL "
                    f"AND lease_expires_at<? AND status NOT IN ({placeholders})",
                    (now.isoformat(), *terminal),
                )
            ).fetchall()
            for row in rows:
                job_id = str(row["job_id"])
                previous_stage = str(row["current_stage"])
                if row["status"] == ReportStatus.CANCELLING.value:
                    status = ReportStatus.CANCELLED
                    errors = json.loads(row["errors_json"])
                elif int(row["attempt"]) < max_attempts:
                    status = ReportStatus.RECOVERABLE
                    errors = json.loads(row["errors_json"])
                else:
                    status = ReportStatus.FAILED
                    errors = [*json.loads(row["errors_json"]), "worker_lease_expired_after_retry"]
                await connection.execute(
                    "UPDATE report_jobs SET status=?,current_stage=?,lease_owner=NULL,"
                    "lease_expires_at=NULL,errors_json=?,updated_at=?,completed_at=? WHERE job_id=?",
                    (
                        status.value,
                        status.value,
                        json_text(errors),
                        now.isoformat(),
                        now.isoformat() if status in TERMINAL_REPORT_STATUSES else None,
                        job_id,
                    ),
                )
                recovered.append((job_id, status.value, previous_stage))
            await connection.commit()
        finally:
            await connection.close()
        for job_id, status, previous_stage in recovered:
            event_type = (
                "report_recoverable"
                if status == ReportStatus.RECOVERABLE.value
                else "done"
            )
            await self.add_event(
                job_id,
                event_type,
                {
                    "status": status,
                    "reason": "worker_lease_expired",
                    "previous_stage": previous_stage,
                },
            )
        return [job_id for job_id, _status, _stage in recovered]

    async def heartbeat(self, job_id: str, worker_id: str, lease_seconds: int) -> bool:
        expires = (utc_now() + timedelta(seconds=lease_seconds)).isoformat()
        async with self.connection() as connection:
            cursor = await connection.execute(
                "UPDATE report_jobs SET lease_expires_at=?,updated_at=? "
                "WHERE job_id=? AND lease_owner=?",
                (expires, utc_now().isoformat(), job_id, worker_id),
            )
            await connection.commit()
            return cursor.rowcount == 1

    async def request_cancel(self, job_id: str) -> None:
        async with self.connection() as connection:
            immediately_cancellable = (
                "status IN ('queued','recoverable') OR "
                "(status='cancelling' AND lease_owner IS NULL)"
            )
            await connection.execute(
                "UPDATE report_jobs SET cancel_requested=1,"
                f"status=CASE WHEN {immediately_cancellable} THEN 'cancelled' ELSE 'cancelling' END,"
                f"current_stage=CASE WHEN {immediately_cancellable} THEN 'cancelled' ELSE current_stage END,"
                f"completed_at=CASE WHEN {immediately_cancellable} THEN ? ELSE completed_at END,"
                "updated_at=? WHERE job_id=?",
                (utc_now().isoformat(), utc_now().isoformat(), job_id),
            )
            await connection.commit()
        await self.add_event(job_id, "warning", {"message": "用户请求取消任务"})

    async def cancel_requested(self, job_id: str) -> bool:
        async with self.connection() as connection:
            row = await (
                await connection.execute(
                    "SELECT cancel_requested FROM report_jobs WHERE job_id=?", (job_id,)
                )
            ).fetchone()
        return bool(row and row[0])

    async def resume_report_job(self, job_id: str) -> None:
        async with self.connection() as connection:
            await connection.execute(
                "UPDATE report_jobs SET status='recoverable',cancel_requested=0,lease_owner=NULL,"
                "lease_expires_at=NULL,completed_at=NULL,errors_json='[]',updated_at=? WHERE job_id=?",
                (utc_now().isoformat(), job_id),
            )
            await connection.commit()
        await self.add_event(job_id, "report_recoverable", {"reason": "manual_resume"})

    async def upsert_workflow_step(
        self,
        job_id: str,
        step_id: str,
        status: str,
        *,
        input_hash: str | None = None,
        output_artifact_id: str | None = None,
        error: str | None = None,
    ) -> None:
        now = utc_now().isoformat()
        async with self.connection() as connection:
            await connection.execute(
                "INSERT INTO workflow_steps(job_id,step_id,status,input_hash,output_artifact_id,"
                "attempt,error,started_at,finished_at) VALUES(?,?,?,?,?,1,?,?,?) "
                "ON CONFLICT(job_id,step_id) DO UPDATE SET status=excluded.status,"
                "input_hash=COALESCE(excluded.input_hash,workflow_steps.input_hash),"
                "output_artifact_id=COALESCE(excluded.output_artifact_id,workflow_steps.output_artifact_id),"
                "attempt=workflow_steps.attempt+1,error=excluded.error,"
                "started_at=COALESCE(workflow_steps.started_at,excluded.started_at),"
                "finished_at=excluded.finished_at",
                (
                    job_id,
                    step_id,
                    status,
                    input_hash,
                    output_artifact_id,
                    error,
                    now if status == "running" else None,
                    now if status in {"completed", "failed", "skipped"} else None,
                ),
            )
            await connection.commit()

    async def completed_step(self, job_id: str, step_id: str, input_hash: str) -> bool:
        async with self.connection() as connection:
            row = await (
                await connection.execute(
                    "SELECT 1 FROM workflow_steps WHERE job_id=? AND step_id=? "
                    "AND status='completed' AND input_hash=?",
                    (job_id, step_id, input_hash),
                )
            ).fetchone()
        return row is not None

    async def add_memory(self, record: MemoryRecord) -> None:
        async with self.connection() as connection:
            await connection.execute(
                "INSERT INTO memories(memory_id,user_id,scope,kind,content,source_message_id,confidence,"
                "confirmed,created_at,updated_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.memory_id,
                    record.user_id,
                    record.scope,
                    record.kind,
                    record.content,
                    record.source_message_id,
                    record.confidence,
                    int(record.confirmed),
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                    record.expires_at.isoformat() if record.expires_at else None,
                ),
            )
            await connection.commit()

    async def list_memories(self, user_id: str = "local") -> list[MemoryRecord]:
        async with self.connection() as connection:
            rows = await (
                await connection.execute(
                    "SELECT * FROM memories WHERE user_id=? AND "
                    "(expires_at IS NULL OR expires_at>?) ORDER BY updated_at DESC",
                    (user_id, utc_now().isoformat()),
                )
            ).fetchall()
        records: list[MemoryRecord] = []
        for row in rows:
            data = dict(row)
            data["confirmed"] = bool(data["confirmed"])
            records.append(MemoryRecord(**data))
        return records

    async def delete_memory(self, memory_id: str) -> bool:
        async with self.connection() as connection:
            cursor = await connection.execute("DELETE FROM memories WHERE memory_id=?", (memory_id,))
            await connection.commit()
            return cursor.rowcount == 1

    async def list_schedules(self) -> list[dict[str, Any]]:
        async with self.connection() as connection:
            rows = await (
                await connection.execute("SELECT * FROM schedules ORDER BY created_at DESC")
            ).fetchall()
        return [dict(row) | {"payload": json.loads(row["payload_json"])} for row in rows]

    async def create_schedule(
        self,
        *,
        name: str,
        task_type: str,
        cron_expression: str,
        payload: dict[str, Any],
        timezone: str = "Asia/Shanghai",
        enabled: bool = False,
    ) -> dict[str, Any]:
        allowed = {
            "check_quarterly_reports",
            "refresh_public_materials",
            "generate_research_draft",
            "run_evaluation",
        }
        if task_type not in allowed:
            raise ValueError(f"unsupported schedule task type: {task_type}")
        schedule_id = f"schedule_{uuid.uuid4().hex}"
        now = utc_now().isoformat()
        async with self.connection() as connection:
            await connection.execute(
                "INSERT INTO schedules(schedule_id,name,task_type,cron_expression,timezone,"
                "payload_json,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    schedule_id,
                    name,
                    task_type,
                    cron_expression,
                    timezone,
                    json_text(payload),
                    int(enabled),
                    now,
                    now,
                ),
            )
            await connection.commit()
        return {
            "schedule_id": schedule_id,
            "name": name,
            "task_type": task_type,
            "cron_expression": cron_expression,
            "timezone": timezone,
            "payload": payload,
            "enabled": enabled,
        }

    async def delete_schedule(self, schedule_id: str) -> bool:
        async with self.connection() as connection:
            cursor = await connection.execute(
                "DELETE FROM schedules WHERE schedule_id=?", (schedule_id,)
            )
            await connection.commit()
            return cursor.rowcount == 1

    async def update_schedule_clock(
        self,
        schedule_id: str,
        *,
        next_run_at: datetime,
        last_run_at: datetime | None = None,
    ) -> None:
        async with self.connection() as connection:
            await connection.execute(
                "UPDATE schedules SET next_run_at=?,last_run_at=COALESCE(?,last_run_at),"
                "updated_at=? WHERE schedule_id=?",
                (
                    next_run_at.isoformat(),
                    last_run_at.isoformat() if last_run_at else None,
                    utc_now().isoformat(),
                    schedule_id,
                ),
            )
            await connection.commit()

    def readonly_sync_connection(self) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
