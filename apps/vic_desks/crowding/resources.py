"""Bound DuckDB work used by the local dashboard.

Limits apply to DuckDB buffers, not to the Python process or macOS itself.
The API separately serializes data requests so several connections do not all
consume their maximum simultaneously. A query that exceeds the budget fails.
"""
import os
import duckdb

from crowding.settings import STATE


def connect():
    con = duckdb.connect(':memory:')
    try:
        # These environment variables are operator controls, not user input.
        # A 1 GB cap makes the real 31-quarter industry query fail even when
        # memory is available. The default is configurable for smaller hosts.
        memory = os.environ.get('CROWDING_DUCKDB_MEMORY', '3GB')
        temporary = os.environ.get('CROWDING_DUCKDB_TEMP_LIMIT', '3GB')
        threads = max(1, min(int(os.environ.get('CROWDING_DUCKDB_THREADS', '2')), 4))
        temp_dir = STATE / 'duckdb-tmp'
        temp_dir.mkdir(parents=True, exist_ok=True)
        con.execute('SET temp_directory = ?', [str(temp_dir)])
        con.execute('SET memory_limit = ?', [memory])
        con.execute('SET max_temp_directory_size = ?', [temporary])
        con.execute(f'SET threads = {threads}')
        con.execute('SET preserve_insertion_order = false')
        return con
    except Exception:
        con.close()
        raise
