from seven_desks.settings import CACHE as BUILD_ROOT, LAKE, SOURCE as REVIEW_SOURCE, FROZEN_VERSION
from seven_desks.store import version_root, connect
"""Conservative, reproducible fund share-class portfolio amount deduplication.

Output is a supplemental holdings dataset; source parquet and lake are read-only.
Only relations disclosed by the owners cutoff in the exact report period qualify.
The complete visible stock portfolios (including duplicate keys and NULLs) must
match exactly; duplicate stock keys disqualify the fund-period.  Raw fund counts
and raw stock share quantities remain available. No name-based inference.
"""
from pathlib import Path
import json
import duckdb

LAKE = LAKE
SRC = LAKE / 'meta/review/seven-followup-20261113/owners'
OUT = (BUILD_ROOT / 'ac-fix')
CUTOFF = '2026-11-09'

def run():
    OUT.mkdir(parents=True, exist_ok=True)
    c = connect(config={'threads': 1})
    c.execute("SET memory_limit='6GB'")
    c.execute(f"CREATE VIEW holdings AS SELECT * FROM read_parquet('{SRC}/fund_holdings_pit.parquet')")
    # Enumerating only periods in the holdings avoids unrelated lake partitions.
    periods = c.execute("SELECT DISTINCT strftime(report_period,'%Y') || 'Q' || cast(quarter(report_period) as varchar) FROM holdings").fetchall()
    paths = [str(p) for (q,) in periods for p in (LAKE/'curated/fund_product_share_observations'/f'report_period={q}').rglob('*.parquet')]
    if not paths: raise RuntimeError('No relation partitions found')
    c.execute("CREATE TEMP TABLE rel_raw AS SELECT DISTINCT report_period, master_code, share_code FROM read_parquet(?, hive_partitioning=false, union_by_name=true) WHERE master_code IS NOT NULL AND share_code IS NOT NULL AND regexp_full_match(report_period, '[0-9]{4}Q[1-4]') AND announce_date IS NOT NULL AND announce_date <= ?::DATE AND (valid_from IS NULL OR valid_from <= last_day(make_date(cast(left(report_period,4) AS INT),cast(right(report_period,1) AS INT)*3,1))) AND (valid_to IS NULL OR valid_to >= last_day(make_date(cast(left(report_period,4) AS INT),cast(right(report_period,1) AS INT)*3,1)))", [paths,CUTOFF])
    c.execute("""CREATE TEMP TABLE rel AS SELECT last_day(make_date(cast(left(report_period,4) AS INT), cast(right(report_period,1) AS INT)*3,1)) report_period, share_code || '.OF' fund_symbol, min(master_code) master_code, count(DISTINCT master_code) master_count FROM rel_raw GROUP BY report_period,share_code""")
    c.execute("""CREATE TEMP TABLE eligible AS SELECT r.* FROM rel r JOIN (SELECT report_period,master_code FROM rel WHERE master_count=1 GROUP BY ALL HAVING count(*)>1) g USING(report_period,master_code) WHERE r.master_count=1""")
    c.execute("""CREATE TEMP TABLE visible_eligible AS SELECT e.* FROM eligible e JOIN (SELECT e.report_period,e.master_code FROM (SELECT DISTINCT report_period,fund_symbol FROM holdings) h JOIN eligible e USING(report_period,fund_symbol) GROUP BY ALL HAVING count(*)>1) x USING(report_period,master_code)""")
    c.execute(f"CREATE TEMP VIEW profile AS SELECT symbol,company_id FROM read_parquet('{SRC}/active_fund_universe.parquet')")
    c.execute("""CREATE TEMP TABLE portfolios AS SELECT h.report_period,h.fund_symbol,e.master_code,pr.company_id,count(*) row_count,count(DISTINCT h.holding_symbol) stock_count,list(struct_pack(symbol := h.holding_symbol, shares := h.shares, market_value := h.market_value) ORDER BY h.holding_symbol,h.shares,h.market_value) portfolio FROM holdings h JOIN visible_eligible e USING(report_period,fund_symbol) JOIN profile pr ON pr.symbol=h.fund_symbol WHERE pr.company_id IS NOT NULL GROUP BY ALL HAVING count(*)=count(DISTINCT h.holding_symbol)""")
    c.execute("""CREATE TEMP TABLE matched AS SELECT a.report_period,a.fund_symbol,a.master_code,min(b.fund_symbol) representative,count(*) equal_funds FROM portfolios a JOIN portfolios b ON a.report_period=b.report_period AND a.master_code=b.master_code AND a.company_id=b.company_id AND a.row_count=b.row_count AND a.portfolio=b.portfolio GROUP BY ALL HAVING count(*)>1""")
    c.execute("""CREATE TEMP TABLE fund_status AS SELECT DISTINCT h.report_period,h.fund_symbol,r.master_code, CASE WHEN r.fund_symbol IS NULL THEN 'no_period_relation' WHEN r.master_count>1 THEN 'ambiguous_period_relation' WHEN m.fund_symbol IS NOT NULL THEN 'verified_identical_portfolio' WHEN p.fund_symbol IS NULL AND v.fund_symbol IS NOT NULL THEN 'excluded_key_or_unknown_company' WHEN e.fund_symbol IS NOT NULL THEN 'unmatched_or_only_one_visible' ELSE 'single_known_share' END ac_coverage, coalesce(m.representative,h.fund_symbol) representative, coalesce(m.equal_funds,1) equal_funds FROM holdings h LEFT JOIN rel r USING(report_period,fund_symbol) LEFT JOIN eligible e USING(report_period,fund_symbol) LEFT JOIN visible_eligible v USING(report_period,fund_symbol) LEFT JOIN portfolios p USING(report_period,fund_symbol) LEFT JOIN matched m USING(report_period,fund_symbol)""")
    def emit(name, sql):
        c.execute(f"COPY ({sql}) TO '{OUT/name}.parquet' (FORMAT PARQUET,COMPRESSION ZSTD)")
        print(name, flush=True)
    emit('fund_share_period_coverage','SELECT * FROM fund_status')
    emit('fund_holdings_corrected', """SELECT h.*,s.master_code ac_master_code,s.representative ac_representative,s.ac_coverage,s.equal_funds ac_equal_funds,CASE WHEN h.fund_symbol=s.representative OR h.market_value IS NULL THEN h.market_value ELSE 0.0 END corrected_market_value,CASE WHEN h.fund_symbol=s.representative OR h.shares IS NULL THEN h.shares ELSE 0.0 END corrected_shares FROM holdings h JOIN fund_status s USING(report_period,fund_symbol)""")
    emit('amount_adjustments', """SELECT h.report_period,h.fund_symbol,s.master_code,s.representative,h.holding_symbol,h.shares original_shares,CASE WHEN h.shares IS NULL THEN NULL ELSE 0.0 END corrected_shares,h.market_value original_market_value,CASE WHEN h.market_value IS NULL THEN NULL ELSE 0.0 END corrected_market_value,h.market_value removed_market_value FROM holdings h JOIN fund_status s USING(report_period,fund_symbol) WHERE s.ac_coverage='verified_identical_portfolio' AND h.fund_symbol<>s.representative""")
    # Exact regression and conservation checks on the complete output.
    c.execute(f"CREATE VIEW corrected AS SELECT * FROM '{OUT}/fund_holdings_corrected.parquet'")
    c.execute(f"CREATE VIEW adjustments AS SELECT * FROM '{OUT}/amount_adjustments.parquet'")
    sample=c.execute("""SELECT count(*),sum(market_value),sum(corrected_market_value),count(*) FILTER (WHERE ac_coverage='verified_identical_portfolio') FROM corrected WHERE report_period=DATE '2025-12-31' AND fund_symbol IN ('012297.OF','012298.OF')""").fetchone()
    assert sample[0] == 824 and sample[3] == 824 and abs(sample[1]-67718176.76)<0.01 and abs(sample[2]-33859088.38)<0.01, sample
    raw_n, out_n, raw_sum, corrected_sum, removed = c.execute("SELECT (SELECT count(*) FROM holdings),(SELECT count(*) FROM corrected),(SELECT sum(market_value) FROM holdings),(SELECT sum(corrected_market_value) FROM corrected),(SELECT sum(removed_market_value) FROM adjustments)").fetchone()
    assert raw_n == out_n
    assert abs((raw_sum-corrected_sum)-removed)<max(1,abs(raw_sum)*1e-10)
    statuses=c.execute("SELECT ac_coverage,count(*) FROM fund_status GROUP BY 1 ORDER BY 1").fetchall()
    stats={'cutoff':CUTOFF,'relation_matching':'exact report period; unique master per share; announced by cutoff; exact complete visible stock portfolio; duplicate keys excluded','sample_2025q4':list(sample),'source_rows':raw_n,'raw_market_value':raw_sum,'corrected_market_value':corrected_sum,'removed_market_value':removed,'fund_period_coverage':dict(statuses),'scope':'Raw shares are preserved alongside corrected_shares; share-class counts remain separate.'}
    (OUT/'validation.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2))
    print(json.dumps(stats,ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__': run()
