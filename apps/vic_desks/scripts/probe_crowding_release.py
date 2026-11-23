#!/usr/bin/env python3
"""HTTP contract/performance acceptance against one pinned published generation."""
import argparse,json,math,time,urllib.request,urllib.parse,urllib.error
from pathlib import Path


def percentile(values,p):
    return sorted(values)[max(0,math.ceil(len(values)*p)-1)] if values else None


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:19222/workspaces/crowding');parser.add_argument('--output',required=True)
    args=parser.parse_args();records=[]
    def request(path):
        start=time.perf_counter()
        with urllib.request.urlopen(args.url+path,timeout=60) as response:
            data=json.load(response)
        records.append({'path':path,'ms':round((time.perf_counter()-start)*1000,3)})
        return data
    health=request('/api/crowding/health');version=health['active_version'] or health['cache_version']
    before=health.get('snapshot_build_count')
    first_detail=request(f'/api/dashboards/fund_crowding/stocks/000001.SZ?start=hp&data_version={version}')
    assert first_detail['code']==0
    first_process_detail_ms=records[-1]['ms']
    pages=[]
    for repeat in range(5):
        for page in ('overview','stocks','funds','industries','crowd_index'):
            for start in ('hp','qe','d5','d20'):
                data=request(f'/api/dashboards/fund_crowding/{page}?start={start}&data_version={version}')
                assert data['code']==0,(page,data)
                assert data['data']['data_version']==version,(page,'version mixed')
                pages.append(records[-1]['ms'])
    first=request(f'/api/dashboards/fund_crowding/stocks?view=held&page_size=500&data_version={version}')['data']['table']
    stocks=list(first['rows'])
    for page in range(2,first['pages']+1):
        stocks.extend(request(f'/api/dashboards/fund_crowding/stocks?view=held&page_size=500&page={page}&data_version={version}')['data']['table']['rows'])
    names=['000001.SZ','300308.SZ','300502.SZ']
    for pick in (lambda r:r.get('fin_chg30_pct') is None,lambda r:r.get('top5_pct') is not None,
                 lambda r:r.get('top10_cover_pct') is not None,lambda r:r.get('off_high_pct') is None,
                 lambda r:r.get('score') is None,lambda r:not r.get('pool')):
        selected=[r['code'] for r in stocks if pick(r) and r['code'] not in names]
        names.extend(selected[:2])
    if len(names)<20:
        candidates=[r['code'] for r in stocks if r['code'] not in names]
        stride=max(1,len(candidates)//(20-len(names)))
        names.extend(candidates[::stride])
    names=names[:20];cold=[];warm=[]
    for code in names:
        path=f'/api/dashboards/fund_crowding/stocks/{code}?start=hp&data_version={version}'
        data=request(path);assert data['code']==0;assert data['data']['code']==code;cold.append(records[-1]['ms'])
        request(path);warm.append(records[-1]['ms'])
    # The first stock in the local no-holding sample must remain selectable.
    none=request(f'/api/dashboards/fund_crowding/stocks?view=none&data_version={version}')['data']['table']['rows']
    if none:
        code=none[0]['code'];query=urllib.parse.quote(code[:6])
        search=request(f'/api/dashboards/fund_crowding/stock-search?q={query}&data_version={version}')
        assert any(r['code']==code for r in search['data']['rows'])
        assert request(f'/api/dashboards/fund_crowding/stocks/{code}?data_version={version}')['code']==0
    sort_fields=['code','name','l1_name','score','n_funds','n_funds_chg','n_corps','hold_mv_h','hold_mv_now','fr_pct',
        'fr_chg_pct','fr_free_pct','top5_pct','passive_pct','liq_days','adv_amount','turnover5_pct','ret_pct','dd_pct',
        'off_high_pct','fin_chg30_pct','mf30_pct','pe3y','pb3y','top10_chg_pct','top10_chg_mv']
    for field in sort_fields:
        for direction in ('asc','desc'):
            page=request(f'/api/dashboards/fund_crowding/stocks?view=held&sort={field}&order={direction}&page_size=500&data_version={version}')['data']['table']
            values=[r[field] for r in page['rows'] if r.get(field) is not None]
            assert values==sorted(values,reverse=direction=='desc'),('sort',field,direction)
    for view in ('pool','high','held','none','crowded_expensive','top10_up','top10_down'):
        assert request(f'/api/dashboards/fund_crowding/stocks?view={view}&data_version={version}')['code']==0
    empty=request(f'/api/dashboards/fund_crowding/stocks?q=not-a-real-stock&data_version={version}')
    assert empty['data']['table']['n']==0
    try:request('/api/dashboards/fund_crowding/overview?data_version=doesnotexist')
    except urllib.error.HTTPError as exc:assert exc.code==409
    else:raise AssertionError('invalid generation accepted')
    after=request('/api/crowding/health');assert after.get('snapshot_build_count')==before
    report={'version':version,'stock_sample':names,'page_p95_ms':percentile(pages,.95),'detail_first_key_p95_ms':percentile(cold,.95),
      'detail_warm_p95_ms':percentile(warm,.95),'first_process_detail_ms':first_process_detail_ms,'detail_first_key_max_ms':max(cold),'page_target_passed':percentile(pages,.95)<300,
      'detail_target_passed':percentile(cold,.95)<1000,'snapshot_builds_before':before,'snapshot_builds_after':after.get('snapshot_build_count'),
      'checks':['pinned versions','four starts','five repeated rounds','20 stratified first-key details','unheld search/detail','empty search','409 invalid version','26 fields asc/desc sorting','seven views','no full snapshot rebuild'],
      'records':records,'cold_definition':'fresh per-stock detail key in this acceptance; shared market/holder inputs precomputed, API and first request may have loaded them'}
    output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!='records'},ensure_ascii=False))
    return 0 if report['page_target_passed'] and report['detail_target_passed'] else 1

if __name__=='__main__':raise SystemExit(main())
