"""Reproducible industry deviation against each period's local A-share benchmark.

Current SW2021 classification is used retrospectively.  Results describe only
available active-product holdings and valuation records. Relation coverage is
explicit; current industry classifications are not point-in-time history.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from crowding.crowd_index import crowd_index
from crowding.industry_benchmark import _level_one_sql, _path
from crowding.lake import DEFAULT_LAKE_ROOT
from crowding.resources import connect


def _deviation(funds: list[dict[str, Any]], benchmark: dict[str, float]) -> tuple[float | None, list[str]]:
    """Half the L1 distance on compatible, nonempty percent distributions."""
    fund_weights = {x['l1_code']: x['share_pct'] for x in funds if x.get('l1_code') and x.get('share_pct') is not None}
    if not fund_weights or not benchmark:
        return None, []
    missing = sorted(set(fund_weights) - set(benchmark))
    # A missing benchmark category may mean an incomplete market snapshot.
    # It cannot be silently interpreted as zero market value.
    if missing:
        return None, missing
    return sum(abs(fund_weights.get(k, 0) - v) for k, v in benchmark.items()) / 2, []


def industry_deviation(*, as_of: date, lake_root: Path = DEFAULT_LAKE_ROOT,
                       start_period: date = date(2020, 1, 1), max_periods: int = 31,
                       index_data: dict[str, Any] | None = None,
                       max_valuation_age_days: int = 7, share_to_master=None, active_symbols=None, classification=None) -> dict[str, Any]:
    """Compute every period against the last valuation snapshot on/before it.

    ``index_data`` may be an existing crowd_index response for the identical
    lake/as_of, avoiding a duplicate full holdings aggregation. The mapping
    snapshot must match as well; it is checked here.
    """
    root = Path(lake_root).expanduser().resolve()
    paths = {n: _path(root, n) for n in ('valuation_metrics', 'industry_members', 'industry_taxonomy')}
    missing = [n for n, p in paths.items() if not p]
    if missing:
        return {'as_of': as_of.isoformat(), 'periods': [], 'reason': '缺少数据集：' + ','.join(missing)}
    if max_periods < 1:
        raise ValueError('max_periods must be positive')
    if max_valuation_age_days < 0:
        raise ValueError('max_valuation_age_days must be non-negative')
    idx = index_data if index_data is not None else crowd_index(as_of=as_of, lake_root=root, start_period=start_period, max_periods=max_periods, share_to_master=share_to_master, active_symbols=active_symbols, classification=classification)
    if idx.get('as_of') != as_of.isoformat():
        raise ValueError('index_data as_of mismatch')
    rows = [r for r in idx.get('periods', []) if start_period <= date.fromisoformat(r['period']) <= as_of][-max_periods:]
    if not rows:
        return {'as_of': as_of.isoformat(), 'periods': [], 'reason': idx.get('reason', '没有可用基金行业历史')}
    mapping_date = idx.get('mapping_date')
    if not mapping_date:
        return {'as_of': as_of.isoformat(), 'periods': [], 'reason': '基金行业分类日期缺失'}

    conn = connect()
    try:
        latest_mapping = conn.execute(f"""SELECT MAX(as_of_date) FROM read_parquet('{paths['industry_members']}',hive_partitioning=false)
          WHERE classification_system='sw' AND source='sw' AND as_of_date<=CAST(? AS DATE)""", [as_of]).fetchone()[0]
        if not latest_mapping or latest_mapping.isoformat() != mapping_date:
            raise ValueError('index_data classification mapping mismatch')
        period_dates = [date.fromisoformat(x['period']) for x in rows]
        dates = conn.execute(f"""SELECT DISTINCT trade_date FROM read_parquet('{paths['valuation_metrics']}',hive_partitioning=false)
            WHERE trade_date BETWEEN CAST(? AS DATE) - INTERVAL (?::INTEGER) DAY AND CAST(? AS DATE)
            ORDER BY 1""", [min(period_dates), max_valuation_age_days, max(period_dates)]).fetchall()
        valuation_dates = [x[0] for x in dates]
        selected = {}
        for p in period_dates:
            candidate = next((d for d in reversed(valuation_dates) if d <= p), None)
            selected[p] = candidate if candidate and (p - candidate).days <= max_valuation_age_days else None
        used = sorted(set(x for x in selected.values() if x is not None))
        if not used:
            aggregates = []
        else:
            aggregates = conn.execute(f"""
              WITH v AS (
                SELECT symbol,trade_date,float_mv FROM read_parquet('{paths['valuation_metrics']}',hive_partitioning=false)
                WHERE trade_date IN (SELECT UNNEST(?::DATE[])) AND float_mv IS NOT NULL AND isfinite(float_mv) AND float_mv>0
                  AND symbol ~ '^[0-9]{{6}}\\.(SH|SZ|BJ)$'
                QUALIFY ROW_NUMBER() OVER(PARTITION BY trade_date,symbol ORDER BY fetched_at DESC NULLS LAST)=1
              ), i AS (
                SELECT symbol,industry_code FROM read_parquet('{paths['industry_members']}',hive_partitioning=false)
                WHERE classification_system='sw' AND source='sw' AND as_of_date=CAST(? AS DATE)
                QUALIFY ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY fetched_at DESC NULLS LAST)=1
              ), t AS (
                SELECT industry_code,parent_industry_code,industry_name,level FROM read_parquet('{paths['industry_taxonomy']}',hive_partitioning=false)
                WHERE classification_system='SW2021'
                QUALIFY ROW_NUMBER() OVER(PARTITION BY industry_code ORDER BY fetched_at DESC NULLS LAST)=1
              ), mapped AS (
                SELECT v.trade_date,v.symbol,v.float_mv,{_level_one_sql()}
                FROM v LEFT JOIN i ON i.symbol=v.symbol LEFT JOIN t l ON l.industry_code=i.industry_code
                LEFT JOIN t p ON p.industry_code=l.parent_industry_code LEFT JOIN t gp ON gp.industry_code=p.parent_industry_code
              ) SELECT trade_date,l1_code,l1_name,SUM(float_mv),COUNT(*) FROM mapped GROUP BY trade_date,l1_code,l1_name
            """, [used, mapping_date]).fetchall()
    finally:
        conn.close()

    market: dict[date, list[tuple[Any, ...]]] = {}
    for d, code, name, mv, count in aggregates:
        market.setdefault(d, []).append((code, name, float(mv), int(count)))
    output = []
    for row, p in zip(rows, period_dates):
        valuation_date = selected[p]
        all_groups = market.get(valuation_date, [])
        mapped = [g for g in all_groups if g[0] is not None]
        mapped_mv = sum(g[2] for g in mapped)
        total_mv = sum(g[2] for g in all_groups)
        benchmark = {g[0]: g[2] / mapped_mv * 100 for g in mapped} if mapped_mv else {}
        top, top_missing = _deviation(row.get('top10_industries', []), benchmark)
        full, full_missing = _deviation(row.get('full_industries', []), benchmark) if row.get('full') else (None, [])
        warnings = []
        if valuation_date is None: warnings.append('报告期附近没有足够新鲜的估值快照')
        if top_missing or full_missing: warnings.append('基金有行业未出现在该日基准，无法计算对应偏离度')
        if total_mv and mapped_mv / total_mv < .9: warnings.append('估值样本行业映射市值覆盖率低于90%')
        output.append({'key': row['key'], 'period': row['period'], 'valuation_date': valuation_date.isoformat() if valuation_date else None,
            'valuation_age_days': (p - valuation_date).days if valuation_date else None,
            'disclosure':row.get('disclosure'), 'disclosing':row.get('disclosing'),
            'disclosed_identity_count':row.get('disclosed_identity_count'),
            'prior_year_identity_count':row.get('prior_year_identity_count'),
            'top10_deviation_pct': top, 'full_deviation_pct': full,
            'top10_unmatched_industries': top_missing, 'full_unmatched_industries': full_missing,
            'market_total_stocks': sum(g[3] for g in all_groups), 'market_mapped_stocks': sum(g[3] for g in mapped),
            'market_mapped_mv_share_pct': mapped_mv / total_mv * 100 if total_mv else None,
            'benchmark_weights': [{'l1_code':g[0], 'name':g[1], 'weight_pct':benchmark[g[0]]} for g in mapped] if benchmark else [],
            'benchmark_industries': len(benchmark), 'top10_fund_coverage': row.get('top10_coverage', {}), 'product_coverage': row.get('product_coverage', {}),
            'full_fund_coverage': row.get('full_coverage', {}), 'warnings': warnings})
    return {'as_of': as_of.isoformat(), 'mapping_date': mapping_date, 'periods': output,
        'scope': '独立实现：主动权益及指数增强；当期验证关系去重，未归并主动份额独立保留；当前申万SW2021一级映射回看，不是历史PIT分类',
        'product_scope': idx.get('scope'), 'classification_scope': idx.get('classification_scope'),
        'method': f'行业偏离度=Σ|基金行业占比−同期本地有流通市值且有行业映射股票的行业市值占比|/2；每期选报告期当日或之前至多{max_valuation_age_days}个自然日最近估值日',
        'limitations': ['基金前十大和半年/年报全部持仓仅反映可得披露样本', '估值股票数及映射市值覆盖每期列示；覆盖相对全市场的完整性未保证', '基金缺少某基准行业按样本未观察持仓处理，不代表真实持仓为零', '分类使用当前版本回看，不是历史PIT分类'],
        'source_datasets': ['fund_holdings', 'valuation_metrics', 'industry_members', 'industry_taxonomy'],
        'independent_implementation': True}
