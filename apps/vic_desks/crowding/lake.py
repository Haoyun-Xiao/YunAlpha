"""Read-only data-lake adapter for the public-fund crowding board.

The adapter intentionally opens an in-memory DuckDB connection and reads the
published Parquet files directly.  It never opens the lake's managed DuckDB
file and never writes to the lake.  The returned snapshot carries its dates,
source paths and coverage gaps so the UI cannot silently present a mixed-time
calculation as a live value.

This is a data adapter, not a claim that the original VicAlpha server formula
has been recovered.  Risk scores and other independent calculations belong in
``crowding.metrics`` after this layer has produced canonical input rows.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
from crowding.resources import connect as bounded_connect



from crowding.settings import LAKE_ROOT

DEFAULT_LAKE_ROOT = LAKE_ROOT


def _sql_path(path: Path) -> str:
    """Return a safely quoted POSIX path for a DuckDB string literal."""

    return path.resolve().as_posix().replace("'", "''")


def _parquet_glob(root: Path, dataset: str) -> Path:
    return root / "curated" / dataset / "**" / "*.parquet"


@dataclass(frozen=True)
class LakeSnapshotConfig:
    """Configuration for a single, immutable read snapshot."""

    lake_root: Path = DEFAULT_LAKE_ROOT
    as_of: date | None = None
    holding_period: date | None = None
    previous_holding_period: date | None = None
    turnover_days: int = 20
    stock_limit: int | None = None
    industry_system: str = "sw"
    industry_source: str = "sw"


class LakeSnapshot:
    """Build a public-fund holding snapshot from published lake partitions."""

    def __init__(self, config: LakeSnapshotConfig | None = None):
        config = config or LakeSnapshotConfig()
        root = Path(config.lake_root).expanduser().resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"lake root does not exist: {root}")
        self.config = LakeSnapshotConfig(
            lake_root=root,
            as_of=config.as_of,
            holding_period=config.holding_period,
            previous_holding_period=config.previous_holding_period,
            turnover_days=max(1, int(config.turnover_days)),
            stock_limit=config.stock_limit,
            industry_system=config.industry_system,
            industry_source=config.industry_source,
        )
        self._conn: duckdb.DuckDBPyConnection | None = None
        self.relation_map: dict[str, str] = {}
        self.classification: dict[str, Any] = {}

    def _connect(self) -> duckdb.DuckDBPyConnection:
        # A private in-memory database is important: connecting to the formal
        # ``data/duckdb/multi-asset-lake.duckdb`` can block ingestion writers.
        if self._conn is None:
            self._conn = bounded_connect()
            # Thread count is bounded by crowding.resources.
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "LakeSnapshot":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def as_of(self) -> date:
        if self.config.as_of is not None:
            return self.config.as_of
        # Do not use today's date as a data date.  The query below resolves the
        # latest available published trading day <= this bound.
        return date.today()

    def _read_path(self, dataset: str) -> str:
        path = _parquet_glob(self.config.lake_root, dataset)
        dataset_dir = self.config.lake_root / "curated" / dataset
        if not dataset_dir.exists():
            return ""
        return _sql_path(path)

    def _has_dataset(self, dataset: str) -> bool:
        dataset_dir = self.config.lake_root / "curated" / dataset
        return dataset_dir.exists() and any(dataset_dir.rglob("*.parquet"))

    def _scalar(self, sql: str, params: list[Any] | None = None) -> Any:
        value = self._connect().execute(sql, params or []).fetchone()
        return value[0] if value else None

    def _resolve_periods(self) -> tuple[date, date | None]:
        if not self._has_dataset("fund_holdings"):
            raise FileNotFoundError("published fund_holdings Parquet is missing")
        holdings = self._read_path("fund_holdings")
        cutoff = self.as_of
        # Full holding disclosures are normally Q2/Q4.  The field is read from
        # the file, not inferred from a partition directory, because the lake
        # has both quarterly and older physical layouts.
        from crowding.product_view import available_periods, partition_period
        paths = [str(p) for p in (self.config.lake_root/'curated/fund_holdings').rglob('*.parquet')
                 if partition_period(p) is None or partition_period(p).month in (6,12)]
        periods = [(p,) for p in reversed(available_periods(conn=self._connect(),holdings=paths,
            as_of=cutoff,start_period=date(1900,1,1))) if p.month in (6,12)]
        if not periods:
            raise ValueError(f"no half-year or year holding period <= {cutoff}")
        current = self.config.holding_period or periods[0][0]
        if current > cutoff:
            raise ValueError("holding_period cannot be later than as_of")
        previous = self.config.previous_holding_period
        if previous is None:
            previous = next((row[0] for row in periods if row[0] < current), None)
        if previous is not None and previous >= current:
            raise ValueError("previous_holding_period must be earlier than holding_period")
        return current, previous

    def _resolve_market_date(self) -> date | None:
        if not self._has_dataset("daily_bars"):
            return None
        bars = self._read_path("daily_bars")
        return self._scalar(
            f"SELECT MAX(trade_date) FROM read_parquet('{bars}', hive_partitioning=false) WHERE trade_date <= CAST(? AS DATE)",
            [self.as_of],
        )

    def _resolve_valuation_date(self, market_date: date | None) -> date | None:
        if not self._has_dataset("valuation_metrics"):
            return None
        valuation = self._read_path("valuation_metrics")
        return self._scalar(
            f"SELECT MAX(trade_date) FROM read_parquet('{valuation}', hive_partitioning=false) WHERE trade_date <= CAST(? AS DATE)",
            [market_date or self.as_of],
        )

    def _resolve_industry_date(self, market_date: date | None) -> date | None:
        if not market_date or not self._has_dataset("industry_members"):
            return None
        industries = self._read_path("industry_members")
        return self._scalar(
            f"""
            SELECT MAX(as_of_date)
            FROM read_parquet('{industries}', hive_partitioning=false)
            WHERE as_of_date <= CAST(? AS DATE)
              AND classification_system = ? AND source = ?
            """,
            [market_date, self.config.industry_system, self.config.industry_source],
        )

    def snapshot(self) -> dict[str, Any]:
        """Return an auditable stock-level snapshot.

        Rows are aggregated across the selected published full-holding period.
        ``holding_mv`` is estimated as disclosed shares × the latest unadjusted
        close, while ``float_mv`` is the lake's valuation metric.  Consequently
        ``hold_float_ratio`` is explicitly a float-market-value proxy, not a
        free-float-share ratio.  Missing values remain ``None``.
        """

        current_period, previous_period = self._resolve_periods()
        from crowding.product_view import period_paths
        holding_paths = list((self.config.lake_root / 'curated/fund_holdings').rglob('*.parquet'))
        current_paths = period_paths(holding_paths,current_period)
        current_source = 'read_parquet([' + ','.join("'" + _sql_path(Path(p)) + "'" for p in current_paths) + '], hive_partitioning=false, union_by_name=true)'
        latest_price_date = self._resolve_market_date()
        latest_valuation_date = self._resolve_valuation_date(latest_price_date)
        market_date = latest_price_date
        if latest_price_date and latest_valuation_date and latest_price_date != latest_valuation_date:
            # One complete generation must not divide today's holding values
            # by yesterday's float market value. Select an observed shared day;
            # retain both arrival dates so the delay remains visible.
            bars_path=self._read_path('daily_bars');val_path=self._read_path('valuation_metrics')
            market_date=self._scalar(f"""SELECT MAX(b.trade_date) FROM
                (SELECT DISTINCT trade_date FROM read_parquet('{bars_path}',hive_partitioning=false)
                 WHERE trade_date<=CAST(? AS DATE)) b JOIN
                (SELECT DISTINCT trade_date FROM read_parquet('{val_path}',hive_partitioning=false)
                 WHERE trade_date<=CAST(? AS DATE)) v USING(trade_date)""",
                [latest_price_date,latest_price_date])
        valuation_date = self._resolve_valuation_date(market_date) if market_date else None
        industry_date = self._resolve_industry_date(market_date)
        root = self.config.lake_root
        holdings = self._read_path("fund_holdings")
        bars = self._read_path("daily_bars") if market_date else ""
        valuation = self._read_path("valuation_metrics") if valuation_date else ""
        share_structure = self._read_path("share_structure") if self._has_dataset("share_structure") else ""
        daily_float_root = root / 'derived' / 'daily_free_float_shares'
        daily_float = str(daily_float_root / '**' / '*.parquet') if daily_float_root.exists() else ''
        industries = self._read_path("industry_members") if industry_date else ""
        instruments = self._read_path("instruments") if self._has_dataset("instruments") else ""

        # Enable the shared all-public-fund portfolio only when relation input
        # exists. Legacy no-relation fixture behavior remains unchanged.
        relation_map: dict[str, str] = {}
        relation_diag: dict[str, Any] = {'relation_status': 'not performed; fund-level rows are aggregated by fund_symbol as published',
            'verified_products': [], 'unknown': [], 'conflicts': []}
        product_coverage = {}
        shared_positions = False
        relation_files = list((root / 'curated' / 'fund_product_share_observations').rglob('*.parquet'))
        from crowding.fund_classification import classify_fund_profiles
        classification = classify_fund_profiles(lake_root=root)
        self.classification = classification
        fund_category = {row['symbol']: row['category'] for row in classification['rows']}
        product_metadata = {}
        if relation_files:
            from crowding.product_view import product_view_context, load_product_view, register_product_rows
            context = product_view_context(lake_root=root, as_of=self.as_of, classification=classification)
            product_view = load_product_view(as_of=self.as_of, report_period=current_period,
                lake_root=root, context=context, conn=self._connect())
            register_product_rows(self._connect(), 'shared_product_holdings', product_view, all_rows=True)
            shared_positions = True
            product_metadata = product_view['products']
            product_coverage = product_view['coverage']
            exact_products = [p for p,m in product_metadata.items() if m['relation_status']=='exact']
            approx_products = [p for p,m in product_metadata.items() if m['relation_status']=='approx']
            for product, meta in product_metadata.items():
                if meta['relation_status'] != 'unknown':
                    relation_map.update({s.removesuffix('.OF'):product.removesuffix('.OF') for s in meta['share_symbols']})
                if fund_category.get(product,'unknown') == 'unknown' and meta['category'] != 'active_from_caller':
                    fund_category[product] = meta['category']
            present_codes = {s.removesuffix('.OF') for m in product_metadata.values() for s in m['share_symbols']}
            present_codes |= {str(s).removesuffix('.OF') for conflict in product_view['diagnostics']['conflicts'] for s in conflict.get('shares',[])}
            candidates = [row for row in context.relation_observations if row['share_code'] in present_codes]
            candidate_codes = {r['share_code'] for r in candidates}
            relation_diag = {'relation_status':'shared_period_product_view_exact_and_approx',
                'verified_products':exact_products, 'approx_products':approx_products,
                'verified_share_codes':sum(len(product_metadata[p]['share_symbols']) for p in exact_products),
                'candidate_products':sorted({r['master_code'] for r in candidates}),
                'candidate_share_codes':sorted(candidate_codes),
                'unmapped_observed_codes':sorted(candidate_codes - relation_map.keys()),
                'conflicts':product_view['diagnostics']['conflicts'],
                'unknown':product_view['diagnostics']['unknown'],
                'cache':product_view.get('cache'), 'scope':product_view['scope']}
            self.relation_map = relation_map
            del product_view

        profile_paths = [str(p) for p in (root / 'curated' / 'fund_profiles').rglob('*.parquet')]
        profile_rows = self._connect().execute(
            """SELECT symbol, management FROM read_parquet(?, hive_partitioning=false, union_by_name=true)
               QUALIFY ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY fetched_at DESC NULLS LAST)=1""",
            [profile_paths],
        ).fetchall() if profile_paths else []
        fund_management = {symbol: management for symbol, management in profile_rows}
        for product, meta in product_metadata.items():
            managers = {fund_management[s] for s in meta['share_symbols'] if fund_management.get(s)}
            if not fund_management.get(product) and len(managers)==1:
                fund_management[product] = next(iter(managers))
        self._connect().execute(
            'CREATE OR REPLACE TEMP TABLE fund_meta(symbol VARCHAR, category VARCHAR, management VARCHAR)')
        meta_rows = [(symbol, fund_category.get(symbol, 'unknown'), fund_management.get(symbol))
                     for symbol in sorted(set(fund_category) | set(fund_management))]
        if meta_rows:
            self._connect().executemany('INSERT INTO fund_meta VALUES (?, ?, ?)', meta_rows)

        # The latest disclosure version for each fund/stock/period is chosen
        # before aggregation.  This removes duplicate physical partitions and
        # preserves the announcement date used for PIT diagnostics.
        bars_sql = (
            f"LEFT JOIN (SELECT symbol, MAX(close) AS close FROM read_parquet('{bars}', hive_partitioning=false) WHERE trade_date = CAST(? AS DATE) GROUP BY symbol) b ON b.symbol = h.holding_symbol"
            if bars
            else "LEFT JOIN (SELECT NULL::VARCHAR symbol, NULL::DATE trade_date, NULL::DOUBLE close, NULL::DOUBLE amount) b ON false"
        )
        valuation_sql = (
            f"LEFT JOIN (SELECT symbol, MAX(float_mv) AS float_mv FROM read_parquet('{valuation}', hive_partitioning=false) WHERE trade_date = CAST(? AS DATE) GROUP BY symbol) v ON v.symbol = h.holding_symbol"
            if valuation
            else "LEFT JOIN (SELECT NULL::VARCHAR symbol, NULL::DATE trade_date, NULL::DOUBLE float_mv) v ON false"
        )
        share_sql = (
            f"""LEFT JOIN (
                SELECT symbol, free_float_shares, change_date
                FROM read_parquet('{share_structure}', hive_partitioning=false)
                WHERE change_date <= CAST(? AS DATE)
                  AND announce_date IS NOT NULL AND announce_date <= CAST(? AS DATE)
                  AND free_float_shares IS NOT NULL AND free_float_shares > 0
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY symbol ORDER BY change_date DESC, announce_date DESC,
                    fetched_at DESC NULLS LAST
                ) = 1
            ) s ON s.symbol = h.holding_symbol"""
            if share_structure
            else "LEFT JOIN (SELECT NULL::VARCHAR symbol, NULL::DOUBLE free_float_shares, NULL::DATE change_date) s ON false"
        )
        daily_basic_sql = (
            f"""LEFT JOIN (
                SELECT symbol,
                       MAX(free_float_shares) AS free_float_shares,
                       MAX(source) AS source,
                       CAST(? AS DATE) AS snapshot_date
                FROM read_parquet('{daily_float}', hive_partitioning=false, union_by_name=true)
                WHERE trade_date = CAST(? AS DATE)
                GROUP BY symbol
            ) d ON d.symbol = h.holding_symbol"""
            if daily_float
            else "LEFT JOIN (SELECT NULL::VARCHAR AS symbol, NULL::DOUBLE AS free_float_shares, NULL::VARCHAR AS source, NULL::DATE AS snapshot_date) d ON false"
        )
        relation_sql = "LEFT JOIN (SELECT NULL::VARCHAR AS share_code, NULL::VARCHAR AS master_code) rm ON false"
        taxonomy = self._read_path('industry_taxonomy') if self._has_dataset('industry_taxonomy') else ''
        if industries and industry_date and self.config.industry_system == 'sw' and taxonomy:
            # Lake SW members may be at L1/L2/L3; join the actual taxonomy rather
            # than mixing all levels in one table or guessing from name/code.
            industry_sql = f"""LEFT JOIN (
                WITH t AS (SELECT industry_code, parent_industry_code, industry_name, level
                    FROM read_parquet('{taxonomy}', hive_partitioning=false)
                    WHERE classification_system='SW2021'
                    QUALIFY ROW_NUMBER() OVER (PARTITION BY industry_code ORDER BY fetched_at DESC NULLS LAST)=1),
                m AS (SELECT symbol, industry_code FROM read_parquet('{industries}', hive_partitioning=false)
                    WHERE as_of_date=CAST(? AS DATE) AND classification_system='sw' AND source='sw'
                    QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY fetched_at DESC NULLS LAST)=1)
                SELECT m.symbol, CASE WHEN l.level='L1' THEN l.industry_name
                    WHEN l.level='L2' THEN p.industry_name WHEN l.level='L3' THEN gp.industry_name END industry_name
                FROM m LEFT JOIN t l ON l.industry_code=m.industry_code
                LEFT JOIN t p ON p.industry_code=l.parent_industry_code
                LEFT JOIN t gp ON gp.industry_code=p.parent_industry_code
            ) i ON i.symbol = h.holding_symbol"""
        elif industries and industry_date:
            industry_sql = f"LEFT JOIN (SELECT symbol, MAX(industry_name) AS industry_name FROM read_parquet('{industries}', hive_partitioning=false) WHERE as_of_date = CAST(? AS DATE) AND classification_system = '{self.config.industry_system}' AND source = '{self.config.industry_source}' GROUP BY symbol) i ON i.symbol = h.holding_symbol"
        else:
            industry_sql = "LEFT JOIN (SELECT NULL::VARCHAR symbol, NULL::VARCHAR industry_name, NULL::DATE as_of_date) i ON false"
        instruments_sql = (
            f"LEFT JOIN (SELECT symbol, MAX(name) AS instrument_name FROM read_parquet('{instruments}', hive_partitioning=false) GROUP BY symbol) n ON n.symbol = h.holding_symbol"
            if instruments
            else "LEFT JOIN (SELECT NULL::VARCHAR symbol, NULL::VARCHAR instrument_name) n ON false"
        )
        params: list[Any] = [current_period, self.as_of]
        if bars:
            params.append(market_date)
        if valuation:
            params.append(valuation_date)
        if share_structure:
            params.extend([market_date or self.as_of, self.as_of])
        if daily_float:
            params.extend([market_date, market_date])
        if industries and industry_date:
            params.append(industry_date)


        ratio_allowed = valuation_date is not None and valuation_date == market_date

        if shared_positions:
            holding_ctes = """latest AS (
                SELECT h.* EXCLUDE(report_period,announce_date),
                    TRY_CAST(h.report_period AS DATE) AS report_period,
                    TRY_CAST(h.announce_date AS DATE) AS announce_date,
                    h.product_symbol AS effective_fund_symbol
                FROM shared_product_holdings h
            """
            params = params[2:]
        else:
            holding_ctes = f"""raw AS (
            SELECT h.*,
                   ROW_NUMBER() OVER (
                     PARTITION BY h.fund_symbol, h.holding_symbol, h.report_period
                     ORDER BY h.announce_date DESC NULLS LAST, h.fetched_at DESC NULLS LAST
                   ) AS rn
            FROM {current_source} h
            WHERE TRY_CAST(h.report_period AS DATE) = CAST(? AS DATE)
              AND h.announce_date IS NOT NULL
              AND h.announce_date <= CAST(? AS DATE)
              AND h.holding_symbol ~ '^[0-9]{{6}}\\.(SH|SZ|BJ)$'
        ), latest_raw AS (
            SELECT * FROM raw WHERE rn = 1
        ), mapped AS (
            SELECT h.*, COALESCE(rm.master_code, h.fund_symbol) AS effective_fund_symbol,
                   ROW_NUMBER() OVER (
                     PARTITION BY COALESCE(rm.master_code, h.fund_symbol), h.holding_symbol, h.report_period
                     ORDER BY CASE WHEN rm.master_code IS NOT NULL THEN 0 ELSE 1 END,
                              h.announce_date DESC NULLS LAST, h.fetched_at DESC NULLS LAST
                   ) AS merge_rn
            FROM latest_raw h
            {relation_sql}
        ), latest AS (
            SELECT * FROM mapped WHERE merge_rn=1
"""
        sql = f"""
        WITH {holding_ctes}        ), product_positions AS (
            SELECT holding_symbol, effective_fund_symbol, MAX(market_value) AS market_value
            FROM latest GROUP BY holding_symbol, effective_fund_symbol
        ), product_enriched AS (
            SELECT p.*, COALESCE(fm.category, 'unknown') AS category, fm.management
            FROM product_positions p LEFT JOIN fund_meta fm ON fm.symbol=p.effective_fund_symbol
        ), product_ranked AS (
            SELECT *, ROW_NUMBER() OVER(
                PARTITION BY holding_symbol ORDER BY market_value DESC NULLS LAST,effective_fund_symbol
            ) AS product_rank
            FROM product_enriched
        ), ownership AS (
            SELECT holding_symbol,
                   SUM(market_value) FILTER (WHERE product_rank<=5) AS top5_mv,
                   SUM(market_value) FILTER (WHERE category IN ('etf','passive_index','active_index_enhanced')) AS passive_mv,
                   COUNT(DISTINCT management) AS n_corps
            FROM product_ranked GROUP BY holding_symbol
        )
        SELECT h.holding_symbol AS symbol,
               COALESCE(MAX(h.holding_name), MAX(n.instrument_name)) AS name,
               COUNT(DISTINCT h.effective_fund_symbol) AS fund_count,
               SUM(h.shares) AS shares,
               SUM(h.market_value) AS reported_mv,
               CASE WHEN COUNT(b.close) > 0 THEN SUM(h.shares) * MAX(b.close) END AS holding_mv,
               MAX(v.float_mv) AS float_mv,
               CASE WHEN {"TRUE" if ratio_allowed else "FALSE"} AND MAX(v.float_mv) > 0 AND COUNT(b.close) > 0
                    THEN SUM(h.shares) * MAX(b.close) / MAX(v.float_mv) END AS hold_float_ratio,
               COALESCE(MAX(d.free_float_shares), MAX(s.free_float_shares)) AS free_float_shares,
               MAX(s.free_float_shares) AS pit_free_float_shares,
               MAX(d.free_float_shares) AS daily_snapshot_free_float_shares,
               MAX(d.source) AS daily_snapshot_source,
               MAX(s.change_date) AS free_float_change_date,
               MAX(d.snapshot_date) AS free_float_snapshot_date,
               CASE WHEN MAX(d.free_float_shares) > 0
                    THEN SUM(h.shares) / MAX(d.free_float_shares)
                    WHEN MAX(s.free_float_shares) > 0
                    THEN SUM(h.shares) / MAX(s.free_float_shares)
                    WHEN {"TRUE" if ratio_allowed else "FALSE"} AND MAX(v.float_mv) > 0 AND COUNT(b.close) > 0
                    THEN SUM(h.shares) * MAX(b.close) / MAX(v.float_mv)
               END AS hold_float_ratio_display,
               CASE WHEN MAX(d.free_float_shares) > 0 THEN 'daily_basic_free_share_snapshot'
                    WHEN MAX(s.free_float_shares) > 0 THEN 'share_structure_exact'
                    WHEN {"TRUE" if ratio_allowed else "FALSE"} AND MAX(v.float_mv) > 0 AND COUNT(b.close) > 0
                    THEN 'float_mv_proxy' END AS hold_float_ratio_basis,
               MAX(b.close) AS close,
               MAX(i.industry_name) AS industry_name,
               MAX(h.announce_date) AS latest_announce_date,
               COALESCE(MAX(o.top5_mv),0) AS top5_mv,
               COALESCE(MAX(o.passive_mv),0) AS passive_mv,
               COALESCE(MAX(o.n_corps),0) AS n_corps
        FROM latest h
        LEFT JOIN ownership o ON o.holding_symbol=h.holding_symbol
        {bars_sql}
        {valuation_sql}
        {share_sql}
        {daily_basic_sql}
        {industry_sql}
        {instruments_sql}
        GROUP BY h.holding_symbol
        ORDER BY holding_mv DESC NULLS LAST, symbol
        """
        rows = self._connect().execute(sql, params).fetchall()
        columns = [item[0] for item in self._connect().description]
        records = [dict(zip(columns, row)) for row in rows]
        if shared_positions:
            self._connect().unregister('shared_product_holdings')
        for row in records:
            reported = row.get('reported_mv')
            row['top5_pct'] = (row.pop('top5_mv') / reported * 100
                               if reported not in (None, 0) else None)
            row['passive_pct'] = (row.pop('passive_mv') / reported * 100
                                  if reported not in (None, 0) else None)
        if self.config.stock_limit is not None:
            records = records[: max(0, int(self.config.stock_limit))]

        previous_count = 0
        if previous_period is not None:
            previous_paths = period_paths(holding_paths,previous_period)
            previous_source = 'read_parquet([' + ','.join("'" + _sql_path(Path(p)) + "'" for p in previous_paths) + '], hive_partitioning=false, union_by_name=true)'
            previous_count = int(
                self._scalar(
                    f"""
                    SELECT COUNT(*) FROM (
                      SELECT DISTINCT fund_symbol, holding_symbol
                      FROM {previous_source}
                      WHERE TRY_CAST(report_period AS DATE) = CAST(? AS DATE)
                        AND announce_date IS NOT NULL
                        AND announce_date <= CAST(? AS DATE)
                    )
                    """,
                    [previous_period, self.as_of],
                )
                or 0
            )

        gaps: list[dict[str, Any]] = []
        if not market_date:
            gaps.append({"field": "close", "reason": "daily_bars has no trade date at or before as_of"})
        if not valuation_date:
            gaps.append({"field": "float_mv", "reason": "valuation_metrics has no date at or before as_of"})
        missing_close = sum(row.get("close") is None for row in records)
        missing_float = sum(row.get("float_mv") is None for row in records)
        pit_available = sum(row.get("pit_free_float_shares") is not None for row in records)
        daily_basic_available = sum(row.get("daily_snapshot_free_float_shares") is not None for row in records)
        overlap_float = sum(
            row.get("pit_free_float_shares") is not None and row.get("daily_snapshot_free_float_shares") is not None
            for row in records
        )
        overlap_diff_gt_1pct = sum(
            bool(row.get("pit_free_float_shares") and row.get("daily_snapshot_free_float_shares")
            and abs(row["pit_free_float_shares"] / row["daily_snapshot_free_float_shares"] - 1) > 0.01)
            for row in records
        )
        if missing_close:
            gaps.append({"field": "close", "rows_missing": missing_close, "reason": "symbol/date coverage"})
        if missing_float:
            gaps.append({"field": "float_mv", "rows_missing": missing_float, "reason": "float market value coverage"})
        if records and max(pit_available, daily_basic_available) < len(records):
            gaps.append({"field": "free_float_shares", "rows_with_pit_value": pit_available,
                         "rows_with_daily_snapshot": daily_basic_available,
                         "rows_missing": len(records) - max(pit_available, daily_basic_available),
                         "reason": "share_structure PIT or daily_basic free_share snapshot coverage; daily snapshot has no announcement date"})
        return {
            "rows": records,
            "metadata": {
                "as_of": self.as_of.isoformat(),
                "holding_period": current_period.isoformat(),
                "previous_holding_period": previous_period.isoformat() if previous_period else None,
                "market_date": market_date.isoformat() if market_date else None,
                "valuation_date": valuation_date.isoformat() if valuation_date else None,
                "latest_price_date": latest_price_date.isoformat() if latest_price_date else None,
                "latest_valuation_date": latest_valuation_date.isoformat() if latest_valuation_date else None,
                "market_selection_policy": "latest observed date shared by prices and valuation; missing dataset remains a gap",
                "market_arrival_delayed": latest_price_date != market_date,
                "holding_unit": "shares / CNY market value",
                "reported_market_value": "fund_holdings.market_value in CNY at report_period; latest disclosure version after PIT filtering",
                "float_ratio_definition": "display ratio uses shares/free_float_shares when PIT share_structure is available; otherwise shares*latest_unadjusted_close/valuation_metrics.float_mv proxy",
                "float_ratio_status": "exact_or_proxy" if ratio_allowed else "exact_only_or_missing",
                "free_float_coverage": {"rows_with_pit": pit_available, "rows_with_daily_snapshot": daily_basic_available,
                                        "rows_total": len(records), "overlap_rows": overlap_float,
                                        "overlap_diff_gt_1pct": overlap_diff_gt_1pct,
                                        "selection_priority": "daily_free_float_shares_same_trade_date > share_structure_latest_known_change",
                                        "sources": ["share_structure", "daily_free_float_shares"] if daily_float else ["share_structure"],
                                        "cutoff": self.as_of.isoformat()},
                "industry_date": industry_date.isoformat() if industry_date else None,
                "industry_classification": ('SW2021 一级' if self.config.industry_system == 'sw' and taxonomy else f"{self.config.industry_system} / {self.config.industry_source}") if industry_date else None,
                "universe_scope": "all funds with published stock holdings; active/passive filter not applied",
                "a_c_merge": relation_diag.get('relation_status', 'not_attempted'),
                "verified_relation_products": len(relation_diag.get('verified_products', [])),
                "verified_relation_share_codes": relation_diag.get("verified_share_codes", len(relation_map)),
                "approx_relation_products": len(relation_diag.get("approx_products", [])),
                "product_coverage": product_coverage,
                "product_view_cache": relation_diag.get("cache"),
                "passive_definition": "etf + passive_index + active_index_enhanced reported market value",
                "relation_candidate_products": len(relation_diag.get('candidate_products', [])),
                "relation_candidate_share_codes": len(relation_diag.get('candidate_share_codes', [])),
                "relation_unmapped_observed_codes": len(relation_diag.get('unmapped_observed_codes', [])),
                "relation_conflict_count": len(relation_diag.get('conflicts', [])),
                "warnings": [
                    "当前样本包含所有已发布股票持仓基金，尚未按主动/被动基金筛选。",
                    (f"共享按期产品视图：当期验证 {len(relation_diag.get('verified_products', []))} 个产品，"
                     f"近似关系回看 {len(relation_diag.get('approx_products', []))} 个产品；"
                     f"未知关系份额 {len(relation_diag.get('unknown', []))} 个独立保留，"
                     f"排除持仓冲突 {product_coverage.get('excluded_conflict_groups', 0)} 组；不是全部已验证产品数。"
                     if shared_positions else "当前没有在知识截止日之前验证通过的产品关系，基金数量和集中度按已发布份额统计。"),
                    "占流通比优先使用同日 daily_free_float_shares，缺失时使用公告时点可知的 share_structure，再缺失时使用流通市值代理；三者口径不可混同。",
                ] + ([f"价格已到{latest_price_date}，估值已到{latest_valuation_date}；综合指标统一使用共同交易日{market_date}，待数据到齐后发布新版。"] if latest_price_date != market_date else [])
                  + ([] if ratio_allowed else ["估值日与行情日不一致，占流通比已留空。"]),
                "previous_distinct_fund_stock_rows": previous_count,
                "source_datasets": [
                    "fund_holdings", "daily_bars", "valuation_metrics", "share_structure", "daily_free_float_shares", "fund_product_share_observations", "industry_members", "industry_taxonomy", "instruments"
                ],
                "read_only": True,
                "lake_root": str(root),
            },
            "gaps": gaps,
        }


def snapshot(
    *,
    lake_root: str | os.PathLike[str] | None = None,
    as_of: date | None = None,
    holding_period: date | None = None,
    stock_limit: int | None = None,
) -> dict[str, Any]:
    """Convenience wrapper for one read-only snapshot."""

    root = Path(lake_root or DEFAULT_LAKE_ROOT)
    with LakeSnapshot(LakeSnapshotConfig(root, as_of=as_of, holding_period=holding_period, stock_limit=stock_limit)) as view:
        return view.snapshot()
