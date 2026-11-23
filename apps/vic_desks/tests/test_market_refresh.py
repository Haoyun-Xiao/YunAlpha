"""The daily builders keep existing formulas and the separate source clocks."""
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import shutil

import numpy as np
import pandas as pd
import pytest

from seven_desks.builders.market_daily_chips import apply_chip_cooldown
from seven_desks.builders.market_daily_valuation import completed_month_forward_returns
from seven_desks.daily_inputs import DailyInputs
from seven_desks.market_refresh import refresh
from seven_desks.store import Store


def test_chip_cooldown_resumes_at_native_session_boundary():
    # A washout on seq=101 is suppressed by the prior version's seq=100;
    # equality at 110 is accepted. Never accept a gap as yesterday's chip.
    f = pd.DataFrame(dict(symbol=['A']*3,seq=[101,110,120],prev_seq=[100,109,118],
                          listing_sessions=[400]*3,history_count=[252]*3,
                          winner_rate=[2]*3,wr20=[40]*3,close=[10]*3))
    for col in ('prev95','prev_close','prev_con90','prev_wr','concentration90',
                'cost_deviation','position_1y','wr10','wr5','prev5'):
        f[col] = np.nan
    result = apply_chip_cooldown(f,{('A','washout'):100})
    assert result.washout.tolist() == [False, True, False]


def test_price_return_uses_complete_calendar_month_endpoint():
    f = pd.DataFrame({'index_symbol':['A']*3,
                      'trade_date':['2025-10-31','2025-11-28','2026-10-30'],'close':[100,101,130]})
    result = completed_month_forward_returns(f,'2026-11-16')
    assert result.forward_12m_annualized.iloc[0] == pytest.approx(.3)
    assert pd.isna(result.forward_12m_annualized.iloc[1])
    partial = completed_month_forward_returns(f,'2026-10-30')
    assert pd.isna(partial.forward_12m_annualized.iloc[0])


class VersionView(Store):
    """Read candidate replacements over the accepted immutable parent."""
    def __init__(self, base, stage, result):
        super().__init__('0000000000000000')
        self.root, self.stage = base, stage
        self.daily_boards = result['boards']
        self.valuation_cutoff = result['valuation_cutoff']

    def path(self, rel):
        staged = self.stage/rel
        return str(staged if staged.is_file() else self.root/rel)


def run(lake, base, stage):
    stage.mkdir()
    x = DailyInputs(lake,date(2026,11,16),stage)
    try:
        x.calendar()
        result = refresh(x,base)
        x.verify()
        assert set(result['outputs'])==set(x.output)
        assert x.files and all(path.is_relative_to(lake) for path in x.files)
        return result
    finally:
        x.db.close()


@pytest.mark.skipif(not (os.environ.get('MARKET_REFRESH_LAKE') and os.environ.get('MARKET_REFRESH_BASE')),
                    reason='requires accepted real lake and seven-desk base')
def test_real_market_refresh_and_same_day_rebuild(tmp_path):
    from seven_desks.adapters import contrarian, market

    lake = Path(os.environ['MARKET_REFRESH_LAKE'])
    base = Path(os.environ['MARKET_REFRESH_BASE'])
    stages = [tmp_path/name for name in ('first','repeat')]
    first, second = (run(lake,base,stage) for stage in stages)
    assert first==second
    def digests(stage):
        return {rel:hashlib.sha256((stage/rel).read_bytes()).hexdigest() for rel in first['outputs']}
    assert digests(stages[0])==digests(stages[1])
    s = VersionView(base,stages[0],first)
    board = first['boards']
    assert board['chips']['cost_and_signals']['data_date']=='2026-11-09'
    assert board['sentiment']['temperature']['data_date']=='2026-11-16'
    assert board['contrarian']['temperature']['data_date']=='2026-11-09'
    assert board['contrarian']['factor_boards']['data_date']=='2026-11-16'
    assert first['valuation_cutoff']=='2026-11-13'
    assert board['valuation']['dividends_and_historical_statistics']['data_date']=='2026-11-16'
    sent = market.panel('sentiment','indicators',{'date':'2026-11-16'},s)
    assert sent['data_date']=='2026-11-16'
    indicators = next(p['rows'] for p in sent['sections'] if p['id']=='indicators')
    by_id = {r['indicator_id']:r for r in indicators}
    assert len(by_id)==23
    assert by_id['C2']['source_date'][:10]=='2026-11-16'
    assert by_id['F1']['raw_value'] is None
    prior = pd.read_parquet(base/'market/sentiment/daily.parquet')
    got = pd.read_parquet(stages[0]/'market/sentiment/daily.parquet')
    old_day = pd.Timestamp(prior.trade_date.max())
    assert got.loc[got.trade_date==old_day,'temperature'].item()==prior.iloc[-1].temperature
    v = market.panel('valuation','overview',{'date':'2026-11-16'},s)
    assert any(section['id']=='map' and section['rows'] for section in v['sections'])
    c = contrarian.panel('contrarian','candidates',{'date':'2026-11-16','board':'high_dividend'},s)
    assert c['data_date']=='2026-11-16'
    candidates = next(p['rows'] for p in c['sections'] if p['id']=='candidates')
    assert candidates and all(r['dv_ttm']>=3 and r['dividend_score']>=80 for r in candidates)
    assert contrarian.panel('contrarian','temperature',{'date':'2026-11-16'},s)['data_date']=='2026-11-09'
    # The selected candidate is also a usable incremental parent.
    new_base = tmp_path/'candidate'/'data'
    new_base.mkdir(parents=True)
    for p in base.rglob('*'):
        if p.is_file():
            dest = new_base/p.relative_to(base)
            dest.parent.mkdir(parents=True,exist_ok=True)
            os.link(p,dest)
    for rel in first['outputs']:
        dest = new_base/rel
        dest.unlink(missing_ok=True)
        dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(stages[0]/rel,dest)
    (new_base.parent/'manifest.json').write_text(json.dumps({'daily_boards':first['boards']}))
    same = run(lake,new_base,tmp_path/'same-parent')
    assert same['outputs']==[]
    assert same['boards']==first['boards']
    assert same['valuation_cutoff']==first['valuation_cutoff']


def compose_candidate(base,stage,result,destination):
    destination.mkdir(parents=True)
    for source in base.rglob('*'):
        if source.is_file():
            target = destination/source.relative_to(base)
            target.parent.mkdir(parents=True,exist_ok=True)
            os.link(source,target)
    for rel in result['outputs']:
        target = destination/rel
        target.unlink(missing_ok=True)
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(stage/rel,target)
    (destination.parent/'manifest.json').write_text(json.dumps({'daily_boards':result['boards']}))
    return destination


def test_replay_replaces_intermediate_date_and_reuses_unchanged_inputs(tmp_path,monkeypatch):
    import seven_desks.market_refresh as module
    lake,base = tmp_path/'lake',tmp_path/'original/data'
    base.mkdir(parents=True)
    dataset = lake/'curated/test_inputs'
    dataset.mkdir(parents=True)
    raw = dataset/'part.parquet'
    pd.DataFrame({'trade_date':pd.to_datetime(['2026-11-10','2026-11-12','2026-11-16']),
                  'value':[10.,np.nan,16.]}).to_parquet(raw,index=False)
    rel = 'market/test/daily.parquet'
    (base/rel).parent.mkdir(parents=True)
    pd.DataFrame({'trade_date':pd.to_datetime(['2026-11-09']),'value':[9.]}).to_parquet(base/rel,index=False)
    calls=[]
    def compute(x,original):
        calls.append(True)
        x.view('test_inputs',date(2026,11,10),day='trade_date')
        data=x.db.execute('SELECT * FROM test_inputs ORDER BY trade_date').df()
        old=module.read(original/rel)
        # This is intentionally the same append-only interface as the builders;
        # the wrapper must supply the original anchor, not previous outputs.
        module.write(x,rel,pd.concat([old,data]),['trade_date'])
        return {'boards':{'sentiment':{'temperature':{'data_date':'2026-11-16'}}},
                'outputs':[rel],'valuation_cutoff':'2026-11-13'}
    monkeypatch.setattr(module,'_refresh',compute)
    def build(source,name):
        stage=tmp_path/name
        stage.mkdir()
        x=DailyInputs(lake,date(2026,11,16),stage)
        x.days=[date(2026,11,16)]
        try:
            result=module.refresh(x,source)
            x.verify()
            return result,stage
        finally:
            x.db.close()
    first,s1=build(base,'first')
    current=compose_candidate(base,s1,first,tmp_path/'candidate/data')
    same,_=build(current,'same')
    assert same['outputs']==[] and len(calls)==1
    x=DailyInputs(lake,date(2026,11,13),tmp_path/'older')
    x.days=[date(2026,11,13)]
    try:
        with pytest.raises(ValueError,match='更早交易日'):
            module.refresh(x,current)
    finally:
        x.db.close()
    # A same-key value is corrected after this candidate is published.
    pd.DataFrame({'trade_date':pd.to_datetime(['2026-11-10','2026-11-12','2026-11-16']),
                  'value':[10.,12.,16.]}).to_parquet(raw,index=False)
    repaired,s2=build(current,'repaired')
    expected,s3=build(base,'expected')
    pd.testing.assert_frame_equal(pd.read_parquet(s2/rel),pd.read_parquet(s3/rel))
    assert pd.read_parquet(s2/rel).value.tolist()==[9.,10.,12.,16.]
    newer=compose_candidate(current,s2,repaired,tmp_path/'newer/data')
    # The replay is self-contained even after the original version is gone.
    shutil.rmtree(base.parent)
    unchanged,_=build(newer,'unchanged')
    assert unchanged['outputs']==[] and len(calls)==3
    # Addition of a previously absent partition also invalidates the fingerprint.
    missing=dataset/'trade_date=2026-11-13'
    missing.mkdir()
    pd.DataFrame({'trade_date':pd.to_datetime(['2026-11-13']),'value':[13.]}).to_parquet(
        missing/'part.parquet',index=False)
    extended,s4=build(newer,'extended')
    assert pd.read_parquet(s4/rel).value.tolist()==[9.,10.,12.,13.,16.]
    assert len(calls)==4


@pytest.mark.skipif(not (os.environ.get('MARKET_REFRESH_LAKE') and os.environ.get('MARKET_REFRESH_BASE')),
                    reason='requires real market data')
def test_real_late_middle_day_matches_original_rebuild(tmp_path):
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    lake = Path(os.environ['MARKET_REFRESH_LAKE'])
    base = Path(os.environ['MARKET_REFRESH_BASE'])
    # Private test lake. Only the selected partitions are writable copies; the
    # other dataset directories stay read-only references to formal inputs.
    local = tmp_path/'lake'
    datasets = ('curated/daily_bars','derived/adj_factors','curated/stock_daily_research_inputs')
    for layer in ('curated','derived'):
        (local/layer).mkdir(parents=True)
        for folder in (lake/layer).iterdir():
            rel = folder.relative_to(lake)
            if str(rel) not in datasets:
                (local/rel).symlink_to(folder,target_is_directory=True)
            else:
                (local/rel).mkdir()
                for partition in folder.iterdir():
                    (local/rel/partition.name).symlink_to(partition,target_is_directory=partition.is_dir())
    db=__import__('duckdb').connect()
    # The recovered real 11-12 day exercises the missing-day gate and its
    # dependent windows without touching the formal lake.
    counts={}
    for day in ('2026-11-12','2026-11-11'):
        bars=lake/'curated/daily_bars'/('trade_date='+day)
        count=db.execute("SELECT count(DISTINCT symbol) FROM read_parquet(?,hive_partitioning=false)",
                         [[str(p) for p in bars.rglob('*.parquet')]]).fetchone()[0]
        counts[day]=count
    missing_day = '2026-11-12'
    assert counts[missing_day]>=5000, 'requires recovered formal 11-12 stock bars'
    saved=[]
    for ds in datasets:
        folder=local/ds
        if ds.endswith('stock_daily_research_inputs'):
            partition=folder/'trade_date=2026-11'
            key='trade_date'
        else:
            partition=folder/('trade_date='+missing_day)
            key='trade_date'
        source=partition.resolve()
        partition.unlink()
        partition.mkdir()
        original=db.read_parquet([str(p) for p in source.rglob('*.parquet')],hive_partitioning=False,
                                  union_by_name=True).arrow().read_all()
        destination=partition/'part.parquet'
        keep=pc.not_equal(original[key],pa.scalar(date.fromisoformat(missing_day),type=original[key].type))
        pq.write_table(original.filter(keep),destination)
        saved.append((destination,original))
    db.close()
    first=run(local,base,tmp_path/'first')
    current=compose_candidate(base,tmp_path/'first',first,tmp_path/'candidate/data')
    for path,original in saved:
        pq.write_table(original,path)
    repaired=run(local,current,tmp_path/'repaired')
    expected=run(local,base,tmp_path/'expected')
    s2,s3=tmp_path/'repaired',tmp_path/'expected'
    expected_business=[r for r in expected['outputs'] if not r.startswith('revision/market/replay/')]
    assert repaired['boards']==expected['boards']
    assert repaired['valuation_cutoff']==expected['valuation_cutoff']
    for rel in expected_business:
        assert (s2/rel).read_bytes()==(s3/rel).read_bytes(),rel
    before=pd.read_parquet(tmp_path/'first/revision/contrarian/daily-factors.parquet')
    after=pd.read_parquet(s2/'revision/contrarian/daily-factors.parquet')
    assert missing_day not in pd.to_datetime(before.trade_date).dt.strftime('%Y-%m-%d').unique()
    assert missing_day in pd.to_datetime(after.trade_date).dt.strftime('%Y-%m-%d').unique()
    # Replaying did not alter the historical prefix.
    old=pd.read_parquet(base/'market/sentiment/daily.parquet')
    new=pd.read_parquet(s2/'market/sentiment/daily.parquet')
    new=new.loc[pd.to_datetime(new.trade_date)<=pd.Timestamp(old.trade_date.max())]
    pd.testing.assert_frame_equal(old.reset_index(drop=True).assign(trade_date=pd.to_datetime(old.trade_date)),
                                  new.reset_index(drop=True),check_dtype=False)
    newer=compose_candidate(current,s2,repaired,tmp_path/'newer/data')
    unchanged=run(local,newer,tmp_path/'unchanged')
    assert unchanged['outputs']==[]


def test_pre_receipt_candidate_follows_original_version_once(tmp_path):
    from seven_desks.market_refresh import _original_market_base, REPLAY_STATE
    versions=tmp_path/'versions'
    old=versions/'1111111111111111/data'
    current=versions/'2222222222222222/data'
    old.mkdir(parents=True)
    marker=current/'revision/market/valuation-daily-rebuilt.json'
    marker.parent.mkdir(parents=True)
    marker.write_text('{}')
    (current.parent/'manifest.json').write_text(json.dumps({'base_version':old.parent.name}))
    assert _original_market_base(current)==old
    # A carried replay anchor removes the need to find any sibling version.
    receipt=current/REPLAY_STATE
    receipt.parent.mkdir(parents=True)
    receipt.write_text('{}')
    shutil.rmtree(old.parent)
    assert _original_market_base(current)==current
