"""Read-only fund/ETF panel for the public-fund crowding dashboard.

This module deliberately computes only from published lake partitions.  It
does not attempt to infer missing scale, holder or A/C relationship data.  A
field is returned as ``None`` when its required dataset is unavailable and a
diagnostic is included in the response.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from math import isfinite
from pathlib import Path
from typing import Any

import duckdb
from crowding.resources import connect as bounded_connect

from crowding.lake import DEFAULT_LAKE_ROOT
from crowding.fund_classification import classify_fund_profiles
from crowding.verified_holdings import verified_product_mapping


def _glob(root: Path, dataset: str) -> str | None:
    # Most lake datasets are curated; fund_scale is a documented derived
    # snapshot and therefore lives under ``lake/derived``.
    for base in ("curated", "derived"):
        directory = root / base / dataset
        if directory.exists() and any(directory.rglob("*.parquet")):
            return (directory / "**" / "*.parquet").resolve().as_posix()
    return None


def _json_rows(rows: list[tuple], columns: list[str]) -> list[dict[str, Any]]:
    return [dict(zip(columns, row)) for row in rows]


def _f(v: Any) -> float | None:
    return None if v is None else float(v)


def _fund_class_filter() -> str:
    # Only explicit stock/fund investment types are admitted.  A NULL or
    # generic ``混合型`` label is not enough to call a product an active fund;
    # those rows remain outside the panel until a product-level mapping is
    # verified.  Enhanced-index funds are intentionally retained.
    return """
      (
        (p.fund_type='股票型' AND p.invest_type IN
          ('股票型','普通股票型','增强指数型','成长型','价值型','积极成长型','科技型','创新型','优选增长型'))
        OR
        (p.fund_type='混合型' AND p.invest_type IN
          ('灵活配置型','平衡型','股票型','成长型','价值型','积极成长型','积极配置型','增长型','优选增长型','创新型','科技型','普通股票型'))
      )
      AND COALESCE(p.name, '') NOT LIKE '%ETF联接%'
      AND COALESCE(p.name, '') NOT LIKE '%FOF%'
      AND COALESCE(p.name, '') NOT LIKE '%ETF%'
    """


def fund_panel(*, lake_root: str | Path = DEFAULT_LAKE_ROOT,
               as_of: date | None = None,
               return_start: date | None = None,
               limit: int = 50,
               board_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the fund/ETF panel from the lake without writing to it.

    ``pool_mv`` uses shares multiplied by the latest common unadjusted close
    for the selected data date.  Returns use adjusted NAV where available.  Net
    asset and scale-dependent fields remain missing unless the separately
    audited ``fund_scale`` dataset is present; ``fund_nav.asset_nav`` is the
    provider's ``net_asset`` field but is not treated as a quarterly scale
    observation here.
    """
    root = Path(lake_root).expanduser().resolve()
    bound = as_of or date.today()
    paths = {d: _glob(root, d) for d in (
        "fund_holdings", "fund_profiles", "fund_nav", "etf_profiles",
        "etf_daily_metrics", "fund_shares_outstanding", "fund_scale", "daily_bars",
        "valuation_metrics", "trading_calendar")}
    required = ["fund_holdings", "fund_profiles"]
    missing = [d for d in required if not paths[d]]
    if missing:
        return {"as_of": None, "holding_period": None, "funds": [],
                "nav_dist": {"bins": []}, "etfs": {"rows": []},
                "warnings": [f"缺少数据集：{', '.join(missing)}"]}

    c = bounded_connect()
    try:
        classification = classify_fund_profiles(lake_root=root)
        active_symbols = classification.get('active_symbols', [])
        c.execute("CREATE TEMP TABLE active_fund_symbols(symbol VARCHAR PRIMARY KEY, category VARCHAR)")
        category_rows = [(row['symbol'], row['category']) for row in classification.get('rows', [])
                         if row.get('is_active_for_crowding') is True]
        if category_rows:
            c.executemany("INSERT INTO active_fund_symbols VALUES (?, ?)", category_rows)
        # The current published full holding period is the latest half-year or
        # annual disclosure available by the requested date.
        period = c.execute(f"""
          SELECT MAX(TRY_CAST(report_period AS DATE))
          FROM read_parquet('{paths['fund_holdings']}', hive_partitioning=false)
          WHERE TRY_CAST(report_period AS DATE) <= CAST(? AS DATE)
            AND EXTRACT(MONTH FROM TRY_CAST(report_period AS DATE)) IN (6, 12)
            AND announce_date IS NOT NULL AND announce_date <= CAST(? AS DATE)
        """, [bound, bound]).fetchone()[0]
        if period is None:
            return {"as_of": None, "holding_period": None, "funds": [],
                    "nav_dist": {"bins": []}, "etfs": {"rows": []},
                    "warnings": ["没有可用的半年报/年报持仓期"]}

        relation_files = list((root / 'curated' / 'fund_product_share_observations').rglob('*.parquet'))
        relation_map = {}
        relation_diag = {'relation_status': 'not_attempted', 'verified_products': [],
                         'candidate_share_codes': [], 'unmapped_observed_codes': [], 'conflicts': []}
        if relation_files:
            period_label = f"{period.year}Q{((period.month - 1) // 3) + 1}"
            relation_codes = [r[0] for r in c.execute(
                "SELECT DISTINCT share_code FROM read_parquet(?,hive_partitioning=false) WHERE report_period=? AND announce_date<=?",
                [[str(p) for p in relation_files], period_label, bound]).fetchall()]
            if relation_codes:
                relation_rows = c.execute(
                    f"""SELECT regexp_replace(fund_symbol,'\\..*$',''),holding_symbol,'{period_label}',shares,market_value,announce_date
                        FROM read_parquet('{paths['fund_holdings']}',hive_partitioning=false)
                        WHERE TRY_CAST(report_period AS DATE)=CAST(? AS DATE) AND announce_date<=CAST(? AS DATE)
                          AND regexp_replace(fund_symbol,'\\..*$','') IN (SELECT * FROM UNNEST(?))
                        QUALIFY ROW_NUMBER() OVER(PARTITION BY fund_symbol,holding_symbol,report_period ORDER BY announce_date DESC,fetched_at DESC)=1""",
                    [period, bound, relation_codes]).fetchall()
                holding_rows = [{'fund_symbol':r[0],'holding_symbol':r[1],'report_period':r[2],
                                 'shares':r[3],'market_value':r[4],
                                 'announce_date':r[5].isoformat() if hasattr(r[5],'isoformat') else r[5]}
                                for r in relation_rows]
                from datetime import datetime, time, timezone
                relation_diag = verified_product_mapping(
                    holding_rows, lake_root=root, report_period=period_label,
                    announcement_cutoff=bound.isoformat(),
                    knowledge_cutoff=datetime.combine(bound,time.max,tzinfo=timezone.utc))
                relation_map = relation_diag.get('share_to_master', {})
                if relation_map:
                    c.execute('CREATE TEMP TABLE verified_relations(share_code VARCHAR,master_code VARCHAR)')
                    c.executemany('INSERT INTO verified_relations VALUES (?,?)',list(relation_map.items()))

        # One latest published observation per fund/security/period prevents
        # overlapping physical partitions from inflating all aggregates.
        raw_h = f"""
          SELECT * EXCLUDE(rn) FROM (
            SELECT h.*, ROW_NUMBER() OVER (
              PARTITION BY fund_symbol, holding_symbol, report_period
              ORDER BY announce_date DESC NULLS LAST, fetched_at DESC NULLS LAST
            ) rn
            FROM read_parquet('{paths['fund_holdings']}', hive_partitioning=false) h
            WHERE TRY_CAST(report_period AS DATE)=CAST(? AS DATE)
              AND announce_date IS NOT NULL AND announce_date <= CAST(? AS DATE)
          ) WHERE rn=1
        """
        if relation_map:
            latest_h = f"""
              WITH raw_h AS ({raw_h}), mapped_h AS (
                SELECT h.*, COALESCE(rm.master_code || '.OF',h.fund_symbol) AS effective_fund_symbol,
                  ROW_NUMBER() OVER (
                    PARTITION BY COALESCE(rm.master_code || '.OF',h.fund_symbol), h.holding_symbol, h.report_period
                    ORDER BY CASE WHEN rm.master_code IS NOT NULL THEN 0 ELSE 1 END,
                             h.announce_date DESC NULLS LAST, h.fetched_at DESC NULLS LAST
                  ) AS merge_rn
                FROM raw_h h LEFT JOIN verified_relations rm
                  ON rm.share_code=regexp_replace(h.fund_symbol,'\\..*$','')
              ) SELECT * EXCLUDE(merge_rn) FROM mapped_h WHERE merge_rn=1
            """
        else:
            latest_h = f"SELECT h.*, h.fund_symbol AS effective_fund_symbol FROM ({raw_h}) h"
        # Consume the shared product view for the actual portfolio rows. It
        # excludes conflicts instead of choosing whichever share happens first.
        from crowding.product_view import load_product_view
        product_view = load_product_view(as_of=bound, report_period=period,
            lake_root=root, classification=classification,
            relation_mapping=relation_diag, conn=c)
        c.execute('CREATE TEMP TABLE canonical_product_holdings(fund_symbol VARCHAR,holding_symbol VARCHAR,report_period DATE,announce_date DATE,shares DOUBLE,market_value DOUBLE)')
        canonical = [(r['product_symbol'], r['holding_symbol'], period, r.get('announce_date'), r.get('shares'), r.get('market_value'))
                     for r in product_view['all_rows']]
        if canonical:
            c.execute('INSERT INTO canonical_product_holdings SELECT UNNEST(?),UNNEST(?),UNNEST(?),UNNEST(?),UNNEST(?),UNNEST(?)', [list(column) for column in zip(*canonical)])
        latest_h = "SELECT h.*,h.fund_symbol effective_fund_symbol FROM canonical_product_holdings h WHERE report_period=CAST(? AS DATE) AND announce_date<=CAST(? AS DATE)"
        # Use the same full-universe, report-value pool as the main board. The
        # previous local SQL used current shares×price and could disagree with
        # the board (and, if applied after LIMIT, silently select a different
        # cohort). LakeSnapshot performs PIT version selection and verified
        # share deduplication before the top-200/top-150 ranking.
        from crowding.metrics import build_crowding_pool
        if board_snapshot is None:
            from crowding.lake import LakeSnapshot, LakeSnapshotConfig
            with LakeSnapshot(LakeSnapshotConfig(lake_root=root, as_of=bound,
                                                 holding_period=period)) as stock_view:
                board_snapshot = stock_view.snapshot()
        pool_rows = build_crowding_pool(board_snapshot.get('rows', []),
                                        value_key='reported_mv')
        pool = [row['symbol'] for row in pool_rows]
        market_day = board_snapshot.get('metadata', {}).get('market_date')
        market_date = date.fromisoformat(market_day) if market_day else None
        if not pool:
            return {"as_of": bound.isoformat(), "holding_period": period.isoformat(),
                    "funds": [], "nav_dist": {"bins": []}, "etfs": {"rows": []},
                    "warnings": ["选定持仓期没有形成抱团池"]}
        c.execute("CREATE TEMP TABLE pool(symbol VARCHAR PRIMARY KEY)")
        c.executemany("INSERT INTO pool VALUES (?)", [(s,) for s in pool])

        # Latest profile per symbol and explicit active-universe filter.
        profiles = f"""
          SELECT * EXCLUDE(rn) FROM (
            SELECT p.*, ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY fetched_at DESC NULLS LAST) rn
            FROM read_parquet('{paths['fund_profiles']}', hive_partitioning=false) p
          ) WHERE rn=1
        """
        nav_cte = ""
        nav_join = ""
        nav_cols = "NULL::DOUBLE current_adj, NULL::DOUBLE prior_adj, NULL::DATE current_nav_date, NULL::DATE prior_nav_date, NULL::DOUBLE peak_adj"
        if paths["fund_nav"]:
            nav_cte = f""",
            n AS (
              SELECT symbol,
                MAX_BY(adjusted_nav, nav_date) FILTER (WHERE nav_date<=CAST(? AS DATE)) current_adj,
                MAX_BY(adjusted_nav, nav_date) FILTER (WHERE nav_date<=CAST(? AS DATE)) prior_adj,
                MAX(nav_date) FILTER (WHERE nav_date<=CAST(? AS DATE) AND adjusted_nav IS NOT NULL) current_nav_date,
                MAX(nav_date) FILTER (WHERE nav_date<=CAST(? AS DATE) AND adjusted_nav IS NOT NULL) prior_nav_date,
                MAX(adjusted_nav) FILTER (WHERE adjusted_nav>0) peak_adj
              FROM read_parquet('{paths['fund_nav']}', hive_partitioning=false)
              WHERE nav_date<=CAST(? AS DATE)
                AND announce_date IS NOT NULL AND announce_date<=CAST(? AS DATE)
                AND symbol IN (SELECT symbol FROM active_fund_symbols)
              GROUP BY symbol
            )"""
            nav_join = "LEFT JOIN n ON n.symbol=a.fund_symbol"
            nav_cols = "n.current_adj, n.prior_adj, n.current_nav_date, n.prior_nav_date, n.peak_adj"
        scale_path = _glob(root, "fund_scale")
        scale_cte = ""
        scale_join = ""
        if scale_path:
            scale_cte = f""",
            s AS (
              SELECT symbol, scale_yi, shares_yi, buy_yi, sell_yi,
                     report_period AS scale_period
              FROM (
                SELECT fs.*, ROW_NUMBER() OVER (
                  PARTITION BY symbol ORDER BY report_period DESC, src DESC NULLS LAST
                ) AS rn
                FROM read_parquet('{scale_path}', hive_partitioning=false) fs
                WHERE report_period<=CAST(? AS DATE)
              ) WHERE rn=1
            )"""
            scale_join = "LEFT JOIN s ON s.symbol=a.fund_symbol"
        scale_cols = "NULL::DOUBLE scale_yi, NULL::DOUBLE shares_yi, NULL::DOUBLE buy_yi, NULL::DOUBLE sell_yi, NULL::DATE scale_period"
        if scale_path:
            scale_cols = "s.scale_yi, s.shares_yi, s.buy_yi, s.sell_yi, s.scale_period"
            scale_history = c.execute(
                """SELECT symbol,report_period,MAX(scale_yi),MAX(shares_yi),MAX(buy_yi),MAX(sell_yi)
                   FROM read_parquet(?,hive_partitioning=false,union_by_name=true)
                   WHERE report_period<=CAST(? AS DATE) AND scale_yi IS NOT NULL AND scale_yi>0
                   GROUP BY symbol,report_period""",
                [scale_path, bound],
            ).fetchall()
        else:
            scale_history = []
        scale_by_share: dict[tuple[str, date], dict[str, float | None]] = {}
        scale_periods: dict[str, set[date]] = {}
        for symbol, report_period, scale_yi, shares_yi, buy_yi, sell_yi in scale_history:
            scale_by_share[(symbol, report_period)] = {
                'scale_yi': _f(scale_yi), 'shares_yi': _f(shares_yi),
                'buy_yi': _f(buy_yi), 'sell_yi': _f(sell_yi),
            }
            scale_periods.setdefault(symbol, set()).add(report_period)
        product_shares: dict[str, set[str]] = {}
        for product, meta in product_view['products'].items():
            product_shares[product] = set(meta['share_symbols'])
        if market_date:
            fund_value_join = f"""
              LEFT JOIN (SELECT symbol, MAX(close) AS close_px
                FROM read_parquet('{paths['daily_bars']}', hive_partitioning=false)
                WHERE trade_date=CAST('{market_date}' AS DATE) GROUP BY symbol) b2
                ON b2.symbol=h.holding_symbol
            """
        else:
            fund_value_join = "LEFT JOIN (SELECT NULL::VARCHAR symbol, NULL::DOUBLE AS close_px) b2 ON false"
        fund_sql = f"""
          WITH h AS ({latest_h}), p AS ({profiles}), agg AS (
            SELECT h.effective_fund_symbol AS fund_symbol, MAX(p.name) AS fund_name, MAX(p.management) AS corp,
              MAX(p.fund_type) fund_type, MAX(p.invest_type) invest_type, MAX(af.category) fund_category,
              SUM(CASE WHEN b2.close_px>0 THEN h.shares*b2.close_px END) pool_mv,
              SUM(h.market_value) pool_reported_mv
            FROM h JOIN pool x ON x.symbol=h.holding_symbol
            LEFT JOIN p ON p.symbol=h.effective_fund_symbol
            JOIN active_fund_symbols af ON af.symbol=h.effective_fund_symbol
            {fund_value_join}
            WHERE TRUE
            GROUP BY h.effective_fund_symbol
          ){nav_cte}{scale_cte}
          SELECT a.*, {nav_cols}, {scale_cols}
          FROM agg a {nav_join} {scale_join}
        """
        # The profile join is repeated to keep the SQL easy to audit; the
        # second join is harmless because p is one row/symbol.
        params = [period, bound]
        if paths["fund_nav"]:
            # latest NAV at ``bound`` versus the latest NAV available at the
            # holding-period end; this avoids reporting a zero return merely
            # because both placeholders used the same cutoff.
            baseline = return_start or period
            nav_end = market_date or bound
            params = [period, bound, nav_end, baseline, nav_end, baseline, nav_end, bound]
        if scale_path:
            params.append(period)
        raw = c.execute(fund_sql, params).fetchall()
        cols = [x[0] for x in c.description]
        fund_rows = []
        for row in _json_rows(raw, cols):
            current, prior = row.pop("current_adj"), row.pop("prior_adj")
            peak = row.pop("peak_adj")
            current_nav_date, prior_nav_date = row.pop("current_nav_date"), row.pop("prior_nav_date")
            scale_yi, shares_yi, buy_yi, sell_yi, scale_period = row.pop("scale_yi"), row.pop("shares_yi"), row.pop("buy_yi"), row.pop("sell_yi"), row.pop("scale_period")
            pool_mv = _f(row.pop("pool_mv"))
            pool_reported_mv = _f(row.pop("pool_reported_mv"))
            # A verified master holds the combined portfolio. Its individual
            # share-class scale and subscription figures are not product totals.
            # Keep them missing until all same-period class scales are verified.
            product_meta = product_view['products'].get(row.get('fund_symbol'), {})
            product_is_merged = product_meta.get('relation_status') == 'exact'
            if product_is_merged:
                scale_yi = shares_yi = buy_yi = sell_yi = None
                scale_period = None
            category = row.pop("fund_category", None)
            ret = (current / prior - 1) if current is not None and prior not in (None, 0) and current_nav_date and prior_nav_date else None
            # Current-value pool holdings and historical net assets are not
            # comparable without a same-date denominator and share handling.
            fund_rows.append({
                "display_code": row.pop("fund_symbol"), "name": row.pop("fund_name"),
                "corp": row.pop("corp"),
                "kind": {'active_equity': '主动', 'active_index_enhanced': '指数增强'}.get(category, '未知'),
                "pool_mv": pool_mv,
                "pool_reported_mv": pool_reported_mv,
                "pool_value_date": period.isoformat(),
                "pool_latest_value_date": market_date.isoformat() if market_date else None,
                "net_asset": scale_yi * 1e8 if scale_yi is not None else None,
                "pool_of_nav_pct": None,
                "ret_pct": ret * 100 if ret is not None else None,
                "since_hp_pct": ret * 100 if ret is not None else None,
                "dd_pct": (current / peak - 1) * 100 if current and peak and peak > 0 else None,
                # buy_yi/sell_yi are shares (亿份), not CNY.  Without a
                # verified beginning-of-period shares denominator we do not
                # manufacture a percentage; raw fields remain available.
                "redeem_pct": None,
                "purchase_pct": None,
                "shares_yi": shares_yi,
                "buy_shares_yi": buy_yi,
                "sell_shares_yi": sell_yi,
                "individual_pct": None,
                "scale_period": scale_period.isoformat() if scale_period else None,
                "nav_start_date": prior_nav_date.isoformat() if prior_nav_date else None,
                "nav_end_date": current_nav_date.isoformat() if current_nav_date else None,
                "merge_status": "verified_product" if product_is_merged else "approximate_product" if product_meta.get('relation_status')=='approx' else "share_or_unmapped",
                "relation_status": product_meta.get('relation_status', 'unknown'),
            })
        fund_rows.sort(key=lambda r: (r.get('merge_status') != 'verified_product',
                                      -(r["pool_mv"] if r["pool_mv"] is not None else -1)))
        fund_rows = fund_rows[: max(1, int(limit))]

        # Product-level scale and subscription/redemption are derived from all
        # share codes that have an exact, holdings-consistent relationship.
        # This is intentionally non-PIT and returned with its source period.
        for row in fund_rows:
            product = row.get('display_code')
            shares = product_shares.get(product, {product} if product else set())
            common_periods = None
            for share in shares:
                periods = scale_periods.get(share, set())
                common_periods = set(periods) if common_periods is None else common_periods & periods
            scale_period = period if common_periods and period in common_periods else None
            records = [scale_by_share.get((share, scale_period)) for share in shares] if scale_period else []
            valid = bool(records) and all(item is not None for item in records)
            scale_yi = sum(item['scale_yi'] for item in records) if valid and all(item['scale_yi'] is not None for item in records) else None
            shares_yi = sum(item['shares_yi'] for item in records) if valid and all(item['shares_yi'] is not None for item in records) else None
            buy_yi = sum(item['buy_yi'] for item in records) if valid and all(item['buy_yi'] is not None for item in records) else None
            sell_yi = sum(item['sell_yi'] for item in records) if valid and all(item['sell_yi'] is not None for item in records) else None
            shared_product = product_view['products'].get(product, {})
            shared_scale = shared_product.get('scale_yi')
            row['net_asset'] = shared_scale * 1e8 if shared_scale is not None else None
            row['shares_yi'] = shares_yi
            row['buy_shares_yi'] = buy_yi
            row['sell_shares_yi'] = sell_yi
            row['scale_period'] = scale_period.isoformat() if scale_period else None
            row['pool_of_nav_pct'] = (row['pool_reported_mv'] / row['net_asset'] * 100
                                      if row['pool_reported_mv'] is not None and row['net_asset'] else None)
            row['pool_latest_of_report_nav_estimate_pct'] = (row['pool_mv'] / row['net_asset'] * 100
                                      if row['pool_mv'] is not None and row['net_asset'] else None)
            row['pool_of_nav_quality'] = 'exact' if row['pool_of_nav_pct'] is not None else 'unavailable'
            row['pool_of_nav_reason'] = None if row['pool_of_nav_pct'] is not None else 'same_period_product_net_asset_missing'
            beginning = (shares_yi - buy_yi + sell_yi
                         if None not in (shares_yi, buy_yi, sell_yi) else None)
            row['purchase_pct'] = buy_yi / beginning * 100 if buy_yi is not None and beginning and beginning > 0 else None
            row['redeem_pct'] = sell_yi / beginning * 100 if sell_yi is not None and beginning and beginning > 0 else None

        holder_path = _glob(root, 'fund_holder_structure_snapshot')
        holder_by_share = {}
        if holder_path:
            holder_rows = c.execute(
                """SELECT symbol,report_period,individual_pct,total_shares_yi
                   FROM read_parquet(?,hive_partitioning=false,union_by_name=true)
                   WHERE report_period<=CAST(? AS DATE)
                   QUALIFY ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY report_period DESC,fetched_at DESC NULLS LAST)=1""",
                [holder_path, bound],
            ).fetchall()
            holder_by_share = {symbol: (period, pct, shares)
                               for symbol, period, pct, shares in holder_rows}
        for row in fund_rows:
            product = row.get('display_code')
            shares = product_shares.get(product, {product} if product else set())
            records = [holder_by_share.get(share) for share in shares]
            records = [item for item in records if item is not None and item[1] is not None]
            weighted = [(item[1], item[2]) for item in records if item[2] is not None and item[2] > 0]
            total_weight = sum(weight for _, weight in weighted)
            same_period_complete = (len(records) == len(shares) and len(weighted) == len(shares)
                                    and len({item[0] for item in records}) == 1)
            row['individual_pct'] = (sum(value * weight for value, weight in weighted) / total_weight
                                     if same_period_complete and total_weight > 0 else None)
            row['individual_pct_reason'] = None if row['individual_pct'] is not None else 'same_period_all_share_weights_missing'
            periods = [item[0] for item in records]
            row['holder_period'] = periods[0].isoformat() if same_period_complete else None
            row['holder_coverage'] = {'available_shares': len(records), 'product_shares': len(shares)}

        # The selected window and the since-holding-period column are distinct
        # when the user switches to 5/20-day or quarter-end windows.
        if paths['fund_nav'] and fund_rows:
            codes = [row['display_code'] for row in fund_rows]
            nav_rows = c.execute(
                """WITH n AS (
                     SELECT symbol,nav_date,MAX(adjusted_nav) adjusted_nav
                     FROM read_parquet(?,hive_partitioning=false,union_by_name=true)
                     WHERE symbol IN (SELECT UNNEST(?::VARCHAR[]))
                       AND nav_date<=CAST(? AS DATE) AND announce_date IS NOT NULL AND announce_date<=CAST(? AS DATE)
                     GROUP BY symbol,nav_date
                   ) SELECT symbol,
                     MAX_BY(adjusted_nav,nav_date) FILTER(WHERE nav_date<=CAST(? AS DATE)) current_adj,
                     MAX_BY(adjusted_nav,nav_date) FILTER(WHERE nav_date<=CAST(? AS DATE)) holding_adj,
                     MAX(adjusted_nav) peak_adj
                   FROM n WHERE adjusted_nav IS NOT NULL AND adjusted_nav>0 GROUP BY symbol""",
                [paths['fund_nav'], codes, market_date or bound, bound, market_date or bound, period],
            ).fetchall()
            nav = {symbol: (current, hold, peak) for symbol, current, hold, peak in nav_rows}
            for row in fund_rows:
                current, hold, peak = nav.get(row['display_code'], (None, None, None))
                row['since_hp_pct'] = (current / hold - 1) * 100 if current is not None and hold not in (None, 0) else None
                row['dd_pct'] = (current / peak - 1) * 100 if current and peak and peak > 0 else row.get('dd_pct')

        returns = [r["ret_pct"] for r in fund_rows if r["ret_pct"] is not None]
        bins = _return_histogram(returns)

        # ETF rows are derived from ETF-profile symbols present in holdings;
        # share change is only emitted when two published ETF observations
        # exist, otherwise it remains missing.
        etf_rows: list[dict[str, Any]] = []
        if paths["etf_profiles"] and paths["etf_daily_metrics"]:
            metric_columns = {r[0] for r in c.execute(f"DESCRIBE SELECT * FROM read_parquet('{paths['etf_daily_metrics']}',hive_partitioning=false,union_by_name=true)").fetchall()}
            etf_size_sql = 'MAX(fund_size) FILTER (WHERE trade_date=CAST(? AS DATE) AND fund_size>0)' if 'fund_size' in metric_columns else 'NULL::DOUBLE'
            etf_sql = f"""
              WITH h AS ({latest_h}), e AS (
                SELECT symbol, MAX(name) AS etf_name FROM read_parquet('{paths['etf_profiles']}', hive_partitioning=false) GROUP BY symbol
              ), agg AS (
                SELECT h.fund_symbol, MAX(e.etf_name) AS etf_name,
                       SUM(CASE WHEN b2.close_px>0 THEN h.shares*b2.close_px END) pool_mv
                FROM h JOIN pool x ON x.symbol=h.holding_symbol JOIN e ON e.symbol=h.fund_symbol
                {fund_value_join}
                GROUP BY h.fund_symbol
              ), m AS (
                SELECT symbol,
                  MAX_BY(shares_outstanding, trade_date) FILTER (WHERE trade_date<=CAST(? AS DATE)) AS shares_out,
                  MAX(trade_date) FILTER (WHERE trade_date<=CAST(? AS DATE) AND shares_outstanding IS NOT NULL) metric_date,
                  MAX(trade_date) FILTER (WHERE trade_date<=CAST(? AS DATE) AND shares_outstanding IS NOT NULL) base_date,
                  MAX_BY(shares_outstanding, trade_date) FILTER (WHERE trade_date<=CAST(? AS DATE)) AS base_share,
                  {etf_size_sql} AS net_asset
                FROM read_parquet('{paths['etf_daily_metrics']}', hive_partitioning=false)
                GROUP BY symbol
              ) SELECT a.fund_symbol, a.etf_name, a.pool_mv, m.shares_out, m.metric_date,
                       m.base_date, m.base_share, m.net_asset FROM agg a LEFT JOIN m ON m.symbol=a.fund_symbol
              ORDER BY a.pool_mv DESC NULLS LAST LIMIT 30
            """
            etf_end = market_date or bound
            etf_params = [period, bound, etf_end, etf_end, period, period]
            if 'fund_size' in metric_columns:
                etf_params.append(etf_end)
            for code, name, mv, share, metric_date, base_date, base_share, net_asset in c.execute(etf_sql, etf_params).fetchall():
                change = ((share / base_share) - 1) * 100 if share is not None and base_share not in (None, 0) else None
                etf_rows.append({"fund_code": code, "name": name or code, "pool_mv": _f(mv),
                                 "share": _f(share), "chg_pct": change,
                                 "net_asset": _f(net_asset),
                                 "nav_date": etf_end.isoformat() if net_asset else None,
                                 "pool_of_nav_pct": float(mv) / float(net_asset) * 100 if mv is not None and net_asset and net_asset > 0 else None,
                                 "pool_of_nav_quality": "estimated" if mv is not None and net_asset and net_asset > 0 else "unavailable",
                                 "pool_of_nav_reason": None if mv is not None and net_asset and net_asset > 0 else "same_day_etf_fund_size_missing",
                                 "pool_value_date": etf_end.isoformat(),
                                 "pool_of_nav_basis": "报告期股数×行情日价格 / 同行情日ETF净资产；未校正期后拆送股及申赎持仓变化",
                                 "spark": [], "metric_date": metric_date.isoformat() if metric_date else None,
                                 "base_date": base_date.isoformat() if base_date else None,
                                 "base_share": _f(base_share)})
            if etf_rows:
                etf_codes = [row['fund_code'] for row in etf_rows]
                spark_rows = c.execute(
                    """WITH ranked AS (
                         SELECT symbol,trade_date,MAX(shares_outstanding) shares_outstanding
                         FROM read_parquet(?,hive_partitioning=false,union_by_name=true)
                         WHERE symbol IN (SELECT UNNEST(?::VARCHAR[])) AND trade_date<=CAST(? AS DATE)
                         GROUP BY symbol,trade_date
                       ), latest AS (
                         SELECT symbol,trade_date,shares_outstanding,
                           ROW_NUMBER() OVER(PARTITION BY symbol ORDER BY trade_date DESC) seq
                         FROM ranked WHERE shares_outstanding IS NOT NULL
                       ) SELECT symbol,list(shares_outstanding ORDER BY trade_date) FILTER(WHERE seq<=21)
                         FROM latest GROUP BY symbol""",
                    [paths['etf_daily_metrics'], etf_codes, etf_end],
                ).fetchall()
                sparks = {symbol: values for symbol, values in spark_rows}
                for row in etf_rows:
                    row['spark'] = [_f(value) for value in sparks.get(row['fund_code'], [])]
        warnings = [
            "抱团池与主看板使用同一全市场结果：报告期披露持仓市值前200与占流通比例前150的并集，并统一要求披露持仓市值不低于10亿元；未处理持仓期后的拆送股。",
            (f"主动基金筛选使用 {classification.get('rules_version', 'unknown')}：纳入 {len(active_symbols)} 个明确主动/指数增强份额；"
             f"unknown {classification.get('unknown_count', 0)} 个，ETF/联接/FOF/QDII 等排除类别按证据处理。"),
            "仅同报告期完整证据及持仓一致的份额已归并，其余基金金额仍可能包含重复披露；",
            "净资产、申购和赎回来自非PIT季度快照 derived/fund_scale；已验证产品要求各份额同一报告期均有规模后才求和。标准抱团占净值使用报告期披露持仓市值除同季净资产；最新价估算另字段保留日期差异；shares_yi/buy_yi/sell_yi 为亿份，申购赎回比例按推算期初份额计算。",
        ]
        return {"as_of": bound.isoformat(), "holding_period": period.isoformat(),
                "market_date": market_date.isoformat() if market_date else None,
                "funds": fund_rows, "nav_dist": {"as_of": bound.isoformat(), "requested_start": (return_start or period).isoformat(), "bins": bins},
                "etfs": {"as_of": bound.isoformat(), "base_day": period.isoformat(), "rows": etf_rows},
                "warnings": warnings, "coverage": {"pool_stocks": len(pool), "fund_rows": len(fund_rows),
                "nav_return_rows": len(returns), "etf_rows": len(etf_rows),
                "fund_nav_ratio_rows": sum(r["pool_of_nav_pct"] is not None for r in fund_rows),
                "etf_nav_ratio_rows": sum(r["pool_of_nav_pct"] is not None for r in etf_rows),
                "classification": classification.get('counts', {}),
                "classification_unknown": classification.get('unknown_count', 0),
                "classification_scope": classification.get('scope'),
                "product_view": product_view["coverage"],
                "relation_status": relation_diag.get('relation_status'),
                "relation_verified_products": len(relation_diag.get('verified_products', [])),
                "relation_candidate_products": len(relation_diag.get('candidate_products', [])),
                "relation_unmapped_observed_codes": len(relation_diag.get('unmapped_observed_codes', [])),
                "relation_conflicts": len(relation_diag.get('conflicts', []))}}
    finally:
        c.close()


def _return_histogram(returns):
    bins = []
    if returns:
        lo, hi = min(returns), max(returns)
        step = (hi - lo) / 10 if hi > lo else 1.0
        for i in range(10):
            a, b = lo + i * step, lo + (i + 1) * step
            bins.append({'lo':a,'hi':b,'n':sum(a <= x <= b if i == 9 else a <= x < b for x in returns)})
    return bins


def refresh_return_window(base_panel, return_start, *, lake_root=DEFAULT_LAKE_ROOT,
                          as_of=None, market_date=None):
    """Reuse an HP panel; only refresh its selected funds' NAV return window.

    Holdings, ETF, scale, since-HP returns, drawdowns and their dates remain
    unchanged. Date-partitioned NAV is read backwards in small batches until
    the selected codes are resolved. A known HP observation bounds the search,
    so a missing/new fund cannot force a whole-history scan for later windows.
    The original panel is never mutated. No market/holdings datasets are read.
    """
    def day(value):
        return date.fromisoformat(str(value)) if value is not None else None
    result = deepcopy(base_panel)
    baseline = day(return_start)
    if baseline is None:
        raise ValueError('return_start is required')
    panel_market = day(base_panel.get('market_date'))
    end = day(market_date) or panel_market or day(base_panel.get('as_of'))
    panel_as_of = day(base_panel.get('as_of'))
    bound = panel_as_of or day(as_of) or end
    if panel_as_of and as_of is not None and day(as_of) != panel_as_of:
        raise ValueError('as_of must match the reused base panel')
    if panel_market and end != panel_market:
        raise ValueError('market_date must match the reused base panel')
    if end is None or bound is None:
        raise ValueError('base panel market_date/as_of is unavailable')
    if baseline > end:
        raise ValueError('return_start is after market_date')
    fund_container = result.get('funds', [])
    funds = fund_container.get('rows', []) if isinstance(fund_container, dict) else fund_container
    codes = sorted({r['display_code'] for r in funds if r.get('display_code')})
    nav_codes = {r['display_code'] for r in funds if r.get('display_code')
                 and ('nav_end_date' not in r or r.get('nav_end_date') is not None)}
    hp = day(base_panel.get('holding_period'))
    # HP NAV dates are observed lower bounds, including a fund's last NAV
    # before a weekend/holiday holding-period end. They do not assume zero NAV.
    old_dates = [day(r['nav_start_date']) for r in funds if r.get('nav_start_date')]
    floor = min(old_dates + ([hp] if hp else [])) if hp and baseline >= hp else None
    root = Path(lake_root).expanduser().resolve()/'curated/fund_nav'
    partitions = []
    flat_files = sorted(root.glob('*.parquet')) if root.exists() else []
    if root.exists():
        for directory in root.iterdir():
            if not directory.is_dir() or not directory.name.startswith('nav_date='):
                continue
            try:
                partition_date = date.fromisoformat(directory.name.split('=',1)[1])
            except ValueError:
                continue
            if partition_date <= end and (floor is None or partition_date >= floor):
                files = sorted(directory.rglob('*.parquet'))
                if files:
                    partitions.append((partition_date, files))
    partitions.sort(reverse=True)
    scanned_files = set()
    current, previous = {}, {}
    if nav_codes and (partitions or flat_files):
        conn = bounded_connect()
        try:
            first = flat_files[0] if flat_files else partitions[0][1][0]
            columns = {r[0] for r in conn.execute('DESCRIBE SELECT * FROM read_parquet(?,hive_partitioning=false)',[str(first)]).fetchall()}
            version_order = 'announce_date DESC' + (',fetched_at DESC NULLS LAST' if 'fetched_at' in columns else '')
            def fetch_at(cutoff):
                pending = set(nav_codes)
                found = {}
                batches = [(None,flat_files)] if flat_files else []
                dated = [(d,files) for d,files in partitions if d <= cutoff]
                batches += [(dated[i][0],[p for _,files in dated[i:i+8] for p in files]) for i in range(0,len(dated),8)]
                for batch_index, (_,files) in enumerate(batches):
                    if not pending:
                        break
                    paths = [str(p) for p in files]
                    scanned_files.update(paths)
                    rows = conn.execute(f"""WITH selected AS (
                        SELECT symbol,nav_date,adjusted_nav FROM read_parquet(?,hive_partitioning=false,union_by_name=true)
                        WHERE symbol IN (SELECT UNNEST(?::VARCHAR[])) AND nav_date<=?
                          AND announce_date IS NOT NULL AND announce_date<=?
                          AND adjusted_nav>0 AND isfinite(adjusted_nav)
                        QUALIFY ROW_NUMBER() OVER(PARTITION BY symbol,nav_date ORDER BY {version_order})=1
                      ) SELECT symbol,MAX_BY(adjusted_nav,nav_date),MAX(nav_date) FROM selected GROUP BY symbol""",
                      [paths,sorted(pending),cutoff,bound]).fetchall()
                    for code,value,nav_date in rows:
                        if code not in found or nav_date > found[code][1]:
                            found[code] = (float(value),nav_date)
                    # Flat legacy files and date partitions can coexist;
                    # resolve only after every newer partition was checked.
                    next_latest = batches[batch_index+1][0] if batch_index+1<len(batches) else None
                    pending = {c for c in pending if c not in found or (next_latest is not None and next_latest>found[c][1])}
                return found
            current, previous = fetch_at(end), fetch_at(baseline)
        finally:
            conn.close()
    for row in funds:
        code = row.get('display_code')
        now, old = current.get(code), previous.get(code)
        value = (now[0]/old[0]-1)*100 if now and old else None
        row['ret_pct'] = value if value is not None and isfinite(value) else None
        row['nav_start_date'] = old[1].isoformat() if old else None
        row['nav_end_date'] = now[1].isoformat() if now else None
    returns = [r['ret_pct'] for r in funds if r.get('ret_pct') is not None]
    result['nav_dist'] = {**result.get('nav_dist',{}),'as_of':bound.isoformat(),
                          'requested_start':baseline.isoformat(),'bins':_return_histogram(returns)}
    result.setdefault('coverage',{})['nav_return_rows'] = len(returns)
    result['return_window_refresh'] = {'source':'selected fund_nav only','fund_codes':len(codes),
        'scanned_nav_files':len(scanned_files),'market_date':end.isoformat(),'as_of':bound.isoformat(),
        'requested_start':baseline.isoformat(),'nav_search_floor':floor.isoformat() if floor else None}
    return result
