"""Contrarian research desk over frozen source and explicit local proxies.

Requests read a bounded daily stock snapshot, precomputed industry aggregates or
single-stock history. Historical dates never borrow current financial risk flags.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date as Date, timedelta
from pathlib import Path

from seven_desks.daily import cutoff_for, observation, rows as daily_rows

CUTOFF='2026-11-09'
TABS=[('overview','逆向总览'),('temperature','市场温度'),('industries','行业逆向'),('candidates','候选榜'),('traps','陷阱排雷'),('signals','拐点信号')]
BOARDS={'low_pe_ttm':'低PE','low_pcf_ttm':'低PCF','low_pb':'低PB','high_dividend':'高股息','low_ev_ebitda':'低EV/EBITDA','low_ps_ttm':'低PS','short_reversal':'短期反转','long_loser':'长期输家','deep_drawdown':'深度回撤','cycle_reverse':'周期反向'}
ALIASES={'composite':'all','low_pe':'low_pe_ttm','low_pcf':'low_pcf_ttm','high_dv':'high_dividend','low_ev':'low_ev_ebitda','low_ps':'low_ps_ttm','short_rev':'short_reversal','deep_dd':'deep_drawdown','cyc_rev':'cycle_reverse'}
LIGHTS={'audit_light':'审计','cashflow_light':'现金流质量','cash_debt_light':'偿债','goodwill_light':'商誉','receivable_light':'应收','deducted_loss_light':'扣非亏损','holder_sell_light':'大股东减持','unlock_light':'未来三月解禁'}
KINDS={'repurchase_announcement':'回购公告','holder_increase_announcement':'产业资本增持','positive_earnings_forecast':'业绩预增/扭亏预告','positive_earnings_express':'正增长快报','eps_up_revision_observed':'观测期EPS上修','stability_transition_proxy':'企稳转换（本地历史代理）'}
LABELS={'symbol':'代码','name':'股票','industry_name':'行业','industry_l1':'申万一级','industry_l2':'申万二级','rank':'名次','pe_ttm':'PE(TTM)','pb':'PB','ps_ttm':'PS(TTM)','pcf_ttm':'PCF(TTM)','ev_ebitda':'EV/EBITDA','dv_ttm':'股息率(%)','ret20':'过去20日收益','ret3y':'过去3年收益','ret60':'过去60日收益','drawdown52':'距52周高点','bias20':'20日乖离','score':'F-score','stable':'企稳','red_count':'红灯数','yellow_count':'黄灯数','lights_available':'有效风险项/8','composite_score':'本地综合分','hit_count':'命中榜数','hits':'命中榜','pe_ttm_discount':'PE相对行业中位偏离','pb_discount':'PB相对行业中位偏离','pcf_ttm_discount':'PCF相对行业中位偏离','ps_ttm_discount':'PS相对行业中位偏离','ev_ebitda_discount':'EV/EBITDA相对行业中位偏离','pe_ttm_time_pct3y':'PE三年历史分位','pb_time_pct3y':'PB三年历史分位','roe_five_year_pct':'ROE五年分位','trade_date':'观察日','temperature':'本地逆向温度','sentiment':'情绪代理组','valuation':'估值代理组','smart_money':'资金/聪明钱代理组','valid_items':'有效项数','valid_groups':'有效组数','band':'温度档位','label':'项目','value':'数值','indicator':'指标','indicator_id':'编号','raw_value':'原值','percentile':'原值五年分位','fear_score':'恐慌得分','group':'分组','direction':'恐慌方向','minimum_samples':'最少样本','window_sessions':'交易日窗口','source_column':'输入列','valuation_percentile':'行业估值五年分位','pe_pct_5y':'PE五年分位','pb_pct_5y':'PB五年分位','pe':'行业指数PE','close':'收盘价','coldness':'冷清度代理','crowding_proxy':'拥挤度代理','contrarian_zone':'本地逆向区','earnings_yoy':'已披露归母净利同比中位数','earnings_change':'同比较上期变化(pp前比例)','earnings_sample_count':'同比有效公司数','profit_turn':'盈利拐点','overweight':'公募行业超低配(可见持仓口径)','allocation_period':'公募持仓报告期','allocation_available':'公募报告保守可得日','candidate_count':'候选股数','stock_count':'证券数','amount_share':'行业成交额占比','report_period':'报告期','as_of_date':'数据可得日','event_date':'事件日','entry_date':'收益计量起日','kind_label':'信号类别','summary':'已有披露摘要','event_count':'事件数','kinds':'信号类型','excess20':'20日相对全指超额','excess60':'60日相对全指超额','end20':'20日收益终日','end60':'60日收益终日','latest_event':'最近事件日','event_week':'事件周','board':'候选榜','board_a':'榜A','board_b':'榜B','intersection':'交集股票数','union':'并集股票数','pool_count':'候选池数','red_ratio':'已知红灯比例','unknown_risk_count':'风险未覆盖股票数','metric':'风险项目','available':'有效公司数','red':'红灯公司数','yellow':'黄灯公司数','rule':'判断规则','reason':'口径/缺失说明','horizon':'交易日期限','sample_count':'完整样本数','mean':'均值','median':'中位数','win_rate':'胜率','start':'区间起日','end':'区间止日','peak_date':'最高恐慌日','peak_temperature':'最高温度','sessions':'持续交易日','holding_start':'持有期起日','holding_end':'持有期止日','price_return':'价格收益','benchmark_return':'同期中证全指收益','excess_return':'相对全指超额','above_ma20':'站上20日线','bias_ok':'20日乖离>-5%','down_streak':'连续下跌交易日','decline_ok':'连续下跌≤2日','stability_conditions':'企稳条件满足数','gap_to_industry_median':'回归行业中位估值空间','financial_value':'财务值','announce_date':'公告日','financial_metric':'财务项目','quality_status':'质量状态'}
LABELS.update(LIGHTS)
LABELS.update({'roe_report_period':'ROE报告期','roe_latest':'ROE原值(%)','roe_sample_count':'五年有效ROE报告数','cycle_industry_yoy':'行业已披露净利同比','cycle_report_period':'行业财报期','opinion':'审计意见原文','audit_firm':'会计师事务所','signing_auditor':'签字会计师','audit_conflict':'同键意见冲突','ocf':'经营现金流(元)','profit':'归母净利润(元)','deducted':'扣非净利润(元)','cfo_profit_ratio':'经营现金流/归母净利','ocf_announce_date':'现金流公告日','profit_announce_date':'归母净利公告日','deducted_announce_date':'扣非净利公告日','ar':'应收账款(元)','revenue':'营收(元)','ar_growth':'应收同比','revenue_growth':'营收同比','ar_growth_gap':'应收同比−营收同比','cash':'现金(元)','short_debt':'短期借款(元)','due_debt':'一年内到期负债(元)','equity':'归母净资产(元)','goodwill':'商誉(元)','cash_debt_ratio':'现金/短债','goodwill_ratio':'商誉/净资产','inputs_available':'所用值最晚公告日'})
LABELS.update({'market_value_cny':'总市值(元)','ocf_ttm_cny':'经营现金流TTM(元)','net_debt_cny':'净债务(元)','ebitda_ttm_cny':'EBITDA TTM(元)','ev_cny':'企业价值EV(元)','pcf_input_quality':'PCF输入质量','ev_input_quality':'EV输入质量','fiscal_year':'财年','components_available':'F-score有效项数/9','positive_roa':'ROA为正','positive_cfo':'经营现金流为正','rising_roa':'ROA改善','cfo_exceeds_income':'现金流高于净利','lower_long_borrowings_proxy':'长期借款杠杆下降(代理)','higher_current_ratio':'流动比率改善','no_share_increase':'股本未增加','higher_gross_margin':'毛利率改善','higher_asset_turnover':'资产周转改善','universe_eligible':'是否符合基础股票池'})
LABELS.update(announcement_date='公告日期',as_of_date='保守可得日',fscore_proxy='历史 F-score 代理',
              fscore_report_period='F-score 年报期',fscore_components_available='有输入项数',
              strict_pit='修订版本已验证',source='来源',metric_value='所选估值',valuation_score='低估值分',group_count='可比组样本数')
PERCENT={'ret20','ret3y','ret60','drawdown52','bias20','pe_ttm_discount','pb_discount','pcf_ttm_discount','ps_ttm_discount','ev_ebitda_discount','earnings_yoy','earnings_change','overweight','amount_share','red_ratio','excess20','excess60','mean','median','win_rate','price_return','benchmark_return','excess_return','gap_to_industry_median'}
BASE=['rank','symbol','name','industry_name','hit_count','hits','score','stable','red_count','yellow_count','lights_available','composite_score']
BOARD_COLUMNS={'low_pe_ttm':['pe_ttm','pe_ttm_discount','pe_ttm_time_pct3y'],'low_pcf_ttm':['market_value_cny','ocf_ttm_cny','pcf_ttm','pcf_ttm_discount'],'low_pb':['pb','pb_discount','pb_time_pct3y'],'high_dividend':['dv_ttm','dividend_score'],'low_ev_ebitda':['market_value_cny','net_debt_cny','ebitda_ttm_cny','ev_cny','ev_ebitda','ev_ebitda_discount'],'low_ps_ttm':['ps_ttm','ps_ttm_discount'],'short_reversal':['ret20'],'long_loser':['ret3y'],'deep_drawdown':['drawdown52'],'cycle_reverse':['roe_five_year_pct','annual_profit_change']}
BOARD_COLUMNS['cycle_reverse']=['roe_five_year_pct','roe_report_period','roe_latest','roe_sample_count','cycle_industry_yoy','cycle_report_period']
PERCENT.update({'cycle_industry_yoy','ar_growth','revenue_growth','ar_growth_gap','goodwill_ratio'})
NOT_DONE=['回购事项去重、EPS首次预期上修和原站上线后企稳信号未完全恢复；本地公告及企稳转换代理分别标识，不冒充原站事件口径。','原站私有11项温度清单、拥挤度综合分及精确评分尚未闭合：页面明确使用本地代理，不能宣称原站数值复现。','历史EV、F-score及八类完整风险灯尚无同口径日频成品，不用最新值回填；历史候选按可用八因子近似。']


def _d(x):return str(x)[:10] if x is not None else ''
def _num(x):return isinstance(x,(int,float)) and math.isfinite(x)
def _board(x):return ALIASES.get(str(x),str(x))
def _cols(keys):return [{'key':k,'label':LABELS.get(k,k),'format':'percent' if k in PERCENT else 'text' if k in ('symbol','name','industry_name') else 'number'} for k in keys]
def sec(id,title,kind,rows,keys=(),**kwargs):
    r={'id':id,'title':title,'kind':kind,'rows':rows,'columns':_cols(keys),'quality':'本地近似 · 非原站数值对账'}
    if not rows:r['missing_reason']=kwargs.pop('missing','该日期或筛选条件下没有有效结果；未知值未替换为零。')
    r.update(kwargs);return r

def query(s,file,sql='SELECT * FROM {t}',params=None):
    p=s.path('contrarian/'+file)
    if not Path(p).exists():return []
    return s.query(sql.replace('{t}',"read_parquet('"+p.replace("'","''")+"',hive_partitioning=false,union_by_name=true)"),params)

def _extra(s,file,sql='SELECT * FROM {t}',params=None):
    if file in ('temperature','temperature-items'):
        p=s.path('revision/contrarian/'+file+'.parquet')
        if Path(p).exists():
            return s.query(sql.replace('{t}',"read_parquet('"+p.replace("'","''")+"')"),params)
    return query(s,'ui-extra/'+file+'.parquet',sql,params)

def _revised(s):return Path(s.path('revision/contrarian/temperature.parquet')).exists()

def _select(key,label,options,value):return {'key':key,'label':label,'type':'select','options':[{'value':str(k),'label':v} for k,v in options],'value':str(value)}
def _input(key,label,value='',type='text'):return {'key':key,'label':label,'type':type,'value':value}
def metadata(board):return {'title':'逆向投资','tabs':[{'id':a,'label':b} for a,b in TABS],'defaults':{'date':CUTOFF,'board':'all','page':1,'page_size':50,'range':'1y'},'description':'真实冻结输入；原站未公开算法使用明确标识的本地研究代理。'}


def _identity(s):
    if not hasattr(s,'_contrarian_identity'):
        names={r['symbol']:r.get('name') or r['symbol'] for r in s.read('local_owners/names.parquet')}
        groups={r['symbol']:r for r in query(s,'ui_factors.parquet','SELECT symbol,industry_l1,industry_l2,score_group FROM {t}')}
        s._contrarian_identity=(names,groups)
    return s._contrarian_identity


def _refresh_rows(s, name, where=None, params=None):
    return s.read('revision/contrarian/'+name+'.parquet',where=where,params=params)


def _days(s):
    if not hasattr(s,'_contrarian_days'):
        s._contrarian_days=sorted({_d(r['trade_date']) for r in s.read('market/sentiment/daily.parquet',columns=['trade_date']) if _d(r['trade_date'])<=CUTOFF})
    return s._contrarian_days


def _date(p,s,tab):
    raw=str(p.get('date',p.get('trade_date',cutoff_for(s,'contrarian',CUTOFF))))[:10]
    try:Date.fromisoformat(raw)
    except ValueError:raise ValueError('观察日必须为YYYY-MM-DD')
    if tab in ('industries','signals'):
        # These panels have their own input family. Their old snapshots should
        # remain visible when a newer ten-board stock cross section is selected.
        dates = [d for d in _days(s) if d<=min(raw,CUTOFF)]
    elif tab=='temperature' and _revised(s):
        rows = _refresh_rows(s,'temperature','trade_date<=? AND temperature IS NOT NULL',[raw])
        dates = sorted({_d(r['trade_date']) for r in rows})
    else:
        dates = _days(s) + [_d(r['trade_date']) for r in _refresh_rows(
            s,'daily-factors','trade_date<=? QUALIFY row_number() OVER(PARTITION BY trade_date ORDER BY symbol)=1',[raw])]
        dates = sorted({d for d in dates if d<=raw})
    return dates[-1] if dates else raw


def _snapshot(s,day,force_history=False):
    if day>CUTOFF:
        rows=_refresh_rows(s,'daily-factors','trade_date=?',[day])
        names,_=_identity(s)
        history = ({r['symbol']:r for r in _refresh_rows(s,'daily-history','trade_date=?',[day])}
                   if force_history else {})
        if force_history:
            rows = [{**r,'composite_score':history[r['symbol']]['historical_composite_proxy']}
                    for r in rows if r['symbol'] in history]
        for r in rows:
            r['name']=names.get(r['symbol'],r['symbol'])
        return rows
    latest=day==CUTOFF and not force_history
    rows=query(s,'ui_factors.parquet') if latest else query(s,f'history/factors_{day[:4]}.parquet','SELECT * FROM {t} WHERE trade_date=?',[day])
    names,groups=_identity(s)
    roe={r['symbol']:r for r in _extra(s,'roe-events','SELECT * FROM {t} WHERE as_of_date<=? QUALIFY row_number() OVER(PARTITION BY symbol ORDER BY as_of_date DESC)=1',[day])}
    earnings={r['industry_name']:r for r in _extra(s,'industry-profit',"SELECT * FROM {t} WHERE as_of_date<=? AND level='L1' QUALIFY row_number() OVER(PARTITION BY industry_name ORDER BY report_period DESC)=1",[day])}
    factor_inputs={r['symbol']:r for r in _extra(s,'factor-inputs')} if latest else {}
    for r in rows:
        r.update({k:v for k,v in factor_inputs.get(r['symbol'],{}).items() if k not in ('symbol','as_of_date')})
        r.pop('forward20',None);r.pop('forward60',None)
        r['name']=names.get(r['symbol'],r['symbol']);g=groups.get(r['symbol'],{})
        r['industry_l1']=g.get('industry_l1','未知行业');r['industry_l2']=g.get('industry_l2','未知行业');r['industry_name']=r['industry_l1'];r['score_group']=g.get('score_group',r['industry_l1'])
        if not latest:
            r['composite_score']=r.get('historical_composite_proxy');r['stable']=r.get('is_stable');r['score']=None
            r['universe_eligible']=bool((_d(r.get('list_date')) and (Date.fromisoformat(day)-Date.fromisoformat(_d(r['list_date']))).days>=365) and (r.get('amount_pct') or 0)>=.05 and 'ST' not in r['name'].upper())
            for k in LIGHTS:r[k]=None
            r.update(red_count=None,yellow_count=None,lights_available=0)
        if r['symbol'] in roe:
            r.update({k:roe[r['symbol']].get(k) for k in ('roe_five_year_pct','roe_report_period','roe_latest','roe_sample_count')})
        er=earnings.get(r['industry_l1'],{})
        r.update(cycle_industry_yoy=er.get('earnings_yoy'),cycle_report_period=er.get('report_period'))
        r['cycle_candidate']=bool(r.get('universe_eligible') and _num(r.get('roe_five_year_pct')) and r['roe_five_year_pct']<=20 and _num(r.get('cycle_industry_yoy')) and r['cycle_industry_yoy']>0)
        if latest and _num(r.get('valuation_score')) and _num(r.get('reversal_score')):
            base=.7*r['valuation_score']+.3*r['reversal_score'];f=r.get('score')
            mult=1.1 if _num(f) and f>=7 else .8 if _num(f) and f<=3 else 1.0
            r['composite_score']=min(100,base*mult*(.9 if (r.get('yellow_count') or 0)>=2 else 1)*(1.1 if r['cycle_candidate'] else 1))
        elif r['cycle_candidate'] and _num(r.get('composite_score')):
            r['composite_score']=min(100,r['composite_score']*1.1)
    return rows


def _memberships(s,day,rows):
    sets=defaultdict(set)
    if day>CUTOFF:
        for r in _refresh_rows(s,'daily-boards','trade_date=?',[day]):
            sets[r['board']].add(r['symbol'])
    elif day==CUTOFF:
        for r in query(s,'ui_boards.parquet','SELECT symbol,board FROM {t}'):sets[r['board']].add(r['symbol'])
    else:
        scores={'low_pe_ttm':'pe_ttm_score','low_pcf_ttm':'pcf_ttm_score','low_pb':'pb_score','low_ps_ttm':'ps_ttm_score','high_dividend':'dv_ttm_score','short_reversal':'ret20_score','long_loser':'ret3y_score','deep_drawdown':'drawdown52_score'}
        for r in rows:
            if not r.get('universe_eligible'):continue
            for b,k in scores.items():
                if _num(r.get(k)) and r[k]>=80 and (b!='high_dividend' or (r.get('dv_ttm') or 0)>=3):sets[b].add(r['symbol'])
    sets['cycle_reverse']={r['symbol'] for r in rows if r.get('cycle_candidate')}
    history_pct=_extra(s,'valuation-history-'+day[:4],'SELECT * FROM {t} WHERE trade_date=?',[day])
    pct={r['symbol']:r for r in history_pct}
    if not pct and day==CUTOFF:pct={r['symbol']:r for r in _extra(s,'stock-percentiles')}
    medians=defaultdict(list)
    for r in rows:
        if r.get('universe_eligible'):
            for k in ['pe_ttm','pb','pcf_ttm','ps_ttm','ev_ebitda']:
                if _num(r.get(k)) and r[k]>0:medians[r['score_group'],k].append(r[k])
    def median(values):
        values=sorted(values);n=len(values);return (values[(n-1)//2]+values[n//2])/2 if n else None
    medians={k:median(v) for k,v in medians.items()}
    for r in rows:
        hits=[b for b in BOARDS if r['symbol'] in sets[b]];r['hit_count']=len(hits);r['hits']='、'.join(BOARDS[b] for b in hits)
        for b in BOARDS:r['hit_'+b]=r['symbol'] in sets[b]
        if day<=CUTOFF:
            r.update({k:pct.get(r['symbol'],{}).get(k) for k in ('pe_ttm_time_pct3y','pb_time_pct3y')})
        for k in ['pe_ttm','pb','pcf_ttm','ps_ttm','ev_ebitda']:
            m=medians.get((r['score_group'],k));r[k+'_discount']=r[k]/m-1 if m and _num(r.get(k)) else None
            r[k+'_industry_median']=m
    return sets


def _filter(rows,p,day):
    chosen=_board(p.get('board','all'));a=_board(p.get('intersection_a','all'));b=_board(p.get('intersection_b','all'))
    combined=str(p.get('intersection','all')).split('|')
    if len(combined)==2:a,b=map(_board,combined)
    q=str(p.get('q','')).strip().casefold();industry=p.get('industry','all');out=[]
    for r in rows:
        if not r.get('universe_eligible'):continue
        if chosen in BOARDS and not r.get('hit_'+chosen):continue
        if any(x in BOARDS and not r.get('hit_'+x) for x in (a,b)):continue
        if day>=CUTOFF and str(p.get('red','0'))!='1' and r.get('red_count',0):continue
        if str(p.get('stable','0'))=='1' and not r.get('stable'):continue
        if industry not in ('all','',None,r.get('industry_l1'),r.get('industry_l2')):continue
        if q and q not in ' '.join(str(r.get(k,'')) for k in ('symbol','name','industry_l1','industry_l2')).casefold():continue
        out.append(r)
    return out


def _page(rows,p,default='composite_score',descending=True):
    allowed=set(LABELS)|set(BOARD_COLUMNS)|{'annual_profit_change','dividend_score'}
    key=str(p.get('sort',default));key=key if key in allowed else default
    valid=[r for r in rows if r.get(key) is not None];missing=[r for r in rows if r.get(key) is None]
    valid.sort(key=lambda r:(r[key],str(r.get('symbol',r.get('industry_name','')))),reverse=p.get('order','desc' if descending else 'asc')!='asc')
    ordered=valid+missing
    try:size=min(200,max(10,int(p.get('page_size',50))));pages=max(1,math.ceil(len(rows)/size));page=min(pages,max(1,int(p.get('page',1))))
    except (ValueError,TypeError):raise ValueError('分页参数必须为整数')
    result=ordered[(page-1)*size:page*size]
    for i,r in enumerate(result,(page-1)*size+1):r['rank']=i
    return result,{'total':len(rows),'page':page,'page_size':size,'pages':pages}


def _controls(p,tab,day):
    common=[_input('date','观察日',day),_select('range','历史区间',[('1y','近1年'),('3y','近3年'),('all','全部')],p.get('range','1y'))]
    if tab in ('overview','candidates','traps'):
        common += [_select('board','候选榜',[('all','综合')]+list(BOARDS.items()),_board(p.get('board','all'))),_input('q','代码/名称/行业',p.get('q','')),_select('stable','企稳',[('0','全部'),('1','仅已企稳')],p.get('stable','0'))]
        if day>=CUTOFF:common += [_select('red','红灯',[('0','剔除已知红灯'),('1','显示红灯')],p.get('red','0'))]
        if tab=='candidates':
            pairs=[('|'.join(sorted([a,b])),BOARDS[a]+' × '+BOARDS[b]) for a in BOARDS for b in BOARDS if a<=b]
            selected=p.get('intersection','|'.join(sorted([p.get('intersection_a','all'),p.get('intersection_b','all')])))
            common += [_select('intersection','命中两榜交集',[('all','不限')]+pairs,selected if selected!='all|all' else 'all')]
    return common


def _history(rows,p):
    n={'1y':252,'3y':756}.get(p.get('range','1y'));return rows[-n:] if n else rows

def _temperature(s,day):
    rows=_extra(s,'temperature','SELECT * FROM {t} WHERE trade_date<=? ORDER BY trade_date',[day])
    if rows:return rows
    rows=query(s,'local_temperature.parquet','SELECT * FROM {t} WHERE trade_date<=? ORDER BY trade_date',[day])
    for r in rows:r.update(sentiment=r.get('price_group'),valuation=r.get('value_group'),smart_money=None,band=None)
    return rows


def _prices(s,symbol,day):return s.read('market/ui-extra/index-prices.parquet',columns=['trade_date','close'],where='symbol=? AND trade_date<=?',params=[symbol,day])
def _stats(v):
    v=sorted(x for x in v if _num(x));n=len(v)
    return {'sample_count':n,'mean':sum(v)/n if n else None,'median':(v[(n-1)//2]+v[n//2])/2 if n else None,'win_rate':sum(x>0 for x in v)/n if n else None}


def _forward_stats(s,temperatures,day):
    calendar=_days(s);index={d:i for i,d in enumerate(calendar)};end=index.get(day,-1)
    prices={_d(r['trade_date']):r['close'] for r in _prices(s,'000985.CSI',day)}
    groups=defaultdict(list)
    for r in temperatures:
        value=r.get('temperature');start=_d(r['trade_date']);i=index.get(start)
        if not _num(value) or i is None or not prices.get(start):continue
        band='极度恐慌' if value>=80 else '偏冷' if value>=60 else '中性' if value>=40 else '偏热' if value>=20 else '极度乐观'
        for h in (20,60,120):
            if i+h<=end and prices.get(calendar[i+h]):groups[band,h].append(prices[calendar[i+h]]/prices[start]-1)
    return [{'band':band,'horizon':h,**_stats(groups[band,h])} for band in ['极度乐观','偏热','中性','偏冷','极度恐慌'] for h in (20,60,120)]


def _panic(s,temperatures,day):
    episodes=[];active=[]
    for r in temperatures:
        if _num(r.get('temperature')) and r['temperature']>=80:active.append(r)
        elif active:episodes.append(active);active=[]
    if active:episodes.append(active)
    out=[]
    for episode in episodes:
        peak=max(episode,key=lambda r:r['temperature'])
        out.append({'start':episode[0]['trade_date'],'end':episode[-1]['trade_date'],'peak_date':peak['trade_date'],'peak_temperature':peak['temperature'],'sessions':len(episode)})
    return out


def _industry(s,day,level,rows,p):
    data=_extra(s,'industry-daily','SELECT * FROM {t} WHERE trade_date=? AND level=?',[day,level])
    if not data and day>CUTOFF:
        data=_extra(s,'industry-daily',
                    'SELECT * FROM {t} WHERE trade_date=? AND level=?',[CUTOFF,level])
    if not data:
        data=query(s,'ui_industry_daily.parquet','SELECT * FROM {t} WHERE trade_date=? AND level=?',[day,level])
        for r in data:r.update(valuation_percentile=None,ret60=None)
    reports=_extra(s,'industry-profit','SELECT * FROM {t} WHERE as_of_date<=? AND level=?',[day,level])
    latest={}
    for r in reports:
        if r['industry_name'] not in latest or str(r['report_period'])>str(latest[r['industry_name']]['report_period']):latest[r['industry_name']]=r
    # Quarter allocations are observations, not immediate disclosure. Conservative
    # report completion lags avoid leaking future reports into historical dates.
    allocations=[]
    allocation_path='ac_fixed/fund_industry_market.parquet' if Path(s.path('ac_fixed/fund_industry_market.parquet')).exists() else 'local_owners/fund_industry_market.parquet'
    for r in s.read(allocation_path):
        d=Date.fromisoformat(_d(r['report_period']));available=d+timedelta(days=60 if d.month==6 else 120 if d.month==12 else 30)
        if available.isoformat()<=day:allocations.append({**r,'allocation_available':available.isoformat()})
    prepared=_extra(s,'fund-allocation','SELECT * FROM {t} WHERE as_of_date<=? AND level=?',[day,level])
    if prepared:
        allocations=[{**r,'allocation_available':_d(r['as_of_date'])} for r in prepared]
    elif level=='L2':allocations=[]
    allocation={}
    for r in sorted(allocations,key=lambda r:_d(r['report_period'])):allocation[r['industry_name']]=r
    key='industry_l1' if level=='L1' else 'industry_l2';counts=defaultdict(int)
    for r in _filter(rows,{**p,'industry':'all','board':'all'},day):counts[r.get(key,'未知行业')]+=1
    for r in data:
        e=latest.get(r['industry_name'],{});r.update({k:e.get(k) for k in ('earnings_yoy','earnings_change','earnings_sample_count','profit_turn','report_period','as_of_date')})
        a=allocation.get(r['industry_name'],{})
        r.update(overweight=a.get('overweight'),allocation_period=a.get('report_period'),allocation_available=a.get('allocation_available'),candidate_count=counts[r['industry_name']])
        r['contrarian_zone']=r.get('valuation_percentile') is not None and r.get('coldness') is not None and r['valuation_percentile']<30 and r['coldness']>60
    return data


def _risk_summary(rows):
    out=[]
    rules={'audit_light':'保留/否定/无法表示红；强调事项/持续经营黄','cashflow_light':'连续两年经营现金流/归母净利<0.5红，最近一年<0.5黄','cash_debt_light':'现金/短债<0.5红，<1黄','goodwill_light':'商誉/净资产>50%红，>30%黄','receivable_light':'应收增速−营收增速>30pp红，>15pp黄','deducted_loss_light':'扣非净利连续两年亏损红，一年黄','holder_sell_light':'近6月已知大股东减持>3%红，>1%黄','unlock_light':'未来3月已知解禁/流通股>20%红，>10%黄'}
    for k,label in LIGHTS.items():
        valid=[r[k] for r in rows if r.get(k) is not None]
        out.append({'metric':label,'available':len(valid),'red':sum(x==2 for x in valid) if valid else None,'yellow':sum(x==1 for x in valid) if valid else None,'rule':rules[k]})
    return out


def _events(s,day,p):
    rows=query(s,'ui_events.parquet','SELECT * FROM {t} WHERE event_date<=?',[day])
    # Unprepared older versions can still show original events, with end dates
    # mapped from the frozen trading calendar rather than treating them mature.
    if not rows:rows=query(s,'events.parquet','SELECT * FROM {t} WHERE event_date<=?',[day])
    excerpts=_extra(s,'event-summaries',"SELECT event_id,string_agg(DISTINCT summary,' / ') summary FROM {t} WHERE event_date<=? GROUP BY event_id",[day])
    by_event={r['event_id']:r['summary'] for r in excerpts}
    for row in rows:row['summary']=by_event.get(row.get('event_id'))
    rows += _extra(s,'stability-events','SELECT * FROM {t} WHERE event_date<=? AND event_date>=?',[day,(Date.fromisoformat(day)-timedelta(weeks=13)).isoformat()])
    names,groups=_identity(s);days=_days(s);ix={d:i for i,d in enumerate(days)}
    chosen=str(p.get('kind','all'));q=str(p.get('q','')).casefold();out=[]
    for r in rows:
        r['name']=names.get(r['symbol'],r['symbol']);r['industry_name']=groups.get(r['symbol'],{}).get('industry_l1','未知行业');r['kind_label']=KINDS.get(r['kind'],r['kind'])
        if chosen!='all' and r['kind']!=chosen:continue
        if q and q not in (r['symbol']+' '+r['name']+' '+r['industry_name']).casefold():continue
        i=ix.get(_d(r.get('entry_date')))
        for h in (20,60):
            endpoint=_d(r.get('end'+str(h)))
            if not endpoint and i is not None and i+h<len(days):endpoint=days[i+h]
            r['end'+str(h)]=endpoint or None
            if not endpoint or endpoint>day:
                r['excess'+str(h)]=None
                r['forward'+str(h)]=None
        out.append(r)
    return out


def _finance(s,symbol,day):
    # Date-only filings cannot prove availability before market close.
    if day>CUTOFF and Path(s.path('revision/contrarian/daily-financial-inputs.parquet')).is_file():
        records=_refresh_rows(s,'daily-financial-inputs','symbol=? AND trade_date=? AND announce_date<?',[symbol,day,day])
    else:
        records=query(s,'financial_inputs.parquet','SELECT * FROM {t} WHERE symbol=? AND announce_date<? ORDER BY report_period,announce_date',[symbol,day])
    records+=_extra(s,'deducted-events','SELECT * FROM {t} WHERE symbol=? AND announce_date<?',[symbol,day])
    grouped=defaultdict(list)
    for r in records:grouped[r['metric'],r['report_period'],_d(r['announce_date'])].append(r)
    records=[]
    for group in grouped.values():
        row=dict(group[0]);values={r['item_value'] for r in group if r.get('item_value') is not None}
        row['item_value']=next(iter(values)) if len(values)==1 else None
        row['input_conflict']=len(values)>1;records.append(row)
    records.sort(key=lambda r:(r['report_period'],_d(r['announce_date'])))
    latest={}
    for r in records:latest[r['metric']]=r
    return records,[{'financial_metric':{'cash':'现金','short_debt':'短期借款','due_debt':'一年内到期负债','equity':'归母净资产','profit':'归母净利润','ocf':'经营现金流','ar':'应收账款','revenue':'营收','goodwill':'商誉','deducted':'扣非净利润'}.get(k,k),'financial_value':r['item_value'],'report_period':r['report_period'],'announce_date':r['announce_date']} for k,r in latest.items()]



def _historical_financial_lights(records,row):
    """Recompute the five supported accounting lights from visible inputs.

    Announcement-date filtering happens in _finance. No audit, shareholder-trade
    or unlock snapshot is borrowed for a historical observation.
    """
    by={}
    for r in records:by[r['metric'],r['report_period']]=r['item_value']
    periods=sorted({p for _,p in by},reverse=True)
    def same(keys,period=None):
        candidates=[period] if period else periods
        for p in candidates:
            values=[by.get((k,p)) for k in keys]
            if all(_num(v) for v in values):return p,values
        return None,None
    _,values=same(['cash','short_debt','due_debt'])
    if values and values[1]+values[2]>0:
        ratio=values[0]/(values[1]+values[2]);row['cash_debt_ratio']=ratio;row['cash_debt_light']=2 if ratio<.5 else 1 if ratio<1 else 0
    _,values=same(['goodwill','equity'])
    if values and values[1]>0:
        ratio=values[0]/values[1];row['goodwill_ratio']=ratio;row['goodwill_light']=2 if ratio>.5 else 1 if ratio>.3 else 0
    annual=sorted([p for p in periods if str(p).endswith('Q4')],reverse=True)
    if len(annual)>=2:
        ratios=[];losses=[]
        for period in annual[:2]:
            _,vals=same(['ocf','profit'],period)
            ratios.append(vals[0]/vals[1] if vals and vals[1]>0 else None)
            losses.append(by.get(('deducted',period)))
        if all(_num(v) for v in ratios):row['cashflow_light']=2 if all(v<.5 for v in ratios) else 1 if ratios[0]<.5 else 0
        if all(_num(v) for v in losses):row['deducted_loss_light']=2 if all(v<0 for v in losses) else 1 if losses[0]<0 else 0
    current,values=same(['ar','revenue'])
    if current and values:
        previous=str(int(current[:4])-1)+current[4:];_,old=same(['ar','revenue'],previous)
        if old and old[0] and old[1]:
            gap=(values[0]/old[0]-1)-(values[1]/old[1]-1);row['ar_growth_gap']=gap;row['receivable_light']=2 if gap>.3 else 1 if gap>.15 else 0
    valid=[row.get(k) for k in LIGHTS if row.get(k) is not None]
    row.update(lights_available=len(valid),red_count=sum(v==2 for v in valid) if valid else None,yellow_count=sum(v==1 for v in valid) if valid else None)

def _financial_comparisons(records):
    by={}
    for r in records:by[r['metric'],r['report_period']]=r
    periods=sorted({p for _,p in by},reverse=True)
    def val(metric,period):return by.get((metric,period),{}).get('item_value')
    def ann(metric,period):return by.get((metric,period),{}).get('announce_date')
    annual=[]
    for period in [p for p in periods if str(p).endswith('Q4')][:2]:
        ocf,profit,deducted=(val(k,period) for k in ('ocf','profit','deducted'))
        annual.append({'report_period':period,'ocf':ocf,'profit':profit,'deducted':deducted,'cfo_profit_ratio':ocf/profit if _num(ocf) and _num(profit) and profit else None,'ocf_announce_date':ann('ocf',period),'profit_announce_date':ann('profit',period),'deducted_announce_date':ann('deducted',period)})
    balance=[];receivable=[]
    if periods:
        current=periods[0];previous=str(int(current[:4])-1)+current[4:]
        for period in [current,previous]:
            r={'report_period':period,**{k:val(k,period) for k in ('cash','short_debt','due_debt','equity','goodwill','ar','revenue')}}
            dates=[ann(k,period) for k in ('cash','short_debt','due_debt','equity','goodwill','ar','revenue') if ann(k,period)]
            r['inputs_available']=max(dates) if dates else None
            r['cash_debt_ratio']=r['cash']/(r['short_debt']+r['due_debt']) if all(_num(r[k]) for k in ('cash','short_debt','due_debt')) and r['short_debt']+r['due_debt']>0 else None
            r['goodwill_ratio']=r['goodwill']/r['equity'] if _num(r['goodwill']) and _num(r['equity']) and r['equity']>0 else None
            balance.append(r)
        c,p=balance
        ag=c['ar']/p['ar']-1 if _num(c['ar']) and _num(p['ar']) and p['ar'] else None
        rg=c['revenue']/p['revenue']-1 if _num(c['revenue']) and _num(p['revenue']) and p['revenue'] else None
        receivable=[{**r,'ar_growth':ag if i==0 else None,'revenue_growth':rg if i==0 else None,'ar_growth_gap':ag-rg if i==0 and ag is not None and rg is not None else None} for i,r in enumerate(balance)]
    return annual,balance,receivable


def _audit_light(opinion):
    if not opinion:return None
    if any(x in opinion for x in ('否定','无法表示')) or ('保留意见' in opinion and '无保留意见' not in opinion):
        return 2
    if any(x in opinion for x in ('强调事项','持续经营','重大不确定')):return 1
    if '无保留意见' in opinion:return 0
    return None


def _audit_inputs(s,symbol,day):
    # No assumptions about publication time within a session. An event
    # announced on the observation date first appears after that date.
    official=s.path('revision/contrarian/phase3-audit-events.parquet')
    if Path(official).is_file():
        scoped=s.read('revision/contrarian/phase3-audit-events.parquet',
                      where='symbol=? AND announce_date<?',params=[symbol,day])
        older=_extra(s,'audit-inputs',"""SELECT * FROM {t} WHERE symbol=? AND announce_date<?
                     AND (TRY_CAST(report_period AS DATE)<DATE '2023-12-31'
                          OR regexp_extract(CAST(report_period AS VARCHAR),'^[0-9]{4}')<'2023')""",[symbol,day])
        rows=older+scoped
    else:
        rows=_extra(s,'audit-inputs','SELECT * FROM {t} WHERE symbol=? AND announce_date<?',[symbol,day])
    return sorted(rows,key=lambda r:(str(r['report_period']),_d(r['announce_date'])),reverse=True)


def _historical_disclosure_event(s,symbol,day):
    """Surface a dated evidence layer, without feeding unverified vintages into ranks."""
    if not Path(s.path('revision/contrarian/phase3-audit-events.parquet')).is_file():
        return []
    keys=('announcement_date','as_of_date','report_period','fscore_report_period','fscore_proxy',
          'fscore_components_available','fscore_missing_components','lights_available','strict_pit','pit_status')
    for year in range(int(day[:4]),2015,-1):
        rows=query(s,f'history-financial/financial-events-{year}.parquet',
            f"SELECT {','.join(keys)} FROM {{t}} WHERE symbol=? AND as_of_date<=? "
            "AND announcement_date<? ORDER BY as_of_date DESC,announcement_date DESC,report_period DESC LIMIT 1",
            [symbol,day,day])
        if rows:
            return rows
    return []


def _entity(s,p,day):
    symbol=str(p.get('entity_id',p.get('code',''))).upper();rows=_snapshot(s,day);_memberships(s,day,rows)
    selected=[r for r in rows if r['symbol']==symbol]
    if not selected:return [sec('detail',symbol+' · 无该日截面','table',[],BASE,missing='该证券在所选交易日没有冻结因子记录；未以最新日画像替换。')],[]
    r=selected[0];history=[]
    for year in range(2016,int(day[:4])+1):history+=query(s,f'history/factors_{year}.parquet','SELECT trade_date,close,px,bias20,ret20,ret3y,drawdown52,is_stable,pe_ttm,pb FROM {t} WHERE symbol=? AND trade_date<=? ORDER BY trade_date',[symbol,day])
    history+=_refresh_rows(s,'daily-factors','symbol=? AND trade_date<=?',[symbol,day])
    history.sort(key=lambda x:_d(x['trade_date']));shown=_history(history,p)
    if history:
        last=history[-1];streak=0
        for a,b in zip(reversed(history[:-1]),reversed(history[1:])):
            if _num(a.get('px')) and _num(b.get('px')) and b['px']<a['px']:streak+=1
            else:break
        checks={'above_ma20':bool((last.get('bias20') or 0)>0),'bias_ok':last.get('bias20') is not None and last['bias20']>-.05,'decline_ok':streak<=2,'down_streak':streak}
        r.update(checks);r['stability_conditions']=sum(checks[k] for k in ('above_ma20','bias_ok','decline_ok'))
        for key in ['pe_ttm','pb']:
            v=[x[key] for x in history[-756:] if _num(x.get(key))]
            current=history[-1].get(key)
            r[key+'_time_pct3y']=100*(sum(x<current for x in v)+.5*sum(x==current for x in v))/len(v) if len(v)>=500 and _num(current) else None
        median=r.get('pe_ttm_industry_median');r['gap_to_industry_median']=median/r['pe_ttm']-1 if median and r.get('pe_ttm') and r['pe_ttm']>0 else None
    records,financial=_finance(s,symbol,day)
    if day<CUTOFF:_historical_financial_lights(records,r)
    audits=_audit_inputs(s,symbol,day)
    if audits:
        latest=[a for a in audits if a['report_period']==audits[0]['report_period'] and a['announce_date']==audits[0]['announce_date']]
        opinions={a['opinion'] for a in latest}
        r['audit_light']=_audit_light(next(iter(opinions))) if len(opinions)==1 else None
        for a in audits:
            a['audit_conflict']=len({b['opinion'] for b in audits if b['report_period']==a['report_period'] and b['announce_date']==a['announce_date']})>1
        valid=[r[k] for k in LIGHTS if r.get(k) is not None]
        r.update(lights_available=len(valid),red_count=sum(v==2 for v in valid),yellow_count=sum(v==1 for v in valid))
    annual,balance,receivable=_financial_comparisons(records)
    risk=[{'metric':label,'value':r.get(k),'reason':'该日冻结风险结果；空值为未知/不适用' if day>=CUTOFF else '该日已知审计公告及财务复算；减持/解禁尚未重建，不借用最新灯'} for k,label in LIGHTS.items()]
    sections=[sec('detail',r['name']+' · '+day+' · 命中与估值画像','table',[r],BASE+['pe_ttm','pb','pcf_ttm','ev_ebitda','dv_ttm','pe_ttm_discount','pe_ttm_time_pct3y','pb_time_pct3y','gap_to_industry_median','universe_eligible']),sec('stability','企稳三条件 · 满足两项','table',[r],['above_ma20','bias_ok','decline_ok','down_streak','stability_conditions','stable']),sec('lights','八类风险灯 · 0正常/1黄/2红/未知','table',risk,['metric','value','reason']),sec('financial','当时已披露财务输入','table',financial,['financial_metric','financial_value','report_period','announce_date']),sec('history','收盘价格历史','line',shown,['trade_date','close'],x='trade_date',series=[{'key':'close','label':'收盘价'}]),sec('bias','20日乖离与收益','line',shown,['trade_date','bias20','ret20','drawdown52'],x='trade_date',series=[{'key':'bias20','label':'20日乖离'},{'key':'ret20','label':'20日收益'},{'key':'drawdown52','label':'52周回撤'}])]
    sections.extend([
        sec('audit-inputs','当时已知审计意见原文与公告日期','table',audits[:30],['report_period','announce_date','opinion','audit_firm','signing_auditor','audit_conflict','source']),
        sec('annual-financials','最近两个已披露年报 · 现金流/归母净利与扣非对照','table',annual,['report_period','ocf','profit','cfo_profit_ratio','deducted','ocf_announce_date','profit_announce_date','deducted_announce_date']),
        sec('balance-financials','当前报告期及上年同期 · 偿债与商誉输入','table',balance,['report_period','cash','short_debt','due_debt','cash_debt_ratio','goodwill','equity','goodwill_ratio','inputs_available']),
        sec('receivable-comparison','应收与营收同比差原值对照','table',receivable,['report_period','ar','revenue','ar_growth','revenue_growth','ar_growth_gap','inputs_available'])])
    events_asof=_historical_disclosure_event(s,symbol,day)
    if events_asof:
        sections.append(sec('financial-disclosure-evidence','历史财务事件 · 公告时点约束，修订版本未证','table',events_asof,
            ['announcement_date','as_of_date','report_period','fscore_report_period','fscore_proxy',
             'fscore_components_available','lights_available','strict_pit'],
            quality='仅用于核对历史可见性；不回填历史榜单、EV 或正式 F-score。公告当日不视为盘前已知。'))
    roe_history=_extra(s,'roe-events','SELECT * FROM {t} WHERE symbol=? AND as_of_date<=? ORDER BY as_of_date',[symbol,day])
    sections.append(sec('roe-history','历史已披露ROE与五年分位 · 至少8份报告','table',roe_history[-40:],['as_of_date','roe_report_period','roe_latest','roe_sample_count','roe_five_year_pct']))
    fscore=_extra(s,'fscore-inputs','SELECT * FROM {t} WHERE symbol=? ORDER BY fiscal_year',[symbol]) if day==CUTOFF else []
    components=['fiscal_year','score','components_available','positive_roa','positive_cfo','rising_roa','cfo_exceeds_income','lower_long_borrowings_proxy','higher_current_ratio','no_share_increase','higher_gross_margin','higher_asset_turnover','quality_status']
    sections.append(sec('fscore','F-score九项输入 · 长期借款杠杆代理','table',fscore,components,missing='所选历史日没有冻结的同口径F-score九项结果，未借用最新财年评分。'))
    start=str(p.get('holding_start',(_d(history[max(0,len(history)-21)]['trade_date']) if history else day)))[:10];end=min(str(p.get('holding_end',day))[:10],day)
    a=[x for x in history if _d(x['trade_date'])<=start and x.get('px')];b=[x for x in history if _d(x['trade_date'])<=end and x.get('px')]
    holding=[]
    if a and b and _d(a[-1]['trade_date'])<_d(b[-1]['trade_date']):
        prices={_d(x['trade_date']):x['close'] for x in _prices(s,'000985.CSI',day)};sa,sb=_d(a[-1]['trade_date']),_d(b[-1]['trade_date']);ret=b[-1]['px']/a[-1]['px']-1;benchmark=prices[sb]/prices[sa]-1 if prices.get(sa) and prices.get(sb) else None
        holding=[{'holding_start':sa,'holding_end':sb,'price_return':ret,'benchmark_return':benchmark,'excess_return':ret-benchmark if benchmark is not None else None}]
    sections.append(sec('holding','自选持有期价格收益 · 后复权个股相对原始中证全指','table',holding,['holding_start','holding_end','price_return','benchmark_return','excess_return']))
    events=[e for e in _events(s,day,{'q':symbol}) if e['symbol']==symbol and _d(e['event_date'])>=(Date.fromisoformat(day)-timedelta(days=366)).isoformat()]
    sections.append(sec('stock-events','近一年信号及完整期限收益','table',sorted(events,key=lambda r:_d(r['event_date']),reverse=True)[:100],['event_date','kind_label','summary','entry_date','end20','excess20','end60','excess60']))
    return sections,[_input('holding_start','持有期起日',start),_input('holding_end','持有期止日',end)]


DAILY_VALUE_BOARDS={'low_pe_ttm':'pe_ttm','low_pb':'pb','low_ps_ttm':'ps_ttm'}


def _daily_value_rows(s, p, day, metric):
    through=observation(p,cutoff_for(s,'contrarian',CUTOFF))
    if through<=day or Path(s.path('revision/contrarian/daily-factors.parquet')).is_file():
        return []
    values=daily_rows(s,'value-screens',where='metric=? AND trade_date>? AND trade_date<=?',params=[metric,day,through])
    if not values:
        return []
    effective=max(row['trade_date'] for row in values)
    return [row for row in values if row['trade_date']==effective]


def panel(board,panel_id,p,s):
    p=dict(p or {});tab=panel_id;day=_date(p,s,tab);entity=str(p.get('entity_id',p.get('code','')))
    if tab=='entity' and entity.startswith('intersection:'):
        values=entity.split(':',1)[1].split('|');p.update(intersection='|'.join(sorted([values[0],values[-1]])),intersection_a=values[0],intersection_b=values[-1]);tab='candidates'
    if _days(s) and day<_days(s)[0]:
        return {'title':'所选日无可用历史','description':'冻结序列起点'+_days(s)[0],'sections':[sec('before_history','该日未覆盖','table',[])],'controls':_controls(p,tab,day),'warnings':[],'data_date':day,'not_done':NOT_DONE}
    revised=_revised(s)
    title=dict(TABS).get(tab,'个股详情');sections=[];extra_controls=[];warnings=['温度、冷清度和评分为本地研究口径；未知项目保留空值。']
    if tab=='entity':
        sections,extra_controls=_entity(s,p,day)
        through=observation(p,cutoff_for(s,'contrarian',CUTOFF))
        rows=daily_rows(s,'value-screens',where='symbol=? AND trade_date>? AND trade_date<=?',
                        params=[entity.upper(),day,through])
        if rows:
            current=max(r['trade_date'] for r in rows)
            sections.insert(0,sec('daily-valuation','最新估值分项 · 风险证据仍按原日期','table',
                [r for r in rows if r['trade_date']==current],['trade_date','metric','metric_value','valuation_score','group_count','close']))
    else:
        rows=_snapshot(s,day);sets=_memberships(s,day,rows);pool=_filter(rows,p,day)
        temperatures=_temperature(s,day);latest=temperatures[-1] if temperatures else {};temp_history=_history(temperatures,p)
        if tab in ('overview','temperature'):
            keys=['temperature','sentiment','valuation','valid_items','valid_groups','band'] if revised else ['temperature','sentiment','valuation','smart_money','valid_items','valid_groups','band']
            names={**LABELS,**({'temperature':'本地逆向环境指数','sentiment':'市场承压组','valuation':'估值便宜组','band':'环境档位'} if revised else {})}
            sections.append(sec('temperature-kpi','本地逆向环境指数 · 便宜度与市场承压各50%' if revised else '旧版本地逆向温度','kpi',[{'label':names[k],'value':latest.get(k)} for k in keys],['label','value']))
            lines=['temperature','sentiment','valuation'] if revised else ['temperature','sentiment','valuation','smart_money']
            sections.append(sec('temperature','两组历史 · 高分表示便宜且承压，不等于收益预测' if revised else '旧版三组历史','line',temp_history,['trade_date']+lines,x='trade_date',series=[{'key':k,'label':names[k]} for k in lines]))
        if tab=='overview':
            universe=[r for r in rows if r.get('universe_eligible')]
            kpis={'过滤后候选池股票数':len(pool),'候选池命中至少3榜':sum(r['hit_count']>=3 for r in pool),'全资格池已知红灯数':sum((r.get('red_count') or 0)>0 for r in universe),'破净股票':sum(_num(r.get('pb')) and 0<r['pb']<1 for r in universe),'52周回撤至少50%':sum(_num(r.get('drawdown52')) and r['drawdown52']<=-.5 for r in universe)}
            event_rows=_events(s,day,{'kind':'holder_increase_announcement'});cut=(Date.fromisoformat(day)-timedelta(days=183)).isoformat();kpis['近6月产业资本增持股票']=len({r['symbol'] for r in event_rows if _d(r['event_date'])>=cut})
            if day<CUTOFF:kpis['全资格池已知红灯数']=None
            sections.insert(0,sec('kpi','市场扫描 · 以筛选母集计数','kpi',[{'label':k,'value':v} for k,v in kpis.items()],['label','value']))
            industries=_industry(s,day,'L1',rows,p);zones=[r for r in industries if r.get('contrarian_zone')]
            sections[0]['rows'].append({'label':'本地逆向行业数','value':len(zones)})
            sections.append(sec('industry-coordinates','行业估值×冷清度（本地代理）','scatter',industries,['industry_name','trade_date','valuation_percentile','coldness'],x='valuation_percentile',series=[{'key':'coldness','label':'冷清度代理'}]))
            sections.append(sec('contrarian-industries','本地逆向区 · 估值分位<30且冷清度>60','table',zones,['industry_name','trade_date','valuation_percentile','coldness','candidate_count']))
            counts=[{'board':label,'pool_count':len(sets[b])} for b,label in BOARDS.items()];sections.append(sec('counts','十榜入选母集数量 · 非筛选后页数','bar',counts,['board','pool_count'],x='board',series=[{'key':'pool_count','label':'母集入选数'}]))
            dates=sorted(set(_days(s)+[_d(r['trade_date']) for r in _refresh_rows(s,'daily-history',
                'trade_date<=? QUALIFY row_number() OVER(PARTITION BY trade_date ORDER BY symbol)=1',[day])]))
            i=dates.index(day) if day in dates else -1;new=[]
            if i>0:
                # Both dates use the same historical eight-factor proxy, avoiding
                # a false change caused by today's richer finance multipliers.
                current=_snapshot(s,day,True);prior=_snapshot(s,dates[i-1],True)
                for v in (current,prior):v[:]=sorted([r for r in v if r.get('universe_eligible') and _num(r.get('composite_score'))],key=lambda r:r['composite_score'],reverse=True)[:50]
                old={r['symbol'] for r in prior};new=[r for r in current if r['symbol'] not in old]
            sections[0]['rows'].append({'label':'新进综合前50（统一历史因子）','value':len(new)})
            sections.append(sec('new','新进综合前50 · 前后日均采用历史八因子近似，非最新全因子榜','table',new,['symbol','name','industry_name','composite_score'],action={'type':'entity','key':'symbol'}))
        elif tab=='temperature':
            items=_extra(s,'temperature-items','SELECT * FROM {t} WHERE trade_date=? ORDER BY indicator_id',[day])
            sections.append(sec('temperature-items','六项独立指标 · 每项至少三年有效观察' if revised else '旧版11项本地指标明细','table',items,['indicator_id','indicator','group','raw_value','percentile','fear_score','direction','minimum_samples','window_sessions','source_column']))
            bands=[{'band':label,'rule':rule} for label,rule in [('极度恐慌','温度≥80'),('偏冷','60≤温度<80'),('中性','40≤温度<60'),('偏热','20≤温度<40'),('极度乐观','温度<20')]]

            if revised:
                bands=[{'band':b,'rule':r} for b,r in [('低','0≤分数<20'),('较低','20≤分数<40'),('中等','40≤分数<60'),('较高','60≤分数<80'),('高','80≤分数≤100')]]
            sections.append(sec('bands','五档口径','table',bands,['band','rule']))
            sections.append(sec('forward-bands','分档后20/60/120日中证全指价格收益 · 仅观察日前完整期限','table',_forward_stats(s,temperatures,day),['band','horizon','sample_count','mean','median','win_rate']))
            sections.append(sec('panic','高逆向环境连续区间 · 阈值80' if revised else '旧版恐慌连续区间 · 阈值80','table',_panic(s,temperatures,day),['start','end','peak_date','peak_temperature','sessions'],missing='本地温度在观察日前没有达到80的连续区间；不复用原站录制的历史底部温度。'))
            benchmark_day=observation(p,cutoff_for(s,'contrarian',CUTOFF))
            points={_d(r['trade_date']):r for r in _prices(s,'000985.CSI',day)}
            points.update({r['trade_date']:r for r in daily_rows(s,'index-quotes',where='symbol=? AND trade_date>? AND trade_date<=?',
                                                                           params=['000985.CSI',day,benchmark_day])})
            index=_history([points[k] for k in sorted(points)],p)
            sections.append(sec('benchmark','独立中证全指价格历史（仅行情更新）','line',index,['trade_date','close'],x='trade_date',series=[{'key':'close','label':'中证全指'}]))
        elif tab=='industries':
            level=p.get('level','L1');level=level if level in ('L1','L2') else 'L1';data=_industry(s,day,level,rows,p)
            extra_controls += [_select('level','行业层级',[('L1','申万一级'),('L2','申万二级')],level)]
            names=sorted({r['industry_name'] for r in data});selected=p.get('industry',names[0] if names else '')
            extra_controls += [_select('industry','所选行业',[(x,x) for x in names],selected)]
            keys=['industry_name','valuation_percentile','pe_pct_5y','pb_pct_5y','coldness','earnings_yoy','earnings_change','profit_turn','overweight','allocation_period','ret60','candidate_count','stock_count']
            sections.append(sec('coordinates','申万'+('一级' if level=='L1' else '二级')+'估值×冷清度','scatter',data,keys,x='valuation_percentile',series=[{'key':'coldness','label':'冷清度代理'}],action={'type':'params','panel':'industries','params':{'industry':'$industry_name','page':1}}))
            page,pagination=_page(data,p,'valuation_percentile',False);sections.append(sec('industries','行业排名 · 可排序分页','table',page,keys,pagination=pagination,action={'type':'params','panel':'industries','params':{'industry':'$industry_name','page':1}}))
            sections.append(sec('valuation-crowding','所选行业估值×拥挤度','scatter',[r for r in data if r['industry_name']==selected],['industry_name','valuation_percentile','crowding_proxy'],x='valuation_percentile',series=[{'key':'crowding_proxy','label':'拥挤度代理'}]))
            hist=_extra(s,'industry-daily','SELECT * FROM {t} WHERE industry_name=? AND level=? AND trade_date<=? ORDER BY trade_date',[selected,level,day])
            sections.append(sec('industry-history','所选行业估值与冷清度历史','line',_history(hist,p),['trade_date','valuation_percentile','coldness'],x='trade_date',series=[{'key':'valuation_percentile','label':'估值分位'},{'key':'coldness','label':'冷清度代理'}]))
            profit=_extra(s,'industry-profit','SELECT * FROM {t} WHERE industry_name=? AND level=? AND as_of_date<=? ORDER BY report_period',[selected,level,day]);sections.append(sec('earnings','所选行业已披露财报同比与变化 · 所用披露队列保守可得日','table',profit,['report_period','as_of_date','earnings_yoy','earnings_change','earnings_sample_count','profit_turn']))
            stocks=[r for r in pool if r.get('industry_l1' if level=='L1' else 'industry_l2')==selected];page,pg=_page(stocks,p)
            sections.append(sec('industry-candidates','所选行业候选','table',page,BASE+['pe_ttm','pb'],pagination=pg,action={'type':'entity','key':'symbol'}))
            warnings.append('公募超低配使用已披露可见持仓队列，两级行业分别聚合；非认证完整持仓，历史可得日保守按队列最晚公告/法定披露期对齐。')
        elif tab=='candidates':
            chosen=_board(p.get('board','all'));columns=BASE+BOARD_COLUMNS.get(chosen,['pe_ttm','pb','dv_ttm','ret20'])
            metric=DAILY_VALUE_BOARDS.get(chosen)
            compatible=all(p.get(k) in (None,'','all') for k in ('intersection','intersection_a','intersection_b'))
            screen=_daily_value_rows(s,p,day,metric) if metric and compatible and p.get('stable')!='1' else []
            if screen:
                screen_day=screen[0]['trade_date']
                q=str(p.get('q','')).strip().casefold()
                filtered=[r for r in screen if r['valuation_score']>=80
                    and (not q or q in (r['symbol']+' '+r['name']+' '+str(r.get('industry_name') or '')).casefold())
                    and p.get('industry') in (None,'','all',r.get('industry_name'))]
                page,pg=_page(filtered,p,default='valuation_score')
                sections.append(sec('candidates',BOARDS[chosen]+' · '+screen_day+' · '+str(len(filtered))+'只','table',page,
                    ['trade_date','symbol','name','industry_name','metric_value','valuation_score','group_count','close'],
                    pagination=pg,action={'type':'entity','key':'symbol'},
                    quality='已上市满一年、同日成交额前95%且冻结行业组至少10只；按行业内低估值中位秩筛选。无最新风险及企稳信息，交集与其他策略仍用原截面。'))
                warnings.append('本日榜未判定红灯或企稳；只看企稳、交集筛选和组合统计仍按原选股截面。')
            else:
                page,pg=_page(pool,p);sections.append(sec('candidates','候选列表 · '+BOARDS.get(chosen,'综合')+' · '+str(len(pool))+'只','table',page,columns,pagination=pg,action={'type':'entity','key':'symbol'}))
            eligible={r['symbol'] for r in _filter(rows,{**p,'board':'all','intersection':'all','intersection_a':'all','intersection_b':'all'},day)};matrix=[]
            for a in BOARDS:
                for b in BOARDS:
                    aa,bb=sets[a]&eligible,sets[b]&eligible;matrix.append({'board_a':BOARDS[a],'board_b':BOARDS[b],'intersection':len(aa&bb),'union':len(aa|bb),'symbol':'intersection:'+a+'|'+b,'intersection_a':a,'intersection_b':b,'intersection_key':'|'.join(sorted([a,b]))})
            action={'type':'filter','key':'intersection','field':'intersection_key'}
            sections.append(sec('matrix','十榜命中矩阵 · 点击单元格筛选两榜交集','heatmap',matrix,['board_a','board_b','intersection','union'],x='board_a',y='board_b',value='intersection',action=action))
            sections.append(sec('matrix-links','点击某行查看该两榜交集 · 与热图同一母集','table',[r for r in matrix if r['board_a']<=r['board_b']],['board_a','board_b','intersection','union'],action=action))
            distribution=defaultdict(int)
            for r in pool:distribution[r['industry_name']]+=1
            sections.append(sec('distribution','筛选后候选行业分布','bar',[{'industry_name':k,'stock_count':v} for k,v in sorted(distribution.items())],['industry_name','stock_count'],x='industry_name',series=[{'key':'stock_count','label':'股票数'}]))
        elif tab=='traps':
            risk_pool=_filter(rows,{**p,'red':'1'},day);summaries=_risk_summary(risk_pool)
            sections.append(sec('risk','八类风险覆盖、红灯与黄灯','bar',summaries,['metric','available','red','yellow'],x='metric',series=[{'key':'available','label':'有效公司数'},{'key':'red','label':'红灯'},{'key':'yellow','label':'黄灯'}]))
            sections.append(sec('risk-rules','八类判断规则与覆盖','table',summaries,['metric','available','red','yellow','rule']))
            red_rates=[]
            for b,label in BOARDS.items():
                sample=[r for r in rows if r['symbol'] in sets[b]];known=[r for r in sample if r.get('lights_available')]
                red=sum((r.get('red_count') or 0)>0 for r in known);red_rates.append({'board':label,'pool_count':len(sample),'red':red if known else None,'red_ratio':red/len(sample) if sample and known else None,'unknown_risk_count':len(sample)-len(known)})
            sections.append(sec('red-ratios','各榜已知红灯比例 · 未覆盖数量另列','bar',red_rates,['board','pool_count','red','red_ratio','unknown_risk_count'],x='board',series=[{'key':'red_ratio','label':'已知红灯比例'}]))
            page,pg=_page(risk_pool,p,'red_count');sections.append(sec('lights-table','候选池八类灯明细 · 缺失不等于安全','table',page,['symbol','name','industry_name']+list(LIGHTS)+['red_count','yellow_count','lights_available'],pagination=pg,action={'type':'entity','key':'symbol'}))
            cycle=[r for r in rows if r['symbol'] in sets['cycle_reverse']];page,pg=_page(cycle,p)
            sections.append(sec('cycle','周期反向专区 · 当时已知ROE分位与行业净利同比','table',page,BASE+BOARD_COLUMNS['cycle_reverse']+['pe_ttm','pb','goodwill_ratio','cash_debt_ratio'],pagination=pg,action={'type':'entity','key':'symbol'}))
            if day<CUTOFF:warnings.append('所选历史日全市场八类风险灯未批量生成，未覆盖项保留未知；ROE分位及周期候选按当时已知报告计算，不借用最新值。')
        elif tab=='signals':
            events=_events(s,day,p);mode=p.get('by','signal');recent=(Date.fromisoformat(day)-timedelta(days=Date.fromisoformat(day).weekday(),weeks=12)).isoformat();window=[r for r in events if _d(r['event_date'])>=recent]
            kpis=[{'label':'近13周事件数','value':len(window)},{'label':'近13周涉及股票','value':len({r['symbol'] for r in window})},{'label':'已成熟20日样本','value':sum(r.get('excess20') is not None for r in events)},{'label':'已成熟60日样本','value':sum(r.get('excess60') is not None for r in events)}];sections.append(sec('signal-kpi','信号观察','kpi',kpis,['label','value']))
            counts=defaultdict(int);weeks=defaultdict(int)
            for r in window:
                if r['kind']!='stability_transition_proxy':counts[r['kind_label']]+=1
                d=Date.fromisoformat(_d(r['event_date']));weeks[(d-timedelta(days=d.weekday())).isoformat()]+=1
            sections.append(sec('types','近13周公告类型统计 · 企稳代理不纳入类型统计','bar',[{'kind_label':k,'event_count':v} for k,v in sorted(counts.items())],['kind_label','event_count'],x='kind_label',series=[{'key':'event_count','label':'事件数'}]))
            sections.append(sec('weeks','近13周信号数量','bar',[{'event_week':k,'event_count':v} for k,v in sorted(weeks.items())],['event_week','event_count'],x='event_week',series=[{'key':'event_count','label':'事件数'}]))
            if mode=='stock':
                groups=defaultdict(list)
                for r in events:groups[r['symbol']].append(r)
                displayed=[]
                for symbol,group in groups.items():displayed.append({'symbol':symbol,'name':group[0]['name'],'industry_name':group[0]['industry_name'],'event_count':len(group),'kinds':'、'.join(sorted({r['kind_label'] for r in group})),'latest_event':max(_d(r['event_date']) for r in group),'excess20':_stats([r.get('excess20') for r in group])['mean'],'excess60':_stats([r.get('excess60') for r in group])['mean']})
                keys=['symbol','name','industry_name','event_count','kinds','latest_event','excess20','excess60'];sort='latest_event'
            else:displayed=events;keys=['event_date','symbol','name','industry_name','kind_label','summary','entry_date','end20','excess20','end60','excess60'];sort='event_date'
            page,pg=_page(displayed,p,sort);sections.append(sec('events','按股票汇总（超额为该股成熟事件均值）' if mode=='stock' else '按信号事件流','table',page,keys,pagination=pg,action={'type':'entity','key':'symbol'}))
            stats=[]
            for kind,label in KINDS.items():
                if kind=='stability_transition_proxy':continue
                sample=[r for r in events if r['kind']==kind]
                for h in (20,60):stats.append({'kind_label':label,'horizon':h,**_stats([r.get('excess'+str(h)) for r in sample])})
            sections.append(sec('summary','已成熟事件相对中证全指超额收益统计','table',stats,['kind_label','horizon','sample_count','mean','median','win_rate']))
            extra_controls += [_select('kind','信号类别',[('all','全部')]+list(KINDS.items()),p.get('kind','all')),_select('by','信号流方式',[('signal','按信号'),('stock','按股票')],mode),_input('q','代码/名称/行业',p.get('q',''))]
        else:raise ValueError('Unknown contrarian panel: '+str(tab))
    if revised:
        warnings.append('逆向环境指数为独立两组模型v2，不对齐原站47分；六项五年中位秩，每项至少756观察，各组至少两项。个股70/30价值反转代理保留；质量核心输入不全，不另造价值质量总分。')
        for part in sections:
            if part['id'] in ('temperature-kpi','temperature','temperature-items','bands','forward-bands','panic'):
                part.update(formula_id='local_contrarian_environment',formula_version='2.0.0',sample_scope='共同交易日日历；五年窗口；当前分类回看；非严格PIT',coverage={'available':latest.get('valid_items'), 'expected':6,'valid_groups':latest.get('valid_groups')},quality='独立本地模型 · 不追平原站私有分数')
                if part['id']=='forward-bands':
                    labels={'极度乐观':'低','偏热':'较低','中性':'中等','偏冷':'较高','极度恐慌':'高'}
                    for row in part['rows']:row['band']=labels.get(row.get('band'),row.get('band'))
            elif part['id'] in ('kpi','counts','candidates','lights-table'):
                part.update(formula_id='local_value_reversal_proxy',formula_version='1.1-coverage',sample_scope='本地资格池；行业内价值70%与反转30%；未知风险不等于通过全部风控')
    if day<CUTOFF:warnings.append('历史财报风险、EV及F-score覆盖不完整；当前行业分类回看，不能将未知理解为已通过风控。')
    return {'title':title,'description':'观察日 '+day+'；温度、拥挤度和评分为本地研究口径。','sections':sections,'controls':_controls(p,tab,day)+extra_controls,'warnings':warnings,'data_date':day,'not_done':NOT_DONE}
