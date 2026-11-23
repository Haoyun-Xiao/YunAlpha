#!/usr/bin/env python3
"""One-time, allowlisted import; runtime never imports from a source checkout."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

AGENT_REV = "df71bf566f75f4106bdef94d2a8a6eba953e0683"
MAL_REV = "7dd2f168af99f436bbc433bd90e1ff6cb04da957"
REPORT_JOB = "report_0f47549b33f0445f806ccac28036b503"
FUND, AS_OF = "013840.OF", "2026-11-05"
REPORT_FILES = {
    "report.md": "artifact_3cfe0a0b20bb490cb4b59d4b91babf57.md",
    "report.html": "artifact_42b65b6cc0314358a392aba54206dfbe.html",
    "report.docx": "artifact_5c5cd2a629e8494f99fe603a2cfaaae7.docx",
}


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(root), *args])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--agent-source", type=Path, required=True)
    parser.add_argument("--mal-source", type=Path, required=True)
    parser.add_argument("--fund-universe", type=Path, required=True)
    parser.add_argument("--uv", default="uv")
    args = parser.parse_args()
    source = args.agent_source.resolve()
    mal = args.mal_source.resolve()
    if git(source, "rev-parse", "HEAD").decode().strip() != AGENT_REV:
        raise SystemExit(f"Agent 来源须位于 {AGENT_REV}")
    target = args.inputs.resolve() / "fund_agent"
    if any(target.is_relative_to(root) for root in (source, mal, args.fund_universe.resolve().parent)):
        raise SystemExit("冻结输入只能写入 YunAlpha")
    if target.exists():
        raise SystemExit(f"已有冻结副本；不会覆盖 {target}")
    target.parent.mkdir(parents=True, exist_ok=True)

    with TemporaryDirectory(dir=target.parent) as temp:
        stage = Path(temp) / "fund_agent"
        source_var = source / "var"
        report = source_var / "jobs" / REPORT_JOB
        accepted = json.loads(
            (source_var / "validation/report-flow-v2/final-acceptance.json").read_text()
        )
        sample = next(
            entry for entry in accepted["reports"]
            if entry["job_id"] == REPORT_JOB and entry["fund"] == FUND
        )
        if sample["status"] != "completed_with_warnings" or not sample["word_download_verified"]:
            raise SystemExit("固定报告不匹配已验收样本")

        def copy(original: Path, relative: str) -> None:
            dest = stage / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, dest)

        for name in ("fund-manifest.csv", "fund-nav-dashboard.parquet", "materials/kb-index.sqlite"):
            copy(source / "external" / name, name)
        copy(args.fund_universe, "fund-universe.xlsx")
        for directory, suffixes in (
            ("materials/handbooks", {".md", ".docx"}),
            ("materials/roadshows", {".txt", ".md"}),
        ):
            for file in sorted((source / "external" / directory).iterdir()):
                if file.is_file() and file.suffix in suffixes:
                    copy(file, f"{directory}/{file.name}")
        for file in sorted((source_var / "replays/tavily").glob("*.json")):
            payload = json.loads(file.read_text())
            if payload.get("version") == "manager-material-search-v4" and payload.get("results"):
                copy(file, f"replays/tavily/{file.name}")
        for file in sorted((source_var / "cache/fund_reports" / FUND.split(".")[0]).glob("reports-*.json")):
            payload = json.loads(file.read_text())
            if payload.get("requested_as_of") == AS_OF and payload.get("records") and not payload.get("warning"):
                copy(file, f"quarterly_reports/{file.parent.name}/{file.name}")
        copy(report / "inputs/materials/scales.json", f"scales/{FUND}-{AS_OF}.json")
        for name, original in REPORT_FILES.items():
            copy(source_var / "artifacts" / REPORT_JOB / original, f"acceptance/report/{name}")
        copy(report / "handbook/handbook.md", "acceptance/report/handbook.md")
        # The frozen replay records the historical result, not the old Job row.
        sample = {key: value for key, value in sample.items() if key != "view_url"}
        (stage / "acceptance/report/provenance.json").write_text(
            json.dumps(sample | {"as_of": AS_OF}, ensure_ascii=False, indent=2) + "\n"
        )

        mal_build = Path(temp) / "mal-source"
        mal_build.mkdir()
        tar = git(mal, "archive", MAL_REV, "pyproject.toml", "README.pypi.md", "LICENSE", "NOTICE", "src")
        subprocess.run(["tar", "-xf", "-", "-C", str(mal_build)], input=tar, check=True)
        subprocess.run(
            [args.uv, "build", "--wheel", "--out-dir", str(stage / "wheels"), str(mal_build)],
            check=True,
        )
        # Avoid machine-specific source paths in the portable inventory.
        with (stage / "manifest.json").open("w", encoding="utf-8") as manifest:
            files = {}
            for file in sorted(stage.rglob("*")):
                if file.is_file() and file.name != "manifest.json":
                    with file.open("rb") as stream:
                        digest = hashlib.file_digest(stream, "sha256").hexdigest()
                    files[file.relative_to(stage).as_posix()] = {"sha256": digest, "bytes": file.stat().st_size}
            json.dump(
                {"source_commit": AGENT_REV, "mal_commit": MAL_REV, "report_sample": sample, "files": files},
                manifest, indent=2, ensure_ascii=False,
            )
            manifest.write("\n")
        stage.rename(target)
        print(f"已冻结 {len(files)} 个输入：{target}")


if __name__ == "__main__":
    main()
