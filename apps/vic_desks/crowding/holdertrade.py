"""Known-field, disclosed holder trade observations; never total market flow."""
from __future__ import annotations
from datetime import date
from math import isfinite
from pathlib import Path
from crowding.resources import connect


def summarize_holdertrades(rows, *, start: date, end: date) -> dict:
    """Include only full declared trade intervals inside the window.

    An announcement observed after the window end is not known at the anchor.
    Events are hash-deduped only, so overlapping disclosures may repeat trades.
    """
    stats={'candidate_events':0,'eligible_complete':0,'missing_amount_fields':0,
           'missing_dates':0,'invalid_dates':0,'straddles_window':0,'future_announcement':0,
           'sell_count':0,'buy_count':0,'sell_estimate_cny':0.,'buy_estimate_cny':0.}
    holders={}
    for x in rows:
        if x.get('trade_type') not in ('IN','DE'):continue
        close=x.get('close_date')
        if close is not None and (close < start or close > end):continue
        if close is None and (x.get('announce_date') is None or not start <= x['announce_date'] <= end):continue
        stats['candidate_events']+=1
        ann,begin=x.get('announce_date'),x.get('begin_date')
        if ann is None or begin is None or close is None:
            stats['missing_dates']+=1;continue
        if begin>close:
            stats['invalid_dates']+=1;continue
        if ann>end:
            stats['future_announcement']+=1;continue
        if begin<start:
            stats['straddles_window']+=1;continue
        volume,price=x.get('change_vol'),x.get('avg_price')
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not isfinite(v) or v<=0 for v in (volume,price)):
            stats['missing_amount_fields']+=1;continue
        value=volume*price
        if not isfinite(value):stats['missing_amount_fields']+=1;continue
        key='sell' if x['trade_type']=='DE' else 'buy'
        stats[key+'_count']+=1;stats[key+'_estimate_cny']+=value;stats['eligible_complete']+=1
        k=(x.get('symbol'),x.get('holder_name'),x.get('trade_type'))
        holders[k]=holders.get(k,0)+1
    for key in ('sell','buy'):
        if not stats[key+'_count']:stats[key+'_estimate_cny']=None
    stats.update(start_date=start.isoformat(),end_date=end.isoformat(),
        potential_duplicate_holder_events=sum(n-1 for n in holders.values() if n>1),
        value=None,estimate_label='已披露且完整落入窗口的字段完整记录估算（非完整市场净减持）',
        method='成交起止均在窗口内且公告不晚于窗口末日；变动股数×均价；各记录仅按原始行哈希去重',
        caveat='交易区间及价格为上游披露，缺失/跨窗/晚披露记录不计；同一事项重复披露无法排除，不可视为完整或真实净额；事后快照非PIT',
        source='tushare.stk_holdertrade')
    return stats


def holdertrade_windows(root: Path, calendar: list[date], window_sizes=(5,20,60), *, knowledge_date: date | None = None) -> dict:
    paths=[str(x) for x in (Path(root)/'curated/stock_holdertrade_announcements').rglob('*.parquet')]
    if not paths or not calendar:
        return {str(n):{'value':None,'reason':'股东增减持来源或交易日历缺失'} for n in window_sizes}
    conn=connect()
    try:
        # Include a modest look-ahead for diagnostic counts of late-disclosed
        # transactions, but never include such rows in a past-window amount.
        records=conn.execute('''SELECT symbol,holder_name,announce_date,begin_date,close_date,trade_type,change_vol,avg_price
            FROM read_parquet(?,hive_partitioning=false,union_by_name=true)
            WHERE (close_date BETWEEN ? AND ? OR close_date IS NULL) AND announce_date<=?''',
            [paths,calendar[-1],calendar[0],knowledge_date or date.today()]).fetchall()
    finally:conn.close()
    keys=('symbol','holder_name','announce_date','begin_date','close_date','trade_type','change_vol','avg_price')
    rows=[dict(zip(keys,r)) for r in records]
    out={}
    for n in window_sizes:
        if len(calendar)<n:out[str(n)]={'value':None,'reason':'交易日历不足'};continue
        out[str(n)]=summarize_holdertrades(rows,start=calendar[n-1],end=calendar[0])
    return out
