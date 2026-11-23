"""Compute small UI-facing derivatives from a frozen input version only."""

import json
import duckdb
from seven_desks.store import connect
import numpy as np
import pandas as pd
from seven_desks.store import CACHE


def rank_history(s, window=1260, minimum=500):
    a = pd.to_numeric(s, errors="coerce").to_numpy()
    result = []
    for i, v in enumerate(a):
        h = a[max(0, i - window + 1) : i + 1]
        h = h[np.isfinite(h)]
        result.append(
            100 * ((h < v).sum() + 0.5 * (h == v).sum()) / len(h)
            if np.isfinite(v) and len(h) >= minimum
            else np.nan
        )
    return result


def build(version):
    root = CACHE / "versions" / version / "data"
    out = root / "contrarian"
    c = connect()
    c.execute("SET threads=2")
    c.execute("SET memory_limit='1GB'")
    c.execute(f"SET temp_directory='{root / '_prepare_tmp'}'")
    history = out / "history"
    dest = out / "local_temperature.parquet"
    if history.exists() and not dest.exists():
        f = c.execute(
            f"select trade_date,median(pe_ttm) pe,median(pb) pb,median(ret20) ret20,median(ret3y) ret3y,median(drawdown52) drawdown52,count(*) sample_count from read_parquet('{history}/factors_*.parquet') group by trade_date order by trade_date"
        ).df()
        for k in ["pe", "pb", "ret20", "ret3y", "drawdown52"]:
            f[k + "_fear"] = 100 - np.array(rank_history(f[k]))
        f["price_group"] = f[["ret20_fear", "ret3y_fear", "drawdown52_fear"]].mean(
            axis=1
        )
        f["value_group"] = f[["pe_fear", "pb_fear"]].mean(axis=1)
        valid = (
            f[[k + "_fear" for k in ["pe", "pb", "ret20", "ret3y", "drawdown52"]]]
            .notna()
            .sum(axis=1)
        )
        f["temperature"] = (
            ((0.5 * f.price_group + 0.3 * f.value_group) / 0.8)
            .where(valid >= 4)
            .rolling(5, min_periods=5)
            .mean()
        )
        f["quality"] = "本地近似：价格与估值两组；缺聪明钱组，非原站温度"
        f.to_parquet(dest, index=False)
        (out / "local_temperature_formula.json").write_text(
            json.dumps(
                {
                    "window": 1260,
                    "minimum": 500,
                    "value": "median PE/PB inverse midrank",
                    "price": "median 20d/3y returns and drawdown inverse midrank",
                    "aggregation": "weight .5 price + .3 value renormalized; >=4 valid; 5 day complete mean",
                    "missing": "smart_money unavailable; industry/classification and financial revisions not strict PIT",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    factors = out / "latest_factors.parquet"
    if factors.exists() and not (out / "ui_factors.parquet").exists():
        x = pd.read_parquet(factors)
        taxonomy = str(root / "support/industry_taxonomy/**/*.parquet")
        tax = (
            c.execute(
                f"SELECT industry_code,industry_name,parent_industry_code,level FROM read_parquet('{taxonomy}',hive_partitioning=false) WHERE classification_system='SW2021'"
            )
            .df()
            .drop_duplicates("industry_code")
            .set_index("industry_code")
        )

        def ancestor(code, level):
            seen = set()
            while str(code) in tax.index and str(code) not in seen:
                code = str(code)
                seen.add(code)
                r = tax.loc[code]
                if r.level == level:
                    return r.industry_name
                code = r.parent_industry_code
            return "未知行业"

        x["industry_l1"] = x.industry_code.map(lambda z: ancestor(z, "L1"))
        x["industry_l2"] = x.industry_code.map(lambda z: ancestor(z, "L2"))
        x["industry_name"] = x.industry_l1
        group = x.industry_l2.where(
            x.groupby("industry_l2").industry_l2.transform("size") >= 15, x.industry_l1
        )
        x["score_group"] = group

        def pct(z, cheap=True):
            n = z.count()
            return (
                (1 - (z.rank(method="average") - 0.5) / n) * 100
                if cheap
                else (z.rank(method="average") - 0.5) / n * 100
            )

        scores = []
        for key in ["pe_ttm", "pb", "ps_ttm", "pcf_ttm", "ev_ebitda"]:
            a = x[key].where((x[key] > 0) & x.universe_eligible)
            if key in ["ps_ttm", "pcf_ttm", "ev_ebitda"]:
                a = a.mask(x.industry_l1.isin(["银行", "非银金融"]))
            x[key + "_cheap"] = a.groupby(group).transform(pct)
            scores.append(key + "_cheap")
        x["dividend_score"] = pct(x.dv_ttm.where(x.universe_eligible), False)
        scores.append("dividend_score")
        x["valuation_score"] = x[scores].mean(axis=1)
        x["composite_score"] = 0.7 * x.valuation_score + 0.3 * x.reversal_score
        mult = (
            np.where(x.score >= 7, 1.1, np.where(x.score <= 3, 0.8, 1.0))
            * np.where(x.yellow_count >= 2, 0.9, 1.0)
            * np.where(x.cycle_candidate, 1.1, 1.0)
        )
        x["composite_score"] = (x.composite_score * mult).clip(0, 100)
        x.to_parquet(out / "ui_factors.parquet")
        rules = {
            **{
                "low_" + k: x[k + "_cheap"] >= 80
                for k in ["pe_ttm", "pb", "ps_ttm", "pcf_ttm", "ev_ebitda"]
            },
            "high_dividend": (x.dv_ttm >= 3) & (x.dividend_score >= 80),
            "short_reversal": x.ret20_reverse >= 80,
            "long_loser": x.ret3y_reverse >= 80,
            "deep_drawdown": x.drawdown52_reverse >= 80,
            "cycle_reverse": x.cycle_candidate,
        }
        bs = []
        for name, mask in rules.items():
            v = (
                x.loc[x.universe_eligible & mask]
                .sort_values("composite_score", ascending=False)
                .reset_index()
            )
            v["board"] = name
            bs.append(v)
        b = pd.concat(bs, ignore_index=True)
        b.to_parquet(out / "ui_boards.parquet", index=False)
        sets = b.groupby("board").symbol.apply(set)
        pd.DataFrame(
            [
                {
                    "board_a": a,
                    "board_b": bb,
                    "intersection": len(v & sets[bb]),
                    "union": len(v | sets[bb]),
                }
                for a, v in sets.items()
                for bb in sets.index
            ]
        ).to_parquet(out / "ui_board_overlap.parquet", index=False)
        tax.reset_index().to_parquet(out / "ui_industry_taxonomy.parquet", index=False)
    if (
        (out / "ui_industry_taxonomy.parquet").exists()
        and history.exists()
        and not (out / "ui_industry_daily.parquet").exists()
    ):
        tax = pd.read_parquet(out / "ui_industry_taxonomy.parquet").set_index(
            "industry_code"
        )

        def parent(code, level):
            seen = set()
            while str(code) in tax.index and str(code) not in seen:
                code = str(code)
                seen.add(code)
                r = tax.loc[code]
                if r.level == level:
                    return r.industry_name
                code = r.parent_industry_code
            return "未知行业"

        mapping = pd.DataFrame(
            [
                {"code": k, "l1": parent(k, "L1"), "l2": parent(k, "L2")}
                for k in tax.index
            ]
        )
        c.register("mapping", mapping)
        frames = []
        for level in ["l1", "l2"]:
            g = c.execute(
                f"SELECT trade_date,coalesce(m.{level},'未知行业') industry_name,sum(amount) amount,avg(ret20) ret20,median(pe_ttm) pe,median(pb) pb,count(*) stock_count FROM read_parquet('{history}/factors_*.parquet') f LEFT JOIN mapping m ON f.industry=m.code GROUP BY 1,2 ORDER BY industry_name,trade_date"
            ).df()
            g["amount_share"] = g.amount / g.groupby("trade_date").amount.transform(
                "sum"
            )
            parts = []
            for name, h in g.groupby("industry_name", sort=False):
                h = h.copy()
                h["crowding_proxy"] = (
                    np.array(rank_history(h.amount_share))
                    + np.array(rank_history(h.ret20))
                ) / 2
                h["coldness"] = 100 - h.crowding_proxy
                h["level"] = level.upper()
                parts.append(h)
            frames.append(pd.concat(parts, ignore_index=True))
        pd.concat(frames, ignore_index=True).to_parquet(
            out / "ui_industry_daily.parquet", index=False
        )
    if (out / "events.parquet").exists() and not (out / "ui_events.parquet").exists():
        c.execute(f"""COPY (WITH dates AS (SELECT DISTINCT trade_date FROM read_parquet('{out / "price_features.parquet"}')),
        cal AS (SELECT trade_date,lead(trade_date,20) OVER(ORDER BY trade_date) end20,lead(trade_date,60) OVER(ORDER BY trade_date) end60 FROM dates)
        SELECT e.*,cal.end20,cal.end60 FROM read_parquet('{out / "events.parquet"}') e LEFT JOIN cal ON e.entry_date=cal.trade_date)
        TO '{out / "ui_events.parquet"}' (FORMAT PARQUET,COMPRESSION ZSTD)""")
    chip_index = root / "market/chip-index"
    chip_daily = list((root / "market/chips").glob("daily-*.parquet"))
    if chip_daily and not (chip_index / "complete.json").exists():
        chip_index.mkdir(exist_ok=True)
        c.execute(
            f"COPY (SELECT *,right(split_part(symbol,'.',1),2) bucket FROM read_parquet('{root / 'market/chips'}/daily-*.parquet',union_by_name=true,hive_partitioning=false) ORDER BY symbol,trade_date) TO '{chip_index}' (FORMAT PARQUET,PARTITION_BY(bucket),COMPRESSION ZSTD,OVERWRITE_OR_IGNORE)"
        )
        (chip_index / "complete.json").write_text(
            json.dumps(
                {
                    "source": "frozen market/chips daily",
                    "partition": "last two numeric code digits",
                    "scope": "same source rows; no recalculation",
                },
                ensure_ascii=False,
            )
        )
    c.close()


if __name__ == "__main__":
    build(json.loads((CACHE / "active.json").read_text())["data_version"])
