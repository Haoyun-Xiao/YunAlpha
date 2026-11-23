from __future__ import annotations

import json
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

from .calculations import calculate_holdings
from .lake import LakeRepository
from .model import JSON_TYPES, load_manifest
from .render import render_all


def _git_revision(repo_root: Path) -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_root, text=True, capture_output=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else None


def generate(
    *,
    database: str | Path,
    manifest: str | Path,
    output: str | Path,
    as_of: date,
    repo_root: str | Path,
) -> dict[str, object]:
    specs = load_manifest(manifest)
    if len(specs) != 52:
        raise ValueError(f"enabled manifest must contain exactly 52 funds, found {len(specs)}")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    repo = LakeRepository(database)
    failures: list[dict[str, str]] = []
    generated = 0
    coverage: list[dict[str, object]] = []
    try:
        for spec in specs:
            try:
                holdings = calculate_holdings(repo, spec, as_of)
                if holdings.empty:
                    raise ValueError("no holdings inside research window")
                documents = render_all(spec, repo, holdings, as_of)
                if set(documents) != set(JSON_TYPES):
                    raise AssertionError("renderer did not return all 12 document types")
                for kind, text in documents.items():
                    (output / f"{spec.agent_name}_{kind}.txt").write_text(text, encoding="utf-8")
                    generated += 1
                coverage.append(
                    {
                        "fund_symbol": spec.fund_symbol,
                        "agent_name": spec.agent_name,
                        "holding_rows": len(holdings),
                        "first_period": str(holdings["report_period"].min().date()),
                        "last_period": str(holdings["report_period"].max().date()),
                        "exception_tags": list(spec.exception_tags),
                    }
                )
            except Exception as exc:
                failures.append(
                    {
                        "fund_symbol": spec.fund_symbol,
                        "agent_name": spec.agent_name,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
    finally:
        versions = repo.dataset_versions()
        repo.close()
    run = {
        "format_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "as_of": as_of.isoformat(),
        "database": str(Path(database).resolve()),
        "manifest": str(Path(manifest).resolve()),
        "git_revision": _git_revision(Path(repo_root)),
        "fund_count": len(specs),
        "expected_files": len(specs) * len(JSON_TYPES),
        "generated_files": generated,
        "failures": failures,
        "coverage": coverage,
        "datasets": versions,
    }
    (output / "run-manifest.json").write_text(
        json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return run


def prepare_agent(
    *, candidate: str | Path, destination: str | Path, manifest: str | Path
) -> dict[str, object]:
    candidate = Path(candidate).resolve()
    destination = Path(destination).resolve()
    if candidate == destination:
        raise ValueError("candidate and destination must be different")
    specs = load_manifest(manifest)
    expected = {f"{spec.agent_name}_{kind}.txt" for spec in specs for kind in JSON_TYPES}
    actual = {path.name for path in candidate.glob("*.txt")}
    missing = sorted(expected - actual)
    if missing:
        raise ValueError(f"candidate is incomplete; missing {len(missing)} files")
    destination.mkdir(parents=True, exist_ok=True)
    for name in sorted(expected):
        (destination / name).write_bytes((candidate / name).read_bytes())
    package = {
        "source": str(candidate),
        "destination": str(destination),
        "files": len(expected),
        "funds": len(specs),
        "official_agent_directory_modified": False,
    }
    (destination / "candidate-manifest.json").write_text(
        json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return package
