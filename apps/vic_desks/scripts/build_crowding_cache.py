#!/usr/bin/env python3
"""Build a validated candidate in the current worktree from read-only lake inputs."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, time as wall_time, timezone
import hashlib
import fcntl
import json
import os
import shutil
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))


def validate_stock_pages(pages):
    hp = pages.get('hp', {})
    lookup = {r['symbol']: r for r in hp.get('rows', [])}
    for start, page in pages.items():
        for row in page.get('rows', []):
            high = row.get('high20_pct')
            if high is not None and high > 1e-10:
                raise ValueError(f"positive high20: {row['symbol']} {high}")
            base = lookup.get(row['symbol'], {})
            for key in ('high20_pct', 'drawdown_pct', 'score', 'dims', 'fund_count'):
                if row.get(key) != base.get(key):
                    raise ValueError(f"non-return field changed with start: {start} {row['symbol']} {key}")
    return {'stock_count':len(lookup), 'positive_high20':0, 'cross_start_invariants':'passed'}


def build_fund_scale_candidate(args) -> str:
    """Run with the lake SDK interpreter; publish only under this task's runtime."""
    from multi_asset_lake.config import load_config
    from multi_asset_lake.query import load
    from crowding.candidate import SCHEMA, candidate_version, make_fund_scale
    from crowding.page_cache import active_manifest, atomic_json, load as load_page
    from crowding.settings import BUILD_MODE, CANDIDATE_ROOT, FROZEN_VERSION
    from crowding.verified_holdings import _read_published_relations

    if BUILD_MODE:
        raise ValueError('请在冻结读取模式构建单项候选')
    base = active_manifest()
    if not base or not args.lake_config or not args.knowledge_cutoff:
        raise ValueError('需要冻结基础版本、--lake-config 和 --knowledge-cutoff')
    period = args.fund_scale_candidate
    as_of = date.fromisoformat(args.as_of or date.today().isoformat())
    knowledge = datetime.fromisoformat(args.knowledge_cutoff)
    holding_period = base['metadata']['holding_period']
    if knowledge.tzinfo is None or not date.fromisoformat(holding_period) < date.fromisoformat(period) <= as_of:
        raise ValueError('需带时区的知悉时刻，且规模报告期必须晚于基础持仓期、不晚于观察日')

    product_sets = []
    for start in ('hp', 'qe', 'd5', 'd20'):
        data = load_page(FROZEN_VERSION, 'funds', start)['data']
        if data['holding_period'] != holding_period:
            raise ValueError(f'冻结基金页持仓期冲突：{start}')
        product_sets.append({r['display_code']: r['relation_status'] for r in data['funds']['rows']})
    if any(items != product_sets[0] for items in product_sets[1:]):
        raise ValueError('冻结基金产品样本随收益起点改变')
    products = product_sets[0]

    cfg = load_config(args.lake_config)
    scale_path = cfg.data_root / 'derived/fund_scale/part-merged.parquet'
    digest = hashlib.sha256(scale_path.read_bytes()).hexdigest()
    known = {code for code, status in products.items() if status == 'exact'}
    observations, conflicts = _read_published_relations(
        cfg.data_root, as_of=as_of, knowledge_cutoff=knowledge, report_period=date.fromisoformat(period),
        master_codes=sorted(code.removesuffix('.OF') for code in known),
    )
    conflict_codes = {row['master_code'] + '.OF' for row in conflicts}
    multi_share_receipts = {r['observation_id'] for r in observations if r['share_code'] != r['master_code']}
    rows = [dict(row, report_period=period) for row in observations if row['master_code'] + '.OF' in known - conflict_codes
            and (row['single_class_evidence_verified'] or row['observation_id'] in multi_share_receipts)]
    share_codes = sorted({row['share_code'] + '.OF' for row in rows})
    if not share_codes:
        raise ValueError('候选报告期没有匹配的正式份额关系')
    scales = load('fund_scale', start=period, end=period, symbols=share_codes, config=cfg)
    if hashlib.sha256(scale_path.read_bytes()).hexdigest() != digest:
        raise ValueError('季度规模表在读取期间变动')
    result = make_fund_scale(known, rows, scales.iter_rows(named=True), report_period=period)
    result.update({code: {'status': 'no_verified_period_relation'} for code in products.keys() - known})
    for code in result.keys() & conflict_codes:
        if result[code]['status'] == 'no_verified_period_relation':
            result[code] = {'status': 'ambiguous_period_relation'}
    coverage = dict(Counter(r['status'] for r in result.values()))
    if not coverage.get('observed_non_pit'):
        raise ValueError('候选没有可用的同季产品规模')

    payload = {
        'schema': SCHEMA, 'status': 'complete', 'parent_version': FROZEN_VERSION,
        'parent_fingerprint': base['input_fingerprint'], 'holding_period': holding_period,
        'report_period': period, 'relation_as_of': as_of.isoformat(), 'relation_knowledge_cutoff': knowledge.isoformat(),
        'fund_scale_input_sha256': digest, 'coverage': coverage, 'fund_scale': result,
    }
    version = candidate_version(payload)
    directory = CANDIDATE_ROOT / version
    directory.mkdir(parents=True, exist_ok=True)
    atomic_json(directory / 'manifest.json', {**payload, 'version': version})
    print(json.dumps({'version': version, 'path': str(directory), 'coverage': coverage}, ensure_ascii=False))
    return version


def apply_daily_fund_scale(version, root, as_of, mother):
    """Apply the same verified share-class rule to the rebuilt fund universe."""
    from crowding import page_cache
    from crowding.candidate import apply_fund_scale, make_fund_scale
    from crowding.resources import connect
    from crowding.verified_holdings import _read_published_relations

    pages = [page_cache.load(version, 'funds', start) for start in page_cache.DAILY_STARTS]
    data = [page['data'] for page in pages]
    holding = data[0]['holding_period']
    products = {r['display_code']: r['relation_status'] for r in data[0]['funds']['rows']}
    if not products or any(d['holding_period'] != holding or
                           {r['display_code']: r['relation_status'] for r in d['funds']['rows']} != products
                           for d in data):
        raise ValueError('日更基金产品样本或持仓期随收益起点改变')
    known = {code for code, status in products.items() if status == 'exact'}
    cutoff = datetime.combine(as_of, wall_time.max, tzinfo=ZoneInfo('Asia/Shanghai'))
    scale_files = sorted(str(p) for p in (root / 'derived/fund_scale').rglob('*.parquet'))
    if not scale_files:
        raise ValueError('正式基金规模 Parquet 缺失')
    with connect() as conn:
        periods = conn.execute("""SELECT DISTINCT CAST(report_period AS DATE) AS period
            FROM read_parquet(?, hive_partitioning=false, union_by_name=true)
            WHERE period > CAST(? AS DATE) AND period <= ? AND src = 'eastmoney_gmbd'
            ORDER BY period DESC""", [scale_files, holding, as_of]).fetchall()
        for (period,) in periods:
            observations, conflicts = _read_published_relations(
                root, as_of=as_of, knowledge_cutoff=cutoff, report_period=period,
                master_codes=sorted(code.removesuffix('.OF') for code in known),
            ) if known else ([], [])
            blocked = {row['master_code'] for row in conflicts}
            multi = {row['observation_id'] for row in observations if row['share_code'] != row['master_code']}
            rows = [dict(row, report_period=period) for row in observations if row['master_code'] not in blocked
                    and (row['single_class_evidence_verified'] or row['observation_id'] in multi)]
            share_codes = sorted({row['share_code'] + '.OF' for row in rows})
            cursor = conn.execute("""SELECT symbol, report_period, scale_yi, src
                FROM read_parquet(?, hive_partitioning=false, union_by_name=true)
                WHERE CAST(report_period AS DATE) = ? AND symbol = ANY(?)""", [scale_files, period, share_codes])
            scales = [dict(zip(('symbol', 'report_period', 'scale_yi', 'src'), row)) for row in cursor.fetchall()]
            result = make_fund_scale(known, rows, scales, report_period=period.isoformat())
            result.update({code: {'status': 'no_verified_period_relation'} for code in products.keys() - known})
            for code in result.keys() & {master + '.OF' for master in blocked}:
                if result[code]['status'] == 'no_verified_period_relation':
                    result[code] = {'status': 'ambiguous_period_relation'}
            coverage = dict(Counter(row['status'] for row in result.values()))
            if coverage.get('observed_non_pit'):
                candidate = {'holding_period': holding, 'report_period': period.isoformat(),
                             'fund_scale': result, 'coverage': coverage}
                for start, page in zip(page_cache.DAILY_STARTS, pages):
                    page_cache.store(version, 'funds', start, apply_fund_scale(page, candidate))
                return period.isoformat(), coverage
    if holding < mother['report_period'] <= as_of.isoformat() and products.keys() & mother['fund_scale'].keys():
        raise ValueError('正式湖未能复现母版较晚报告期的已验证基金规模')
    return None, {}


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument('--starts', nargs='+', default=['hp','qe','d5','d20'], choices=['hp','qe','d5','d20'])
    parser.add_argument('--include-funds',action='store_true')
    parser.add_argument('--include-industries',action='store_true')
    parser.add_argument('--only',nargs='+',choices=['overview','stocks','unheld','funds','industries','index'])
    parser.add_argument('--force',action='store_true')
    parser.add_argument('--as-of')
    parser.add_argument('--daily', action='store_true', help='build and publish all six pages as a standalone version')
    parser.add_argument('--base-version', help='existing fund-scale v1 or full daily version')
    parser.add_argument('--fund-scale-candidate', metavar='REPORT_PERIOD')
    parser.add_argument('--lake-config', type=Path)
    parser.add_argument('--knowledge-cutoff', help='ISO timestamp for the published relation evidence')
    args=parser.parse_args()
    if args.daily and (not args.base_version or not args.as_of or args.fund_scale_candidate
                       or args.only or args.force or set(args.starts) != {'hp', 'qe', 'd5', 'd20'}):
        parser.error('--daily 需要 --base-version 和 --as-of，且必须完整构建六页及四个收益起点')
    if args.base_version and not args.daily:
        parser.error('--base-version 只用于 --daily')
    if args.fund_scale_candidate:
        build_fund_scale_candidate(args)
        return 0
    os.environ['CROWDING_CACHE_BUILD']='1'
    if args.as_of:
        date.fromisoformat(args.as_of)
        os.environ['CROWDING_AS_OF']=args.as_of
    from crowding import page_cache
    from crowding.lake import DEFAULT_LAKE_ROOT
    from crowding.settings import CANDIDATE_ROOT
    root = DEFAULT_LAKE_ROOT
    if not os.environ.get('YUNALPHA_LAKE_ROOT') or not (root / 'curated').is_dir():
        parser.error('请指定含 curated 的 YUNALPHA_LAKE_ROOT；只读构建候选')
    page_cache.CACHE_ROOT.mkdir(parents=True,exist_ok=True)
    lock=open(page_cache.CACHE_ROOT/'build.lock','a+')
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        print('Another crowding cache build is running',flush=True)
        return 2
    started=time.monotonic()
    code_version=page_cache.calculation_version()
    fingerprint=page_cache.snapshot_fingerprint(root)
    mother = None
    if args.daily:
        from crowding.candidate import load_candidate
        base = page_cache.daily_manifest(args.base_version)
        base_version = base['base_version'] if base else args.base_version
        mother = load_candidate(base_version)
        version = hashlib.sha256('|'.join((page_cache.DAILY_SCHEMA, base_version, args.as_of,
            fingerprint, code_version, hashlib.sha256(Path(__file__).read_bytes()).hexdigest())).encode()).hexdigest()[:16]
        final = CANDIDATE_ROOT / version
        previous = page_cache.daily_manifest(version)
        if previous:
            print(json.dumps({'status': 'reused', 'version': version, 'path': str(final),
                              'elapsed_seconds': round(time.monotonic()-started, 2),
                              'metadata': previous['metadata'], 'fund_scale_period': previous['fund_scale_period'],
                              'failures': []}, ensure_ascii=False), flush=True)
            return 0
        if final.exists():
            raise ValueError(f'版本目录已存在，但不属于完整日更包：{final}')
    else:
        version=page_cache.lake_version(root,args.as_of or date.today().isoformat())
    os.environ['CROWDING_CACHE_VERSION']=version
    from crowding import server
    pages=list(dict.fromkeys(args.only or ['stocks','unheld','overview','funds','industries','index']))
    results=[]; failures=[]; current_task=None
    def progress(status):
        page_cache.atomic_json(page_cache.CACHE_ROOT/'build-progress.json',{
            'version':version,'status':status,'updated_at':datetime.now(timezone.utc).isoformat(),
            'elapsed_seconds':round(time.monotonic()-started,2),'current_task':current_task,'tasks':results,'failures':failures})
    current_task={'page':'snapshot','started_at':datetime.now(timezone.utc).isoformat()}
    progress('running')
    snap=server.get_snapshot()
    if args.force:
        for page in pages:
            for start in (['default'] if page=='index' else args.starts):
                page_cache.invalidate(version,{'unheld':'unheld_stocks'}.get(page,page),start)
    for page in pages:
        for start in (['hp'] if page=='index' else args.starts):
            then=time.monotonic()
            current_task={'page':page,'start':start,'started_at':datetime.now(timezone.utc).isoformat()}
            progress('running')
            try:
                fn={'stocks':server.get_stock_page,'unheld':server.get_unheld_stock_page,
                    'overview':server.get_overview_page,'funds':server.get_funds_page,
                    'industries':server.get_industries_page,'index':lambda _:server.get_index_page()}[page]
                fn(start)
                item={'page':page,'start':start,'status':'ok','seconds':round(time.monotonic()-then,2)}
            except Exception as exc:
                item={'page':page,'start':start,'status':'failed','error':f'{type(exc).__name__}: {exc}'}
                failures.append(item)
            results.append(item);progress('running');print(json.dumps(item,ensure_ascii=False),flush=True)
    if not failures and len(pages)==6:
        then=time.monotonic()
        try:
            batch=server.get_batch_market(snap)
            for row in batch.get('rows',[]):
                page_cache.store(version,'stock_market_inputs',row['symbol'],{'row':row,'metadata':batch.get('metadata',{})})
            holders=server.get_holder_inputs(snap)
            for symbol in (r['symbol'] for r in batch.get('rows',[])):
                page_cache.store(version,'stock_holder_inputs',symbol,holders.get(symbol,{}))
            results.append({'page':'holder_inputs','status':'ok','stocks':len(holders),'seconds':round(time.monotonic()-then,2)})
        except Exception as exc:
            failures.append({'page':'holder_inputs','status':'failed','error':f'{type(exc).__name__}: {exc}'})
        progress('running')
    current_task=None
    checks={}
    if not failures and set(args.starts)==set(server.START_MAP) and 'stocks' in pages:
        try:
            checks=validate_stock_pages({start:server.get_stock_page(start) for start in args.starts})
        except Exception as exc:
            failures.append({'page':'validation','error':f'{type(exc).__name__}: {exc}'})
    summary={'version':version,'elapsed_seconds':round(time.monotonic()-started,2),'failures':failures,
             'validation':checks,'tasks':results,'cache_schema':'crowding-generation-v2'}
    if page_cache.calculation_version() != code_version:
        failures.append({'page':'validation','error':'calculation code changed during build'})
    if fingerprint != page_cache.snapshot_fingerprint(root):
        failures.append({'page':'validation','error':'lake inputs changed during build'})
    if not failures and len(pages)==6 and set(args.starts)==set(server.START_MAP):
        try:
            if args.daily:
                period, coverage = apply_daily_fund_scale(version, root, date.fromisoformat(args.as_of), mother)
                staging = page_cache.CACHE_ROOT / version
                (staging / 'snapshots').mkdir(exist_ok=True)
                name = f'{fingerprint}.json.gz'
                shutil.copyfile(page_cache.CACHE_ROOT / 'snapshots' / name, staging / 'snapshots' / name)
                summary.update(schema=page_cache.DAILY_SCHEMA, base_version=base_version,
                               fund_scale_period=period, fund_scale_coverage=coverage)
            page_cache.complete_version(version,{**summary,'lake_root':str(root),
                'input_fingerprint':fingerprint,'calculation_version':code_version,
                'metadata':snap['metadata']})
            if args.daily:
                final.parent.mkdir(parents=True, exist_ok=True)
                staging.rename(final)
        except Exception as exc:
            failures.append({'page':'publication','error':str(exc)})
    summary['status']='failed' if failures else 'complete'
    summary['elapsed_seconds'] = round(time.monotonic()-started, 2)
    page_cache.atomic_json(page_cache.CACHE_ROOT/'build-summary.json',summary)
    progress(summary['status'])
    result = {'version':version,'status':summary['status'],'elapsed_seconds':summary['elapsed_seconds'],'failures':failures}
    if args.daily and not failures:
        result.update(path=str(final), metadata=snap['metadata'], fund_scale_period=period)
    print(json.dumps(result,ensure_ascii=False),flush=True)
    return 1 if failures else 0


if __name__=='__main__':
    raise SystemExit(main())
