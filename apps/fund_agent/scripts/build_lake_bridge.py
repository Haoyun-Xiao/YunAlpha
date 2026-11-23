#!/usr/bin/env python3
"""Build a worktree DuckDB catalog whose views read official Parquet in place."""

from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from fund_research_agent.config import load_settings
from fund_research_agent.paths import WriteBoundary

TABLES = (
    "fund_profiles",
    "fund_manager_tenures",
    "fund_nav",
    "fund_holdings",
    "fund_reports",
    "fund_scale",
    "fund_annual_turnover",
    "daily_bars",
    "index_bars",
    "corporate_actions",
    "valuation_metrics",
    "financial_statement_items",
    "industry_members",
    "industry_taxonomy",
    "instruments",
)


def source_for(lake: Path, table: str) -> Path | None:
    for layer in ("derived", "curated"):
        candidate = lake / layer / table
        if candidate.exists() and any(candidate.rglob("*.parquet")):
            return candidate
    return None


def main() -> None:
    settings = load_settings()
    lake = settings.legacy_root
    if not all((lake / layer).is_dir() for layer in ("curated", "derived")):
        raise SystemExit(f"未找到正式湖 curated/derived：{lake}")
    boundary = WriteBoundary(settings.project_root, lake, settings.var_root)
    database = boundary.assert_writable(settings.lake_database)
    boundary.ensure_dir(database.parent)
    catalog: list[dict[str, str]] = []
    # Publish the new catalog only after all views bind successfully.
    with tempfile.TemporaryDirectory(dir=database.parent) as temp_dir:
        pending = Path(temp_dir) / database.name
        with duckdb.connect(str(pending)) as connection:
            connection.execute("PRAGMA threads=4")
            connection.execute("PRAGMA memory_limit='2GB'")
            for table in TABLES:
                source = source_for(lake, table)
                if source is None:
                    continue
                glob = str(source / "**/*.parquet").replace("'", "''")
                connection.execute(
                    f'CREATE VIEW "{table}" AS SELECT * FROM '
                    f"read_parquet('{glob}', union_by_name=true, hive_partitioning=false)"
                )
                catalog.append({"table": table, "source": str(source), "mode": "read_parquet_view"})
            required = {"fund_profiles", "fund_manager_tenures", "fund_nav", "fund_holdings", "index_bars"}
            missing = required - {
                entry["table"] for entry in catalog
            }
            if missing:
                raise SystemExit(f"正式湖缺少基金 Agent 基础表：{', '.join(sorted(missing))}")
            connection.execute("CREATE TABLE bridge_metadata(key VARCHAR PRIMARY KEY,value VARCHAR)")
            metadata = {
                "built_at": datetime.now(UTC).isoformat(),
                "lake_root": str(lake),
                "tables": catalog,
            }
            connection.execute(
                "INSERT INTO bridge_metadata VALUES ('manifest', ?)",
                [json.dumps(metadata, ensure_ascii=False)],
            )
        pending.replace(database)
    # MAL reads the official lake, but creates its own DuckDB catalog on SQL
    # calls. Keep that catalog separate from the Agent's read-only bridge.
    mcp_catalog = boundary.assert_writable(database.parent / "mal-readonly.duckdb")
    boundary.write_text(
        settings.var_root / "lake_bridge/mcp.toml",
        f"[data]\nroot = {json.dumps(str(lake), ensure_ascii=False)}\n"
        f"[duckdb]\npath = {json.dumps(str(mcp_catalog), ensure_ascii=False)}\n"
        'memory_limit = "2GB"\nthreads = 4\n'
        "[on_demand]\nenabled = false\ndatasets = []\n",
    )
    print(json.dumps({"database": str(database), "views": len(catalog)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
