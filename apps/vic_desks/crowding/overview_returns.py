"""Read-only overview return series using the same validated market adapter.

The grouping is provided by the board's fixed holdings snapshot. Values are
fractions, not percentages. Missing prices or factors never become zero.
"""
from datetime import date, timedelta
from math import isfinite
from pathlib import Path
import duckdb
from crowding.resources import connect as bounded_connect
from crowding.lake import DEFAULT_LAKE_ROOT
from crowding.market import market_data

PERIODS = ('持仓期以来', '季末以来', '近 5 日', '近 20 日')


def _sessions(root, market_date):
    paths=[str(p) for p in (root/'curated/trading_calendar').rglob('*.parquet')]
    if not paths:
        return []
    con=bounded_connect()
    try:
        return [x[0] for x in con.execute('SELECT DISTINCT trade_date FROM read_parquet(?,hive_partitioning=false) WHERE is_trading AND trade_date<=? ORDER BY trade_date', [paths,market_date]).fetchall()]
    finally:
        con.close()


def aggregate_returns(snapshot, board, market, days):
    """Cross-sectional cumulative returns, with explicit per-day coverage.

    Denominator is the eligible group with both baseline and that day's valid
    adjusted close; it can vary by date, so coverage accompanies each value.
    Fixed market-date public-fund holding values are used as weights. Captured
    original endpoints support this basis; historical weighting dates remain unknown.
    """
    source={r['symbol']:r for r in snapshot['rows']}
    metrics=board.get('rows',{})
    groups={q:{'q':q, 'n':sum(1 for x in metrics.values() if x.get('quintile')==q), 'ew':[], 'cw':[], 'coverage':[]} for q in range(1,6)}
    by_day={d:{q:[] for q in groups} for d in days}
    individual={}
    pool_values=[]
    pool_equal=[]
    pool_total=sum(1 for x in metrics.values() if x.get('pool'))
    for row in market.get('rows',[]):
        symbol=row['symbol']; bucket=metrics.get(symbol,{}).get('quintile')
        if bucket not in groups: continue
        prices={p['date']:p.get('close') for p in row.get('price_margin_series',[])}
        baseline=prices.get(days[0]) if days else None
        if not isinstance(baseline,(int,float)) or not isfinite(baseline) or baseline<=0: continue
        weight=source.get(symbol,{}).get('holding_mv')
        if not isinstance(weight,(int,float)) or not isfinite(weight) or weight<=0: weight=None
        last=None
        for d in days:
            value=prices.get(d)
            if not isinstance(value,(int,float)) or not isfinite(value) or value<=0: continue
            ret=value/baseline-1
            by_day[d][bucket].append((ret,weight))
            if d==days[-1]: last=ret
        individual[symbol]=last
        if metrics.get(symbol,{}).get('pool') and last is not None:
            pool_equal.append(last)
            if weight is not None:
                pool_values.append((last,weight))
    for d in days:
        for q,g in groups.items():
            xs=by_day[d][q]
            weighted=[(v,w) for v,w in xs if w is not None]
            total=sum(w for _,w in weighted)
            g['ew'].append(sum(v for v,_ in xs)/len(xs) if xs else None)
            g['cw'].append(sum(v*w for v,w in weighted)/total if total>0 else None)
            g['coverage'].append({'valid':len(xs),'eligible':g['n'],'weighted':len(weighted)})
    return {'groups':list(groups.values()),'individual_returns':individual,
            'pool_return':sum(v*w for v,w in pool_values)/sum(w for _,w in pool_values) if pool_values else None,
            'pool_return_cw':sum(v*w for v,w in pool_values)/sum(w for _,w in pool_values) if pool_values else None,
            'pool_return_ew':sum(pool_equal)/len(pool_equal) if pool_equal else None,
            'pool_coverage': {'valid_weighted':len(pool_values),'eligible':pool_total},
            'pool_weight_basis':'行情日公募持仓现值（固定权重近似；原站历史权重日期未唯一确认）'}


def index_returns(days, root, symbol='932000.CSI'):
    paths=[str(p) for p in (root/'curated/index_bars').rglob('*.parquet')]
    if not days or not paths:
        return {'symbol':symbol,'name':'中证2000','returns':[None]*len(days),'reason':'指数行情缺失'}
    db=bounded_connect()
    try:
        records=db.execute('''SELECT trade_date,close FROM read_parquet(?,hive_partitioning=false,union_by_name=true)
             WHERE symbol=? AND trade_date IN (SELECT UNNEST(?::DATE[]))
             QUALIFY ROW_NUMBER() OVER(PARTITION BY symbol,trade_date ORDER BY fetched_at DESC NULLS LAST,source DESC NULLS LAST)=1''',
             [paths,symbol,[date.fromisoformat(d) for d in days]]).fetchall()
    finally:
        db.close()
    values={d.isoformat():v for d,v in records if v is not None and isfinite(v) and v>0}
    baseline=values.get(days[0])
    return {'symbol':symbol,'name':'中证2000','returns':[values[d]/baseline-1 if baseline and d in values else None for d in days],
            'reason': None if baseline else '指数基准日行情缺失', 'observed':len(values), 'expected':len(days),
            'source':'index_bars.close；缺行情保留空值'}


def unheld_candidates(snapshot, root, market_date):
    """Stocks in the same-day valuation universe absent from disclosed holdings.

    Absence is relative to the lake's latest disclosed sample, not proof that
    a stock has no current fund ownership.
    """
    if not snapshot.get('rows') or not snapshot.get('metadata', {}).get('holding_period'):
        return {}
    paths = [str(p) for p in (root/'curated/valuation_metrics').rglob('*.parquet')]
    if not paths:
        return {}
    db = bounded_connect()
    try:
        rows = db.execute("""SELECT symbol,float_mv FROM read_parquet(?,hive_partitioning=false)
          WHERE trade_date=? AND symbol ~ '^[0-9]{6}\\.(SH|SZ|BJ)$'
          QUALIFY ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY fetched_at DESC NULLS LAST)=1""", [paths,market_date]).fetchall()
    finally:
        db.close()
    held = {r['symbol'] for r in snapshot.get('rows', [])}
    return {symbol: weight for symbol,weight in rows if symbol not in held}


def aggregate_unheld(candidates, market, days):
    by_day = {d: [] for d in days}
    for row in market.get('rows', []):
        symbol = row['symbol']
        if symbol not in candidates:
            continue
        prices = {p['date']:p.get('close') for p in row.get('price_margin_series', [])}
        baseline = prices.get(days[0]) if days else None
        if not isinstance(baseline,(int,float)) or not isfinite(baseline) or baseline <= 0:
            continue
        weight = candidates[symbol]
        if not isinstance(weight,(int,float)) or not isfinite(weight) or weight <= 0:
            weight = None
        for day in days:
            value = prices.get(day)
            if isinstance(value,(int,float)) and isfinite(value) and value > 0:
                by_day[day].append((value/baseline-1,weight))
    ew, cw, coverage = [], [], []
    for day in days:
        rows=by_day[day]; weighted=[(v,w) for v,w in rows if w is not None]
        total=sum(w for _,w in weighted)
        ew.append(sum(v for v,_ in rows)/len(rows) if rows else None)
        cw.append(sum(v*w for v,w in weighted)/total if total else None)
        coverage.append({'valid':len(rows),'eligible':len(candidates),'weighted':len(weighted)})
    return {'n':len(candidates),'ew':ew,'cw':cw,'coverage':coverage,
            'scope':'当日估值表A股中，不在本湖最新公开基金持仓样本的股票；并不证明当前没有基金持有'}


def overview_returns(snapshot, board, *, period='持仓期以来', lake_root=DEFAULT_LAKE_ROOT, market_fn=market_data):
    if period not in PERIODS: raise ValueError('unsupported period')
    meta=snapshot['metadata']; root=Path(lake_root).expanduser().resolve()
    market_day=date.fromisoformat(meta['market_date']) if meta.get('market_date') else None
    if market_day is None: return {'error':'行情日期缺失'}
    sessions=_sessions(root,market_day)
    if not sessions or sessions[-1]!=market_day: return {'error':'交易日历缺失或不包含行情日'}
    if period=='持仓期以来':
        requested=date.fromisoformat(meta['holding_period']) if meta.get('holding_period') else None
    elif period=='季末以来':
        requested=date(market_day.year,((market_day.month-1)//3)*3+1,1)-timedelta(days=1)
    else:
        n=5 if period=='近 5 日' else 20
        requested=sessions[-n-1] if len(sessions)>n else None
    if requested is None: return {'error':'收益起点或交易日历不足'}
    baseline=next((d for d in reversed(sessions) if d<=requested),None)
    if baseline is None: return {'error':'交易日历无可用收益基准日'}
    days=[d.isoformat() for d in sessions if baseline<=d<=market_day]
    symbols=[r['symbol'] for r in snapshot['rows'] if board.get('rows',{}).get(r['symbol'],{}).get('quintile')]
    unheld = unheld_candidates(snapshot,root,market_day)
    raw=market_fn(symbols+list(unheld),lake_root=root,as_of=market_day,period_start=baseline,series_start=baseline)
    data=aggregate_returns(snapshot,board,raw,days)
    return {'days':days, **data, 'none_group':aggregate_unheld(unheld,raw,days), 'index': index_returns(days,root), 'metadata':{'period':period,'requested_start':requested.isoformat(),
        'baseline':baseline.isoformat(),'market_date':market_day.isoformat(),
        'return_unit':'fraction','weight_basis':'行情日公募持仓现值，固定权重；原站历史权重时点未唯一确认',
        'coverage_rule':'每个时点仅对起点与该日均有可验证复权价格的股票计算，样本可能随日期变化',
        'scope':'全部已披露基金；仅部分份额归并；五组按当前评分集合内占流通比由低到高等分',
        'limitations':['无持仓仅表示不在最新公开持仓样本，不代表实际零持仓；中证2000行情缺失日期留空','各日缺失复权行情导致有效样本可能变化'],
        'source_datasets':['daily_bars','adj_factors','trading_calendar','valuation_metrics']}}
