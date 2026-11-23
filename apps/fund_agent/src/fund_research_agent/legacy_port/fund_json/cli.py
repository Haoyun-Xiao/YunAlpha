from __future__ import annotations

from datetime import date
from pathlib import Path

import click

from .engine import generate as generate_run
from .engine import prepare_agent as prepare_agent_run
from .legacy import audit_legacy as audit_legacy_run
from .validation import validate as validate_run

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PROJECT_ROOT.parents[1]
DEFAULT_MANIFEST = PROJECT_ROOT / "config" / "funds.csv"
DEFAULT_DATABASE = REPO_ROOT / "lake" / "duckdb" / "multi-asset-lake.duckdb"


@click.group()
def main() -> None:
    """Generate and validate fund-report JSON without Wind or legacy inputs."""


@main.command("audit-legacy")
@click.option("--legacy-json", type=click.Path(path_type=Path, exists=True), required=True)
@click.option("--legacy-source", type=click.Path(path_type=Path, exists=True), required=True)
@click.option("--dashboard-source", type=click.Path(path_type=Path, exists=True), required=True)
@click.option("--manifest", type=click.Path(path_type=Path, exists=True), default=DEFAULT_MANIFEST)
@click.option("--database", type=click.Path(path_type=Path, exists=True), default=DEFAULT_DATABASE)
@click.option("--output", type=click.Path(path_type=Path), required=True)
def audit_legacy_command(
    legacy_json: Path,
    legacy_source: Path,
    dashboard_source: Path,
    manifest: Path,
    database: Path,
    output: Path,
) -> None:
    result = audit_legacy_run(
        legacy_json=legacy_json,
        legacy_source=legacy_source,
        dashboard_source=dashboard_source,
        manifest=manifest,
        database=database,
        output=output,
    )
    click.echo(f"audited {result['funds']} funds and {result['files']} legacy files")


@main.command("generate")
@click.option("--database", type=click.Path(path_type=Path, exists=True), default=DEFAULT_DATABASE)
@click.option("--manifest", type=click.Path(path_type=Path, exists=True), default=DEFAULT_MANIFEST)
@click.option(
    "--as-of", "as_of_text", required=True, help="Required reproducibility cutoff, YYYY-MM-DD"
)
@click.option("--output", type=click.Path(path_type=Path), required=True)
def generate_command(database: Path, manifest: Path, as_of_text: str, output: Path) -> None:
    run = generate_run(
        database=database,
        manifest=manifest,
        output=output,
        as_of=date.fromisoformat(as_of_text),
        repo_root=REPO_ROOT,
    )
    click.echo(f"generated {run['generated_files']}/{run['expected_files']} files")
    if run["failures"]:
        raise click.ClickException(f"{len(run['failures'])} funds failed; see run-manifest.json")


@main.command("validate")
@click.option("--legacy-json", type=click.Path(path_type=Path, exists=True), required=True)
@click.option("--candidate", type=click.Path(path_type=Path, exists=True), required=True)
@click.option("--manifest", type=click.Path(path_type=Path, exists=True), default=DEFAULT_MANIFEST)
@click.option("--output", type=click.Path(path_type=Path), required=True)
def validate_command(legacy_json: Path, candidate: Path, manifest: Path, output: Path) -> None:
    result = validate_run(
        legacy_json=legacy_json, candidate=candidate, manifest=manifest, output=output
    )
    click.echo(f"validation gate: {'PASS' if result['passed'] else 'FAIL'}")
    if not result["passed"]:
        raise click.ClickException("acceptance gate failed; see validation-summary.json")


@main.command("prepare-agent")
@click.option("--candidate", type=click.Path(path_type=Path, exists=True), required=True)
@click.option("--destination", type=click.Path(path_type=Path), required=True)
@click.option("--manifest", type=click.Path(path_type=Path, exists=True), default=DEFAULT_MANIFEST)
def prepare_agent_command(candidate: Path, destination: Path, manifest: Path) -> None:
    result = prepare_agent_run(candidate=candidate, destination=destination, manifest=manifest)
    click.echo(f"prepared {result['files']} candidate files without modifying the Agent")


if __name__ == "__main__":
    main()
