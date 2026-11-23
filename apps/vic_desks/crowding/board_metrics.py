"""Auditable approximation of the crowding pool and partial risk scores."""
from crowding.metrics import build_crowding_pool, compute_risk_scores


def board_metrics(snapshot, liquidity, funding=None, market=None, changes=None):
    rows = []
    amounts = liquidity.get('rows', {})
    flows = (funding or {}).get('rows', {})
    prices = {r['symbol']: r for r in (market or {}).get('rows', [])}
    for source in snapshot.get('rows', []):
        row = dict(source)
        change = (changes or {}).get(row.get('symbol'), {})
        row['delta_hold_float_ratio'] = change.get('delta_hold_float_ratio')
        row['avg_turnover_20d'] = amounts.get(row.get('symbol'), {}).get('avg_turnover')
        flow = flows.get(row.get('symbol'), {})
        price = prices.get(row.get('symbol'), {})
        row['period_return'] = price.get('period_return')
        row['drawdown_52w'] = price.get('drawdown_52w')
        row['big_order_30d'] = flow.get('big_order_30d')
        row['margin_balance_change_ratio_30d'] = flow.get('margin_balance_change_ratio_30d')
        # Published method uses the disclosure-period value to choose the
        # cohort, but current estimated market value in the score formula.
        report_value = row.get('reported_mv')
        row['has_quote'] = bool(row.get('close') and report_value is not None and report_value >= 100_000_000)
        rows.append(row)
    pool = build_crowding_pool(rows, value_key='reported_mv')
    flags = {r['symbol']: {'in_top_market_value': r['in_top_market_value'], 'in_top_float_ratio': r['in_top_float_ratio']} for r in pool}
    scored = compute_risk_scores(rows, minimum_scoring_mv=0)
    # Original quintiles are grouped by public-fund float ownership, not
    # by the composite risk score. Sort only the same eligible score cohort.
    ordered = sorted((r for r in scored if r['risk_status'] != 'outside_scoring_set'
                      and r.get('hold_float_ratio') is not None),
                     key=lambda r: (r['hold_float_ratio'], r['symbol']))
    buckets = {r['symbol']: min(5, i * 5 // len(ordered) + 1)
               for i, r in enumerate(ordered)} if ordered else {}
    result = {}
    for row in scored:
        sym = row['symbol']
        result[sym] = {'pool': sym in flags, 'pool_sources': flags.get(sym),
                       'score': row['risk_score'], 'status': row['risk_status'], 'quintile': buckets.get(sym),
                       'dimensions': row['risk_dimensions'], 'percentiles': row['risk_percentiles'], 'flow': flows.get(sym), 'price': {'period_return': row.get('period_return'), 'drawdown_52w': row.get('drawdown_52w')},
                       'weights_used': row['risk_weights_used']}
    return {'rows': result, 'metadata': {
        'holding_period': snapshot.get('metadata', {}).get('holding_period'),
        'market_date': snapshot.get('metadata', {}).get('market_date'),
        'pool_size': len(pool),
        'scored': sum(r['score'] is not None for r in result.values()), 'quintile_basis': '评分集合内占普通流通比从低到高等分',
        'scope': '全部已披露基金；仅部分份额归并；并非原站完整口径',
        'method': '披露持仓市值前200与占流通比前150并集，披露市值至少10亿元；风险分仅使用可用维度重新归一；独立实现',
        'warnings': ['加仓仅使用可比较的两期占流通变化；价格维度使用持仓期以来收益和52周高点回撤；资金维度使用本地供应商与融资余额变化率，缺失维度重新归一。', '基金份额归并不完整，样本值可能偏高。'],
    }}
