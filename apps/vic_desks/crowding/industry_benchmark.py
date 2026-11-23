"""Independent SW2021 level-one all-A float-market-value benchmark.

The original board's ``bench_pct`` is a server-side value whose exact universe
and vendor are not exposed by the captured response.  This adapter computes a
reproducible local benchmark from the published lake and returns the dates,
coverage and unresolved symbols needed to explain differences.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import duckdb
from crowding.resources import connect as bounded_connect

from crowding.lake import DEFAULT_LAKE_ROOT


def _path(root: Path, dataset: str) -> str | None:
    directory = root / "curated" / dataset
    if not directory.exists() or not any(directory.rglob("*.parquet")):
        return None
    return (directory / "**" / "*.parquet").resolve().as_posix()


def _level_one_sql() -> str:
    return """
      CASE
        WHEN l.level='L1' THEN l.industry_code
        WHEN l.level='L2' THEN p.industry_code
        WHEN l.level='L3' THEN gp.industry_code
      END AS l1_code,
      CASE
        WHEN l.level='L1' THEN l.industry_name
        WHEN l.level='L2' THEN p.industry_name
        WHEN l.level='L3' THEN gp.industry_name
      END AS l1_name
    """


def industry_benchmark(*, as_of: date, lake_root: Path = DEFAULT_LAKE_ROOT) -> dict[str, Any]:
    """Return all-A SW2021 L1 float-MV weights at the latest common date.

    ``valuation_metrics.float_mv`` is already normalized to CNY.  Stocks with
    no classification or no same-date float MV are kept in coverage counts and
    excluded from the denominator; no zero is fabricated.
    """
    root = Path(lake_root).expanduser().resolve()
    # Instruments metadata is not required for the benchmark itself: the
    # symbol suffix and the valuation row are sufficient to define the A-share
    # universe.  Keeping it optional also makes the adapter usable on a minimal
    # published-lake fixture.
    paths = {name: _path(root, name) for name in
             ("valuation_metrics", "industry_members", "industry_taxonomy")}
    missing = [name for name, value in paths.items() if value is None]
    if missing:
        return {"as_of": as_of.isoformat(), "rows": [], "reason": f"缺少数据集：{','.join(missing)}"}

    conn = bounded_connect()
    try:
        valuation_date = conn.execute(f"""
          SELECT MAX(trade_date) FROM read_parquet('{paths['valuation_metrics']}', hive_partitioning=false)
          WHERE trade_date<=CAST(? AS DATE)
        """, [as_of]).fetchone()[0]
        mapping_date = conn.execute(f"""
          SELECT MAX(as_of_date) FROM read_parquet('{paths['industry_members']}', hive_partitioning=false)
          WHERE classification_system='sw' AND source='sw' AND as_of_date<=CAST(? AS DATE)
        """, [valuation_date or as_of]).fetchone()[0]
        if valuation_date is None or mapping_date is None:
            return {"as_of": as_of.isoformat(), "rows": [], "reason": "共同行情/行业日期缺失"}
        rows = conn.execute(f"""
          WITH v AS (
            SELECT symbol, float_mv
            FROM read_parquet('{paths['valuation_metrics']}', hive_partitioning=false)
            WHERE trade_date=CAST(? AS DATE)
              AND float_mv IS NOT NULL AND float_mv>0
              AND symbol ~ '^[0-9]{{6}}\\.(SH|SZ|BJ)$'
            QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY fetched_at DESC NULLS LAST)=1
          ), i AS (
            SELECT symbol, industry_code
            FROM read_parquet('{paths['industry_members']}', hive_partitioning=false)
            WHERE classification_system='sw' AND source='sw' AND as_of_date=CAST(? AS DATE)
            QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY fetched_at DESC NULLS LAST)=1
          ), t AS (
            SELECT industry_code,parent_industry_code,industry_name,level
            FROM read_parquet('{paths['industry_taxonomy']}', hive_partitioning=false)
            WHERE classification_system='SW2021'
            QUALIFY ROW_NUMBER() OVER (PARTITION BY industry_code ORDER BY fetched_at DESC NULLS LAST)=1
          ), mapped AS (
            SELECT v.symbol,v.float_mv,{_level_one_sql()}
            FROM v LEFT JOIN i ON i.symbol=v.symbol
            LEFT JOIN t l ON l.industry_code=i.industry_code
            LEFT JOIN t p ON p.industry_code=l.parent_industry_code
            LEFT JOIN t gp ON gp.industry_code=p.parent_industry_code
          )
          SELECT l1_code,l1_name,SUM(float_mv) float_mv,COUNT(*) stocks
          FROM mapped WHERE l1_code IS NOT NULL GROUP BY l1_code,l1_name
          ORDER BY float_mv DESC,l1_code
        """, [valuation_date, mapping_date]).fetchall()
        coverage = conn.execute(f"""
          WITH v AS (
            SELECT symbol,float_mv FROM read_parquet('{paths['valuation_metrics']}',hive_partitioning=false)
            WHERE trade_date=CAST(? AS DATE) AND float_mv IS NOT NULL AND float_mv>0
              AND symbol ~ '^[0-9]{{6}}\\.(SH|SZ|BJ)$'
            QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY fetched_at DESC NULLS LAST)=1
          ), i AS (
            SELECT symbol,industry_code FROM read_parquet('{paths['industry_members']}',hive_partitioning=false)
            WHERE classification_system='sw' AND source='sw' AND as_of_date=CAST(? AS DATE)
            QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY fetched_at DESC NULLS LAST)=1
          ), t AS (
            SELECT industry_code,parent_industry_code,industry_name,level
            FROM read_parquet('{paths['industry_taxonomy']}',hive_partitioning=false)
            WHERE classification_system='SW2021'
            QUALIFY ROW_NUMBER() OVER (PARTITION BY industry_code ORDER BY fetched_at DESC NULLS LAST)=1
          ), mapped AS (
            SELECT v.symbol,v.float_mv,{_level_one_sql()}
            FROM v LEFT JOIN i ON i.symbol=v.symbol
            LEFT JOIN t l ON l.industry_code=i.industry_code
            LEFT JOIN t p ON p.industry_code=l.parent_industry_code
            LEFT JOIN t gp ON gp.industry_code=p.parent_industry_code
          ) SELECT COUNT(*) total_stocks,
                   COUNT(*) FILTER (WHERE l1_code IS NOT NULL) mapped_stocks,
                   SUM(float_mv) total_float_mv,
                   SUM(float_mv) FILTER (WHERE l1_code IS NOT NULL) mapped_float_mv
          FROM mapped
        """, [valuation_date, mapping_date]).fetchone()
    finally:
        conn.close()

    total_float = float(coverage[3] or 0)
    result = []
    for code, name, value, stocks in rows:
        result.append({
            "l1_code": code,
            "name": name,
            "float_mv": float(value),
            "stock_count": int(stocks),
            "benchmark_pct": float(value) / total_float * 100 if total_float else None,
        })
    return {
        "as_of": as_of.isoformat(),
        "valuation_date": valuation_date.isoformat(),
        "classification_date": mapping_date.isoformat(),
        "rows": result,
        "coverage": {
            "total_stocks_with_float_mv": int(coverage[0] or 0),
            "mapped_stocks": int(coverage[1] or 0),
            "total_float_mv": float(coverage[2] or 0),
            "mapped_float_mv": float(coverage[3] or 0),
            "unmapped_stocks": int((coverage[0] or 0) - (coverage[1] or 0)),
            "mapped_mv_share": float(coverage[3] / coverage[2] * 100) if coverage[2] else None,
        },
        "scope": "A股股票（SH/SZ/BJ）有同日流通市值且有申万SW2021一级映射；非原站主动基金口径",
        "method": "同一估值日float_mv按申万SW2021三级→一级父链聚合；缺分类股票不进入基准分母并在coverage披露",
        "source_datasets": ["valuation_metrics", "industry_members", "industry_taxonomy"],
        "independent_implementation": True,
    }
