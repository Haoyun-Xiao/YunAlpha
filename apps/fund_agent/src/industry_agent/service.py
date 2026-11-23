from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from .config import Settings
from .store import Store

FINAL = frozenset({"completed", "completed_with_warnings"})
RESTARTABLE = frozenset({"recoverable", "failed", "cancelled"})
FORMATS = {"html": "HTML", "md": "Markdown", "json": "JSON"}
ReportFormat = Literal["html", "md", "json"]


class IndustryService:
    def __init__(self, settings: Settings, base_path: str, timezone: str):
        self.settings = settings
        self.store = Store(settings)
        self.base_path = base_path
        self.timezone = timezone

    def create(
        self,
        anchor: str,
        as_of: date | None,
        session_id: str | None,
        *,
        run_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        anchor = anchor.strip()
        if not anchor:
            raise ValueError("请提供产业链研究主题")
        day = as_of or datetime.now(ZoneInfo(self.timezone)).date()
        key = (
            f"run:{run_id}"
            if run_id
            else (f"api:{session_id or ''}:{idempotency_key}" if idempotency_key else None)
        )
        return self.status(
            self.store.create_job(
                anchor,
                session_id,
                day.isoformat(),
                key,
                run_id=run_id,
            )
        )

    def active_job(self, session_id: str) -> dict | None:
        job = self.store.latest_job(session_id)
        return self.status(job) if job else None

    def job(self, job_id: str, *, session_id: str | None = None) -> dict:
        job = self.store.job(job_id)
        if session_id is not None and job["session_id"] != session_id:
            raise KeyError("当前会话没有该产业链任务")
        return job

    def status(self, job: dict) -> dict:
        url = f"{self.base_path}/api/v1/industry-jobs/{job['job_id']}"
        ready = job["status"] in FINAL and bool(job["report_id"])
        stage = job["stage"]
        if stage == "sources":
            stage = "prepare"
        elif stage.startswith("materials_") or stage in {"company_discovery", "candidate_mapping"}:
            stage = "snapshot"
        return {
            **{
                k: job[k]
                for k in (
                    "job_id",
                    "session_id",
                    "run_id",
                    "status",
                    "progress",
                    "anchor",
                    "as_of",
                    "report_id",
                    "error",
                    "warnings",
                    "cancel_requested",
                )
            },
            "stage": stage,
            "title": f"{job['anchor']}产业链研究",
            "view_url": f"{url}/view",
            "artifacts": [
                {"label": label, "url": f"{url}/download?format={ext}"}
                for ext, label in FORMATS.items()
            ]
            if ready
            else [],
        }

    def cancel(self, job: dict) -> dict:
        jid = job["job_id"]

        def transition(current):
            status = current["status"]
            if status in FINAL or status == "cancelled":
                return {}
            changes = {"cancel_requested": True}
            if status != "running":
                changes.update(status="cancelled", stage="cancelled", live={})
            return changes

        return self.status(self.store.transition_job(jid, transition))

    def resume(self, job: dict) -> dict:
        def transition(current):
            if current["status"] not in RESTARTABLE:
                raise ValueError("仅可恢复中断、失败或已取消的任务")
            return {"status": "queued", "stage": "queued", "cancel_requested": False, "error": None}

        return self.status(self.store.transition_job(job["job_id"], transition))

    def artifact(self, job: dict, format: ReportFormat) -> Path:
        if job["status"] not in FINAL or not job["report_id"]:
            raise ValueError("报告还未生成")
        path = self.store.path(f"jobs/{job['job_id']}/output/report.{format}")
        if not path.is_file():
            raise KeyError("报告产物不存在")
        return path

    def result(self, job: dict, query: str | None, offset: int = 0) -> dict:
        status = self.status(job)
        if job["status"] not in FINAL:
            return status | {"message": "报告还未生成。按任务状态查询，勿在当前轮轮询。"}
        report = self.store.report(job["report_id"])
        info = status | {"report_title": report["title"], "headline": report.get("headline")}
        if query and query.strip():
            return info | {"matches": self.store.search(query, job["report_id"])}
        text = self.artifact(job, "md").read_text(encoding="utf-8")
        end = min(offset + 7000, len(text))
        return info | {
            "markdown": text[offset:end],
            "offset": offset,
            "next_offset": end if end < len(text) else None,
        }
