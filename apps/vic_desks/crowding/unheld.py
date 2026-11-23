"""Latest disclosed-sample absence view, never a statement of actual ownership."""
from __future__ import annotations
from datetime import date
from math import isfinite
from pathlib import Path
from crowding.overview_returns import unheld_candidates
from crowding.resources import connect


def unheld_stocks(snapshot: dict, *, root: Path) -> dict:
    meta=snapshot.get('metadata', {})
    market_date=meta.get('market_date')
    if not market_date:
        return {'rows': [], 'metadata': {'reason':'行情日期缺失'}}
    root=Path(root)
    candidates=unheld_candidates(snapshot,root,date.fromisoformat(market_date))
    # No fund disclosure data is not evidence of non-ownership for all stocks.
    if not snapshot.get('rows') or not meta.get('holding_period'):
        return {'rows': [], 'metadata': {'reason':'最近披露持仓样本缺失，无法判断样本内未观察持仓'}}
    names,industries={},{}
    p=lambda dataset:[str(x) for x in (root/'curated'/dataset).rglob('*.parquet')]
    instrument_paths,member_paths,taxonomy_paths=p('instruments'),p('industry_members'),p('industry_taxonomy')
    conn=connect()
    try:
        if instrument_paths:
            names=dict(conn.execute('''SELECT symbol,name FROM read_parquet(?,hive_partitioning=false,union_by_name=true)
              WHERE symbol IN (SELECT UNNEST(?::VARCHAR[]))
              QUALIFY ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY fetched_at DESC NULLS LAST)=1''',[instrument_paths,list(candidates)]).fetchall())
        if member_paths and taxonomy_paths and meta.get('industry_date'):
            mapped=conn.execute('''WITH i AS (
                SELECT symbol,industry_code FROM read_parquet(?,hive_partitioning=false,union_by_name=true)
                WHERE classification_system='sw' AND source='sw' AND as_of_date=CAST(? AS DATE)
                  AND symbol IN (SELECT UNNEST(?::VARCHAR[]))
                QUALIFY ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY fetched_at DESC NULLS LAST)=1
              ), t AS (
                SELECT industry_code,parent_industry_code,industry_name,level FROM read_parquet(?,hive_partitioning=false,union_by_name=true)
                WHERE classification_system='SW2021'
                QUALIFY ROW_NUMBER() OVER(PARTITION BY industry_code ORDER BY fetched_at DESC NULLS LAST)=1
              ) SELECT i.symbol,CASE WHEN l.level='L1' THEN l.industry_name WHEN l.level='L2' THEN p.industry_name
                    WHEN l.level='L3' THEN gp.industry_name END FROM i LEFT JOIN t l ON l.industry_code=i.industry_code
                LEFT JOIN t p ON p.industry_code=l.parent_industry_code LEFT JOIN t gp ON gp.industry_code=p.parent_industry_code''',
                [member_paths,meta['industry_date'],list(candidates),taxonomy_paths]).fetchall()
            industries=dict(mapped)
    finally:
        conn.close()
    rows=[]
    for symbol,weight in sorted(candidates.items()):
        rows.append({'symbol':symbol,'name':names.get(symbol),'industry_name':industries.get(symbol),
          'float_mv':float(weight) if isinstance(weight,(int,float)) and isfinite(weight) and weight>0 else None,
          'sample_presence':'not_observed'})
    return {'rows':rows,'metadata':{'market_date':market_date,'holding_period':meta['holding_period'],
        'industry_date':meta.get('industry_date'),'candidate_count':len(rows),
        'scope':'行情日估值表股票，未出现在本湖最近披露的基金持仓样本；不证明当前没有公募持有',
        'limitations':['最新披露期覆盖可能不完整','基金持仓存在披露时滞','缺名称、行业和估值时显示空值']}}


def unheld_market(snapshot: dict, *, root: Path, period: str, candidates: dict | None = None) -> dict:
    """Batch market-only columns for sample-unobserved stocks, keeping gaps null."""
    from datetime import timedelta
    from crowding.overview_returns import PERIODS, _sessions
    from crowding.market import market_data
    from crowding.liquidity import turnover_20d
    from crowding.funding import funding_30d
    if period not in PERIODS:
        raise ValueError('unsupported period')
    meta=snapshot.get('metadata', {})
    market_date=meta.get('market_date')
    if not market_date:
        return {'rows': {}, 'metadata': {'reason':'行情日期缺失','period':period}}
    day=date.fromisoformat(market_date)
    sessions=_sessions(Path(root),day)
    if not sessions or sessions[-1]!=day:
        return {'rows': {}, 'metadata': {'reason':'行情日或交易日历缺失','period':period}}
    if period=='持仓期以来':
        start=date.fromisoformat(meta['holding_period']) if meta.get('holding_period') else None
    elif period=='季末以来':
        start=date(day.year,((day.month-1)//3)*3+1,1)-timedelta(days=1)
    else:
        n=5 if period=='近 5 日' else 20
        start=sessions[-n-1] if len(sessions)>n else None
    if start is None:
        return {'rows': {}, 'metadata': {'reason':'收益起点或交易日历不足','period':period}}
    baseline=next((d for d in reversed(sessions) if d<=start),None)
    if baseline is None:
        return {'rows': {}, 'metadata': {'reason':'交易日历没有可用收益基准日','period':period}}
    candidates = candidates if candidates is not None else unheld_stocks(snapshot,root=Path(root))
    symbols=[r['symbol'] for r in candidates.get('rows',[])]
    if not symbols:
        return {'rows': {}, 'metadata': {'reason':candidates.get('metadata',{}).get('reason','没有样本内未观察持仓股票'),'period':period}}
    # Each adapter is called once for the entire universe, not once per row.
    market=market_data(symbols, lake_root=root, as_of=day, period_start=baseline)
    liq=turnover_20d(symbols, as_of=day, lake_root=root)
    funding=funding_30d(symbols, as_of=day, lake_root=root)
    by={}
    for row in market['rows']:
        symbol=row['symbol']; flow=funding['rows'].get(symbol,{}); turnover=liq['rows'].get(symbol,{})
        def finite(value):
            return float(value) if isinstance(value,(int,float)) and isfinite(value) else None
        by[symbol]={'return':finite(row.get('period_return')),'drawdown':finite(row.get('drawdown_52w')),
            'avg_turnover':finite(turnover.get('avg_turnover')),
            'margin':finite(flow.get('margin_balance_change_ratio_30d')),'large':finite(flow.get('big_order_30d')),
            'coverage':{'period_return':row.get('coverage',{}).get('period_return'),
                'turnover_days':turnover.get('observed_days'), 'flow_days':flow.get('flow_days'),
                'margin_start':flow.get('margin_start'),'margin_end':flow.get('margin_end')}}
    return {'rows':by,'metadata':{'period':period,'market_date':market_date,'baseline':baseline.isoformat(),
        'scope':'样本内未观察持仓股票的独立行情列；成交额及资金使用相应严格覆盖窗口，缺失不填零',
        'turnover':liq.get('metadata',{}),'funding':funding.get('metadata',{}),
        'limitations':['未观察持仓不等于实际零持仓','价格和复权因子缺失的收益留空','资金、融资和成交额来源及可用日期可能不同']}}
