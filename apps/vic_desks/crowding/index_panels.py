"""Historical percentiles and report-period product NAV concentration."""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from datetime import date
from math import isfinite
from pathlib import Path
from statistics import median

from crowding.resources import connect
from crowding.product_view import load_product_view, available_periods, product_view_context, disclosure_history, period_key
from crowding.fund_classification import classify_fund_profiles


def percentile_rank(value: float | None, values: list[float]) -> float | None:
    clean = sorted(float(item) for item in values if item is not None and isfinite(item))
    if value is None or not isfinite(value) or not clean:
        return None
    left, right = bisect_left(clean, value), bisect_right(clean, value)
    if len(clean) == 1:
        return 50.0
    return max(0.0, min(100.0, 100.0 * (left + (right - left - 1) / 2) / (len(clean) - 1)))


def product_top10_nav_summary(portfolios, products, *, report_period):
    """Share the complete-product NAV contract across both index outputs.

    Unknown scales may be individual share-class NAV. Retaining an identity in
    holdings never grants it a complete-product denominator. Complete approx
    relations remain explicitly labeled as cross-period estimates.
    """
    weights, exclusions = [], []
    valid_relations = Counter()
    for product, values in portfolios.items():
        meta = products[product]
        status = meta.get('relation_status')
        reason = None
        if status not in ('exact', 'approx'):
            reason = 'product_relation_unverified'
        elif len(values) < 5:
            reason = 'fewer_than_five_a_stock_holdings'
        else:
            scale = meta.get('scale_yi')
            if (scale is None or not isfinite(scale) or scale <= 0
                    or str(meta.get('scale_period')) != str(report_period)
                    or meta.get('scale_status') != 'same_period_share_nav_sum_non_pit'):
                reason = 'missing_or_conflicting_same_period_scale'
            else:
                ratio = sum(sorted(values, reverse=True)[:10]) / (scale * 1e8) * 100
                if isfinite(ratio) and 0 < ratio <= 100:
                    weights.append(ratio)
                    valid_relations[status] += 1
                else:
                    reason = 'invalid_top10_nav_weight'
        if reason:
            exclusions.append({'product_symbol': product, 'reason': reason})
    reasons = Counter(row['reason'] for row in exclusions)
    return {'median_pct': median(weights) if weights else None,
        'coverage': {'candidate_identities': len(portfolios), 'valid_nav_weights': len(weights),
            'valid_exact_products': valid_relations['exact'],
            'valid_approx_products': valid_relations['approx'],
            'excluded_unknown_relation_identities': reasons['product_relation_unverified'],
            'exclusion_counts': dict(reasons), 'excluded': exclusions},
        'scope': '仅当期exact及完整approx产品、同季完整份额净资产；approx为跨期关系估算；unknown份额不进入产品净值权重中位数；规模非PIT'}


def top10_weight_history(*, as_of: date, lake_root: str | Path,
                         share_to_master: dict[str, str] | None = None,
                         active_symbols: set[str] | None = None, start_period: date = date(2019, 3, 31),
                         classification=None) -> dict:
    root = Path(lake_root).expanduser().resolve()
    holdings = [str(p) for p in (root / 'curated/fund_holdings').rglob('*.parquet')]
    if not holdings:
        return {'series': {}, 'periods': [], 'reason': 'holdings_missing'}
    if classification is None and active_symbols is None:
        classification = classify_fund_profiles(lake_root=root)
    conn = connect()
    keyed, coverage, details, counts = {}, {}, {}, {}
    try:
        periods = available_periods(conn=conn, holdings=holdings, as_of=as_of, start_period=start_period)
        context = product_view_context(lake_root=root, as_of=as_of, share_to_master=share_to_master,
            active_symbols=active_symbols, classification=classification)
        for period in periods:
            view = load_product_view(as_of=as_of, report_period=period, lake_root=root, conn=conn,
                share_to_master=share_to_master, active_symbols=active_symbols, classification=classification, context=context)
            portfolios = defaultdict(list)
            for row in view['rows']:
                portfolios[row['product_symbol']].append(row['market_value'])
            nav = product_top10_nav_summary(portfolios, view['products'], report_period=period)
            counts[str(period)] = view['coverage']['active_products_or_unmerged_shares']
            key = period_key(period)
            keyed[key] = nav['median_pct']
            coverage[key] = nav['coverage']['valid_nav_weights']
            details[key] = dict(view['coverage'], disclosure=view['disclosure'], **nav['coverage'],
                nav_scope=nav['scope'],
                relation_diagnostics={'conflicts':view['diagnostics']['conflicts'][:100],
                    'conflict_count':len(view['diagnostics']['conflicts']),
                    'unknown_count':len(view['diagnostics']['unknown']),
                    'scale_exclusion_count':len(view['diagnostics']['scale_exclusions'])}, quality='partial' if nav['coverage']['excluded'] or view['coverage']['unmerged_active_shares'] else 'estimated')
            del view
    finally:
        conn.close()
    disclosure = disclosure_history(counts, {period:details[period_key(period)]['disclosure'] for period in counts})
    for period, metadata in disclosure.items():
        details[period_key(period)]['disclosure'] = metadata
    return {'disclosure':disclosure, 'series': keyed, 'periods': [period_key(p) for p in periods], 'coverage': coverage,
        'coverage_details': details,
        'scope': '主动权益及指数增强；按期产品股票去重后取前十；同期完整份额净资产合计，非PIT；仅exact及完整approx产品入中位数，approx为跨期关系估算；未归并主动份额不进入产品净值权重中位数',
        'classification_scope': (classification or {}).get('scope', 'caller supplied active symbols')}
