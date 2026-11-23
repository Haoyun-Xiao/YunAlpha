/* Page placement is keyed by stable section identifiers, not response order. */
const PAGES={
 national_team:{overview:{market:12,holdings_kpi:12,etf_subscription_kpi:12,etf_overview:6,subject_value:3,industry:3,etf_volume_anomalies:4,announcements:4,subject_history:4},stock:{stocks:5,stock_list:5,stock_structure:7,stock_shareholders:7,stock_history:7,stock_price:7}},
 sentiment:{overview:{gauge:4,radar:4,contributions:4,'temperature-history':12,freshness:12},dimensions:{'dimension-cards':12,'dimension-contributions':6,'dimension-items':6}},
 valuation:{overview:{temperature:12,matrix:8,map:4,'temperature-history':12}},
 chips:{stock:{'stock-summary':12,'cost-bands':8,'stock-indicators':4,'stock-events':6,peers:6,candlestick:8}},
 fund_holdings:{overview:{market_kpi:12,industry:4,consensus_buy:4,consensus_sell:4,quarter_top10_DESC:6,quarter_top10_ASC:6,band_totals:6,history:6,crowding:6}},
 capital_flow:{overview:{overview_summary:12,overview_main_trend:4,overview_board_flows:4,southbound_daily:4,overview_dragon:4,margin_exchange_flow:4,etf_category_flow:4,balance:12},main:{main_trend:8,main_kpi:12,main_cumulative:4},stocks:{stock_flows:8,stock_heat:4,stock_detail:4,price:4,three_sources:4}}
};
export function sectionLayout(board,panel,section){
 const value=PAGES[board]?.[panel]?.[section.id];
 return {span:value??section.preferred_span??(section.kind==='kpi'?12:section.kind==='table'?12:6),height:board==='capital_flow'&&panel==='overview'&&['overview_main_trend','overview_board_flows','southbound_daily','overview_dragon','margin_exchange_flow','etf_category_flow'].includes(section.id)?165:section.id==='matrix'?0:section.id==='cost-bands'?300:240};
}
