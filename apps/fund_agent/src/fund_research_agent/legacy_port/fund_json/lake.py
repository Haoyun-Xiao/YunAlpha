from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

from fund_research_agent.config import load_settings

SW1_CATEGORY = {
    "农林牧渔": "消费",
    "食品饮料": "消费",
    "家用电器": "消费",
    "纺织服饰": "消费",
    "轻工制造": "消费",
    "商贸零售": "消费",
    "社会服务": "消费",
    "美容护理": "消费",
    "医药生物": "消费",
    "传媒": "TMT",
    "电子": "TMT",
    "计算机": "TMT",
    "通信": "TMT",
    "国防军工": "制造",
    "机械设备": "制造",
    "汽车": "制造",
    "电力设备": "制造",
    "环保": "制造",
    "综合": "制造",
    "石油石化": "周期",
    "煤炭": "周期",
    "基础化工": "周期",
    "钢铁": "周期",
    "有色金属": "周期",
    "建筑材料": "周期",
    "建筑装饰": "周期",
    "公用事业": "周期",
    "交通运输": "周期",
    "银行": "金融",
    "非银金融": "金融",
    "房地产": "金融",
}


class LakeRepository:
    """Read-only access used by the generator. No legacy JSON or Excel is reachable here."""

    def __init__(self, database: str | Path, reports_overlay: Path | None = None, *, as_of=None, scales_overlay: Path | None = None):
        self.as_of = pd.Timestamp(as_of or "2262-01-01").date()
        self.scales_overlay = scales_overlay
        self.quality_flags: list[str] = []
        self.database = Path(database).resolve()
        if not self.database.exists():
            raise FileNotFoundError(self.database)
        self.con = duckdb.connect(str(self.database), read_only=True)
        configured_overlay = os.getenv("FUND_AGENT_NAV_OVERLAY", "").strip()
        self.nav_overlay = (
            Path(configured_overlay).expanduser().resolve()
            if configured_overlay
            else load_settings().inputs_root / "fund-nav-dashboard.parquet"
        )
        configured_reports_overlay = os.getenv("FUND_AGENT_REPORTS_OVERLAY", "").strip()
        self.reports_overlay = (
            reports_overlay.resolve()
            if reports_overlay
            else Path(configured_reports_overlay).expanduser().resolve()
            if configured_reports_overlay
            else None
        )

    def close(self) -> None:
        self.con.close()

    def dataset_versions(self) -> dict[str, dict[str, object]]:
        result: dict[str, dict[str, object]] = {}
        for table in (
            "fund_holdings",
            "fund_nav",
            "fund_scale",
            "fund_annual_turnover",
            "fund_reports",
            "daily_bars",
            "corporate_actions",
            "valuation_metrics",
        ):
            try:
                count, fetched = self.con.execute(
                    f"SELECT count(*), max(fetched_at) FROM {table}"
                    if table not in {"fund_scale"}
                    else f"SELECT count(*), NULL FROM {table}"
                ).fetchone()
            except duckdb.Error:
                count, fetched = self.con.execute(f"SELECT count(*), NULL FROM {table}").fetchone()
            result[table] = {"rows": count, "fetched_at": str(fetched) if fetched else None}
        return result

    def positions(self, fund_symbol: str) -> pd.DataFrame:
        code = fund_symbol.split(".", 1)[0]
        df = self.con.execute(
            """
            SELECT fund_symbol, holding_symbol, report_period, announce_date,
                   shares, market_value, weight_nav
            FROM fund_holdings
            WHERE split_part(fund_symbol, '.', 1) = ?
              AND report_period <= ? AND (announce_date IS NULL OR announce_date <= ?)
            QUALIFY row_number() OVER (
                PARTITION BY holding_symbol,report_period
                ORDER BY CASE WHEN fund_symbol = ? THEN 0 ELSE 1 END,fetched_at DESC
            ) = 1
            ORDER BY report_period, holding_symbol
            """,
            [code, self.as_of, self.as_of, fund_symbol],
        ).df()
        if not df.empty:
            df["report_period"] = pd.to_datetime(df["report_period"]).astype("datetime64[ns]")
            hk = df["holding_symbol"].str.endswith(".HK", na=False)
            df.loc[hk, "holding_symbol"] = (
                df.loc[hk, "holding_symbol"].str.split(".").str[0].str.zfill(5) + ".HK"
            )
            df = df.drop_duplicates(
                ["fund_symbol", "holding_symbol", "report_period"], keep="last"
            ).sort_values(["report_period", "holding_symbol"])
        return df

    def _share_symbols(self, fund_symbol: str) -> list[str]:
        profile = self.con.execute(
            "SELECT name FROM fund_profiles WHERE symbol = ?", [fund_symbol]
        ).fetchone()
        if not profile or not profile[0]:
            return [fund_symbol]
        base_name = re.sub(r"[-－]?[A-CEIRXYZ](?:类)?$", "", str(profile[0]))
        return [
            row[0]
            for row in self.con.execute(
                "SELECT symbol FROM fund_profiles WHERE regexp_replace(name, '[-－]?[A-CEIRXYZ](类)?$', '') = ?",
                [base_name],
            ).fetchall()
        ] or [fund_symbol]

    def scales(self, fund_symbol: str) -> pd.DataFrame:
        symbols = self._share_symbols(fund_symbol)
        placeholders = ",".join("?" for _ in symbols)
        df = self.con.execute(
            f"SELECT symbol, report_period, scale_yi FROM fund_scale WHERE symbol IN ({placeholders}) AND report_period <= ?",
            [*symbols, self.as_of],
        ).df()
        if self.scales_overlay and self.scales_overlay.exists():
            records = json.loads(self.scales_overlay.read_text(encoding="utf-8")).get("records", [])
            overlay = pd.DataFrame(records)
            if not overlay.empty:
                overlay["report_period"] = pd.to_datetime(overlay["report_period"]).dt.date
                overlay = overlay[overlay["symbol"].isin(symbols) & (overlay["report_period"] <= self.as_of)]
                df = pd.concat([df, overlay[["symbol", "report_period", "scale_yi"]]], ignore_index=True)
        if not df.empty:
            df["report_period"] = pd.to_datetime(df["report_period"]).astype("datetime64[ns]")
            if self.as_of < date.today():
                reports = self.reports(fund_symbol)
                available = reports.dropna(subset=["announce_date"]).groupby("report_period")["announce_date"].min() if not reports.empty else pd.Series(dtype=object)
                available.index = pd.to_datetime(available.index)
                known = pd.to_datetime(df["report_period"].map(available), errors="coerce")
                valid = known.notna() & (known <= pd.Timestamp(self.as_of))
                if not valid.all():
                    self.quality_flags.append("historical_scale_publication_unverified")
                df = df[valid]
            df = df.drop_duplicates(["symbol", "report_period"], keep="first")
            df = df.groupby("report_period", as_index=False).agg(scale_yi=("scale_yi", lambda x: x.sum(min_count=len(symbols))))
            df["symbol"] = fund_symbol
        return df

    def manager_scales(
        self, manager_name: str, periods: list[pd.Timestamp], required_fund_symbol: str
    ) -> dict[pd.Timestamp, float]:
        if not periods:
            return {}
        tenures = self.con.execute(
            """
            SELECT fund_symbol, begin_date, end_date
            FROM fund_manager_tenures WHERE manager_name = ?
            """,
            [manager_name],
        ).df()
        if tenures.empty:
            return {}
        tenures["begin_date"] = pd.to_datetime(tenures["begin_date"])
        tenures["end_date"] = pd.to_datetime(tenures["end_date"])
        symbols = sorted(tenures["fund_symbol"].dropna().unique())
        share_groups: dict[str, list[str]] = {}
        all_shares: set[str] = set()
        for symbol in symbols:
            share_groups[symbol] = self._share_symbols(symbol)
            all_shares.update(share_groups[symbol])
        required_shares = self._share_symbols(required_fund_symbol)
        all_shares.update(required_shares)
        placeholders = ",".join("?" for _ in all_shares)
        scale = self.con.execute(
            f"SELECT symbol, report_period, scale_yi FROM fund_scale WHERE symbol IN ({placeholders})",
            sorted(all_shares),
        ).df()
        if scale.empty:
            return {}
        scale["report_period"] = pd.to_datetime(scale["report_period"])
        result: dict[pd.Timestamp, float] = {}
        for period in periods:
            active_funds = tenures[
                (tenures["begin_date"] <= period)
                & (tenures["end_date"].isna() | (tenures["end_date"] >= period))
            ]["fund_symbol"]
            active = {share for fund in active_funds for share in share_groups.get(fund, [fund])}
            active.update(required_shares)
            sample = scale[(scale["report_period"] == period) & scale["symbol"].isin(active)].dropna(subset=["scale_yi"])
            if set(sample["symbol"]) == active:
                result[period] = float(sample.drop_duplicates("symbol")["scale_yi"].sum())
        return result

    def turnover(self, fund_symbol: str) -> dict[int, float]:
        rows = self.con.execute(
            """SELECT report_year, turnover_rate FROM fund_annual_turnover
            WHERE fund_symbol = ? AND turnover_rate IS NOT NULL AND report_period <= ?
            QUALIFY row_number() OVER (PARTITION BY report_year ORDER BY CASE WHEN source='wind' THEN 0 ELSE 1 END, fetched_at DESC)=1
            ORDER BY report_year""", [fund_symbol, self.as_of],
        ).fetchall()
        if self.as_of < date.today() and rows:
            published = self.reports(fund_symbol)
            years = {pd.Timestamp(row.report_period).year for row in published.itertuples() if str(row.report_label).endswith("年报") and not str(row.report_label).endswith("半年报")}
            rows = [(year, value) for year, value in rows if int(year) in years]
        return {int(year): float(value) for year, value in rows}

    def reports(self, fund_symbol: str) -> pd.DataFrame:
        symbols = self._share_symbols(fund_symbol)
        placeholders = ",".join("?" for _ in symbols)
        lake = self.con.execute(
            f"""
            SELECT report_period, report_label, announce_date, strategy_analysis, outlook
            FROM fund_reports WHERE fund_symbol IN ({placeholders}) AND report_period <= ? AND (announce_date IS NULL OR announce_date <= ?)
            QUALIFY row_number() OVER (
                PARTITION BY report_label
                ORDER BY CASE WHEN fund_symbol = ? THEN 0 ELSE 1 END, report_period DESC
            ) = 1
            ORDER BY report_period, report_label
            """,
            [*symbols, self.as_of, self.as_of, fund_symbol],
        ).df()
        if not self.reports_overlay or not self.reports_overlay.exists():
            return lake
        payload = json.loads(self.reports_overlay.read_text(encoding="utf-8"))
        overlay = pd.DataFrame(payload.get("records") or [])
        if overlay.empty:
            return lake
        overlay = overlay.reindex(columns=["report_period", "report_label", "announce_date", "strategy_analysis", "outlook"])
        overlay["announce_date"] = pd.to_datetime(overlay["announce_date"], errors="coerce")
        overlay = overlay[overlay["announce_date"].notna() & (overlay["announce_date"] <= pd.Timestamp(self.as_of))]
        overlay["report_period"] = pd.to_datetime(overlay["report_period"])
        overlay = overlay[overlay["report_period"] <= pd.Timestamp(self.as_of)]
        if not lake.empty:
            lake["report_period"] = pd.to_datetime(lake["report_period"])
        return (
            pd.concat([overlay, lake], ignore_index=True)
            .drop_duplicates("report_label", keep="first")
            .sort_values(["report_period", "report_label"])
            .reset_index(drop=True)
        )

    def fund_found_date(self, fund_symbol: str) -> pd.Timestamp | None:
        row = self.con.execute(
            "SELECT found_date FROM fund_profiles WHERE symbol = ?", [fund_symbol]
        ).fetchone()
        return pd.Timestamp(row[0]) if row and row[0] else None

    def nav(self, fund_symbol: str) -> pd.Series:
        df = self.con.execute(
            """SELECT nav_date, adjusted_nav FROM fund_nav
            WHERE symbol=? AND adjusted_nav IS NOT NULL AND nav_date<=?
              AND (announce_date IS NULL OR announce_date<=?)
            ORDER BY nav_date, fetched_at DESC, source""", [fund_symbol, self.as_of, self.as_of],
        ).df()
        if self.nav_overlay.exists():
            overlay = self.con.execute(
                "SELECT nav_date,adjusted_nav FROM read_parquet(?) WHERE fund_symbol=? AND adjusted_nav IS NOT NULL AND nav_date<=?",
                [str(self.nav_overlay), fund_symbol, self.as_of],
            ).df()
            df = pd.concat([df, overlay], ignore_index=True)
        if df.empty:
            return pd.Series(dtype=float)
        df["nav_date"] = pd.to_datetime(df["nav_date"])
        conflicts = df.groupby("nav_date")["adjusted_nav"].nunique()
        if (conflicts > 1).any():
            self.quality_flags.append("nav_source_conflict")
        return df.drop_duplicates("nav_date", keep="first").set_index("nav_date")["adjusted_nav"].astype(float).sort_index()

    def enrich_positions(self, positions: pd.DataFrame) -> pd.DataFrame:
        if positions.empty:
            return positions
        df = positions.copy()
        symbols = sorted(df["holding_symbol"].dropna().unique())
        placeholders = ",".join("?" for _ in symbols)

        names = (
            self.con.execute(
                f"SELECT symbol, name FROM instruments WHERE symbol IN ({placeholders})", symbols
            )
            .df()
            .drop_duplicates("symbol", keep="last")
        )
        df = df.merge(names, left_on="holding_symbol", right_on="symbol", how="left")
        df["股票名称"] = df["name"].fillna(df["holding_symbol"])
        df = df.drop(columns=["symbol", "name"], errors="ignore")

        members = self.con.execute(
            f"""
            SELECT symbol, industry_code, as_of_date FROM industry_members
            WHERE classification_system = 'sw' AND symbol IN ({placeholders})
            ORDER BY symbol, as_of_date
            """,
            symbols,
        ).df()
        taxonomy = (
            self.con.execute(
                """
            SELECT industry_code, industry_name, level, parent_industry_code
            FROM industry_taxonomy
            """
            )
            .df()
            .drop_duplicates("industry_code", keep="last")
        )
        tax = {
            str(row.industry_code): (
                str(row.industry_name),
                str(row.level),
                None if pd.isna(row.parent_industry_code) else str(row.parent_industry_code),
            )
            for row in taxonomy.itertuples()
        }

        def hierarchy(code: object) -> tuple[str, str, str]:
            levels = {"L1": "", "L2": "", "L3": ""}
            current = "" if pd.isna(code) else str(code)
            for _ in range(4):
                item = tax.get(current)
                if not item:
                    break
                levels[item[1]] = item[0]
                current = item[2] or ""
            return levels["L1"], levels["L2"], levels["L3"]

        if not members.empty:
            members["as_of_date"] = pd.to_datetime(members["as_of_date"]).astype("datetime64[ns]")
            members = members.rename(columns={"symbol": "holding_symbol"}).drop_duplicates(["holding_symbol", "as_of_date"], keep="last")
            df = pd.merge_asof(df.sort_values(["report_period", "holding_symbol"]), members.sort_values(["as_of_date", "holding_symbol"]), left_on="report_period", right_on="as_of_date", by="holding_symbol", direction="backward")
            missing = df["industry_code"].isna()
            if missing.any():
                fallback = members.sort_values("as_of_date").drop_duplicates("holding_symbol", keep="last").set_index("holding_symbol")["industry_code"]
                df.loc[missing, "industry_code"] = df.loc[missing, "holding_symbol"].map(fallback)
                df["industry_current_fallback"] = missing & df["industry_code"].notna()
                self.quality_flags.append("industry_current_classification_fallback")
            levels = df["industry_code"].map(hierarchy)
            df[["申万一级", "申万二级", "申万三级"]] = pd.DataFrame(levels.tolist(), index=df.index)
        else:
            df[["申万一级", "申万二级", "申万三级"]] = ""
        df["大类"] = df["申万一级"].map(SW1_CATEGORY).fillna("")

        scales = self.scales(str(df["fund_symbol"].iloc[0])).set_index("report_period")["scale_yi"]
        df["基金规模"] = df["report_period"].map(scales)
        calculated = df["market_value"] / (df["基金规模"] * 1e8)
        native = pd.to_numeric(df["weight_nav"], errors="coerce") / 100
        native = native.where(native.between(0, 1))
        df["占净值比"] = native.combine_first(calculated)
        df["权重来源"] = native.notna().map({True: "native_weight_nav", False: "same_period_scale"})
        df["权重差异"] = native - calculated
        if (df["权重差异"].abs() > .001).any():
            self.quality_flags.append("native_weight_scale_disagreement")
        totals = df.groupby("report_period")["market_value"].transform("sum")
        df["股票市值权重"] = df["market_value"] / totals

        valuation = self.con.execute(
            f"""
            SELECT symbol, trade_date, pe_ttm, pb, total_mv
            FROM valuation_metrics WHERE symbol IN ({placeholders})
            ORDER BY symbol, trade_date
            """,
            symbols,
        ).df()
        if not valuation.empty:
            valuation["trade_date"] = pd.to_datetime(valuation["trade_date"]).astype("datetime64[ns]")
            valuation = valuation.rename(columns={"symbol": "holding_symbol"})
            df = pd.merge_asof(
                df.sort_values(["report_period", "holding_symbol"]),
                valuation.sort_values(["trade_date", "holding_symbol"]),
                left_on="report_period",
                right_on="trade_date",
                by="holding_symbol",
                direction="backward",
            )
            df["PE_raw"] = df["pe_ttm"]
            df["PB_raw"] = df["pb"]
            df["PE"] = df["pe_ttm"].clip(0, 200)
            df["PB"] = df["pb"].clip(0, 20)
            df["SIZE"] = df["total_mv"] / 1e8
        for column in ("PE", "PB", "SIZE"):
            if column not in df:
                df[column] = pd.NA
        df = self._add_roe(df, symbols)
        df = self._add_dividend_yield(df, symbols)
        return df

    def _add_roe(self, df: pd.DataFrame, symbols: list[str]) -> pd.DataFrame:
        placeholders = ",".join("?" for _ in symbols)
        items = self.con.execute(
            f"""
            SELECT symbol, report_period, item_code, item_value, announce_date
            FROM financial_statement_items
            WHERE symbol IN ({placeholders})
              AND item_code IN ('n_income_attr_p', 'total_hldr_eqy_inc_min_int')
              AND announce_date IS NOT NULL AND announce_date <= ?
            """,
            [*symbols, self.as_of],
        ).df()
        df["ROE"] = pd.NA
        if items.empty:
            return df
        items["report_date"] = pd.to_datetime(
            items["report_period"]
            .str.replace("Q1", "-03-31")
            .str.replace("Q2", "-06-30")
            .str.replace("Q3", "-09-30")
            .str.replace("Q4", "-12-31"),
            errors="coerce",
        ).astype("datetime64[ns]")
        wide = items.pivot_table(
            index=["symbol", "report_date"],
            columns="item_code",
            values="item_value",
            aggfunc="last",
        ).reset_index()
        required = {"n_income_attr_p", "total_hldr_eqy_inc_min_int"}
        if not required.issubset(wide.columns):
            return df
        income = {
            (row.symbol, row.report_date.year, row.report_date.month): row.n_income_attr_p
            for row in wide.dropna(subset=["report_date", "n_income_attr_p"]).itertuples()
        }

        def trailing_income(row: object) -> float | None:
            current = row.n_income_attr_p
            if pd.isna(current):
                return None
            if row.report_date.month == 12:
                return float(current)
            previous_annual = income.get((row.symbol, row.report_date.year - 1, 12))
            previous_same = income.get(
                (row.symbol, row.report_date.year - 1, row.report_date.month)
            )
            if previous_annual is None or previous_same is None:
                return None
            return float(current + previous_annual - previous_same)

        wide["ttm"] = [trailing_income(row) for row in wide.itertuples()]
        wide["ROE"] = wide["ttm"] / wide["total_hldr_eqy_inc_min_int"]
        wide["ROE_raw"] = wide["ROE"]
        wide["ROE"] = wide["ROE"].clip(-0.5, 1.0)
        available = items.groupby(["symbol", "report_date"])["announce_date"].max().rename("available_date").reset_index()
        wide = wide.merge(available, on=["symbol", "report_date"])
        wide["available_date"] = pd.to_datetime(wide["available_date"]).astype("datetime64[ns]")
        wide = wide.rename(columns={"symbol": "holding_symbol"}).dropna(
            subset=["report_date", "ROE"]
        )
        if wide.empty:
            return df
        base = df.drop(columns=["ROE"])
        return pd.merge_asof(
            base.sort_values(["report_period", "holding_symbol"]),
            wide[["holding_symbol", "available_date", "report_date", "ROE", "ROE_raw"]].rename(columns={"report_date": "roe_report_date"}).sort_values(
                ["available_date", "holding_symbol"]
            ),
            left_on="report_period",
            right_on="available_date",
            by="holding_symbol",
            direction="backward",
        )

    def _add_dividend_yield(self, df: pd.DataFrame, symbols: list[str]) -> pd.DataFrame:
        placeholders = ",".join("?" for _ in symbols)
        actions = self.con.execute(
            f"""
            SELECT symbol, ex_date, cash_dividend, source FROM corporate_actions
            WHERE symbol IN ({placeholders}) AND cash_dividend > 0
            """,
            symbols,
        ).df()
        bars = self.con.execute(
            f"""
            SELECT symbol, trade_date, close FROM daily_bars
            WHERE symbol IN ({placeholders}) ORDER BY symbol, trade_date
            """,
            symbols,
        ).df()
        df["股息率"] = pd.NA
        if bars.empty:
            return df
        bars["trade_date"] = pd.to_datetime(bars["trade_date"]).astype("datetime64[ns]")
        close = pd.merge_asof(
            df[["holding_symbol", "report_period"]].sort_values(
                ["report_period", "holding_symbol"]
            ),
            bars.rename(columns={"symbol": "holding_symbol"}).sort_values(
                ["trade_date", "holding_symbol"]
            ),
            left_on="report_period",
            right_on="trade_date",
            by="holding_symbol",
            direction="backward",
        )
        if actions.empty:
            return df
        actions["ex_date"] = pd.to_datetime(actions["ex_date"]).astype("datetime64[ns]")
        actions = actions.sort_values("source").drop_duplicates(
            ["symbol", "ex_date", "cash_dividend"]
        )
        dividends: dict[tuple[str, pd.Timestamp], float] = {}
        for row in (
            df[["holding_symbol", "report_period"]].drop_duplicates().itertuples(index=False)
        ):
            report_period = pd.Timestamp(row.report_period).as_unit("ns")
            trailing_start = report_period - pd.DateOffset(days=365)
            mask = (
                (actions["symbol"] == row.holding_symbol)
                & (actions["ex_date"] > trailing_start)
                & (actions["ex_date"] <= report_period)
            )
            dividends[(row.holding_symbol, report_period)] = float(
                actions.loc[mask, "cash_dividend"].sum()
            )
        close["dps"] = [
            dividends.get((r.holding_symbol, pd.Timestamp(r.report_period)), 0.0)
            for r in close.itertuples()
        ]
        close["股息率"] = close["dps"] / close["close"]
        return df.drop(columns=["股息率"]).merge(
            close[["holding_symbol", "report_period", "股息率"]],
            on=["holding_symbol", "report_period"],
            how="left",
        )

    def interval_vwap(self, intervals: pd.DataFrame) -> pd.DataFrame:
        if intervals.empty:
            return pd.DataFrame(columns=["holding_symbol", "period", "vwap"])
        local = duckdb.connect()
        local.register("intervals", intervals)
        local.execute(f"ATTACH '{self.database.as_posix()}' AS lake (READ_ONLY)")
        try:
            return local.execute(
                """
                SELECT i.holding_symbol, i.period,
                       CASE WHEN sum(b.volume) > 0 THEN sum(b.amount) / sum(b.volume) END AS vwap
                FROM intervals i
                LEFT JOIN lake.daily_bars b ON b.symbol = i.holding_symbol
                     AND b.trade_date >= i.start_date AND b.trade_date <= i.end_date AND b.trade_date <= ?
                GROUP BY i.holding_symbol, i.period
                """, [self.as_of]
            ).df()

        finally:
            local.close()

    def interval_dividends(self, intervals: pd.DataFrame) -> pd.DataFrame:
        if intervals.empty:
            return pd.DataFrame(columns=["holding_symbol", "period", "dps"])
        local = duckdb.connect()
        local.register("intervals", intervals)
        local.execute(f"ATTACH '{self.database.as_posix()}' AS lake (READ_ONLY)")
        try:
            return local.execute(
                """
                WITH actions AS (
                    SELECT symbol, ex_date, cash_dividend,
                           row_number() OVER (
                               PARTITION BY symbol, ex_date, cash_dividend ORDER BY source
                           ) AS duplicate_rank
                    FROM lake.corporate_actions WHERE cash_dividend > 0
                )
                SELECT i.holding_symbol, i.period, coalesce(sum(a.cash_dividend), 0) AS dps
                FROM intervals i LEFT JOIN actions a
                  ON a.symbol = i.holding_symbol AND a.duplicate_rank = 1
                 AND a.ex_date > i.start_date AND a.ex_date <= i.end_date AND a.ex_date <= ?
                GROUP BY i.holding_symbol, i.period
                """, [self.as_of]
            ).df()
        finally:
            local.close()
