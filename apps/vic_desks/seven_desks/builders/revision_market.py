from seven_desks.settings import CACHE as BUILD_ROOT, LAKE, SOURCE as REVIEW_SOURCE, FROZEN_VERSION
from seven_desks.store import version_root, connect
"""Build cost-range positions from the immutable 23f review snapshot only."""
from pathlib import Path
import hashlib
import json
import duckdb

ROOT = Path(__file__).resolve().parents[2]
SOURCE = (version_root(FROZEN_VERSION) / 'data')
OUT = (BUILD_ROOT / 'revision-market')
FORMULA = 'local_cost_range_252market_sessions_v2'

# src and calendar are relations with unique (symbol, trade_date) and trade_date
# respectively. Integer market-session range prevents suspensions from pulling
# arbitrarily old stock observations into the window.
POSITION_QUERY = """WITH valid AS (
   SELECT s.*,c.seq,isfinite(cost_5pct) AND isfinite(cost_95pct) AND cost_5pct>0 AND cost_95pct>=cost_5pct ok
   FROM src s JOIN calendar c USING(trade_date)
), w AS (
   SELECT symbol,trade_date,close,
   count(*) FILTER (WHERE ok) OVER win sample_count,
   min(cost_5pct) FILTER (WHERE ok) OVER win cost_low,
   max(cost_95pct) FILTER (WHERE ok) OVER win cost_high
   FROM valid WINDOW win AS (PARTITION BY symbol ORDER BY seq RANGE BETWEEN 251 PRECEDING AND CURRENT ROW)
) SELECT symbol,trade_date,
   CASE WHEN sample_count>=202 AND cost_low>0 AND cost_high>cost_low AND isfinite(close) AND close>0
        THEN 100*(close-cost_low)/(cost_high-cost_low) END position_range,
   sample_count,cost_low,cost_high,
   sample_count/252.0 coverage,
   'last_252_market_sessions;valid_positive_ordered_cost_pairs' sample_scope,
   'local_cost_range_252market_sessions' formula_id, 'v2' formula_version,
   CASE WHEN sample_count<202 THEN 'valid_cost_pairs_below_202'
        WHEN cost_low IS NULL OR cost_low<=0 OR cost_high<=cost_low THEN 'invalid_or_flat_cost_range'
        WHEN close IS NULL OR NOT isfinite(close) OR close<=0 THEN 'invalid_close' END missing_reason
   FROM w"""


def build():
    OUT.mkdir(parents=True, exist_ok=True)
    inputs = sorted((SOURCE / 'market/chips').glob('daily-20[12][0-9].parquet'))
    if len(inputs) != 9:
        raise RuntimeError('expected 2018–2026 frozen daily files')
    c = connect()
    c.execute("SET memory_limit='1GB'")
    c.execute('SET threads=2')
    c.execute('SET temp_directory=?', [str(OUT / 'duckdb-tmp')])
    glob = str(SOURCE / 'market/chips/daily-20[12][0-9].parquet').replace("'", "''")
    c.execute(f"CREATE TEMP VIEW src AS SELECT symbol,trade_date,close,cost_5pct,cost_95pct FROM read_parquet('{glob}', union_by_name=true)")
    calendar = str(SOURCE / 'market/sentiment/daily.parquet').replace("'", "''")
    c.execute(f"CREATE TEMP VIEW calendar AS SELECT trade_date,row_number() OVER (ORDER BY trade_date) seq FROM read_parquet('{calendar}')")
    missing_dates = c.execute('SELECT count(*) FROM (SELECT DISTINCT trade_date FROM src) s ANTI JOIN calendar c USING(trade_date)').fetchone()[0]
    if missing_dates:
        raise RuntimeError(f'{missing_dates} chip dates absent from frozen market calendar')
    out = OUT / 'chip-cost-position.parquet'
    tmp = OUT / 'chip-cost-position.tmp.parquet'
    c.execute('COPY (' + POSITION_QUERY + ') TO ? (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 122880)', [str(tmp)])
    tmp.replace(out)
    stats = c.execute('SELECT count(*),count(position_range),min(trade_date),max(trade_date) FROM read_parquet(?)',[str(out)]).fetchone()
    sample = c.execute("SELECT position_range,sample_count,cost_low,cost_high FROM read_parquet(?) WHERE symbol='600519.SH' AND trade_date=DATE '2026-11-06'",[str(out)]).fetchone()
    c.close()
    digest = hashlib.sha256()
    with out.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    manifest = {'input_version':FROZEN_VERSION,'source_files':[str(p.relative_to(SOURCE)) for p in inputs]+['market/sentiment/daily.parquet'],
        'formula_id':'local_cost_range_252market_sessions','formula_version':'v2','sample_scope':'last 252 market sessions; at least 202 valid positive ordered cost pairs','clipped':False,
        'output':out.name,'rows':stats[0],'valid_rows':stats[1],'date_min':str(stats[2]),'date_max':str(stats[3]),'sample_600519_2026-11-06':sample,
        'sha256':digest.hexdigest()}
    (OUT/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(json.dumps(manifest,ensure_ascii=False))

if __name__ == '__main__':
    build()
