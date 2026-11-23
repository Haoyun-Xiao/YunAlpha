from seven_desks.settings import CACHE as BUILD_ROOT, LAKE, SOURCE as REVIEW_SOURCE, FROZEN_VERSION
from seven_desks.store import version_root, connect
"""Attach existing disclosure summary fields, no download or generated quotations."""
from pathlib import Path
import duckdb
ROOT=Path(__file__).resolve().parents[2];OUT=(BUILD_ROOT / 'contrarian-extra')
OUT.mkdir(parents=True, exist_ok=True)
c=connect(config={'threads':2,'memory_limit':'1GB'})
c.execute(f"COPY (SELECT concat(symbol,':forecast:',announce_date,':',report_period) AS event_id,symbol,announce_date event_date,'positive_earnings_forecast' kind,left(coalesce(nullif(summary,''),change_reason),500) summary,'earnings_forecasts' AS source FROM read_parquet('{LAKE}/curated/earnings_forecasts/**/*.parquet',hive_partitioning=false) WHERE announce_date BETWEEN '2016-01-01' AND '2026-11-09' AND coalesce(nullif(summary,''),nullif(change_reason,'')) IS NOT NULL UNION ALL SELECT concat(symbol,':express:',announce_date,':',report_period),symbol,announce_date,'positive_earnings_express',left(performance_summary,500),'earnings_express' FROM read_parquet('{LAKE}/curated/earnings_express/**/*.parquet',hive_partitioning=false) WHERE announce_date BETWEEN '2016-01-01' AND '2026-11-09' AND nullif(performance_summary,'') IS NOT NULL) TO '{OUT}/event-summaries.parquet' (FORMAT PARQUET,COMPRESSION ZSTD)")
c.close()
