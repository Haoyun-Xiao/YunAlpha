from pathlib import Path

import pytest

from fund_research_agent.domain.errors import PolicyDenied
from fund_research_agent.memory.service import MemoryService
from fund_research_agent.paths import WriteBoundary
from fund_research_agent.persistence.database import Database


@pytest.fixture
async def service(tmp_path: Path):
    project = tmp_path / "project"
    legacy = tmp_path / "legacy"
    (project / "var").mkdir(parents=True)
    legacy.mkdir()
    database = Database(project / "var/agent.sqlite", WriteBoundary(project, legacy))
    await database.migrate(Path(__file__).resolve().parents[2] / "sql")
    return MemoryService(database)


async def test_memory_requires_safe_long_lived_content(service):
    record = await service.remember("preference", "报告中优先展示最大回撤")
    assert (await service.list())[0].memory_id == record.memory_id
    with pytest.raises(PolicyDenied):
        await service.remember("project_fact", "当前净值是 1.52")
