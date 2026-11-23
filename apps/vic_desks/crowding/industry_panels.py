"""Industry weights of the largest same-report-period active identities."""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from pathlib import Path

from crowding.resources import connect
from crowding.product_view import load_product_view


def top100_industry_weights(*, as_of: date, period: date, lake_root: str | Path,
                            share_to_master: dict[str, str] | None = None,
                            active_symbols: set[str] | None = None,
                            classification=None, include_metadata=False) -> dict:
    """Legacy return is name→percentage; include_metadata exposes coverage.

    Missing same-period NAV excludes an identity from the known-size subset.
    Such a subset cannot be described as the actual complete top 100.
    """
    root = Path(lake_root).expanduser().resolve()
    industry_paths = [str(p) for p in (root / 'curated/industry_members').rglob('*.parquet')]
    taxonomy_paths = [str(p) for p in (root / 'curated/industry_taxonomy').rglob('*.parquet')]
    conn = connect()
    try:
        view = load_product_view(as_of=as_of, report_period=period, lake_root=root, conn=conn,
            share_to_master=share_to_master, active_symbols=active_symbols, classification=classification)
        active = {k: p for k,p in view['products'].items() if p['is_active']}
        sized = [(k,p) for k,p in active.items() if p['scale_yi'] is not None]
        selected = sorted(sized, key=lambda x: (-x[1]['scale_yi'], x[0]))[:100]
        codes = {k for k,_ in selected}
        industries = {}
        mapping_date = None
        if industry_paths and taxonomy_paths:
            mapping_date = conn.execute('''SELECT MAX(as_of_date)
                FROM read_parquet(?,hive_partitioning=false,union_by_name=true)
                WHERE classification_system='sw' AND source='sw' AND as_of_date<=CAST(? AS DATE)''', [industry_paths, as_of]).fetchone()[0]
            if mapping_date:
                rows = conn.execute('''WITH i AS (
                    SELECT symbol,industry_code FROM read_parquet(?,hive_partitioning=false,union_by_name=true)
                    WHERE classification_system='sw' AND source='sw' AND as_of_date=CAST(? AS DATE)
                    QUALIFY ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY fetched_at DESC NULLS LAST)=1),
                  t AS (SELECT industry_code,parent_industry_code,industry_name,level
                    FROM read_parquet(?,hive_partitioning=false,union_by_name=true)
                    WHERE classification_system='SW2021'
                    QUALIFY ROW_NUMBER() OVER(PARTITION BY industry_code ORDER BY fetched_at DESC NULLS LAST)=1)
                  SELECT i.symbol,CASE WHEN l.level='L1' THEN l.industry_name
                    WHEN l.level='L2' THEN p.industry_name WHEN l.level='L3' THEN gp.industry_name END
                  FROM i LEFT JOIN t l ON i.industry_code=l.industry_code
                    LEFT JOIN t p ON p.industry_code=l.parent_industry_code
                    LEFT JOIN t gp ON gp.industry_code=p.parent_industry_code''', [industry_paths,mapping_date,taxonomy_paths]).fetchall()
                industries = {s:n for s,n in rows if n}
    finally:
        conn.close()
    values, mapped_value, total_value = defaultdict(float), 0.0, 0.0
    for row in view['rows']:
        if row['product_symbol'] not in codes:
            continue
        total_value += row['market_value']
        name = industries.get(row['holding_symbol'])
        if name:
            values[name] += row['market_value']
            mapped_value += row['market_value']
    weights = {name: value / mapped_value * 100 for name,value in values.items()} if mapped_value else {}
    if not include_metadata:
        return weights
    return {'weights': weights, 'period': period.isoformat(), 'mapping_date': mapping_date.isoformat() if mapping_date else None,
        'coverage': dict(view['coverage'], selected_identities=len(selected), same_period_size_missing=len(active)-len(sized),
            selection_complete=len(active)==len(sized), mapped_value_share_pct=mapped_value/total_value*100 if total_value else None),
        'selected': [{'product_symbol': k, 'scale_yi': p['scale_yi'], 'relation_status':p['relation_status']} for k,p in selected],
        'scope': '主动权益及指数增强；按目标报告期净资产合计选择已知规模前100；规模缺失时为已知规模子集，非完整头部100；未归并主动份额独立保留',
        'diagnostics': view['diagnostics'], 'disclosure':view['disclosure']}
