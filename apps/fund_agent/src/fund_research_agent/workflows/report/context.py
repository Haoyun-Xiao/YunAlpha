from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fund_research_agent.paths import WriteBoundary


@dataclass(frozen=True)
class ReportRunContext:
    job_id: str
    root: Path
    inputs: Path
    json_dir: Path
    part_data_dir: Path
    materials_dir: Path
    handbook_dir: Path
    parts_dir: Path
    audit_dir: Path
    output_dir: Path
    checkpoints_dir: Path

    @classmethod
    def create(cls, var_root: Path, job_id: str, boundary: WriteBoundary) -> ReportRunContext:
        root = boundary.ensure_dir(var_root / "jobs" / job_id)
        values = {
            "inputs": root / "inputs",
            "json_dir": root / "inputs/json",
            "part_data_dir": root / "inputs/part-json",
            "materials_dir": root / "inputs/materials",
            "handbook_dir": root / "handbook",
            "parts_dir": root / "parts",
            "audit_dir": root / "audit",
            "output_dir": root / "output",
            "checkpoints_dir": root / "checkpoints",
        }
        for path in values.values():
            boundary.ensure_dir(path)
        return cls(job_id=job_id, root=root, **values)
