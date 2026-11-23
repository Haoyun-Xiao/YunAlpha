#!/usr/bin/env python3
"""Build independent daily dashboard versions from published formal inputs."""
from datetime import date, datetime, timedelta
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import tomllib
from zoneinfo import ZoneInfo

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from seven_desks.daily_inputs import DailyInputs, etf_flows, market_money, official_valuation, quote_tables, stock_flows, stock_valuation, value_screens, literal
from seven_desks.settings import RUNTIME
from seven_desks.store import code_hashes


def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f,'sha256').hexdigest()


def atomic_json(path, data):
    tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)+'\n')
    tmp.replace(path)


def clone(src,dst):
    dst.parent.mkdir(parents=True,exist_ok=True)
    try:
        subprocess.run(['cp','-c',str(src),str(dst)],check=True,capture_output=True)
    except subprocess.CalledProcessError:
        shutil.copy2(src,dst)


def identity(file):
    return json.loads(file.read_text())


def status_info(seven, sources, target, crowding):
    frozen='2026-11-09'
    def group(name, old=None, reason='正式数据未达到发布门槛'):
        p=sources.get('revision/daily/'+name+'.parquet')
        if not p:
            return {'status':'retained','data_date':old,'reason':reason}
        return {'status':'current' if p['data_date']==str(target) else 'lagged',
                **p, 'reason':None if p['data_date']==str(target) else '只更新到来源最近已公布交易日'}
    def hold(period,reason):
        return {'status':'retained','data_date':period,'reason':reason}
    def index(symbol):
        info=sources.get('revision/daily/index-quotes.parquet',{})
        actual=info.get('symbol_dates',{}).get(symbol)
        return {'status':'current' if actual==str(target) else 'lagged','data_date':actual,
                'reason':None if actual==str(target) else '指数日线分项较晚'} if actual else hold(
                    frozen,'该指数无更新日线，其他指数日期不可替代')
    return {
        'valuation': {'stock_structure':group('valuation-structure',seven['valuation_cutoff']),
            'official_index_pe_pb':{'status':'current' if seven['valuation_cutoff']==str(target) else 'lagged',
                'data_date':seven['valuation_cutoff'],'reason':None if seven['valuation_cutoff']==str(target) else '正式指数尚无连续完整新截面；不使用代理'},
            'dividends_and_historical_statistics':hold(frozen,'仍用已发布历史口径')},
        'capital_flow': {'market_main_native':group('capital-market-native',reason='东方财富大盘源暂不可用'),
            'main_money':group('capital-market',frozen),'stock_main_windows':group('capital-stock',frozen,'未达到个股主力样本门槛'),
            'etf':group('etf-flow',frozen),'margin_cross_dragon_balance':hold(frozen,'完整分项口径未重算，源日各自保留')},
        'national_team': {'etf':group('etf-flow',frozen),'index_prices':index('000300.SH'),
            'holdings':hold('2026-09-30','报告期持仓与账户身份沿用核验快照'), 'portfolio_temperature':hold(frozen,'影子组合与托底信号未重算')},
        'fund_holdings': {'stock_quotes':group('stock-quotes',frozen),
            'holdings':hold('2026-06-30','半年报/年报全持仓，季度前十大不替代'),
            'company_nav':hold(frozen,'公司级NAV权重与可得日未重算')},
        'chips': {'stock_quotes':group('stock-quotes',frozen),'cost_and_signals':hold(frozen,'正式筹码成本无新的同口径版本')},
        'sentiment': {'market_main20':group('sentiment-money',frozen),
            'temperature':hold(frozen,'六维指标未完整重算；辅助主力净流入不计入温度')},
        'contrarian': {'benchmark_index':index('000985.CSI'),'valuation_screens':group('value-screens',frozen),
            'factors_temperature_financials':hold(frozen,'综合分、复权反转、PCF/EV、温度及历史财务不借用新行情日期')},
        'crowding': {'core':hold(crowding.get('quotes_date','2026-11-13'),'抱团完整日更由独立核心构建负责，当前入口保留已选版'),
            'portfolio':hold(crowding['holding_period'],'全持仓和抱团风险沿用已验证版本'),
            'top10_and_fund_scale':hold(crowding['report_period'],'按报告期更新，独立于行情'),
            'interval_returns_and_risk':hold('2026-11-13','原收益起点、风险与持仓估值仍使用相同冻结行情')},
    }


def merge_history(x, base, bound):
    for rel, meta in x.output.items():
        if not rel.startswith('revision/daily/'):
            continue
        previous=base/'data'/rel
        if not previous.is_file():
            continue
        date_col='trade_date'
        current=x.staging/rel
        dest=current.with_suffix('.merged')
        p,c=map(literal,(previous,current))
        last=x.db.execute(f'SELECT max({date_col}) FROM read_parquet({p})').fetchone()[0]
        if last>bound:
            raise ValueError('不允许把已发布分项回退到更早目标日：'+rel)
        # All producers emit a complete source-date slice. Keep old dates and
        # don't mutate earlier versions when a supplier republishes a date.
        cols={r[0] for r in x.db.execute(f'DESCRIBE SELECT * FROM read_parquet({c})').fetchall()}
        sort=[k for k in ('trade_date','symbol','bucket','metric','board_id') if k in cols]
        union=(f'SELECT * FROM read_parquet({p}) WHERE trade_date NOT IN '
               f'(SELECT DISTINCT trade_date FROM read_parquet({c})) UNION ALL BY NAME SELECT * FROM read_parquet({c})')
        x.db.execute(f'COPY ({union} ORDER BY {",".join(sort)}) TO {literal(dest)} (FORMAT PARQUET,COMPRESSION ZSTD)')
        dest.replace(current)
        n,a,b=x.db.execute(f'SELECT count(*),min(trade_date),max(trade_date) FROM read_parquet({c})').fetchone()
        meta.update(rows=n,first_date=str(a),data_date=str(b))


def verify_serving(version, root):
    from seven_desks import server
    manifest=identity(root/'manifest.json')
    server.verify_algorithm(version)
    for rel,digest in manifest['daily_outputs'].items():
        if sha(root/'data'/rel)!=digest:
            raise ValueError('日更候选文件摘要错误：'+rel)
    if (root/'daily-verified.json').is_file():
        return identity(root/'daily-verified.json')
    routes = [(board, tab['id']) for board in server.BOARDS
              for tab in server.metadata(board, version)['tabs']]
    results={}
    for board,tab in routes:
        params={**server.metadata(board, version).get('defaults', {}), 'data_version':version}
        if tab=='stock':
            params['code']='600519.SH'
        if (board,tab)==('contrarian','candidates'):
            params['board']='low_pb'
        payload=json.loads(server.response(board,tab,params))
        if payload.get('data_version')!=version or not payload.get('sections') or any(
            section['id']=='version_input_missing' for section in payload['sections']):
            raise ValueError(f'候选接口校验失败：{board}/{tab}')
        active=manifest['daily_boards'][board]
        if payload.get('daily_components')!=active:
            raise ValueError(f'候选未返回分项日期：{board}/{tab}')
        results[f'{board}/{tab}']=len(payload['sections'])
    atomic_json(root/'daily-verified.json',results)
    return results


def seven_version(base, out, stage, sources, boards, valuation_cutoff):
    parent=identity(base/'manifest.json')
    code=code_hashes()
    outputs={rel:sha(stage/rel) for rel in sources}
    builders = {str(p.relative_to(APP)): sha(p) for p in sorted(
        [APP/'scripts/refresh_daily.py', APP/'seven_desks/daily_inputs.py',
         APP/'seven_desks/market_refresh.py', APP/'seven_desks/owners_refresh.py',
         *(APP/'seven_desks/builders').glob('*.py')])}
    if not outputs or (parent.get('daily_outputs')==outputs and dict(parent['code_hashes'])==code
                       and parent.get('daily_boards')==boards and parent.get('builder_hashes')==builders
                       and parent.get('valuation_cutoff')==valuation_cutoff):
        return parent['data_version'],base,False
    version=hashlib.sha256(json.dumps([parent['data_version'],outputs,code,builders,boards,valuation_cutoff],sort_keys=True).encode()).hexdigest()[:16]
    root=out/'seven/versions'/version
    if root.is_dir():
        manifest=identity(root/'manifest.json')
        if manifest.get('daily_outputs')!=outputs:
            raise ValueError('七台同名候选与内容不符')
        return version,root,False
    root.parent.mkdir(parents=True,exist_ok=True)
    with TemporaryDirectory(dir=root.parent,prefix='.daily-') as tm:
        draft=Path(tm)/version
        for p in sorted((base/'data').rglob('*')):
            if p.is_symlink():
                raise ValueError('母版不得依赖链接')
            if p.is_file() and str(p.relative_to(base/'data')) not in sources:
                clone(p,draft/p.relative_to(base))
        for rel in sources:
            clone(stage/rel,draft/'data'/rel)
        daily={**parent,'data_version':version,'base_version':parent['data_version'],'code_hashes':code,
               'builder_hashes':builders,'daily_outputs':outputs,'daily_files':sources,'daily_boards':boards,'valuation_cutoff':valuation_cutoff,'snapshot_type':'bounded_daily_refresh'}
        atomic_json(draft/'manifest.json',daily)
        draft.rename(root)
    return version,root,True


def check_version(base, version):
    if not re.fullmatch('[0-9a-f]{16}',version) or identity(base/'manifest.json').get('data_version',version)!=version:
        raise ValueError('已有候选版本与清单不符')


def refresh_start(manifest, bound):
    """Revisit the last published observations, including gaps after a stopped runner."""
    dates = [date.fromisoformat(manifest.get('valuation_cutoff', '2026-11-09'))]
    dates.extend(date.fromisoformat(item['data_date']) for rel, item in manifest.get('daily_files', {}).items()
                 if rel.startswith('revision/daily/') and item.get('data_date'))
    return min(min(dates), bound) - timedelta(days=14)


def main():
    from seven_desks.market_refresh import refresh as refresh_market
    from seven_desks.owners_refresh import refresh as refresh_owners
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lake-config',type=Path,required=True)
    parser.add_argument('--base-runtime',type=Path,required=True)
    parser.add_argument('--seven-version',required=True)
    parser.add_argument('--crowding-version',required=True)
    parser.add_argument('--as-of',type=date.fromisoformat,default=datetime.now(ZoneInfo('Asia/Shanghai')).date())
    a=parser.parse_args()
    out=RUNTIME/'phase2/vic_desks'
    daily=out/'daily'
    daily.mkdir(parents=True,exist_ok=True)
    result={'status':'failed','requested_upper_date':str(a.as_of),'candidates':{
        'seven':{'version':a.seven_version},'crowding':{'version':a.crowding_version}}}
    with (daily/'build.lock').open('a') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({**result,'error':'另一次日更正在运行'},ensure_ascii=False))
            return 1
        try:
            if any(not re.fullmatch('[a-f0-9]{16}',v) for v in (a.seven_version,a.crowding_version)):
                raise ValueError('无效版本号')
            parent=a.base_runtime.resolve()/'phase2/vic_desks'
            seven=parent/'seven/versions'/a.seven_version
            crowd=parent/'crowding/versions'/a.crowding_version
            check_version(seven,a.seven_version)
            crowd_manifest=identity(crowd/'manifest.json')
            if crowd_manifest['version']!=a.crowding_version:
                raise ValueError('公募抱团母版版本不符')
            if 'metadata' in crowd_manifest:
                crowd_manifest={**crowd_manifest,**crowd_manifest['metadata'],
                    'report_period':crowd_manifest['fund_scale_period'],
                    'quotes_date':crowd_manifest['metadata']['market_date']}
            seven_manifest=identity(seven/'manifest.json')
            root=Path(tomllib.loads(a.lake_config.read_text())['data']['root']).expanduser()
            if not root.is_absolute() or not (root/'curated').is_dir():
                raise ValueError('请提供正式湖配置的绝对 data.root')
            with TemporaryDirectory(dir=daily,prefix='inputs-') as tmp:
                stage=Path(tmp)
                x=DailyInputs(root.resolve(),a.as_of,stage)
                try:
                    first=refresh_start(seven_manifest,a.as_of)
                    target=x.calendar(start=first-timedelta(days=180))
                    quote_tables(x,first)
                    stock_valuation(x,first)
                    value_screens(x,seven/'data')
                    stock_flows(x,first)
                    market_money(x,first)
                    etf_flows(x,first,seven/'data')
                    cutoff=official_valuation(x,seven/'data',seven_manifest['valuation_cutoff'])
                    extensions = [refresh_market(x,seven/'data'), refresh_owners(x,seven/'data')]
                    cutoff = extensions[0].get('valuation_cutoff') or cutoff
                    x.verify()
                    merge_history(x,seven,target)
                    for rel,status in seven_manifest.get('daily_files',{}).items():
                        if rel not in x.output:
                            clone(seven/'data'/rel,stage/rel)
                            x.output[rel]=status
                    x.verify()
                    boards=status_info({**seven_manifest,'valuation_cutoff':cutoff},x.output,target,crowd_manifest)
                    for extension in extensions:
                        for board, components in extension.get('boards',{}).items():
                            boards.setdefault(board,{}).update(components)
                    if 'capital_balance' in boards.get('capital_flow',{}):
                        boards['capital_flow'].pop('margin_cross_dragon_balance',None)
                    if 'shadow_portfolio' in boards.get('national_team',{}):
                        boards['national_team'].pop('portfolio_temperature',None)
                    if 'factor_boards' in boards.get('contrarian',{}):
                        boards['contrarian'].pop('factors_temperature_financials',None)
                    sv,sp,sb=seven_version(seven,out,stage,x.output,boards,cutoff)
                    validation=verify_serving(sv,sp) if sp!=seven else {}
                    cv,cp,cb=a.crowding_version,crowd,False
                finally:
                    x.db.close()
            result.update(status='ready' if sv!=a.seven_version or cv!=a.crowding_version else 'unchanged',target_trading_date=str(target),
                          candidates={'seven':{'version':sv,'path':str(sp),'created':sb},
                                      'crowding':{'version':cv,'path':str(cp),'created':cb}},boards=boards,validated=validation)
        except Exception as error:
            result['error']=f'{type(error).__name__}: {error}'
        atomic_json(daily/'latest.json',result)
    print(json.dumps(result,ensure_ascii=False,sort_keys=True))
    return int(result['status']=='failed')


if __name__=='__main__':
    raise SystemExit(main())
