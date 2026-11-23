"""Small disk cache for holdings-only historical aggregates."""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import gzip
import hashlib
import json
from pathlib import Path

from crowding.product_view import _atomic_json, disclosure_history

from crowding.settings import STATE

CACHE_ROOT = STATE / 'fixed-aggregates'
SCHEMA = 'fixed-aggregate-v1'


def cache_key(*, name, lake_root, context_version, periods, dependencies, code_paths):
    content = {'schema':SCHEMA,'name':name,'lake_root':str(Path(lake_root).resolve()),
        'context_version':context_version,'periods':[str(p) for p in periods],
        'dependencies':dependencies,
        'code':[(Path(p).name,hashlib.sha256(Path(p).read_bytes()).hexdigest()) for p in code_paths]}
    return hashlib.sha256(json.dumps(content,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()


def load(name, key, *, as_of, context=None):
    try:
        with gzip.open(CACHE_ROOT/name/(key+'.json.gz'),'rt',encoding='utf-8') as stream:
            payload=json.load(stream)
        if payload['schema']!=SCHEMA or payload['key']!=key:
            return None
        result=payload['result']
        if not isinstance(result,dict) or result.get('reason') or 'periods' not in result:
            return None
        result['as_of']=as_of.isoformat()
        # Re-read only tiny prior-year summaries: never freeze a missing
        # denominator just because a summary was not yet built on the first run.
        if context is not None and name in ('crowd_index','overlap_index'):
            counts={r['period']:r['product_coverage']['active_products_or_unmerged_shares']
                    for r in result['periods']}
            previous={r['period']:dict(r.get('disclosure') or {}) for r in result['periods']}
            for period in counts:
                prior=date.fromisoformat(period).replace(year=date.fromisoformat(period).year-1).isoformat()
                try:
                    summary=json.loads((context.cache_dir/(prior+'.summary.json')).read_text())
                    if summary['input_version']==context.input_version:
                        previous[period]['prior_year_identity_count']=summary['disclosed_identity_count']
                except (OSError,ValueError,KeyError,TypeError):
                    pass
            metadata=disclosure_history(counts,previous)
            for row in result['periods']:
                row['disclosure']=metadata[row['period']]
                for field in ('disclosing','disclosed_identity_count','prior_year_identity_count'):
                    row[field]=row['disclosure'][field]
        return result
    except (OSError,ValueError,EOFError,KeyError,TypeError):
        return None


def store(name,key,result):
    if not isinstance(result,dict) or result.get('reason') or 'periods' not in result:
        return
    try:
        _atomic_json(CACHE_ROOT/name/(key+'.json.gz'),
            {'schema':SCHEMA,'key':key,'result':deepcopy(result)},compressed=True)
    except OSError:
        pass  # A cache write failure never changes a valid calculation.
