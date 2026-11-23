"""Compact, versioned stock-holder index from the canonical product view."""
from __future__ import annotations

from collections import defaultdict
from datetime import date
import gzip
import hashlib
import heapq
import json
from math import isfinite
import os
from pathlib import Path
import threading
import uuid

from crowding.lake import DEFAULT_LAKE_ROOT
from crowding.product_view import load_product_view
from crowding.resources import connect as bounded_connect

from crowding.settings import STATE

HOLDER_CACHE_ROOT = STATE / 'holder-indexes'
_HOLDER_INDEXES = {}
_HOLDER_INDEX_LOCK = threading.RLock()


def _number(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and isfinite(value) and value>=0


def _holder_fingerprint(root, period, as_of, mapping, categories, view=None):
    key=f'{period.year}Q{(period.month-1)//3+1}'
    holdings=list((root/'curated/fund_holdings'/f'report_period={key}').glob('*.parquet'))
    if not holdings:
        holdings=list((root/'curated/fund_holdings').glob('*.parquet'))
    inputs=list(holdings)
    for folder in ('curated/fund_profiles','derived/fund_scale','curated/etf_profiles',
                   'curated/fund_classification_snapshot','curated/fund_product_share_observations'):
        inputs.extend((root/folder).rglob('*.parquet'))
    inputs.extend((root/'meta/fund_product_share_observations/published').glob('*.json'))
    inputs.extend(Path(__file__).with_name(name) for name in
                  ('stock_holders.py','product_view.py','fund_classification.py','verified_holdings.py'))
    parts=[str(root),str(period),str(as_of),json.dumps(mapping,sort_keys=True),json.dumps(categories,sort_keys=True)]
    for path in sorted(inputs):
        stat=path.stat();parts.append(f'{path}:{stat.st_size}:{stat.st_mtime_ns}')
    if view is not None:
        parts.append(str(view.get('cache',{}).get('input_version')))
    return hashlib.sha256('|'.join(parts).encode()).hexdigest()[:24],bool(holdings) or view is not None


def _compact_index(view, profiles, categories, period, as_of):
    """One pass; retain at most 30 holder records per stock, not all rows."""
    states={}
    sequence=0
    for position in view['all_rows']:
        symbol=position['holding_symbol'];product=position['product_symbol']
        meta=view['products'][product]
        value=position.get('market_value')
        if not _number(value):
            continue
        value=float(value)
        state=states.setdefault(symbol,{'count':0,'share_count':0,'total':0.,'companies':defaultdict(float),
                                        'top':[],'passive':0.,'enhanced':0.,'relations':defaultdict(int)})
        shares=meta.get('share_symbols') or [product]
        profile=profiles.get(product) or next((profiles[s] for s in shares if s in profiles),{})
        kind=categories.get(product,meta.get('category','unknown'))
        relation=meta.get('relation_status','unknown')
        state['count']+=1;state['share_count']+=len(shares);state['total']+=value
        state['relations'][relation]+=1
        company=profile.get('management')
        if company:state['companies'][company]+=value
        if kind in ('etf','passive_index'):state['passive']+=value
        if kind=='active_index_enhanced':state['enhanced']+=value
        # Numeric value wins; equal values prefer the smaller product code.
        priority=(value,tuple(-ord(c) for c in product))
        if len(state['top'])>=30 and priority<=state['top'][0][:2]:
            continue
        scale=meta.get('scale_yi')
        weight=position.get('weight_nav')
        row={'fund_symbol':product,'product_code':product.removesuffix('.OF'),
             'fund_name':profile.get('name'),'management':company,'fund_type':profile.get('fund_type'),
             'disclosed_market_value':value,'weight_nav_pct':weight if _number(weight) and weight<=100 else None,
             'invalid_weight_nav':weight is not None and (not _number(weight) or weight>100),
             'shares':position.get('shares'),'announce_date':position.get('announce_date'),
             'kind':kind,'share_codes':list(shares),'product_scale_yi':scale,
             'share_of_fund_nav_pct':value/(scale*1e8)*100 if _number(scale) and scale>0 else None,
             'relation_status':relation,'relation_period':meta.get('relation_period'),
             'merge_status':'verified_product' if relation=='exact' else 'approximate_product' if relation=='approx' else 'unmapped_share'}
        sequence+=1;entry=(*priority,sequence,row)
        if len(state['top'])<30:heapq.heappush(state['top'],entry)
        else:heapq.heapreplace(state['top'],entry)
    index={}
    for symbol,state in states.items():
        total=state['total']
        rows=sorted((x[3] for x in state['top']),key=lambda r:(-r['disclosed_market_value'],r['fund_symbol']))
        for row in rows:row['share_of_public_pct']=row['disclosed_market_value']/total*100 if total>0 else None
        companies=state['companies']
        company_rows=[{'name':name,'market_value':value,'share':value/total if total>0 else None}
                      for name,value in sorted(companies.items(),key=lambda x:(-x[1],x[0]))[:10]]
        index[symbol]={'rows':rows,'holder_count':state['count'],'share_holder_count':state['share_count'],
            'company_rows':company_rows,'company_coverage':{'classified_value':sum(companies.values()),'total_value':total},
            'top5_pct':sum(r['disclosed_market_value'] for r in rows[:5])/total*100 if total>0 else None,
            'company_hhi':sum((value/total)**2 for value in companies.values())*10000 if total>0 and companies else None,
            'passive_pct':state['passive']/total*100 if total>0 else None,
            'index_enhanced_pct':state['enhanced']/total*100 if total>0 else None,
            'relation_counts':dict(state['relations']),'holding_period':str(period),'as_of':str(as_of),
            'scope':'共享产品视图：当期精确关系优先，跨期验证关系标近似，未知关系份额未合并、独立保留；冲突组合排除；净值占比用同季完整份额净资产合计（非PIT）'}
    return index


def build_holder_index(*, period: date, as_of: date, lake_root: Path=DEFAULT_LAKE_ROOT,
                       share_to_master=None, fund_category=None, cache_dir=None, product_view=None):
    """Persist a compact index; caller may pass an already loaded product_view.

    Default cache is the worktree holder-indexes directory. Fingerprints
    include source publications, mapping, classification and calculation code.
    Shared-view conflicts are excluded before aggregation. No lake writes.
    """
    root=Path(lake_root).expanduser().resolve()
    mapping,categories=share_to_master or {},fund_category or {}
    fingerprint,has_input=_holder_fingerprint(root,period,as_of,mapping,categories,product_view)
    if not has_input:return {}
    directory=Path(cache_dir) if cache_dir is not None else HOLDER_CACHE_ROOT
    cached=directory/f'holders-{fingerprint}.json.gz'
    with _HOLDER_INDEX_LOCK:
        if fingerprint in _HOLDER_INDEXES:return _HOLDER_INDEXES[fingerprint]
        if cached.exists():
            try:
                with gzip.open(cached,'rt',encoding='utf-8') as handle:value=json.load(handle)
                if value.get('fingerprint')==fingerprint:
                    _HOLDER_INDEXES.clear();_HOLDER_INDEXES[fingerprint]=value['index']
                    return value['index']
            except (OSError,ValueError,EOFError,KeyError,TypeError):
                pass
        view=product_view if product_view is not None else load_product_view(
            as_of=as_of,report_period=period,lake_root=root,share_to_master=share_to_master)
        paths=sorted((root/'curated/fund_profiles').rglob('*.parquet'))
        profiles={}
        if paths:
            conn=bounded_connect()
            try:
                records=conn.execute('''SELECT symbol,name,management,fund_type
                    FROM read_parquet(?,hive_partitioning=false,union_by_name=true)
                    QUALIFY ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY fetched_at DESC NULLS LAST)=1''',
                    [[str(p) for p in paths]]).fetchall()
                profiles={symbol:{'name':name,'management':management,'fund_type':kind} for symbol,name,management,kind in records}
            finally:conn.close()
        index=_compact_index(view,profiles,categories,period,as_of)
        cached.parent.mkdir(parents=True,exist_ok=True)
        temporary=cached.with_suffix(f'.{os.getpid()}.{uuid.uuid4().hex}.tmp')
        try:
            with gzip.open(temporary,'wt',encoding='utf-8',compresslevel=3) as handle:
                json.dump({'fingerprint':fingerprint,'index':index},handle,ensure_ascii=False,default=str,allow_nan=False,separators=(',',':'))
            os.replace(temporary,cached)
        finally:temporary.unlink(missing_ok=True)
        _HOLDER_INDEXES.clear();_HOLDER_INDEXES[fingerprint]=index
        return index


def stock_holders(symbol: str, *, period: date, as_of: date, lake_root: Path=DEFAULT_LAKE_ROOT,
                  share_to_master: dict[str,str]|None=None, fund_category: dict[str,str]|None=None,
                  cache_dir=None) -> dict:
    index=build_holder_index(period=period,as_of=as_of,lake_root=lake_root,
        share_to_master=share_to_master,fund_category=fund_category,cache_dir=cache_dir)
    return index.get(symbol,{'rows':[],'reason':'所选持仓期暂无该股已发布持仓'})
