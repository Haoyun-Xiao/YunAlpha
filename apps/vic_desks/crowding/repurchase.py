"""Tushare plan-announcement diagnostic, not a complete upper-bound estimate."""
from __future__ import annotations
from datetime import date
from math import isfinite
from pathlib import Path
from crowding.resources import connect


def summarize_plans(rows, *, start: date, end: date) -> dict:
    """Sum observable plan announcement amounts; duplicate plans remain unknown."""
    candidates=[x for x in rows if x.get('progress')=='预案' and start <= x['announce_date'] <= end]
    valid=[x for x in candidates if isinstance(x.get('amount'),(int,float)) and not isinstance(x.get('amount'),bool) and isfinite(x['amount']) and x['amount']>=0]
    symbols={x.get('symbol') for x in candidates if x.get('symbol')}
    # Not a lower/upper bound: vendor amount definitions and repeated announcements are unknown.
    return {'value': None, 'estimate_cny':sum(x['amount'] for x in valid) if valid else None, 'estimate_label':'预案公告金额观察合计（独立估算，未核验上限）', 'observed_amount_sum_cny':sum(x['amount'] for x in valid) if valid else None,
        'event_count':len(candidates),'amount_observed_count':len(valid),'amount_missing_count':len(candidates)-len(valid),
        'unique_symbols':len(symbols),'repeated_symbol_events':len(candidates)-len(symbols),
        'start_date':start.isoformat(),'end_date':end.isoformat(),
        'strict_reason':'Tushare 金额不保证预案上限；存在金额缺失及跨公告同方案无法识别，不能作为完整值',
        'method':'进度=预案、公告日在窗口内；仅对可用非负金额求和，为未按方案去重的观察值，不计入净额；不可解释为完整上限或可信边界',
        'source':'tushare.repurchase','known_at':'采集时点（事后快照，非历史PIT）'}


def repurchase_windows(root: Path, calendars: list[date], window_sizes=(5,20,60)) -> dict:
    """Calendar is descending and anchored to market-funds source's own date."""
    paths=[str(x) for x in (Path(root)/'curated/market_repurchase_announcements').rglob('*.parquet')]
    if not paths or not calendars:
        return {str(n):{'value':None,'strict_reason':'回购公告数据或交易日历缺失'} for n in window_sizes}
    need=max(window_sizes)
    if len(calendars)<need:
        return {str(n):{'value':None,'strict_reason':'回购计算窗口交易日历不足'} for n in window_sizes}
    end,start=calendars[0],calendars[need-1]
    conn=connect()
    try:
        data=conn.execute('''SELECT symbol,announce_date,progress,amount FROM read_parquet(?,hive_partitioning=false,union_by_name=true)
            WHERE announce_date BETWEEN ? AND ? AND progress='预案' ''',[paths,start,end]).fetchall()
    finally:conn.close()
    rows=[dict(zip(('symbol','announce_date','progress','amount'),r)) for r in data]
    result={}
    for n in window_sizes:
        selected=calendars[:n]
        result[str(n)]=summarize_plans(rows,start=selected[-1],end=selected[0])
    return result
