from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from croniter import croniter

from fund_research_agent.bootstrap import build_container
from fund_research_agent.domain.contracts import ReportRequest


class ScheduleRunner:
    def __init__(self) -> None:
        self.container = build_container()

    async def tick(self) -> int:
        ran = 0
        for schedule in await self.container.database.list_schedules():
            if not schedule["enabled"]:
                continue
            now = datetime.now(ZoneInfo(schedule["timezone"]))
            next_run = (
                datetime.fromisoformat(schedule["next_run_at"])
                if schedule["next_run_at"]
                else None
            )
            if next_run is None:
                upcoming = croniter(schedule["cron_expression"], now).get_next(datetime)
                await self.container.database.update_schedule_clock(
                    schedule["schedule_id"], next_run_at=upcoming
                )
                continue
            if next_run > now:
                continue
            await self._execute(schedule)
            upcoming = croniter(schedule["cron_expression"], now).get_next(datetime)
            await self.container.database.update_schedule_clock(
                schedule["schedule_id"], next_run_at=upcoming, last_run_at=now
            )
            ran += 1
        return ran

    async def _execute(self, schedule: dict) -> None:
        payload = schedule["payload"]
        if schedule["task_type"] == "generate_research_draft":
            request = ReportRequest.model_validate(payload)
            await self.container.reports.create(request)
        else:
            # Other schedule types are intentionally local maintenance signals. A dedicated
            # handler can consume the event; no launchd/crontab or legacy task is invoked.
            await self.container.database.add_event(
                f"schedule:{schedule['schedule_id']}",
                "schedule_triggered",
                {"task_type": schedule["task_type"], "payload": payload},
            )

    async def serve(self) -> None:
        await self.container.database.migrate(self.container.settings.project_root / "sql")
        while True:
            await self.tick()
            await asyncio.sleep(30)


def main() -> None:
    asyncio.run(ScheduleRunner().serve())


if __name__ == "__main__":
    main()
