import gzip
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crowding import page_cache


def test_daily_version_is_self_contained_and_fails_closed(tmp_path):
    runtime = tmp_path / 'runtime'
    versions = runtime / 'phase2/vic_desks/crowding/versions'
    version = '0123456789abcdef'
    fingerprint = '1234567890abcdef1234'
    metadata = {'market_date': '2026-11-16', 'as_of': '2026-11-16', 'holding_period': '2026-06-30'}
    manifest = {'schema': page_cache.DAILY_SCHEMA, 'version': version, 'status': 'complete',
                'input_fingerprint': fingerprint, 'metadata': metadata,
                'base_version': 'a49acf3ac9d03625', 'fund_scale_period': '2026-09-30'}
    page_cache.atomic_json(versions / version / 'manifest.json', manifest)
    for name, keys in page_cache.DAILY_PAGES.items():
        for key in keys:
            page_cache.atomic_json(page_cache._path(version, name, key, root=versions), {
                'data': {'trade_date': '2026-11-16', 'later_scale': {'report_period': '2026-09-30'}}
            })
    snapshot = versions / version / 'snapshots' / f'{fingerprint}.json.gz'
    snapshot.parent.mkdir()
    with gzip.open(snapshot, 'wt') as handle:
        json.dump({'fingerprint': fingerprint, 'snapshot': {'metadata': metadata, 'rows': []}}, handle)
    env = {**os.environ, 'YUNALPHA_RUNTIME': str(runtime), 'YUNALPHA_INPUTS': str(tmp_path / 'no-inputs'),
           'YUNALPHA_LAKE_ROOT': str(tmp_path / 'no-lake'), 'YUNALPHA_CROWDING_VERSION': version,
           'PYTHONDONTWRITEBYTECODE': '1'}
    env.pop('CROWDING_CACHE_BUILD', None)
    program = """from crowding import server
assert server.get_snapshot()['metadata']['market_date'] == '2026-11-16'
assert server.get_funds_page('d20')['data']['later_scale']['report_period'] == '2026-09-30'
assert server._snapshot_build_count == 0
"""

    def start():
        return subprocess.run([sys.executable, '-c', program], cwd=Path(__file__).resolve().parents[1],
                              env=env, capture_output=True, text=True, check=False)

    result = start()
    assert result.returncode == 0, result.stderr
    page_cache._path(version, 'industries', 'qe', root=versions).unlink()
    result = start()
    assert result.returncode != 0
    assert 'incomplete daily bundle' in result.stderr
