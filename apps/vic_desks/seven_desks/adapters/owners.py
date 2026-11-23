"""Read-only adapters for the three ownership / capital desks.

All broad-market aggregation is an offline artifact. Request SQL only selects
published frozen review tables or a selected entity/basket (maximum six firms).
No ingestion, lake mutation, or browser state is involved.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache
from contextvars import ContextVar
import json
import math
from pathlib import Path
import re

FREEZE = '2026-11-09'
from seven_desks.settings import FROZEN_VERSION
from seven_desks.store import Store, connect
from seven_desks.daily import cutoff_for, observation

BOARDS = {
    'national_team': ('国家队全景', [('overview','全景总览'),('etf','ETF'),('portfolio','组合'),('holdings','持仓'),('changes','调仓'),('entity','主体'),('stock','个股')]),
    'fund_holdings': ('A股公募持仓', [('overview','总览'),('ranking','公司排行'),('corp','公司详情'),('stock','个股透视'),('industries','行业动向'),('heat','持股热力'),('compare','公司比较')]),
    'capital_flow': ('A股资金流', [('overview','总览'),('main','主力动向'),('boards','板块跟踪'),('stocks','个股热图'),('margin','两融杠杆'),('cross','跨境资金'),('etf','ETF资金'),('toplist','龙虎榜'),('corporate','产业资本')]),
}
ENTITY = {'medium':'中口径','narrow':'窄口径','hj_inv':'汇金投资','hj_am':'汇金资管','zj':'证金','sb_council':'社保理事会','sb_pf':'社保组合','gx':'国新','ct':'诚通'}
CATEGORY = {'all':'全部','broad':'宽基','industry_theme':'行业主题','strategy_style':'策略风格','cross_border':'跨境','other':'其他','unclassified':'未分类'}
LABEL = dict(symbol='证券代码',name='名称',company_id='基金公司',entity_id='主体',report_period='报告期',trade_date='数据日期',industry_name='申万行业',industry_code='行业代码',market_value='可见持仓市值',visible_market_value='可见持仓市值',shares='持股数',holding_pct='占流通股比例(%)',subject_count='主体数',subjects='主体',stock_count='持股只数',fund_count='基金份额数',company_count='公司数',share_class_count='基金份额数',weight='持仓权重',market_weight='全A样本权重',overweight='超配',hhi='HHI',top10_weight='前十集中度',estimated_net_change='估算持股变动额',estimated_change_value='估算持股变动额',size_band='规模分档',new_count='新进',increase_count='增持',decrease_count='减持',exit_count='退出可见',coverage_ratio='覆盖率',quality_status='数据质量',main_net='市场净额(源字段)',main_net_inflow='主力净流入',net_flow='净流入(源单位)',flow_5d='5日净额',flow_20d='20日净额',net_subscription='估算净申购',cumulative_flow='累计净申购',fund_size='规模',shares_outstanding='份额',nav_used='估算使用净值',margin_balance='融资余额',financing_net_buy='融资买入减偿还',balance_change='融资余额变化',margin_buy='融资买入',net_buy='净买入(CNY)',turnover='成交额',net_amount='净额',buy_amount='买入额',sell_amount='卖出额',channel='通道',currency='币种',reasons='上榜原因',seat='营业部',daily_return='当日收益',next_trading_day_return='下一交易日收益',main_turnover_ratio='净流入占成交',change_status='可见持仓状态',float_ratio='公募占流通股',crowding_score='拥挤度',buyers='买入公司数',sellers='卖出公司数',consensus='共识信号',holding_rank='公司持仓排名',account_count='账户数',close='收盘价',float_market_value='流通市值估计',market_board='板块',period_return='区间收益',jaccard='只数Jaccard',weight_overlap='权重重叠',estimated_amount='公告估算金额',holder_name='股东',announce_date='公告日',unlock_date='解禁日',estimated_value='解禁估算市值',float_share='解禁股数',raised_amount='IPO募资额',issue_price='发行价',issue_shares='发行股数',progress='回购进度',amount='源披露金额',volume='源披露股数',support_event='托底候选日',balance_event='平准候选日',threshold_yi='阈值(亿元)',benchmark_return='沪深300收益',intraday_recovery='日内修复',volume_ratio='成交放量倍数',sector_id='板块代码',sector_name='板块',company_a='公司A',company_b='公司B',main_net_inflow_sum='窗口主力净额')
LABEL.update(turnover_rate='当日换手率(%)',margin_repay='融资偿还额',observed_net_subscription='有效样本净申购',complete_net_subscription='完整净申购',observed_size='已覆盖规模',valid_flow_count='有效ETF数',volume_percentile='放量分位(%)',pressure_percentile='跌幅压力分位(%)',recovery_percentile='日内修复分位(%)')
PCT_KEYS = {'portfolio_return','excess_return','volatility','historical_peak_drawdown','fresh_price_weight','equal_weight_return','scale_weight_return','equal_weight_excess','scale_weight_excess','mean_observed_coverage','mean_scale_coverage','arithmetic_contribution','institution_ratio','margin_float_ratio','weight','market_weight','overweight','top10_weight','coverage_ratio','float_ratio','jaccard','weight_overlap','daily_return','next_trading_day_return','main_turnover_ratio','period_return','benchmark_return','intraday_recovery','net_ratio','mean_daily_return','share_of_ashare_turnover','financing_buy_turnover_ratio','stock_turnover','estimated_amount_turnover','share_change_ratio','float_holding_ratio'}
WARNINGS = {
 'national_team':['仅已核实别名对应的前十大流通股东可见持仓；退出可见范围不等于清仓。','行业与ETF分类含当前快照近似；托底温度的原站公式未知。'],
 'fund_holdings':['基金份额数分别统计；仅对已验证关系且逐股组合相同的共享份额去重金额和股数，未验证关系不强并。资产分母未核定的公司期保留为空。','持股变动额按复权股数变化及期末价估算；仅合格跨期样本，部分覆盖不代表全市场。公司名仅按冻结唯一证据归并。'],
 'capital_flow':['各数据源单独标明日期，缺值保留为空；不同资金口径不能相加。','ETF净申购为份额变化乘净值估计，当前分类近似；未分类不计入股票ETF。'],
}


LABEL.update({'flow_60d':'60日净额','consecutive_days':'连续净流入/出天数','recent_20_flows':'近20日资金','observed_sum':'已观测合计','complete_window_value':'完整窗口合计','source_as_of':'分项截至日','valid_days':'有效交易日','window_days':'窗口交易日','direction':'平衡方向','line':'资金分项','net_assets':'基金净资产','holding_nav_ratio':'A股持仓占净资产','stock_allocation':'股票仓位','net_asset_coverage':'净资产份额覆盖率','mean_visible_halfyears':'平均连续持有半年度数','return_1y':'近1年等权收益','return_2y':'近2年等权收益','weighted_return_1y':'近1年规模加权收益','excess_1y':'近1年沪深300超额','implied_return':'持仓隐含半年收益','influence_pp':'持仓权重变化','consecutive_halfyears':'连续持有半年度数','post_period_return':'期后收益','net_estimated_change':'估算持股变动额','estimated_net_change':'估算持股变动额','weight_change':'权重变化','composite_score_proxy':'持股综合分（本地）','rank_risers':'排名上升公司数','top10_entrants':'新进公司前十数','current_shares':'本期持股','previous_adjusted_shares':'复权上期持股','turnover_60d':'60日成交额','selected_net':'所选窗口净额','selected_return':'所选窗口成分等权收益','selected_ratio':'所选窗口净占比','observed_net_amount':'已观测净额','denominator_sample':'市值分母样本数','period_value_change':'可见市值较上期','previous_holding_pct':'上期流通占比','holding_pct':'本期流通占比(%)'})
PCT_KEYS.update({'company_coverage_ratio','fund_coverage_ratio','amount_coverage_ratio','change_company_coverage_ratio','change_fund_coverage_ratio','change_amount_coverage_ratio','max_drawdown','holding_nav_ratio','stock_allocation','net_asset_coverage','return_1y','return_2y','weighted_return_1y','excess_1y','implied_return','influence_pp','post_period_return','weight_change','selected_return','selected_ratio','net_estimated_change_ratio'})
LABEL.update({'max_drawdown':'窗口内最大回撤','listing_date':'上市日','holding_change':'复权较上期股数变化','active_manager_name_count':'期末经理去重姓名数','public_holding_shares':'报告期公募持股','stock_count_change':'持股只数较上期','leading_industry':'首位行业','top5_weight':'前五集中度','main_net_5d':'5日主力净流入','main_net_20d':'20日主力净流入','main_net_60d':'60日主力净流入','support_days':'候选托底日数','balance_days':'候选平准日数','unusual_volume_count':'放量ETF只数','previous_20d_average_turnover':'前20交易日成交均值','net_flow_cny':'统一CNY净流入','source_unit':'源金额单位','display_unit':'展示币种','current_holding_pct':'本期占流通比(%)','current_value':'本期可见市值'})

LABEL.update({'sample_new_count':'合格样本新进家次','sample_increase_count':'合格样本增持家次','sample_decrease_count':'合格样本减持家次','sample_exit_count':'合格样本退出可见家次','sample_estimated_change_value':'合格样本估算持股变动额','qualified_company_count':'变动合格公司数','company_coverage_ratio':'变动公司覆盖率','qualified_company_fund_count':'合格公司基金份额数','qualified_comparable_fund_count':'合格公司可比基金份额数','fund_coverage_ratio':'合格公司份额覆盖率','qualified_company_market_value':'合格公司静态可见持仓市值','amount_coverage_ratio':'合格公司静态金额覆盖率','flow_100d':'100日净额','main_net_100d':'100日主力净额'})

ACTION_CONTRACT={'type':'params','panel':'target tab id','params':'map of literal values or $row_column substitutions; preserve other URL params; reset page/entity','actions':'optional section.actions or column.actions buttons, same params contract; basket_add uses company_id','presentation':'treemap uses size_key/color_key; pie uses x/series; candlestick uses open/high/low/close plus markers; stacked uses stack/group; fallback kind remains existing six types','sparkline':'columns[].format=sparkline with list[float|null] row value'}


def _params_action(panel,**params):
    return {'type':'params','panel':panel,'params':params}


def _multi(key,label,values,options):
    out=_select(key,label,','.join(values),options);out['multiple']=True;out['max_selections']=4 if key=='etfs' else 6 if key=='ids' else 9;return out


_META_STORE=ContextVar('owners_metadata_store',default=None)


@lru_cache(maxsize=32)
def _read_meta(path):
    return json.loads(Path(path).read_text())


def _meta(store=None):
    selected=store if store is not None else (_META_STORE.get() or Store(FROZEN_VERSION))
    path=Path(selected.path('revision/owners/metadata.json'))
    if not path.exists():path=Path(selected.path('local_owners/metadata.json'))
    if not path.exists():return {'company_ids':[],'periods':[],'fund_periods':[],'etf_indexes':[],'industries':[],'missing_reason':'该版本未冻结控件元数据，不回退当前LOCAL。'}
    return _read_meta(str(path))


def metadata(board,store=None):
    if board not in BOARDS:
        raise ValueError('Unknown ownership desk: ' + str(board))
    title,tabs = BOARDS[board]
    default = {'period':'2026Q3','scope':'medium'} if board=='national_team' else ({'period':'2026Q2','band':'all'} if board=='fund_holdings' else {'date':FREEZE,'win':'20'})
    return {'id':board,'title':title,'available_periods':_meta(store).get('periods' if board=='national_team' else 'fund_periods',[]) if board!='capital_flow' else [],'metadata_source':('frozen revision/owners/metadata.json' if Path(store.path('revision/owners/metadata.json')).exists() else 'frozen local_owners/metadata.json') if store is not None else 'local builder metadata','description':f'冻结至 {FREEZE} 的只读复刻；每个图表保留其数据口径和缺口。','tabs':[{'id':k,'label':v} for k,v in tabs],'action_contract':ACTION_CONTRACT,'defaults':dict(tab='overview',q='',sort='',desc='1',page=1,page_size=50,**default)}


def _clean(value):
    if isinstance(value,(list,tuple)):return [_clean(v) for v in value]
    if isinstance(value,dict):return {k:_clean(v) for k,v in value.items()}
    if isinstance(value,(date,datetime)): return value.isoformat()
    if isinstance(value,Decimal): return float(value)
    if isinstance(value,float) and not math.isfinite(value): return None
    return value


class _Data:
    def __init__(self, store, observation_date=None):
        store = store if store is not None else Store(FROZEN_VERSION)
        self.c = connect()
        self.views=set()
        # Register lazy Parquet views. Paths come from inventory, never user input.
        source_root=Path(store.path('owners'))
        local_root=Path(store.path('local_owners'))
        for p in source_root.glob('*.parquet'):
            if p.name.startswith(('tmp_', '_')):continue
            self._view(p.stem,p)
        for p in local_root.glob('*.parquet'):
            resolved=p
            self._view('local_'+p.stem,resolved)
            if p.stem not in self.views:self._view(p.stem,resolved)
        corrected_root=Path(store.path('ac_fixed'))
        for p in corrected_root.glob('*.parquet'):
            if p.stem in self.views:
                self._view(p.stem,p)
            if 'local_'+p.stem in self.views:
                self._view('local_'+p.stem,p)
        for original,completed in {'stock_fund_flow_windows':'local_stock_flow_complete','sector_flow_windows':'local_sector_flow_complete','margin_exchange_flow':'local_margin_exchange_complete','etf_daily_flow':'local_etf_trading_complete','company_period':'local_company_rank_complete','company_stock_holdings':'local_company_holding_complete','fund_stock_crowding':'local_fund_heat_complete','local_sw_flow':'local_sw_flow_complete','local_fund_industry_market':'local_fund_industry_complete','local_fund_band':'local_fund_band_complete'}.items():
            if completed in self.views:self.c.execute('CREATE OR REPLACE VIEW '+original+' AS SELECT * FROM '+completed)
        if 'cross_border_daily' in self.views:
            self.c.execute("CREATE OR REPLACE VIEW southbound_daily AS SELECT trade_date,channel,net_amount net_buy,buy_amount,sell_amount,currency,source,sum(net_amount) OVER(PARTITION BY channel ORDER BY trade_date) cumulative_net_buy,'HKD_direct_net_observations_no_CNY_conversion' quality_status FROM cross_border_daily WHERE channel IN ('south_sh','south_sz') AND currency='HKD'")
            if (source_root/'capital_balance_daily.parquet').exists():
                self._view('raw_capital_balance_daily',source_root/'capital_balance_daily.parquet')
                self._balance_sql = "WITH cn AS (SELECT trade_date,CASE WHEN count(DISTINCT channel)=2 AND count(net_amount)=2 THEN sum(net_amount) END corrected_south FROM cross_border_daily WHERE channel IN ('south_sh','south_sz') AND currency='CNY' GROUP BY trade_date) SELECT b.* REPLACE(c.corrected_south AS southbound_net_buy,b.etf_net_subscription+b.financing_net_buy-c.corrected_south-b.ipo_funding-b.holder_decrease AS strict_net_balance,'southbound_direct_CNY_only_no_total_plus_components;missing_FX_null' AS quality_status) FROM raw_capital_balance_daily b LEFT JOIN cn c USING(trade_date)"
                self.c.execute("CREATE OR REPLACE VIEW capital_balance_daily AS "+self._balance_sql)
        # Revision outputs win only within the selected immutable version. A
        # baseline version never falls through to a mutable preview namespace.
        revision_root=Path(store.path('revision/owners'))
        aliases={'stock_flow_complete':'stock_fund_flow_windows','sector_flow_complete':'sector_flow_windows',
                 'etf_trading_complete':'etf_daily_flow','company_rank_complete':'company_period',
                 'fund_heat_complete':'fund_stock_crowding','fund_industry_complete':'local_fund_industry_market',
                 'fund_band_complete':'local_fund_band','sw_flow_complete':'local_sw_flow'}
        for artifact in revision_root.glob('*.parquet'):
            self._view(artifact.stem,artifact)
            self._view('local_'+artifact.stem,artifact)
            if artifact.stem in aliases:self._view(aliases[artifact.stem],artifact)
        for name, (artifact, frozen) in {
            'local_market_stock_flow_windows':('capital-market','market_stock_flow_windows'),
            'stock_fund_flow_windows':('capital-stock','stock_flow_complete'),
            'etf_daily_flow':('etf-flow','etf_trading_complete'),
        }.items():
            daily=Path(store.path('revision/daily/'+artifact+'.parquet'))
            previous=Path(store.path('revision/owners/'+frozen+'.parquet'))
            if daily.is_file() and previous.is_file():
                view='daily_'+name
                parent='parent_'+name
                self._view(view,daily)
                self._view(parent,previous)
                date_filter=''
                if artifact=='etf-flow' and observation_date:
                    # The observation comes from date.fromisoformat, not raw query text.
                    date_filter=f" WHERE available_date<=DATE '{date.fromisoformat(observation_date)}'"
                selected=f'SELECT * FROM {view}{date_filter}'
                self.c.execute(f'CREATE OR REPLACE VIEW {name} AS SELECT * FROM {parent} '
                               f'WHERE trade_date NOT IN (SELECT DISTINCT trade_date FROM ({selected})) '
                               f'UNION ALL BY NAME {selected}')
        index=Path(store.path('revision/daily/index-quotes.parquet'))
        if index.is_file():
            self._view('phase_daily_index',index)
        quotes=Path(store.path('revision/daily/stock-quotes.parquet'))
        if quotes.is_file():
            self._view('phase_daily_quotes',quotes)
        self._view('capital_market_native',Path(store.path('revision/daily/capital-market-native.parquet')))
        self.observation_date = observation_date
        self.updated = set()
        day_keys = {'fund_return_weight_evidence':'nav_date','holdertrade_daily':'obs_date',
                    'holdertrade_valued':'announce_date','local_dragon_regulars_history':'anchor_date'}
        per_row = {
            'cross_border_daily':('channel','currency'), 'cross_border_top_trades_detail':('channel','symbol'),
            'margin_security_flow':('symbol',), 'margin_exchange_flow':('exchange',),
            'local_margin_industry_complete':('exchange','industry_code'), 'local_margin_ratio_complete':('exchange',),
        }
        parent_paths = {
            'local_market_stock_flow_windows':'revision/owners/market_stock_flow_windows',
            'stock_fund_flow_windows':'revision/owners/stock_flow_complete',
            'etf_daily_flow':'revision/owners/etf_trading_complete',
            'local_sw_flow':'revision/owners/sw_flow_complete',
            'sector_flow_windows':'revision/owners/sector_flow_complete',
            'company_nav_daily':'revision/owners/company_nav_daily',
            'margin_exchange_flow':'local_owners/margin_exchange_complete',
        }
        for artifact in sorted(Path(store.path('revision/owners_daily')).glob('*.parquet')):
            name = artifact.stem
            if name not in self.views:
                continue
            # These histories use the immutable full-schema parent rather than
            # the earlier narrow daily projection.
            parent = parent_paths.get(name, 'local_owners/'+name[6:] if name.startswith('local_') else 'owners/'+name)
            incoming, retained = 'owners_increment_'+name, 'owners_parent_'+name
            if name == 'capital_balance_daily':
                self.c.execute(f'CREATE TEMP VIEW {retained} AS '+self._balance_sql)
            else:
                self._view(retained,Path(store.path(parent+'.parquet')))
            self._view(incoming,artifact)
            selected=f'SELECT * FROM {incoming}'
            if observation_date and name in ('etf_daily_flow','etf_category_flow','etf_index_flow','local_etf_period_asof'):
                selected+=f" WHERE available_date<=DATE '{date.fromisoformat(observation_date)}'"
            keys=(day_keys.get(name,'trade_date'),*per_row.get(name,()))
            match=' AND '.join(f'a.{key} IS NOT DISTINCT FROM b.{key}' for key in keys)
            self.c.execute(f'CREATE OR REPLACE VIEW {name} AS SELECT a.* FROM {retained} a '
                           f'ANTI JOIN ({selected}) b ON {match} UNION ALL BY NAME {selected}')
            self.updated.add(name)
        if 'cross_border_daily' in self.updated:
            self.c.execute("""CREATE OR REPLACE VIEW southbound_daily AS
                SELECT trade_date,channel,net_amount net_buy,buy_amount,sell_amount,currency,source,
                  sum(net_amount) OVER(PARTITION BY channel ORDER BY trade_date) cumulative_net_buy,
                  'HKD_direct_net_observations_no_CNY_conversion' quality_status FROM cross_border_daily
                WHERE channel IN('south_sh','south_sz') AND currency='HKD'""")

    def _view(self,name,path):
        if not path.is_file(): return
        self.c.execute('CREATE OR REPLACE VIEW "'+name+'" AS SELECT * FROM read_parquet(\''+str(path).replace("'","''")+'\',hive_partitioning=false)')
        self.views.add(name)
    def q(self,sql,args=()):
        cur=self.c.execute(sql,list(args)); keys=[x[0] for x in cur.description]
        return [{k:_clean(v) for k,v in zip(keys,row)} for row in cur.fetchall()]
    def close(self): self.c.close()


def _section(sid,title,rows=None,kind='table',columns=None,x=None,series=None,missing=None,quality=None,action=None,**extra):
    rows=rows or []
    keys=columns or (list(rows[0]) if rows else [])
    cols=[{'key':k,'label':LABEL.get(k,k),**({'format':'sparkline'} if k=='recent_20_flows' else {'format':'percent'} if k in PCT_KEYS else {})} if isinstance(k,str) else k for k in keys]
    out={'id':sid,'title':title,'kind':kind,'columns':cols,'rows':rows,**extra}
    for col in cols:
        if re.search(r'市值|金额|成交额|净额|净流|净买|申购|募资|融资余额|估算持股变动额',col.get('label','')) and col.get('format')!='percent':col.update(format='money',unit='CNY')
    if x:out['x']=x
    if series:out['series']=[{'key':s,'label':LABEL.get(s,s)} if isinstance(s,str) else s for s in series]
    if missing and not rows:out['missing_reason']=missing
    elif not rows:out['missing_reason']='当前筛选无可用记录；未将缺失数据填为0。'
    if quality:out['quality']=quality
    if action:out['action']=action if isinstance(action,dict) else {'type':'entity','key':action}
    return out


def _empty(sid,title,why):return _section(sid,title,missing=why)

def _select(key,label,value,options):
    return {'key':key,'label':label,'type':'select','value':str(value),'options':[{'value':str(k),'label':v} for k,v in options]}

def _text(key,label,value=''):return {'key':key,'label':label,'type':'text','value':str(value),'options':[]}

def _number(key,label,value):return {'key':key,'label':label,'type':'number','value':value,'options':[]}

def _int(v,default,lo,hi):
    try:return max(lo,min(hi,int(v)))
    except (TypeError,ValueError):return default

def _period(v,default):
    s=str(v or default)
    m=re.fullmatch(r'(20\d\d)Q([1-4])',s)
    if m:return min(m[1]+'-'+{'1':'03-31','2':'06-30','3':'09-30','4':'12-31'}[m[2]],FREEZE)
    try:return min(date.fromisoformat(s),date.fromisoformat(FREEZE)).isoformat()
    except ValueError:return _period(default,default)

def _symbol(v):
    s=str(v or '').strip().upper()
    if re.fullmatch(r'\d{6}',s):return s+('.SH' if s.startswith(('5','6','9')) else '.BJ' if s.startswith(('4','8')) else '.SZ')
    return s if re.fullmatch(r'\d{6}\.(?:SH|SZ|BJ|OF)',s) else ''

def _ids(value):
    vals=value if isinstance(value,list) else str(value or '').split(',')
    aliases={'178':'易方达基金管理有限公司','94':'华夏基金管理有限公司'}
    valid=set(_meta()['company_ids']);out=[]
    for v in vals:
        k=aliases.get(str(v),str(v))
        if k in valid and k not in out:out.append(k)
    return out[:6]

def _company(v):
    ids=_ids(v)
    if ids:return ids[0]
    match=[s for s in _meta()['company_ids'] if str(v or '').strip() and str(v).strip() in s]
    return match[0] if len(match)==1 else ''

def _filters(p,alias='',search=('symbol','name'),industry=True):
    prefix=alias+'.' if alias else '';w=[];a=[]
    if p.get('q') and search:
        w.append('('+' OR '.join(f'contains(coalesce({prefix}{k}::VARCHAR,\'\'),?)' for k in search)+')');a.extend([str(p['q'])]*len(search))
    l1=p.get('l1') or p.get('industry')
    if industry and l1 and l1!='all':w.append(f'{prefix}industry_code=?');a.append(l1)
    return w,a

def _page(d,sid,title,base,args,p,cols,default_sort,action='symbol',quality=None):
    allowed=set(cols)
    order=p.get('sort') if p.get('sort') in allowed else default_sort
    direction='ASC' if p.get('order')=='asc' or str(p.get('desc','1')).lower() in ('0','false','asc') else 'DESC'
    page=_int(p.get('page'),1,1,10000);size=_int(p.get('page_size'),50,1,100)
    count=d.q('SELECT count(*) n FROM ('+base+')',args)[0]['n']
    rows=d.q('SELECT '+','.join('"'+k+'"' for k in cols)+' FROM ('+base+') ORDER BY "'+order+'" '+direction+' NULLS LAST LIMIT ? OFFSET ?',[*args,size,(page-1)*size])
    return _section(sid,title,rows,columns=cols,action=action,quality=quality,total=count,page=page,page_size=size)


def _controls(board,p):
    c=[_text('q','搜索代码 / 名称',p.get('q',''))]
    if board!='capital_flow':
        ps=_meta()['periods' if board=='national_team' else 'fund_periods']
        c.insert(0,_select('period','报告期',_period(p.get('period'),'2026Q3' if board=='national_team' else '2026Q2'),[(s,s) for s in ps]))
    else:
        c=[_text('date','数据截止',p.get('date',FREEZE)),_select('win','统计窗口',p.get('win',20),[(1,'今日'),(5,'5日'),(20,'20日'),(60,'60日'),(100,'100日')])]+c
    if board=='national_team':c.append(_select('scope','主体口径',p.get('scope','medium'),[('medium','中口径'),('narrow','窄口径')]))
    if board=='fund_holdings':c.append(_select('band','公司规模档',p.get('band','all'),[('all','全部'),('top','头部 >2000亿'),('large','大型 500–2000亿'),('medium','中型 100–500亿'),('small','小型 <100亿')]))
    c.extend([_number('page','页码',_int(p.get('page'),1,1,10000)),_number('page_size','每页条数',_int(p.get('page_size'),50,1,100))])
    return c


# Keep the latest visible filing together: never fill a partial amendment
# with ranks from a different announcement.
_PHASE3_FLOAT_FILINGS = """
    WITH visible AS (
      SELECT DISTINCT symbol,record_date,announce_date,holder_rank,holder_name,holding_shares,holding_pct
      FROM ({source}) h WHERE {filters} AND announce_date<=CAST(? AS DATE)
    ), latest AS (
      SELECT symbol,record_date,max(announce_date) announce_date FROM visible GROUP BY 1,2
    ), chosen AS (
      SELECT v.* FROM visible v JOIN latest USING(symbol,record_date,announce_date)
    ), completeness AS (
      SELECT symbol,record_date, count(*)=10 AND count(DISTINCT holder_rank)=10
          AND count(DISTINCT holder_name)=10 AND count(holding_shares)=10
          AND min(holder_rank)=1 AND max(holder_rank)=10 AS whole_list
      FROM chosen GROUP BY 1,2
    )
"""


def _float_holder_detail(d, code, period, asof):
    source='SELECT * FROM local_full_top_holders'
    if 'phase3_float_holders' in d.views:
        cols='symbol,record_date,announce_date,holder_rank,holder_name,holding_shares,holding_pct'
        source=(f"SELECT {cols} FROM phase3_float_holders UNION ALL SELECT {cols} "
                "FROM local_full_top_holders WHERE record_date NOT IN ('2026-06-30','2026-09-30')")
    if date.fromisoformat(period).strftime('%m-%d') not in ('03-31','06-30','09-30','12-31'):
        return []
    sql=_PHASE3_FLOAT_FILINGS.format(
        source=source,filters="symbol=? AND record_date IN(CAST(? AS DATE),last_day(CAST(? AS DATE)-INTERVAL '3 months'))")
    return d.q(sql+"""
        SELECT h.record_date,h.announce_date,h.holder_rank,h.holder_name,h.holding_shares,h.holding_pct,
               CASE WHEN a.whole_list AND b.whole_list AND p.holding_shares IS NOT NULL
                    THEN h.holding_shares-p.holding_shares END holding_change,
               a.whole_list
        FROM chosen h JOIN completeness a USING(symbol,record_date)
        LEFT JOIN completeness b ON b.symbol=h.symbol AND b.record_date=last_day(h.record_date-INTERVAL '3 months')
        LEFT JOIN chosen p ON b.whole_list AND p.symbol=h.symbol AND p.record_date=b.record_date AND p.holder_name=h.holder_name
        ORDER BY h.record_date DESC,h.holder_rank,h.holder_name
    """,[code,period,period,asof])


def _phase3_holder_scope(d, period, asof, subjects):
    if 'phase3_float_holders' not in d.views or period not in ('2026-06-30','2026-09-30'):
        return []
    sql=_PHASE3_FLOAT_FILINGS.format(source='SELECT * FROM phase3_float_holders',filters='record_date=CAST(? AS DATE)')
    binds=','.join('?'*len(subjects))
    return d.q(sql+f"""
        SELECT CAST(? AS DATE) report_period,count(DISTINCT h.symbol) published_stock_count,
               count(DISTINCT h.symbol) FILTER(WHERE v.whole_list) whole_list_stock_count,
               count(DISTINCT h.symbol) FILTER(WHERE v.whole_list AND n.entity_id IN ({binds})) known_alias_stock_count
        FROM chosen h JOIN completeness v USING(symbol,record_date)
        LEFT JOIN national_alias_evidence n USING(holder_name)
    """,[period,asof,period,*subjects])


def _phase3_company_scale(d, period, sections, company=None):
    if 'phase3_fund_scale' not in d.views:
        return
    filt=' AND company_id=?' if company else ''
    args=[period,company] if company else [period]
    if company and 'phase3_fund_companies' in d.views and period=='2026-06-30':
        identities=d.q("""SELECT company_id AS registered_company_id,name AS registered_company_name,source
            FROM phase3_fund_companies
            WHERE name=? AND setup_date<=? AND (end_date IS NULL OR end_date>=?)
            QUALIFY count(DISTINCT company_id) OVER()=1 ORDER BY source LIMIT 1""",[company,period,period])
        if identities:
            sections.append(_section('company_identity','基金公司主档 · 当前名称精确匹配',identities,
                columns=['registered_company_id','registered_company_name','source'],
                quality='正式基金公司主档；未据现名推定历史更名或公司接管。'))
    rows=d.q('''WITH x AS (SELECT *,sample_holding_ratio BETWEEN 0 AND 1 usable
                FROM phase3_fund_scale WHERE report_period=?'''+filt+''')
            SELECT report_period,count(*) FILTER(WHERE usable) verified_product_count,
                sum(share_count) FILTER(WHERE usable)::BIGINT verified_share_count,
                sum(sample_assets_cny) FILTER(WHERE usable) sample_assets_cny,
                sum(sample_a_stock_value_cny) FILTER(WHERE usable) sample_a_stock_value_cny,
                sum(sample_a_stock_value_cny) FILTER(WHERE usable)
                  /nullif(sum(sample_assets_cny) FILTER(WHERE usable),0) sample_holding_pct,
                count(*) FILTER(WHERE NOT usable) excluded_ratio_count
            FROM x GROUP BY 1''',args)
    if rows:
        sections.append(_section('verified_scale_sample','同报告期可核实产品样本 · 非公司总净资产',rows,
            columns=['report_period',
                     {'key':'verified_product_count','label':'可比产品数'},
                     {'key':'verified_share_count','label':'所含份额类别数'},
                     {'key':'sample_assets_cny','label':'样本净资产','format':'money','unit':'CNY'},
                     {'key':'sample_a_stock_value_cny','label':'样本 A 股持仓市值','format':'money','unit':'CNY'},
                     {'key':'sample_holding_pct','label':'样本 A 股占净值','format':'percent'},
                     {'key':'excluded_ratio_count','label':'待核异常产品数'}],
            quality='完整匹配公告份额清单与冻结 A 股持仓。规模按亿元源值相加后换算为元；分子超过净资产的产品单列待核，不进入样本占比。事后数据，不代公司全量分母。'))


def _shadow_views(d, asof):
    anchor=date.fromisoformat(asof)
    # Match owners_summaries: date-window return compounds daily returns;
    # arithmetic attribution divides by prior segment value on each day.
    d.c.execute(f"""CREATE TEMP VIEW observed_shadow_summary AS
      WITH w AS(SELECT * FROM (VALUES('1y',366),('3y',1096)) t(range_id,days)), a AS (
        SELECT portfolio,range_id,min(trade_date) start_date,max(trade_date) end_date,
          exp(sum(ln(1+daily_return)))-1 portfolio_return,exp(sum(ln(1+benchmark_return)))-1 benchmark_return,
          stddev_samp(daily_return)*sqrt(252) volatility,arg_max(component_count,trade_date) component_count,
          arg_max(rebalance_date,trade_date) rebalance_date,arg_max(fresh_price_weight,trade_date) fresh_price_weight
        FROM national_shadow_nav CROSS JOIN w WHERE trade_date>DATE '{anchor}'-days AND trade_date<=DATE '{anchor}'
        GROUP BY 1,2)
      SELECT *,portfolio_return-benchmark_return excess_return FROM a""")
    d.c.execute(f"""CREATE TEMP VIEW observed_shadow_latest AS
      SELECT *,segment_value_change/nullif(segment_value-segment_value_change,0) daily_return
      FROM national_shadow_component_daily WHERE trade_date<=DATE '{anchor}'
      QUALIFY trade_date=max(trade_date) OVER(PARTITION BY portfolio)""")
    d.c.execute(f"""CREATE TEMP VIEW observed_shadow_contribution AS
      WITH w AS(SELECT * FROM (VALUES('1y',366),('3y',1096)) t(range_id,days)), a AS (
        SELECT portfolio,symbol,trade_date,segment_value_change/nullif(
          sum(segment_value) OVER p-sum(segment_value_change) OVER p,0) contribution
        FROM national_shadow_component_daily WHERE trade_date>DATE '{anchor}'-1096 AND trade_date<=DATE '{anchor}'
        WINDOW p AS(PARTITION BY portfolio,trade_date,rebalance_date))
      SELECT portfolio,symbol,range_id,sum(contribution) arithmetic_contribution,count(contribution) valid_days
      FROM a CROSS JOIN w WHERE trade_date>DATE '{anchor}'-days GROUP BY 1,2,3""")
    d.c.execute(f"""CREATE TEMP VIEW observed_shadow_monthly AS
      SELECT portfolio,cast(date_trunc('month',trade_date) AS DATE) month_start,
        exp(sum(ln(1+daily_return)))-exp(sum(ln(1+benchmark_return))) excess_return
      FROM national_shadow_nav WHERE trade_date<=DATE '{anchor}' GROUP BY 1,2""")


def _company_windows(d, asof):
    if 'company_nav_daily' not in d.updated:
        return 'company_return_windows'
    anchor=date.fromisoformat(asof)
    d.c.execute(f"""CREATE TEMP VIEW observed_company_returns AS
      WITH end_day AS (
        SELECT max(trade_date) d FROM company_nav_daily WHERE trade_date<=DATE '{anchor}' AND equal_weight_return IS NOT NULL
      ), benchmark AS (
        SELECT trade_date,close,lag(close) OVER(ORDER BY trade_date) prior_close FROM local_benchmark_daily
      ), a AS (
        SELECT company_id,calendar_days,min(trade_date) first_date,max(trade_date) last_date,
          exp(sum(ln(1+equal_weight_return)))-1 equal_weight_return,
          exp(sum(ln(1+scale_weight_return)))-1 scale_weight_return,count(equal_weight_return) valid_days,
          avg(coverage_ratio) mean_observed_coverage,avg(scale_coverage_ratio) mean_scale_coverage,
          stddev_samp(equal_weight_return)*sqrt(252) annualized_volatility,min(equal_weight_drawdown) global_peak_drawdown
        FROM company_nav_daily CROSS JOIN end_day CROSS JOIN (VALUES(365),(730)) w(calendar_days)
        WHERE trade_date>d-calendar_days AND trade_date<=d GROUP BY 1,2
      ) SELECT a.*,b1.close/nullif(b0.prior_close,0)-1 benchmark_return,
        equal_weight_return-benchmark_return equal_weight_excess,scale_weight_return-benchmark_return scale_weight_excess
      FROM a LEFT JOIN benchmark b0 ON b0.trade_date=a.first_date LEFT JOIN benchmark b1 ON b1.trade_date=a.last_date""")
    return 'observed_company_returns'


def _national(d,tab,p,sections,controls):
    asof=_period(p.get('date'),FREEZE)
    etf_day=p.get('daily_observation',asof)
    period=_period(p.get('period'),'2026Q3');scope='narrow' if p.get('scope')=='narrow' else 'medium'
    subjects=['hj_inv','hj_am','zj'] if scope=='narrow' else [k for k in ENTITY if k not in ('medium','narrow')]
    entity=p.get('entity',scope);entity=entity if entity in ENTITY else scope
    entity_filter='entity_id IN ('+','.join('?'*len(subjects))+')';entity_args=subjects
    s=sections
    if tab in ('overview','etf'):
        updated_pool='national_etf_pool_daily' in d.updated
        gate=" AND (net_flow_available_date<=? OR trade_date<DATE '"+FREEZE+"')" if updated_pool else ''
        pool=d.q('SELECT * FROM national_etf_pool_daily WHERE trade_date<=?'+gate+' ORDER BY trade_date DESC LIMIT 1',
                 [etf_day,etf_day] if updated_pool else [etf_day])
        s.append(_section('market','宽基ETF与沪深300行情',pool,'kpi',columns=['trade_date','benchmark_return','turnover','volume_ratio','intraday_recovery','net_subscription'],quality='宽基ETF池使用当前分类近似；申赎为T+1披露估计。'))
        if 'phase_daily_index' in d.views:
            quote=d.q("SELECT trade_date,symbol,open,high,low,close,amount FROM phase_daily_index WHERE symbol='000300.SH' AND trade_date>? AND trade_date<=? ORDER BY trade_date DESC LIMIT 1",[FREEZE,etf_day])
            if quote:
                s.append(_section('benchmark_daily','沪深300正式行情 · 与ETF披露日分列',quote,'kpi',
                    columns=['trade_date','close','open','high','low','amount']))
        if tab=='overview':
            s.append(_section('holdings_kpi','季度可见持仓及进退',d.q('SELECT p.*,c.new_count,c.exit_count,c.increase_count,c.decrease_count FROM national_scope_period p LEFT JOIN local_national_change_counts c USING(scope,report_period) WHERE p.scope=? AND p.report_period=?',[scope,period]),'kpi',columns=['report_period','stock_count','visible_market_value','subject_count','new_count','exit_count','increase_count','decrease_count']))
            coverage=_phase3_holder_scope(d,period,asof,subjects)
            if coverage:
                s.append(_section('float_scope','正式流通股东披露及已核实名覆盖',coverage,
                    columns=['report_period',{'key':'published_stock_count','label':'有流通股东披露的股票'},
                             {'key':'whole_list_stock_count','label':'最新榜单完整股票'},
                             {'key':'known_alias_stock_count','label':'完整榜单内匹配核实名股票'}],
                    quality='截至观察日的季末流通股东披露；账户按现有唯一已核实名匹配。此数为证据覆盖，未替换国家队组合或估算未见账户。'))
            etf_kpi=d.q("SELECT max(trade_date) trade_date,count(*) etf_count,sum(net_subscription) net_subscription,sum(flow_5d) flow_5d,sum(flow_20d) flow_20d,CASE WHEN count(volume_ratio)>0 THEN count(*) FILTER(WHERE volume_ratio>=2.5) END unusual_volume_count FROM etf_daily_flow WHERE category='broad' AND trade_date=(SELECT max(trade_date) FROM etf_daily_flow WHERE trade_date<=?)",[etf_day])
            s.append(_section('etf_subscription_kpi','宽基ETF昨日/5日/20日估算净申购',etf_kpi,'kpi',quality='份额披露T+1，按连续交易观测份额差×NAV估计；显示来源日。'))
            anomaly=d.q("SELECT symbol,name,trade_date,turnover,previous_20d_average_turnover,volume_ratio,net_subscription FROM etf_daily_flow WHERE category='broad' AND volume_ratio IS NOT NULL AND trade_date=(SELECT max(trade_date) FROM etf_daily_flow WHERE trade_date<=? AND volume_ratio IS NOT NULL AND category='broad') ORDER BY volume_ratio DESC LIMIT 20",[etf_day])
            s.append(_section('etf_volume_anomalies','逐ETF成交异动榜',anomaly,action=_params_action('etf',etf='$symbol'),quality='成交额/前20交易日均值，历史不满20日则空。'))
            s.append(_section('subject_value','主体可见市值',d.q('SELECT entity_id,visible_market_value,stock_count FROM national_entity_period WHERE report_period=? AND '+entity_filter,[period,*entity_args]),'bar',x='entity_id',series=['visible_market_value'],presentation='pie',action='entity_id'))
            s.append(_section('industry','行业分布',d.q('SELECT industry_code,industry_name,market_value,stock_count FROM local_national_scope_industry WHERE scope=? AND report_period=? ORDER BY market_value DESC',[scope,period]),'bar',x='industry_name',series=['market_value'],presentation='treemap',size_key='market_value',color_key='market_value',action=_params_action('holdings',l1='$industry_code')))
            _group_line(s,'subject_history','主体持股只数趋势',d.q("SELECT report_period,entity_id,stock_count,visible_market_value FROM national_entity_period WHERE report_period<=? AND last_day(report_period)=report_period AND month(report_period) IN(3,6,9,12) AND "+entity_filter+' ORDER BY report_period',[period,*entity_args]),'report_period','entity_id','stock_count')
            s.append(_section('temperature','托底温度（本地滚动分位代理）',d.q('SELECT * FROM local_temperature_proxy WHERE trade_date<=? AND temperature_proxy IS NOT NULL ORDER BY trade_date DESC LIMIT 1',[etf_day]),'kpi',columns=['trade_date','temperature_proxy','volume_percentile','pressure_percentile','recovery_percentile'],quality='本地代理：252交易观测窗，放量/跌幅压力/日内修复滚动中位秩分位等权；每项至少120样本，非原站公式。'))
            s.append(_section('temperature_history','本地托底温度历史',d.q('SELECT trade_date,temperature_proxy FROM local_temperature_proxy WHERE trade_date BETWEEN cast(? as date)-366 AND ? ORDER BY trade_date',[etf_day,etf_day]),'line',x='trade_date',series=['temperature_proxy']))
            s.append(_section('announcements','已核实别名国家队公告',d.q('SELECT * FROM local_national_announcements WHERE announce_date<=? AND entity_id IN ('+','.join('?'*len(subjects))+') ORDER BY announce_date DESC LIMIT 30',[FREEZE,*subjects]),action='symbol',quality='仅别名逐字匹配已核实七主体；数量是源披露，金额缺价格时为空。'))
        if tab=='overview':
            daily=d.q("SELECT trade_date,sum(net_subscription) net_subscription FROM etf_daily_flow WHERE category='broad' AND trade_date<=? AND trade_date>cast(? as date)-100 GROUP BY trade_date ORDER BY trade_date",[etf_day,FREEZE])
            s.append(_section('etf_overview','宽基ETF近期净申购（估算）',daily,'bar',x='trade_date',series=['net_subscription'],actions=[dict(label='ETF托底 ›',**_params_action('etf'))]))
        else:
            _etf(d,p,s,controls,national=True)
        return
    if tab=='portfolio':
        portfolios=p.get('portfolios','narrow,medium');portfolios=portfolios if isinstance(portfolios,list) else str(portfolios).split(',');selected=[v for v in portfolios if v in ENTITY] or ['narrow','medium']
        controls.extend([_multi('portfolios','组合多选',selected,[(k,v) for k,v in ENTITY.items()]),_select('range','区间',p.get('range','1y'),[('1y','近1年'),('3y','近3年')])])
        if 'national_shadow_nav' in d.views:
            ph=','.join('?'*len(selected));days=1096 if p.get('range')=='3y' else 366
            rows=d.q(f"SELECT * FROM national_shadow_nav WHERE portfolio IN({ph}) AND trade_date>cast(? as date)-?::INTEGER AND trade_date<=? ORDER BY trade_date",[*selected,etf_day,days,etf_day])
            peaks={};minimum_drawdown={}
            for row in rows:
                key=row['portfolio'];nav=row.get('nav')
                if nav is not None and nav>0:peaks[key]=max(peaks.get(key,nav),nav);row['drawdown']=nav/peaks[key]-1;minimum_drawdown[key]=min(minimum_drawdown.get(key,0),row['drawdown'])
            _group_line(s,'portfolio_nav','可见持仓影子组合净值',rows,'trade_date','portfolio','nav',quality='披露截止后首交易日收盘调仓；可见持仓满仓、无现金、停牌沿用末价，非真实国家队组合。')
            _group_line(s,'portfolio_drawdown','影子组合回撤',rows,'trade_date','portfolio','drawdown')
            summary, latest, contributions, monthly = 'local_shadow_summary','local_shadow_latest_components','local_shadow_contribution','national_shadow_monthly'
            if 'national_shadow_nav' in d.updated:
                _shadow_views(d,etf_day)
                summary, latest, contributions, monthly = 'observed_shadow_summary','observed_shadow_latest','observed_shadow_contribution','observed_shadow_monthly'
            months=d.q(f'SELECT * FROM {monthly} WHERE portfolio IN({ph}) AND month_start>cast(? as date)-?::INTEGER ORDER BY month_start',[*selected,etf_day,days])
            _group_line(s,'portfolio_month','月度超额',months,'month_start','portfolio','excess_return',kind='bar')
            component=p.get('component_portfolio',selected[0]);component=component if component in selected else selected[0]
            controls.append(_select('component_portfolio','成分/贡献组合',component,[(k,ENTITY[k]) for k in selected]))
            range_id='3y' if p.get('range')=='3y' else '1y'
            s.insert(0,_section('portfolio_kpi','影子组合区间指标',d.q(f'SELECT * FROM {summary} WHERE portfolio IN({ph}) AND range_id=?',[*selected,range_id]),'kpi',columns=['portfolio','start_date','end_date','portfolio_return','benchmark_return','excess_return','volatility','max_drawdown','component_count','rebalance_date','fresh_price_weight'],quality='回撤相对所选窗口累计峰值；区间收益复合每日组合收益。'))
            components=d.q(f'SELECT portfolio,segment_value FROM {latest} WHERE portfolio IN('+ph+')',[*selected]);concentration={}
            for row in components:concentration.setdefault(row['portfolio'],[]).append(row['segment_value'])
            for row in s[0]['rows']:
                values=sorted([v for v in concentration.get(row['portfolio'],[]) if v is not None],reverse=True);row['top5_weight']=sum(values[:5])/sum(values) if values and sum(values) else None;row['max_drawdown']=minimum_drawdown.get(row['portfolio'])
            s[0]['columns'].append({'key':'top5_weight','label':'前五集中度','format':'percent'})
            for direction,label in [('DESC','当日成分涨幅前十'),('ASC','当日成分跌幅前十')]:
                s.append(_section('portfolio_daily_'+direction,label,d.q(f'SELECT h.*,n.name FROM {latest} h LEFT JOIN local_names n USING(symbol) WHERE portfolio=? ORDER BY daily_return {direction} NULLS LAST LIMIT 10',[component]),columns=['symbol','name','portfolio','trade_date','weight','daily_return','valuation_price_date'],action='symbol'))
            s.append(_section('portfolio_contribution','区间成分贡献（每日算术贡献合计）',d.q(f'SELECT c.*,n.name FROM {contributions} c LEFT JOIN local_names n USING(symbol) WHERE portfolio=? AND range_id=? ORDER BY arithmetic_contribution DESC LIMIT 30',[component,range_id]),action='symbol',quality='每日成分价值变化 / 同日组合期初价值后加总，为算术贡献近似；不冒充几何归因。'))
        else:
            for key,title in [('portfolio_kpi','组合收益、回撤与波动'),('portfolio_nav','窄中及主体组合净值'),('portfolio_drawdown','组合回撤'),('portfolio_month','月度超额'),('portfolio_daily','当日成分涨跌'),('portfolio_contribution','区间贡献')]:s.append(_empty(key,title,'影子组合的披露截止、首交易日调仓、现金及停牌处理尚未形成完整冻结结果；持仓输入存在，不能据静态持仓替代实际组合收益。'))
        return
    if tab=='holdings':
        controls.extend([_select('metric','矩阵指标',p.get('metric','stock_count'),[('stock_count','只数'),('visible_market_value','可见市值'),('average_visible_float_pct','平均占流通比')]),_select('distribution_metric','分布指标',p.get('distribution_metric','market_value'),[('market_value','可见市值'),('stock_count','只数')]),_select('band','明细流通市值层',p.get('band','all'),[('all','全部'),('lt10','<10亿'),('10_50','10–50亿'),('50_200','50–200亿'),('200_1000','200–1000亿'),('ge1000','≥1000亿')]),_select('exclude_banks','银行处理',p.get('exclude_banks','0'),[('0','包含银行'),('1','剔除银行')]),_select('l1','行业',p.get('l1','all'),[('all','全部')]+[(x['value'],x['label']) for x in _meta()['industries']])])
        rows=d.q('SELECT * FROM national_entity_period WHERE report_period<=? AND last_day(report_period)=report_period AND month(report_period) IN(3,6,9,12) AND '+entity_filter+' ORDER BY report_period DESC',[period,*subjects])
        matrix_window=str(p.get('matrix_window','24'));matrix_window=matrix_window if matrix_window in ('8','24','all') else '24'
        controls.append(_select('matrix_window','主体矩阵报告期数',matrix_window,[('8','近8期'),('24','展开24期'),('all','全部报告期')]))
        if matrix_window!='all':
            selected_periods=set(sorted({r['report_period'] for r in rows},reverse=True)[:int(matrix_window)]);rows=[r for r in rows if r['report_period'] in selected_periods]
        metric=p.get('metric') if p.get('metric') in ('stock_count','visible_market_value','average_visible_float_pct') else 'stock_count'
        s.append(_section('matrix','主体 × 报告期',rows,'heatmap',columns=['entity_id','report_period',metric],x='report_period',series=[metric],action=_params_action('holdings',period='$report_period')))
        s.append(_section('industry','行业可见市值',d.q('SELECT * FROM local_national_scope_industry WHERE scope=? AND report_period=? ORDER BY market_value DESC',[scope,period]),'bar',x='industry_name',series=['market_value'],presentation='treemap',size_key='market_value',color_key='market_value',action=_params_action('holdings',l1='$industry_code')))
    if tab=='changes':
        controls.extend([_select('entity','主体',p.get('entity',scope),[(k,v) for k,v in ENTITY.items()]),_select('l1','行业',p.get('l1','all'),[('all','全部')]+[(x['value'],x['label']) for x in _meta()['industries']])])
        chosen=subjects if entity in ('medium','narrow') else [entity]
        if entity=='narrow':chosen=['hj_inv','hj_am','zj']
        w=['c.report_period=?','c.entity_id IN ('+','.join('?'*len(chosen))+')'];a=[period,*chosen]
        fw,fa=_filters(p,'h');w+=fw;a+=fa
        base='SELECT c.*,h.name,h.industry_name,h.industry_code,r.post_period_return,n.holding_pct current_holding_pct,old.holding_pct previous_holding_pct FROM national_changes c LEFT JOIN local_stock_context h USING(symbol) LEFT JOIN local_stock_period_returns r ON r.symbol=c.symbol AND r.report_period=c.report_period LEFT JOIN national_stock_holdings n ON n.symbol=c.symbol AND n.report_period=c.report_period AND n.entity_id=c.entity_id LEFT JOIN national_stock_holdings old ON old.symbol=c.symbol AND old.report_period=c.prev_period AND old.entity_id=c.entity_id WHERE '+' AND '.join(w)
        stats=d.q('SELECT change_status,count(*) subject_records,count(DISTINCT symbol) stock_count,sum(estimated_change_value) estimated_change_value FROM ('+base+') GROUP BY change_status',a)
        s.append(_section('changes_kpi','可见持仓变动（主体笔数 / 股票只数）',stats,'kpi'))
        for state,title in [('new_visible','新进可见'),('increase','增持'),('decrease','减持'),('exit_visible','退出可见')]:
            cols=['symbol','name','entity_id','industry_name','current_holding_pct','previous_holding_pct','current_shares','previous_adjusted_shares','share_change_ratio','estimated_change_value','post_period_return','change_status']
            s.append(_page(d,state,title,base+' AND c.change_status=?',a+[state],p,cols,'estimated_change_value',quality='复权股数同比超过±2%；变动额非真实成交额。'))
        hm=p.get('heatmap_metric','stock_count');hm=hm if hm in ('stock_count','estimated_change_value','current_value') else 'stock_count'
        controls.append(_select('heatmap_metric','变动热力指标',hm,[('stock_count','股票只数'),('estimated_change_value','估算变动额'),('current_value','可见市值')]))
        heat_state=p.get('heat_state','all');heat_state=heat_state if heat_state in ('all','new_visible','increase','decrease','exit_visible','unchanged') else 'all'
        controls.append(_select('heat_state','热力变动类型',heat_state,[('all','全部'),('new_visible','新进'),('increase','增持'),('decrease','减持'),('exit_visible','退出'),('unchanged','不变')]))
        heat_where='scope=? AND report_period=? AND entity_id IN ('+','.join('?'*len(chosen))+')';heat_args=[scope,period,*chosen]
        if p.get('l1') and p['l1']!='all':heat_where+=' AND industry_code=?';heat_args.append(p['l1'])
        if heat_state!='all':heat_where+=' AND change_status=?';heat_args.append(heat_state)
        heat=d.q('SELECT entity_id,industry_code,max(industry_name) industry_name,sum('+hm+') metric_value FROM local_national_change_heat WHERE '+heat_where+' GROUP BY entity_id,industry_code',heat_args)
        s.append(_section('changes_heat','主体×行业变动热力',heat,'heatmap',x='industry_name',series=[{'key':'metric_value','label':LABEL.get(hm,hm)}],y='entity_id',action=_params_action('changes',entity='$entity_id',l1='$industry_code')))
        topic=p.get('topic_metric','buyers');topic=topic if topic in ('buyers','sellers','market_value','estimated_change_value') else 'buyers'
        controls.append(_select('topic_metric','共振专题排序',topic,[('buyers','共同增持主体数'),('sellers','共同减持主体数'),('market_value','可见市值'),('estimated_change_value','估算变动额')]))
        resonance=d.q("SELECT symbol,max(name) AS name,count(DISTINCT entity_id) FILTER(WHERE change_status IN('new_visible','increase')) buyers,count(DISTINCT entity_id) FILTER(WHERE change_status IN('exit_visible','decrease')) sellers,sum(current_value) market_value,sum(estimated_change_value) estimated_change_value FROM ("+base+") GROUP BY symbol HAVING buyers>=2 OR sellers>=2 ORDER BY "+topic+' DESC NULLS LAST LIMIT 30',a)
        s.append(_section('resonance','筛选后多主体共振',resonance,action='symbol',quality='至少两个所选主体同向；单主体筛选无多主体共振时为空。'))
        return
    if tab=='entity':
        controls.append(_select('entity','主体详情',entity,[(k,v) for k,v in ENTITY.items()]))
        if entity in ('medium','narrow'):
            scope_kpi=d.q("SELECT p.*,p.visible_market_value-old.visible_market_value period_value_change,p.stock_count-old.stock_count stock_count_change,c.new_count,c.exit_count,c.increase_count,c.decrease_count FROM national_scope_period p LEFT JOIN national_scope_period old ON old.scope=p.scope AND old.report_period=last_day(p.report_period-INTERVAL '3 months') LEFT JOIN local_national_change_counts c ON c.scope=p.scope AND c.report_period=p.report_period WHERE p.scope=? AND p.report_period=?",[entity,period])
            s.append(_section('entity_kpi','口径持仓指标',scope_kpi,'kpi',columns=['scope','report_period','stock_count','visible_market_value','period_value_change','stock_count_change','new_count','exit_count','increase_count','decrease_count']))
            s.append(_section('entity_history','持仓历史',d.q('SELECT * FROM national_scope_period WHERE scope=? AND report_period<=? ORDER BY report_period',[entity,period]),'line',x='report_period',series=['visible_market_value','stock_count']))
            s.append(_section('entity_industry','行业构成',d.q('SELECT * FROM local_national_scope_industry WHERE scope=? AND report_period=? ORDER BY market_value DESC',[entity,period]),'bar',x='industry_name',series=['market_value']))
            leading=s[-1]['rows'][0]['industry_name'] if s[-1]['rows'] else None
            members=['hj_inv','hj_am','zj'] if entity=='narrow' else [k for k in ENTITY if k not in ('medium','narrow')]
            accounts=d.q('SELECT count(DISTINCT holder_name) account_count FROM national_accounts WHERE record_date=? AND entity_id IN('+','.join('?'*len(members))+')',[period,*members])
            vals=d.q('SELECT market_value FROM local_national_scope_stock WHERE scope=? AND report_period=? ORDER BY market_value DESC NULLS LAST',[entity,period]);values=[r['market_value'] for r in vals if r['market_value'] is not None]
            if s[0]['rows']:
                s[0]['rows'][0].update(leading_industry=leading,account_count=accounts[0]['account_count'],top10_weight=sum(values[:10])/sum(values) if values and sum(values) else None);s[0]['columns'] += [{'key':'leading_industry','label':'首位行业'},{'key':'account_count','label':'去重账户数'},{'key':'top10_weight','label':'前十集中度','format':'percent'}]
            scope=entity
        else:
            s.append(_section('entity_kpi',ENTITY[entity]+'指标',d.q("SELECT n.*,n.visible_market_value-old.visible_market_value period_value_change,n.stock_count-old.stock_count stock_count_change,c.new_count,c.exit_count,c.increase_count,c.decrease_count FROM national_entity_period n LEFT JOIN national_entity_period old ON old.entity_id=n.entity_id AND old.report_period=last_day(n.report_period-INTERVAL '3 months') LEFT JOIN local_national_entity_changes c ON c.entity_id=n.entity_id AND c.report_period=n.report_period WHERE n.entity_id=? AND n.report_period=?",[entity,period]),'kpi',columns=['report_period','stock_count','visible_market_value','account_count','top10_weight','period_value_change','stock_count_change','new_count','exit_count','increase_count','decrease_count','coverage_ratio']))
            leading=d.q('SELECT industry_name FROM national_industry WHERE entity_id=? AND report_period=? ORDER BY market_value DESC LIMIT 1',[entity,period])
            accounts=d.q('SELECT count(DISTINCT holder_name) account_count FROM national_accounts WHERE entity_id=? AND record_date=?',[entity,period])
            if s[-1]['rows']:
                s[-1]['rows'][0]['leading_industry']=leading[0]['industry_name'] if leading else None;s[-1]['rows'][0]['account_count']=accounts[0]['account_count'];s[-1]['columns'].append({'key':'leading_industry','label':'首位行业'})
            s.append(_section('entity_history','主体持仓历史',d.q('SELECT * FROM national_entity_period WHERE entity_id=? AND report_period<=? AND last_day(report_period)=report_period ORDER BY report_period',[entity,period]),'line',x='report_period',series=['visible_market_value','stock_count']))
            s.append(_section('entity_industry','主体行业构成',d.q('SELECT * FROM national_industry WHERE entity_id=? AND report_period=? ORDER BY market_value DESC',[entity,period]),'bar',x='industry_name',series=['market_value'],action='industry_code'))
            fw,fa=_filters(p,'h')
            base='SELECT h.*,n.name,c.change_status,c.share_change_ratio,c.estimated_change_value,r.post_period_return FROM national_stock_holdings h LEFT JOIN local_names n USING(symbol) LEFT JOIN national_changes c ON c.entity_id=h.entity_id AND c.report_period=h.report_period AND c.symbol=h.symbol LEFT JOIN local_stock_period_returns r ON r.symbol=h.symbol AND r.report_period=h.report_period WHERE h.entity_id=? AND h.report_period=?'+(' AND '+' AND '.join(fw) if fw else '')
            # Search uses the joined name; substitute only an internal identifier.
            base=base.replace('h.name','n.name')
            s.append(_page(d,'entity_holdings','主体当期持仓',base,[entity,period,*fa],p,['symbol','name','industry_name','shares','holding_pct','market_value','account_count','change_status','share_change_ratio','estimated_change_value','post_period_return'],'market_value'))
            return
    if tab in ('holdings','entity','stock'):
        w=['h.scope=?','h.report_period=?'];args=[scope,period];fw,fa=_filters(p,'h');w+=fw;args+=fa
        if str(p.get('exclude_banks','0')) in ('1','true'):w.append("coalesce(h.industry_name,'') NOT LIKE '%银行%'")
        state=p.get('filter','all')
        status_map={'new':'new_count','increase':'increase_count','decrease':'decrease_count','exit':'exit_count'}
        if state in status_map:w.append('coalesce(c.'+status_map[state]+',0)>0')
        elif state in ('multi','two'):w.append('h.subject_count>=2')
        elif state=='unchanged':w.append("coalesce(c.new_count,0)+coalesce(c.increase_count,0)+coalesce(c.decrease_count,0)+coalesce(c.exit_count,0)=0 AND EXISTS(SELECT 1 FROM national_changes u WHERE u.symbol=h.symbol AND u.report_period=h.report_period AND u.change_status='unchanged')")
        cap=str(p.get('band','all'));limits={'lt10':(0,1e9),'10_50':(1e9,5e9),'50_200':(5e9,2e10),'200_1000':(2e10,1e11),'ge1000':(1e11,1e30),'1000+':(1e11,1e30)}
        if cap in limits:w.append('h.float_market_value>=? AND h.float_market_value<?');args+=list(limits[cap])
        stock_source='local_national_stock_universe' if tab=='stock' else 'local_national_scope_stock'
        base='SELECT h.*,c.new_count,c.increase_count,c.decrease_count,c.exit_count,c.estimated_change_value,r.post_period_return FROM '+stock_source+' h LEFT JOIN local_national_stock_changes c USING(scope,report_period,symbol) LEFT JOIN local_stock_period_returns r USING(symbol,report_period) WHERE '+' AND '.join(w)
        s.append(_page(d,'stock_list','可见持仓明细',base,args,p,['symbol','name','industry_name','shares','holding_pct','market_value','subject_count','subjects','new_count','increase_count','decrease_count','exit_count','post_period_return'],'market_value'))
        if tab=='holdings':
            s.append(_section('concentration','当前筛选集中度',d.q('WITH x AS (SELECT *,row_number() OVER(ORDER BY market_value DESC) r FROM ('+base+')) SELECT count(*) stock_count,sum(market_value) market_value,sum(market_value) FILTER(WHERE r<=10)/nullif(sum(market_value),0) top10_weight FROM x',args),'kpi'))
            dist=p.get('distribution_metric','market_value');dist=dist if dist in ('market_value','stock_count') else 'market_value'
            s.append(_section('market_board','板块分布',d.q('SELECT market_board,sum(market_value) market_value,count(*) stock_count FROM ('+base+') GROUP BY market_board',args),'bar',x='market_board',series=[dist],presentation='pie'))
            bands=d.q("SELECT CASE WHEN float_market_value<1e9 THEN 'lt10' WHEN float_market_value<5e9 THEN '10_50' WHEN float_market_value<2e10 THEN '50_200' WHEN float_market_value<1e11 THEN '200_1000' WHEN float_market_value>=1e11 THEN 'ge1000' ELSE 'unknown' END band,sum(market_value) market_value,count(*) stock_count FROM ("+base+") GROUP BY band",args)
            s.append(_section('market_cap_bands','流通市值分层',bands,'bar',x='band',series=[dist],action=_params_action('holdings',band='$band')))
            if p.get('l1') and p['l1']!='all':s.append(_section('industry_top20','所选行业前20股',d.q('SELECT symbol,name,subjects,market_value,holding_pct FROM ('+base+') ORDER BY market_value DESC LIMIT 20',args),action='symbol'))
        if tab=='stock':
            controls.extend([_text('code','下钻证券',p.get('code','')),_select('filter','持仓状态',state,[('all','全部'),('multi','至少两主体'),('new','新进'),('increase','增持'),('decrease','减持'),('exit','退出'),('unchanged','不变')]),_select('l1','行业',p.get('l1','all'),[('all','全部')]+[(x['value'],x['label']) for x in _meta()['industries']]),_select('band','流通市值档',cap,[('all','全部'),('lt10','<10亿'),('10_50','10–50亿'),('50_200','50–200亿'),('200_1000','200–1000亿'),('ge1000','≥1000亿')])])
            code=_symbol(p.get('code'))
            if code:
                s.append(_section('stock_structure','该股主体持股结构',d.q('SELECT * FROM national_stock_holdings WHERE symbol=? AND report_period=? AND '+entity_filter,[code,period,*subjects]),'bar',columns=['entity_id','shares','holding_pct','market_value','account_count'],x='entity_id',series=['holding_pct']))
                s.append(_section('top_holders','所选相邻两季十大流通股东',_float_holder_detail(d,code,period,asof),
                    columns=['record_date','announce_date','holder_rank','holder_name','holding_shares','holding_pct',
                             {'key':'holding_change','label':'相邻期披露股数差（未复权）'}, {'key':'whole_list','label':'分榜完整'}],
                    quality='仅季末流通股东和观察日已公告记录。完整名单的同名账户才显示股数差；未处理送转，缺榜不计为退出。'))
                s.append(_section('stock_history','国家队可见持仓历史与进出标记',d.q('SELECT * FROM national_changes WHERE symbol=? AND '+entity_filter+' ORDER BY report_period',[code,*subjects]),'line',columns=['report_period','entity_id','current_shares','change_status'],x='report_period',series=['current_shares']))
                _price(d,code,s,national=True)
            else:s.append(_empty('stock_detail','个股下钻','从持仓明细选择证券，或输入六位股票代码。'))


def _etf(d,p,s,controls,national=False):
    category='broad' if national else str(p.get('category','all'))
    if category not in CATEGORY:category='all'
    win=_int(p.get('win'),60 if national else 20,1,250);win=win if win in (1,5,20,60,100,120,250) else 60
    index=str(p.get('index','all'));code=_symbol(p.get('etf'));asof=p.get('daily_observation',_period(p.get('date'),FREEZE))
    controls.extend([_select('win','ETF曲线交易日数',win,[(1,'1日'),(5,'5日'),(20,'20日'),(60,'60日'),(100,'100日'),(120,'120日'),(250,'250日')]),_select('index','跟踪指数',index,[('all','池合计')]+[(x['value'],x['label']) for x in _meta()['etf_indexes']]),_text('etf','ETF下钻代码',code)])
    if not national:
        controls.append(_select('category','ETF分类',category,list(CATEGORY.items())))
        stock_total=d.q("SELECT max(trade_date) trade_date,count(*) etf_count,count(net_subscription) valid_flow_count,sum(net_subscription) observed_net_subscription,CASE WHEN count(net_subscription)=count(*) THEN sum(net_subscription) END complete_net_subscription,sum(fund_size) observed_size FROM etf_daily_flow WHERE category IN ('broad','industry_theme','strategy_style') AND trade_date=(SELECT max(trade_date) FROM etf_daily_flow WHERE trade_date<=?)",[asof])
        s.append(_section('stock_etf_total','境内股票类ETF合计（独立全市场口径）',stock_total,'kpi',columns=['trade_date','etf_count','valid_flow_count','observed_net_subscription','complete_net_subscription','observed_size'],quality='仅宽基+行业主题+策略风格，排除跨境/其他/未分类；观察合计与完整合计区分。'))
    w=['trade_date=(SELECT max(trade_date) FROM etf_daily_flow WHERE trade_date<=?)'];a=[asof]
    if category!='all':w.append('category=?');a.append(category)
    if index!='all':w.append('reported_index_code=?');a.append(index)
    if p.get('q'):w.append('(contains(symbol,?) OR contains(name,?))');a.extend([str(p['q'])]*2)
    base='SELECT * FROM etf_daily_flow WHERE '+' AND '.join(w)
    if national:
        groups=d.q('SELECT reported_index_code,sum(fund_size) fund_size,sum(shares_outstanding) shares_outstanding,sum(net_subscription) net_subscription,sum(flow_5d) flow_5d,sum(flow_20d) flow_20d,count(*) etf_count FROM ('+base+') GROUP BY reported_index_code ORDER BY fund_size DESC',a)
        s.append(_section('etf_groups','按指数分组（点击展开）',groups,action=_params_action('etf',index='$reported_index_code',expanded='$reported_index_code'),actions=[dict(label='全部展开',**_params_action('etf',index='all',expanded='all')),dict(label='全部收起',**_params_action('etf',index='all',expanded='none'))]))
        expanded=str(p.get('expanded','all'))
        controls.append(_select('expanded','池明细展开',expanded,[('all','全部展开'),('none','全部收起')]+[(x['value'],x['label']) for x in _meta()['etf_indexes']]))
        if expanded=='none':poolbase=base+' AND false';poolargs=a
        elif expanded!='all':poolbase=base+' AND reported_index_code=?';poolargs=[*a,expanded]
        else:poolbase,poolargs=base,a
    else:poolbase,poolargs=base,a
    flow='net_subscription' if win==1 else f'flow_{win}d'
    cols=['symbol','name','category','reported_index_code','trade_date','shares_outstanding','fund_size','net_subscription','flow_5d','flow_20d','flow_60d','flow_120d','flow_250d','consecutive_days','turnover','volume_ratio','nav_used','quality_status']
    if 'fund_change_coverage' in d.views:cols.insert(cols.index('flow_120d'),'flow_100d')
    s.append(_page(d,'etf_pool','宽基ETF池明细' if national else 'ETF资金明细',poolbase,poolargs,p,cols,'fund_size',action=_params_action('etf',etf='$symbol'),quality='连续交易观测份额差×NAV，NAV回退留来源；机构持有不等于国家队。'))
    kpi=d.q('SELECT max(trade_date) trade_date,count(*) etf_count,sum(fund_size) fund_size,sum(net_subscription) net_subscription,sum(flow_5d) flow_5d,sum(flow_20d) flow_20d,count(*) FILTER(WHERE volume_ratio>=2.5) unusual_volume_count FROM ('+base+')',a)
    s.append(_section('etf_kpi','所选ETF池规模与申赎',kpi,'kpi'))
    if index!='all':
        history=d.q('SELECT * FROM etf_index_flow WHERE reported_index_code=? AND trade_date<=? ORDER BY trade_date DESC LIMIT ?',[index,asof,win]);history.reverse();group='reported_index_code'
        s.append(_section('etf_index_top4','所选指数规模前4只',d.q('SELECT symbol,name,fund_size,net_subscription,flow_5d,flow_20d FROM ('+base+') ORDER BY fund_size DESC LIMIT 4',a),action=_params_action('etf',etf='$symbol')))
    else:
        catwhere='category=? AND ' if category!='all' else '';cata=[category] if category!='all' else []
        history=d.q('SELECT * FROM etf_category_flow WHERE '+catwhere+'trade_date IN (SELECT DISTINCT trade_date FROM etf_category_flow WHERE trade_date<=? ORDER BY trade_date DESC LIMIT ?) ORDER BY trade_date,category',[*cata,asof,win]);group='category'
    if national:
        heat=d.q('SELECT * FROM etf_index_flow WHERE trade_date IN(SELECT DISTINCT trade_date FROM etf_index_flow WHERE trade_date<=? ORDER BY trade_date DESC LIMIT ?)'+(' AND reported_index_code=?' if index!='all' else '')+' ORDER BY trade_date',[asof,win,index] if index!='all' else [asof,win])
        s.append(_section('etf_heat','指数×日净申购热力',heat,'heatmap',x='trade_date',y='reported_index_code',series=['net_subscription'],action=_params_action('etf',index='$reported_index_code')))
    else:s.append(_section('etf_heat','分类×日净申购热力',history,'heatmap',x='trade_date',y=group,series=['net_subscription'],action=_params_action('etf',category='$category')))
    mode=str(p.get('mode','cumulative' if national else 'daily'));controls.append(_select('mode','每日/累计',mode,[('daily','每日'),('cumulative','累计（窗口起点归零）')]))
    totals={}
    for row in history:
        g=row[group]
        if row.get('net_subscription') is not None:totals[g]=totals.get(g,0)+row['net_subscription'];row['cumulative_flow']=totals[g]
        else:row['cumulative_flow']=None
    _group_line(s,'etf_trend','ETF观察窗累计申赎' if mode=='cumulative' else 'ETF每日净申购',history,'trade_date',group,'cumulative_flow' if mode=='cumulative' else 'net_subscription',quality='累计从本窗口第一条观测起加总；缺失净值/份额不填零，覆盖率另列。')
    if code:
        rows=d.q('SELECT * FROM etf_daily_flow WHERE symbol=? AND trade_date<=? ORDER BY trade_date DESC LIMIT ?',[code,asof,max(20,win)]);rows.reverse()
        s.append(_section('etf_detail','所选ETF日度申赎与规模',rows,'line',columns=['trade_date','symbol','name','net_subscription','flow_5d','flow_20d','flow_60d','fund_size','shares_outstanding','consecutive_days','volume_ratio'],x='trade_date',series=['net_subscription']))
    weekly_where='week_start<=?';weekly_args=[asof]
    if code:weekly_where+=' AND symbol=?';weekly_args.append(code)
    elif category!='all':weekly_where+=' AND category=?';weekly_args.append(category)
    weekly=d.q('SELECT symbol,name,category,week_start,week_net_subscription net_subscription,week_valid_days valid_days,week_observed_days observed_days,trade_date source_as_of FROM local_etf_period_asof WHERE '+weekly_where+' AND trade_date<=? AND week_start>=cast(? as date)-112 AND symbol IN(SELECT symbol FROM ('+base+') ORDER BY fund_size DESC LIMIT 50) QUALIFY row_number() OVER(PARTITION BY symbol,week_start ORDER BY trade_date DESC)=1 ORDER BY week_start,symbol',[*weekly_args,asof,asof,*a])
    s.append(_section('etf_weekly','ETF×周净申购热力',weekly,'heatmap',x='week_start',y='symbol',series=['net_subscription'],action=_params_action('etf',etf='$symbol',week='$week_start'),quality='按周一分组；点击ETF周单元下钻到基金。'))
    if p.get('week') and code:
        s.append(_section('etf_week_detail','选中周的每日申赎',d.q("SELECT trade_date,symbol,net_subscription,nav_used,nav_source_date FROM etf_daily_flow WHERE symbol=? AND trade_date>=cast(? as date) AND trade_date<cast(? as date)+INTERVAL '7 days' AND trade_date<=? ORDER BY trade_date",[code,p['week'],p['week'],asof]),action='symbol'))
    if national:
        thr=_int(p.get('thr'),200,100,300);thr=thr if thr in (100,200,300) else 200
        controls.append(_select('thr','托底阈值',thr,[(100,'100亿元'),(200,'200亿元'),(300,'300亿元')]))
        event_view='national_etf_events'
        if event_view in d.updated:
            anchor=date.fromisoformat(asof)
            d.c.execute(f"""CREATE TEMP VIEW visible_national_etf_events AS SELECT * REPLACE (
                CASE WHEN next_flow_available_date<=DATE '{anchor}' OR trade_date<DATE '{FREEZE}'
                  THEN next_trading_net_subscription END AS next_trading_net_subscription,
                CASE WHEN support_available_date<=DATE '{anchor}' OR trade_date<DATE '{FREEZE}'
                  THEN support_event END AS support_event,
                CASE WHEN balance_available_date<=DATE '{anchor}' OR trade_date<DATE '{FREEZE}'
                  THEN balance_event END AS balance_event,
                CASE WHEN net_flow_available_date<=DATE '{anchor}' OR trade_date<DATE '{FREEZE}'
                  THEN net_subscription END AS net_subscription) FROM national_etf_events""")
            event_view='visible_national_etf_events'
        events=d.q(f'SELECT * FROM {event_view} WHERE threshold_yi=? AND trade_date>=cast(? as date)-1096 AND trade_date<=? AND (support_event OR balance_event) ORDER BY trade_date DESC',[thr,asof,asof])
        s.append(_section('etf_event_kpi','近三年候选托底/平准日数',[{'threshold_yi':thr,'support_days':sum(bool(r['support_event']) for r in events),'balance_days':sum(bool(r['balance_event']) for r in events)}],'kpi'))
        s.append(_section('etf_events','托底/平准候选事件',events,columns=['trade_date','threshold_yi','benchmark_return','volume_ratio','next_trading_net_subscription','net_subscription','support_event','balance_event'],quality='按公开条件复算；当前ETF分类近似，不宣称国家队真实操作。'))
        chosen=p.get('etfs');chosen=chosen if isinstance(chosen,list) else str(chosen or '').split(',');chosen=[_symbol(v) for v in chosen if _symbol(v)][:4]
        if not chosen:chosen=[r['symbol'] for r in d.q('SELECT symbol FROM ('+base+') ORDER BY fund_size DESC LIMIT 4',a)]
        choices=d.q('SELECT symbol,name FROM ('+base+') ORDER BY fund_size DESC',a)
        controls.append(_multi('etfs','季度申赎比较（最多4只）',chosen,[(r['symbol'],r['name']) for r in choices]))
        if chosen:
            ph=','.join('?'*len(chosen));quarter=d.q('SELECT symbol,name,quarter_start,quarter_net_subscription net_subscription,quarter_valid_days valid_days,quarter_observed_days observed_days,trade_date source_as_of FROM local_etf_period_asof WHERE symbol IN('+ph+') AND trade_date<=? AND quarter_start>=cast(? as date)-1096 QUALIFY row_number() OVER(PARTITION BY symbol,quarter_start ORDER BY trade_date DESC)=1 ORDER BY quarter_start',[*chosen,asof,asof])
            _group_line(s,'etf_quarter_compare','四基金季度净申购比较',quarter,'quarter_start','symbol','net_subscription')
        s.append(_empty('institution_holdings','ETF机构持有份额及比例','正式持有人结构未覆盖当前宽基ETF池，不把普通基金结构或机构比例视为国家队持有。'))



def _price(d,code,s,years=3,national=False,holding=False,days=None,asof=FREEZE):
    if 'local_daily_prices' in d.updated and d.observation_date:
        asof = min(asof if asof!=FREEZE else d.observation_date,d.observation_date)
    if 'local_daily_prices' not in d.views:
        s.append(_empty('price','复权价格','该冻结版本缺实体日价格产物，不回退当前湖。'));return
    limit_clause=' ORDER BY trade_date DESC LIMIT '+str(int(days)) if days else ''
    raw=d.q('SELECT * FROM local_daily_prices WHERE symbol=? AND trade_date<=? AND trade_date>=cast(? as date)-?::INTEGER'+limit_clause,[code,asof,asof,366*years])
    raw.sort(key=lambda r:r['trade_date']);reference=next((r['factor'] for r in reversed(raw) if r.get('factor') and r['factor']>0),None)
    grouped={}
    for r in raw:
        dt=date.fromisoformat(r['trade_date']);anchor=dt if days else dt.fromordinal(dt.toordinal()-dt.weekday());key=anchor.isoformat();factor=r.get('factor')/reference if reference and r.get('factor') else None
        values={k:r.get(k)*factor if r.get(k) is not None and factor is not None else None for k in ('open','high','low','close')}
        if key not in grouped:grouped[key]={'week_start':key,**values,'adjusted_close':values['close'],'price_date':r['trade_date']}
        else:
            z=grouped[key];z['high']=max(v for v in (z['high'],values['high']) if v is not None) if any(v is not None for v in (z['high'],values['high'])) else None;z['low']=min(v for v in (z['low'],values['low']) if v is not None) if any(v is not None for v in (z['low'],values['low'])) else None;z['close']=values['close'];z['adjusted_close']=values['close'];z['price_date']=r['trade_date']
    markers=[]
    if national:
        events=d.q("SELECT c.report_period,c.entity_id,c.change_status,max(a.announce_date) announce_date FROM national_changes c LEFT JOIN national_accounts a ON a.symbol=c.symbol AND a.record_date=c.report_period AND a.entity_id=c.entity_id WHERE c.symbol=? AND c.change_status IN('new_visible','exit_visible') GROUP BY c.report_period,c.entity_id,c.change_status",[code])
        for r in events:
            anchor=r['announce_date'] or r['report_period'];dt=date.fromisoformat(anchor);markers.append({'x':dt.fromordinal(dt.toordinal()-dt.weekday()).isoformat(),'label':ENTITY.get(r['entity_id'],r['entity_id'])+('新进' if r['change_status']=='new_visible' else '退出可见'),'kind':r['change_status']})
    if holding:
        holdings=d.q('SELECT report_period,shares FROM fund_stock_crowding WHERE symbol=? ORDER BY report_period',[code])
        for r in holdings:
            dt=date.fromisoformat(r['report_period']);key=dt.fromordinal(dt.toordinal()-dt.weekday()).isoformat()
            if key in grouped:grouped[key]['public_holding_shares']=r['shares']
    chart=_section('price',str(days)+'交易日前复权日线' if days else '近'+str(years)+'年前复权周线',list(grouped.values()),'line',columns=['week_start','open','high','low','close','adjusted_close']+(['public_holding_shares'] if holding else []),x='week_start',series=['adjusted_close'],presentation='candlestick',ohlc={'open':'open','high':'high','low':'low','close':'close'},markers=markers,quality='正式日线×后复权因子/本区间最新因子，当前价格锚定；无分钟级尾盘数据。持仓柱是报告期末披露统计，非当日已知交易。')
    chart['overlays']=[]
    if holding:chart['overlays']=[{'key':'public_holding_shares','label':'报告期公募持股','type':'bar','axis':'right'}]
    s.append(chart)


def _fund(d,tab,p,s,controls):
    period=_period(p.get('period'),'2026Q2');band=p.get('band','all');corp=_company(p.get('corp'))
    code=_symbol(p.get('code'));l1=p.get('l1') or p.get('industry') or 'all'
    controls.extend([_text('corp','基金公司 / 名称前缀',p.get('corp','')),_text('code','个股代码',code),_select('ids','比较篮（最多6家公司）',','.join(_ids(p.get('ids'))),[(x,x) for x in _meta()['company_ids']]),_select('l1','申万行业',l1,[('all','全部')]+[(x['value'],x['label']) for x in _meta()['industries']])])
    if tab!='overview' and 'fund_change_coverage' in d.views:
        s.append(_section('change_scope_note','静态持仓与跨期变动覆盖',d.q('SELECT * FROM fund_change_coverage WHERE report_period=?',[period]),columns=['qualified_company_count','company_count','company_coverage_ratio','qualified_company_fund_count','qualified_comparable_fund_count','share_class_count','fund_coverage_ratio','amount_coverage_ratio'],quality='跨期方向和金额仅为合格公司样本。金额覆盖是合格公司静态市值覆盖；未披露与未过门槛均非零变动。'))
    w=['report_period=?'];a=[period]
    if band in ('top','large','medium','small'):w.append('size_band=?');a.append(band)
    if p.get('q'):w.append('contains(company_id,?)');a.append(str(p['q']))
    company_projection=('* REPLACE (NULL::DOUBLE AS net_assets,NULL::DOUBLE AS holding_nav_ratio,'
                        'NULL::DOUBLE AS stock_allocation,NULL::DOUBLE AS net_asset_coverage)'
                        if 'phase3_fund_scale' in d.views else '*')
    rankbase='SELECT '+company_projection+' FROM company_period WHERE '+' AND '.join(w)
    if tab in ('overview','ranking'):
        s.append(_section('band_totals','公司规模分档',d.q('SELECT * FROM local_fund_band WHERE report_period=?'+(' AND size_band=?' if band!='all' else ''),[period,band] if band!='all' else [period]),'bar',x='size_band',series=['market_value','estimated_change_value']))
        if tab=='ranking':
            band_rows=d.q('SELECT * FROM local_fund_band WHERE report_period=?'+(' AND size_band=?' if band!='all' else ''),[period,band] if band!='all' else [period])
            s.append(_section('band_summary','四档公司类型汇总',band_rows,columns=['size_band','company_count','market_value','fund_count','new_count','increase_count','decrease_count','exit_count','estimated_change_value']))
            cols=['company_id','visible_market_value','period_value_change','holding_nav_ratio','stock_count','fund_count','new_count','increase_count','decrease_count','exit_count','estimated_net_change','stock_turnover','estimated_amount_turnover','top10_weight','hhi','mean_visible_halfyears','return_1y','return_2y','weighted_return_1y','excess_1y','implied_return','size_band','change_comparable_coverage_ratio','net_asset_coverage']
            s.append(_page(d,'ranking','公司排行',rankbase,a,p,cols,'visible_market_value',action='company_id'))
            s.append(_section('concentration_scatter','集中度 × 连续持有期',d.q('SELECT company_id,size_band,top10_weight,mean_visible_halfyears,visible_market_value FROM ('+rankbase+')',a),'scatter',x='top10_weight',series=['mean_visible_halfyears'],action='company_id',quality='持有期为连续可见完整半年报/年报期数平均，不等于真实交易持有期限。'))
            returns_view=_company_windows(d,p.get('daily_observation',FREEZE))
            s.append(_section('return_turnover','真实净值收益 × 只数换手',d.q('SELECT c.company_id,c.size_band,c.visible_market_value,c.stock_turnover,r.equal_weight_return,r.scale_weight_return FROM ('+rankbase+') c LEFT JOIN '+returns_view+' r ON c.company_id=r.company_id AND r.calendar_days=365',a),'scatter',x='stock_turnover',series=['equal_weight_return'],action='company_id',quality='主动权益基金池为当前分类近似；真实复权NAV收益，规模加权仅使用前一交易日已公开的正规模。'))
        else:
            _phase3_company_scale(d,period,s)
            s.append(_section('market_kpi','公募可见静态快照 · 变动全市场口径未覆盖',d.q('SELECT * FROM local_fund_market_history WHERE report_period=?',[period]),'kpi',columns=['company_count','market_value','stock_count','share_class_count','new_count','increase_count','decrease_count','exit_count','estimated_change_value'],quality='静态持仓分母与跨期变动分母独立；部分公司合格时全市场变动保留为空。'))
            if 'fund_change_coverage' in d.views:
                s.append(_section('sample_changes','合格公司跨期样本 · 不代表全市场',d.q('SELECT * FROM fund_change_coverage WHERE report_period=?',[period]),columns=['sample_new_count','sample_increase_count','sample_decrease_count','sample_exit_count','sample_estimated_change_value','qualified_company_count','company_count','company_coverage_ratio','qualified_company_fund_count','qualified_comparable_fund_count','share_class_count','fund_coverage_ratio','qualified_company_market_value','market_value','amount_coverage_ratio'],quality='金额覆盖为合格公司的静态可见市值覆盖，并非同基金可比持仓金额覆盖；未覆盖公司不作零变动。'))
            s.append(_section('history','公募池内历史趋势',d.q('SELECT * FROM local_fund_market_history WHERE report_period<=? ORDER BY report_period',[period]),'line',x='report_period',series=['market_value','company_count','stock_count']))
            s.append(_section('industry','行业配置与超配',d.q('SELECT * FROM local_fund_industry_market WHERE report_period=? ORDER BY market_value DESC',[period]),'bar',x='industry_name',series=['weight','weight_change','estimated_net_change'],action=_params_action('industries',l1='$industry_code'),quality='全A分母取有价格和流通股本的期间股票样本；非原站逐键对账结果。'))
            s.append(_section('crowding','拥挤度分布',d.q('SELECT symbol,market_value,company_count,float_ratio,crowding_score FROM fund_stock_crowding WHERE report_period=? ORDER BY market_value DESC LIMIT 500',[period]),'scatter',x='company_count',series=['float_ratio'],action='symbol'))
            _consensus(d,period,s)
            _quarter_sections(d,period,None,s)
        return
    if tab=='corp':
        if not corp:s.append(_empty('choose_company','公司详情','选择公司排行中的公司，或输入能够唯一匹配的公司名称前缀。'));return
        s.append(_section('corp_kpi',corp+' · 可见持仓指标',d.q('SELECT '+company_projection+' FROM company_period WHERE company_id=? AND report_period=?',[corp,period]),'kpi',columns=['visible_market_value','holding_nav_ratio','stock_allocation','stock_count','return_1y','excess_1y','fund_count','net_asset_coverage'],quality='公司全量净资产口径未证明；样本分母在下方独列。' if 'phase3_fund_scale' in d.views else None))
        _phase3_company_scale(d,period,s,corp)
        s.append(_section('corp_industry','公司行业配置',d.q('SELECT c.*,m.weight market_fund_weight,m.market_weight,c.weight-m.weight overweight_funds,c.weight-m.market_weight overweight FROM company_industry c LEFT JOIN local_fund_industry_market m USING(report_period,industry_code) WHERE c.company_id=? AND c.report_period=? ORDER BY c.market_value DESC',[corp,period]),'bar',x='industry_name',series=['weight','overweight'],action='industry_code'))
        industry_rows=d.q('SELECT c.*,m.weight market_fund_weight,m.market_weight,c.weight-m.weight overweight_funds,c.weight-m.market_weight overweight FROM company_industry c LEFT JOIN local_fund_industry_market m USING(report_period,industry_code) WHERE c.company_id=? AND c.report_period=? ORDER BY c.market_value DESC',[corp,period])
        s.append(_section('corp_industry_table','公司行业配置明细',industry_rows,columns=['industry_name','weight','market_fund_weight','market_weight','overweight','overweight_funds'],action=_params_action('industries',l1='$industry_code')))
        top=[r['industry_code'] for r in industry_rows[:10]];history=d.q('SELECT * FROM company_industry WHERE company_id=? AND report_period<=? ORDER BY report_period',[corp,period]);stack={}
        for row in history:
            label=row['industry_name'] if row['industry_code'] in top else '其他';key=(row['report_period'],label);stack.setdefault(key,{'report_period':row['report_period'],'industry_group':label,'weight':0});stack[key]['weight']+=row['weight'] or 0
        _group_line(s,'corp_industry_history','前十行业与其他 · 历史配置',list(stack.values()),'report_period','industry_group','weight',kind='bar');s[-1].update(presentation='stacked',stack=True)
        s.append(_section('corp_overlap','重叠公司前十',d.q("SELECT CASE WHEN company_a=? THEN company_b ELSE company_a END company_id,jaccard,weight_overlap FROM company_overlap WHERE report_period=? AND (company_a=? OR company_b=?) ORDER BY weight_overlap DESC LIMIT 10",[corp,period,corp,corp]),action='company_id',quality='权重重叠=sum(min(双方归一化可见持仓权重))；原站后台公式未独立核验。'))
        fw,fa=_filters(p,'h');
        base='SELECT h.* FROM local_company_holding_universe h WHERE h.company_id=? AND h.report_period=?'+(' AND '+' AND '.join(fw) if fw else '')
        state=p.get('filter','all')
        if state in ('new_visible','increase','decrease','unchanged','exit_visible'):base+=' AND h.change_status=?';fa.append(state)
        controls.append(_select('filter','持仓状态',state,[('all','全部'),('new_visible','新进'),('increase','增持'),('decrease','减持'),('exit_visible','退出可见'),('unchanged','不变')]))
        cols=['symbol','name','industry_name','market_value','shares','weight','holding_rank','fund_count','change_status','share_change_ratio','estimated_change_value','influence_pp','consecutive_halfyears','post_period_return']
        s.append(_page(d,'corp_holdings','公司持仓明细',base,[corp,period,*fa],p,cols,'market_value'))
        for direction,title in [('DESC','估算增持变动前列'),('ASC','估算减持变动前列')]:
            s.append(_section('corp_changes_'+direction,title,d.q('SELECT c.symbol,n.name,c.change_status,c.estimated_change_value FROM company_changes c LEFT JOIN local_names n USING(symbol) WHERE entity_id=? AND report_period=? ORDER BY estimated_change_value '+direction+' NULLS LAST LIMIT 10',[corp,period]),action='symbol',quality='此为持仓变动估算，不是定报大额买卖金额。'))
        if code:s.append(_section('corp_stock_history','该公司单股持仓历史',d.q('SELECT report_period,symbol,market_value,shares,weight,holding_rank FROM company_stock_holdings WHERE company_id=? AND symbol=? ORDER BY report_period',[corp,code]),'line',x='report_period',series=['shares','weight']))
        _company_performance(d,[corp],s,p,controls)
        for side in ('buy','sell'):
            s.append(_section('corp_disclosed_'+side,'定报披露大额'+('买入' if side=='buy' else '卖出')+'前列',d.q('SELECT * FROM local_company_disclosed_trades WHERE company_id=? AND report_period=? AND side=? ORDER BY disclosed_amount DESC LIMIT 20',[corp,period,side]),action='symbol',quality='仅已披露大额交易、现有采集样本；不代表全部交易金额。2024及以前按原站说明无此明细。'))
        _quarter_sections(d,period,corp,s)
        return
    if tab=='stock':
        if not code:s.append(_empty('choose_stock','个股详情','输入六位股票代码，或在持仓表点击证券。'));return
        s.append(_section('stock_kpi','公募持股与拥挤度',d.q('SELECT f.*,m.active_manager_name_count,r.post_period_return FROM fund_stock_crowding f LEFT JOIN local_fund_stock_managers m ON m.symbol=f.symbol AND m.report_period=f.report_period LEFT JOIN local_stock_period_returns r ON r.symbol=f.symbol AND r.report_period=f.report_period WHERE f.report_period=? AND f.symbol=?',[period,code]),'kpi',columns=['symbol','name','market_value','shares','company_count','share_class_count','float_ratio','buyers','sellers','top10_entrants','active_manager_name_count','crowding_score','post_period_return'],quality='经理人数按期末任职经理姓名去重，可能同名；前十为可见公司持仓排名近似。'))
        history=d.q('SELECT f.*,m.active_manager_name_count FROM fund_stock_crowding f LEFT JOIN local_fund_stock_managers m ON m.symbol=f.symbol AND m.report_period=f.report_period WHERE f.symbol=? ORDER BY f.report_period',[code])
        s.append(_section('stock_history','冻结公募池可见持仓历史',history,'line',columns=['report_period','company_count','share_class_count','shares','float_ratio','buyers','sellers','crowding_score'],x='report_period',series=['shares','company_count']))
        s.append(_section('stock_history_table','公募持仓历史明细',history,columns=['report_period','company_count','share_class_count','active_manager_name_count','float_ratio','new_count','increase_count','decrease_count','exit_count','top10_entrants','crowding_score']))
        base='SELECT h.company_id,p.size_band,h.shares,h.market_value,h.weight,h.holding_rank,h.fund_count,c.change_status,c.share_change_ratio FROM company_stock_holdings h LEFT JOIN company_period p USING(company_id,report_period) LEFT JOIN company_changes c ON c.entity_id=h.company_id AND c.report_period=h.report_period AND c.symbol=h.symbol WHERE h.symbol=? AND h.report_period=?'
        stockargs=[code,period]
        if p.get('q'):base+=' AND contains(h.company_id,?)';stockargs.append(str(p['q']))
        s.append(_page(d,'stock_companies','持有公司',base,stockargs,p,['company_id','size_band','shares','market_value','weight','holding_rank','fund_count','change_status','share_change_ratio'],'market_value',action=_params_action('stock',code=code,corp='$company_id')))
        peer=d.q('SELECT industry_code FROM fund_stock_crowding WHERE symbol=? AND report_period=?',[code,period])
        if peer:s.append(_section('stock_peers','同业公募持仓前20',d.q('SELECT f.symbol,n.name,f.market_value,f.company_count,f.crowding_score FROM fund_stock_crowding f LEFT JOIN local_names n USING(symbol) WHERE report_period=? AND industry_code=? ORDER BY market_value DESC LIMIT 20',[period,peer[0]['industry_code']]),action='symbol'))
        if corp:s.append(_section('stock_company_history','选中公司单股历史',d.q('SELECT report_period,shares,weight,holding_rank FROM company_stock_holdings WHERE company_id=? AND symbol=? ORDER BY report_period',[corp,code]),'line',x='report_period',series=['shares']))
        _price(d,code,s,years=4,holding=True);return
    if tab=='industries':
        controls.extend([_select('bench','配置基准',p.get('bench','market'),[('market','全A样本流通市值'),('all','公募整体')]),_select('metric','轮动热力',p.get('metric','weight'),[('weight','公募权重'),('overweight','超配'),('weight_change','权重变化'),('estimated_net_change','估算持股变动额'),('market_value','持仓市值')])])
        rows=d.q('SELECT * FROM local_fund_industry_market WHERE report_period=? ORDER BY market_value DESC',[period])
        if p.get('bench')=='all':
            for row in rows:row['benchmark_weight']=row['weight'];row['overweight']=0 if row['weight'] is not None else None
        else:
            for row in rows:row['benchmark_weight']=row.get('market_weight')
        allowed={'industry_name','weight','overweight','weight_change','estimated_net_change','market_value'};sort=p.get('sort') if p.get('sort') in allowed else 'market_value'
        rows.sort(key=lambda r:(r.get(sort) is not None,r.get(sort) or 0),reverse=p.get('order','desc')!='asc')
        s.append(_section('industry_table','行业配置',rows,columns=['industry_code','industry_name','weight','market_weight','benchmark_weight','overweight','weight_change','estimated_net_change','market_value'],action=_params_action('industries',l1='$industry_code'),quality='公募整体作自身参考时超配为0；公司榜则与公募行业权重比较。全A分母为有价格/股本样本。'))
        metric=p.get('metric') if p.get('metric') in ('weight','overweight','weight_change','estimated_net_change','market_value') else 'weight'
        s.append(_section('industry_rotation','行业配置轮动',d.q('SELECT * FROM local_fund_industry_market WHERE report_period<=? ORDER BY report_period',[period]),'heatmap',columns=['report_period','industry_code','industry_name',metric],x='report_period',series=[metric]))
        if l1=='all' and rows:l1=rows[0]['industry_code']
        s.append(_section('industry_stocks','行业个股前50',d.q('SELECT f.*,n.name FROM fund_stock_crowding f LEFT JOIN local_names n USING(symbol) WHERE report_period=? AND industry_code=? ORDER BY market_value DESC LIMIT 50',[period,l1]),columns=['symbol','name','market_value','company_count','crowding_score','float_ratio'],action='symbol'))
        bench='m.weight' if p.get('bench')=='all' else 'm.market_weight'
        s.append(_section('industry_companies','行业超配公司前20',d.q('SELECT c.company_id,c.industry_name,c.weight,'+bench+' benchmark_weight,c.weight-'+bench+' overweight,c.market_value FROM company_industry c LEFT JOIN local_fund_industry_market m USING(report_period,industry_code) WHERE c.report_period=? AND c.industry_code=? ORDER BY overweight DESC LIMIT 20',[period,l1]),action='company_id'))
        return
    if tab=='heat':
        preset=p.get('preset','all');controls.extend([_select('preset','热力预设',preset,[('all','全部'),('buy','共识买入'),('sell','共识卖出'),('low_buy','低拥挤增持'),('high_sell','高拥挤减持'),('new','新进公司最多')]),_number('crowd_min','最低拥挤度',_float(p.get('crowd_min'),0,0,100)),_number('crowd_max','最高拥挤度',_float(p.get('crowd_max'),100,0,100))])
        fw,fa=_filters(p,'h');w=['h.report_period=?','h.crowding_score>=?','h.crowding_score<=?',*fw];a=[period,_float(p.get('crowd_min'),0,0,100),_float(p.get('crowd_max'),100,0,100),*fa]
        if preset=='buy':w.append("h.consensus='buy'")
        elif preset=='sell':w.append("h.consensus='sell'")
        elif preset=='low_buy':w.extend(['h.crowding_score<50','h.buyers>h.sellers'])
        elif preset=='high_sell':w.extend(['h.crowding_score>=50','h.sellers>h.buyers'])
        cap=str(p.get('cap','all'));limits={'lt10':(0,1e9),'10_50':(1e9,5e9),'50_200':(5e9,2e10),'200_1000':(2e10,1e11),'ge1000':(1e11,1e30)}
        controls.append(_select('cap','流通市值档',cap,[('all','全部'),('lt10','<10亿'),('10_50','10–50亿'),('50_200','50–200亿'),('200_1000','200–1000亿'),('ge1000','≥1000亿')]))
        if cap in limits:w.append('h.float_market_value>=? AND h.float_market_value<?');a+=list(limits[cap])
        base='SELECT h.* FROM fund_stock_crowding h WHERE '+' AND '.join(w)
        s.append(_page(d,'heat_stocks','持股热力与拥挤度',base,a,p,['symbol','name','industry_name','market_value','shares','company_count','share_class_count','float_ratio','buyers','sellers','new_count','increase_count','decrease_count','exit_count','top10_entrants','rank_risers','composite_score_proxy','crowding_score','consensus'],'new_count' if preset=='new' else 'composite_score_proxy'))
        _consensus(d,period,s)
        s.append(_section('composite_score','五项持股综合分（公开近似）',d.q('SELECT symbol,name,composite_score_proxy,top10_entrants,rank_risers,crowding_score,score_status FROM ('+base+') ORDER BY composite_score_proxy DESC LIMIT 100',a),action='symbol',quality='公司数、流通比例、市值、净买公司数、排名上升五项分位均值近似；非原站专有公式。'))
        returns=d.q('SELECT * FROM local_fund_consensus_following_complete WHERE report_period<? ORDER BY report_period,consensus',[period])
        _group_line(s,'consensus_next_return','剔除期内新股后的下一报告期收益（回顾统计）',returns,'report_period','consensus','equal_weight_following_return',kind='line',quality='报告期末至下一半年期末复权股价等权回顾统计；已剔除期内新股，并列沪深300；尚非按实际披露时点成交的PIT策略回测。')
        signal_chart=s[-1];benchmark_values={r['report_period']:r['benchmark_return'] for r in returns}
        for row in signal_chart['rows']:row['benchmark_return']=benchmark_values.get(row['report_period'])
        signal_chart['columns'].append({'key':'benchmark_return','label':'沪深300基准','format':'percent'});signal_chart['series'].append({'key':'benchmark_return','label':'沪深300基准','type':'line','line_style':'dashed'})
        benchmark={r['report_period']:{'report_period':r['report_period'],'benchmark_return':r['benchmark_return']} for r in returns}
        s.append(_section('consensus_benchmark','同区间沪深300基准',list(benchmark.values()),'line',x='report_period',series=['benchmark_return']))
        return
    if tab=='compare':
        ids=_ids(p.get('ids'))
        if len(ids)<2:s.append(_empty('comparison_basket','公司比较篮','至少选择2家、最多6家公司；可在排行点击公司加入，也可输入逗号分隔名称（兼容原站178/94）。'));return
        ph=','.join('?'*len(ids))
        overlaps=d.q(f'SELECT * FROM company_overlap WHERE report_period=? AND company_a IN({ph}) AND company_b IN({ph})',[period,*ids,*ids])
        lookup={(r['company_a'],r['company_b']):r for r in overlaps};matrix=[]
        for i,aa in enumerate(ids):
            for j,bb in enumerate(ids):
                r=lookup.get((aa,bb)) or lookup.get((bb,aa))
                matrix.append({'company_a':aa,'company_b':bb,'metric':'Jaccard' if i<j else '权重重叠','value':1 if i==j else r['jaccard' if i<j else 'weight_overlap'] if r else None})
        s.append(_section('overlap','两两重叠：上三角Jaccard / 下三角权重重叠',matrix,'heatmap',x='company_b',series=['value'],quality='权重重叠定义=sum(min(双方可见市值归一化权重))，数学口径明确但未经原站后台对账。'))
        holds=d.q(f'SELECT company_id,symbol,market_value,weight FROM company_stock_holdings WHERE report_period=? AND company_id IN({ph})',[period,*ids])
        by={}
        for row in holds:
            r=by.setdefault(row['symbol'],{'symbol':row['symbol'],'market_value':0,'company_count':0,**{'company_'+str(i):None for i in range(len(ids))}})
            r['market_value']+=row['market_value'] or 0;r['company_count']+=1;r['company_'+str(ids.index(row['company_id']))]=row['weight']
        common=sorted([r for r in by.values() if r['company_count']>=2],key=lambda r:r['market_value'],reverse=True)[:30]
        names={r['symbol']:r['name'] for r in d.q('SELECT symbol,name FROM local_names WHERE symbol IN('+','.join('?'*len(common))+')',[r['symbol'] for r in common])} if common else {}
        for i,row in enumerate(common,1):row['rank']=i;row['name']=names.get(row['symbol'])
        columns=['rank','symbol','name','market_value','company_count']+[{'key':'company_'+str(i),'label':v,'format':'percent'} for i,v in enumerate(ids)]
        s.append(_section('common_holdings','共同持仓前30（至少2家公司）',common,columns=columns,action='symbol'))
        s.append(_section('company_metrics','公司指标比较',d.q(f'SELECT {company_projection} FROM company_period WHERE report_period=? AND company_id IN({ph})',[period,*ids]),columns=['company_id','visible_market_value','period_value_change','holding_nav_ratio','stock_count','fund_count','new_count','increase_count','decrease_count','exit_count','estimated_net_change','stock_turnover','estimated_amount_turnover','top10_weight','hhi','mean_visible_halfyears','return_1y','return_2y','weighted_return_1y','excess_1y','implied_return','size_band','change_comparable_coverage_ratio','net_asset_coverage']))
        changes=d.q(f"SELECT symbol,entity_id,change_status,estimated_change_value FROM company_changes WHERE report_period=? AND entity_id IN({ph}) AND change_status IN('new_visible','increase','decrease','exit_visible')",[period,*ids]);div={}
        for r in changes:
            z=div.setdefault(r['symbol'],{'symbol':r['symbol'],'buyers':[],'sellers':[],'estimated_change_value':0})
            z['buyers' if r['change_status'] in ('new_visible','increase') else 'sellers'].append(r['entity_id']);z['estimated_change_value']+=r['estimated_change_value'] or 0
        rows=[{**r,'buyers':'、'.join(r['buyers']),'sellers':'、'.join(r['sellers'])} for r in div.values() if r['buyers'] and r['sellers']]
        rows.sort(key=lambda r:abs(r['estimated_change_value']),reverse=True)
        s.append(_section('divergence','分歧持股变化',rows[:50],action='symbol',quality='公司可见持股方向分歧，不是实际交易对手关系。'))
        _company_performance(d,ids,s,p,controls)


def _consensus(d,period,s):
    s.append(_section('comparable_coverage','公司跨期可比基金覆盖',d.q('SELECT company_id,fund_count,change_comparable_coverage_ratio,comparable_fund_count FROM company_period WHERE report_period=? ORDER BY change_comparable_coverage_ratio DESC NULLS LAST',[period]),quality='低于80%公司不认定新进、增持、减持、退出；买卖和共识空值不是0。',action='company_id'))
    for side,title in [('buy','共识买入'),('sell','共识卖出')]:
        rows=d.q("SELECT f.symbol,n.name,f.market_value,f.buyers,f.sellers,f.crowding_score FROM fund_stock_crowding f LEFT JOIN local_names n USING(symbol) WHERE report_period=? AND consensus=? AND NOT EXISTS(SELECT 1 FROM local_stock_lifetimes life WHERE life.symbol=f.symbol AND life.list_date>last_day(f.report_period-INTERVAL '6 months') AND life.list_date<=f.report_period) ORDER BY "+('buyers' if side=='buy' else 'sellers')+' DESC LIMIT 30',[period,side])
        s.append(_section('consensus_'+side,title,rows,action='symbol',missing='跨期可比基金样本覆盖不足80%，未认定买卖；没有达到至少3家公司且方向数量至少为反向3倍的信号。' if not rows else None,quality='公司跨期可比覆盖至少80%才认定方向；按冻结上市日剔除前半年报告期末后新股，上市日缺失样本保留并在数据说明标明。'))


def _capital(d,tab,p,s,controls):
    asof=p.get('daily_observation',_period(p.get('date'),FREEZE));win=_int(p.get('win'),20,1,100);win=win if win in (1,5,20,60,100) else 20
    code=_symbol(p.get('code'));controls.append(_text('code','证券下钻',code))
    if tab=='overview':
        market=d.q('SELECT * FROM local_market_stock_flow_windows WHERE trade_date<=? ORDER BY trade_date DESC LIMIT 1',[asof])
        sample = market[0].get('sample_count') if market else None
        s.append(_section('main_money_flow_daily','个股样本主力净额（CNY）',market,'kpi',
            columns=['trade_date','main_net','main_net_5d','main_net_20d','main_net_60d'],
            quality=f'东财个股资金流加总；最近截面 {sample:,} 只。各日样本会变化。' if sample else '东财个股资金流加总。'))
        dragon_daily=d.q('SELECT * FROM dragon_tiger_daily WHERE trade_date<=? ORDER BY trade_date DESC LIMIT 1',[asof])
        s.append(_section('dragon_tiger_daily','龙虎榜已观测净额（CNY，冲突未计入）',dragon_daily,'kpi',columns=['trade_date','net_amount','event_count','coverage_ratio']))
        rows=d.q('SELECT * FROM capital_balance_daily WHERE trade_date<=? ORDER BY trade_date DESC LIMIT ?',[asof,win]);rows.reverse()
        lines=[]
        for key,label,sign in [('etf_net_subscription','股票ETF净申购',1),('financing_net_buy','融资买入减偿还',1),('southbound_net_buy','南向净买入',-1),('ipo_funding','IPO募资',-1),('holder_decrease','股东减持估值',-1)]:
            valid=[r for r in rows if r[key] is not None and
                   (key!='etf_net_subscription' or not r.get('etf_available_date') or r['etf_available_date']<=asof)]
            lines.append({'line':label,'direction':sign,'window_days':win,'valid_days':len(valid),'source_as_of':valid[-1]['trade_date'] if valid else None,'observed_sum':sum(r[key] for r in valid) if valid else None,'complete_window_value':sum(r[key] for r in valid) if len(valid)==win else None})
        complete=all(r['complete_window_value'] is not None for r in lines)
        s.append(_section('balance','资金平衡表（严格完整窗口，CNY）',lines,quality='各行保留真实数据日。南向直接源为港元，缺少可核定的人民币转换输入时该行及严格净额留空；无事件记录不自动视为零。',net_balance=sum(r['direction']*r['complete_window_value'] for r in lines) if complete and 'capital_balance_daily' not in d.updated else None))
        margin=d.q("SELECT trade_date,margin_balance,financing_net_buy,balance_change FROM margin_exchange_flow WHERE exchange='ALL' AND trade_date<=? ORDER BY trade_date DESC LIMIT 60",[asof]);margin.reverse()
        s.append(_section('margin_exchange_flow','沪深两市融资余额 · 近60交易日',margin,'line',x='trade_date',series=['margin_balance'],preferred_span=4,actions=[dict(label='查看两融杠杆',**_params_action('margin',exchange='all'))]))
        south=d.q('SELECT trade_date,sum(net_buy) net_buy FROM southbound_daily WHERE trade_date<=? GROUP BY trade_date ORDER BY trade_date DESC LIMIT 60',[asof]);south.reverse()
        s.append(_section('southbound_daily','南向净买入 · 近60交易日（HKD）',south,'line',x='trade_date',series=['net_buy'],preferred_span=4,actions=[dict(label='查看跨境资金',**_params_action('cross'))]))
        etf=d.q('SELECT category,trade_date,net_subscription,fund_size FROM etf_category_flow WHERE trade_date=(SELECT max(trade_date) FROM etf_category_flow WHERE trade_date<=?)',[asof])
        s.append(_section('etf_category_flow','ETF分类最新净申购（CNY估算）',etf,'bar',x='category',series=['net_subscription'],preferred_span=4,action=_params_action('etf',category='$category'),actions=[dict(label='查看ETF资金',**_params_action('etf'))]))
        main_history = d.q('SELECT * FROM capital_market_native WHERE trade_date<=? ORDER BY trade_date DESC LIMIT 100',[asof]) if 'capital_market_native' in d.views else []
        main_history.reverse()
        native = main_history[-1] if main_history and main_history[-1]['trade_date']==asof else {}
        s.append(_section('overview_main_trend','东财大盘主力净额 · 最近100个数据日',main_history,'line',
            x='trade_date',series=['main_net'],preferred_span=4,quality='东方财富大盘资金流向（人民币）。',
            missing='该版本没有大盘资金流向记录。' if not main_history else None,
            actions=[dict(label='查看个股样本',**_params_action('main',history=100))]))
        board_flows=d.q("WITH latest AS(SELECT trade_date,industry_code,industry_name,net_flow,row_number() OVER(ORDER BY net_flow DESC,industry_code) high_rank,row_number() OVER(ORDER BY net_flow ASC,industry_code) low_rank FROM local_sw_flow WHERE trade_date=(SELECT max(trade_date) FROM local_sw_flow WHERE trade_date<=?) AND net_flow IS NOT NULL) SELECT trade_date,industry_code,industry_name,net_flow,CASE WHEN high_rank<=5 THEN '净流入前五' ELSE '净流出前五' END flow_group FROM latest WHERE high_rank<=5 OR low_rank<=5 ORDER BY net_flow DESC",[asof])
        s.append(_section('overview_board_flows','板块资金 · 申万一级前后五（CNY近似）',board_flows,'bar',x='industry_name',series=['net_flow'],preferred_span=4,quality='申万一级采用当前已知行业映射近似；与子页申万口径同源，不混用单位未核定的同花顺板块。',action=_params_action('boards',src='sw',sector='$industry_code'),actions=[dict(label='板块跟踪',**_params_action('boards',src='sw'))]))
        dragon_top=d.q('SELECT e.symbol,n.name,e.trade_date,e.daily_return,e.net_amount,e.reasons,e.quality_status FROM dragon_tiger_events e LEFT JOIN local_names n USING(symbol) WHERE e.trade_date=(SELECT max(trade_date) FROM dragon_tiger_events WHERE trade_date<=?) AND e.net_amount IS NOT NULL ORDER BY e.net_amount DESC,e.symbol LIMIT 7',[asof])
        s.append(_section('overview_dragon','龙虎榜 · 同日有效净买入前七（CNY）',dragon_top,columns=['symbol','name','trade_date','daily_return','net_amount','reasons'],preferred_span=4,quality='复用同股同日已去重事件；多原因金额冲突不进入榜单，不能把有效样本合计视为完整全市场。',action=_params_action('toplist',code='$symbol',event_date='$trade_date'),actions=[dict(label='龙虎榜',**_params_action('toplist'))]))
        stock_etf=d.q("SELECT max(trade_date) trade_date,count(*) etf_count,count(net_subscription) valid_flow_count,sum(net_subscription) observed_net_subscription FROM etf_daily_flow WHERE category IN ('broad','industry_theme','strategy_style') AND trade_date=(SELECT max(trade_date) FROM etf_daily_flow WHERE trade_date<=?)",[asof])[0]
        south_summary=d.q("SELECT trade_date,count(DISTINCT channel) channel_count,count(net_buy) valid_channels,sum(net_buy) observed_net_buy FROM southbound_daily WHERE trade_date=(SELECT max(trade_date) FROM southbound_daily WHERE trade_date<=?) GROUP BY trade_date",[asof])
        current_dragon=dragon_daily[0] if dragon_daily else {};current_margin=margin[-1] if margin else {};current_south=south_summary[0] if south_summary else {}
        north_summary=d.q("SELECT trade_date,count(DISTINCT channel) channel_count,count(turnover) valid_channels,sum(turnover) turnover FROM cross_border_daily WHERE trade_date<=? AND channel IN ('north_sh','north_sz') AND currency='CNY' GROUP BY trade_date HAVING count(DISTINCT channel)=2 AND count(turnover)=2 ORDER BY trade_date DESC LIMIT 1",[asof])
        north_summary=north_summary[0] if north_summary else {}
        summary=[]
        for label,value,source_date,unit,source,extra in [
            ('东财大盘主力净额',native.get('main_net'),native.get('trade_date'),'CNY','东方财富大盘资金流向',{'missing_reason':None if native else '该观察日无东财大盘主力净额。'}),
            ('东财大盘超大单净额',native.get('super_large_net'),native.get('trade_date'),'CNY','东方财富大盘资金流向',{'missing_reason':None if native.get('super_large_net') is not None else '该观察日无东财大盘超大单净额。'}),
            ('北向全市场成交',north_summary.get('turnover') if north_summary.get('channel_count')==2 and north_summary.get('valid_channels')==2 else None,north_summary.get('trade_date'),'CNY','沪深股通全市场成交',{'missing_reason':None if north_summary.get('channel_count')==2 and north_summary.get('valid_channels')==2 else '当日北向全市场成交未覆盖；十大成交不代总量，停披净买入不补造。'}),
            ('龙虎榜已观测净额',current_dragon.get('net_amount'),current_dragon.get('trade_date'),'CNY','同股同日去重龙虎榜',{'coverage_ratio':current_dragon.get('coverage_ratio'),'quality':'多原因金额冲突未计入，部分覆盖。'}),
            ('融资余额变化',current_margin.get('balance_change'),current_margin.get('trade_date'),'CNY','沪深两市融资余额变化',{'quality':'余额变化与融资买入减偿还分别计算。'}),
            ('南向净买入',current_south.get('observed_net_buy') if current_south.get('channel_count')==2 and current_south.get('valid_channels')==2 else None,current_south.get('trade_date'),'HKD','沪深港股通原币净买入',{'valid_channels':current_south.get('valid_channels'),'missing_reason':None if current_south.get('channel_count')==2 and current_south.get('valid_channels')==2 else '南向两通道当日有效金额不完整。'}),
            ('股票ETF估算净申购（已观测）',stock_etf.get('observed_net_subscription'),stock_etf.get('trade_date'),'CNY','宽基+行业主题+策略风格ETF',{'valid_flow_count':stock_etf.get('valid_flow_count'),'etf_count':stock_etf.get('etf_count'),'quality':'份额变化乘NAV估算；已观测合计，缺失未补零。'}),
        ]:
            summary.append({'label':label,'value':value,'trade_date':source_date,'unit':unit,'currency':unit,'format':'money','source':source,**extra})
        s.insert(0,_section('overview_summary','资金摘要 · 各项来源日与币种分列',summary,'kpi',quality='各项独立展示，HKD与CNY不折算、不加总；日期按各自最后有效来源记录。'))
        industry=d.q('SELECT sum(net_increase_amount) holder_net_increase,sum(decrease_amount) holder_decrease,sum(increase_amount) holder_increase FROM holdertrade_daily WHERE obs_date<=? AND obs_date>cast(? as date)-90',[asof,asof])
        s.append(_section('industrial_kpi','过去90日股东增减持公告估值（CNY）',industry,'kpi',quality='公告均价缺失按公告前末价估算；不是实际全市场成交。',actions=[dict(label='产业资本',**_params_action('corporate',detail='holder'))]))
        forward=d.q("SELECT e.*,p.close estimated_price,p.trade_date price_source_date,e.float_share*p.close estimated_value FROM (SELECT u.symbol,u.unlock_date,u.float_share,cast(? as date) anchor_date FROM local_unlock_versions u WHERE u.announce_date<=? AND (u.valid_to IS NULL OR u.valid_to>?) AND u.unlock_date>=date_trunc('week',cast(? as date)) AND u.unlock_date<date_trunc('week',cast(? as date))+INTERVAL '4 weeks') e ASOF LEFT JOIN local_daily_prices p ON e.symbol=p.symbol AND e.anchor_date>=p.trade_date",[asof,asof,asof,asof,asof])
        s.append(_section('unlock_forward','未来4周已知解禁估值（CNY）',[{'anchor_date':asof,'estimated_value':sum(r['estimated_value'] for r in forward if r['estimated_value'] is not None) if forward else None,'disclosure_count':len(forward)}],'kpi',quality='仅锚日已公告且冻结时可见的计划；未来覆盖未认证。',actions=[dict(label='解禁前瞻',**_params_action('corporate',detail='unlock'))]))
        if 'cross_border_daily' not in d.updated:
            s.append(_empty('north_current','近期北向全市场成交','本版本北向总量止于2024-08-16，十大成交不等于全市场成交。'))
        s.append(_empty('repurchase_plan','90日回购预案金额上限','当前正式表amount没有经验证的预案上限契约；产业资本页提供源披露金额，未冒充预案上限。'))
        s.append(_section('module_navigation','资金模块导航',[{'target_panel':t,'label':label} for t,label in BOARDS['capital_flow'][1] if t!='overview'],action=_params_action('$target_panel'),columns=[{'key':'label','label':'资金模块'}]))
        return
    if tab=='main':
        flow=p.get('flow','main_net');flow=flow if flow in ('main_net','super_large_net','large_net','medium_net','small_net') else 'main_net'
        controls.append(_select('flow','个股源分档',flow,[('main_net','主力'),('super_large_net','超大单'),('large_net','大单'),('medium_net','中单'),('small_net','小单')]))
        history=_int(p.get('history'),100,20,250);controls.append(_select('history','曲线历史',history,[(60,'60日'),(100,'100日'),(120,'120日'),(250,'250日')]))
        rows=d.q('SELECT * FROM local_market_stock_flow_windows WHERE trade_date<=? ORDER BY trade_date DESC LIMIT ?',[asof,history]);rows.reverse()
        s.append(_section('main_trend','个股源主力分档时序（CNY）',rows,'line',x='trade_date',series=[flow],quality='fund_flow原生个股净流入汇总；不同提供者口径不混加。'))
        suffix='' if win==1 else '_'+str(win)+'d'
        labels={'main_net':'主力','super_large_net':'超大单','large_net':'大单','medium_net':'中单','small_net':'小单'}
        cards=[]
        for key,label in labels.items():
            current=next((r for r in reversed(rows) if r.get(key+suffix) is not None),{})
            cards.append({'label':label+' · '+str(win)+'日净额','value':current.get(key+suffix),
                          'trade_date':current.get('trade_date'),'unit':'CNY','format':'money'})
        s.append(_section('main_kpi',str(win)+'日个股样本分档净额（CNY） · 分档最近完整窗口',cards,'kpi',
                              quality='若当前窗口缺源，保留最近完整窗口和实际截止日。'))
        cumulative=[];sums={key:0 for key in labels}
        for row in rows:
            point={'trade_date':row['trade_date']}
            for key in labels:
                if row.get(key) is not None:sums[key]+=row[key];point[key]=sums[key]
                else:point[key]=None
            cumulative.append(point)
        s.append(_section('main_cumulative','个股样本分档区间累计净额（CNY）',cumulative,'line',x='trade_date',series=list(labels)))
        field='main_net_inflow' if win==1 else f'flow_{win}d'
        for desc,title in [('DESC','净流入前十'),('ASC','净流出前十')]:
            s.append(_section('top_'+desc,str(win)+'日个股主力'+title,d.q('SELECT f.symbol,n.name,f.main_net_inflow,f.flow_5d,f.flow_20d,f.flow_60d,f.consecutive_days,f.daily_return,f.main_turnover_ratio FROM stock_fund_flow_windows f LEFT JOIN local_names n USING(symbol) WHERE trade_date=(SELECT max(trade_date) FROM stock_fund_flow_windows WHERE trade_date<=?) ORDER BY '+field+' '+desc+' NULLS LAST LIMIT 10',[asof]),action='symbol'))
        s.append(_section('flow_distribution','所选资金日净占比分布',d.q('SELECT * FROM local_flow_distribution_history WHERE trade_date=(SELECT max(trade_date) FROM local_flow_distribution_history WHERE trade_date<=?)',[asof]),'bar',x='bucket',series=['sample_count'],quality='原生个股样本，缺失净占比分箱单列。'))
        return
    if tab=='boards':
        src=p.get('src','sw');src=src if src in ('sw','ths_industry','ths_concept','em_region') else 'sw'
        controls.append(_select('src','板块口径',src,[('sw','申万一级（当前映射近似）'),('ths_industry','同花顺行业'),('ths_concept','同花顺概念'),('em_region','东财地域')]))
        sector=str(p.get('sector',''));controls.append(_text('sector','板块下钻代码',sector))
        heat_sort=p.get('heat_sort','flow_20d');heat_sort=heat_sort if heat_sort in ('consecutive_days','flow_20d','net_flow') else 'flow_20d'
        controls.append(_select('heat_sort','热力行排序',heat_sort,[('consecutive_days','连续天数'),('flow_20d','20日净额'),('net_flow','今日净额')]))
        field='net_flow' if win==1 else f'flow_{win}d'
        if src=='em_region':
            s.append(_empty('region','东财地域资金','冻结板块预计算只有东财行业/概念，没有经过核验的地域分类与成分；不能把行业表改名为地域。'));return
        if src=='sw':
            selected_turnover='turnover' if win==1 else f'turnover_{win}d'
            base='SELECT industry_code sector_id,industry_name sector_name,trade_date,net_flow,turnover,net_ratio,mean_daily_return,company_count,flow_5d,flow_20d,flow_60d,consecutive_days,'+field+' selected_net,return_'+str(win)+'d selected_return,'+field+'/nullif('+selected_turnover+',0) selected_ratio FROM local_sw_flow WHERE trade_date=(SELECT max(trade_date) FROM local_sw_flow WHERE trade_date<=?)';a=[asof]
            if p.get('q'):base+=' AND contains(industry_name,?)';a.append(str(p['q']))
            rows=d.q(base+' ORDER BY '+field+' DESC',a)
            s.append(_section('board_map','申万资金地图',rows,'heatmap',x='sector_name',series=['selected_net'],presentation='treemap',size_key='turnover',color_key='selected_ratio',action=_params_action('boards',sector='$sector_id'),quality='当前期间行业映射近似；窗口收益按逐股复合后等权平均，净占比用同窗成交额。'))
            s.append(_section('board_quadrant','资金 × 当日成分收益',rows,'scatter',x='selected_net',series=['selected_return'],action='sector_id'))
            s.append(_section('board_details','板块明细',rows,action='sector_id'))
            heat=d.q('SELECT * FROM local_sw_flow WHERE trade_date IN (SELECT DISTINCT trade_date FROM local_sw_flow WHERE trade_date<=? ORDER BY trade_date DESC LIMIT 20)'+(' AND industry_code=?' if sector else '')+' ORDER BY trade_date',[asof,sector] if sector else [asof])
            ordering={r['sector_id']:i for i,r in enumerate(sorted(rows,key=lambda r:(r.get(heat_sort) is not None,r.get(heat_sort) or 0),reverse=True))};heat.sort(key=lambda r:(ordering.get(r['industry_code'],99999),r['trade_date']))
            s.append(_section('board_heat','近20日行业资金热力',heat,'heatmap',x='trade_date',series=['net_flow']))
            if sector:
                s.append(_section('board_trend','所选行业资金走势',d.q('SELECT * FROM local_sw_flow WHERE industry_code=? AND trade_date<=? ORDER BY trade_date DESC LIMIT 120',[sector,asof]),'line',x='trade_date',series=['net_flow']))
                s.append(_section('members','行业成分最新资金',d.q('SELECT f.symbol,n.name,f.main_net_inflow,f.daily_return,f.turnover FROM stock_fund_flow_windows f JOIN local_stock_context c USING(symbol) LEFT JOIN local_names n USING(symbol) WHERE c.industry_code=? AND trade_date=(SELECT max(trade_date) FROM stock_fund_flow_windows WHERE trade_date<=?) ORDER BY turnover DESC LIMIT 100',[sector,asof]),action='symbol',quality='当前行业映射近似。'))
        else:
            bt='industry' if src=='ths_industry' else 'concept'
            base="SELECT * FROM sector_flow_windows WHERE provider='ths' AND board_type=? AND trade_date=(SELECT max(trade_date) FROM sector_flow_windows WHERE provider='ths' AND board_type=? AND trade_date<=?)";a=[bt,bt,asof]
            if p.get('q'):base+=' AND contains(sector_name,?)';a.append(str(p['q']))
            s.append(_page(d,'board_details','同花顺板块资金明细',base,a,p,['sector_id','sector_name','trade_date','net_flow','change_pct','company_count','flow_5d','flow_20d','flow_60d','consecutive_days'],field,action='sector_id',quality='来源原生板块口径，金额源单位未核定；概念存在成分重叠，不与申万金额相加。'))
            s.append(_section('board_map','板块资金地图',d.q(base+' ORDER BY '+field+' DESC LIMIT 100',a),'heatmap',x='sector_name',series=[field],presentation='treemap',size_key='company_count',color_key=field,action=_params_action('boards',sector='$sector_id')))
            if sector:s.append(_section('board_trend','所选板块历史',d.q("SELECT * FROM sector_flow_windows WHERE provider='ths' AND board_type=? AND sector_id=? AND trade_date<=? ORDER BY trade_date DESC LIMIT 120",[bt,sector,asof]),'line',x='trade_date',series=['net_flow']))
            ths_heat=d.q("SELECT * FROM sector_flow_windows WHERE provider='ths' AND board_type=? AND trade_date IN(SELECT DISTINCT trade_date FROM sector_flow_windows WHERE provider='ths' AND board_type=? AND trade_date<=? ORDER BY trade_date DESC LIMIT 20)"+(' AND sector_id=?' if sector else '')+' ORDER BY trade_date',[bt,bt,asof,sector] if sector else [bt,bt,asof])
            native=d.q(base,a);ordering={r['sector_id']:i for i,r in enumerate(sorted(native,key=lambda r:(r.get(heat_sort) is not None,r.get(heat_sort) or 0),reverse=True))};ths_heat.sort(key=lambda r:(ordering.get(r['sector_id'],99999),r['trade_date']))
            quadrant=[{**r,'selected_net':r.get(field),'selected_return':r.get('return_'+str(win)+'d')} for r in native]
            s.append(_section('board_quadrant','原生板块资金×复合涨跌',quadrant,'scatter',x='selected_net',series=['selected_return'],action=_params_action('boards',sector='$sector_id'),quality='同花顺原生板块涨跌复合，非申万成分等权；金额按来源原始单位，不混加。'))
            s.append(_section('board_heat','同花顺板块20日资金热力',ths_heat,'heatmap',x='trade_date',series=['net_flow'],action=_params_action('boards',sector='$sector_id'),quality='来源原生单位单列，不与申万CNY流量相加。'))
            s.append(_empty('members','板块成分','原生同花顺板块与历史成分未对齐，不能用申万成分替代概念成分。'))
        return
    if tab=='stocks':
        field='main_net_inflow' if win==1 else f'flow_{win}d'
        w=['f.trade_date=(SELECT max(trade_date) FROM stock_fund_flow_windows WHERE trade_date<=?)'];a=[asof];fw,fa=_filters(p,'c');w+=fw;a+=fa
        w=[v.replace('c.symbol','f.symbol') for v in w]
        cap=p.get('cap','all');limits={'lt10':(0,1e9),'10_50':(1e9,5e9),'50_200':(5e9,2e10),'200_1000':(2e10,1e11),'ge1000':(1e11,1e30),'≥1000亿':(1e11,1e30)}
        if cap in limits:w.append('c.float_market_value>=? AND c.float_market_value<?');a+=list(limits[cap])
        state=p.get('filter','all')
        return_field='daily_return' if win==1 else f'return_{win}d'
        states={'up_in':f'f.{return_field}>0 AND f.{field}>0','down_in':f'f.{return_field}<0 AND f.{field}>0','up_out':f'f.{return_field}>0 AND f.{field}<0','down_out':f'f.{return_field}<0 AND f.{field}<0'}
        if state in states:w.append(states[state])
        controls.extend([_select('l1','行业',p.get('l1','all'),[('all','全部')]+[(x['value'],x['label']) for x in _meta()['industries']]),_select('cap','流通市值档',cap,[('all','全部'),('lt10','<10亿'),('10_50','10–50亿'),('50_200','50–200亿'),('200_1000','200–1000亿'),('ge1000','≥1000亿')]),_select('filter','资金状态',state,[('all','全部'),('up_in','价涨资金入'),('down_in','价跌资金入'),('up_out','价涨资金出'),('down_out','价跌资金出')])])
        turn_field='turnover' if win==1 else f'turnover_{win}d'
        base='SELECT f.*,c.name,c.industry_name,c.float_market_value,f.'+field+' selected_net,f.'+return_field+' selected_return,f.'+field+'/nullif(f.'+turn_field+',0) selected_ratio FROM stock_fund_flow_windows f LEFT JOIN local_stock_context c USING(symbol) WHERE '+' AND '.join(w)
        cols=['symbol','name','industry_name','trade_date','selected_net','selected_return','selected_ratio','daily_return','turnover','main_net_inflow','main_turnover_ratio','super_large_net_inflow','large_net_inflow','medium_net_inflow','small_net_inflow','flow_5d','flow_20d','flow_60d','consecutive_days','recent_20_flows','float_market_value']
        if 'fund_change_coverage' in d.views:cols.insert(cols.index('consecutive_days'),'flow_100d')
        s.append(_page(d,'stock_flows','个股资金表',base,a,p,cols,field,quality='流通市值与行业来自最近已知报告期近似；市场资金日与市值口径日期不同。'))
        s.append(_section('stock_heat','成交额前500（当前筛选）',d.q('SELECT symbol,name,industry_name,turnover,main_net_inflow,daily_return,selected_net,selected_return FROM ('+base+') ORDER BY turnover DESC LIMIT 500',a),'heatmap',x='symbol',series=['selected_net'],presentation='treemap',size_key='turnover',color_key='selected_net',action='symbol'))
        if code:
            rows=d.q('SELECT * FROM stock_fund_flow_windows WHERE symbol=? AND trade_date<=? ORDER BY trade_date DESC LIMIT 60',[code,asof]);rows.reverse()
            s.append(_section('stock_detail','所选个股60日资金',rows,'line',columns=['trade_date','symbol','main_net_inflow','super_large_net_inflow','large_net_inflow','medium_net_inflow','small_net_inflow','daily_return','turnover'],x='trade_date',series=['main_net_inflow','super_large_net_inflow','large_net_inflow']))
            _price(d,code,s,days=60,asof=asof)
            source_rows=d.q('SELECT * FROM local_stock_three_sources_cny WHERE symbol=? AND trade_date<=? QUALIFY row_number() OVER(PARTITION BY provider ORDER BY trade_date DESC)<=60 ORDER BY trade_date,provider',[code,asof])
            s.append(_section('three_sources','三来源资金对照（统一CNY，分档仍不同）',source_rows,columns=['provider','trade_date','net_flow_native','source_unit','net_flow_cny','display_unit'],quality='moneyflow原金额单位万元×10000转CNY；其他源原值为元。统一单位不代表分档/统计口径一致。'))
            _group_line(s,'source_comparison','三来源净流入对照（CNY）',source_rows,'trade_date','provider','net_flow_cny',quality='moneyflow万元×10000；单位统一，分档仍独立，禁止加总为总资金。')
            for provider in ('fund_flow','moneyflow','stock_moneyflow_ths'):
                s.append(_section('source_'+provider,provider+'原生资金序列',[r for r in source_rows if r['provider']==provider],'line',x='trade_date',series=['net_flow_cny'],quality='统一CNY，moneyflow保留原单位万元及原值；来源分档独立不相加。'))
        return
    if tab=='margin':
        ex=str(p.get('exchange','all'));ex=ex if ex in ('all','SSE','SZSE','BSE') else 'all';exkey='ALL' if ex=='all' else ex
        controls.append(_select('exchange','交易所',ex,[('all','沪深两市合计'),('SSE','沪'),('SZSE','深'),('BSE','北')]))
        rows=d.q('SELECT * FROM margin_exchange_flow WHERE trade_date<=? AND exchange=? ORDER BY trade_date DESC LIMIT 250',[asof,exkey]);rows.reverse()
        s.append(_section('margin_kpi','融资余额与来源日期（CNY）',rows[-1:],'kpi',columns=['exchange','trade_date','margin_balance','margin_buy','margin_repay','financing_net_buy','balance_change','financing_buy_turnover_ratio'],quality='沪深合计严格只含SSE+SZSE；任何必要分项缺失保留空，不混入北交所。'))
        s.append(_section('margin_trend','融资余额',rows,'line',x='trade_date',series=['margin_balance']))
        s.append(_section('margin_net','融资买入减偿还 / 余额变化',rows,'line',x='trade_date',series=['financing_net_buy','balance_change'],quality='两者分别展示，不将余额变化当净买入。'))
        s.append(_section('margin_buy_ratio','融资买入占成交额',rows,'line',x='trade_date',series=['financing_buy_turnover_ratio']))
        heat=d.q('SELECT * FROM local_margin_industry_complete WHERE exchange=? AND trade_date IN(SELECT DISTINCT trade_date FROM local_margin_industry_complete WHERE trade_date<=? AND exchange=? ORDER BY trade_date DESC LIMIT 20) ORDER BY trade_date',[exkey,asof,exkey])
        s.append(_section('margin_industry','所选交易所申万行业20日融资余额变化',heat,'heatmap',x='trade_date',series=['balance_change'],action=_params_action('stocks',l1='$industry_code'),quality='当前行业映射近似；余额变化不是融资买入减偿还。'))
        rank=d.q('SELECT industry_code,max(industry_name) industry_name,sum(balance_change) balance_change,sum(margin_buy) margin_buy FROM local_margin_industry_complete WHERE exchange=? AND trade_date IN(SELECT DISTINCT trade_date FROM local_margin_industry_complete WHERE trade_date<=? AND exchange=? ORDER BY trade_date DESC LIMIT ?) GROUP BY industry_code ORDER BY balance_change DESC',[exkey,asof,exkey,win])
        s.append(_section('margin_industry_rank',str(win)+'日行业融资余额变化排行',rank,action=_params_action('stocks',l1='$industry_code'),quality='个券偿还合同缺失，不能改称精确净买排行。'))
        exfilter=" AND (ends_with(m.symbol,'.SH') OR ends_with(m.symbol,'.SZ'))" if ex=='all' else ' AND '+{'SSE':"ends_with(m.symbol,'.SH')",'SZSE':"ends_with(m.symbol,'.SZ')",'BSE':"ends_with(m.symbol,'.BJ')"}[ex]
        base='SELECT m.*,n.name FROM margin_security_flow m LEFT JOIN local_names n USING(symbol) WHERE trade_date=(SELECT max(trade_date) FROM margin_security_flow m WHERE trade_date<=?'+exfilter+')'+exfilter
        if p.get('q'):base+=' AND (contains(m.symbol,?) OR contains(n.name,?))';args=[asof,str(p['q']),str(p['q'])]
        else:args=[asof]
        s.append(_page(d,'margin_securities','所选交易所个券融资余额变化（含ETF等）',base,args,p,['symbol','name','trade_date','margin_balance','margin_buy','balance_change','quality_status'],'balance_change'))
        ratio=d.q('SELECT * FROM local_margin_ratio_complete WHERE trade_date<=? AND exchange=? ORDER BY trade_date DESC LIMIT 250',[asof,exkey]);ratio.reverse()
        s.append(_section('margin_float_ratio','融资余额 / 同日股票流通市值',ratio,'line',x='trade_date',series=['margin_float_ratio'],quality='分母是同日正式股票市值样本；沪深合计只含沪深，不代表仅融资标的市值。'))
        return
    if tab=='cross':
        legacy=str(p.get('channel','all'))
        north=p.get('north_channel',legacy if legacy.startswith('north_') else 'all');north=north if north in ('all','north_sh','north_sz') else 'all'
        south=p.get('south_channel',legacy if legacy.startswith('south_') else 'all');south=south if south in ('all','south_sh','south_sz') else 'all'
        controls.extend([_select('north_channel','北向通道',north,[('all','沪深合计/分列'),('north_sh','沪股通'),('north_sz','深股通')]),_select('south_channel','南向通道',south,[('all','沪深港股通'),('south_sh','沪港股通'),('south_sz','深港股通')])])
        sw='trade_date<=?'+(' AND channel=?' if south!='all' else '');sa=[asof,south] if south!='all' else [asof]
        rows=d.q('SELECT trade_date,channel,net_buy,cumulative_net_buy,quality_status FROM southbound_daily WHERE '+sw+' QUALIFY row_number() OVER(PARTITION BY channel ORDER BY trade_date DESC)<=250 ORDER BY trade_date',sa)
        _group_line(s,'south_trend','南向净买入（港元原币）',rows,'trade_date','channel','net_buy')
        _group_line(s,'south_cumulative','南向已覆盖观测累计净买入（港元）',rows,'trade_date','channel','cumulative_net_buy')
        nw="trade_date<=? AND channel IN ('north_sh','north_sz')"+(' AND channel=?' if north!='all' else '');na=[asof,north] if north!='all' else [asof]
        cross=d.q('SELECT * FROM cross_border_daily WHERE '+nw+' QUALIFY row_number() OVER(PARTITION BY channel ORDER BY trade_date DESC)<=250 ORDER BY trade_date',na)
        _group_line(s,'cross_turnover','北向全市场成交（人民币）',cross,'trade_date','channel','turnover',quality='停披的是净买入；总成交只展示正式通道有值的日期。')
        _group_line(s,'cross_ratio','北向成交占全A',cross,'trade_date','channel','share_of_ashare_turnover')
        for key,title,allowed,selected in [('cross_top','北向十大成交（CNY）',('north_sh','north_sz'),north),('cross_south_top','港股通十大成交（HKD）',('south_sh','south_sz'),south)]:
            channels=list(allowed) if selected=='all' else [selected]; placeholders=','.join('?'*len(channels))
            q='SELECT * FROM cross_border_top_trades_detail WHERE channel IN ('+placeholders+') AND trade_date=(SELECT max(trade_date) FROM cross_border_top_trades_detail WHERE trade_date<=? AND channel IN ('+placeholders+')) ORDER BY channel,rank'
            s.append(_section(key,title,d.q(q,[*channels,asof,*channels]),columns=['symbol','name','channel','trade_date','currency','rank','turnover','buy_amount','sell_amount','net_amount'],action='symbol',quality='十大成交不是全市场总量；币种按原字段保留。'))
        return
    if tab=='etf':_etf(d,p,s,controls);return
    if tab=='toplist':
        safe_asof=date.fromisoformat(asof).isoformat()
        d.c.execute("CREATE TEMP VIEW visible_dragon_events AS SELECT * REPLACE(CASE WHEN next_date<=DATE '"+safe_asof+"' THEN next_trading_day_return END AS next_trading_day_return) FROM dragon_tiger_events")
        reason=str(p.get('reason',''));seat=str(p.get('seat',''));event_date=observation({'date':p.get('event_date') or asof},asof)
        controls.extend([_text('reason','上榜原因包含',reason),_text('seat','席位精确名称',seat)])
        w=['e.trade_date=(SELECT max(trade_date) FROM visible_dragon_events WHERE trade_date<=?)'];a=[event_date]
        if reason:w.append('contains(reasons,?)');a.append(reason)
        if p.get('q'):w.append('(contains(e.symbol,?) OR contains(n.name,?))');a.extend([str(p['q'])]*2)
        base='SELECT e.*,n.name,t.turnover_rate FROM visible_dragon_events e LEFT JOIN local_names n USING(symbol) LEFT JOIN local_dragon_turnover t USING(symbol,trade_date) WHERE '+' AND '.join(w)
        events=_page(d,'dragon_events','龙虎榜事件（同股日去重）',base,a,p,['symbol','name','trade_date','daily_return','net_amount','buy_amount','sell_amount','turnover','turnover_rate','net_turnover_ratio','reasons','next_trading_day_return','quality_status'],'net_amount',action=_params_action('toplist',code='$symbol',event_date='$trade_date'),quality='多原因金额冲突保留空，不重复累加；点击事件可查看对应交易日席位。')
        s.append(events)
        s.append(_section('active_seats','所选交易日活跃营业部',d.q('SELECT * FROM dragon_tiger_seat_ranking WHERE trade_date=(SELECT max(trade_date) FROM dragon_tiger_seat_ranking WHERE trade_date<=?) ORDER BY stock_count DESC,net_amount DESC LIMIT 30',[asof]),action=_params_action('toplist',seat='$seat')))
        if code:
            rows=d.q('SELECT * FROM dragon_tiger_seat_daily WHERE symbol=? AND trade_date=(SELECT max(trade_date) FROM dragon_tiger_seat_daily WHERE symbol=? AND trade_date<=?) ORDER BY buy_amount DESC',[code,code,event_date])
            s.append(_section('buy_seats','所选事件买入前五席位',rows[:5],columns=['symbol','trade_date','seat','buy_amount','sell_amount','net_amount','quality_status'],action=_params_action('toplist',seat='$seat')))
            s.append(_section('sell_seats','所选事件卖出前五席位',sorted(rows,key=lambda r:r['sell_amount'] or 0,reverse=True)[:5],columns=['symbol','trade_date','seat','buy_amount','sell_amount','net_amount','quality_status'],action=_params_action('toplist',seat='$seat')))
            s.append(_section('dragon_history','个股上榜及次交易日表现',d.q('SELECT * FROM visible_dragon_events WHERE symbol=? AND trade_date<=? ORDER BY trade_date DESC LIMIT 60',[code,asof]),'line',columns=['trade_date','net_amount','next_trading_day_return','quality_status'],x='trade_date',series=['net_amount','next_trading_day_return'],quality='次日表现是冻结日已知价格的回顾统计，不代表上榜当日已知。'))
        if seat:
            s.append(_section('seat_history','该营业部近期上榜证券',d.q('SELECT h.*,n.name FROM dragon_tiger_seat_daily h LEFT JOIN local_names n USING(symbol) WHERE seat=? AND trade_date<=? ORDER BY trade_date DESC LIMIT 100',[seat,asof]),columns=['trade_date','symbol','name','buy_amount','sell_amount','net_amount','quality_status'],action=_params_action('toplist',code='$symbol',event_date='$trade_date')))
        regular=d.q('SELECT r.*,n.name FROM local_dragon_regulars_history r LEFT JOIN local_names n USING(symbol) WHERE anchor_date=(SELECT max(anchor_date) FROM local_dragon_regulars_history WHERE anchor_date<=?) ORDER BY event_count DESC,observed_net_amount DESC NULLS LAST LIMIT 30',[asof])
        for row in regular:row.pop('observed_next_return',None)
        s.append(_section('dragon_regulars','截至所选日近5交易日常客',regular,action=_params_action('toplist',code='$symbol'),quality='按交易日历滚动5日；同股日去重，冲突净额不累加，valid_amount_days单列。'))
        s.append(_section('dragon_themes','所选日龙虎榜题材资金（当前已知归属近似）',d.q('SELECT * FROM local_dragon_themes_history WHERE trade_date=(SELECT max(trade_date) FROM local_dragon_themes_history WHERE trade_date<=?) ORDER BY observed_net_amount DESC NULLS LAST LIMIT 30',[asof]),quality='冻结前已知概念近似；成分重叠不可合计，不宣称历史PIT题材。'))
        s.append(_section('dragon_next_distribution','所选上榜日次交易日收益分布',d.q("SELECT trade_date,CASE WHEN next_trading_day_return<-.05 THEN '<-5%' WHEN next_trading_day_return<0 THEN '-5%至0' WHEN next_trading_day_return<.05 THEN '0至5%' WHEN next_trading_day_return>=.05 THEN '>=5%' ELSE '期限未满或缺失' END bucket,count(*) event_count FROM visible_dragon_events WHERE trade_date=(SELECT max(trade_date) FROM visible_dragon_events WHERE trade_date<=?) GROUP BY ALL",[asof]),'bar',x='bucket',series=['event_count'],quality='使用冻结日已知的下一交易日股价；最新日无未来价格时缺失单列。'))
        return
    if tab=='corporate':
        detail=p.get('detail','unlock');detail=detail if detail in ('unlock','ipo','holder','repurchase') else 'unlock'
        controls.append(_select('detail','产业资本明细',detail,[('unlock','解禁'),('ipo','IPO募资'),('holder','股东增减持'),('repurchase','回购实施')]))
        if detail=='repurchase':
            view=p.get('repurchase_view','latest');view=view if view in ('latest','announcements') else 'latest';controls.append(_select('repurchase_view','回购记录口径',view,[('latest','当时每股最新披露'),('announcements','过去90日全部公告')]))
            if view=='latest':base='SELECT r.*,n.name FROM local_repurchase_versions r LEFT JOIN local_names n USING(symbol) WHERE announce_date<=? AND (valid_to IS NULL OR valid_to>?)';args=[asof,asof]
            else:base='SELECT r.*,n.name FROM repurchase_announcements r LEFT JOIN local_names n USING(symbol) WHERE announce_date<=? AND announce_date>cast(? as date)-90';args=[asof,asof]
            cols=['symbol','name','announce_date','progress','amount','volume','end_date','quality_status'];order='amount';title='当时每股最新回购披露' if view=='latest' else '90日回购公告（源值，不重复求和）'
        elif detail=='holder':
            base='SELECT h.*,n.name FROM holdertrade_valued h LEFT JOIN local_names n USING(symbol) WHERE announce_date<=? AND announce_date>cast(? as date)-90';args=[asof,asof];cols=['symbol','name','announce_date','holder_name','trade_type','change_vol','estimated_amount','estimated_price','price_source_date','quality_status'];order='estimated_amount';title='90日重要股东增减持公告估值'
        elif detail=='ipo':
            base='SELECT i.*,life.list_date listing_date FROM ipo_funding_detail i LEFT JOIN local_stock_lifetimes life USING(symbol) WHERE i.trade_date<=?';args=[asof];cols=['symbol','name','trade_date','listing_date','subscription_date','issue_date','issue_price','issue_shares','raised_amount'];order='trade_date';title='IPO募资明细'
        else:
            mode=p.get('unlock_mode','future');mode=mode if mode in ('future','history') else 'future';weeks=_int(p.get('unlock_weeks'),8,4,26)
            controls.extend([_select('unlock_mode','解禁方向',mode,[('future','未来已公告计划'),('history','过去解禁股数')]),_select('unlock_weeks','解禁观察周数',weeks,[(4,'4周'),(8,'8周'),(12,'12周'),(26,'26周')])])
            where="u.announce_date<=? AND (u.valid_to IS NULL OR u.valid_to>?)"
            if mode=='future':where+=" AND u.unlock_date>=date_trunc('week',cast(? as date)) AND u.unlock_date<date_trunc('week',cast(? as date))+?::INTEGER*INTERVAL '1 week'";args=[asof,asof,asof,asof,weeks]
            else:where+=" AND u.unlock_date<=cast(? as date) AND u.unlock_date>cast(? as date)-?::INTEGER*INTERVAL '1 week'";args=[asof,asof,asof,asof,weeks]
            base='SELECT e.*,n.name,p.close estimated_price,p.trade_date price_source_date,e.float_share*p.close estimated_value FROM (SELECT u.*,cast(? as date) price_anchor FROM local_unlock_versions u WHERE '+where+') e ASOF LEFT JOIN local_daily_prices p ON e.symbol=p.symbol AND e.price_anchor>=p.trade_date LEFT JOIN local_names n ON n.symbol=e.symbol'
            args=[asof,*args];cols=['symbol','name','announce_date','unlock_date','holder_name','share_type','float_share','share_ratio']+(['estimated_value','estimated_price','price_source_date'] if mode=='future' else []);order='unlock_date';title=('未来'+str(weeks)+'周已公告解禁估值') if mode=='future' else '过去'+str(weeks)+'周解禁股数'
        base='SELECT * FROM ('+base+') data WHERE true'
        if code:base+=' AND symbol=?';args.append(code)
        if p.get('q'):base+=' AND (contains(symbol,?) OR contains(name,?))';args.extend([str(p['q'])]*2)
        s.append(_page(d,detail,title,base,args,p,cols,order,quality='按所选日筛选公告有效版本；金额为源值/估值，不能冒充预案金额上限或真实交易。'))
        if detail=='unlock':
            metric='estimated_value' if mode=='future' else 'float_share'
            weekly=d.q("SELECT cast(date_trunc('week',unlock_date) AS DATE) week_start,sum("+metric+") "+metric+",count(*) disclosure_count FROM ("+base+") GROUP BY week_start ORDER BY week_start",args)
            s.append(_section('unlock_weeks','所选窗口解禁'+('估值' if mode=='future' else '股数'),weekly,'bar',x='week_start',series=[metric],quality='未来按所选观察日前末价估算；历史只展示股数。未来披露生命周期覆盖未验收。'))
        if detail=='repurchase':s.append(_empty('repurchase_plan','回购预案金额上限','正式amount字段尚无经核实的预案上限合同；源披露金额不可冒充上限。'))
        return


def _panel_impl(board,panel_id,params,store=None):
    """Return one complete tab; unknown tabs fail explicitly before SQL."""
    meta=metadata(board,store);tab=panel_id or 'overview'
    params=dict(params or {})
    if tab=='entity':
        target=str(params.get('entity_id') or params.get('entity') or (params.get('code') if board!='national_team' else '') or '')
        industry_values={x['value'] for x in _meta()['industries']}
        if board=='fund_holdings':
            corp=_company(target)
            if corp:tab='corp';params['corp']=corp
            elif target in industry_values:tab='industries';params['l1']=target
            else:tab='stock';params['code']=_symbol(target)
        elif board=='capital_flow':
            if str(params.get('source_panel') or params.get('tab') or '')=='toplist':
                tab='toplist'
                if _symbol(target):params['code']=target
                else:params['seat']=target
            elif target in ENTITY:tab='stocks'
            elif str(params.get('source_panel') or params.get('tab') or '')=='etf' or _symbol(target).startswith(('5','15','16')):tab='etf';params['etf']=target
            elif target and not _symbol(target):tab='boards';params['sector']=target
            else:tab='stocks';params['code']=target
        elif target in industry_values:tab='holdings';params['l1']=target
        elif _symbol(target).startswith(('5','15','16')):tab='etf';params['etf']=target
        elif _symbol(target):tab='stock';params['code']=target
        elif target in ENTITY:params['entity']=target
    aliases={'company':'corp','industry':'industries','cross_border':'cross','main_force':'main'}
    tab=aliases.get(tab,tab)
    if tab not in {x['id'] for x in meta['tabs']}:raise ValueError('Unknown desk tab: '+str(tab))
    p={**meta['defaults'],**(params or {})};sections=[];controls=_controls(board,p)
    if store is not None and getattr(store,"daily_boards",{}):
        p['daily_observation']=observation(params,cutoff_for(store,board,FREEZE))
        if 'date' not in params:
            p['date']=p['daily_observation']
            controls=_controls(board,p)
    else:
        p.pop('daily_observation',None)
    d=_Data(store,p.get("daily_observation"))
    if board!='capital_flow' and d.updated:
        controls.append(_text('date','净值/行情观察日',p.get('daily_observation',FREEZE)))
    try:
        if board=='national_team':_national(d,tab,p,sections,controls)
        elif board=='fund_holdings':_fund(d,tab,p,sections,controls)
        else:_capital(d,tab,p,sections,controls)
        if board in ('fund_holdings','national_team') and tab=='stock' and _symbol(p.get('code')) and 'phase_daily_quotes' in d.views:
            points=d.q('SELECT * FROM phase_daily_quotes WHERE symbol=? AND trade_date>? AND trade_date<=? ORDER BY trade_date',
                       [_symbol(p['code']),FREEZE,p['daily_observation']])
            if points and 'local_daily_prices' not in d.updated:
                sections.append(_section('daily_raw_quote','最新正式不复权日线 · 与历史复权线分列',points,'line',
                    columns=['trade_date','open','high','low','close','amount'],x='trade_date',series=['close'],
                    presentation='candlestick',ohlc={'open':'open','high':'high','low':'low','close':'close'},
                    quality='仅更新行情，持仓期、组合收益与历史复权价格的口径不变。'))
    except Exception as error:
        if (error.__class__.__name__=='CatalogException' and 'does not exist' in str(error)) or (error.__class__.__name__=='BinderException' and ('not found' in str(error) or 'does not have' in str(error))):
            sections.append(_empty('version_input_missing','该冻结版本尚无此预计算',str(error).split('\n')[0]+'；未回退读取当前可变产物。'))
        else:raise
    finally:d.close()
    label=next(x['label'] for x in meta['tabs'] if x['id']==tab)
    _finalize_sections(board,tab,p,sections)
    return {'title':meta['title']+' · '+label,'description':meta['description'],'sections':sections,'controls':_scoped_controls(board,tab,controls),'warnings':list(WARNINGS[board]),'freeze_date':FREEZE,'board':board,'tab':tab}


def _group_line(sections,sid,title,rows,x,group,value,quality=None,kind='line',normalize=False):
    groups=list(dict.fromkeys(r[group] for r in rows));wide={};first={}
    for r in rows:
        g=r[group];v=r.get(value)
        if normalize and v is not None and v>0:first.setdefault(g,v)
        if normalize:v=v/first[g]*100 if v is not None and g in first else None
        wide.setdefault(r[x],{x:r[x]})['value_'+str(groups.index(g))]=v
    cols=[x]+[{'key':'value_'+str(i),'label':ENTITY.get(g,CATEGORY.get(g,g)),'format':'percent' if value in PCT_KEYS or value.endswith('_return') or value in ('drawdown','excess_return') else None} for i,g in enumerate(groups)]
    sections.append(_section(sid,title,[wide[k] for k in sorted(wide)],kind,columns=cols,x=x,series=[{'key':'value_'+str(i),'label':ENTITY.get(g,CATEGORY.get(g,g))} for i,g in enumerate(groups)],quality=quality))


def _company_performance(d,ids,s,p=None,controls=None):
    p=p or {};ph=','.join('?'*len(ids));weight='scale' if p.get('nav_weight')=='scale' else 'equal';interval=p.get('nav_range','2y');interval=interval if interval in ('1y','2y','3y','5y') else '2y';days=int(interval[0])*366
    if controls is not None:controls.extend([_select('nav_weight','累计收益曲线加权',weight,[('equal','等权'),('scale','前一日已公开规模加权')]),_select('nav_range','累计收益曲线区间',interval,[('1y','近1年'),('2y','近2年'),('3y','近3年'),('5y','近5年')])])
    asof=p.get('daily_observation',FREEZE)
    returns_view=_company_windows(d,asof)
    s.append(_section('company_returns','公司真实NAV区间收益',d.q(f'SELECT * FROM {returns_view} WHERE company_id IN({ph}) ORDER BY company_id,calendar_days',[*ids]),columns=['company_id','calendar_days','first_date','last_date','equal_weight_return','scale_weight_return','benchmark_return','equal_weight_excess','scale_weight_excess','valid_days','mean_observed_coverage','mean_scale_coverage'],quality='主动权益基金池当前名称分类近似；实际复权NAV，规模权重仅用前一交易日已公开净资产。'))
    value='scale_weight_nav' if weight=='scale' else 'equal_weight_nav'
    rows=d.q(f'SELECT company_id,trade_date,{value} nav_value FROM company_nav_daily WHERE company_id IN({ph}) AND trade_date>cast(? as date)-?::INTEGER AND trade_date<=? ORDER BY trade_date',[*ids,asof,days,asof])
    benchmark=d.q("SELECT '沪深300' company_id,trade_date,benchmark_nav nav_value FROM local_benchmark_daily WHERE trade_date>cast(? as date)-?::INTEGER AND trade_date<=? ORDER BY trade_date",[asof,days,asof]);rows+=benchmark;rows.sort(key=lambda r:r['trade_date'])
    _group_line(s,'compare_returns',('规模加权' if weight=='scale' else '等权')+'真实累计净值与沪深300（首值100）',rows,'trade_date','company_id','nav_value',normalize=True,quality='每条曲线从本窗口首有效正值归一100；收益截至实际净值观测日，独立于持仓报告期。')
    if len(ids)==1:
        scale=d.q(f'SELECT company_id,trade_date,scale_weight_nav FROM company_nav_daily WHERE company_id IN({ph}) AND trade_date>cast(? as date)-?::INTEGER AND trade_date<=? ORDER BY trade_date',[*ids,asof,days,asof])
        _group_line(s,'company_scale_nav','规模加权净值（首值100）',scale,'trade_date','company_id','scale_weight_nav',normalize=True)


def _quarter_sections(d,period,corp,s):
    where='report_period=(SELECT max(report_period) FROM local_quarter_top10_coverage WHERE report_period>cast(? as date) AND report_period<=last_day(cast(? as date)+INTERVAL \'3 months\'))'
    args=[period,period]
    if corp:where+=' AND company_id=?';args.append(corp)
    coverage=d.q('SELECT * FROM local_quarter_top10_coverage WHERE '+where+' ORDER BY coverage_ratio DESC',args)
    s.append(_section('quarter_coverage','季报前十可比基金覆盖',coverage,columns=['company_id','report_period','prev_period','prev_funds','matched_funds','coverage_ratio'],quality='按每基金可见市值排序取10；相邻季报与前期均有披露，至少5份基金且可比覆盖≥80%才进变动榜。'))
    for direction,title in [('DESC','最新季前十大增持'),('ASC','最新季前十大减持')]:
        state=" AND ((previous_shares IS NULL AND current_shares IS NOT NULL) OR share_change_ratio>.02)" if direction=='DESC' else " AND (current_shares IS NULL OR share_change_ratio<-.02)"
        rows=d.q('SELECT t.*,n.name FROM local_quarter_top10_changes t LEFT JOIN local_names n USING(symbol) WHERE '+where+state+' ORDER BY share_change_ratio '+direction+' NULLS LAST LIMIT 30',args)
        s.append(_section('quarter_top10_'+direction,title,rows,columns=['company_id','report_period','symbol','name','current_shares','adjusted_previous_shares','share_change_ratio','fund_count','coverage_ratio','quality_status'],action='symbol',missing='没有通过可比基金覆盖≥80%、基金份额至少5份的季报前十样本；未放宽门槛认定增减持。' if not rows else None,quality='rank缺失时以每份基金可见市值排序重建前十；季度披露scope未逐报告认证，明确为本地可见前十近似。'))


def _scoped_controls(board,tab,controls):
    """Only expose controls with an implemented effect, naming partial scopes."""
    by_key={c['key']:c for c in controls}
    keep=None
    labels={}
    if board=='national_team':
        if tab=='portfolio':keep={'portfolios','component_portfolio','range','date'}
        elif tab=='etf':keep={'q','index','win','mode','thr','etf','etfs','expanded','page','page_size','date'}
        elif tab=='overview':keep={'period','scope'};labels={'period':'季度持仓报告期','scope':'季度持仓主体口径'}
        elif tab=='holdings':labels={'q':'明细/集中度搜索','l1':'明细/集中度行业','exclude_banks':'明细/集中度/板块剔银行','metric':'主体矩阵指标'}
        elif tab=='changes':labels={'entity':'四表/热力/共振主体','l1':'四表/热力/共振行业','q':'四表/统计搜索'}
    elif board=='fund_holdings':
        fund_keep={'overview':{'period','band','ids','date'},'ranking':{'period','band','q','corp','ids','page','page_size','date'},'corp':{'period','corp','code','q','l1','filter','ids','page','page_size','nav_weight','nav_range','date'},'stock':{'period','code','corp','q','ids','page','page_size','date'},'industries':{'period','l1','bench','metric','date'},'heat':{'period','q','l1','preset','crowd_min','crowd_max','cap','ids','page','page_size','date'},'compare':{'period','ids','nav_weight','nav_range','date'}}
        by_key={k:v for k,v in by_key.items() if k in fund_keep[tab]}
        if tab in ('corp','stock','industries','heat','compare'):by_key.pop('band',None)
        if tab=='ranking':labels={'corp':'图中高亮公司'}
        if tab=='overview':labels={'band':'分档汇总筛选'};by_key.pop('q',None)
        if tab in ('industries','compare'):by_key.pop('q',None)
        if tab=='corp':labels={'q':'持仓明细搜索','l1':'持仓明细行业','filter':'持仓明细状态'}
        if tab=='stock':labels={'q':'持有公司搜索'}
        if tab=='industries':labels={'bench':'行业表/公司榜配置基准','metric':'行业热力指标'}
    else:
        if tab not in ('overview','main','boards','stocks','margin','etf'):by_key.pop('win',None)
        if tab not in ('stocks','toplist','corporate'):by_key.pop('code',None)
        if tab in ('overview','main','cross'):by_key.pop('q',None)
        if tab=='overview':
            keep={'date','win'}
            labels={'win':'资金平衡表窗口'}
        if tab=='margin':labels={'win':'行业排行窗口','q':'个券表搜索'}
        if tab=='boards':labels={'q':'板块明细/地图搜索','sector':'走势/成分板块','win':'板块累计窗口'}
        if tab=='toplist':labels={'code':'上榜股席位/历史','q':'事件表搜索'}
        if tab=='etf':by_key.pop('code',None)
        if tab=='corporate':labels={'q':'当前产业明细搜索','code':'当前产业明细证券'}
    # The original fund/national pages have report periods, not an editable global quote date.
    if board!='capital_flow' and 'date' not in by_key:
        by_key['date']=_select('date','净值/行情冻结日',FREEZE,[(FREEZE,FREEZE)])
    if keep is not None:by_key={k:v for k,v in by_key.items() if k in keep}
    for k,label in labels.items():
        if k in by_key:by_key[k]['label']=label
    return list(by_key.values())


def panel(board,panel_id,params,store=None):
    token=_META_STORE.set(store)
    try:return _panel_impl(board,panel_id,params,store)
    finally:_META_STORE.reset(token)


def _float(value,default,lo,hi):
    try:return round(max(lo,min(hi,float(value))),1)
    except (TypeError,ValueError):return default


def _finalize_sections(board,tab,p,sections):
    """Small UI contracts shared by tables and charts; no data collection here."""
    for section in sections:
        section.setdefault('calculation_status','proxy' if any(t in str(section.get('quality',''))+section.get('title','') for t in ('代理','近似','估算','估值')) else 'frozen_observation')
        if board=='capital_flow' and section['id'] in ('southbound_daily','south_trend','south_cumulative','cross_south_top'):
            section['currency']='HKD'
            for col in section['columns']:
                if col.get('format')=='money' or col['key'].startswith('value_'):
                    col.update(format='money',unit='HKD')
                    col['label']=col.get('label',col['key']).replace('CNY','HKD')
            for series in section.get('series',[]):series['unit']='HKD'
        if board=='capital_flow' and tab=='boards' and str(p.get('src','sw')).startswith('ths'):
            section['currency']=None
            section['source_unit_status']='unverified_native_unit'
            for col in section['columns']:
                if col.get('format')=='money':col['unit']='source_native'

        if board=='fund_holdings' and section['id'] in ('crowding','stock_kpi','heat_stocks','composite_score'):
            section['calculation_status']='proxy'
        if board=='capital_flow' and tab=='boards' and str(p.get('win'))=='100' and p.get('src','sw')=='sw' and section['id'] in ('board_map','board_quadrant','board_details'):
            section['calculation_status']='proxy'
            section['quality']=section.get('quality','')+'；100日收益为行业日均成分收益复合代理，非逐股复合后等权。'
        if board=='fund_holdings' and any(t in section['id'] for t in ('consensus','heat','changes','composite','turnover')):
            section.setdefault('coverage_note','跨期指标仅满足门槛的公司样本；空值代表覆盖不足。')
        if section['id']=='ranking':
            section['action']=_params_action('ranking',corp='$company_id')
            for col in section['columns']:
                if col['key']=='company_id':col['actions']=[dict(label='详情',**_params_action('corp',corp='$company_id')),{'type':'basket_add','key':'company_id','label':'加入比较'}]
        if section['id']=='corp_holdings':
            section['action']=_params_action('corp',corp=p.get('corp',''),code='$symbol')
            for col in section['columns']:
                if col['key']=='symbol':col['actions']=[dict(label='个股透视',**_params_action('stock',code='$symbol'))]
        if section['id']=='stock_companies':
            for col in section['columns']:
                if col['key']=='company_id':col['actions']=[dict(label='公司详情',**_params_action('corp',corp='$company_id'))]
        if section['id']=='etf_pool':section['group_by']='reported_index_code'
        if board=='fund_holdings' and tab=='ranking' and section['id'] in ('return_turnover','concentration_scatter'):
            selected=_company(p.get('corp'))
            original=section['series'][0]['key']
            for row in section['rows']:row['highlighted_company']=row.get(original) if row.get('company_id')==selected else None
            section['columns'].append({'key':'highlighted_company','label':'选中公司','format':'percent' if original in PCT_KEYS else None})
            section['series'].append({'key':'highlighted_company','label':'选中公司'})
