"""Build a full local seven-desk candidate with a bounded valuation-only lake refresh."""
import argparse
from hashlib import sha256
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from seven_desks.settings import CACHE, CUTOFF, FROZEN_VERSION
from seven_desks.store import version_root

OFFICIAL = ("000001.SH", "000016.SH", "000300.SH", "000905.SH", "399001.SZ", "399006.SZ")
RELEASE = "2026-11-13"
WINDOWS = (3, 5, 10, 20)


def weekly_rank(series, day, years, week_ends):
    valid = series.loc[series.index.intersection(week_ends)]
    samples = valid.loc[(valid.index > day - pd.DateOffset(years=years)) & (valid.index <= day)].dropna()
    value = series.get(day)
    size = len(samples)
    if size < math.ceil(years * 52 * .8) or value is None or not math.isfinite(value):
        return None, size
    return float(100 * ((samples < value).sum() + .5 * (samples == value).sum()) / size), size


def clean_frame(frame, keys, fields):
    frame = frame[[*keys, *fields]].drop_duplicates().copy()
    if frame.duplicated(keys).any():
        raise ValueError(f"conflicting lake keys: {keys}")
    for field in fields:
        frame[field] = frame[field].where(np.isfinite(frame[field]), np.nan)
    return frame


def read(root, name):
    return pd.read_parquet(root / f"{name}.parquet")


def build_tables(src, base, *, base_cutoff=CUTOFF, through=RELEASE, strict=True):
    official = read(src, "official").query("symbol in @OFFICIAL").copy()
    official["trade_date"] = pd.to_datetime(official.trade_date)
    official = clean_frame(official, ["symbol", "trade_date"], ["pe_ttm", "pb"]).sort_values("trade_date")
    for metric in ("pe_ttm", "pb"):
        # Official providers mark unavailable values as null. A non-positive ratio
        # is not interpreted as a cheap index or as a meaningful historical rank.
        official[metric] = official[metric].where(official[metric] > 0)
    cal = read(src, "calendar")
    sessions = pd.DatetimeIndex(pd.to_datetime(cal.loc[cal.is_trading, "trade_date"]).sort_values().unique())
    days = sessions[(sessions > base_cutoff) & (sessions <= through)]
    if strict and days.strftime("%Y-%m-%d").tolist() != ["2026-11-10", "2026-11-11", "2026-11-12", RELEASE]:
        raise ValueError("formal trading sessions do not match the bounded refresh")
    raw = read(base, "daily")
    columns = raw.columns
    symbols = sorted(raw.index_symbol.unique())
    all_days = pd.DatetimeIndex(pd.to_datetime(raw.trade_date).unique()).union(
        pd.DatetimeIndex(official.trade_date.unique())
    ).union(sessions)
    week_ends = all_days.to_series().groupby(all_days.to_period("W-FRI")).max()
    lookup = {symbol: data.set_index("trade_date") for symbol, data in official.groupby("symbol")}
    new = []
    for day in days:
        for symbol in symbols:
            row = {"index_symbol": symbol, "trade_date": day}
            if symbol in OFFICIAL:
                history = lookup[symbol]
                if day not in history.index or pd.isna(history.loc[day, ["pe_ttm", "pb"]]).any():
                    raise ValueError(f"missing official PE/PB: {symbol} {day}")
                for metric, source_col, official_col in (
                    ("pe_ttm", "pe_source", "official_pe"), ("pb", "pb_source", "official_pb")
                ):
                    row[metric] = row[official_col] = history.at[day, metric]
                    row[source_col] = "official.index_valuation_metrics"
                    for period in WINDOWS:
                        pct, count = weekly_rank(history[metric], day, period, week_ends)
                        row[f"{metric}_pct_{period}y"] = pct
                        row[f"{metric}_n_{period}y"] = count
            new.append(row)
    daily = pd.DataFrame(new).reindex(columns=columns)
    identity = read(src, "identity").query("asset_type in ['stock', 'cdr']").copy()
    for field in ("list_date", "delist_date"):
        identity[field] = pd.to_datetime(identity[field])
    if identity.duplicated("symbol").any():
        raise ValueError("conflicting stock identities")
    stock = read(src, "stock")
    stock["trade_date"] = pd.to_datetime(stock.trade_date)
    stock = clean_frame(stock, ["symbol", "trade_date"], ["pe_ttm", "pb", "total_mv"])
    hist = read(base, "stock-structure")
    structure, distributions = [], []
    for day in days:
        eligible = identity.loc[(identity.list_date <= day) & (identity.delist_date.isna() | (identity.delist_date >= day))]
        rows = stock.loc[stock.trade_date.eq(day) & stock.symbol.isin(eligible.symbol)]
        if len(rows) < 5000:
            raise ValueError(f"incomplete equity valuation slice {day}: {len(rows)}")
        pe, pb = rows.pe_ttm, rows.pb.dropna()
        positive = pe[pe > 0]
        structure.append({"trade_date": day, "sample_count": len(rows), "positive_pe_median": positive.median(),
                          "below_book_ratio": (pb < 1).mean(), "pe_quantiles": positive.quantile([.1, .25, .5, .75, .9]).tolist(),
                          "eligible_count": len(eligible), "missing_row_count": len(eligible) - len(rows),
                          "positive_pe_count": len(positive), "valid_pb_count": len(pb),
                          "negative_pb_count": int((pb < 0).sum()),
                          "unknown_listing_count": int(identity.list_date.isna().sum())})
        bucket = pd.cut(pe, [-np.inf, 0, 10, 20, 30, 50, 100, 200, np.inf], right=False,
                        labels=["loss_or_zero", "0-10", "10-20", "20-30", "30-50", "50-100", "100-200", "200+"]).astype("string")
        bucket = bucket.mask(pe.eq(0), "loss_or_zero").fillna("missing")
        for label, part in rows.assign(bucket=bucket).groupby("bucket"):
            distributions.append({"trade_date": day, "bucket": label, "stock_count": len(part),
                                  "market_value": part.total_mv.sum(min_count=1)})
    structure = pd.concat([hist, pd.DataFrame(structure)], ignore_index=True)
    structure["trade_date"] = pd.to_datetime(structure.trade_date)
    median = structure.set_index("trade_date").positive_pe_median
    temp = read(base, "temperature")
    latest = daily.loc[daily.index_symbol.eq("000300.SH")].copy()
    latest["ashare_pe_pct_5y"] = [weekly_rank(median, day, 5, week_ends)[0] for day in latest.trade_date]
    parts = latest[["pe_ttm_pct_5y", "pb_pct_5y", "ashare_pe_pct_5y"]]
    latest["sample_count"] = parts.count(axis=1)
    latest["temperature"] = parts.mean(axis=1).where(latest.sample_count >= 3)
    ratios = read(base, "relative-valuations")
    pe = daily.pivot(index="trade_date", columns="index_symbol", values="pe_ttm")
    relative = pd.DataFrame({"trade_date": pe.index, "small_large_pe_ratio": np.nan,
                             "growth_value_pe_ratio": (pe["399006.SZ"] / pe["000016.SH"]).values})
    prices = read(src, "prices").query("frequency == '1d'").copy()
    prices["trade_date"] = pd.to_datetime(prices.trade_date)
    prices = clean_frame(prices, ["symbol", "trade_date"], ["close", "amount"]).query("trade_date > @base_cutoff")
    comparisons = read(src, "comparisons")
    comparisons = comparisons.loc[comparisons.provider.isin(["csindex", "legulegu"]),
                                  ["symbol", "trade_date", "provider", "metric", "value", "unit", "observation_frequency"]]
    comparisons = clean_frame(comparisons, ["symbol", "trade_date", "provider", "metric",
                                            "unit", "observation_frequency"], ["value"])
    published = read(src, "published")
    published = clean_frame(published, ["entity_id", "obs_date", "metric_id", "data_version", "sample_count"], ["value"])
    if len(published) != 258 or published.duplicated(["entity_id", "obs_date", "metric_id"]).any():
        raise ValueError("unexpected published PE5 return-statistics coverage")
    def append(name, frame):
        return pd.concat([read(base, name), frame], ignore_index=True)
    if not strict:
        prices=pd.concat([read(base,'phase3-prices'),prices],ignore_index=True)
        prices['trade_date']=pd.to_datetime(prices.trade_date)
        prices=prices.drop_duplicates(['symbol','trade_date'],keep='last')
    return {
        "daily": pd.concat([raw, daily], ignore_index=True),
        "temperature": pd.concat([temp, latest.reindex(columns=temp.columns)], ignore_index=True),
        "stock-structure": structure,
        "relative-valuations": pd.concat([ratios, relative], ignore_index=True),
        "pe-histogram": append("pe-histogram", pd.DataFrame(distributions)),
        "phase3-prices": prices.sort_values(["symbol", "trade_date"]),
        "phase3-comparisons": comparisons.sort_values(["symbol", "provider", "metric", "trade_date"]),
        "phase3-returns": published,
    }


def digest(path):
    with path.open("rb") as f:
        from hashlib import file_digest
        return file_digest(f, "sha256").hexdigest()


def build(source):
    from seven_desks.store import code_hashes

    base = version_root(FROZEN_VERSION)
    inputs = {p.name: digest(p) for p in sorted(source.glob("*.parquet"))}
    hashes = code_hashes()
    version = sha256(json.dumps([FROZEN_VERSION, inputs, hashes, digest(Path(__file__))],
                                sort_keys=True).encode()).hexdigest()[:16]
    root = CACHE / "versions" / version
    # APFS copy-on-write is independent; no shared inode or legacy absolute link.
    if (root / "manifest.json").exists():
        return version, root
    import shutil
    import subprocess
    from tempfile import TemporaryDirectory

    tables = build_tables(source, base / "data/market/valuation")
    root.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=root.parent, prefix=".valuation-build-") as temp:
        staging = Path(temp) / "version"
        staging.mkdir()
        # Prefer the existing snapshot mechanism's per-file APFS clone, fall back to
        # copying only when same-volume cloning is unavailable.
        for p in sorted((base / "data").rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(base)
            if rel.parent == Path("data/market/valuation") and p.stem in tables:
                continue
            target = staging / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                subprocess.run(["cp", "-c", str(p), str(target)], check=True, capture_output=True)
            except subprocess.CalledProcessError:
                shutil.copy2(p, target)
        dest = staging / "data/market/valuation"
        dest.mkdir(parents=True, exist_ok=True)
        outputs = {}
        for name, table in tables.items():
            target = dest / f"{name}.parquet"
            if "trade_date" in table:
                table["trade_date"] = pd.to_datetime(table.trade_date).dt.date
            table.to_parquet(target, index=False, compression="zstd")
            outputs[target.name] = {"sha256": digest(target), "rows": len(table)}
        manifest = {"data_version": version, "base_version": FROZEN_VERSION, "cutoff": CUTOFF,
                    "valuation_cutoff": RELEASE, "code_hashes": hashes, "inputs": inputs,
                    "valuation_outputs": outputs, "snapshot_type": "valuation_scoped_lake_refresh"}
        (staging / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        staging.rename(root)
    return version, root


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="read-only SDK export")
    version, root = build(parser.parse_args().source)
    print(json.dumps({"version": version, "candidate": str(root)}, ensure_ascii=False))
