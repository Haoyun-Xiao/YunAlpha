"""Compare risk on a fixed disclosure universe, never changes in disclosure coverage."""
from datetime import date
from pathlib import Path

from crowding.resources import connect
from crowding.market import _parts, market_data
from crowding.funding import funding_30d
from crowding.liquidity import turnover_20d
from crowding.board_metrics import board_metrics


def previous_day_risk(snapshot, *, previous_day: date, lake_root: Path, changes: dict):
    meta = snapshot['metadata']
    bar_paths = _parts(lake_root, 'daily_bars', previous_day, previous_day)
    val_paths = _parts(lake_root, 'valuation_metrics', previous_day, previous_day)
    if not bar_paths or not val_paths:
        return {'rows': {}, 'metadata': {'reason': 'previous_market_data_missing'}}
    db = connect()
    try:
        prices = dict(db.execute('''SELECT symbol,close FROM read_parquet(?,hive_partitioning=false,union_by_name=true)
            WHERE trade_date=? AND close>0 QUALIFY ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY fetched_at DESC NULLS LAST)=1''',
            [bar_paths,previous_day]).fetchall())
        floats = dict(db.execute('''SELECT symbol,float_mv FROM read_parquet(?,hive_partitioning=false,union_by_name=true)
            WHERE trade_date=? AND float_mv>0 QUALIFY ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY fetched_at DESC NULLS LAST)=1''',
            [val_paths,previous_day]).fetchall())
    finally:
        db.close()
    rows=[]
    for source in snapshot['rows']:
        row=dict(source); symbol=row['symbol']; close=prices.get(symbol); floating=floats.get(symbol)
        value=row.get('shares')*close if row.get('shares') is not None and close is not None else None
        row.update(close=close,holding_mv=value,float_mv=floating,
                   hold_float_ratio=value/floating if value is not None and floating else None)
        rows.append(row)
    snap={'rows':rows,'metadata':{**meta,'market_date':previous_day.isoformat()}}
    names=[r['symbol'] for r in rows]
    liquid=turnover_20d(names,as_of=previous_day,lake_root=lake_root)
    flow=funding_30d(names,as_of=previous_day,lake_root=lake_root)
    market=market_data(names,as_of=previous_day,period_start=date.fromisoformat(meta['holding_period']),lake_root=lake_root)
    result=board_metrics(snap,liquid,flow,market,changes=changes)
    result['metadata']['comparison_scope']='same published holdings, relationship and classification version; independently reranked previous trading day'
    return result
