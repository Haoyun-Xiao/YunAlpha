"""Select daily overlays without changing the date of frozen families."""
from datetime import date
from pathlib import Path

# Only these observed feeds may advance the page's date selector. Other
# sections in the same response continue to supply their own source dates.
GROUPS = {
    'valuation': ('stock_structure',),
    'capital_flow': ('market_main_native','main_money','stock_main_windows','etf','sector_windows',
                     'margin_exchange','margin_securities','cross_border','dragon_events','dragon_seats','capital_balance'),
    'national_team': ('etf','index_prices','shadow_portfolio','support_signals','support_temperature'),
    'fund_holdings': ('stock_quotes','company_nav'),
    'chips': ('stock_quotes',),
    'sentiment': ('market_main20','temperature'),
    'contrarian': ('benchmark_index','valuation_screens','temperature','factor_boards'),
}


def component_date(store, board, component, default):
    manifest=getattr(store,'daily_boards',{})
    return manifest.get(board,{}).get(component,{}).get('data_date') or default


def cutoff_for(store, board, default):
    return max([default]+[component_date(store,board,key,default) for key in GROUPS.get(board,())])


def observation(params, cutoff):
    raw=params.get('date') or params.get('trade_date') or cutoff
    return min(date.fromisoformat(str(raw)[:10]).isoformat(),cutoff)


def has(store, name):
    return Path(store.path('revision/daily/'+name+'.parquet')).is_file()


def rows(store, name, **kwargs):
    return store.read('revision/daily/'+name+'.parquet',**kwargs)


def annotate(store, board, result):
    marks=getattr(store,'daily_boards',{}).get(board)
    if marks:
        result['daily_components']=marks
        # All component dates are shown separately; the top-level data_date
        # remains the observation used by the original primary panel.
        result.setdefault('warnings',[]).append('分项数据日：'+'；'.join(
            f'{LABELS.get(key,key)} {part.get("data_date") or "—"}'
            +('（沿用）' if part.get('status')=='retained' else '') for key,part in marks.items()))
    return result


LABELS={
    'stock_structure':'全A估值结构','official_index_pe_pb':'正式指数PE/PB',
    'dividends_and_historical_statistics':'股息和历史统计','main_money':'个股主力净额汇总',
    'stock_main_windows':'个股主力窗口','etf':'ETF份额/申赎','margin_cross_dragon_balance':'两融、跨境、龙虎榜和平衡',
    'index_prices':'指数行情','holdings':'持仓报告期','portfolio_temperature':'影子组合和托底温度',
    'stock_quotes':'独立股票行情','company_nav':'公司净值','cost_and_signals':'筹码成本与信号',
    'market_main20':'辅助主力20日','temperature':'情绪温度','benchmark_index':'指数行情',
    'valuation_screens':'PE/PB/PS候选','factors_temperature_financials':'其他候选因子、逆向温度及财务'}

LABELS.update({
    'market_main_native':'东方财富大盘主力净额',
    'sector_windows':'板块资金窗口','margin_securities':'个券两融',
    'margin_exchange':'交易所两融','cross_border':'跨境资金',
    'dragon_events':'龙虎榜事件','dragon_seats':'龙虎榜席位',
    'capital_balance':'资金供需分项','support_signals':'ETF托底信号',
    'support_temperature':'托底温度','shadow_portfolio':'影子组合',
    'quarter_top10':'季度前十大持仓','factor_boards':'逆向候选因子',
})
