"""Exact, bounded-memory public-fund overlap and concentration history."""
from __future__ import annotations

from collections import defaultdict, Counter
from datetime import date
from pathlib import Path
from crowding import fixed_aggregate_cache as fixed_cache
from math import isfinite
from statistics import median
import re
from typing import Any

from crowding.resources import connect as bounded_connect
import numpy as np
try:
    from scipy.sparse import csr_matrix
except ImportError:
    csr_matrix = None

from crowding.lake import DEFAULT_LAKE_ROOT
from crowding.index_panels import product_top10_nav_summary
from crowding.product_view import load_product_view, available_periods, product_view_context, disclosure_history
from crowding.fund_classification import classify_fund_profiles


def _path(root: Path, dataset: str) -> str | None:
    folder = root / 'curated' / dataset
    if not folder.exists() or not any(folder.rglob('*.parquet')):
        return None
    return (folder / '**' / '*.parquet').resolve().as_posix()


def _exact_jaccard(funds: list[list[str]], *, block_size: int = 64) -> float | None:
    """All pairs, including pairs sharing nothing; process bounded sparse blocks."""
    n = len(funds)
    if n < 2:
        return None
    if n > 30000:
        raise ValueError('基金样本超过本地安全上限：30000，未启动两两计算')
    if csr_matrix is None:
        # Optional-SciPy fallback: inverted stock lists plus a bounded vector
        # for one fund, still counting all zero-intersection pairs.
        postings = defaultdict(list)
        unique_funds = [set(fund) for fund in funds]
        lengths = np.asarray([len(f) for f in unique_funds], dtype=np.int16)
        for i, fund in enumerate(unique_funds):
            for symbol in fund:
                postings[symbol].append(i)
        postings = {key: np.asarray(value, dtype=np.int32) for key, value in postings.items()}
        total = 0.0
        for i, fund in enumerate(unique_funds):
            common = np.zeros(n, dtype=np.int16)
            for symbol in fund:
                common[postings[symbol]] += 1
            common = common[i + 1:]
            denominator = lengths[i] + lengths[i + 1:] - common
            total += np.sum(np.divide(common, denominator, out=np.zeros_like(common, dtype=float), where=denominator > 0))
        return float(total * 100 / (n * (n - 1) / 2))
    positions = {}
    indices, indptr = [], [0]
    for fund in funds:
        for symbol in set(fund):
            if symbol not in positions:
                positions[symbol] = len(positions)
            indices.append(positions[symbol])
        indptr.append(len(indices))
    matrix = csr_matrix((np.ones(len(indices), dtype=np.int16),
                         np.asarray(indices, dtype=np.int32),
                         np.asarray(indptr, dtype=np.int32)),
                        shape=(n, len(positions)), dtype=np.int16)
    matrix.sort_indices()
    counts = np.diff(matrix.indptr)
    total = 0.0
    for start in range(0, n, block_size):
        product = (matrix[start:start + block_size] @ matrix.T).tocsr()
        for i in range(product.shape[0]):
            begin, end = product.indptr[i:i + 2]
            col = product.indices[begin:end]
            common = product.data[begin:end].astype(np.float64)
            keep = col > start + i
            col, common = col[keep], common[keep]
            total += np.sum(common / (counts[start + i] + counts[col] - common))
    return float(total * 100 / (n * (n - 1) / 2))


def verified_product_top10_nav_weights(products, scale_rows, *, report_period):
    """Pure, strict calculation; data integration must independently verify inputs.

    Products need product_id, period, verification_status="verified_complete",
    report_id, pdf_sha256, unique share_codes and an already-deduplicated A-stock
    portfolio. All shares must correspond to identical product-level holdings.
    One same-period ``scale_yi`` row per share is required; scale is in CNY 1e8,
    holdings market_value in CNY. Derived fund_scale is non-PIT.

    Unknown/unverified products and a separately labeled single-share experiment
    never contribute to the verified product median. Caller must ensure source
    observation time, fund code suffix normalization, product identity and
    disclosure-version consistency before marking any product verified.
    """
    def valid_positive(value):
        return not isinstance(value, bool) and isinstance(value, (int, float)) and isfinite(value) and value > 0
    def valid_nonnegative(value):
        return not isinstance(value, bool) and isinstance(value, (int, float)) and isfinite(value) and value >= 0
    by_share = defaultdict(list)
    for row in scale_rows:
        if row.get('report_period') == report_period:
            by_share[row.get('share_code')].append(row.get('scale_yi'))
    candidates = Counter(p.get('product_id') for p in products if p.get('product_id'))
    share_owners = defaultdict(set)
    for p in products:
        if p.get('verification_status') == 'verified_complete' and p.get('report_period') == report_period:
            for code in set(p.get('share_codes') or []):
                share_owners[code].add(p.get('product_id'))
    values, exclusions, experiments = [], [], []
    for p in products:
        pid = p.get('product_id')
        if p.get('report_period') != report_period:
            exclusions.append({'product_id':pid,'reason':'period_mismatch'}); continue
        codes = p.get('share_codes')
        portfolio = p.get('holdings')
        if not isinstance(portfolio,list):
            exclusions.append({'product_id':pid,'reason':'holdings_missing'}); continue
        symbols = [h.get('symbol') for h in portfolio]
        if (len(portfolio) < 5 or len(set(symbols)) != len(symbols)
                or any(not isinstance(x,str) or not re.fullmatch(r'[0-9]{6}\.(?:SH|SZ|BJ)',x) for x in symbols)
                or any(not valid_nonnegative(h.get('market_value')) for h in portfolio)):
            exclusions.append({'product_id':pid,'reason':'invalid_or_insufficient_holdings'}); continue
        top10 = sum(sorted((h['market_value'] for h in portfolio),reverse=True)[:10])
        if p.get('verification_status') != 'verified_complete':
            # Diagnostic only: a single-share denominator may represent just
            # one class of a multi-class product, so it never enters the median.
            code = p.get('single_share_code')
            scale = by_share.get(code, [])
            value = top10/(scale[0]*1e8)*100 if len(scale)==1 and valid_positive(scale[0]) else None
            if value is not None and not isfinite(value): value = None
            experiments.append({'share_code':code,'raw_ratio_pct':value,'plausible':value is not None and 0 < value <= 100,
                                'note':'单份额分母仅供口径排查，不可认定产品净值权重'})
            exclusions.append({'product_id':pid,'reason':'product_relation_unverified'}); continue
        if (not pid or candidates[pid] != 1 or not isinstance(codes,list) or len(codes)<2 or len(codes)!=len(set(codes))
                or any(not isinstance(c,str) or not re.fullmatch(r'[0-9]{6}',c) for c in codes)
                or any(len(share_owners[c]) != 1 for c in codes)
                or not p.get('report_id') or not p.get('pdf_sha256')):
            exclusions.append({'product_id':pid,'reason':'invalid_or_conflicting_product_evidence'}); continue
        # Duplicate rows remain ambiguous even when their numeric values agree.
        if any(len(by_share[c]) != 1 or not valid_positive(by_share[c][0]) for c in codes):
            exclusions.append({'product_id':pid,'reason':'missing_duplicate_or_invalid_share_scale'}); continue
        scale_yi = sum(by_share[c][0] for c in codes)
        weight_pct = top10/(scale_yi*1e8)*100
        if not 0 < weight_pct <= 100:
            exclusions.append({'product_id':pid,'reason':'invalid_top10_nav_weight'}); continue
        values.append({'product_id':pid,'report_period':report_period,'top10_market_value_cny':top10,
                       'total_scale_yi':scale_yi,'weight_pct':weight_pct,'share_codes':sorted(codes),
                       'report_id':p['report_id'],'pdf_sha256':p['pdf_sha256']})
    return {'median_pct':median(x['weight_pct'] for x in values) if values else None,
            'verified_products':values,'excluded':exclusions,'single_share_experiments':experiments,
            'coverage':{'candidate_products':len(products),'valid_products':len(values),
                        'excluded_products':len(exclusions),'single_share_experiment_count':len(experiments)},
            'scope':'仅已验证完整份额及一致持仓的产品；同期份额规模合计；净资产规模非 PIT；不是原站主动样本',
            'source_units':{'market_value':'CNY','fund_scale.scale_yi':'CNY 1e8'},
            'caveat':'输入必须由调用方核验报告证据及其时点；单份额实验不构成产品权重'}

def overlap_index(*, as_of: date, lake_root: Path = DEFAULT_LAKE_ROOT,
                  start_period: date = date(2019, 3, 31), max_periods: int = 31,
                  head_n: int = 200, share_to_master=None, active_symbols=None, classification=None) -> dict[str, Any]:
    root = Path(lake_root).expanduser().resolve()
    holdings, members = _path(root, 'fund_holdings'), _path(root, 'industry_members')
    taxonomy, instruments = _path(root, 'industry_taxonomy'), _path(root, 'instruments')
    if not holdings:
        return {'as_of': as_of.isoformat(), 'periods': [], 'series': {}, 'reason': '持仓数据缺失'}
    if max_periods < 1 or head_n < 1:
        raise ValueError('max_periods and head_n must be positive')
    c = bounded_connect()
    try:
        periods = available_periods(conn=c, holdings=[holdings], as_of=as_of,
                                    start_period=start_period, max_periods=max_periods)
        if classification is None and active_symbols is None:
            classification = classify_fund_profiles(lake_root=root)
        mapping_date = c.execute(f"SELECT MAX(as_of_date) FROM read_parquet('{members}', hive_partitioning=false) WHERE classification_system='sw' AND source='sw' AND as_of_date<=CAST(? AS DATE)", [as_of]).fetchone()[0] if members else None
        industry_map, stock_names = {}, {}
        if members and taxonomy and mapping_date:
            mapped = c.execute(f"""
              WITH i AS (SELECT symbol,industry_code FROM read_parquet('{members}',hive_partitioning=false)
                WHERE classification_system='sw' AND source='sw' AND as_of_date=CAST(? AS DATE)
                QUALIFY row_number() OVER(PARTITION BY symbol ORDER BY fetched_at DESC NULLS LAST)=1),
              t AS (SELECT industry_code,parent_industry_code,industry_name,level FROM read_parquet('{taxonomy}',hive_partitioning=false)
                WHERE classification_system='SW2021' QUALIFY row_number() OVER(PARTITION BY industry_code ORDER BY fetched_at DESC NULLS LAST)=1)
              SELECT i.symbol, CASE WHEN l.level='L1' THEN l.industry_name WHEN l.level='L2' THEN p.industry_name WHEN l.level='L3' THEN gp.industry_name END
              FROM i LEFT JOIN t l ON i.industry_code=l.industry_code LEFT JOIN t p ON l.parent_industry_code=p.industry_code LEFT JOIN t gp ON p.parent_industry_code=gp.industry_code
            """, [mapping_date]).fetchall()
            industry_map = {symbol: name for symbol, name in mapped if name}
        if instruments:
            stock_names = dict(c.execute(f"""SELECT symbol,name FROM read_parquet('{instruments}',hive_partitioning=false)
                QUALIFY row_number() OVER(PARTITION BY symbol ORDER BY fetched_at DESC NULLS LAST)=1""").fetchall())
        context = product_view_context(lake_root=root, as_of=as_of, share_to_master=share_to_master,
            active_symbols=active_symbols, classification=classification)
        fixed_key = fixed_cache.cache_key(name='overlap_index',lake_root=root,
            context_version=context.input_version,periods=periods,
            dependencies={'mapping_date':str(mapping_date),'industry_map':industry_map,
                'stock_names':stock_names,'head_n':head_n,'start_period':str(start_period),'max_periods':max_periods},
            code_paths=[Path(__file__),Path(__file__).with_name('index_panels.py'),Path(fixed_cache.__file__)])
        cached = fixed_cache.load('overlap_index',fixed_key,as_of=as_of,context=context)
        if cached is not None:
            return cached
        output, historical_stocks = [], []
        for p in periods:
            view = load_product_view(as_of=as_of, report_period=p, lake_root=root, conn=c,
                share_to_master=share_to_master, active_symbols=active_symbols, classification=classification, context=context)
            all_funds = defaultdict(list)
            for row in view['rows']:
                all_funds[row['product_symbol']].append((row['holding_symbol'], row['market_value']))
            funds = {f: sorted(values, key=lambda x: (-x[1], x[0]))[:10] for f, values in all_funds.items()}
            stock_values, stock_fund_counts = defaultdict(float), defaultdict(int)
            known_product_counts, unmerged_share_counts = defaultdict(int), defaultdict(int)
            for fund, values in funds.items():
                for symbol, value in values:
                    stock_values[symbol] += value
                    stock_fund_counts[symbol] += 1
                    if view['products'][fund]['relation_status'] == 'unknown':
                        unmerged_share_counts[symbol] += 1
                    else:
                        known_product_counts[symbol] += 1
            eligible = {fund: values for fund, values in funds.items() if len(values) >= 5}
            all_names = sorted(eligible)
            # Exact same-quarter fund NAV/size, never inferred from stock holdings.
            # fund_scale is a non-PIT snapshot; ambiguous duplicate values are excluded.
            scales = {f: meta['scale_yi'] for f, meta in view['products'].items()
                      if meta['is_active'] and meta['scale_yi'] is not None}
            sized = [f for f in eligible if f in scales]
            head_names = sorted(sized, key=lambda f: (-scales[f], f))[:head_n]
            all_result = _exact_jaccard([[s for s, _ in eligible[f]] for f in all_names])
            target_head = min(head_n, len(eligible))
            # A smaller known subset is not the requested head universe.
            head_estimate = _exact_jaccard([[s for s, _ in eligible[f]] for f in head_names])
            head_result = (head_estimate if len(sized) == len(eligible) and len(head_names) == target_head else None)
            # NAV eligibility is stricter than retained holding identities.
            # Keep overlap, head selection and stock identity samples intact.
            nav = product_top10_nav_summary({f: [v for _, v in values] for f, values in all_funds.items()},
                view['products'], report_period=p)
            n, hn = len(all_names), len(head_names)
            total = sum(stock_values.values())
            top20 = sum(sorted(stock_values.values(), reverse=True)[:20])
            output.append({'key': f'{p.year}Q{(p.month-1)//3+1}', 'period': p.isoformat(),
                'full': p.month in (6, 12), 'fund_count': len(funds), 'eligible_funds': n,
                'pair_count': n*(n-1)//2, 'head_pair_count': hn*(hn-1)//2,
                'jaccard_all_pct': all_result, 'jaccard_head_pct': head_result,
                'jaccard_head_estimate_pct':head_estimate, 'jac_head_estimate_pct':head_estimate,
                'head_estimate_scope':'已知同季规模子集前200估算' if head_n==200 else f'已知同季规模子集前{head_n}估算',
                'head_estimate_coverage':{'eligible_identities':n,'same_period_sized_identities':len(sized),
                    'missing_size_identities':n-len(sized),'selected_identities':hn,
                    'excluded_known_size_outside_head':max(0,len(sized)-head_n),
                    'eligible_size_coverage_pct':len(sized)/n*100 if n else None},
                'jaccard_all_mode': 'exact_block_sparse' if csr_matrix is not None else 'exact_numpy_postings', 'jaccard_head_mode': 'exact_block_sparse' if csr_matrix is not None else 'exact_numpy_postings',
                'pair_sample_funds': n, 'head_funds': hn, 'head_size_covered': len(sized), 'head_size_missing': n-len(sized),
                'head_selection_scope': '同报告期产品份额 fund_scale.scale_yi 合计降序；非PIT；任一合格产品缺同季规模则头部重合度留空',
                'head_selection_complete': len(sized) == n,
                'top20_pct': top20/total*100 if total else None, 'top10_weight_median_pct': nav['median_pct'],
                'top10_weight_valid_identities': nav['coverage']['valid_nav_weights'],
                'top10_weight_coverage': nav['coverage'], 'top10_weight_scope': nav['scope'],
                'product_coverage': view['coverage'], 'disclosure':view['disclosure'],
                'coverage_scope': '主动权益及指数增强A股前十大；当期关系去重；未归并主动份额独立保留',
                'missing': ([] if n >= 2 else ['少于两只基金有至少5只A股前十大']) + ([] if len(sized)==n else ['部分合格产品缺同季度规模；无法确认真实头部集合，头部重合度留空'])})
            historical_stocks.append({'period': p.isoformat(), 'key': f'{p.year}Q{(p.month-1)//3+1}', 'values': dict(stock_values), 'counts': dict(stock_fund_counts), 'known_product_counts':dict(known_product_counts), 'unmerged_share_counts':dict(unmerged_share_counts), 'total': total})
            del view
    finally:
        c.close()
    disclosure = disclosure_history({r['period']:r['product_coverage']['active_products_or_unmerged_shares'] for r in output}, {r['period']:r['disclosure'] for r in output})
    for row in output:
        row['disclosure'] = disclosure[row['period']]
        row['disclosing'] = row['disclosure']['disclosing']
        row['disclosed_identity_count'] = row['disclosure']['disclosed_identity_count']
        row['prior_year_identity_count'] = row['disclosure']['prior_year_identity_count']
    stock_rows = []
    if historical_stocks and historical_stocks[-1]['total']:
        latest = historical_stocks[-1]
        previous = historical_stocks[-2] if len(historical_stocks) > 1 else None
        for symbol, value in sorted(latest['values'].items(), key=lambda kv: (-kv[1], kv[0]))[:30]:
            share = value / latest['total'] * 100
            prev_share = (previous['values'].get(symbol, 0) / previous['total'] * 100
                          if previous and previous['total'] else None)
            stock_rows.append({'code': symbol, 'name': stock_names.get(symbol) or symbol,
                'l1_name': industry_map.get(symbol), 'share_pct': share,
                'share_chg_pp': share-prev_share if prev_share is not None else None,
                'n_funds': latest['counts'][symbol], 'n_products': latest['known_product_counts'].get(symbol, 0),
                'n_unmerged_shares': latest['unmerged_share_counts'].get(symbol, 0),
                'n_identities':latest['counts'][symbol],
                'count_scope':'n_products为已验证或近似归并产品；未归并主动份额单列，不充作产品数',
                'spark': [{'period': x['key'], 'n_products': x['known_product_counts'].get(symbol, 0), 'n_unmerged_shares':x['unmerged_share_counts'].get(symbol, 0)}
                          for x in historical_stocks[-12:]],
                'share_spark': [{'period': x['key'], 'share_pct': x['values'].get(symbol, 0) / x['total'] * 100 if x['total'] else None}
                                for x in historical_stocks[-12:]]})
    result = {'as_of': as_of.isoformat(), 'mapping_date': mapping_date.isoformat() if mapping_date else None,
        'periods': output, 'stocks': stock_rows,
        'stocks_scope': '主动权益及指数增强产品前十大市值；未归并主动份额独立保留；未观察持仓的有效报告期按样本内0处理',
        'series': {'jac_all_pct': [x['jaccard_all_pct'] for x in output],
                   'jac_head_pct': [x['jaccard_head_pct'] for x in output],
                   'jac_head_estimate_pct':[x['jaccard_head_estimate_pct'] for x in output],
                   'top20_pct': [x['top20_pct'] for x in output],
                   'w10_median_pct': [x['top10_weight_median_pct'] for x in output],
                   'period_keys': [x['key'] for x in output]},
        'scope': '独立实现：主动权益及指数增强产品A股前十大；当期验证去重，历史关系回看标approx；未归并主动份额单列；非全部已验证产品',
        'method': '每期分块稀疏矩阵计算全部基金两两交集；分母包含零交集组合；头部按同季度已知基金规模选取，非PIT',
        'classification_scope': (classification or {}).get('scope', 'caller supplied active symbols'),
        'source_datasets': ['fund_holdings', 'industry_members', 'industry_taxonomy', 'instruments', 'fund_scale', 'fund_product_share_observations', 'fund_profiles'], 'independent_implementation': True}

    fixed_cache.store('overlap_index',fixed_key,result)
    return result
