"""Local, read-only crowding board; deliberately separate from fixture replay."""
from __future__ import annotations

import json
import copy
import gzip
import hashlib
import mimetypes
import os
import re
import threading
import time
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from functools import lru_cache
from statistics import median
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import duckdb
from crowding.resources import connect as bounded_connect

from crowding.lake import LakeSnapshot, LakeSnapshotConfig
from crowding.candidate import apply_fund_scale, load_candidate
from crowding.settings import BUILD_MODE, CANDIDATE_ROOT, FROZEN_VERSION, LAKE_ROOT, SERVING_VERSION
from crowding.market import _parts, market_data
from crowding.market_funds import market_funds
from crowding.fund_panel import fund_panel, refresh_return_window
from crowding.fund_positions import fund_position_history
from crowding.industry_panels import top100_industry_weights
from crowding.index_panels import percentile_rank, top10_weight_history
from crowding.stock_holders import build_holder_index
from crowding.industry_history import industry_history
from crowding.liquidity import turnover_20d
from crowding.industry_benchmark import industry_benchmark
from crowding.industry_deviation import industry_deviation
from crowding.crowd_index import crowd_index
from crowding.board_metrics import board_metrics
from crowding.funding import funding_30d
from crowding.overview_returns import overview_returns, unheld_candidates
from crowding.overlap_index import overlap_index
from crowding.unheld import unheld_stocks, unheld_market
from crowding.dashboard_metrics import stock_rows as compose_stock_rows
from crowding.stock_extras import previous_period_deltas
from crowding.daily_risk import previous_day_risk
from crowding.top10_changes import split_adjustments
from crowding.page_cache import (
    lake_version as cache_version,
    get_or_build as cache_get_or_build,
    load_snapshot,
    save_snapshot,
    snapshot_fingerprint,
    active_manifest, daily_manifest, load as load_cache,
)

WEB = Path(__file__).parent / "web"
BROWSER_PREFIX = "/workspaces/crowding"
_cache: dict = {}
_snapshot_origin = 'memory:empty'
_snapshot_input_fingerprint: str | None = None
_snapshot_build_count = 0
START_MAP = {'hp': '持仓期以来', 'qe': '季末以来', 'd5': '近 5 日', 'd20': '近 20 日'}
DIM_ALIAS = {'crowding': 'crowd', 'liquidity': 'liq', 'adding': 'chg', 'price': 'px', 'money': 'flow'}
_liquidity_lock = threading.Lock()
_liquidity_cache: dict = {}
_board_lock = threading.Lock()
_board_cache: dict = {}
_previous_snapshot_cache: dict = {}
_api_slot = threading.BoundedSemaphore(1)


def _field_coverage(rows: list[dict], fields: tuple[str, ...]) -> dict:
    total = len(rows)
    return {
        field: {'status':'observed' if total and all(row.get(field) is not None for row in rows) else 'partial' if any(row.get(field) is not None for row in rows) else 'unavailable',
                'reason':'availability_count_only_not_a_completeness_guarantee', 'scope':'published_local_sample',
                'available': sum(row.get(field) is not None for row in rows),
                'total': total,
                'pct': round(sum(row.get(field) is not None for row in rows) / total * 100, 2) if total else None}
        for field in fields
    }


def _lake_root() -> Path:
    return LAKE_ROOT


@lru_cache(maxsize=1)
def _serving_daily():
    return None if BUILD_MODE or SERVING_VERSION == FROZEN_VERSION else daily_manifest(SERVING_VERSION)


def _page_version(root: Path, as_of: str) -> str:
    if not BUILD_MODE:
        return SERVING_VERSION if _serving_daily() else FROZEN_VERSION
    return os.environ.get('CROWDING_CACHE_VERSION') or cache_version(root, as_of)


def _page_cache_get(version: str, name: str, key: str, builder):
    if BUILD_MODE:
        return cache_get_or_build(version, name, key, builder)
    if name == 'stock_detail':
        # Only joins the frozen per-stock inputs and selected stock table.
        return builder()
    value = load_cache(version, name, key)
    if value is None:
        raise RuntimeError(f'公募抱团版本 {version} 缺少缓存文件：{name}/{key}')
    return value


def _build_base_snapshot() -> tuple[dict, float, Path]:
    root = _lake_root()
    fingerprint = snapshot_fingerprint(root)
    as_of = os.environ.get('CROWDING_AS_OF')
    with LakeSnapshot(LakeSnapshotConfig(lake_root=root, as_of=date.fromisoformat(as_of) if as_of else None)) as view:
        value = view.snapshot()
        value['_relation_map'] = view.relation_map
        value['_fund_category'] = {row['symbol']: row['category'] for row in view.classification.get('rows', [])}
    if snapshot_fingerprint(root) != fingerprint:
        raise RuntimeError('湖输入在构建期间变动')
    path = save_snapshot(root, value, fingerprint=fingerprint)
    return value, time.time(), path


def get_snapshot() -> dict:
    global _cache, _snapshot_origin, _snapshot_input_fingerprint, _snapshot_build_count
    if _cache:
        return _cache
    if BUILD_MODE:
        restored = load_snapshot(_lake_root())
        if not restored:
            restored = _build_base_snapshot()
            _snapshot_build_count += 1
    else:
        daily = _serving_daily()
        manifest = daily or active_manifest()
        if not manifest:
            raise RuntimeError(f'缺少公募抱团冻结版本 {FROZEN_VERSION}')
        restored = load_snapshot(_lake_root(), fingerprint=manifest['input_fingerprint'],
                                 directory=CANDIDATE_ROOT / SERVING_VERSION if daily else None)
        if not restored:
            raise RuntimeError('公募抱团版本的基础快照不可读')
        if daily and restored[0]['metadata'] != manifest['metadata']:
            raise RuntimeError('公募抱团日更版本快照与清单不符')
        if SERVING_VERSION != FROZEN_VERSION and not daily:
            candidate = load_candidate(SERVING_VERSION)
            if candidate['parent_fingerprint'] != manifest['input_fingerprint']:
                raise RuntimeError('公募抱团候选与冻结基础快照不匹配')
    _cache, _, path = restored
    _snapshot_origin = (f'disk:{path.name}' if BUILD_MODE else
                        f'daily:{SERVING_VERSION}' if daily else f'frozen:{FROZEN_VERSION}')
    _snapshot_input_fingerprint = path.name.removesuffix('.json.gz')
    return _cache


def get_liquidity(snap):
    day = snap['metadata'].get('market_date')
    if not day:
        raise ValueError('行情日期缺失')
    key = (day, snap['metadata'].get('holding_period'))
    with _liquidity_lock:
        cached = _liquidity_cache.get(key)
        if not cached or time.monotonic() - cached[0] >= 300:
            root = _lake_root()
            data = turnover_20d([r['symbol'] for r in snap['rows']], as_of=date.fromisoformat(day), lake_root=root)
            _liquidity_cache.clear()
            _liquidity_cache[key] = (time.monotonic(), data)
        return _liquidity_cache[key][1]


def get_board(snap, liq):
    root = _lake_root()
    meta = snap['metadata']
    key = (meta['as_of'], meta['market_date'], meta['holding_period'])
    with _board_lock:
        cached = _board_cache.get(key)
        if not cached or time.monotonic() - cached[0] >= 300:
            day = date.fromisoformat(meta['market_date'])
            funding = funding_30d([r['symbol'] for r in snap['rows']], as_of=day, lake_root=root)
            names = [r['symbol'] for r in snap['rows'] if r.get('reported_mv') is not None and r['reported_mv'] >= 100_000_000 and r.get('close')]
            market = get_batch_market(snap)
            changes = previous_period_deltas(snap, _previous_snapshot(snap))
            data = board_metrics(snap, liq, funding, market, changes=changes)
            data['metadata']['funding'] = funding['metadata']
            _board_cache.clear()
            _board_cache[key] = (time.monotonic(), data)
        return _board_cache[key][1]


def _previous_snapshot(snap: dict) -> dict:
    """Load the prior full-holding snapshot once for change metrics."""
    meta = snap['metadata']
    previous = meta.get('previous_holding_period')
    if not previous:
        return {'rows': [], 'metadata': {}}
    key = (meta.get('as_of'), previous)
    cached = _previous_snapshot_cache.get(key)
    if cached:
        return cached
    with LakeSnapshot(LakeSnapshotConfig(
        lake_root=_lake_root(),
        as_of=date.fromisoformat(meta['as_of']), holding_period=date.fromisoformat(previous),
    )) as view:
        full = view.snapshot()
    result = {
        'rows': [{field: row.get(field) for field in
                  ('symbol', 'fund_count', 'hold_float_ratio', 'hold_float_ratio_display', 'holding_mv',
                   'reported_mv', 'industry_name', 'shares', 'float_mv', 'close')}
                 for row in full.get('rows', [])],
        'metadata': full.get('metadata', {}),
    }
    adjustment = split_adjustments(_lake_root(), date.fromisoformat(previous),
                                  date.fromisoformat(meta['holding_period']), date.fromisoformat(meta['as_of']))
    for row in result['rows']:
        symbol = row['symbol']
        if not adjustment['available'] or symbol in adjustment['unavailable_symbols']:
            row['hold_float_ratio'] = None
            row['change_quality'] = 'corporate_action_not_comparable'
        elif row.get('hold_float_ratio') is not None:
            row['hold_float_ratio'] *= adjustment['multipliers'].get(symbol, 1.0)
    result['metadata']['corporate_actions'] = adjustment['coverage']
    _previous_snapshot_cache.clear()
    _previous_snapshot_cache[key] = result
    return result


def _return_baseline(meta: dict, start_key: str) -> tuple[date | None, list[date]]:
    market_date = date.fromisoformat(meta['market_date']) if meta.get('market_date') else None
    if not market_date:
        return None, []
    root = _lake_root()
    paths = [str(p) for p in (root / 'curated' / 'trading_calendar').rglob('*.parquet')]
    sessions = []
    if paths:
        conn = bounded_connect()
        try:
            sessions = [row[0] for row in conn.execute(
                "SELECT DISTINCT trade_date FROM read_parquet(?,hive_partitioning=false) "
                "WHERE is_trading AND trade_date<=? ORDER BY trade_date", [paths, market_date]
            ).fetchall()]
        finally:
            conn.close()
    if not sessions:
        return None, []
    if start_key == 'hp':
        requested = date.fromisoformat(meta['holding_period'])
    elif start_key == 'qe':
        requested = date(market_date.year, ((market_date.month - 1) // 3) * 3 + 1, 1) - timedelta(days=1)
    else:
        offset = 5 if start_key == 'd5' else 20
        requested = sessions[-offset - 1] if len(sessions) > offset else None
    baseline = next((d for d in reversed(sessions) if requested is not None and d <= requested), None)
    return baseline, sessions


def get_stock_page(start_key: str) -> dict:
    if start_key not in START_MAP:
        raise ValueError('invalid start')
    snap = get_snapshot()
    meta = snap['metadata']
    version = _page_version(_lake_root(), meta['as_of'])
    return _page_cache_get(version, 'stocks', start_key, lambda: _build_stock_page(snap, start_key))


def get_batch_market(snap: dict) -> dict:
    root = _lake_root()
    meta = snap['metadata']
    if _serving_daily():
        return _page_cache_get(SERVING_VERSION, 'market_inputs', 'all', None)
    baseline, sessions = _return_baseline(meta, 'hp')
    if not baseline:
        return {'rows': [], 'metadata': {'reason': 'missing_calendar'}}
    oldest = min(baseline, sessions[-21] if len(sessions) >= 21 else sessions[0])
    version = _page_version(root, meta['as_of'])
    return _page_cache_get(version, 'market_inputs', 'all', lambda: market_data(
        list(dict.fromkeys([row['symbol'] for row in snap['rows']] + list(unheld_candidates(snap,root,date.fromisoformat(meta['market_date']))))), lake_root=root,
        as_of=date.fromisoformat(meta['market_date']), period_start=baseline, series_start=oldest))


def _build_stock_page(snap: dict, start_key: str) -> dict:
    root = _lake_root()
    meta = snap['metadata']
    baseline, sessions = _return_baseline(meta, start_key)
    if baseline is None:
        raise ValueError('无法确定收益基准日')
    liq = get_liquidity(snap)
    board = get_board(snap, liq)
    raw_market = get_batch_market(snap)
    if start_key != 'hp':
        hp = get_stock_page('hp')
        lookup = {row['symbol']: row for row in raw_market.get('rows', [])}
        changed = []
        for item in hp['rows']:
            row = dict(item)
            prices = {point['date']: point.get('close') for point in lookup.get(row['symbol'], {}).get('price_margin_series', [])}
            old, latest = prices.get(baseline.isoformat()), prices.get(meta['market_date'])
            row['return_pct'] = latest / old - 1 if latest is not None and old is not None and old > 0 else None
            changed.append(row)
        return {**hp, 'rows': changed, 'metadata': {**hp['metadata'], 'start': start_key,
                'start_label': START_MAP[start_key], 'baseline': baseline.isoformat()}}
    market = raw_market
    composed = compose_stock_rows(
        snapshot=snap, previous_snapshot=_previous_snapshot(snap), board=board,
        liquidity=liq, market=market, as_of=date.fromisoformat(meta['as_of']),
        lake_root=root, include_top10_changes=True,
    )
    composed['metadata'].update({
        'start': start_key, 'start_label': START_MAP[start_key],
        'baseline': baseline.isoformat(), 'market_date': meta['market_date'],
        'holding_period': meta['holding_period'],
        'unknown_product_policy': 'unverified shares remain individual observations; no cross-class merge',
        'cache_version': _page_version(root, meta['as_of']),
    })
    composed['coverage'] = _field_coverage(composed['rows'], (
        'score', 'fund_count', 'n_corps', 'top5_pct', 'passive_pct',
        'turnover5_pct', 'return_pct', 'pe3y', 'pb3y',
        'top10_chg_pct', 'top10_chg_mv', 'large30_pct', 'margin30_pct',
    ))
    composed['sources'] = [
        'fund_holdings', 'fund_product_share_observations', 'daily_bars',
        'adj_factors', 'valuation_metrics', 'daily_free_float_shares',
        'moneyflow', 'fund_flow', 'margin_trading', 'industry_members',
        'industry_taxonomy',
    ]
    return composed


def get_unheld_stock_page(start_key: str) -> dict:
    snap = get_snapshot()
    meta = snap['metadata']
    root = _lake_root()
    version = _page_version(root, meta['as_of'])
    return _page_cache_get(version, 'unheld_stocks', start_key,
                           lambda: _build_unheld_stock_page(snap, start_key, root))


def _build_unheld_stock_page(snap: dict, start_key: str, root: Path) -> dict:
    source = unheld_stocks(snap, root=root)
    market = unheld_market(snap, root=root, period=START_MAP[start_key], candidates=source)
    rows = []
    for item in source.get('rows', []):
        symbol = item['symbol']
        quote = (market.get('rows') or {}).get(symbol, {})
        rows.append({
            'symbol': symbol, 'name': item.get('name'), 'industry_name': item.get('industry_name'),
            'score': None, 'band': None, 'dims': {}, 'dims_missing': [],
            'fund_count': 0, 'fund_change': None, 'n_corps': 0,
            'reported_mv': None, 'holding_mv': None, 'hold_float_ratio': None,
            'hold_float_ratio_display': None, 'hold_float_ratio_basis': None,
            'ratio_change': None, 'top5_pct': None, 'passive_pct': None,
            'liquidation': None, 'avg_turnover': None, 'turnover5_pct': None,
            'return_pct': quote.get('return'), 'drawdown_pct': quote.get('drawdown'),
            'high20_pct': None, 'margin30_pct': quote.get('margin'), 'large30_pct': None,
            'pe3y': None, 'pb3y': None, 'top10_chg_pct': None, 'top10_cover_pct': None,
            'top10_chg_mv': None, 'pool': False, 'sample_presence': 'not_observed',
        })
    return {'rows': rows, 'metadata': {**snap.get('metadata', {}), **(source.get('metadata') or {}),
                                       **(market.get('metadata') or {})}}


def get_funds_page(start_key: str) -> dict:
    snap = get_snapshot()
    meta = snap['metadata']
    root = _lake_root()
    version = _page_version(root, meta['as_of'])
    page = _page_cache_get(version, 'funds', start_key,
                           lambda: _build_funds_page(snap, start_key, root))
    if not BUILD_MODE and SERVING_VERSION != FROZEN_VERSION and not _serving_daily():
        return apply_fund_scale(page, load_candidate(SERVING_VERSION))
    return page


def _build_funds_page(snap: dict, start_key: str, root: Path) -> dict:
    meta = snap['metadata']
    baseline, _ = _return_baseline(meta, start_key)
    if start_key != 'hp':
        base=get_funds_page('hp')
        data=refresh_return_window(base['data'],baseline,lake_root=root,
            as_of=date.fromisoformat(meta['as_of']),market_date=date.fromisoformat(meta['market_date']))
        data['start']=start_key
        return {**base,'data':data}
    panel = fund_panel(lake_root=root, as_of=date.fromisoformat(meta['as_of']),
                       return_start=baseline, board_snapshot=snap)
    raw_funds = _page_cache_get(_page_version(root,meta['as_of']), 'fund_flow_inputs', 'all',
        lambda: market_funds(lake_root=root, as_of=date.fromisoformat(meta['market_date'])))
    windows = []
    for count in (5, 20, 60):
        source = raw_funds.get('windows', {}).get(str(count), {})
        detail = source.get('details', {})
        etf = detail.get('equity_etf_flow_estimate', {})
        margin = detail.get('margin_net_buy')
        south = detail.get('southbound_net_buy')
        ipo = detail.get('ipo_funding')
        holder = detail.get('holdertrade_observed', {})
        holder_value = holder.get('sell_estimate_cny')
        inflow = [
            {'key': 'etf_equity', 'label': '股票类ETF净申购', 'value': etf.get('partial_value_cny'),
             'as_of': etf.get('end_date'), 'tracked': etf.get('partial_value_cny') is not None,
             'quality': 'partial' if etf.get('partial_value_cny') is not None and not etf.get('complete') else 'exact' if etf.get('complete') else 'missing'},
            {'key': 'margin_net', 'label': '融资净买入', 'value': margin,
             'as_of': detail.get('margin_end_date'), 'tracked': margin is not None},
        ]
        outflow = [
            {'key': 'south_net', 'label': '南向净买入', 'value': south,
             'as_of': source.get('end_date'), 'tracked': south is not None},
            {'key': 'ipo', 'label': 'IPO募资', 'value': ipo,
             'as_of': source.get('end_date'), 'tracked': ipo is not None},
            {'key': 'dec_est', 'label': '股东减持(估)', 'value': holder_value,
             'as_of': source.get('end_date'), 'tracked': holder_value is not None, 'quality': 'estimate'},
        ]
        component_meta = {item['key']:item for item in source.get('net_components', [])}
        for item in inflow + outflow:
            item.update({k:v for k,v in component_meta.get(item['key'], {}).items()
                         if k in ('quality','reason','coverage')})
            component = component_meta.get(item['key'], {})
            item['observed_value'] = item.get('value')
            item['observed_as_of'] = item.get('as_of')
            item['value'] = component.get('value')
            item['tracked'] = item['value'] is not None
            item['as_of'] = source.get('common_cutoff') if item['tracked'] else item['observed_as_of']
            item['start_date'] = source.get('start_date') if item['tracked'] else None
            if item['value'] is None:
                item['quality'] = 'missing'
        repurchase = detail.get('repurchase_plan', {})
        windows.append({
            'n': count, 'inflow': inflow, 'outflow': outflow, 'net': source.get('net'),
            'strict_net': source.get('net'), 'estimated_net': source.get('partial_net_estimate'),
            'net_quality': source.get('net_quality','unavailable'),
            'common_cutoff':source.get('common_cutoff'), 'excluded_components':source.get('excluded_components',[]),
            'net_note':source.get('net_note'),
            'standalone': [
                {'key': 'buyback_plan', 'label': '预案公告金额观察值', 'value': repurchase.get('estimate_cny'),
                 'as_of': repurchase.get('end_date'), 'note': '公告口径，未完成方案级去重，不计入净额'},
                {'key': 'main_force', 'label': '大单及特大单净额', 'value': source.get('value'),
                 'as_of': source.get('end_date'), 'note': '大单及特大单估算口径，不计入净额'},
            ],
            'coverage': source.get('coverage', {}),
            'missing_dates': source.get('missing_dates', []),
        })
    return {'code': 0, 'message': 'success', 'data': {
        **panel,
        'start': start_key,
        'funds': {'as_of': panel.get('holding_period'),
                  'note': '主动产品；A/C 仅在完整报告证据和同组合校验通过时合并',
                  'rows': panel.get('funds', [])},
        'nav_dist': panel.get('nav_dist', {'bins': []}),
        'etfs': panel.get('etfs', {'rows': []}),
        'balance': {'windows': windows, 'southbound_side': 'outflow'},
        'balance_as_of': meta['market_date'],
        'balance_note': 'ETF与股东减持为局部估算；分项不完整时不计算净额',
        'marks': {key: meta.get(key) for key in ('as_of','holding_period','previous_holding_period','market_date','latest_price_date','latest_valuation_date')},
        'quality': {'net_flow': 'ETF/减持为局部估算；未纳入回购与主力净流入',
                    'fund_scale': '非 PIT 同季或最近披露快照'},
        'sources': ['fund_holdings', 'fund_profiles', 'fund_scale', 'fund_nav',
                    'fund_holder_structure_snapshot', 'etf_profiles', 'etf_daily_metrics',
                    'market_money_flow', 'southbound_flows', 'margin_trading',
                    'market_ipo_funding', 'market_repurchase_announcements',
                    'stock_holdertrade_announcements'],
        'coverage': {
            'fund_rows': len(panel.get('funds', [])), 'etf_rows': len(panel.get('etfs', {}).get('rows', [])),
            'balance_windows': len(windows),
            'individual_pct_available': sum(row.get('individual_pct') is not None for row in panel.get('funds', [])),
        },
        'data_version': _page_version(root, meta['as_of']),
    }}


def get_industries_page(start_key: str) -> dict:
    snap = get_snapshot()
    meta = snap['metadata']
    root = _lake_root()
    version = _page_version(root, meta['as_of'])
    page = _page_cache_get(version, 'industries', start_key,
                           lambda: _build_industries_page(snap, start_key, root))
    if BUILD_MODE or SERVING_VERSION == FROZEN_VERSION:
        return page
    # F10 can repeat the whole product's assets under multiple share codes.
    # Keep the independently computed medians; suppress ambiguous size weights.
    data = page['data']
    by_type = data['by_type']
    return {**page, 'data': {**data, 'by_type': {
        **by_type,
        'rows': [{**row, 'w': None, 'net_asset': None} for row in by_type['rows']],
        'size_weight_status': 'unavailable_mixed_asset_scope',
    }}}


def _build_industries_page(snap: dict, start_key: str, root: Path) -> dict:
    meta = snap['metadata']
    if start_key != 'hp':
        # Only returns depend on the UI interval. Holdings, classifications,
        # historical panels and same-quarter ranking are one shared input.
        result=copy.deepcopy(get_industries_page('hp'))
        grouped=defaultdict(list)
        for row in get_stock_page(start_key)['rows']:
            if row.get('industry_name') and row.get('return_pct') is not None and row.get('reported_mv'):
                grouped[row['industry_name']].append((row['return_pct'],row['reported_mv']))
        for row in result['data']['table']['rows']:
            values=grouped.get(row['name'],[]) if row['name']!='合计' else []
            weight=sum(w for _,w in values)
            row['ret_pct']=sum(v*w for v,w in values)/weight*100 if weight else None
        result['data']['start']=start_key
        return result
    stock_page = get_stock_page(start_key)
    stock_by_symbol = {row['symbol']: row for row in stock_page['rows']}
    current: dict[str, dict] = {}
    total = 0.0
    for row in snap['rows']:
        industry = row.get('industry_name')
        value = row.get('reported_mv')
        if not industry or value is None:
            continue
        item = current.setdefault(industry, {'market_value': 0.0, 'stocks': []})
        item['market_value'] += float(value)
        item['stocks'].append(row['symbol'])
        total += float(value)
    previous = _previous_snapshot(snap)
    previous_total = sum(row['reported_mv'] for row in previous['rows'] if row.get('reported_mv') is not None)
    previous_groups: dict[str, float] = {}
    for row in previous['rows']:
        if row.get('industry_name') and row.get('reported_mv') is not None:
            previous_groups[row['industry_name']] = previous_groups.get(row['industry_name'], 0.0) + float(row['reported_mv'])
    benchmark = industry_benchmark(as_of=date.fromisoformat(meta['holding_period']), lake_root=root)
    bench = {row['name']: row['benchmark_pct'] for row in benchmark.get('rows', [])}
    industry_codes = {row['name']: row['l1_code'] for row in benchmark.get('rows', [])}
    active = {symbol for symbol, category in snap.get('_fund_category', {}).items()
              if category in ('active_equity', 'active_index_enhanced')}
    top100_result = top100_industry_weights(
        as_of=date.fromisoformat(meta['as_of']), period=date.fromisoformat(meta['holding_period']),
        lake_root=root, active_symbols=active, include_metadata=True,
    )
    top100 = top100_result.get('weights', {})
    history = industry_history(as_of=date.fromisoformat(meta['as_of']), lake_root=root)
    hist_periods = [date.fromisoformat(value).strftime('%YQ') + str((date.fromisoformat(value).month - 1) // 3 + 1)
                    for value in history.get('periods', [])]
    hist_values = defaultdict(dict)
    for item in history.get('rows', []):
        raw_share = item.get('share_all_disclosed')
        hist_values[item['industry']][item['period']] = raw_share * 100 if raw_share is not None else None
    hist_overweights = defaultdict(dict)
    for period in history.get('periods', []):
        historical_bench = industry_benchmark(as_of=date.fromisoformat(period), lake_root=root)
        bp = {r['name']:r['benchmark_pct'] for r in historical_bench.get('rows', [])}
        for name, vals in hist_values.items():
            value = vals.get(period)
            hist_overweights[name][period] = value - bp[name] if value is not None and name in bp else None
    rows = []
    for industry, item in sorted(current.items(), key=lambda item: -item[1]['market_value']):
        ratio = item['market_value'] / total * 100 if total else None
        base = bench.get(industry)
        previous_ratio = previous_groups.get(industry, 0) / previous_total * 100 if previous_total else None
        stock_rows = [stock_by_symbol[symbol] for symbol in item['stocks'] if symbol in stock_by_symbol]
        returns = [(row.get('return_pct'), row.get('reported_mv')) for row in stock_rows
                   if row.get('return_pct') is not None and row.get('reported_mv')]
        ret_total = sum(weight for _, weight in returns)
        weighted_return = sum(value * weight for value, weight in returns) / ret_total if ret_total else None
        frs = [row.get('hold_float_ratio') for row in stock_rows if row.get('hold_float_ratio') is not None]
        pool_n = sum(bool(row.get('pool')) for row in stock_rows)
        rows.append({
            'name': industry, 'hold_mv': item['market_value'], 'share_pct': ratio,
            'bench_pct': base, 'ow_pp': ratio - base if ratio is not None and base is not None else None,
            'share_chg_pp': ratio - previous_ratio if ratio is not None and previous_ratio is not None else None,
            'top100_ow_pp': top100[industry] - base if industry in top100 and base is not None else None,
            'hist': [hist_values[industry].get(date.fromisoformat(value).isoformat()) for value in history.get('periods', [])],
            'pool_n': pool_n, 'fr_median_pct': median(frs) * 100 if frs else None,
            'ret_pct': weighted_return * 100 if weighted_return is not None else None,
            'quality': 'estimated' if base is None else 'derived',
        })
    total_row = {'name': '合计', 'hold_mv': total, 'share_pct': 100 if total else None,
                 'bench_pct': 100 if total else None, 'ow_pp': None, 'share_chg_pp': None,
                 'top100_ow_pp': None, 'hist': [], 'pool_n': sum(row['pool_n'] for row in rows),
                 'fr_median_pct': None, 'ret_pct': None}
    table_rows = [{'l1_code': None, **total_row}]
    table_rows.extend({'l1_code': industry_codes.get(row['name']), **row} for row in rows)
    positions = fund_position_history(as_of=date.fromisoformat(meta['as_of']), lake_root=root,
                                      share_to_master=snap.get('_relation_map'))
    position_boxes = []
    for index, box in enumerate(positions.get('boxes', [])):
        period_label = positions.get('periods', [{}])[index].get('key') if index < len(positions.get('periods', [])) else box.get('key')
        position_boxes.append({**box, 'label': period_label, 'med': box.get('median'), 'w': None})
    top100_rows = [{'name': row['name'], 'all_pp': row['ow_pp'], 'top100_pp': row['top100_ow_pp']}
                   for row in sorted(rows, key=lambda r: -(abs(r.get('ow_pp') or 0)))[:15]]
    return {'code': 0, 'message': 'success', 'data': {
        'as_of': meta['market_date'], 'start': start_key,
        'table': {'rows': table_rows,
                  'note': '全部已披露基金份额；当前分类回看；缺失基准不填零'},
        'hist': {'periods': hist_periods, 'rows': [{'name': row['name'], 'values': row['hist'], 'share_values': row['hist'],
                  'overweight_values': [hist_overweights.get(row['name'],{}).get(value) for value in history.get('periods', [])]} for row in rows]},
        'top100': {'rows': top100_rows,
                   'note': '同季已知规模前100主动产品；关系不足时以单份额保留并标近似'},
        'position': {'as_of': meta['as_of'], 'label': positions.get('label'),
                     'period': positions.get('by_type', {}).get('period'),
                     'periods': positions.get('periods', []), 'boxes': position_boxes,
                     'note': positions.get('scope')},
        'by_type': positions.get('by_type', {'rows': []}),
        'marks': {key: meta.get(key) for key in ('as_of','holding_period','previous_holding_period','market_date','latest_price_date','latest_valuation_date')},
        'quality': {'position_data': 'non-PIT 东方财富 F10 allocation snapshot',
                    'top100': top100_result.get('coverage', {})},
        'sources': ['fund_holdings', 'fund_profiles', 'fund_asset_allocation_snapshot',
                    'industry_members', 'industry_taxonomy', 'valuation_metrics'],
        'coverage': {'industries': len(rows), 'position_periods': len(positions.get('periods', [])),
                     'top100_rows': len(top100_rows), 'top100_selection': top100_result.get('coverage', {})},
        'data_version': _page_version(root, meta['as_of']),
    }}


def get_index_page() -> dict:
    snap = get_snapshot()
    meta = snap['metadata']
    root = _lake_root()
    version = _page_version(root, meta['as_of'])
    return _page_cache_get(version, 'index', 'default', lambda: _build_index_page(snap, root))


def get_stock_detail_page(symbol: str, start_key: str) -> dict:
    snap = get_snapshot()
    meta = snap['metadata']
    root = _lake_root()
    version = _page_version(root, meta['as_of'])
    return _page_cache_get(version, 'stock_detail', f'{symbol}:{start_key}',
                           lambda: _build_stock_detail_page(snap, symbol, start_key, root))


def get_stock_market_input(snap: dict,symbol: str) -> dict:
    version=_page_version(_lake_root(),snap['metadata']['as_of'])
    def build():
        batch=get_batch_market(snap)
        return {'row':next((r for r in batch.get('rows',[]) if r['symbol']==symbol),{}),
                'metadata':batch.get('metadata',{})}
    if _serving_daily():
        return load_cache(version, 'stock_market_inputs', symbol) or build()
    return _page_cache_get(version,'stock_market_inputs',symbol,build)


def get_stock_holder_input(snap: dict,symbol: str) -> dict:
    version=_page_version(_lake_root(),snap['metadata']['as_of'])
    if _serving_daily():
        value = load_cache(version, 'stock_holder_inputs', symbol)
        return value if value is not None else get_holder_inputs(snap).get(symbol, {})
    return _page_cache_get(version,'stock_holder_inputs',symbol,
        lambda:get_holder_inputs(snap).get(symbol,{}))


def get_holder_inputs(snap: dict) -> dict:
    meta=snap['metadata'];root=_lake_root()
    return _page_cache_get(_page_version(root,meta['as_of']),'holder_inputs','all',
        lambda: build_holder_index(period=date.fromisoformat(meta['holding_period']),
            as_of=date.fromisoformat(meta['as_of']),lake_root=root,
            fund_category=snap.get('_fund_category')))


def _build_stock_detail_page(snap: dict, symbol: str, start_key: str, root: Path) -> dict:
    meta = snap['metadata']
    stock_page = get_stock_page('hp')
    def find(page):
        return next((item for item in page['rows'] if item['symbol'] == symbol), None)

    row = find(stock_page)
    get_page = get_stock_page
    if row is None:
        get_page = get_unheld_stock_page
        row = find(get_page('hp'))
        if row is None:
            raise ValueError('股票不在当前已发布A股样本')
    selected = find(get_page(start_key)) or {}
    holders = get_stock_holder_input(snap,symbol)
    market = get_stock_market_input(snap,symbol)
    market_row = market.get('row',{})
    board = {'score': row.get('score'), 'percentiles': row.get('dims'),
             'dimensions': row.get('dimension_raw') or {}}
    dims = []
    labels = {'crowding':'拥挤','liquidity':'流动性','adding':'加仓','price':'价格','money':'资金'}
    weights = {'crowding':30,'liquidity':20,'adding':15,'price':20,'money':15}
    for key in ('crowding','liquidity','adding','price','money'):
        dims.append({'key':key,'label':labels[key],'weight':weights[key],
                     'raw':(board.get('dimensions') or {}).get(key),
                     'pct':(board.get('percentiles') or {}).get(key)})
    top10 = {'current_shares': selected.get('top10_current_shares'),
             'previous_shares': selected.get('top10_previous_shares'), 'chg_pct': selected.get('top10_chg_pct'),
             'cover_pct': selected.get('top10_cover_pct'), 'new_in': selected.get('top10_new_in'), 'out': selected.get('top10_out'),
             'current_period': stock_page.get('metadata',{}).get('top10_change_metadata',{}).get('current_period'),
             'previous_period': stock_page.get('metadata',{}).get('top10_change_metadata',{}).get('previous_period'),
             'note': '季报前十大同样本；覆盖率不足时仅作近似'}
    return {'code': 0, 'message': 'success', 'data': {
        'code': symbol, 'name': row.get('name'), 'l1_name': row.get('industry_name'),
        'as_of': meta['market_date'], 'trade_date': meta['market_date'], 'start': start_key,
        'marks': {key: meta.get(key) for key in ('as_of','holding_period','previous_holding_period','market_date','latest_price_date','latest_valuation_date')},
        'kpi': {'score': row.get('score'), 'band': row.get('band'),
                'fr_pct': row.get('hold_float_ratio') * 100 if row.get('hold_float_ratio') is not None else None,
                'fr_prev_pct': (row.get('hold_float_ratio') - (row.get('ratio_change') or 0)/100) * 100
                if row.get('hold_float_ratio') is not None and row.get('ratio_change') is not None else None,
                'n_products': row.get('fund_count'), 'n_products_chg': row.get('fund_change'),
                'holding_mv_now': row.get('holding_mv'), 'liq_days': row.get('liquidation'),
                'ret_pct': selected.get('return_pct') * 100 if selected.get('return_pct') is not None else None,
                'mf30_pct': selected.get('large30_pct'),
                'large30_amount': (selected.get('large30_pct') / 100 * row.get('float_mv')
                                   if selected.get('large30_pct') is not None and row.get('float_mv') is not None else None),
                'top5_pct': holders.get('top5_pct'), 'company_hhi': holders.get('company_hhi'),
                'passive_pct': holders.get('passive_pct'), 'index_enhanced_pct': holders.get('index_enhanced_pct')},
        'holders': {'top': holders.get('rows', []), 'corp_rows': holders.get('company_rows', []),
                    'note': holders.get('scope') or holders.get('note'), 'holder_count': holders.get('holder_count'),
                    'share_holder_count': holders.get('share_holder_count'),
                    'top5_pct': holders.get('top5_pct'), 'company_hhi': holders.get('company_hhi'),
                    'passive_pct': holders.get('passive_pct'),
                    'index_enhanced_pct': holders.get('index_enhanced_pct')},
        'px_margin': {'as_of': meta.get('market_date'), 'series': market_row.get('price_margin_series', []),
                      'metadata': market.get('metadata', {})},
        'dims': {'score': board.get('score'), 'band': row.get('band'), 'items': dims,
                 'missing': row.get('dims_missing')},
        'top10': top10, 'quality': {'relation': holders.get('scope'),
                                    'scale': '同一报告期产品份额合计；非 PIT',
                                    'top10': selected.get('top10_quality_flags')},
        'sources': ['fund_holdings', 'fund_profiles', 'fund_product_share_observations',
                    'fund_scale', 'daily_bars', 'adj_factors', 'valuation_metrics',
                    'moneyflow', 'fund_flow', 'margin_trading'],
        'coverage': {'holders': len(holders.get('rows', [])),
                     'companies': len(holders.get('company_rows', [])),
                     'price_points': len(market_row.get('price_margin_series', [])),
                     'top10_coverage_pct': top10.get('cover_pct')},
        'data_version': _page_version(root, meta['as_of']),
    }}


def _build_index_page(snap: dict, root: Path) -> dict:
    meta = snap['metadata']
    as_of = date.fromisoformat(meta['as_of'])
    idx = crowd_index(as_of=as_of, lake_root=root)
    overlap = overlap_index(as_of=as_of, lake_root=root)
    deviation = industry_deviation(as_of=as_of, lake_root=root, index_data=idx)
    deviation_by_period = {row['period']: row for row in deviation.get('periods', [])}
    active = {symbol for symbol, category in snap.get('_fund_category', {}).items()
              if category in ('active_equity', 'active_index_enhanced')}
    weighted = {'series': {}, 'coverage': {}}  # overlap computes the same-period NAV series
    periods = idx.get('periods', [])
    latest = periods[-1] if periods else {}
    previous_periods = periods[:-1]
    ref = next((item for item in periods if item.get('key') == '2021Q1'), {})
    series = dict(idx.get('series', {}))
    series.update(overlap.get('series', {}))
    series['w10_median_pct'] = overlap.get('series', {}).get('w10_median_pct', [])
    series['dev_pct'] = [deviation_by_period.get(item.get('period'), {}).get('top10_deviation_pct')
                         for item in periods]
    series['full_dev_pct'] = [deviation_by_period.get(item.get('period'), {}).get('full_deviation_pct')
                              for item in periods]
    top3_values = series.get('top3_pct', [])
    hhi_values = series.get('hhi', [])
    dev_values = series.get('dev_pct', [])
    jac_values = overlap.get('series', {}).get('jac_all_pct', [])
    strict_head = overlap.get('series', {}).get('jac_head_pct', [])
    head_estimates = overlap.get('series', {}).get('jac_head_estimate_pct', [])
    head_values = [v if v is not None else (head_estimates[i] if i<len(head_estimates) else None) for i,v in enumerate(strict_head)]
    w10_values = series.get('w10_median_pct', [])
    current = lambda values: values[-1] if values else None
    current_top3, current_hhi, current_dev = current(top3_values), current(hhi_values), current(dev_values)
    current_jac, current_head, current_w10 = current(jac_values), current(head_values), current(w10_values)
    def disclosing(item):
        return item.get('disclosing')
    def pct(values):
        valid = [v for p, v in zip(periods, values) if v is not None and disclosing(p) is False]
        return percentile_rank(values[-1] if values else None, valid) if len(valid) >= 8 else None
    ref_index = next((i for i,p in enumerate(periods) if p.get('key')=='2021Q1'), None)
    def ref_label(values):
        value = values[ref_index] if ref_index is not None and ref_index < len(values) else None
        return f'{value:.1f}%' if value is not None else '—'
    disclosure_label = '披露中' if disclosing(latest) is True else ('已披露样本' if disclosing(latest) is False else '披露覆盖未知')

    top_industry = latest.get('top10_industries', [{}])[0] if latest.get('top10_industries') else {}
    ref_top_industry = ref.get('top10_industries', [{}])[0] if ref.get('top10_industries') else {}
    industry_label = lambda item: (f"{item.get('name','—')} {item['share_pct']:.1f}%" if item.get('share_pct') is not None else '—')
    kpis = [
        {'key':'top3','label':'前三行业占比','value':current_top3,
         'sub':f"第 {pct(top3_values):.0f} 分位 · 21Q1 {ref_label(top3_values)} · {disclosure_label}" if pct(top3_values) is not None else '历史分位不足', 'tone':'neutral'},
        {'key':'dev','label':'行业偏离度','value':current_dev,
         'sub':f"第 {pct(dev_values):.0f} 分位 · 21Q1 {ref_label(dev_values)} · {disclosure_label}" if pct(dev_values) is not None else '历史分位不足', 'tone':'neutral'},
        {'key':'top1','label':'第一大行业','value':industry_label(top_industry),
         'sub':f"21Q1 第一大：{industry_label(ref_top_industry)} · {disclosure_label}", 'tone':'neutral'},
        {'key':'jac_all','label':'重合度 · 全部','value':current_jac,
         'sub':f"第 {pct(jac_values):.0f} 分位 · 21Q1 {ref_label(jac_values)} · {disclosure_label}" if pct(jac_values) is not None else '历史分位不足', 'tone':'neutral'},
        {'key':'jac_head','label':'重合度 · 头部 200' if strict_head and strict_head[-1] is not None else '已知规模前200估算',
         'quality':'exact' if strict_head and strict_head[-1] is not None else 'estimated','value':current_head,
         'sub':f"第 {pct(head_values):.0f} 分位 · 21Q1 {ref_label(head_values)} · {disclosure_label}" if pct(head_values) is not None else '历史分位不足', 'tone':'neutral'},
        {'key':'w10_med','label':'前十大权重中位','value':current_w10,
         'sub':f"第 {pct(w10_values):.0f} 分位 · 21Q1 {ref_label(w10_values)} · {disclosure_label}" if pct(w10_values) is not None else '规模或份额关系覆盖不足', 'tone':'neutral'},
    ]
    focus_series = (idx.get('industry_series', {}).get('top10') or [])
    full_series = {item.get('l1_code'): item for item in (idx.get('industry_series', {}).get('full') or [])}
    focus_details = [{
        'l1_code': item.get('l1_code'), 'name': item.get('name'),
        'top10': [point.get('share_pct') for point in item.get('points', [])],
        'full': [point.get('share_pct') for point in full_series.get(item.get('l1_code'), {}).get('points', [])],
    } for item in focus_series]
    focus_codes=[item['l1_code'] for item in focus_details if item.get('name') in ('电子','食品饮料','医药生物','电力设备')]
    return {'code': 0, 'message': 'success', 'data': {
        'as_of': idx.get('as_of'), 'latest': latest.get('key'), 'building': False,
        'periods': [{'key': item.get('key'), 'label': item.get('key'), 'disclosing': disclosing(item)}
                    for index, item in enumerate(periods)],
        'kpis': kpis, 'series': series,
        'industries': focus_details, 'focus': focus_codes,
        'top_stocks': overlap.get('stocks', []),
        'ref_marks': {'label':'21Q1','note':'2021年一季度基准'},
        'note': {'classification':'当前基金分类回看','industry':'当前申万SW2021回看',
                 'relation':'当期已验证关系优先；历史近似和未归并份额分列' ,
                 'w10_coverage': {p['key']:p.get('top10_weight_valid_identities') for p in overlap.get('periods',[])}, 'deviation': deviation, 'per_period':overlap.get('periods',[])},
        'marks': {key: meta.get(key) for key in ('as_of','holding_period','market_date')},
        'quality': {'independent_implementation': True,
                    'active_universe': 'active_equity + active_index_enhanced',
                    'source_scope': 'current classification applied retrospectively'},
        'sources': ['fund_holdings', 'fund_profiles', 'fund_product_share_observations',
                    'industry_members', 'industry_taxonomy', 'valuation_metrics', 'fund_scale'],
        'coverage': {'periods': len(periods), 'industries': len(focus_details),
                     'top_stocks': len(overlap.get('stocks', [])),
                     'weighted_history': weighted.get('coverage', {})},
        'data_version': _page_version(root, meta['as_of']),
    }}


def _stock_original_row(row: dict) -> dict:
    raw_dims = row.get('dims') or {}
    dims = {DIM_ALIAS.get(key, key): value for key, value in raw_dims.items()}
    missing = [DIM_ALIAS.get(key, key) for key in (row.get('dims_missing') or [])]
    return {
        'code': row.get('symbol'), 'name': row.get('name'), 'l1_name': row.get('industry_name'),
        'score': row.get('score'), 'band': row.get('band'), 'dims': dims,
        'dims_missing': missing, 'n_products': row.get('fund_count'),
        'n_products_chg': row.get('fund_change'), 'n_funds': row.get('fund_count'),
        'n_funds_chg': row.get('fund_change'), 'n_corps': row.get('n_corps'),
        'hold_mv_h': row.get('reported_mv'), 'hold_mv_now': row.get('holding_mv'),
        'fr_pct': row.get('hold_float_ratio') * 100 if row.get('hold_float_ratio') is not None else None,
        'fr_chg_pct': row.get('ratio_change'),
        'fr_free_pct': row.get('hold_float_ratio_display') * 100
        if row.get('hold_float_ratio_basis') in ('daily_basic_free_share_snapshot', 'share_structure_exact')
        and row.get('hold_float_ratio_display') is not None else None,
        'top5_pct': row.get('top5_pct'), 'passive_pct': row.get('passive_pct'),
        'liq_days': row.get('liquidation'), 'adv_amount': row.get('avg_turnover'),
        'turnover5_pct': row.get('turnover5_pct'),
        'ret_pct': row.get('return_pct') * 100 if row.get('return_pct') is not None else None,
        'dd_pct': row.get('drawdown_pct') * 100 if row.get('drawdown_pct') is not None else None,
        'off_high_pct': row.get('high20_pct') * 100 if row.get('high20_pct') is not None else None,
        'fin_chg30_pct': row.get('margin30_pct'), 'mf30_pct': row.get('large30_pct'),
        'pe3y': row.get('pe3y'), 'pb3y': row.get('pb3y'),
        'top10_chg_pct': row.get('top10_chg_pct'), 'top10_cover_pct': row.get('top10_cover_pct'),
        'top10_chg_mv': row.get('top10_chg_mv'),
        'top10_current_shares': row.get('top10_current_shares'),
        'top10_previous_shares': row.get('top10_previous_shares'),
        'top10_prior_products': row.get('top10_prior_products'), 'pool': row.get('pool'),
        'quality_flags': row.get('quality_flags'), 'top10_eligible_for_rank': row.get('top10_eligible_for_rank'),
    }


def _stock_table_contract(rows: list[dict]) -> dict:
    keys = (
        'code', 'name', 'l1_code', 'pool', 'score', 'band', 'dims', 'dims_missing',
        'n_funds', 'n_funds_chg', 'n_corps', 'hold_mv_h', 'hold_mv_now', 'fr_pct', 'fr_chg_pct', 'fr_free_pct',
        'top5_pct', 'passive_pct', 'liq_days', 'adv_amount', 'turnover5_pct',
        'pe3y', 'pb3y', 'top10_chg_pct', 'top10_cover_pct', 'ret_pct', 'dd_pct',
        'off_high_pct', 'fin_chg30_pct', 'mf30_pct', 'top10_chg_mv',
    )
    return {
        'n': len(rows), 'money_unit': 1,
        'dims_order': ['crowd', 'liq', 'chg', 'px', 'flow'],
        'l1': {row.get('l1_name'): row.get('l1_name') for row in rows if row.get('l1_name')},
        'cols': {key: [row.get(key) for row in rows] for key in keys},
    }


def _detail_contract(data: dict) -> dict:
    kpi = data.get('kpi') or {}
    score = kpi.get('score')
    band = kpi.get('band')
    kpi_items = [
        {'key': 'selector', 'label': '行业 · 板块', 'value': data.get('l1_name'),
         'sub': data.get('code'), 'tone': 'neutral'},
        {'key': 'score', 'label': '风险分', 'value': score,
         'sub': '高风险' if band == 'high' else '关注' if band == 'warn' else '常规', 'tone': 'neutral'},
        {'key': 'fr', 'label': '占流通比', 'value': kpi.get('fr_pct'),
         'sub': f"上期 {kpi.get('fr_prev_pct'):.2f}%" if kpi.get('fr_prev_pct') is not None else None, 'tone': 'neutral'},
        {'key': 'funds', 'label': '持有基金', 'value': kpi.get('n_products'),
         'sub': (f"Δ {kpi.get('n_products_chg'):+.0f} · 产品组 {data.get('holders', {}).get('holder_count')} / 份额行 {data.get('holders', {}).get('share_holder_count')}"
                 if kpi.get('n_products_chg') is not None else None), 'tone': 'neutral'},
        {'key': 'hold_mv', 'label': '持仓市值（按最新价）', 'value': kpi.get('holding_mv_now'),
         'sub': '产品级已验证份额合并', 'tone': 'neutral'},
        {'key': 'liq', 'label': '清仓天数', 'value': kpi.get('liq_days'),
         'sub': '持仓现值 / 20 日平均成交额', 'tone': 'neutral'},
        {'key': 'ret', 'label': '区间收益', 'value': kpi.get('ret_pct'),
         'sub': data.get('start'), 'tone': 'down' if (kpi.get('ret_pct') or 0) < 0 else 'up'},
        {'key': 'mf30', 'label': '大单及特大单净额 30 日', 'value': kpi.get('large30_amount'),
         'sub': f"{kpi.get('mf30_pct'):.2f}% / 流通市值" if kpi.get('mf30_pct') is not None else None,
         'tone': 'down' if (kpi.get('mf30_pct') or 0) < 0 else 'up'},
    ]
    data['kpi'] = {**kpi, 'as_of': data.get('trade_date'), 'items': kpi_items, 'note': None}
    holders = data.get('holders') or {}
    holders.update({'hhi': holders.get('company_hhi'), 'enhanced_pct': holders.get('index_enhanced_pct'),
                    'n_products': holders.get('holder_count'), 'note': holders.get('scope') or holders.get('note')})
    dims = data.get('dims') or {}
    dims['dims_missing'] = [item['key'] for item in dims.get('items', []) if item.get('pct') is None]
    points = (data.get('px_margin') or {}).get('series') or []
    data['px_margin'] = {**data.get('px_margin', {}),
                         'dates': [point.get('date') for point in points],
                         'close': [point.get('close') for point in points],
                         'fin_balance': [point.get('margin_balance') for point in points]}
    top = data.get('top10') or {}
    top.update({'h_sh': top.get('current_shares'), 't_sh': top.get('previous_shares')})
    data['links'] = {'crowding': f"/dashboard/crowding?tab=stock&s={data.get('code')}",
                     'stock_page': f"/stock/{data.get('code')}"}
    data['market_type'] = data.get('l1_name')
    marks = data.get('marks') or {}
    marks.update({'h': marks.get('holding_period'), 't': top.get('current_period'),
                  's': top.get('previous_period'), 'd': marks.get('market_date'),
                  'hp_end': marks.get('holding_period')})
    return data


def _stock_view_filter(rows: list[dict], view: str) -> list[dict]:
    if view == 'pool':
        return [row for row in rows if row.get('pool')]
    if view == 'high':
        return [row for row in rows if (row.get('score') or -1) >= 85]
    if view == 'held':
        return rows
    if view == 'crowded_expensive':
        return [row for row in rows if row.get('pool') and (row.get('score') or 0) >= 70
                and any(row.get(key) is not None and row[key] >= 70 for key in ('pe3y', 'pb3y'))]
    if view in ('top10_up', 'top10_down'):
        sign = 1 if view=='top10_up' else -1
        return sorted([r for r in rows if r.get('top10_eligible_for_rank')
                       and r.get('top10_chg_pct') is not None and sign*r['top10_chg_pct']>0],
                      key=lambda r: -sign*(r.get('top10_chg_mv') or 0))
    return []


def get_overview_page(start_key: str) -> dict:
    snap = get_snapshot()
    meta = snap['metadata']
    root = _lake_root()
    version = _page_version(root, meta['as_of'])
    return _page_cache_get(version, 'overview', start_key,
                           lambda: _build_overview_page(snap, start_key, root))


def get_daily_risk_change(snap, board):
    meta = snap['metadata']
    root = _lake_root()
    version = _page_version(root, meta['as_of'])
    def build():
        _, sessions = _return_baseline(meta, 'hp')
        if len(sessions) < 2:
            return {'delta':None,'reason':'previous_trading_day_missing'}
        changes=previous_period_deltas(snap,_previous_snapshot(snap))
        old=previous_day_risk(snap,previous_day=sessions[-2],lake_root=root,changes=changes)
        current={s:r for s,r in board['rows'].items() if r.get('score') is not None}
        previous={s:r for s,r in old['rows'].items() if r.get('score') is not None}
        same=set(current)==set(previous)
        comparable=set(current)&set(previous)
        delta=sum(current[s]['score']>=85 for s in comparable)-sum(previous[s]['score']>=85 for s in comparable)
        return {'delta':delta if same else None, 'comparable_estimate_delta':delta,
                'current_count':len(current),'previous_count':len(previous),'comparable_count':len(comparable),
                'previous_date':sessions[-2].isoformat(),'quality':'estimated' if same else 'partial',
                'reason':None if same else 'scoring_cohort_changed',
                'scope':'固定披露、关系和分类版本；行情变化重新排名；非实际买卖变化'}
    return _page_cache_get(version,'daily_risk','change',build)


def _build_overview_page(snap: dict, start_key: str, root: Path) -> dict:
    meta = snap['metadata']
    stock_page = get_stock_page(start_key)
    rows = stock_page['rows']
    snap_rows = snap['rows']
    total_report_value = sum(row['reported_mv'] for row in snap_rows if row.get('reported_mv') is not None)
    grouped: dict[str, float] = {}
    for row in snap_rows:
        if row.get('industry_name') and row.get('reported_mv') is not None:
            grouped[row['industry_name']] = grouped.get(row['industry_name'], 0.0) + float(row['reported_mv'])
    lead_name, lead_value = max(grouped.items(), key=lambda item: item[1]) if grouped else (None, 0)
    lead_share = lead_value / total_report_value * 100 if total_report_value else None
    benchmark = industry_benchmark(as_of=date.fromisoformat(meta['holding_period']), lake_root=root)
    bench = {row['name']: row['benchmark_pct'] for row in benchmark.get('rows', [])}
    lead_bench = bench.get(lead_name)
    previous = _previous_snapshot(snap)
    prev_grouped: dict[str, float] = {}
    prev_total = sum(row['reported_mv'] for row in previous['rows'] if row.get('reported_mv') is not None)
    for row in previous['rows']:
        if row.get('industry_name') and row.get('reported_mv') is not None:
            prev_grouped[row['industry_name']] = prev_grouped.get(row['industry_name'], 0.0) + float(row['reported_mv'])
    prev_share = prev_grouped.get(lead_name, 0) / prev_total * 100 if prev_total and lead_name else None
    liq = get_liquidity(snap)
    board = get_board(snap, liq)
    returns_data = overview_returns(snap, board, period=START_MAP[start_key], lake_root=root,
                                    market_fn=lambda *args, **kwargs: get_batch_market(snap))
    position = fund_position_history(as_of=date.fromisoformat(meta['as_of']), lake_root=root,
                                     share_to_master=snap.get('_relation_map'))
    latest_box = position.get('boxes', [])[-1] if position.get('boxes') else {}
    high_rows = [row for row in rows if row.get('score') is not None and row['score'] >= 85]
    day_change = get_daily_risk_change(snap, board)
    views = {
        'pool': sum(bool(row.get('pool')) for row in rows), 'high': len(high_rows),
        'held': len(rows), 'none': None,
        'crowded_expensive': len(_stock_view_filter(rows, 'crowded_expensive')),
        'top10_up': sum(bool(r.get('top10_eligible_for_rank')) and (r.get('top10_chg_pct') or 0)>0 for r in rows),
        'top10_down': sum(bool(r.get('top10_eligible_for_rank')) and (r.get('top10_chg_pct') or 0)<0 for r in rows),
    }
    qgroups = returns_data.get('groups', [])
    q1 = next((item for item in qgroups if item.get('q') == 1), {})
    q5 = next((item for item in qgroups if item.get('q') == 5), {})
    q1_last = (q1.get('ew') or [None])[-1]
    q5_last = (q5.get('ew') or [None])[-1]
    all_a = None
    try:
        conn = bounded_connect()
        try:
            paths = [str(p) for p in (root / 'curated' / 'valuation_metrics').rglob('*.parquet')]
            all_a = conn.execute(
                "SELECT COUNT(DISTINCT symbol) FROM read_parquet(?,hive_partitioning=false,union_by_name=true) "
                "WHERE trade_date=CAST(? AS DATE) AND symbol ~ '^[0-9]{6}\\.(SH|SZ|BJ)$'",
                [paths, meta['market_date']],
            ).fetchone()[0] if paths else None
        finally:
            conn.close()
    except Exception:
        all_a = None
    kpis = [
        {'key': 'hold_mv', 'label': '公募股票持仓', 'value': total_report_value,
         'sub': f"{meta['holding_period']} · {len(snap_rows)} 只股票", 'tone': 'neutral'},
        {'key': 'n_held', 'label': '有公募持仓', 'value': len(snap_rows),
         'sub': f"全 A {all_a if all_a is not None else '—'} 只", 'tone': 'neutral'},
        {'key': 'top_industry', 'label': '第一大行业占比', 'value': lead_share,
         'sub': f"{lead_name or '—'} · 较上期 {('%+.1fpp' % (lead_share-prev_share)) if lead_share is not None and prev_share is not None else '—'} · 超配 {('%+.1fpp' % (lead_share-lead_bench)) if lead_share is not None and lead_bench is not None else '—'}",
         'tone': 'neutral'},
        {'key': 'pool_ret', 'label': '抱团池区间收益',
         'value': returns_data.get('pool_return', 0) * 100 if returns_data.get('pool_return') is not None else None,
         'sub': f"{sum(bool(r.get('pool')) for r in rows)} 只 · 行情日公募持仓现值加权 · {START_MAP[start_key]}",
         'tone': 'up' if (returns_data.get('pool_return') or 0) > 0 else 'down'},
        {'key': 'crowded_ret', 'label': '最拥挤组区间收益',
         'value': q5_last * 100 if q5_last is not None else None,
         'sub': f"最分散组 {q1_last * 100:.1f}% · {START_MAP[start_key]}" if q1_last is not None else START_MAP[start_key],
         'tone': 'up' if (q5_last or 0) > 0 else 'down'},
        {'key': 'high_risk', 'label': '高风险个股', 'value': len(high_rows),
         'sub': ('风险分 ≥ 85 · 较昨日 %+.0f（固定样本估算）' % day_change['delta']) if day_change.get('delta') is not None else '风险分 ≥ 85 · 昨日可比样本不足', 'tone': 'up'},
        {'key': 'equity_pos', 'label': '偏股基金仓位中位', 'value': latest_box.get('median'),
         'sub': latest_box.get('key') or '数据尚未接入', 'tone': 'neutral'},
        {'key': 'top10', 'label': '主动前十大净减持',
         'value': views['top10_down'] if stock_page.get('metadata',{}).get('top10_change_metadata',{}).get('denominator_complete') else None,
         'sub': f"质量门槛下：净减 {views['top10_down']} / 净增 {views['top10_up']}" if stock_page.get('metadata',{}).get('top10_change_metadata',{}).get('denominator_complete') else '同样本覆盖不足，暂不生成净增减只数', 'tone': 'neutral'},
    ]
    warn = []
    for row in sorted((r for r in rows if r.get('pool') and r.get('score') is not None),
                      key=lambda r: (-r['score'], r['symbol']))[:15]:
        dims = {DIM_ALIAS.get(key, key): value for key, value in (row.get('dims') or {}).items()}
        missing = [DIM_ALIAS.get(key, key) for key in (row.get('dims_missing') or [])]
        warn.append({'code': row['symbol'], 'name': row.get('name'), 'l1_name': row.get('industry_name'),
                     'score': row['score'], 'band': row.get('band'), 'dims': dims,
                     'dims_missing': missing,
                     'fr_pct': row.get('hold_float_ratio') * 100 if row.get('hold_float_ratio') is not None else None,
                     'liq_days': row.get('liquidation'),
                     'ret_pct': row.get('return_pct') * 100 if row.get('return_pct') is not None else None,
                     'dd_pct': row.get('drawdown_pct') * 100 if row.get('drawdown_pct') is not None else None,
                     'mf30_pct': row.get('large30_pct')})
    map_rows = [{'code': row['symbol'], 'name': row.get('name'), 'l1_name': row.get('industry_name'),
                 'score': row.get('score'), 'band': row.get('band'),
                 'fr_pct': row.get('hold_float_ratio') * 100 if row.get('hold_float_ratio') is not None else None,
                 'ret_pct': row.get('return_pct') * 100 if row.get('return_pct') is not None else None,
                 'hold_mv_now': row.get('holding_mv'), 'pool': row.get('pool')}
                for row in rows if row.get('pool')]
    return {'code': 0, 'message': 'success', 'data': {
        'kpi': {'as_of': meta['market_date'], 'items': kpis}, 'start': start_key,
        'starts': list(START_MAP), 'status': 'ready',
        'quintiles': {**returns_data, 'as_of': meta['market_date']},
        'map': {'as_of': meta['market_date'], 'rows': map_rows},
        'warn': {'as_of': meta['market_date'], 'rows': warn},
        'points': {'as_of': meta['market_date'], 'items': views},
        'top10': {'as_of': meta['as_of'], 'cover_min': 80, 'down': views['top10_down'],
                  'up': views['top10_up'], 'n_funds_t': None},
        'marks': {key: meta.get(key) for key in ('as_of','holding_period','previous_holding_period','market_date','latest_price_date','latest_valuation_date')},
        'quality': {'daily_risk_change': day_change, 'top10_change_status': stock_page.get('metadata', {}).get('top10_change_status'),
                    'source_scope': 'all published fund shares; verified A/C merged only'},
        'data_version': meta.get('cache_version') or _page_version(root, meta['as_of']),
    }}

def _json_default(obj):
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    raise TypeError(type(obj).__name__)


class Handler(BaseHTTPRequestHandler):
    def _route_path(self):
        path = urlsplit(self.path).path
        return path.removeprefix(BROWSER_PREFIX) if path.startswith(BROWSER_PREFIX + '/') else path

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == BROWSER_PREFIX:
            self.send_response(308)
            self.send_header("Location", BROWSER_PREFIX + "/" + ("?" + url.query if url.query else ""))
            self.end_headers()
            return
        path = self._route_path()
        if not path.startswith('/api/crowding/') and not path.startswith('/api/dashboards/fund_crowding/'):
            return self._handle_get()
        if not _api_slot.acquire(timeout=90):
            self.send_data('{"error":"计算繁忙，请稍后重试"}'.encode(), 'application/json; charset=utf-8', 503)
            return
        try:
            return self._handle_get()
        finally:
            _api_slot.release()

    def _handle_get(self):
        path = self._route_path()
        query = parse_qs(urlsplit(self.path).query)
        requested_version = query.get('data_version', [None])[0]
        if path.startswith('/api/') and requested_version and requested_version != SERVING_VERSION:
            self.send_data(json.dumps({'code': 409, 'message': '数据版本不存在或未完成，请刷新页面'}).encode(),
                           'application/json; charset=utf-8', 409)
            return
        detail_match = re.fullmatch(r'/api/dashboards/fund_crowding/stocks/([0-9]{6}\.(?:SH|SZ|BJ))', path)
        if detail_match:
            try:
                start_key = parse_qs(urlsplit(self.path).query).get('start', ['hp'])[0]
                if start_key not in START_MAP:
                    raise ValueError('invalid start')
                payload = get_stock_detail_page(detail_match.group(1), start_key)
                payload['data'] = _detail_contract(payload['data'])
                self.send_api(payload)
            except Exception as exc:
                self.send_data(json.dumps({'code': 500, 'message': str(exc)}, ensure_ascii=False).encode(),
                               'application/json; charset=utf-8', 500)
            return
        if path == '/api/crowding/health':
            daily = _serving_daily()
            manifest = daily or active_manifest()
            snapshot_ready = manifest is not None and bool(_cache)
            metadata = _cache.get('metadata', {})
            payload = {
                'status': 'ok' if snapshot_ready else 'unavailable',
                'active_version': SERVING_VERSION if snapshot_ready else None,
                'cache_version': SERVING_VERSION,
                'calculation_version': manifest.get('calculation_version') if manifest else None,
                'snapshot_origin': _snapshot_origin,
                'snapshot_fingerprint': _snapshot_input_fingerprint,
                'snapshot_build_count': _snapshot_build_count,
                'data_date': metadata.get('market_date'),
                'holding_period': metadata.get('holding_period'),
                'latest_price_date': metadata.get('latest_price_date'),
                'latest_valuation_date': metadata.get('latest_valuation_date'),
                'industry_date': metadata.get('industry_date'),
                'auto_refresh': False,
                'refresh_running': False,
            }
            if snapshot_ready and daily:
                payload.update(schema=daily['schema'], base_version=daily['base_version'],
                               fund_scale_period=daily['fund_scale_period'])
            elif snapshot_ready and SERVING_VERSION != FROZEN_VERSION:
                candidate = load_candidate(SERVING_VERSION)
                payload.update(base_version=FROZEN_VERSION, fund_scale_period=candidate['report_period'])
            self.send_data(json.dumps(payload).encode(), 'application/json; charset=utf-8',
                           200 if snapshot_ready else 503)
            return
        if path == '/api/dashboards/fund_crowding/overview':
            try:
                start_key = parse_qs(urlsplit(self.path).query).get('start', ['hp'])[0]
                if start_key not in START_MAP:
                    raise ValueError('invalid start')
                payload = get_overview_page(start_key)
                data = payload['data']
                items = (data.get('kpi') or {}).get('items') or []
                by_key = {item.get('key'): item.get('value') for item in items}
                trade_date = data.get('trade_date') or (data.get('kpi') or {}).get('as_of')
                data['status'] = {
                    'as_of': trade_date, 'hold_mv_total': by_key.get('hold_mv'),
                    'n_held': by_key.get('n_held'), 'n_all': None, 'n_funds': None,
                    'pool_n': None, 'pool_ret_pct': by_key.get('pool_ret'),
                    'high_risk_n': by_key.get('high_risk'), 'high_risk_chg': None,
                    'top10_down_n': by_key.get('top10'), 'top10_up_n': (data.get('top10') or {}).get('up'),
                }
                data['starts'] = {key: get_stock_page(key)['metadata'].get('baseline') for key in START_MAP}
                self.send_api(payload)
            except Exception as exc:
                self.send_data(json.dumps({'code': 500, 'message': str(exc)}, ensure_ascii=False).encode(),
                               'application/json; charset=utf-8', 500)
            return
        if path == '/api/dashboards/fund_crowding/stock-search':
            try:
                query = parse_qs(urlsplit(self.path).query)
                needle = query.get('q', [''])[0].strip().lower()
                if not needle:
                    payload = {'code': 0, 'message': 'success', 'data': {'rows': [], 'data_version':_page_version(_lake_root(),date.today().isoformat())}}
                else:
                    base = get_stock_page('hp')
                    rows = []
                    for row in base.get('rows', []) + get_unheld_stock_page('hp').get('rows', []):
                        code = str(row.get('symbol') or '')
                        name = str(row.get('name') or '')
                        if needle in code.lower() or needle in name.lower():
                            rows.append({
                                'code': code, 'name': name,
                                'l1_name': row.get('industry_name'),
                                'score': row.get('score'), 'pool': bool(row.get('pool')),
                            })
                            if len(rows) >= 12:
                                break
                    payload = {'code': 0, 'message': 'success', 'data': {'rows': rows, 'data_version': _page_version(_lake_root(),base['metadata']['as_of'])}}
                self.send_api(payload)
            except Exception as exc:
                self.send_data(json.dumps({'code': 500, 'message': str(exc)}, ensure_ascii=False).encode(),
                               'application/json; charset=utf-8', 500)
            return
        if path == '/api/dashboards/fund_crowding/stocks':
            try:
                query = parse_qs(urlsplit(self.path).query)
                start_key = query.get('start', ['hp'])[0]
                view = query.get('view', ['pool'])[0]
                if start_key not in START_MAP or view not in {
                    'pool', 'high', 'held', 'none', 'crowded_expensive', 'top10_up', 'top10_down'
                }:
                    self.send_data(b'{"code":400,"message":"invalid query"}', 'application/json; charset=utf-8', 400)
                    return
                base = get_stock_page(start_key)
                unheld = get_unheld_stock_page(start_key)
                held_rows = [_stock_original_row(row) for row in base.get('rows', [])]
                unheld_rows = [_stock_original_row(row) for row in unheld.get('rows', [])]
                rows = unheld_rows if view == 'none' else held_rows
                industries = sorted({row.get('l1_name') for row in held_rows + unheld_rows if row.get('l1_name')})
                views = {
                    'pool': sum(bool(row.get('pool')) for row in held_rows),
                    'high': sum((row.get('score') or -1) >= 85 for row in held_rows),
                    'held': len(held_rows),
                    'none': len(unheld_rows),
                    'crowded_expensive': len(_stock_view_filter(held_rows, 'crowded_expensive')),
                    'top10_up': len(_stock_view_filter(held_rows, 'top10_up')),
                    'top10_down': len(_stock_view_filter(held_rows, 'top10_down')),
                }
                if view == 'none':
                    selected = unheld_rows
                else:
                    selected = _stock_view_filter(rows, view)
                l1 = query.get('l1', [''])[0]
                search = query.get('q', [''])[0].strip().lower()
                if l1:
                    selected = [row for row in selected if row.get('l1_name') == l1]
                if search:
                    selected = [row for row in selected
                                if search in f"{row.get('code', '')} {row.get('name', '')}".lower()]
                sort_key = query.get('sort', ['score'])[0]
                order = query.get('order', ['desc'])[0]
                if sort_key not in (rows[0] if rows else {}):
                    sort_key = 'score'
                direction = 1 if order == 'asc' else -1
                present = [row for row in selected if row.get(sort_key) is not None]
                missing = [row for row in selected if row.get(sort_key) is None]
                if sort_key in ('code', 'name', 'l1_name'):
                    present.sort(key=lambda row: (str(row.get(sort_key) or ''), row.get('code', '')),
                                 reverse=direction < 0)
                else:
                    present.sort(key=lambda row: (float(row.get(sort_key) or 0), row.get('code', '')),
                                 reverse=direction < 0)
                missing.sort(key=lambda row: row.get('code', ''))
                selected = present + missing
                page_size = min(500, max(1, int(query.get('page_size', ['100'])[0])))
                total = len(selected)
                max_page = max(1, (total + page_size - 1) // page_size)
                page = min(max_page, max(1, int(query.get('page', ['1'])[0])))
                page_rows = selected[(page - 1) * page_size:page * page_size]
                table_contract = _stock_table_contract(page_rows)
                table_contract.update({'n': total, 'page': page, 'page_size': page_size, 'pages': max_page,
                                       'rows': page_rows})
                payload = {'code': 0, 'message': 'success', 'data': {
                    'as_of': base['metadata']['as_of'], 'trade_date': base['metadata']['market_date'],
                    'start': start_key, 'views': views,
                    'holders_ready': True,
                    'note': ('当前没有达到完整分母、覆盖率≥80%及上期至少5产品条件的股票；两期观察变化仍可在全部持仓表和个股详情查看，不能将未披露视作退出。'
                             if view in ('top10_up','top10_down') and total == 0 else None),
                    'top10_diagnostics': base['metadata'].get('top10_change_metadata'),
                    'filters': {'industries': industries},
                    'table': table_contract,
                    'marks': {k: base['metadata'].get(k) for k in
                              ('holding_period', 'previous_holding_period', 'market_date', 'as_of','latest_price_date','latest_valuation_date')},
                    'quality': {'rule': base['metadata'].get('missing_policy'),
                                'top10_change_status': base['metadata'].get('top10_change_status')},
                    'data_version': base['metadata'].get('cache_version'),
                }}
                self.send_api(payload)
            except Exception as exc:
                body = json.dumps({'code': 500, 'message': str(exc)}, ensure_ascii=False).encode()
                self.send_data(body, 'application/json; charset=utf-8', 500)
            return
        if path == '/api/dashboards/fund_crowding/funds':
            try:
                start_key = parse_qs(urlsplit(self.path).query).get('start', ['hp'])[0]
                if start_key not in START_MAP:
                    raise ValueError('invalid start')
                payload = get_funds_page(start_key)
                self.send_api(payload)
            except Exception as exc:
                self.send_data(json.dumps({'code': 500, 'message': str(exc)}, ensure_ascii=False).encode(),
                               'application/json; charset=utf-8', 500)
            return
        if path == '/api/dashboards/fund_crowding/industries':
            try:
                start_key = parse_qs(urlsplit(self.path).query).get('start', ['hp'])[0]
                if start_key not in START_MAP:
                    raise ValueError('invalid start')
                payload = get_industries_page(start_key)
                self.send_api(payload)
            except Exception as exc:
                self.send_data(json.dumps({'code': 500, 'message': str(exc)}, ensure_ascii=False).encode(),
                               'application/json; charset=utf-8', 500)
            return
        if path == '/api/dashboards/fund_crowding/crowd_index':
            try:
                payload = get_index_page()
                self.send_api(payload)
            except Exception as exc:
                self.send_data(json.dumps({'code': 500, 'message': str(exc)}, ensure_ascii=False).encode(),
                               'application/json; charset=utf-8', 500)
            return
        file_name = {"/": "index.html", "/dashboard.js": "dashboard.js",
                     "/style.css": "style.css", "/dashboard.css": "dashboard.css"}.get(path)
        if not file_name:
            self.send_error(404)
            return
        file = WEB / file_name
        self.send_data(file.read_bytes(), (mimetypes.guess_type(file.name)[0] or "text/plain") + "; charset=utf-8")

    def send_api(self, payload: dict):
        if SERVING_VERSION != FROZEN_VERSION:
            payload = {**payload, 'data': {**payload['data'], 'data_version': SERVING_VERSION}}
        self.send_data(json.dumps(payload, ensure_ascii=False, default=_json_default, allow_nan=False).encode(),
                       'application/json; charset=utf-8')

    def send_data(self, body: bytes, mime: str, status: int = 200):
        etag = '"' + hashlib.sha1(body).hexdigest()[:20] + '"'
        if status == 200 and self.headers.get('If-None-Match') == etag:
            self.send_response(304)
            self.send_header('ETag', etag)
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            return
        compress = ('gzip' in self.headers.get('Accept-Encoding', '')
                    and len(body) >= 1024
                    and not mime.startswith('image/'))
        if compress:
            body = gzip.compress(body, compresslevel=4)
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        if compress:
            self.send_header('Content-Encoding', 'gzip')
            self.send_header('Vary', 'Accept-Encoding')
        self.send_header('ETag', etag)
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    if BUILD_MODE:
        raise SystemExit('构建模式不能启动公募抱团服务')
    if len(sys.argv) != 2:
        raise SystemExit('用法：python -m crowding.server PORT')
    port = int(sys.argv[1])
    if not 1 <= port <= 65535:
        raise SystemExit('端口必须在 1–65535 内')
    get_snapshot()
    with ThreadingHTTPServer(("127.0.0.1", port), Handler) as server:
        print(f"公募抱团（只读版本 {SERVING_VERSION}）: http://127.0.0.1:{port}{BROWSER_PREFIX}/", flush=True)
        server.serve_forever()
