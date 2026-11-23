"""Read bounded official data into local institution revisions; assemble a new immutable candidate."""
import argparse
from collections import defaultdict
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

from seven_desks.settings import CACHE, CUTOFF, RUNTIME
from seven_desks.store import code_hashes, connect

BASE_VERSION = '249854a814731c52'
H1, Q3 = date(2026, 6, 30), date(2026, 9, 30)
SECTIONS = ('owners', 'contrarian')


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _local(path):
    if not path.resolve().is_relative_to(RUNTIME):
        raise ValueError('只允许写当前工作树的运行目录')


def _publish_tables(stage, output):
    if output.exists():
        raise FileExistsError(output)
    stage.rename(output)


def prepare(base_root, lake_config, output):
    from multi_asset_lake.config import load_config
    from multi_asset_lake.query import load
    from crowding.verified_holdings import _read_published_relations
    import pyarrow as pa
    import pyarrow.parquet as pq
    import polars as pl

    _local(output)
    cfg = load_config(lake_config)
    d = base_root / 'data'
    conn = connect()
    try:
        groups = conn.execute("""
            SELECT u.company_id, c.master_code, list(c.fund_symbol ORDER BY c.fund_symbol) shares
            FROM read_parquet(?) c JOIN read_parquet(?) u ON u.symbol=c.fund_symbol
            WHERE c.report_period=? AND c.ac_coverage='verified_identical_portfolio'
              AND c.master_code IS NOT NULL AND u.active_equity
            GROUP BY 1,2 HAVING count(*)=count(DISTINCT c.fund_symbol)
                AND count(*)>1 AND min(c.equal_funds)=count(*) AND max(c.equal_funds)=count(*)
            """, [str(d/'ac_fixed/fund_share_period_coverage.parquet'), str(d/'owners/active_fund_universe.parquet'), H1]).fetchall()
        per_master = defaultdict(list)
        for company, master, shares in groups:
            per_master[master].append((company, shares))
        as_of = date.fromisoformat(CUTOFF)
        known, rejected = _read_published_relations(
            cfg.data_root, as_of=as_of, report_period=H1, master_codes=sorted(per_master),
            knowledge_cutoff=datetime.fromisoformat(CUTOFF+'T23:59:59+08:00'),
        )
        bad = {row['master_code'] for row in rejected}
        receipt = defaultdict(list)
        for row in known:
            if row['master_code'] in per_master and row['master_code'] not in bad:
                receipt[row['master_code']].append(row)
        scale_path = cfg.data_root/'derived/fund_scale/part-merged.parquet'
        scale_hash = digest(scale_path)
        scales = defaultdict(list)
        for row in load('fund_scale', start=H1, end=H1, config=cfg).filter(
                (pl.col('report_period')==H1) & (pl.col('src')=='eastmoney_gmbd')).iter_rows(named=True):
            if row['scale_yi'] is not None and 0 < row['scale_yi'] < float('inf'):
                scales[row['symbol']].append(row['scale_yi'])
        if digest(scale_path) != scale_hash:
            raise ValueError('基金规模在读取期间变化')

        selected = []
        for master, items in per_master.items():
            if len(items)!=1 or master not in receipt:
                continue
            company, shares = items[0]
            proof = receipt[master]
            verified = sorted(r['share_code']+'.OF' for r in proof)
            if shares != verified or master+'.OF' not in verified or any(len(scales[s])!=1 for s in shares):
                continue
            for share in shares:
                selected.append({'master_code':master, 'company_id':company, 'fund_symbol':share,
                                 'share_asset_cny':round(scales[share][0]*1e8, 2),
                                 'observation_id':proof[0]['observation_id']})
        if not selected:
            raise ValueError('冻结持仓与同季正式份额规模没有可靠的交集')
        conn.register('selected_share', pa.Table.from_pylist(selected))
        products = conn.execute("""
            WITH p AS (
                SELECT s.master_code,s.company_id,sum(s.share_asset_cny) sample_assets_cny,
                       count(*)::INTEGER share_count,min(observation_id) observation_id
                FROM selected_share s GROUP BY 1,2
            ), h AS (
                SELECT s.master_code, count(DISTINCT h.holding_symbol)::INTEGER stock_count,
                       sum(h.corrected_market_value) sample_a_stock_value_cny
                FROM read_parquet(?) h JOIN selected_share s USING(fund_symbol)
                WHERE h.report_period=? AND h.announce_date<=? AND h.ac_coverage='verified_identical_portfolio'
                  AND regexp_full_match(h.holding_symbol,'[0-9]{6}[.](SH|SZ|BJ)')
                GROUP BY 1
            )
            SELECT ?::DATE report_period,p.*,h.* EXCLUDE(master_code),
                   h.sample_a_stock_value_cny/nullif(p.sample_assets_cny,0) AS sample_holding_ratio
            FROM p JOIN h USING(master_code) WHERE h.sample_a_stock_value_cny>0
            ORDER BY p.company_id,p.master_code
            """, [str(d/'ac_fixed/fund_holdings_corrected.parquet'), H1, as_of, H1]).to_arrow_table()
        filters = [pl.col(x).is_not_null() for x in ('record_date','announce_date','holder_rank','holder_name')]
        holders = load('top_holders', start=H1, end=Q3, as_of=as_of,
                       all_vintages=True, config=cfg).filter(
            pl.col('record_date').is_in([H1,Q3]), pl.col('holder_scope')=='float',
            pl.col('holder_rank').is_between(1,10), *filters,
        ).select('symbol','record_date','announce_date','holder_rank','holder_name','holding_shares',
                 'holding_pct','source','data_version').unique().sort('record_date','symbol','announce_date','holder_rank')

        audits = load('audit_opinions', start='2023-12-31', end='2025-12-31',
                      as_of=as_of, all_vintages=True, config=cfg).filter(
            pl.col('report_period').dt.month()==12, pl.col('announce_date').is_not_null(),
            pl.col('opinion').is_not_null(), pl.col('source')=='tushare',
        ).select('symbol','report_period','announce_date','opinion','audit_firm','signing_auditor','source','data_version').unique().sort(
            'symbol','report_period','announce_date','opinion','audit_firm','signing_auditor')
        companies = load('fund_companies', config=cfg).select(
            'company_id','name','setup_date','end_date','source',
        ).filter(pl.col('company_id').is_not_null(),pl.col('name').is_not_null()).unique().sort('name','company_id')
        if min(holders.height, audits.height, companies.height, products.num_rows) == 0:
            raise ValueError('正式切片为空，停止生成候选')
    finally:
        conn.close()

    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent,prefix='.institution-') as tmp:
        stage = Path(tmp)/'revision'
        for group in SECTIONS:
            (stage/group).mkdir(parents=True)
        pq.write_table(products, stage/'owners/phase3_fund_scale.parquet',compression='zstd')
        holders.write_parquet(stage/'owners/phase3_float_holders.parquet')
        companies.write_parquet(stage/'owners/phase3_fund_companies.parquet')
        audits.write_parquet(stage/'contrarian/phase3-audit-events.parquet')
        meta = {'cutoff':CUTOFF,'fund_report_period':str(H1),'float_holder_periods':[str(H1),str(Q3)],
                'scale_sha256':scale_hash,'fund_scale_sample_products':products.num_rows,
                'float_holder_rows':holders.height,'audit_opinion_rows':audits.height,
                'company_profile_rows':companies.height,'holder_scope':'float',
                'fund_scale':'fund_scale:eastmoney_gmbd, share-level; non-PIT sources',
                'history':'audit_opinions/tushare with original announcement date; financial restatement vintage remains unverified'}
        (stage/'institution-prepare.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n')
        _publish_tables(stage,output)
    return meta


def assemble(base_root, revision):
    """Use the existing immutable data layout and runtime hash check."""
    base_root = Path(base_root)
    base = json.loads((base_root/'manifest.json').read_text())
    if base.get('data_version')!=BASE_VERSION or base.get('valuation_cutoff')!='2026-11-13':
        raise ValueError('输入母版必须是含 11-13 市场估值的指定版本')
    inputs = {str(p.relative_to(revision)):digest(p)
              for group in SECTIONS for p in sorted((revision/group).glob('*.parquet'))}
    expected = {'owners/phase3_fund_scale.parquet','owners/phase3_float_holders.parquet',
                'owners/phase3_fund_companies.parquet','contrarian/phase3-audit-events.parquet'}
    if inputs.keys()!=expected:
        raise ValueError('机构三台数据文件范围不符')
    hashes = code_hashes()
    version = hashlib.sha256(json.dumps([BASE_VERSION,hashes,inputs],sort_keys=True).encode()).hexdigest()[:16]
    root = CACHE/'versions'/version
    _local(root)
    root.parent.mkdir(parents=True,exist_ok=True)
    if root.exists():
        if not (root/'manifest.json').is_file():
            raise FileExistsError(root)
        return version,root
    with TemporaryDirectory(dir=root.parent,prefix='.institution-version-') as tmp:
        stage = Path(tmp)/version
        stage.mkdir()
        for p in sorted((base_root/'data').rglob('*')):
            if p.is_symlink():raise ValueError(f'母版包含链接：{p.relative_to(base_root)}')
            if not p.is_file():continue
            target = stage/p.relative_to(base_root)
            target.parent.mkdir(parents=True,exist_ok=True)
            try:
                subprocess.run(['cp','-c',str(p),str(target)],check=True,capture_output=True)
            except subprocess.CalledProcessError:
                shutil.copy2(p,target)
        for rel in inputs:
            target = stage/'data/revision'/rel
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(revision/rel,target)
            if digest(target)!=inputs[rel]:
                raise ValueError(f'候选输入在组装时变化：{rel}')
        manifest = {'data_version':version,'base_version':BASE_VERSION,'cutoff':base['cutoff'],
                    'valuation_cutoff':base['valuation_cutoff'],'valuation_outputs':base['valuation_outputs'],
                    'code_hashes':hashes,'institution_outputs':inputs,'snapshot_type':'institution_scoped_lake_refresh'}
        (stage/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
        _publish_tables(stage,root)
    return version,root


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','assemble'])
    parser.add_argument('--base-root',type=Path,required=True)
    parser.add_argument('--revision',type=Path,required=True)
    parser.add_argument('--lake-config',type=Path)
    args=parser.parse_args()
    if args.action=='prepare':
        if not args.lake_config:parser.error('prepare requires --lake-config')
        print(json.dumps(prepare(args.base_root,args.lake_config,args.revision),ensure_ascii=False))
    else:
        version,root=assemble(args.base_root,args.revision)
        print(json.dumps({'version':version,'candidate':str(root)},ensure_ascii=False))
