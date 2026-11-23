from seven_desks.settings import CACHE as BUILD_ROOT, LAKE, SOURCE as REVIEW_SOURCE, FROZEN_VERSION
from seven_desks.store import version_root, connect
"""Read-only preparation of additional contrarian UI research views.

Reads a bound frozen input version and the formal SW index daily source. Writes
only output/seven-desks/contrarian-extra; a parent builder freezes this directory
as contrarian/ui-extra. No publication, ingestion or formal-lake modification.
"""
from pathlib import Path
import hashlib
import json
import duckdb
import numpy as np
import pandas as pd
from seven_desks.store import CACHE, Store
from seven_desks.settings import SOURCE

OUT = CACHE / 'contrarian-extra'
CUTOFF = '2026-11-09'
# These are explicit local research proxies, not an invented claim that the
# original site's unpublished eleven-item list or backend was recovered.
SPECS = [
 ('S1','站上250日线占比','sentiment','D3',True),
 ('S2','涨停减跌停','sentiment','D6',True),
 ('S3','融资余额20日变化','sentiment','B3',True),
 ('S4','两市换手率5日','sentiment','C1',True),
 ('S5','上证A股年线偏离','sentiment','F1',True),
 ('V1','沪深300 PE','valuation','A1',True),
 ('V2','沪深300 PB','valuation','A2',True),
 ('V3','全A PE中位数','valuation','A3',True),
 ('M1','宽基ETF净申购20日','smart_money','E2',False),
 ('M2','产业资本净增持20日','smart_money','E3',False),
 ('M3','偏股基金股票仓位','smart_money','E4',True),
]


def rank(series, window=1260, minimum=500):
    def score(v):
        x=v[-1]
        v=v[np.isfinite(v)]
        return 100*((v<x).sum()+.5*(v==x).sum())/len(v) if np.isfinite(x) else np.nan
    return pd.to_numeric(series,errors='coerce').rolling(window,min_periods=minimum).apply(score,raw=True)


def build(version=None):
    version=version or json.loads((CACHE/'active.json').read_text())['data_version']
    s=Store(version);OUT.mkdir(parents=True,exist_ok=True)
    c=connect(config={'threads':2,'memory_limit':'1GB'})
    c.execute(f"SET temp_directory='{str(OUT/'spill').replace(chr(39),chr(39)*2)}'")
    frozen=[]
    def table(rel):
        p=Path(s.path(rel));frozen.append(rel)
        return "read_parquet('"+str(p).replace("'","''")+"',hive_partitioning=false,union_by_name=true)"
    def write(frame,name):
        target=OUT/name;tmp=target.with_suffix('.tmp.parquet');frame.to_parquet(tmp,index=False);tmp.replace(target);print(name,len(frame),flush=True)
    try:
        raw=pd.DataFrame(s.read('market/sentiment/daily.parquet'));raw.trade_date=pd.to_datetime(raw.trade_date);raw=raw.sort_values('trade_date')
        details=[];daily=raw[['trade_date']].copy();groups={k:[] for k in ['sentiment','valuation','smart_money']}
        for key,label,group,column,reverse in SPECS:
            values=pd.to_numeric(raw[column],errors='coerce');pct=rank(values);fear=100-pct if reverse else pct
            daily[key]=fear;groups[group].append(key)
            details.append(pd.DataFrame({'trade_date':raw.trade_date,'indicator_id':key,'indicator':label,'group':group,'raw_value':values,'percentile':pct,'fear_score':fear,'direction':'低值更恐慌' if reverse else '高值更恐慌','window_sessions':1260,'minimum_samples':500,'source_column':column,'quality':'explicit_local_proxy_not_original_eleven_definition'}))
        for group,keys in groups.items():daily[group]=daily[keys].mean(axis=1)
        daily['valid_items']=daily[[x[0] for x in SPECS]].notna().sum(axis=1)
        weights={'sentiment':.5,'valuation':.3,'smart_money':.2}
        denominator=sum(daily[g].notna()*w for g,w in weights.items());numerator=sum(daily[g].fillna(0)*w for g,w in weights.items())
        daily['valid_groups']=daily[list(weights)].notna().sum(axis=1)
        daily['unsmoothed_temperature']=(numerator/denominator).where((daily.valid_items>=4)&(daily.valid_groups>=2))
        daily['temperature']=daily.unsmoothed_temperature.rolling(5,min_periods=5).mean()
        daily['band']=pd.cut(daily.temperature,[-np.inf,20,40,60,80,np.inf],right=False,labels=['极度乐观','偏热','中性','偏冷','极度恐慌']).astype('object')
        write(daily,'temperature.parquet');write(pd.concat(details,ignore_index=True),'temperature-items.parquet')
        # Official SW index valuations, not stock median PE/PB disguised as an
        # index. Current taxonomy is explicitly the same classification as site.
        tax=pd.DataFrame(s.read('contrarian/ui_industry_taxonomy.parquet'))
        taxonomy=c.execute(f"SELECT index_symbol,industry_code,industry_name,level,parent_industry_code FROM {table('support/industry_taxonomy/*.parquet')} WHERE classification_system='SW2021' AND level IN ('L1','L2')").df()
        tax_rows={r['industry_code']:r for r in taxonomy.to_dict('records')}
        def financial(code):
            seen=set()
            while code in tax_rows and code not in seen:
                seen.add(code);r=tax_rows[code]
                if r['level']=='L1':return r['industry_name'] in ('银行','非银金融')
                code=r['parent_industry_code']
            return False
        taxonomy['financial_sector']=taxonomy.industry_code.map(financial)
        sw=SOURCE.parents[2]/'curated/sw_daily'
        formal_files=[{'path':str(p),'bytes':p.stat().st_size,'mtime_ns':p.stat().st_mtime_ns} for p in sorted(sw.rglob('*.parquet'))]
        c.register('taxonomy',taxonomy)
        index=c.execute(f"SELECT d.index_symbol,d.trade_date,t.industry_code,t.industry_name,t.level,t.financial_sector,min(d.pe) pe,min(d.pb) pb,min(d.close) AS close FROM read_parquet('{sw}/**/*.parquet',hive_partitioning=false,union_by_name=true) d JOIN taxonomy t USING(index_symbol) WHERE d.trade_date<='{CUTOFF}' GROUP BY ALL HAVING count(DISTINCT struct_pack(pe:=d.pe,pb:=d.pb,c:=d.close))=1 ORDER BY index_symbol,trade_date").df()
        pieces=[]
        for _,g in index.groupby('index_symbol',sort=False):
            g=g.copy();g['pe_pct_5y']=rank(g.pe);g['pb_pct_5y']=rank(g.pb)
            g['valuation_percentile']=g.pb_pct_5y if g.financial_sector.iloc[0] else g[['pe_pct_5y','pb_pct_5y']].mean(axis=1)
            g['ret60']=g.close.pct_change(60,fill_method=None);pieces.append(g)
        industries=pd.concat(pieces,ignore_index=True);industries.trade_date=pd.to_datetime(industries.trade_date)
        crowd=pd.DataFrame(s.read('contrarian/ui_industry_daily.parquet'));crowd.trade_date=pd.to_datetime(crowd.trade_date)
        industries=industries.merge(crowd[['trade_date','industry_name','level','coldness','crowding_proxy','amount_share','stock_count']],on=['trade_date','industry_name','level'],how='left')
        industries['contrarian_zone']=(industries.valuation_percentile<30)&(industries.coldness>60)
        industries=industries[industries.trade_date>=pd.Timestamp('2016-01-01')]
        write(industries,'industry-daily.parquet')
        # Conservative report cohorts: each aggregate becomes visible only on
        # the latest announcement used in that cohort; never expose it earlier.
        profit=c.execute(f"SELECT symbol,report_period,announce_date,min(item_value) profit FROM {table('contrarian/financial_inputs.parquet')} WHERE metric='profit' AND announce_date<='{CUTOFF}' GROUP BY 1,2,3 HAVING count(DISTINCT item_value)=1").df()
        profit.announce_date=pd.to_datetime(profit.announce_date)
        c.register('profit',profit)
        growth=c.execute("SELECT a.symbol,a.report_period,a.announce_date,(a.profit-b.profit)/nullif(abs(b.profit),0) yoy FROM profit a ASOF LEFT JOIN (SELECT symbol,cast(cast(substr(report_period,1,4) AS INT)+1 AS VARCHAR)||substr(report_period,5) next_period,announce_date,profit FROM profit) b ON a.symbol=b.symbol AND a.report_period=b.next_period AND a.announce_date>=b.announce_date").df()
        stocks=pd.DataFrame(s.read('contrarian/ui_factors.parquet',columns=['symbol','industry_l1','industry_l2']))
        growth=growth.merge(stocks,on='symbol',how='left').sort_values('announce_date').drop_duplicates(['symbol','report_period'],keep='last')
        profit_groups=[]
        for level,key in [('L1','industry_l1'),('L2','industry_l2')]:
            g=growth.groupby([key,'report_period'],dropna=False).agg(earnings_yoy=('yoy','median'),earnings_sample_count=('yoy','count'),as_of_date=('announce_date','max')).reset_index().rename(columns={key:'industry_name'})
            g['level']=level;g=g.sort_values(['industry_name','report_period']);g['earnings_change']=g.groupby('industry_name').earnings_yoy.diff()
            g['profit_turn']=np.where(g.earnings_change>0.05,'改善',np.where(g.earnings_change<-.05,'恶化','持平'));g.loc[g.earnings_change.isna(),'profit_turn']=None
            profit_groups.append(g)
        write(pd.concat(profit_groups,ignore_index=True),'industry-profit.parquet')
        # Latest stock trailing-three-year valuation midranks; historical entity
        # views independently compute the same bounded single-stock operation.
        h=table('contrarian/history/factors_*.parquet')
        values=c.execute(f"SELECT symbol,trade_date,pe_ttm,pb FROM {h} WHERE trade_date>=DATE '{CUTOFF}'-INTERVAL '3 years' AND trade_date<=DATE '{CUTOFF}' ORDER BY symbol,trade_date").df()
        pct=[]
        for symbol,g in values.groupby('symbol',sort=False):
            row={'symbol':symbol,'as_of_date':CUTOFF}
            for key in ['pe_ttm','pb']:
                a=pd.to_numeric(g[key],errors='coerce').dropna().tail(756).to_numpy();x=g.iloc[-1][key] if len(g) and str(g.iloc[-1].trade_date)[:10]==CUTOFF else np.nan
                row[key+'_time_pct3y']=100*((a<x).sum()+.5*(a==x).sum())/len(a) if len(a)>=500 and pd.notna(x) else None
            pct.append(row)
        write(pd.DataFrame(pct),'stock-percentiles.parquet')
        # Reaggregate the visible fund holdings to both classification levels.
        # Keep the cohort's latest disclosure date in the denominator as well as
        # numerator; never use a future full cohort to compute a historical weight.
        holdings=table('owners/company_stock_holdings.parquet')
        context=table('owners/period_stock_context.parquet')
        mapping=pd.DataFrame(s.read('contrarian/ui_factors.parquet',columns=['symbol','industry_l1','industry_l2']))
        c.register('stock_mapping',mapping)
        allocations=[]
        for level,key in [('L1','industry_l1'),('L2','industry_l2')]:
            allocation=c.execute(f"""WITH h AS (
              SELECT h.report_period,coalesce(m.{key},'未知行业') industry_name,sum(h.market_value) market_value,
                max(h.latest_announce) latest_announce
              FROM {holdings} h LEFT JOIN stock_mapping m USING(symbol)
              WHERE h.latest_announce<='{CUTOFF}' AND month(h.report_period) IN (6,12) GROUP BY 1,2),
            total AS (SELECT report_period,sum(market_value) total_value,max(latest_announce) latest_announce FROM h GROUP BY 1),
            market AS (SELECT report_period,coalesce(m.{key},'未知行业') industry_name,sum(float_shares*close) float_value
              FROM {context} p LEFT JOIN stock_mapping m USING(symbol) GROUP BY 1,2),
            market_total AS (SELECT report_period,sum(float_value) total_float FROM market GROUP BY 1)
            SELECT h.report_period,h.industry_name,h.market_value,h.market_value/nullif(t.total_value,0) weight,
              m.float_value/nullif(mt.total_float,0) market_weight,
              greatest(t.latest_announce,h.report_period+INTERVAL 1 DAY*(CASE WHEN month(h.report_period)=6 THEN 60 ELSE 120 END)) as_of_date
            FROM h JOIN total t USING(report_period) LEFT JOIN market m USING(report_period,industry_name)
            LEFT JOIN market_total mt USING(report_period)""").df()
            allocation['overweight']=allocation.weight-allocation.market_weight;allocation['level']=level
            allocation['disclosure_scope']='visible_company_stock_holdings_not_certified_full_market;current_classification'
            allocations.append(allocation)
        write(pd.concat(allocations,ignore_index=True),'fund-allocation.parquet')
        # Preserve real same-day factor inputs, rather than reverse-engineering
        # OCF or enterprise value from the displayed factor ratio.
        extra_factors={}
        for key,rel in [('pcf','derived/pcf_ttm_candidate'),('ev','derived/ev_ebitda_ttm_candidate'),('fscore','derived/fscore_candidate')]:
            directory=SOURCE.parents[2]/rel
            formal_files += [{'path':str(p),'bytes':p.stat().st_size,'mtime_ns':p.stat().st_mtime_ns} for p in sorted(directory.rglob('*.parquet'))]
            extra_factors[key]=c.execute(f"SELECT * FROM read_parquet('{directory}/**/*.parquet',hive_partitioning=false,union_by_name=true) WHERE as_of_date=DATE '{CUTOFF}'").df()
        pcf=extra_factors['pcf'][['symbol','as_of_date','market_value_cny','operating_cash_flow_ttm_cny','quality_status']].rename(columns={'operating_cash_flow_ttm_cny':'ocf_ttm_cny','quality_status':'pcf_input_quality'})
        ev=extra_factors['ev'][['symbol','net_debt_cny','ebitda_ttm_cny','ev_cny','quality_status']].rename(columns={'quality_status':'ev_input_quality'})
        if pcf.symbol.duplicated().any() or ev.symbol.duplicated().any():raise ValueError('Conflicting factor-input primary keys')
        write(pcf.merge(ev,on='symbol',how='outer'),'factor-inputs.parquet')
        write(extra_factors['fscore'],'fscore-inputs.parquet')
        # Additional local stability transitions are labelled as historical
        # research proxies, never claimed to be the site's since-launch feed.
        features=table('contrarian/price_features.parquet')
        index_prices=table('market/ui-extra/index-prices.parquet')
        stable_sql=f"""WITH benchmark AS (
          SELECT trade_date,lead(trade_date,20) OVER w end20,lead(trade_date,60) OVER w end60,
            lead(close,20) OVER w/close-1 benchmark20,lead(close,60) OVER w/close-1 benchmark60
          FROM {index_prices} WHERE symbol='000985.CSI' WINDOW w AS(ORDER BY trade_date)),
        transitions AS (SELECT *,lag(is_stable) OVER(PARTITION BY symbol ORDER BY trade_date) was_stable
          FROM {features} WHERE trade_date<='{CUTOFF}')
        SELECT p.symbol||':stable:'||cast(p.trade_date AS VARCHAR) event_id,p.symbol,p.trade_date event_date,
          p.trade_date entry_date,'stability_transition_proxy' kind,
          p.forward20-b.benchmark20 excess20,p.forward60-b.benchmark60 excess60,b.end20,b.end60,
          'local_historical_stability_transition;same_close_event_study;not_original_since_launch_feed' quality_status
        FROM transitions p LEFT JOIN benchmark b USING(trade_date)
        WHERE p.is_stable AND p.was_stable=false AND p.trade_date>='2016-01-01'
        ORDER BY event_date,symbol"""
        stable_file=OUT/'stability-events.tmp.parquet'
        c.execute(f"COPY ({stable_sql}) TO '{stable_file}' (FORMAT PARQUET,COMPRESSION ZSTD)")
        stable_file.replace(OUT/'stability-events.parquet')
        print('stability-events.parquet',c.execute('SELECT count(*) FROM read_parquet(?)',[str(OUT/'stability-events.parquet')]).fetchone()[0],flush=True)
        files=[{'relative':str(p.relative_to(OUT)),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(OUT.glob('*.parquet'))]
        (OUT/'manifest.json').write_text(json.dumps({'code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'input_version':version,'cutoff':CUTOFF,'frozen_inputs':frozen+['market/sentiment/daily.parquet','contrarian/ui_industry_daily.parquet','contrarian/ui_factors.parquet'],'formal_read_only_inputs':formal_files,'outputs':files,'temperature_definition':'Eleven explicit local proxies; not an assertion that original private eleven items were recovered; 1260/500 inclusive midrank, 50/30/20 available group reweight then complete 5-day smoothing','industry_definition':'Official current SW index PE/PB five-year ranks; coldness uses local amount-share/return percentile proxy; not original crowding composite','profit_definition':'Parent net-profit same-period prior-year growth; conservative whole report cohort available at max used announce_date; current classification; not strict financial revision PIT','stock_pct_definition':'last756 finite observations in trailing3 calendar years, minimum500; inclusive midrank'},ensure_ascii=False,indent=2))
    finally:
        c.close()
        spill=OUT/'spill'
        if spill.exists() and not any(spill.iterdir()):spill.rmdir()





def build_history(version=None):
    """Cython rolling ranks in bounded symbol buckets; no O(N*window) loop."""
    import shutil
    version=version or json.loads((CACHE/'active.json').read_text())['data_version']
    store=Store(version);OUT.mkdir(parents=True,exist_ok=True)
    c=connect(config={'threads':2,'memory_limit':'1GB'})
    scratch=OUT/'history-tmp';scratch.mkdir(parents=True, exist_ok=True)
    c.execute(f"SET temp_directory='{scratch/'spill'}'")
    history=store.path('contrarian/history/factors_*.parquet')
    inputs=[]
    try:
        for bucket in range(10):
            frame=c.execute("SELECT symbol,trade_date,pe_ttm,pb FROM read_parquet(?,hive_partitioning=false,union_by_name=true) WHERE right(split_part(symbol,'.',1),1)=? AND trade_date<=? ORDER BY symbol,trade_date",[history,str(bucket),CUTOFF]).df()
            frame.trade_date=pd.to_datetime(frame.trade_date)
            for column in ['pe_ttm','pb']:
                values=pd.to_numeric(frame[column],errors='coerce').replace([np.inf,-np.inf],np.nan)
                group=values.groupby(frame.symbol,sort=False)
                ranks=group.transform(lambda a:a.rolling(756,min_periods=500).rank(method='average'))
                counts=group.transform(lambda a:a.rolling(756,min_periods=1).count())
                frame[column+'_time_pct3y']=(ranks-.5)/counts*100
                frame[column+'_samples3y']=counts.astype('int16')
            frame=frame.drop(columns=['pe_ttm','pb'])
            frame.to_parquet(scratch/f'bucket-{bucket}.parquet',index=False)
            print('historical valuation bucket',bucket,len(frame),flush=True)
        for year in range(2016,2027):
            tmp=OUT/f'valuation-history-{year}.tmp.parquet'
            c.execute("COPY (SELECT * FROM read_parquet(?,hive_partitioning=false) WHERE year(trade_date)=? ORDER BY symbol,trade_date) TO '"+str(tmp)+"' (FORMAT PARQUET,COMPRESSION ZSTD)",[str(scratch/'bucket-*.parquet'),year])
            tmp.replace(OUT/f'valuation-history-{year}.parquet')
        lake=SOURCE.parents[2]
        for rel in ['curated/audit_opinions','curated/stock_financial_indicators']:
            inputs += [{'path':str(p),'bytes':p.stat().st_size,'mtime_ns':p.stat().st_mtime_ns} for p in sorted((lake/rel).rglob('*.parquet'))]
        audits=c.execute(f"SELECT DISTINCT symbol,report_period,announce_date,opinion,audit_firm,signing_auditor FROM read_parquet('{lake}/curated/audit_opinions/**/*.parquet',hive_partitioning=false,union_by_name=true) WHERE announce_date<='{CUTOFF}' ORDER BY symbol,report_period,announce_date").df()
        audits.to_parquet(OUT/'audit-inputs.parquet',index=False);print('audit-inputs',len(audits),flush=True)
        deducted=c.execute(f"SELECT symbol,report_period,announce_date,min(profit_dedt) item_value FROM read_parquet('{lake}/curated/stock_financial_indicators/**/*.parquet',hive_partitioning=false,union_by_name=true) WHERE announce_date<='{CUTOFF}' AND profit_dedt IS NOT NULL GROUP BY 1,2,3 HAVING count(DISTINCT profit_dedt)=1").df()
        deducted['report_period']=pd.to_datetime(deducted.report_period).dt.to_period('Q').astype(str)
        deducted['metric']='deducted'
        deducted.to_parquet(OUT/'deducted-events.parquet',index=False);print('deducted-events',len(deducted),flush=True)
        roe=c.execute(f"SELECT symbol,report_period,announce_date,min(roe) roe FROM read_parquet('{lake}/curated/stock_financial_indicators/**/*.parquet',hive_partitioning=false,union_by_name=true) WHERE announce_date<='{CUTOFF}' AND roe IS NOT NULL AND report_period<=announce_date GROUP BY 1,2,3 HAVING count(DISTINCT roe)=1 ORDER BY symbol,announce_date,report_period").df()
        roe.report_period=pd.to_datetime(roe.report_period);roe.announce_date=pd.to_datetime(roe.announce_date)
        events=[]
        for symbol,group in roe.groupby('symbol',sort=False):
            visible={}
            for day,announcements in group.groupby('announce_date',sort=False):
                for row in announcements.itertuples():visible[row.report_period]=row.roe
                latest=max(visible);value=visible[latest];start=latest-pd.DateOffset(years=5)
                sample=np.asarray([v for period,v in visible.items() if start<period<=latest and np.isfinite(v)])
                score=100*((sample<value).sum()+.5*(sample==value).sum())/len(sample) if len(sample)>=8 else np.nan
                events.append({'symbol':symbol,'as_of_date':day,'roe_report_period':latest,'roe_latest':value,'roe_sample_count':len(sample),'roe_five_year_pct':score})
        pd.DataFrame(events).to_parquet(OUT/'roe-events.parquet',index=False);print('roe-events',len(events),flush=True)
        manifest=OUT/'manifest.json';data=json.loads(manifest.read_text()) if manifest.exists() else {}
        data.update(code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),history_input_version=version,history_definition='756 observed security sessions inclusive rolling average rank minus0.5, divided by finite count; minimum500; Pandas Cython rolling.rank per symbol bucket',roe_definition='Latest report known on each announce_date; latest visible version per report; trailing5 report calendar years, inclusive midrank, minimum8 reports; not strict financial restatement PIT')
        data['formal_read_only_inputs']=data.get('formal_read_only_inputs',[])+inputs
        data['outputs']=[{'relative':str(p.relative_to(OUT)),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(OUT.glob('*.parquet'))]
        manifest.write_text(json.dumps(data,ensure_ascii=False,indent=2))
    finally:
        c.close();shutil.rmtree(scratch,ignore_errors=True)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--history-only',action='store_true');args=parser.parse_args()
    if not args.history_only:build()
    build_history()
