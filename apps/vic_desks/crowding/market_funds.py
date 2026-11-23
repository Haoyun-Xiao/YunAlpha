"""Market-wide capital-flow inputs from published lake datasets."""
from datetime import date
from pathlib import Path

import duckdb
from crowding.resources import connect as bounded_connect
from crowding.etf_flow import etf_flow

from crowding.lake import DEFAULT_LAKE_ROOT
from crowding.repurchase import repurchase_windows
from crowding.holdertrade import holdertrade_windows


def market_funds(*, lake_root=DEFAULT_LAKE_ROOT, as_of=None):
    root = Path(lake_root)
    bound = as_of or date.today()
    flow_paths = sorted((root / 'curated' / 'market_money_flow').rglob('*.parquet'))
    margin_paths = sorted((root / 'curated' / 'margin_trading').rglob('*.parquet'))
    north_paths = sorted((root / 'curated' / 'northbound_flows').rglob('*.parquet'))
    south_paths = sorted((root / 'curated' / 'southbound_flows').rglob('*.parquet'))
    etf_paths = sorted((root / 'curated' / 'etf_daily_metrics').rglob('*.parquet'))
    calendar_paths = sorted((root / 'curated' / 'trading_calendar').rglob('*.parquet'))
    # Market totals and stock-level data have separate coverage and sources.
    market_margin_paths = sorted((root / 'curated' / 'market_margin_totals').rglob('*.parquet'))
    ipo_paths = sorted((root / 'curated' / 'market_ipo_funding').rglob('*.parquet'))
    if not flow_paths or not calendar_paths:
        return {'market_main_flow': None, 'windows': {}, 'reason': '市场资金流或交易日历缺失'}
    con = bounded_connect()
    try:
        source_latest = {}
        for name, paths in (('market_money_flow',flow_paths), ('market_margin_totals',market_margin_paths), ('etf_daily_metrics',etf_paths)):
            if paths:
                latest = con.execute('SELECT MAX(trade_date) FROM read_parquet(?,hive_partitioning=false,union_by_name=true) WHERE trade_date<=?', [[str(p) for p in paths],bound]).fetchone()[0]
                if latest:
                    source_latest[name] = latest
        common_cutoff = min(source_latest.values()) if source_latest else bound
        days = [r[0] for r in con.execute(
            'SELECT DISTINCT trade_date FROM read_parquet(?, hive_partitioning=false) '
            'WHERE is_trading AND trade_date <= ? ORDER BY trade_date DESC LIMIT 62',
            [[str(p) for p in calendar_paths], common_cutoff]).fetchall()]
        if not days:
            return {'market_main_flow': None, 'windows': {}, 'reason': '无已公布交易日'}
        rows = con.execute('''SELECT trade_date, extra_large_buy_amount, large_buy_amount, source
            FROM read_parquet(?, hive_partitioning=false, union_by_name=true)
            WHERE trade_date BETWEEN ? AND ?
            QUALIFY ROW_NUMBER() OVER (PARTITION BY trade_date ORDER BY fetched_at DESC NULLS LAST) = 1''',
            [[str(p) for p in flow_paths], days[-1], days[0]]).fetchall()
        margin_rows = []
        if margin_paths:
            margin_rows = con.execute('''SELECT trade_date, SUM(margin_buy) buy, SUM(margin_balance) balance, COUNT(*) n
                FROM read_parquet(?, hive_partitioning=false, union_by_name=true)
                WHERE trade_date BETWEEN ? AND ? GROUP BY trade_date''',
                [[str(p) for p in margin_paths], days[-1], days[0]]).fetchall()
        market_margin_rows = []
        if market_margin_paths:
            market_margin_rows = con.execute('''SELECT trade_date, margin_balance
                FROM read_parquet(?, hive_partitioning=false, union_by_name=true)
                WHERE trade_date BETWEEN ? AND ?''',
                [[str(p) for p in market_margin_paths], days[-1], days[0]]).fetchall()
        north_rows = []
        if north_paths:
            north_rows = con.execute('''SELECT trade_date, SUM(net_buy) net_buy
                FROM read_parquet(?, hive_partitioning=false, union_by_name=true)
                WHERE trade_date BETWEEN ? AND ? GROUP BY trade_date''',
                [[str(p) for p in north_paths], days[-1], days[0]]).fetchall()
        south_rows = []
        if south_paths:
            columns = {r[0] for r in con.execute('DESCRIBE SELECT * FROM read_parquet(?,hive_partitioning=false,union_by_name=true)', [[str(p) for p in south_paths]]).fetchall()}
            version_expr = 'data_version' if 'data_version' in columns else 'NULL::VARCHAR'
            south_rows = con.execute(f"""SELECT trade_date,net_buy,{version_expr} data_version
                FROM read_parquet(?, hive_partitioning=false, union_by_name=true)
                WHERE channel='south_total' AND trade_date BETWEEN ? AND ?
                QUALIFY ROW_NUMBER() OVER(PARTITION BY trade_date,channel ORDER BY fetched_at DESC NULLS LAST)=1""",
                [[str(p) for p in south_paths], days[-1], days[0]]).fetchall()
        ipo_rows = []
        if ipo_paths:
            ipo_rows = con.execute('''SELECT trade_date, SUM(raised_amount) FILTER(WHERE isfinite(raised_amount) AND raised_amount>=0) raised_amount, COUNT(*) n,
                                             COUNT(*) FILTER(WHERE isfinite(raised_amount) AND raised_amount>=0) n_with_amount
                FROM read_parquet(?, hive_partitioning=false, union_by_name=true)
                WHERE trade_date BETWEEN ? AND ? GROUP BY trade_date''',
                [[str(p) for p in ipo_paths], days[-1], days[0]]).fetchall()
    finally:
        con.close()
    by_day = {d: (extra, large, source) for d, extra, large, source in rows}
    # Coverage is reported separately; we do not silently turn partial symbol
    # coverage into a market total.  The current lake has no complete, audited
    # financing-net-buy series for these windows.
    margin_by_day = {}
    market_margin_by_day = {d: balance for d, balance in market_margin_rows}
    market_margin_days = sorted(market_margin_by_day, reverse=True)
    north_by_day = {d: net for d, net in north_rows}
    south_by_day = {d: net for d, net, version in south_rows if version == 'moneyflow-hsgt-daily-net-million-to-cny-v1'}
    south_unverified_versions = sorted({version or 'missing_version' for _,_,version in south_rows if version != 'moneyflow-hsgt-daily-net-million-to-cny-v1'})
    ipo_by_day = {d: (amount, n, n_with_amount) for d, amount, n, n_with_amount in ipo_rows}
    # Anchor to the independently observed T+1 exchange margin snapshot when
    # available; the captured original buyback value shares that 11-05 date.
    # Source cutoffs are disclosed instead of presenting a 11-06 event as 11-05.
    repurchase_anchor = days[0]
    repurchase_calendar = [d for d in days if repurchase_anchor and d <= repurchase_anchor]
    repurchase = repurchase_windows(root, repurchase_calendar) if repurchase_calendar else {}
    etf_estimate = etf_flow(lake_root=root, as_of=repurchase_anchor) if repurchase_anchor else {'windows': {}}
    holdertrade = holdertrade_windows(root, repurchase_calendar, knowledge_date=bound) if repurchase_calendar else {}
    windows = {}
    for count in (5, 20, 60):
        selected = days[:count]
        # A sum of N daily balance changes needs N+1 balances.  The margin
        # series commonly ends one session before the market's latest date.
        margin_end = days[0] if market_margin_days else None
        margin_calendar = [d for d in days if margin_end and d <= margin_end][:count + 1]
        margin_complete = (len(margin_calendar) == count + 1 and all(
            d in market_margin_by_day and market_margin_by_day[d] is not None for d in margin_calendar))
        valid = [(d, by_day[d]) for d in selected if d in by_day and by_day[d][0] is not None and by_day[d][1] is not None]
        complete = len(selected) == count and len(valid) == count
        south_values = [(d, south_by_day[d]) for d in selected if d in south_by_day and south_by_day[d] is not None]
        windows[str(count)] = {
            'value': sum(v[0] + v[1] for _, v in valid) if complete else None,
            'observed_days': len(valid), 'expected_days': count,
            'missing_dates': [d.isoformat() for d in selected if d not in by_day or by_day[d][0] is None or by_day[d][1] is None],
            'common_cutoff': days[0].isoformat(),
            'start_date': selected[-1].isoformat() if selected else None,
            'end_date': selected[0].isoformat() if selected else None,
            'sources': sorted({v[2] for _, v in valid if v[2]}),
            'details': {
                'equity_etf_flow_estimate': etf_estimate.get('windows', {}).get(str(count), {}),
                # The lake's ``margin_buy`` is gross margin-purchase amount;
                # it is not the board's verified financing-net-buy series.
                # Keep it separately labelled and leave the requested net
                # figure unknown rather than presenting a wrong sign.
                'margin_net_buy': (
                    market_margin_by_day[margin_calendar[0]] - market_margin_by_day[margin_calendar[-1]]
                    if margin_complete else None
                ),
                'margin_buy_amount': _sum_window(margin_by_day, selected, 0),
                'margin_balance_change': _change_window(margin_by_day, selected, 1),
                'northbound_net_buy': _sum_window(north_by_day, selected),
                'southbound_net_buy': sum(v for _, v in south_values) if len(selected)==count and len(south_values)==count else None,
                'southbound_quality': 'exact' if len(selected)==count and len(south_values)==count else 'unavailable',
                'southbound_reason': 'source_semantics_unverified' if south_unverified_versions else ('incomplete_trading_window' if len(south_values)!=count else None),
                'southbound_unverified_versions': south_unverified_versions,
                'southbound_observed_days': len(south_values),
                'southbound_expected_days': count,
                'southbound_missing_dates': [d.isoformat() for d in selected if d not in south_by_day or south_by_day[d] is None],
                'ipo_funding': (_sum_sparse(ipo_by_day, selected, 0)
                                if len(selected) == count and all(
                                    ipo_by_day[d][1] == ipo_by_day[d][2]
                                    for d in selected if d in ipo_by_day) else None),
                'ipo_count': sum(ipo_by_day[d][1] for d in selected if d in ipo_by_day) if any(d in ipo_by_day for d in selected) else None,
                'ipo_quality': ('partial' if len(selected)==count and any(d in ipo_by_day for d in selected)
                                and all(ipo_by_day[d][1]==ipo_by_day[d][2] for d in selected if d in ipo_by_day) else 'unavailable'),
                'ipo_source_status': 'published_events_completeness_unverified' if ipo_paths else 'source_dataset_missing',
                'ipo_reason_code': ('trading_calendar_incomplete' if len(selected)!=count else
                                    'source_dataset_missing' if not ipo_paths else
                                    'no_observed_events_source_completeness_unknown' if not any(d in ipo_by_day for d in selected) else
                                    'invalid_or_missing_event_amount' if any(ipo_by_day[d][1]!=ipo_by_day[d][2] for d in selected if d in ipo_by_day) else
                                    'event_source_completeness_unverified'),
                'ipo_coverage': {'source_available': bool(ipo_paths),
                                 'observed_event_count': sum(ipo_by_day[d][1] for d in selected if d in ipo_by_day),
                                 'valid_amount_count': sum(ipo_by_day[d][2] for d in selected if d in ipo_by_day),
                                 'source_window_complete': False},
                'ipo_reason': ('交易日历不足' if len(selected) != count else
                               '部分 IPO 事件金额缺失、无穷或为负' if any(ipo_by_day[d][1] != ipo_by_day[d][2] for d in selected if d in ipo_by_day) else
                               'IPO 来源数据集缺失，不能按零事件处理' if not ipo_paths else
                               '该窗口没有观察到 IPO 事件，来源完整性未知，不能按零募资处理' if not any(d in ipo_by_day for d in selected) else
                               '已发布事件观察合计，来源完整性未验证'),
                'ipo_dates': sorted(d.isoformat() for d in ipo_by_day if d in selected),
                'repurchase_plan': repurchase.get(str(count), {'value':None,'strict_reason':'缺少可核实T+1数据锚点'}),
                'holdertrade_observed': holdertrade.get(str(count), {'value':None,'reason':'缺少可核实T+1数据锚点'}),
                'margin_as_of': margin_end.isoformat() if margin_end else None,
                'margin_start_date': margin_calendar[-1].isoformat() if margin_complete else None,
                'margin_end_date': margin_end.isoformat() if margin_end else None,
            },
            'coverage': {
                'margin_rows': {d.isoformat(): next((n for md, _, _, n in margin_rows if md == d), 0) for d in selected},
                'margin_expected_min_rows': 3900,
                'northbound_rows': {d.isoformat(): (1 if d in north_by_day else 0) for d in selected},
            },
        }
    for key, window in windows.items():
        detail = window['details']
        etf = detail['equity_etf_flow_estimate']
        # A missing or differently anchored component is excluded, never zero.
        etf_aligned = (etf.get('start_date') == window['start_date'] and etf.get('end_date') == window['end_date'])
        components = [
            {'key':'etf_equity','side':'inflow','value':etf.get('partial_value_cny') if etf_aligned else None,
             'quality':'estimated' if etf.get('complete') else 'partial', 'reason':None if etf_aligned else 'window_not_aligned'},
            {'key':'margin_net','side':'inflow','value':detail.get('margin_net_buy'),
             'quality':'exact','reason':None if detail.get('margin_net_buy') is not None else 'incomplete_trading_window'},
            {'key':'south_net','side':'outflow','value':detail.get('southbound_net_buy'),
             'quality':detail['southbound_quality'],'reason':detail['southbound_reason']},
            {'key':'ipo','side':'outflow','value':detail.get('ipo_funding'),
             'quality':detail['ipo_quality'],'reason':detail['ipo_reason_code']},
            {'key':'dec_est','side':'outflow','value':detail.get('holdertrade_observed',{}).get('sell_estimate_cny'),
             'quality':'partial','reason':'disclosure_and_event_deduplication_incomplete'},
        ]
        included = [x for x in components if x['value'] is not None and x['quality'] in ('exact','estimated','partial')]
        full_window = window['expected_days'] == len(days[:int(key)])
        window['net'] = (sum(x['value'] * (1 if x['side']=='inflow' else -1) for x in components)
                         if full_window and len(included)==len(components) and all(x['quality']=='exact' for x in components) else None)
        window['net_quality'] = 'exact' if window['net'] is not None else 'unavailable'
        window['partial_net_estimate'] = (sum(x['value'] * (1 if x['side']=='inflow' else -1) for x in included)
                                          if full_window and included else None)
        window['partial_net_quality'] = 'partial' if window['partial_net_estimate'] is not None else 'unavailable'
        window['net_components'] = components
        window['excluded_components'] = [{'key':x['key'],'reason':x['reason'] or 'value_missing'} for x in components if x not in included]
        window['net_note'] = '覆盖样本净额估算：仅相同交易窗口内可解释的分项；排除项不按0处理，不能视为全市场净额'
    return {'market_main_flow': '大单与特大单净额之和', 'windows': windows,
            'etf_flow_metadata': {k:v for k,v in etf_estimate.items() if k!='windows'},
            'common_cutoff': days[0].isoformat(), 'source_latest_dates': {k:v.isoformat() for k,v in source_latest.items()},
            'unit': 'CNY', 'note': '大单及特大单为市场资金流独立口径；融资净买入仅在 source-validated market_margin_totals 发布后计算；当前 per-symbol margin_buy 不冒充市场净买入；南向仅接受经当前来源定义验证的daily净额版本；既有累计值差分版本含义未核实并隔离，不参与净额。'}


def _sum_window(values, selected, index=None):
    nums = []
    for d in selected:
        if d not in values:
            continue
        value = values[d]
        if index is not None:
            value = value[index]
        if value is not None:
            nums.append(value)
    return sum(nums) if len(nums) == len(selected) else None


def _change_window(values, selected, index):
    if not selected or selected[0] not in values or selected[-1] not in values:
        return None
    start, end = values[selected[-1]][index], values[selected[0]][index]
    return end - start if start is not None and end is not None else None


def _sum_sparse(values, selected, index=None):
    """Sum event rows in a window; missing trading dates mean no event, not a gap."""
    nums = []
    for d in selected:
        if d not in values:
            continue
        value = values[d]
        if index is not None:
            value = value[index]
        if value is not None:
            nums.append(value)
    return sum(nums) if nums else None
