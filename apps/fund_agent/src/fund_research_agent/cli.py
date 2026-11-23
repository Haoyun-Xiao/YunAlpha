from __future__ import annotations

import asyncio
import json
import subprocess
import sys

from fund_research_agent.bootstrap import build_container


async def _migrate() -> None:
    container = build_container()
    await container.database.migrate(container.settings.project_root / "sql")
    print(container.settings.state_db)


def migrate_main() -> None:
    asyncio.run(_migrate())


def doctor_main() -> None:
    container = build_container()
    script = container.settings.project_root / "scripts/dependency_doctor.py"
    result = subprocess.run([sys.executable, str(script)], check=False)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    print(json.dumps({"commands": ["fund-agent-migrate", "fund-agent-doctor"]}))
