from __future__ import annotations

import asyncio
import os
import socket
import uuid

from fund_research_agent.bootstrap import build_container
from industry_agent.worker import IndustryWorker


class ReportWorker:
    def __init__(self, worker_id: str | None = None) -> None:
        self.container = build_container()
        self.worker_id = worker_id or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"

    async def run_once(self) -> bool:
        settings = self.container.settings
        job = await self.container.database.claim_report_job(
            self.worker_id, settings.runtime.worker_lease_seconds
        )
        if job is None:
            return False
        await self._run_claimed(job)
        return True

    async def _run_claimed(self, job) -> None:
        heartbeat = asyncio.create_task(self._heartbeat(job.job_id))
        try:
            await self.container.report_workflow.run(job.job_id)
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def _heartbeat(self, job_id: str) -> None:
        settings = self.container.settings
        while True:
            await asyncio.sleep(settings.runtime.worker_heartbeat_seconds)
            ok = await self.container.database.heartbeat(
                job_id, self.worker_id, settings.runtime.worker_lease_seconds
            )
            if not ok:
                return

    async def serve(self) -> None:
        await self.container.database.migrate(self.container.settings.project_root / "sql")
        service_heartbeat = asyncio.create_task(self._service_heartbeat())
        industry = IndustryWorker(self.container.industry, self.worker_id)
        industry_poll = asyncio.create_task(industry.serve(), name="industry-worker")
        active: set[asyncio.Task] = set()
        try:
            while True:
                limit = self.container.settings.runtime.max_report_jobs
                # A non-positive value means no application-side job cap.  The
                # provider, machine resources and SQLite writer remain the
                # practical backpressure points.
                while limit <= 0 or len(active) < limit:
                    job = await self.container.database.claim_report_job(
                        self.worker_id,
                        self.container.settings.runtime.worker_lease_seconds,
                    )
                    if job is None:
                        break
                    active.add(asyncio.create_task(self._run_claimed(job)))
                if active:
                    done, _pending = await asyncio.wait(
                        active,
                        timeout=0.5,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in done:
                        active.discard(task)
                        # ReportWorkflow records expected failures in the Job;
                        # retrieving the exception prevents an unhandled-task
                        # warning if an unexpected exception escapes it.
                        if not task.cancelled():
                            task.exception()
                else:
                    await asyncio.sleep(1)
        finally:
            for task in active:
                task.cancel()
            await asyncio.gather(*active, return_exceptions=True)
            service_heartbeat.cancel()
            industry_poll.cancel()
            await asyncio.gather(service_heartbeat, industry_poll, return_exceptions=True)

    async def _service_heartbeat(self) -> None:
        while True:
            await self.container.database.upsert_service_heartbeat(
                "report-worker",
                self.worker_id,
                {"pid": os.getpid(), "hostname": socket.gethostname()},
            )
            await asyncio.sleep(self.container.settings.runtime.worker_heartbeat_seconds)


def main() -> None:
    try:
        asyncio.run(ReportWorker().serve())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
