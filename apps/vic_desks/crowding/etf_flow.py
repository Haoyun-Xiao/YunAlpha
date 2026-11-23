"""Reproducible, coverage-aware ETF share-change cash-flow proxy.

Classification is a current AKShare snapshot applied retrospectively. It is
not VicAlpha's provider series and is not a historical point-in-time universe.
"""
from collections import Counter
from datetime import date
from pathlib import Path
import math
from crowding.lake import DEFAULT_LAKE_ROOT
from crowding.resources import connect

EQUITY_LABELS = frozenset({'指数型-股票', '指数型-海外股票', '股票型'})


def _files(root, dataset):
    return [str(p) for p in sorted((root/'curated'/dataset).rglob('*.parquet'))]


def etf_flow(*, lake_root=DEFAULT_LAKE_ROOT, as_of=None, windows=(5,20,60)):
    root=Path(lake_root); bound=as_of or date.today()
    paths={name:_files(root,name) for name in ('etf_profiles','fund_profiles','fund_classification_snapshot','etf_daily_metrics','trading_calendar')}
    missing=[name for name,files in paths.items() if not files]
    if missing:return {'windows':{},'reason':'missing datasets: '+', '.join(missing)}
    db=connect()
    try:
        end=db.execute('''SELECT MAX(trade_date) FROM read_parquet(?,hive_partitioning=false,union_by_name=true)
                          WHERE trade_date<=?''',[paths['etf_daily_metrics'],bound]).fetchone()[0]
        if not end:return {'windows':{},'reason':'no ETF observation before as_of'}
        days=[r[0] for r in db.execute('''SELECT DISTINCT trade_date FROM read_parquet(?,hive_partitioning=false)
                    WHERE is_trading AND trade_date<=? ORDER BY trade_date DESC LIMIT ?''',
                    [paths['trading_calendar'],end,max(windows)+1]).fetchall()]
        if not days:return {'windows':{},'reason':'trading calendar empty'}
        profiles=db.execute('''SELECT symbol,list_date,etf_type FROM read_parquet(?,hive_partitioning=false,union_by_name=true)
               QUALIFY ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY fetched_at DESC NULLS LAST)=1''',[paths['etf_profiles']]).fetchall()
        snapshots=db.execute('''SELECT symbol,classification_text,snapshot_date FROM read_parquet(?,hive_partitioning=false,union_by_name=true)
               QUALIFY ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY snapshot_date DESC,fetched_at DESC)=1''',[paths['fund_classification_snapshot']]).fetchall()
        funds=db.execute('''SELECT symbol,delist_date FROM read_parquet(?,hive_partitioning=false,union_by_name=true)
               QUALIFY ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY fetched_at DESC NULLS LAST)=1''',[paths['fund_profiles']]).fetchall()
        metrics=db.execute('''SELECT symbol,trade_date,shares_outstanding,unit_nav FROM read_parquet(?,hive_partitioning=false,union_by_name=true)
               WHERE trade_date BETWEEN ? AND ? QUALIFY ROW_NUMBER() OVER(PARTITION BY symbol,trade_date ORDER BY fetched_at DESC NULLS LAST)=1''',
               [paths['etf_daily_metrics'],days[-1],days[0]]).fetchall()
    finally:db.close()
    # A numeric ETF code must identify one classification and one profile;
    # ambiguity remains unknown instead of being silently multiplied.
    labels={}; endings={}; codes=Counter(s[:6] for s,_,_ in profiles if s)
    for s,label,dt in snapshots:
        if s:labels.setdefault(s[:6],[]).append((label,dt))
    for s,dt in funds:
        if s:endings.setdefault(s[:6],[]).append(dt)
    # ETF profiles may include both an OF fund identity and exchange security
    # for the same six-digit code; metrics use SH/SZ trading symbols.
    profiles=[p for p in profiles if p[0] and p[0].endswith(('.SH','.SZ'))]
    codes=Counter(s[:6] for s,_,_ in profiles)
    eligible={}; unknown=[]
    for symbol,listed,channel in profiles:
        if not symbol or codes[symbol[:6]]!=1:continue
        matches=labels.get(symbol[:6],[])
        if len(matches)!=1:
            unknown.append(symbol);continue
        label,seen=matches[0]
        if label not in EQUITY_LABELS:continue
        end_dates=endings.get(symbol[:6],[])
        eligible[symbol]={'list_date':listed,'delist_date':end_dates[0] if len(end_dates)==1 else None,'label':label,'snapshot_date':seen,'channel':channel}
    by_key={(s,d):(shares,nav) for s,d,shares,nav in metrics}
    daily=[]
    for i,day in enumerate(days[:-1]):
        prev=days[i+1]; value=0.; valid=0; expected=0; gaps=Counter(); unknown_lifecycle=0
        for s,info in eligible.items():
            listed=info['list_date']; delisted=info['delist_date']
            if listed and listed>day or delisted and delisted<day:continue
            # A new listing has no t-1 observation; do not manufacture zero shares.
            if not listed:unknown_lifecycle+=1
            expected+=1
            current=by_key.get((s,day));previous=by_key.get((s,prev))
            if not current or not previous:gaps['missing_daily_row']+=1;continue
            now,nav=current; old=previous[0]
            if now is None or old is None or not math.isfinite(now) or not math.isfinite(old) or now<0 or old<0:
                gaps['missing_shares']+=1;continue
            if now == 0 or old == 0:
                gaps['zero_shares_or_inactive']+=1;continue
            if nav is None or not math.isfinite(nav) or nav<=0:gaps['missing_unit_nav']+=1;continue
            value+=(now-old)*nav;valid+=1
        daily.append({'date':day.isoformat(),'previous_date':prev.isoformat(),'partial_value_cny':value if valid else None,
                      'matched_funds':valid,'expected_funds':expected,'unmatched_funds':expected-valid,
                      'unknown_list_date':unknown_lifecycle,'gaps':dict(gaps)})
    result={}
    for n in windows:
        selected=daily[:n]; complete=len(selected)==n and all(x['unmatched_funds']==0 and x['unknown_list_date']==0 for x in selected) and not unknown
        known=[x['partial_value_cny'] for x in selected if x['partial_value_cny'] is not None]
        result[str(n)]={'value_cny':sum(known) if complete and len(known)==n else None,
          'partial_value_cny':sum(known) if known else None,'observed_days':len(known),'expected_days':n,
          'start_date':selected[-1]['date'] if selected else None,'end_date':selected[0]['date'] if selected else None,
          'complete':complete,'daily':selected}
    dates=sorted({v['snapshot_date'].isoformat() for v in eligible.values() if v['snapshot_date']})
    return {'as_of':days[0].isoformat(),'windows':result,'classified_equity_funds':len(eligible),'unknown_classification_count':len(unknown),
      'classification_dates':dates,'unit':'CNY','method':'sum over trading days and ETFs of (shares_t - shares_previous_trading_day) * unit_nav_t',
      'scope':'current AKShare classifications applied retrospectively; stock and overseas stock index ETFs; excluded unclassified products',
      'source':'Tushare etf_daily_metrics + AKShare current fund_classification_snapshot; independent estimate, not VicAlpha',
      'limitations':'share changes also reflect splits, new listings, delistings and fund events; missing observations are not zero; lifecycle metadata may be incomplete'}
