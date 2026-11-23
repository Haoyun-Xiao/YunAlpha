"""Six full-disclosure periods, with the same all-public product view as today."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from crowding import fixed_aggregate_cache as fixed_cache

from crowding.resources import connect as bounded_connect
from crowding.lake import DEFAULT_LAKE_ROOT
from crowding.product_view import (available_periods, load_product_view,
    product_view_context, register_product_rows, period_paths, partition_period)


def industry_history(*, as_of: date, lake_root: Path = DEFAULT_LAKE_ROOT) -> dict:
    lake = Path(lake_root).expanduser().resolve()
    root = lake / 'curated'
    holdings = [str(p) for p in (root / 'fund_holdings').rglob('*.parquet')]
    members = [str(p) for p in (root / 'industry_members').rglob('*.parquet')]
    taxonomy = [str(p) for p in (root / 'industry_taxonomy').rglob('*.parquet')]
    if not holdings or not members or not taxonomy:
        return {'periods': [], 'rows': [], 'reason': '持仓或申万行业数据缺失'}
    shared = any((root / 'fund_product_share_observations').rglob('*.parquet'))
    conn = bounded_connect()
    product_coverage, disclosure = {}, {}
    try:
        mapping_date = conn.execute("SELECT max(as_of_date) FROM read_parquet(?,hive_partitioning=false) WHERE classification_system='sw' AND source='sw' AND as_of_date<=?", [members, as_of]).fetchone()[0]
        if not mapping_date:
            return {'periods': [], 'rows': [], 'reason': '截止日之前没有申万行业分类'}
        # Discover from data, including unknown legacy layouts; known physical
        # partitions outside full-report months never enter this scan.
        full_paths = [p for p in holdings if partition_period(p) is None or partition_period(p).month in (6,12)]
        periods = [p for p in available_periods(conn=conn, holdings=full_paths, as_of=as_of,
            start_period=date(1900,1,1)) if p.month in (6,12)][-6:]
        mapping_sql = """WITH m AS (
            SELECT symbol,industry_code FROM read_parquet(?,hive_partitioning=false)
            WHERE classification_system='sw' AND source='sw' AND as_of_date=?
            QUALIFY row_number() OVER(PARTITION BY symbol ORDER BY fetched_at DESC NULLS LAST)=1),
          t AS (SELECT industry_code,parent_industry_code,industry_name,level FROM read_parquet(?,hive_partitioning=false)
            WHERE classification_system='SW2021'
            QUALIFY row_number() OVER(PARTITION BY industry_code ORDER BY fetched_at DESC NULLS LAST)=1)
          SELECT m.symbol,CASE WHEN l.level='L1' THEN l.industry_name WHEN l.level='L2' THEN p.industry_name
            WHEN l.level='L3' THEN gp.industry_name END AS industry
          FROM m LEFT JOIN t l ON l.industry_code=m.industry_code
            LEFT JOIN t p ON p.industry_code=l.parent_industry_code
            LEFT JOIN t gp ON gp.industry_code=p.parent_industry_code"""
        conn.execute('CREATE TEMP TABLE history_industry_map AS ' + mapping_sql, [members,mapping_date,taxonomy])
        context = product_view_context(lake_root=lake,as_of=as_of)
        actual_mapping = conn.execute('SELECT symbol,industry FROM history_industry_map ORDER BY symbol').fetchall()
        fixed_key = fixed_cache.cache_key(name='industry_history',lake_root=lake,
            context_version=context.input_version,periods=periods,
            dependencies={'mapping_date':str(mapping_date),'industry_map':actual_mapping,'shared':shared},
            code_paths=[Path(__file__),Path(fixed_cache.__file__)])
        cached = fixed_cache.load('industry_history',fixed_key,as_of=as_of)
        if cached is not None:
            return cached
        result = []
        for period in periods:
            if shared:
                view = load_product_view(as_of=as_of,report_period=period,lake_root=lake,context=context,conn=conn)
                register_product_rows(conn,'history_product_positions',view,all_rows=True)
                source = 'SELECT holding_symbol,market_value FROM history_product_positions'
                params = []
                product_coverage[period.isoformat()] = view['coverage']
                disclosure[period.isoformat()] = view['disclosure']
                del view
            else:
                source = """SELECT holding_symbol,market_value FROM read_parquet(?,hive_partitioning=false,union_by_name=true)
                  WHERE TRY_CAST(report_period AS DATE)=CAST(? AS DATE)
                    AND announce_date IS NOT NULL AND announce_date<=CAST(? AS DATE)
                    AND regexp_matches(holding_symbol,'^[0-9]{6}\\.(SH|SZ|BJ)$')
                  QUALIFY row_number() OVER(PARTITION BY fund_symbol,holding_symbol,report_period
                    ORDER BY announce_date DESC NULLS LAST,fetched_at DESC NULLS LAST)=1"""
                params = [period_paths(holdings,period), period,as_of]
            aggregated = conn.execute(f"""WITH h AS ({source})
                SELECT i.industry,SUM(h.market_value),COUNT(*) FROM h
                LEFT JOIN history_industry_map i ON i.symbol=h.holding_symbol
                WHERE h.market_value IS NOT NULL AND isfinite(h.market_value) AND h.market_value>=0
                GROUP BY i.industry ORDER BY i.industry""",params).fetchall()
            result.extend((period,industry,value,count) for industry,value,count in aggregated)
            if shared:
                conn.unregister('history_product_positions')
    finally:
        conn.close()
    totals = {}
    for period, industry, value, count in result:
        totals[period] = totals.get(period,0) + value
    rows = [{'period':p.isoformat(),'industry':name,'market_value':value,'observations':count,
             'share_all_disclosed':value/totals[p] if totals[p] else None}
            for p,name,value,count in result]
    result = {'periods':sorted({r['period'] for r in rows}),'rows':rows,
        'classification_date':mapping_date.isoformat(),
        'scope':('申万2021一级当前分类回看；全部公募持仓，未筛选主动基金；共享按期产品视图去重；当期验证/最近关系approx分列，未知关系份额独立保留；报告期披露市值' if shared else
            '申万2021一级当前分类回看；全部基金份额，未筛选主动基金或合并份额；报告期披露市值'),
        'product_coverage':product_coverage,'disclosure':disclosure,
        'relation_status':'shared_period_product_view_exact_and_approx' if shared else 'no_relation_input',
        'denominator':'包含未匹配行业的全部股票持仓市值；未分类单列','as_of':as_of.isoformat()}

    fixed_cache.store('industry_history',fixed_key,result)
    return result
