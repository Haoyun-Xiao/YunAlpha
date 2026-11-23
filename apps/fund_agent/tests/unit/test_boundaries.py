from pathlib import Path

import pytest

from fund_research_agent.domain.errors import WriteBoundaryViolation
from fund_research_agent.paths import WriteBoundary


def test_write_boundary_allows_only_var(tmp_path: Path):
    project = tmp_path / "new"
    legacy = tmp_path / "old"
    (project / "var").mkdir(parents=True)
    legacy.mkdir()
    boundary = WriteBoundary(project, legacy)
    assert boundary.write_text(project / "var/ok.txt", "ok").read_text() == "ok"
    with pytest.raises(WriteBoundaryViolation):
        boundary.write_text(project / "config/no.txt", "no")
    with pytest.raises(WriteBoundaryViolation):
        boundary.write_text(legacy / "no.txt", "no")


def test_write_boundary_resolves_symlink_escape(tmp_path: Path):
    project = tmp_path / "new"
    legacy = tmp_path / "old"
    (project / "var").mkdir(parents=True)
    legacy.mkdir()
    (project / "var/escape").symlink_to(legacy, target_is_directory=True)
    boundary = WriteBoundary(project, legacy)
    with pytest.raises(WriteBoundaryViolation):
        boundary.write_text(project / "var/escape/no.txt", "no")
