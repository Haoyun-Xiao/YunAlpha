"""Prepare local market supplements using read-only formal lake inputs.

Output lives only in output/seven-desks/market-extra. The version builder freezes
that directory as data/market/ui-extra; no formal source is written or fetched.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import duckdb
from seven_desks.store import connect
import numpy as np
import pandas as pd

from seven_desks.adapters.market import CUTOFF, INDICES
from seven_desks.store import CACHE
from seven_desks.settings import SOURCE

LAKE = SOURCE.parents[2]
OUT = CACHE / "market-extra"
VERSION = "seven-local-market-extra-20261113-v1"


def _quoted(path):
    return str(path).replace("'", "''")


def build(resume=False):
    OUT.mkdir(parents=True, exist_ok=True)
    c = connect(config={"threads": 2, "memory_limit": "1GB"})
    c.execute(f"SET temp_directory='{_quoted(OUT / 'spill')}'")
    inputs = {}

    def source(rel):
        files = sorted((LAKE / rel).rglob("*.parquet"))
        if not files:
            raise FileNotFoundError(rel)
        inputs[rel] = [{"path": str(p.relative_to(LAKE)), "bytes": p.stat().st_size, "mtime_ns": p.stat().st_mtime_ns} for p in files]
        return f"read_parquet('{_quoted(LAKE / rel)}/**/*.parquet', hive_partitioning=false, union_by_name=true)"

    def copy(sql, rel):
        dest = OUT / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".tmp.parquet")
        c.execute(f"COPY ({sql}) TO '{_quoted(tmp)}' (FORMAT PARQUET, COMPRESSION ZSTD)")
        tmp.replace(dest)
        print(rel, c.execute("SELECT count(*) FROM read_parquet(?)", [str(dest)]).fetchone()[0], flush=True)

    try:
        bars = source("curated/daily_bars")
        turnover = source("curated/stock_daily_research_inputs")
        # Distinct conflicting observations never win by physical file order.
        c.execute(f"CREATE VIEW bars AS SELECT symbol,trade_date,min(open) AS open,min(high) AS high,min(low) AS low,min(close) AS close,min(volume) AS volume,min(amount) AS amount FROM {bars} WHERE trade_date<='{CUTOFF}' GROUP BY 1,2 HAVING count(DISTINCT struct_pack(o:=open,h:=high,l:=low,c:=close,v:=volume,a:=amount))=1")
        c.execute(f"CREATE VIEW turnover AS SELECT symbol,trade_date,min(turnover_rate) AS turnover_rate FROM {turnover} WHERE trade_date<='{CUTOFF}' GROUP BY 1,2 HAVING count(DISTINCT turnover_rate)<=1")
        for year in range(2018, 2027):
            chip = SOURCE / f"market/chips/daily-{year}.parquet"
            if not chip.exists():
                continue
            st = chip.stat()
            inputs[f"review/market/chips/daily-{year}.parquet"] = [{"path": str(chip.relative_to(LAKE)), "bytes": st.st_size, "mtime_ns": st.st_mtime_ns}]
            if resume and (OUT / f"chips/bars-{year}.parquet").exists():
                continue
            # The chip panel defines the bounded product universe; all observations
            # remain raw prices, matching the supplier's unadjusted cost bands.
            copy(f"SELECT k.symbol,k.trade_date,b.open,b.high,b.low,b.close,b.volume,b.amount,t.turnover_rate FROM read_parquet('{_quoted(chip)}',hive_partitioning=false) k LEFT JOIN (SELECT * FROM bars WHERE trade_date BETWEEN DATE '{year}-01-01' AND DATE '{year}-12-31') b ON k.symbol=b.symbol AND k.trade_date=b.trade_date LEFT JOIN (SELECT * FROM turnover WHERE trade_date BETWEEN DATE '{year}-01-01' AND DATE '{year}-12-31') t ON k.symbol=t.symbol AND k.trade_date=t.trade_date WHERE k.trade_date<='{CUTOFF}'", f"chips/bars-{year}.parquet")
        indices = source("curated/index_bars")
        codes = ",".join("'" + s + "'" for s in list(INDICES) + ["000002.SH"])
        copy(f"SELECT symbol,trade_date,min(open) AS open,min(high) AS high,min(low) AS low,min(close) AS close,min(volume) AS volume,min(amount) AS amount FROM {indices} WHERE symbol IN ({codes}) AND trade_date BETWEEN DATE '2010-01-01' AND DATE '{CUTOFF}' GROUP BY 1,2 HAVING count(DISTINCT struct_pack(o:=open,h:=high,l:=low,c:=close))=1 ORDER BY symbol,trade_date", "index-prices.parquet")
        macro = source("curated/macro_indicators")
        money = source("curated/market_money_flow")
        valuation = source("curated/index_valuation_metrics")
        days = c.execute(f"SELECT trade_date FROM {indices} WHERE symbol='000300.SH' AND trade_date BETWEEN DATE '2010-01-01' AND DATE '{CUTOFF}' GROUP BY 1 ORDER BY 1").df().trade_date
        cal = pd.DatetimeIndex(days)
        raw_money = c.execute(f"SELECT trade_date,min(net_amount) AS net_amount FROM {money} WHERE trade_date<='{CUTOFF}' GROUP BY 1 HAVING count(DISTINCT net_amount)<=1 ORDER BY 1").df().set_index("trade_date")
        main20 = raw_money.net_amount.reindex(cal).rolling(20, min_periods=20).sum()
        pmi = c.execute(f"SELECT obs_date,min(value) AS value FROM {macro} WHERE indicator_id='pmi_manufacturing' AND obs_date<='{CUTOFF}' GROUP BY 1 HAVING count(DISTINCT value)<=1 ORDER BY 1").df()
        vix = c.execute(f"SELECT obs_date,min(value) AS value FROM {macro} WHERE indicator_id='VIXCLS' AND obs_date<='{CUTOFF}' GROUP BY 1 HAVING count(DISTINCT value)<=1 ORDER BY 1").df()
        pe = c.execute(f"SELECT trade_date,min(pe_ttm) AS value FROM {valuation} WHERE symbol='000300.SH' AND trade_date<='{CUTOFF}' GROUP BY 1 HAVING count(DISTINCT pe_ttm)<=1 ORDER BY 1").df().set_index("trade_date").value
        rows = []
        for day in cal[cal >= pd.Timestamp("2016-01-01")]:
            d = day.date().isoformat()
            main = main20.get(day, np.nan)
            rows.append({"trade_date": d, "indicator_id": "MAIN20", "indicator": "主力净流入20日", "raw_value": main, "source_date": d if pd.notna(main) else None, "unit": "CNY", "source": "curated/market_money_flow.net_amount", "missing_reason": None if pd.notna(main) else "前20个A股交易日未获得完整主力资金分类金额", "availability": "供应商最终快照；非逐版本PIT"})
            for key, label, frame, strict in [("PMI", "制造业PMI", pmi, False), ("VIX", "VIX现货(辅助)", vix, True)]:
                visible = frame[frame.obs_date < day] if strict else frame[frame.obs_date <= day]
                last = visible.iloc[-1] if len(visible) else None
                rows.append({"trade_date": d, "indicator_id": key, "indicator": label, "raw_value": last.value if last is not None else None, "source_date": last.obs_date.date().isoformat() if last is not None else None, "unit": "index_points", "source": "curated/macro_indicators", "missing_reason": None if last is not None else "该观察日以前没有来源值", "availability": "严格前一美股来源日期" if strict else "按来源月末观察日对齐；无逐公告发布时间，非严格PIT"})
            sample = pe[(pe.index > day - pd.DateOffset(years=20)) & (pe.index <= day)].dropna()
            value = pe.get(day)
            score = 100 * ((sample < value).sum() + .5 * (sample == value).sum()) / len(sample) if len(sample) >= 4032 and pd.notna(value) else None
            rows.append({"trade_date": d, "indicator_id": "HS300_PE20", "indicator": "沪深300 PE20年分位", "raw_value": score, "source_date": d if pd.notna(value) else None, "unit": "score_0_100", "source": "curated/index_valuation_metrics.pe_ttm", "missing_reason": None if score is not None else f"20年窗口有效交易日{len(sample)}，未达到80%完整度门槛4032；不拿短史分位冒充20年", "availability": "供应商最终估值快照；非逐版本PIT"})
            rows.append({"trade_date": d, "indicator_id": "VIX_TERM", "indicator": "VIX3M/VIX期限结构", "raw_value": None, "source_date": None, "unit": "ratio", "source": "curated/macro_indicators", "missing_reason": "已枚举正式macro_indicators：仅有VIXCLS，没有VIX3M输入，不能用VIX现货代替期限比值", "availability": "missing_source"})
        display = pd.DataFrame(rows)
        for col in ("trade_date", "source_date"):
            display[col] = pd.to_datetime(display[col])
        display.to_parquet(OUT / "sentiment-display.parquet", index=False)
        print("sentiment-display.parquet", len(display), flush=True)
        files = []
        for f in sorted(OUT.rglob("*.parquet")):
            files.append({"path": str(f.relative_to(OUT)), "bytes": f.stat().st_size, "sha256": hashlib.sha256(f.read_bytes()).hexdigest()})
        manifest = {"code_path": str(Path(__file__).resolve()), "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "version": VERSION, "cutoff": CUTOFF, "lake_read_only": True, "inputs": inputs, "outputs": files, "definitions": {"stock_prices": "unadjusted formal daily OHLC; one row per chip observation; conflicting source keys excluded", "turnover": "same-day formal turnover_rate, no historical carrying", "main20": "market_money_flow.net_amount summed only over 20 complete A-share market sessions", "PMI": "latest formal obs_date <= observation day, no claim of exact announcement availability", "VIX": "latest strictly earlier US source date", "HS300_PE20": "inclusive daily midrank in trailing 20 calendar years; minimum 4032 finite daily values", "VIX_TERM": "null: enumerated formal inputs lack VIX3M", "dynamic_replay": "adapter recomputes correlations, event paths and band returns using only selected-day-visible daily temperatures/events/prices"}}
        (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    finally:
        c.close()
        spill = OUT / "spill"
        if spill.exists() and not any(spill.iterdir()):
            spill.rmdir()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", action="store_true", help="Only for continuing the same unchanged local input batch")
    build(parser.parse_args().resume)
