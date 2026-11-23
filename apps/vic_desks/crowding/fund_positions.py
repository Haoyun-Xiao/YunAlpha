"""Fund stock-position history and cross-sectional distributions."""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from pathlib import Path
from statistics import median
from typing import Any

from crowding.resources import connect
from crowding.lake import DEFAULT_LAKE_ROOT


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lower, upper = int(position), min(len(ordered) - 1, int(position) + 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def fund_position_history(*, as_of: date, lake_root: str | Path = DEFAULT_LAKE_ROOT,
                          max_periods: int = 6,
                          share_to_master: dict[str, str] | None = None) -> dict[str, Any]:
    root = Path(lake_root).expanduser().resolve()
    allocation_paths = [str(p) for p in (root / 'curated' / 'fund_asset_allocation_snapshot').rglob('*.parquet')]
    classification_paths = [str(p) for p in (root / 'curated' / 'fund_classification_snapshot').rglob('*.parquet')]
    if not allocation_paths or not classification_paths:
        return {'periods': [], 'boxes': [], 'by_type': {'rows': []},
                'reason': '基金仓位或分类数据缺失', 'scope': 'independent local implementation'}
    conn = connect()
    try:
        class_rows = conn.execute(
            """SELECT symbol,classification_text FROM read_parquet(?,hive_partitioning=false,union_by_name=true)
               QUALIFY ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY snapshot_date DESC,fetched_at DESC)=1""",
            [classification_paths],
        ).fetchall()
        categories = {symbol: text for symbol, text in class_rows}
        periods = [row[0] for row in conn.execute(
            """SELECT DISTINCT report_period FROM read_parquet(?,hive_partitioning=false,union_by_name=true)
               WHERE report_period<=CAST(? AS DATE) ORDER BY report_period DESC LIMIT ?""",
            [allocation_paths, as_of, max_periods],
        ).fetchall()]
        periods = sorted(periods)
        rows = conn.execute(
            """SELECT symbol,report_period,stock_nav_ratio_pct,share_nav
               FROM read_parquet(?,hive_partitioning=false,union_by_name=true)
               WHERE report_period IN (SELECT UNNEST(?::DATE[])) AND stock_nav_ratio_pct IS NOT NULL
               QUALIFY ROW_NUMBER() OVER(PARTITION BY symbol,report_period ORDER BY fetched_at DESC NULLS LAST)=1""",
            [allocation_paths, periods],
        ).fetchall()
    finally:
        conn.close()
    mapping = share_to_master or {}
    grouped: dict[tuple[date, str], dict[str, Any]] = {}
    for symbol, period, ratio, net_asset in rows:
        code = str(symbol).removesuffix('.OF')
        master = mapping.get(code)
        product = f'{master}.OF' if master else str(symbol)
        key = (period, product)
        item = grouped.setdefault(key, {'ratios': [], 'net_asset': [], 'category': categories.get(product, '未知')})
        item['ratios'].append(float(ratio))
        if net_asset is not None:
            item['net_asset'].append(float(net_asset))
    by_period: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for (period, product), item in grouped.items():
        by_period[period].append({
            'product': product, 'ratio': median(item['ratios']),
            'net_asset': sum(item['net_asset']) if item['net_asset'] else None,
            'category': item['category'],
        })
    boxes = []
    for period in periods:
        values = [row['ratio'] for row in by_period[period] if row['category'] == '混合型-偏股' and row['ratio'] is not None]
        boxes.append({'key': f'{period.year}Q{(period.month - 1) // 3 + 1}', 'period': period.isoformat(),
                      'n': len(values), 'p10': _quantile(values, .1), 'p25': _quantile(values, .25),
                      'median': _quantile(values, .5), 'p75': _quantile(values, .75), 'p90': _quantile(values, .9)})
    latest_period = periods[-1] if periods else None
    types = defaultdict(list)
    if latest_period:
        for row in by_period[latest_period]:
            if row['ratio'] is not None:
                types[row['category']].append(row)
    type_rows = []
    for category, values in sorted(types.items(), key=lambda item: -len(item[1])):
        weighted = [(row['ratio'], row['net_asset']) for row in values
                    if row['ratio'] is not None and row['net_asset'] and row['net_asset'] > 0]
        total = sum(weight for _, weight in weighted)
        type_rows.append({'type': category, 'n': len(values),
                          'med': median(row['ratio'] for row in values if row['ratio'] is not None) if values else None,
                          'w': sum(value * weight for value, weight in weighted) / total if total > 0 else None,
                          'net_asset': sum(row['net_asset'] for row in values if row['net_asset'] is not None) or None})
    return {'periods': [{'key': f'{p.year}Q{(p.month - 1) // 3 + 1}', 'period': p.isoformat()} for p in periods],
            'boxes': boxes, 'label': '偏股混合基金仓位历史',
            'by_type': {'as_of': as_of.isoformat(), 'label': '最近一期', 'period': latest_period.isoformat() if latest_period else None,
                        'rows': type_rows},
            'scope': '当前基金分类与当前/最近可验证 A/C 映射回看；仓位来源东方财富 F10，非 PIT',
            'method': '同一产品份额仓位取中位，净资产为同报告期各份额合计；箱线为 P10/P25/P50/P75/P90',
            'independent_implementation': True}
