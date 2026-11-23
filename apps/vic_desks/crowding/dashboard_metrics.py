"""Compose page-shaped metrics from auditable stock and fund inputs."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from crowding.lake import DEFAULT_LAKE_ROOT
from crowding.stock_extras import previous_period_deltas, valuation_percentiles
from crowding.top10_changes import lake_top10_changes


def _finite(value: Any) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value == value and value not in (float('inf'), float('-inf')) else None


def stock_rows(*, snapshot: dict, previous_snapshot: dict | None, board: dict,
               liquidity: dict, market: dict, as_of: date,
               lake_root: str | Path = DEFAULT_LAKE_ROOT,
               include_top10_changes: bool = True) -> dict:
    """Return all publicly described stock-table fields with quality gaps."""
    board_rows = board.get('rows', {}) if board else {}
    liquidity_rows = liquidity.get('rows', {}) if liquidity else {}
    market_rows = {row['symbol']: row for row in market.get('rows', [])} if market else {}
    previous = previous_period_deltas(snapshot, previous_snapshot or {'rows': []})
    pe_pb = valuation_percentiles([row['symbol'] for row in snapshot.get('rows', [])],
                                  as_of=as_of, lake_root=lake_root)
    top10 = {'rows': [], 'metadata': {'reason': 'disabled'}}
    if include_top10_changes:
        try:
            top10 = lake_top10_changes(as_of=as_of, lake_root=lake_root)
        except Exception as exc:
            top10 = {'rows': [], 'metadata': {'reason': f'{type(exc).__name__}: {exc}'}}
    top10_rows = {row['symbol']: row for row in top10.get('rows', [])}
    output = []
    for source in snapshot.get('rows', []):
        symbol = source['symbol']
        metric = board_rows.get(symbol, {})
        price = market_rows.get(symbol, {})
        delta = previous.get(symbol, {})
        valuation = pe_pb.get(symbol, {})
        change = top10_rows.get(symbol, {})
        amount5 = _finite(price.get('turnover_5d_amount'))
        float_mv = _finite(source.get('float_mv'))
        large = _finite((metric.get('flow') or {}).get('big_order_30d'))
        margin_ratio = _finite((metric.get('flow') or {}).get('margin_balance_change_ratio_30d'))
        score = _finite(metric.get('score'))
        output.append({
            **source,
            **delta,
            'score': score,
            'band': 'high' if score is not None and score >= 85 else 'warn' if score is not None and score >= 70 else 'normal' if score is not None else None,
            'dims': metric.get('percentiles'),
            'dimension_raw': metric.get('dimensions'),
            'weights_used': metric.get('weights_used'),
            'dims_missing': [key for key, value in (metric.get('percentiles') or {}).items() if value is None],
            'pool': bool(metric.get('pool')),
            'pool_sources': metric.get('pool_sources'),
            'liquidation': (source.get('holding_mv') / liquidity_rows.get(symbol, {}).get('avg_turnover')
                            if source.get('holding_mv') is not None
                            and (liquidity_rows.get(symbol, {}).get('avg_turnover') or 0) > 0 else None),
            'avg_turnover': _finite(liquidity_rows.get(symbol, {}).get('avg_turnover')),
            'turnover5_pct': amount5 / float_mv * 100 if amount5 is not None and float_mv and float_mv > 0 else None,
            'return_pct': _finite(price.get('period_return', {}).get('value')) if isinstance(price.get('period_return'), dict) else _finite(price.get('period_return')),
            'drawdown_pct': _finite(price.get('drawdown_52w')),
            'high20_pct': _finite(price.get('high_20d_return')),
            'margin30_pct': margin_ratio * 100 if margin_ratio is not None else None,
            'large30_pct': large / float_mv * 100 if large is not None and float_mv and float_mv > 0 else None,
            'pe3y': valuation.get('pe_3y_pct'),
            'pb3y': valuation.get('pb_3y_pct'),
            'top10_chg_pct': _finite(change.get('delta_pct')) * 100 if change.get('delta_pct') is not None else None,
            'top10_chg_mv': _finite(change.get('delta_value')),
            'top10_cover_pct': _finite(change.get('coverage')) * 100 if change.get('coverage') is not None else None,
            'top10_current_shares': _finite(change.get('current_shares')),
            'top10_previous_shares': _finite(change.get('previous_shares')),
            'top10_prior_products': _finite(change.get('prior_products')),
            'top10_new_in': change.get('new_in'), 'top10_out': change.get('new_out'),
            'top10_eligible_for_rank': change.get('eligible_for_rank'),
            'top10_quality_flags': change.get('quality_flags', []),
        })
    return {'rows': output, 'metadata': {
        **snapshot.get('metadata', {}),
        'stock_count': len(output),
        'top10_change_status': 'approximate' if top10.get('rows') else top10.get('metadata', {}).get('reason'),
        'top10_change_metadata': top10.get('metadata', {}),
        'missing_policy': 'available deterministic or sourced values are returned; unsupported values remain null and carry reasons',
    }}
