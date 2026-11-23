"""Atomic local cache for page-shaped crowding responses."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Any


PROJECT = Path(__file__).resolve().parents[1]
from crowding.settings import BUILD_MODE, CANDIDATE_ROOT, FROZEN_ROOT, FROZEN_VERSION, STATE

CACHE_ROOT = STATE / 'cache' if BUILD_MODE else FROZEN_ROOT
DAILY_SCHEMA = 'crowding-daily-v1'
DAILY_STARTS = ('hp', 'qe', 'd5', 'd20')
DAILY_PAGES = {
    'stocks': DAILY_STARTS, 'unheld_stocks': DAILY_STARTS, 'overview': DAILY_STARTS,
    'funds': DAILY_STARTS, 'industries': DAILY_STARTS, 'index': ('default',),
    'holder_inputs': ('all',), 'market_inputs': ('all',),
}
_MEMORY: dict[tuple[str, str, str], Any] = {}
_BUILD_LOCK = threading.Lock()
_BUILDING: set[tuple[str, str, str]] = set()
_KEY_LOCKS: dict[tuple[str, str, str], threading.RLock] = {}


def lake_version(lake_root: Path, as_of: str) -> str:
    """Cheap publication fingerprint; avoids recursive hashing of all Parquet."""
    root = Path(lake_root)
    parts = [calculation_version(), *_stat_parts(root, 'pages-v3')]
    if os.environ.get('CROWDING_AS_OF'):
        parts.append('as_of=' + os.environ['CROWDING_AS_OF'])
    return hashlib.sha256('|'.join(parts).encode()).hexdigest()[:16]


# Only published inputs used by this dashboard invalidate its generations.
# The global ingestion manifest also changes for unrelated news/research jobs.
INPUT_DATASETS = {
    'curated': ('fund_holdings','fund_profiles','fund_product_share_observations',
        'fund_classification_snapshot','fund_asset_allocation_snapshot',
        'fund_holder_structure_snapshot','fund_nav','fund_shares_outstanding','etf_profiles','etf_daily_metrics',
        'daily_bars','valuation_metrics','share_structure','instruments',
        'industry_members','industry_taxonomy','trading_calendar','index_bars',
        'fund_flow','moneyflow','margin_trading','market_margin_totals',
        'market_money_flow','northbound_flows','southbound_flows',
        'market_ipo_funding','market_repurchase_announcements',
        'stock_holdertrade_announcements','corporate_actions'),
    'derived': ('adj_factors','daily_free_float_shares','fund_scale'),
}


def _fingerprint_candidates(root: Path) -> list[Path]:
    paths=[]
    for layer, names in INPUT_DATASETS.items():
        for name in names:
            folder=root/layer/name
            paths.extend(folder.rglob('*.parquet'))
    for folder in (root/'meta/adj_factors_cache',
                   root/'meta/fund_product_share_observations/published',
                   root/'meta/fund_classification_snapshot'):
        if folder.exists():
            paths.extend(p for p in folder.rglob('*') if p.is_file())
    for name in ('fund_coverage_ledger.parquet','adj-factor-blocklist.parquet'):
        evidence=root/'meta/quality'/name
        if evidence.exists():paths.append(evidence)
    return sorted(paths)


def _stat_parts(root: Path, scope: str) -> list[str]:
    parts = [scope]
    for path in _fingerprint_candidates(root):
        try:
            stat=path.stat()
            parts.append(f'{path}:{stat.st_mtime_ns}:{stat.st_size}')
        except OSError:
            parts.append(f'{path}:missing')
    return parts


def snapshot_fingerprint(lake_root: Path) -> str:
    """Version the expensive input snapshot without reading every Parquet file."""
    root = Path(lake_root)
    parts = _stat_parts(root, 'snapshot-v3')
    for name in ('lake.py', 'product_view.py', 'verified_holdings.py', 'fund_classification.py'):
        parts.append(hashlib.sha256((PROJECT / 'crowding' / name).read_bytes()).hexdigest())
    if os.environ.get('CROWDING_AS_OF'):
        parts.append('as_of=' + os.environ['CROWDING_AS_OF'])
    return hashlib.sha256('|'.join(parts).encode()).hexdigest()[:20]


def _clean_json(value: Any) -> Any:
    """Keep persisted cache JSON strict so a corrupt float cannot leak to APIs."""
    if isinstance(value, float):
        return value if value == value and value not in (float('inf'), float('-inf')) else None
    if isinstance(value, dict):
        return {str(key): _clean_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean_json(item) for item in value]
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    return value


def save_snapshot(lake_root: Path, value: dict, *, fingerprint: str | None = None) -> Path:
    """Atomically persist the normalized snapshot used by every dashboard page."""
    fingerprint = fingerprint or snapshot_fingerprint(lake_root)
    directory = CACHE_ROOT / 'snapshots'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f'{fingerprint}.json.gz'
    temporary = path.with_suffix(f'.{os.getpid()}.{uuid.uuid4().hex}.tmp')
    payload = {'saved_at': time.time(), 'fingerprint': fingerprint, 'snapshot': _clean_json(value)}
    with gzip.open(temporary, 'wt', encoding='utf-8', compresslevel=3) as handle:
        json.dump(payload, handle, ensure_ascii=False, allow_nan=False, default=str, separators=(',', ':'))
    temporary.replace(path)
    return path


def load_snapshot(lake_root: Path, *, fingerprint: str | None = None,
                  directory: Path | None = None) -> tuple[dict, float, Path] | None:
    """Load the snapshot matching the current cheap lake fingerprint."""
    fingerprint = fingerprint or snapshot_fingerprint(lake_root)
    path = (directory or CACHE_ROOT) / 'snapshots' / f'{fingerprint}.json.gz'
    try:
        with gzip.open(path, 'rt', encoding='utf-8') as handle:
            payload = json.load(handle)
        value = payload.get('snapshot')
        if payload.get('fingerprint') != fingerprint or not isinstance(value, dict) or not isinstance(value.get('metadata'), dict):
            return None
        return value, float(payload.get('saved_at') or path.stat().st_mtime), path
    except (OSError, ValueError, TypeError):
        return None


def _path(version: str, name: str, key: str, *, root: Path | None = None) -> Path:
    digest = hashlib.sha256(key.encode()).hexdigest()[:20]
    if root is None:
        root = CANDIDATE_ROOT if not BUILD_MODE and version != FROZEN_VERSION else CACHE_ROOT
    return root / version / name / f'{digest}.json'


def latest_stale(name: str, key: str, *, exclude_version: str) -> tuple[str, dict] | None:
    """Return the newest published page for a logical key across lake versions."""
    digest = hashlib.sha256(key.encode()).hexdigest()[:20]
    candidates = []
    if CACHE_ROOT.is_dir():
        for directory in CACHE_ROOT.iterdir():
            if not directory.is_dir() or directory.name in ('snapshots',) or directory.name == exclude_version:
                continue
            path = directory / name / f'{digest}.json'
            try:
                candidates.append((path.stat().st_mtime_ns, directory.name, path))
            except OSError:
                continue
    for _, version, path in sorted(candidates, reverse=True):
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
            _MEMORY[(version, name, key)] = value
            return version, value
        except (OSError, ValueError):
            continue
    return None


def load(version: str, name: str, key: str) -> dict | None:
    memory_key = (version, name, key)
    if memory_key in _MEMORY:
        return _MEMORY[memory_key]
    path = _path(version, name, key)
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if len(_MEMORY) >= 64:
            _MEMORY.clear()
        _MEMORY[memory_key] = value
        return value
    except (OSError, ValueError):
        return None


def store(version: str, name: str, key: str, value: Any) -> None:
    path = _path(version, name, key)
    atomic_json(path, value)
    if len(_MEMORY) >= 64:
        _MEMORY.clear()
    _MEMORY[(version, name, key)] = value


def invalidate(version: str, name: str, key: str) -> None:
    _MEMORY.pop((version, name, key), None)
    try:
        _path(version, name, key).unlink()
    except FileNotFoundError:
        pass


def get_or_build(version: str, name: str, key: str, builder: Callable[[], Any],
                 *, allow_stale: bool = False) -> Any:
    cached = load(version, name, key)
    if cached is not None:
        return cached
    if allow_stale:
        stale = latest_stale(name, key, exclude_version=version)
        if stale is not None:
            build_key = (version, name, key)
            with _BUILD_LOCK:
                should_build = build_key not in _BUILDING
                if should_build:
                    _BUILDING.add(build_key)
            if should_build:
                def refresh() -> None:
                    try:
                        store(version, name, key, builder())
                    finally:
                        with _BUILD_LOCK:
                            _BUILDING.discard(build_key)
                threading.Thread(target=refresh, name=f'cache-refresh-{name}', daemon=True).start()
            return stale[1]
    build_key = (version, name, key)
    with _BUILD_LOCK:
        lock = _KEY_LOCKS.setdefault(build_key, threading.RLock())
    with lock:
        cached = load(version, name, key)
        if cached is not None:
            return cached
        value = builder()
        store(version, name, key, value)
        return value


def calculation_version() -> str:
    digest = hashlib.sha256()
    for path in sorted((PROJECT / 'crowding').glob('*.py')):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def daily_manifest(version: str) -> dict | None:
    """Identify a complete daily bundle; legacy scale overlays have a different schema."""
    if len(version) != 16 or any(c not in '0123456789abcdef' for c in version):
        return None
    try:
        value = json.loads((CANDIDATE_ROOT / version / 'manifest.json').read_text())
        if value.get('schema') != DAILY_SCHEMA:
            return None
        fingerprint = value['input_fingerprint']
        if (value.get('version') != version or value.get('status') != 'complete'
                or not isinstance(fingerprint, str) or len(fingerprint) != 20
                or any(c not in '0123456789abcdef' for c in fingerprint)
                or not (CANDIDATE_ROOT / version / 'snapshots' / f'{fingerprint}.json.gz').is_file()
                or any(not _path(version, name, key, root=CANDIDATE_ROOT).is_file()
                       for name, keys in DAILY_PAGES.items() for key in keys)):
            raise ValueError('incomplete daily bundle')
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise RuntimeError(f'公募抱团日更版本 {version} 校验失败') from exc
    return value


def active_manifest() -> dict | None:
    try:
        pointer = json.loads((CACHE_ROOT / 'active.json').read_text())
        return version_manifest(pointer['version'])
    except (OSError, ValueError, KeyError):
        return None


def version_manifest(version: str) -> dict | None:
    if not BUILD_MODE and version != FROZEN_VERSION:
        return None
    if not isinstance(version, str) or not version or any(c not in '0123456789abcdef-' for c in version):
        return None
    try:
        manifest = json.loads((CACHE_ROOT / version / 'manifest.json').read_text())
        return manifest if manifest.get('status') == 'complete' and manifest.get('version') == version else None
    except (OSError, ValueError):
        return None


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f'.{os.getpid()}.{uuid.uuid4().hex}.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, default=str,
                                        separators=(',', ':')), encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def complete_version(version: str, manifest: dict) -> None:
    if not manifest.get('tasks') or any(item.get('status') != 'ok' for item in manifest['tasks']):
        raise ValueError('cannot publish incomplete cache tasks')
    if manifest.get('cache_schema') == 'crowding-generation-v2':
        starts={'hp','qe','d5','d20'}
        required={(page,start) for page in ('stocks','unheld','overview','funds','industries') for start in starts}
        required.add(('index','hp'))
        observed={(task.get('page'),task.get('start')) for task in manifest['tasks']}
        if not required <= observed:
            raise ValueError('generation is missing a page/start')
        for page,start in required:
            name={'unheld':'unheld_stocks'}.get(page,page)
            key='default' if page=='index' else start
            if load(version,name,key) is None:
                raise ValueError(f'generation page absent: {page}/{start}')
        for name in ('holder_inputs','market_inputs'):
            if load(version,name,'all') is None:
                raise ValueError(f'precomputed shared input absent: {name}')
    if manifest.get('input_fingerprint') != snapshot_fingerprint(Path(manifest['lake_root'])):
        raise ValueError('lake publications changed during build; candidate not published')
    if manifest.get('calculation_version') and manifest['calculation_version'] != calculation_version():
        raise ValueError('calculation code changed during build; candidate not published')
    payload = {**manifest, 'version': version, 'status': 'complete', 'validated_at': time.time()}
    atomic_json(CACHE_ROOT / version / 'manifest.json', payload)


def publish_version(version: str, manifest: dict) -> None:
    complete_version(version, manifest)
    payload=version_manifest(version)
    payload['published_at']=time.time()
    atomic_json(CACHE_ROOT / version / 'manifest.json',payload)
    atomic_json(CACHE_ROOT / 'active.json', {'version': version})
