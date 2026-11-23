from __future__ import annotations

import asyncio
import json
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import deepagents
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from fund_research_agent.bootstrap import Container, build_container
from fund_research_agent.config import load_settings
from fund_research_agent.domain.contracts import ReportRequest
from fund_research_agent.skills.catalog import mounted_skills
from industry_agent.contracts import JobRequest
from industry_agent.service import ReportFormat


def _inject_before_last_closing_tag(content: str, tag: str, fragment: str) -> str:
    """Avoid matching HTML-looking strings embedded inside inline JavaScript."""
    marker = f"</{tag}>"
    index = content.lower().rfind(marker)
    if index < 0:
        return content + fragment
    return content[:index] + fragment + content[index:]


class SessionCreate(BaseModel):
    title: str = "新研究会话"


class ChatRunCreate(BaseModel):
    session_id: str
    message: str = Field(min_length=1, max_length=20_000)


class MemoryCreate(BaseModel):
    kind: str
    content: str
    scope: str = "global"


class ScheduleCreate(BaseModel):
    name: str
    task_type: str
    cron_expression: str
    payload: dict[str, Any] = Field(default_factory=dict)
    timezone: str = "Asia/Shanghai"
    enabled: bool = False


def _container(request: Request) -> Container:
    return request.app.state.container


def _track(app: FastAPI, coroutine) -> None:
    task = asyncio.create_task(coroutine)
    app.state.background_tasks.add(task)
    task.add_done_callback(app.state.background_tasks.discard)


def create_app(container: Container | None = None) -> FastAPI:
    settings = container.settings if container else load_settings()
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.container = container or build_container()
        app.state.background_tasks = set()
        await app.state.container.database.migrate(app.state.container.settings.project_root / "sql")
        await app.state.container.runtime.start()
        await app.state.container.database.recover_interrupted_agent_runs()
        yield
        pending = tuple(app.state.background_tasks)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await app.state.container.runtime.stop()

    app = FastAPI(
        title="YunAlpha 研究 Agent",
        version="0.1.0",
        description="只读研究 Agent、可恢复报告工作流与证据 Trace。",
        lifespan=lifespan,
        root_path=settings.base_path,
    )

    @app.post("/api/v1/sessions")
    async def create_session(body: SessionCreate, request: Request):
        database = _container(request).database
        session_id = await database.create_session(body.title)
        return await database.get_session(session_id)

    @app.get("/api/v1/sessions")
    async def list_sessions(request: Request):
        return await _container(request).database.list_sessions()

    @app.get("/api/v1/sessions/{session_id}")
    async def get_session(session_id: str, request: Request):
        result = await _container(request).database.get_session(session_id)
        if result is None:
            raise HTTPException(404, "session not found")
        return result

    @app.post("/api/v1/chat/runs", status_code=202)
    async def create_chat_run(body: ChatRunCreate, request: Request):
        container_value = _container(request)
        if await container_value.database.get_session(body.session_id) is None:
            raise HTTPException(404, "session not found")
        try:
            run_id = await container_value.database.create_agent_run(body.session_id)
        except ValueError as exc:
            message = str(exc)
            if message == "legacy_session_readonly":
                raise HTTPException(409, "历史会话只读，请新建研究会话") from exc
            if message.startswith("session_run_active:"):
                raise HTTPException(409, message) from exc
            raise HTTPException(422, message) from exc
        _track(
            request.app,
            container_value.runtime.run(body.session_id, body.message, run_id=run_id),
        )
        return {"status": "accepted", "run_id": run_id}

    @app.post("/api/v1/runs/{run_id}/resume", status_code=202)
    async def resume_chat_run(run_id: str, request: Request):
        container_value = _container(request)
        run = await container_value.database.get_agent_run(run_id)
        if run is None:
            raise HTTPException(404, "run not found")
        if run["status"] != "recoverable":
            raise HTTPException(409, f"run is not recoverable: {run['status']}")
        _track(request.app, container_value.runtime.resume(run_id))
        return {"status": "accepted", "run_id": run_id}

    @app.get("/api/v1/runs/{run_id}")
    async def get_run(run_id: str, request: Request):
        result = await _container(request).database.get_agent_run(run_id)
        if result is None:
            raise HTTPException(404, "run not found")
        return result

    @app.get("/api/v1/runs/{run_id}/events")
    async def stream_run_events(
        run_id: str,
        request: Request,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ):
        return _event_stream(_container(request), run_id, last_event_id)

    @app.get("/api/v1/skills")
    def skills(request: Request):
        c = _container(request)
        return {"items": mounted_skills(c.industry.settings.skill_root)}

    @app.post("/api/v1/industry-jobs", status_code=202)
    async def create_industry_job(body: JobRequest, request: Request):
        c = _container(request)
        if body.session_id and await c.database.get_session(body.session_id) is None:
            raise HTTPException(404, "session not found")
        try:
            return await asyncio.to_thread(
                c.industry.create, body.anchor, body.as_of, body.session_id,
                idempotency_key=body.idempotency_key,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/v1/industry-jobs/{job_id}")
    def get_industry_job(job_id: str, request: Request):
        service = _container(request).industry
        return service.status(industry_job(service, job_id))

    @app.post("/api/v1/industry-jobs/{job_id}/resume")
    def resume_industry_job(job_id: str, request: Request):
        service = _container(request).industry
        try:
            return service.resume(industry_job(service, job_id))
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/v1/industry-jobs/{job_id}/cancel")
    def cancel_industry_job(job_id: str, request: Request):
        service = _container(request).industry
        return service.cancel(industry_job(service, job_id))

    @app.get("/api/v1/industry-jobs/{job_id}/view")
    def view_industry_report(job_id: str, request: Request):
        return industry_file(request, job_id, "html", download=False)

    @app.get("/api/v1/industry-jobs/{job_id}/download")
    def download_industry_report(job_id: str, request: Request, format: ReportFormat = "md"):
        return industry_file(request, job_id, format, download=True)

    def industry_job(service, job_id):
        try:
            return service.job(job_id)
        except KeyError as exc:
            raise HTTPException(404, "industry job not found") from exc

    def industry_file(request, job_id, format, *, download):
        service = _container(request).industry
        job = industry_job(service, job_id)
        try:
            path = service.artifact(job, format)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, "industry report artifact not found") from exc
        media_type = {"html": "text/html", "md": "text/markdown", "json": "application/json"}[format]
        return FileResponse(
            path, media_type=media_type,
            filename=f"{job['report_id']}.{format}" if download else None,
        )

    @app.post("/api/v1/report-jobs", status_code=202)
    async def create_report_job(body: ReportRequest, request: Request):
        return await _container(request).reports.create(body)

    @app.get("/api/v1/report-jobs/{job_id}")
    async def get_report_job(job_id: str, request: Request):
        result = await _container(request).reports.get(job_id)
        if result is None:
            raise HTTPException(404, "report job not found")
        return result

    @app.get("/api/v1/report-jobs/{job_id}/events")
    async def stream_report_events(
        job_id: str,
        request: Request,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ):
        return _event_stream(_container(request), job_id, last_event_id)

    @app.post("/api/v1/report-jobs/{job_id}/cancel")
    async def cancel_report_job(job_id: str, request: Request):
        await _container(request).reports.cancel(job_id)
        return {"job_id": job_id, "status": "cancelling"}

    @app.post("/api/v1/report-jobs/{job_id}/resume")
    async def resume_report_job(job_id: str, request: Request):
        await _container(request).reports.resume(job_id)
        return {"job_id": job_id, "status": "recoverable"}

    @app.get("/api/v1/report-jobs/{job_id}/artifacts")
    async def report_artifacts(job_id: str, request: Request):
        return await _container(request).reports.result(job_id)

    @app.get("/api/v1/report-jobs/{job_id}/view")
    async def view_report(job_id: str, request: Request):
        container_value = _container(request)
        artifacts = await container_value.artifacts.list_for_job(job_id)
        html = next(
            (item for item in reversed(artifacts) if item.kind == "report_html"), None
        )
        if html is None:
            raise HTTPException(404, "html report not found")
        path = container_value.boundary.assert_writable(html.path)
        content = path.read_text(encoding="utf-8")
        docx = next((item for item in reversed(artifacts) if item.kind == "report_docx"), None)
        if docx is not None:
            download_url = request.url_for("get_artifact", artifact_id=docx.artifact_id).path
            content = re.sub(
                r'(<a class="export-word-link" href=")[^"]*(")',
                lambda match: match.group(1) + download_url + match.group(2),
                content,
                count=1,
            )
        cleanup = (
            '<style id="fund-agent-view-cleanup">'
            ".ai-assistant-tab,.ai-assistant-drawer{display:none!important}"
            "</style>"
        )
        content = _inject_before_last_closing_tag(content, "head", cleanup)
        cleanup_script = (
            '<script id="fund-agent-view-cleanup-script">'
            "window.addEventListener('load',()=>{"
            "document.querySelectorAll('.ai-assistant-tab,.ai-assistant-drawer')"
            ".forEach(node=>node.remove())});"
            "</script>"
        )
        content = _inject_before_last_closing_tag(content, "body", cleanup_script)
        return HTMLResponse(
            content,
            headers={"Content-Disposition": f'inline; filename="{path.name}"'},
        )

    @app.get("/api/v1/artifacts/{artifact_id}")
    async def get_artifact(artifact_id: str, request: Request):
        container_value = _container(request)
        record = await container_value.artifacts.get(artifact_id)
        if record is None:
            raise HTTPException(404, "artifact not found")
        path = container_value.boundary.assert_writable(record.path)
        return FileResponse(path, media_type=record.mime_type, filename=path.name)

    @app.get("/api/v1/memories")
    async def list_memories(request: Request):
        return await _container(request).memory.list()

    @app.post("/api/v1/memories")
    async def create_memory(body: MemoryCreate, request: Request):
        try:
            return await _container(request).memory.remember(body.kind, body.content, body.scope)
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.delete("/api/v1/memories/{memory_id}")
    async def delete_memory(memory_id: str, request: Request):
        return {"deleted": await _container(request).memory.forget(memory_id)}

    @app.get("/api/v1/traces/{trace_id}")
    async def get_trace(trace_id: str, request: Request):
        database = _container(request).database
        run = await database.get_agent_run(trace_id)
        report = await database.get_report_job_by_trace(trace_id)
        event_owner = report.job_id if report else trace_id
        return {
            "trace_id": trace_id,
            "run": run,
            "report_job": report,
            "events": await database.list_events(event_owner),
        }

    @app.get("/api/v1/dependencies/health")
    async def dependency_health(request: Request):
        container_value = _container(request)
        worker_heartbeat = await container_value.database.get_service_heartbeat("report-worker")
        worker_age_seconds: float | None = None
        if worker_heartbeat:
            updated = datetime.fromisoformat(worker_heartbeat["updated_at"])
            worker_age_seconds = max(0.0, (datetime.now(UTC) - updated).total_seconds())
        worker_fresh = worker_age_seconds is not None and worker_age_seconds <= 30
        checks: dict[str, Any] = {
            "state_db": container_value.settings.state_db.exists(),
            "lake_bridge": container_value.lake.database.exists(),
            "legacy_kb": container_value.settings.legacy_kb.exists(),
            "legacy_read_only": True,
            "model_key": bool(container_value.model.api_key),
            "deep_agents": {
                "version": getattr(deepagents, "__version__", "unknown"),
                "checkpoint_db": container_value.settings.checkpoint_db.exists(),
                "runtime": "deep_agents",
                "model_tool_calling_configured": bool(container_value.model.api_key),
            },
            "tavily_key": container_value.materials.key_pool.available_count > 0,
            "tavily_key_pool": {
                "configured": container_value.materials.key_pool.configured_count,
                "available": container_value.materials.key_pool.available_count,
                "current_fingerprint": container_value.materials.key_pool.current_fingerprint,
            },
            "trading_enabled": container_value.settings.trading_tools_enabled,
            "report_worker": {
                "fresh": worker_fresh,
                "age_seconds": worker_age_seconds,
                "instance_id": worker_heartbeat["instance_id"] if worker_heartbeat else None,
            },
        }
        checks["healthy"] = all(checks[key] for key in ("state_db", "lake_bridge"))
        checks["ready_for_reports"] = checks["healthy"] and worker_fresh
        return checks

    @app.get("/api/v1/schedules")
    async def list_schedules(request: Request):
        return await _container(request).database.list_schedules()

    @app.post("/api/v1/schedules")
    async def create_schedule(body: ScheduleCreate, request: Request):
        try:
            return await _container(request).database.create_schedule(**body.model_dump())
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.delete("/api/v1/schedules/{schedule_id}")
    async def delete_schedule(schedule_id: str, request: Request):
        return {"deleted": await _container(request).database.delete_schedule(schedule_id)}

    web_root = Path(__file__).resolve().parents[3] / "web"
    app.mount("/", StaticFiles(directory=web_root, html=True), name="web")
    return app


def _event_stream(container: Container, run_id: str, last_event_id: str | None):
    try:
        after_id = int(last_event_id or 0)
    except ValueError:
        after_id = 0

    async def events():
        async for event in container.database.stream_events(run_id, after_id):
            payload = json.dumps(event["payload"], ensure_ascii=False, default=str)
            yield f"id: {event['event_id']}\nevent: {event['event_type']}\ndata: {payload}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def main() -> None:
    container = build_container()
    uvicorn.run(
        create_app(container),
        host=container.settings.host,
        port=container.settings.port,
    )


if __name__ == "__main__":
    main()
