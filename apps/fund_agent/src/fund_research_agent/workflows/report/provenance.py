from __future__ import annotations

import hashlib
import json
from pathlib import Path

import duckdb

from fund_research_agent.domain.report_data import PIPELINE_VERSION


def fingerprint(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def lake_snapshot(database: Path) -> dict:
    """Fingerprint source files without scanning every financial row or copying data."""
    import glob
    import re

    con = duckdb.connect(str(database), read_only=True)
    try:
        views = con.execute(
            "SELECT view_name,sql FROM duckdb_views() WHERE NOT internal"
        ).fetchall()
    finally:
        con.close()
    files = {}
    for _name, sql in views:
        for expression in re.findall(r"'([^']+\.parquet)'", sql or ""):
            for item in glob.glob(expression, recursive=True):
                path = Path(item)
                stat = path.stat()
                files[str(path.resolve())] = [stat.st_size, stat.st_mtime_ns]
    return {"version": PIPELINE_VERSION, "files": files, "fingerprint": fingerprint(files)}
