from __future__ import annotations

import asyncio
import logging

from .service import IndustryService
from .workflow import ResearchWorkflow

logger = logging.getLogger(__name__)


class IndustryWorker:
    """Poll the separate industry queue inside YunAlpha's existing worker."""

    def __init__(self, service: IndustryService, owner: str):
        self.settings = service.settings
        self.store = service.store
        self.owner = owner

    async def run_once(self) -> bool:
        job = self.store.claim(self.owner, self.settings.max_jobs)
        if job is None:
            return False

        jid = job["job_id"]

        async def execute():
            await ResearchWorkflow(self.settings, self.store).run(jid)

        task = asyncio.create_task(execute())

        def interrupted(current, error=None):
            if current["status"] != "running":
                return {}
            cancelled = current["cancel_requested"]
            return {
                "status": "cancelled" if cancelled else "recoverable",
                "stage": "cancelled" if cancelled else current["stage"],
                "live": {},
                "error": None if cancelled else error,
            }

        try:
            while not task.done():
                await asyncio.wait([task], timeout=5)
                if not task.done() and (
                    self.store.job(jid)["cancel_requested"]
                    or not self.store.heartbeat(jid, self.owner)
                ):
                    task.cancel()
            await task
        except asyncio.CancelledError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self.store.transition_job(jid, interrupted, owner=self.owner)
            # A user cancellation cancels the child, whereas shutdown cancels the poll task.
            if asyncio.current_task().cancelling():
                raise
        except Exception as exc:
            error = f"{type(exc).__name__}: {str(exc)[:1200]}"
            self.store.transition_job(
                jid,
                lambda current: interrupted(current, error),
                owner=self.owner,
            )
        finally:
            self.store.release(jid, self.owner)
        return True

    async def serve(self) -> None:
        while True:
            try:
                if await self.run_once():
                    continue
            except Exception:
                logger.exception("产业链任务轮询失败")
            await asyncio.sleep(1)
