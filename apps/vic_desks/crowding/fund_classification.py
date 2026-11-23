"""Versioned, evidence-scoped fund classification for the crowding board.

The original board excludes passive index funds, ETFs, linked funds, bond/FOF
and other non-equity products, while retaining enhanced-index funds as active.
This module uses explicit lake fields only. A fund name alone never proves ETF
or linked status; a symbol present in the published ``etf_profiles`` dataset
is the only ETF evidence accepted here.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import duckdb
from crowding.resources import connect as bounded_connect

from crowding.lake import DEFAULT_LAKE_ROOT


ACTIVE_MIXED_TYPES = frozenset({
    '灵活配置型', '平衡型', '股票型', '成长型', '价值型', '价值增长型',
    '积极成长型', '增长型', '创新型', '积极配置型', '普通股票型',
})
PASSIVE_INDEX_TYPE = '被动指数型'
ENHANCED_INDEX_TYPE = '增强指数型'


def classify_profile(row: dict[str, Any], *, etf_symbols: set[str] | None = None) -> dict[str, Any]:
    """Return one classification without guessing from a display name."""
    etf_symbols = etf_symbols or set()
    symbol = str(row.get('symbol') or '')
    fund_type = row.get('fund_type')
    invest_type = row.get('invest_type')
    declared_type = row.get('type')
    is_etf = symbol in etf_symbols if etf_symbols else None
    base = {
        'symbol': symbol,
        'fund_type': fund_type,
        'invest_type': invest_type,
        'type': declared_type,
        'is_etf': is_etf,
        'evidence': ['fund_profiles.fund_type', 'fund_profiles.invest_type'],
    }
    # These product labels are explicit evidence of a feeder/FOF/QDII
    # structure. They are used as an exclusion guard, while a name by itself
    # remains insufficient evidence for ordinary classification.
    text_name = str(row.get('name') or '')
    if 'FOF' in text_name.upper():
        return {**base, 'category': 'fof', 'is_active_for_crowding': False,
                'reason': 'explicit product label contains FOF'}
    if 'QDII' in text_name.upper():
        return {**base, 'category': 'qdii', 'is_active_for_crowding': False,
                'reason': 'explicit product label contains QDII'}
    if 'ETF联接' in text_name:
        return {**base, 'category': 'linked_fund', 'is_active_for_crowding': False,
                'reason': 'explicit product label contains ETF联接'}
    if is_etf is True:
        return {**base, 'category': 'etf', 'is_active_for_crowding': False,
                'reason': 'symbol exists in published etf_profiles'}
    if invest_type == PASSIVE_INDEX_TYPE:
        return {**base, 'category': 'passive_index', 'is_active_for_crowding': False,
                'reason': 'explicit fund_profiles.invest_type=被动指数型'}
    if invest_type == ENHANCED_INDEX_TYPE:
        if fund_type in ('股票型', '混合型'):
            return {**base, 'category': 'active_index_enhanced', 'is_active_for_crowding': True,
                    'reason': 'explicit enhanced-index type; original board includes it as active'}
        return {**base, 'category': 'unknown', 'is_active_for_crowding': None,
                'reason': 'enhanced-index label with non-equity fund_type'}
    if fund_type == '股票型' and invest_type in ('股票型', '普通股票型'):
        return {**base, 'category': 'active_equity', 'is_active_for_crowding': True,
                'reason': 'explicit equity fund and equity investment type'}
    if fund_type == '混合型' and invest_type in ACTIVE_MIXED_TYPES:
        return {**base, 'category': 'active_equity', 'is_active_for_crowding': True,
                'reason': 'explicit equity-oriented mixed investment type'}
    if fund_type in ('股票型', '混合型') and invest_type is None:
        return {**base, 'category': 'unknown', 'is_active_for_crowding': None,
                'reason': 'equity fund type but missing invest_type'}
    if fund_type in ('股票型', '混合型') and invest_type == '混合型':
        return {**base, 'category': 'unknown', 'is_active_for_crowding': None,
                'reason': 'generic mixed investment type; equity exposure not proven'}
    if fund_type in ('债券型', '货币型', '货币市场型', '短期理财债券型', 'REITs', '其他', '保本型'):
        return {**base, 'category': 'non_equity', 'is_active_for_crowding': False,
                'reason': f'non-equity fund_type={fund_type}'}
    return {**base, 'category': 'unknown', 'is_active_for_crowding': None,
            'reason': 'missing or unrecognized explicit classification fields'}


def classify_profiles(rows: Iterable[dict[str, Any]], *, etf_symbols: set[str] | None = None,
                      snapshot: dict[str, dict] | None = None) -> dict[str, Any]:
    snapshot = snapshot or {}
    results = []
    for row in rows:
        item = classify_profile(row, etf_symbols=etf_symbols)
        # The provider's current category is used only to resolve unknown
        # profile types; it never overrides explicit profile or ETF evidence.
        extra = snapshot.get(item['symbol'])
        if item['category'] == 'unknown' and extra:
            label = extra.get('classification_text') or ''
            if label in ('混合型-偏股', '混合型-灵活', '混合型-平衡', '股票型'):
                item.update(category='active_equity', is_active_for_crowding=True)
            elif label in ('混合型-偏债',):
                item.update(category='non_equity', is_active_for_crowding=False)
            elif label.startswith('FOF-'):
                item.update(category='fof', is_active_for_crowding=False)
            if item['category'] != 'unknown':
                item.update(reason='explicit current AKShare classification (non-PIT retrospective)',
                            snapshot_category=label, snapshot_date=extra.get('snapshot_date'))
                item['evidence'].append('fund_classification_snapshot')
        results.append(item)
    counts = Counter(item['category'] for item in results)
    active = [item['symbol'] for item in results if item['is_active_for_crowding'] is True]
    unknown = [item for item in results if item['category'] == 'unknown']
    return {
        'rows': results, 'counts': dict(counts), 'active_symbols': active,
        'unknown_count': len(unknown), 'scope': 'current fund_profiles plus AKShare non-PIT snapshot; current classification retrospectively applied',
        'rules_version': 'crowding-fund-classification-v3',
    }


def classify_fund_profiles(*, lake_root: Path = DEFAULT_LAKE_ROOT) -> dict[str, Any]:
    root = Path(lake_root)
    profiles = sorted((root / 'curated' / 'fund_profiles').rglob('*.parquet'))
    if not profiles:
        return {'rows': [], 'counts': {}, 'active_symbols': [], 'unknown_count': 0,
                'scope': 'fund_profiles missing', 'rules_version': 'crowding-fund-classification-v3'}
    etf_paths = sorted((root / 'curated' / 'etf_profiles').rglob('*.parquet'))
    conn = bounded_connect()
    try:
        rows = [dict(zip([c[0] for c in conn.description], row)) for row in conn.execute(
            "SELECT * FROM read_parquet(?, hive_partitioning=false, union_by_name=true)",
            [[str(p) for p in profiles]],
        ).fetchall()]
        etfs = set()
        if etf_paths:
            cols = [c[0] for c in conn.execute('DESCRIBE SELECT * FROM read_parquet(?, hive_partitioning=false)', [[str(etf_paths[0])]]).fetchall()]
            if 'symbol' in cols:
                etfs = {str(r[0]) for r in conn.execute("SELECT DISTINCT symbol FROM read_parquet(?, hive_partitioning=false)", [[str(p) for p in etf_paths]]).fetchall()}
        snapshots = {}
        snapshot_files = sorted((root / 'curated' / 'fund_classification_snapshot').rglob('*.parquet'))
        if snapshot_files:
            records = conn.execute("""SELECT symbol, classification_text, CAST(snapshot_date AS VARCHAR)
                FROM read_parquet(?, hive_partitioning=false, union_by_name=true)
                QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY snapshot_date DESC, fetched_at DESC)=1""", [[str(p) for p in snapshot_files]]).fetchall()
            snapshots = {symbol: {'classification_text': label, 'snapshot_date': day} for symbol, label, day in records}
    finally:
        conn.close()
    # Latest profile row per symbol, using the source receipt timestamp.
    latest = {}
    for row in rows:
        symbol = row.get('symbol')
        old = latest.get(symbol)
        if old is None or str(row.get('fetched_at') or '') > str(old.get('fetched_at') or ''):
            latest[symbol] = row
    result = classify_profiles(latest.values(), etf_symbols=etfs, snapshot=snapshots)
    result['coverage'] = {'fund_profile_symbols': len(latest), 'etf_profile_symbols': len(etfs), 'classification_snapshot_symbols': len(snapshots),
                          'classification_snapshot_date': max((v['snapshot_date'] for v in snapshots.values()), default=None),
                          'classification_snapshot_applied': sum('fund_classification_snapshot' in x['evidence'] for x in result['rows']),
                          'name_guard_counts': {k: sum(1 for row in result['rows'] if row['category'] == k)
                                                for k in ('linked_fund', 'fof', 'qdii')}}
    return result
