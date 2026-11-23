"""Read formal lake releases to build isolated, mergeable market desk inputs.

Run in the locked vic_desks environment. No SDK/process state, collection, or
write under the lake. The lake table locations are the existing formal contract.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

from seven_desks.settings import CACHE, CUTOFF
from seven_desks.store import code_hashes, connect


BASE_VERSION = "249854a814731c52"
RANGE_START = "2026-10-09"


def digest(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def sql_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def prepare(lake, out, base):
    lake, out, base = (p.resolve() for p in (lake, out, base))
    if out.is_relative_to(lake) or out.is_relative_to(base):
        raise ValueError("market revision output must be outside the input roots")
    if out.exists():
        raise FileExistsError(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=out.parent) as tmp, connect() as db:
        stage = Path(tmp) / "revision-market"
        stage.mkdir()
        inputs = {}
        outputs = {}

        def view(name, directory):
            # Only enumerate the named data set; no formal lake databases opened.
            parts = sorted(p for p in directory.rglob("*.parquet") if p.is_file())
            if not parts:
                raise FileNotFoundError(directory)
            inputs[name] = {p.relative_to(lake).as_posix(): [p.stat().st_size, p.stat().st_mtime_ns] for p in parts}
            files = ",".join(map(sql_literal, parts))
            db.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet([{files}], hive_partitioning=false, union_by_name=true)")

        def emit(name, query, key):
            path = stage / f"{name}.parquet"
            db.execute(f"COPY ({query}) TO {sql_literal(path)} (FORMAT PARQUET, COMPRESSION ZSTD)")
            result = db.execute(
                f"SELECT count(*),count(*)-count(DISTINCT ({key})) FROM read_parquet(?)", [str(path)]
            ).fetchone()
            if result[0] == 0 or result[1] != 0:
                raise ValueError(f"empty or duplicate result: {name}: {result}")
            outputs[path.name] = {"rows": result[0], "sha256": digest(path)}

        view("macro", lake / "curated/macro_indicators")
        view("holders", lake / "curated/stock_holdertrade_announcements")
        view("chip", lake / "curated/stock_chip_metrics")
        view("cross_border", lake / "curated/cross_border_daily_stats")
        view("margin", lake / "curated/margin_trading")
        view("balances", lake / "derived/seven_capital_balance_windows")
        seen_sessions = base / "data/market/sentiment/daily.parquet"
        db.execute(f"CREATE VIEW sessions AS SELECT cast(trade_date AS DATE) trade_date FROM read_parquet({sql_literal(seen_sessions)}) WHERE trade_date <= DATE '{CUTOFF}'")
        emit("phase3-vix-term", f"""
            WITH known AS (
                SELECT cast(obs_date AS DATE) source_date, indicator_id, min(value) v
                FROM macro WHERE ((indicator_id='VIX3M' AND source='cboe.VIX3M') OR
                 (indicator_id='VIXCLS' AND source='fred.VIXCLS'))
                 AND obs_date <= DATE '{CUTOFF}' AND isfinite(value) AND value > 0
                GROUP BY 1,2 HAVING count(DISTINCT value)=1
            ), paired AS (
                SELECT source_date, max(v) FILTER (WHERE indicator_id='VIX3M') vix3m,
                   max(v) FILTER (WHERE indicator_id='VIXCLS') vix
                FROM known GROUP BY 1 HAVING count(*)=2
            ), us_days AS (
                SELECT source_date, count(*)=2 paired_today FROM known GROUP BY 1
            ), last_visible AS (
                SELECT d.trade_date, u.source_date, u.paired_today
                FROM sessions d ASOF LEFT JOIN us_days u ON d.trade_date > u.source_date
            )
            SELECT d.trade_date, p.source_date, p.vix3m, p.vix,
                p.vix3m/nullif(p.vix,0) raw_value
            FROM last_visible d LEFT JOIN paired p ON p.source_date=d.source_date AND d.paired_today
            ORDER BY 1
        """, "trade_date")
        emit("phase3-holder-amounts", f"""
            WITH events AS (
                SELECT CAST(announce_date AS DATE) announce_date, symbol, holder_name, trade_type,
                       begin_date, close_date, abs(change_vol) shares,
                       CASE WHEN count(DISTINCT avg_price) FILTER (WHERE avg_price>0)=1
                            THEN min(avg_price) FILTER (WHERE avg_price>0) END price
                FROM holders
                WHERE announce_date BETWEEN DATE '{RANGE_START}' AND DATE '{CUTOFF}' AND trade_type IN ('IN','DE')
                GROUP BY ALL
            )
            SELECT announce_date, COUNT(*) AS event_count,
                count(*) FILTER (WHERE price>0 AND shares>0) AS with_price_count,
                SUM(shares*price) FILTER (WHERE trade_type='IN' AND shares>0) AS observed_increase,
                SUM(shares*price) FILTER (WHERE trade_type='DE' AND shares>0) AS observed_decrease
            FROM events GROUP BY 1 ORDER BY 1
        """, "announce_date")
        emit("phase3-chip-native", f"""
            SELECT symbol, trade_date, winner_rate, weight_avg, cost_5pct, cost_15pct, cost_50pct,
                cost_85pct, cost_95pct, his_low, his_high, min(source) AS "source", min(data_version) source_version
            FROM chip
            WHERE trade_date IN (DATE '2026-11-06', DATE '{CUTOFF}') AND source='tushare.cyq_perf'
            GROUP BY ALL
        """, "symbol,trade_date")
        emit("phase3-capital-north", f"""
            SELECT trade_date, channel, min(turnover) turnover, min(source) AS "source", 'CNY' currency
            FROM cross_border
            WHERE source='hkex.csm.daily_stat' AND channel IN ('north_sh','north_sz') AND currency='CNY'
                AND trade_date BETWEEN DATE '2024-08-19' AND DATE '{CUTOFF}'
                AND isfinite(turnover) AND turnover>=0
            GROUP BY 1,2 HAVING count(DISTINCT turnover)=1 ORDER BY 1,2
        """, "trade_date,channel")
        emit("phase3-capital-margin", f"""
            SELECT symbol, trade_date, min(margin_buy) financing_buy, min(margin_repay) financing_repay,
                   min(margin_buy)-min(margin_repay) financing_net_buy, min(source) AS "source"
            FROM margin
            WHERE trade_date BETWEEN DATE '{RANGE_START}' AND DATE '{CUTOFF}'
                AND isfinite(margin_buy) AND isfinite(margin_repay) AND margin_buy>=0 AND margin_repay>=0
            GROUP BY 1,2
            HAVING count(DISTINCT struct_pack(b:=margin_buy,r:=margin_repay))=1
            ORDER BY 2,1
        """, "symbol,trade_date")
        emit("phase3-capital-balance", f"""
            SELECT obs_date, line, window_days, source_as_of, observed_sum, valid_days, strict_valid_days,
                   complete_window_value, observed_complete_window_estimate, quality_status, source, data_version
            FROM balances
            WHERE obs_date BETWEEN DATE '{RANGE_START}' AND DATE '{CUTOFF}' ORDER BY 1,3,2
        """, "obs_date,line,window_days")
        for recorded in inputs.values():
            for name, stat in recorded.items():
                source = lake / name
                actual = source.stat()
                if [actual.st_size, actual.st_mtime_ns] != stat:
                    raise RuntimeError(f"lake file changed during build: {name}")
        manifest = {"kind": "phase3-market-inputs", "cutoff": CUTOFF, "base_version": base.name,
                    "inputs": inputs, "calendar_sha256": digest(seen_sessions), "outputs": outputs}
        (stage / "phase3-market-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        stage.rename(out)
    return outputs


def candidate(base, revision_market, destination=None, overlays=()):
    """Compose under merged runtime code, retaining valuation cutoff and all other desks."""
    base = base.resolve()
    start = json.loads((base / "manifest.json").read_text())
    if start.get("valuation_cutoff") != "2026-11-13":
        raise ValueError("base must keep the accepted November 13 valuation")
    layers = [("market", revision_market.resolve()), *overlays]
    inputs = {}
    for group, folder in layers:
        for source in sorted(folder.rglob("*")):
            if source.is_file() and source.suffix in (".json", ".parquet"):
                inputs[f"revision/{group}/{source.relative_to(folder)}"] = source
    if "revision/market/phase3-market-manifest.json" not in inputs:
        raise ValueError("missing market preparation manifest")
    hashes = code_hashes()
    proof = {name: digest(file) for name, file in inputs.items()}
    identity = [digest(base / "manifest.json"), hashes, proof]
    version = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:16]
    parent = (destination or CACHE / "versions").resolve()
    root = parent / version
    if (root / "manifest.json").is_file():
        return root
    parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=parent, prefix=".market-candidate-") as temp:
        stage = Path(temp) / "version"
        stage.mkdir()
        try:
            subprocess.run(["cp", "-cR", str(base / "data"), str(stage / "data")], check=True, capture_output=True)
        except subprocess.CalledProcessError:
            if (stage / "data").exists():
                shutil.rmtree(stage / "data")
            shutil.copytree(base / "data", stage / "data")
        for relative, source in inputs.items():
            target = stage / "data" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            if digest(target) != proof[relative]:
                raise RuntimeError(f"revision changed during composition: {relative}")
        meta = {key: value for key, value in start.items() if key not in ("files", "source")}
        meta.update(data_version=version, base_version=base.name, code_hashes=hashes,
                    phase3_revision_sha256=proof, snapshot_type="phase3_valuation_plus_market_revisions")
        (stage / "manifest.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
        stage.rename(root)
    return root


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, type=Path)
    parser.add_argument("--lake", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--compose", action="store_true")
    parser.add_argument("--overlay", action="append", default=[], type=Path,
                        help="existing revision-owners or revision-contrarian directory")
    options = parser.parse_args()
    if options.lake:
        print(json.dumps(prepare(options.lake, options.out, options.base), ensure_ascii=False))
    if options.compose:
        overlays = [(p.name.removeprefix("revision-"), p.resolve()) for p in options.overlay]
        if any(group not in ("owners", "contrarian") for group, _ in overlays):
            parser.error("--overlay expects revision-owners or revision-contrarian")
        print(candidate(options.base, options.out, overlays=overlays))
