"""Independent historical industry-concentration metrics.

Computes parallel active-product top-ten and full published-holdings views.
Unmerged shares and approximate relations are counted explicitly.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from pathlib import Path
from crowding import fixed_aggregate_cache as fixed_cache
from typing import Any

import duckdb
from crowding.resources import connect as bounded_connect

from crowding.lake import DEFAULT_LAKE_ROOT
from crowding.product_view import load_product_view, available_periods, product_view_context, disclosure_history
from crowding.fund_classification import classify_fund_profiles


def _path(root: Path, dataset: str) -> str | None:
    folder = root / "curated" / dataset
    if not folder.exists() or not any(folder.rglob("*.parquet")):
        return None
    return (folder / "**" / "*.parquet").resolve().as_posix()


def _l1_sql() -> str:
    return """
      CASE WHEN l.level='L1' THEN l.industry_code
           WHEN l.level='L2' THEN p.industry_code
           WHEN l.level='L3' THEN gp.industry_code END AS l1_code,
      CASE WHEN l.level='L1' THEN l.industry_name
           WHEN l.level='L2' THEN p.industry_name
           WHEN l.level='L3' THEN gp.industry_name END AS l1_name
    """


def _metrics(rows: list[dict[str, Any]]) -> tuple[float | None, float | None, float]:
    ordered = sorted(rows, key=lambda x: x["market_value"], reverse=True)
    total = sum(x["market_value"] for x in ordered)
    if not total:
        return None, None, 0.0
    shares = [x["market_value"] / total * 100 for x in ordered]
    return sum(shares[:3]), sum((s / 100) ** 2 for s in shares) * 10000, total


def crowd_index(*, as_of: date, lake_root: Path = DEFAULT_LAKE_ROOT,
                start_period: date = date(2019, 3, 31), max_periods: int = 31,
                share_to_master=None, active_symbols=None, classification=None) -> dict[str, Any]:
    root = Path(lake_root).expanduser().resolve()
    paths = {n: _path(root, n) for n in ("fund_holdings", "industry_members", "industry_taxonomy")}
    missing = [n for n, v in paths.items() if v is None]
    if missing:
        return {"as_of": as_of.isoformat(), "periods": [], "series": {}, "reason": f"缺少数据集：{','.join(missing)}"}
    conn = bounded_connect()
    try:
        if max_periods < 1:
            raise ValueError('max_periods must be positive')
        periods = available_periods(conn=conn, holdings=[paths['fund_holdings']], as_of=as_of,
                                    start_period=start_period, max_periods=max_periods)
        if not periods:
            return {"as_of": as_of.isoformat(), "periods": [], "series": {}, "reason": "截止日之前没有持仓报告期"}
        mapping_date = conn.execute(f"""
          SELECT MAX(as_of_date) FROM read_parquet('{paths['industry_members']}',hive_partitioning=false)
          WHERE classification_system='sw' AND source='sw' AND as_of_date<=CAST(? AS DATE)
        """, [as_of]).fetchone()[0]
        if mapping_date is None:
            return {"as_of": as_of.isoformat(), "periods": [], "series": {}, "reason": "截止日之前没有申万行业成员映射"}
        mapped_rows = conn.execute(f"""
          WITH i AS (SELECT symbol,industry_code FROM read_parquet('{paths['industry_members']}',hive_partitioning=false)
            WHERE classification_system='sw' AND source='sw' AND as_of_date=CAST(? AS DATE)
            QUALIFY ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY fetched_at DESC NULLS LAST)=1),
          t AS (SELECT industry_code,parent_industry_code,industry_name,level
            FROM read_parquet('{paths['industry_taxonomy']}',hive_partitioning=false)
            WHERE classification_system='SW2021'
            QUALIFY ROW_NUMBER() OVER(PARTITION BY industry_code ORDER BY fetched_at DESC NULLS LAST)=1)
          SELECT i.symbol,{_l1_sql()} FROM i
            LEFT JOIN t l ON i.industry_code=l.industry_code
            LEFT JOIN t p ON p.industry_code=l.parent_industry_code
            LEFT JOIN t gp ON gp.industry_code=p.parent_industry_code
        """, [mapping_date]).fetchall()
        industries = {symbol: (code, name) for symbol, code, name in mapped_rows if code}
        if classification is None and active_symbols is None:
            classification = classify_fund_profiles(lake_root=root)
        context = product_view_context(lake_root=root, as_of=as_of, share_to_master=share_to_master,
            active_symbols=active_symbols, classification=classification)
        fixed_key = fixed_cache.cache_key(name='crowd_index',lake_root=root,
            context_version=context.input_version,periods=periods,
            dependencies={'mapping_date':str(mapping_date),'industries':industries,
                'start_period':str(start_period),'max_periods':max_periods},
            code_paths=[Path(__file__),Path(fixed_cache.__file__)])
        cached = fixed_cache.load('crowd_index',fixed_key,as_of=as_of,context=context)
        if cached is not None:
            return cached
        aggregate_rows, coverage_rows, product_coverage, cached_disclosure = [], [], {}, {}
        for period in periods:
            view = load_product_view(as_of=as_of, report_period=period, lake_root=root, conn=conn,
                share_to_master=share_to_master, active_symbols=active_symbols, classification=classification, context=context)
            product_coverage[period] = view['coverage']
            cached_disclosure[str(period)] = view['disclosure']
            per_product = defaultdict(list)
            for row in view['rows']:
                per_product[row['product_symbol']].append(row)
            bases = {'full': view['rows'], 'top10': [r for rows in per_product.values()
                for r in sorted(rows, key=lambda r: (-r['market_value'], r['holding_symbol']))[:10]]}
            for basis, observations in bases.items():
                groups = defaultdict(lambda: {'value': 0.0, 'products': set(), 'observations': 0})
                mapped_count = 0
                for row in observations:
                    industry = industries.get(row['holding_symbol'])
                    if not industry:
                        continue
                    mapped_count += 1
                    group = groups[industry]
                    group['value'] += row['market_value']
                    group['products'].add(row['product_symbol'])
                    group['observations'] += 1
                coverage_rows.append((period, basis, len(per_product), len(observations), mapped_count))
                for (code, name), g in groups.items():
                    aggregate_rows.append((period,basis,code,name,g['value'],len(g['products']),g['observations']))
            del view, per_product, bases
    finally:
        conn.close()
    by_basis: dict[tuple[date, str], list[dict[str, Any]]] = {}
    for p, basis, code, name, value, funds, observations in aggregate_rows:
        by_basis.setdefault((p, basis), []).append({"l1_code": code, "name": name, "market_value": float(value), "funds": int(funds), "observations": int(observations)})
    cov = {(p, basis): {"funds": int(f), "observations": int(o), "mapped_observations": int(m)} for p, basis, f, o, m in coverage_rows}
    disclosure = disclosure_history({str(p): cov['active_products_or_unmerged_shares'] for p,cov in product_coverage.items()}, cached_disclosure)
    period_rows, top3_series, hhi_series, full_top3_series, full_hhi_series = [], [], [], [], []
    industry_details: dict[str, dict[str, list[dict[str, Any]]]] = {"top10": {}, "full": {}}
    for p in periods:
        top3, hhi, top_total = _metrics(by_basis.get((p, "top10"), [])); full_top3, full_hhi, full_total = _metrics(by_basis.get((p, "full"), []))
        full_candidate = p.month in (6, 12)
        # Q1/Q3 disclosure generally contains only top-ten positions. Calling
        # those observations full holdings would manufacture a full series.
        if not full_candidate:
            full_top3, full_hhi, full_total = None, None, None
        key = f"{p.year}Q{(p.month - 1) // 3 + 1}"
        item = {"key": key, "period": p.isoformat(), "full": full_candidate,
                "disclosure_state": "full_disclosure_candidate" if full_candidate else "quarterly_top10_candidate",
                "top3_pct": top3, "hhi": hhi, "full_top3_pct": full_top3, "full_hhi": full_hhi,
                "top10_top3_pct": top3, "top10_hhi": hhi, "top10_mapped_value": top_total, "full_mapped_value": full_total,
                "top10_coverage": cov.get((p, "top10"), {}), "full_coverage": cov.get((p, "full"), {}),
                "industry_count_top10": len(by_basis.get((p, "top10"), [])), "industry_count_full": len(by_basis.get((p, "full"), [])),
                "coverage_scope": "主动权益及指数增强；当期已验证关系去重；未归并主动份额独立保留",
                "product_coverage": product_coverage[p], "disclosure":disclosure[str(p)],
                "disclosing":disclosure[str(p)]["disclosing"],
                "disclosed_identity_count":disclosure[str(p)]["disclosed_identity_count"],
                "prior_year_identity_count":disclosure[str(p)]["prior_year_identity_count"]}
        for basis, field in (("top10", "top10_industries"), ("full", "full_industries")):
            source = sorted(by_basis.get((p, basis), []), key=lambda row: row["market_value"], reverse=True) if basis != "full" or full_candidate else []
            total = sum(row["market_value"] for row in source)
            details = []
            for rank, row in enumerate(source, 1):
                details.append({
                    "rank": rank,
                    "l1_code": row["l1_code"],
                    "name": row["name"],
                    "market_value": row["market_value"],
                    "share_pct": row["market_value"] / total * 100 if total else None,
                    "funds": row["funds"],
                    "observations": row["observations"],
                })
            item[field] = details
            industry_details[basis][key] = details
        period_rows.append(item); top3_series.append(top3); hhi_series.append(hhi); full_top3_series.append(full_top3); full_hhi_series.append(full_hhi)
    industry_series: dict[str, list[dict[str, Any]]] = {}
    for basis, period_map in industry_details.items():
        codes = {}
        for key, details in period_map.items():
            for detail in details:
                codes.setdefault(detail["l1_code"], detail["name"])
        basis_series = []
        for code, name in sorted(codes.items()):
            points = []
            for period in period_rows:
                detail = next((x for x in period[f"{basis}_industries"] if x["l1_code"] == code), None)
                points.append({
                    "key": period["key"], "period": period["period"],
                    "share_pct": detail["share_pct"] if detail else None,
                    "market_value": detail["market_value"] if detail else None,
                    "rank": detail["rank"] if detail else None,
                })
            basis_series.append({"l1_code": code, "name": name, "points": points})
        industry_series[basis] = basis_series
    latest_max_industry = {}
    for basis in ("top10", "full"):
        latest = next((x[f"{basis}_industries"] for x in reversed(period_rows) if x[f"{basis}_industries"]), [])
        latest_max_industry[basis] = latest[0] if latest else None
    result = {"as_of": as_of.isoformat(), "mapping_date": mapping_date.isoformat(), "periods": period_rows,
            "series": {"top3_pct": top3_series, "hhi": hhi_series, "full_top3_pct": full_top3_series, "full_hhi": full_hhi_series, "period_keys": [x["key"] for x in period_rows]},
            "industry_series": industry_series,
            "latest_max_industry": latest_max_industry,
            "scope": "独立实现：主动权益及指数增强，当前申万SW2021一级回看；当期产品股票去重后取前十；未归并主动份额独立保留，非全部已验证产品",
            "method": "公告版本去重+沪深北交所持仓过滤；top10按每产品每期market_value排序，full保留全部去重持仓；HHI=Σ(行业占比)^2×10000",
            "classification_scope": (classification or {}).get("scope", "caller supplied active symbols"),
            "source_datasets": ["fund_holdings", "industry_members", "industry_taxonomy", "fund_product_share_observations", "fund_profiles"], "independent_implementation": True}

    fixed_cache.store('crowd_index',fixed_key,result)
    return result
