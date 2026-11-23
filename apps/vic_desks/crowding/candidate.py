"""A published fund-scale addition to one fixed crowding generation."""
from functools import lru_cache
import hashlib
import json
import math
import re

from crowding.settings import CANDIDATE_ROOT, FROZEN_VERSION

SCHEMA = 'crowding-fund-scale-v1'


def candidate_version(payload: dict) -> str:
    content = json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(content.encode()).hexdigest()[:16]


@lru_cache(maxsize=1)
def load_candidate(version: str) -> dict:
    if not re.fullmatch(r'[0-9a-f]{16}', version):
        raise RuntimeError('公募抱团候选版本号必须为 16 位小写十六进制')
    try:
        manifest = json.loads((CANDIDATE_ROOT / version / 'manifest.json').read_text())
        observed_version = manifest.pop('version')
        if (observed_version != version or manifest['status'] != 'complete'
                or manifest['schema'] != SCHEMA or manifest['parent_version'] != FROZEN_VERSION
                or candidate_version(manifest) != version):
            raise ValueError('manifest mismatch')
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise RuntimeError(f'公募抱团候选 {version} 不存在或校验失败') from exc
    return manifest


def make_fund_scale(products, observations, scale_rows, *, report_period):
    """Sum known share-class assets only for complete, same-quarter receipts."""
    relations = {}
    for row in observations:
        if str(row['report_period']) == report_period:
            master = row['master_code'] + '.OF'
            if master in products:
                relations.setdefault(master, []).append(row)
    scales = {}
    for row in scale_rows:
        if str(row['report_period']) == report_period:
            scales.setdefault(row['symbol'], []).append(row)

    result = {}
    for master in sorted(products):
        receipt = relations.get(master, [])
        if not receipt:
            result[master] = {'status': 'no_verified_period_relation'}
            continue
        ids = {row['observation_id'] for row in receipt}
        codes = sorted(row['share_code'] + '.OF' for row in receipt)
        if len(ids) != 1 or len(set(codes)) != len(codes) or master not in codes:
            result[master] = {'status': 'ambiguous_period_relation'}
            continue
        items = []
        for code in codes:
            matches = scales.get(code, [])
            if len(matches) != 1:
                break
            row = matches[0]
            size = row.get('scale_yi')
            if row.get('src') != 'eastmoney_gmbd' or size is None or not math.isfinite(size) or size <= 0:
                break
            items.append({'symbol': code, 'scale_yi': size})
        if len(items) != len(codes):
            result[master] = {'status': 'incomplete_share_scale', 'share_symbols': codes}
            continue
        result[master] = {
            'status': 'observed_non_pit', 'net_asset_cny': round(math.fsum(x['scale_yi'] for x in items) * 1e8, 2),
            'share_scales': items, 'observation_id': next(iter(ids)),
            'announce_date': str(receipt[0]['announce_date']),
        }
    return result


def apply_fund_scale(page: dict, candidate: dict) -> dict:
    data = page['data']
    rows = data['funds']['rows']
    result = candidate['fund_scale']
    if {row['display_code'] for row in rows} != result.keys() or data['holding_period'] != candidate['holding_period']:
        raise RuntimeError('公募抱团候选基金名单或持仓期与基线不匹配')
    return {**page, 'data': {
        **data,
        'funds': {**data['funds'], 'rows': [
            {**row, 'later_scale': result[row['display_code']],
             'later_net_asset_cny': result[row['display_code']].get('net_asset_cny')} for row in rows
        ]},
        'later_scale': {'report_period': candidate['report_period'], 'source': 'fund_scale:eastmoney_gmbd',
                        'point_in_time': False, 'coverage': candidate['coverage']},
    }}
