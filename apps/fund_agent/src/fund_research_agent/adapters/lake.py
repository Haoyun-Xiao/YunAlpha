from __future__ import annotations

import csv
import math
import re
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from fund_research_agent.adapters.fund_universe import FundUniverseAdapter
from fund_research_agent.domain.contracts import FundIdentity
from fund_research_agent.domain.errors import DataUnavailable, PolicyDenied
from fund_research_agent.domain.fund_query import (
    extract_fund_query,
    is_subsequence,
    normalize_fund_text,
)

SELECT_PATTERN = re.compile(r"^\s*(?:WITH\b[\s\S]+?\bSELECT\b|SELECT\b)", re.IGNORECASE)
FORBIDDEN_SQL = re.compile(
    r"\b(INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|COPY|EXPORT|IMPORT|ATTACH|DETACH|VACUUM|CALL|PRAGMA)\b",
    re.IGNORECASE,
)


class ReadOnlyLakeAdapter:
    """Read the legacy lake without refreshing or mutating its DuckDB view cache."""

    def __init__(
        self,
        legacy_database: Path,
        fund_manifest: Path | None = None,
        fund_universe: FundUniverseAdapter | None = None,
    ) -> None:
        self.database = legacy_database.resolve()
        if not self.database.exists():
            raise FileNotFoundError(self.database)
        self.manifest: dict[str, dict[str, str]] = {}
        self.fund_universe = fund_universe
        self._profile_name_cache: list[tuple[str, str]] | None = None
        if fund_manifest and fund_manifest.exists():
            with fund_manifest.open(encoding="utf-8-sig", newline="") as stream:
                self.manifest = {
                    row["fund_symbol"].strip().upper(): row for row in csv.DictReader(stream)
                }

    def _connect(self) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(str(self.database), read_only=True)

    def describe(self) -> dict[str, list[dict[str, str]]]:
        allowed = (
            "fund_profiles",
            "fund_manager_tenures",
            "fund_nav",
            "fund_holdings",
            "fund_reports",
            "fund_scale",
            "fund_annual_turnover",
        )
        connection = self._connect()
        try:
            return {
                table: [
                    {"name": row[0], "type": row[1]}
                    for row in connection.execute(f'DESCRIBE "{table}"').fetchall()
                ]
                for table in allowed
            }
        finally:
            connection.close()

    def resolve_fund(self, query: str, as_of: date | None = None) -> list[FundIdentity]:
        if self.fund_universe is not None:
            universe_matches = self.fund_universe.resolve(query, as_of)
            if universe_matches:
                connection = self._connect()
                try:
                    for identity in universe_matches:
                        row = connection.execute(
                            "SELECT name,management FROM fund_profiles WHERE symbol=? LIMIT 1",
                            [identity.fund_symbol],
                        ).fetchone()
                        if row:
                            identity.company_full_name = str(
                                identity.company_full_name or row[1] or ""
                            )
                        match = re.search(
                            r"(?:[-—\s]?)([A-ZIC])$", identity.fund_name, re.IGNORECASE
                        )
                        if match:
                            identity.share_class = match.group(1).upper()
                finally:
                    connection.close()
                return universe_matches
        text = extract_fund_query(query)
        if not text:
            return []
        symbol_match = re.search(r"(?<!\d)(\d{6}(?:\.(?:OF|SH|SZ))?)(?!\w)", text, re.IGNORECASE)
        if symbol_match:
            text = symbol_match.group(1).upper()
            if "." not in text:
                text += ".OF"
        exact_symbol = bool(re.fullmatch(r"\d{6}\.(?:OF|SH|SZ)", text, re.IGNORECASE))
        where = "p.symbol = ?" if exact_symbol else "p.name LIKE ?"
        parameter = text.upper() if exact_symbol else f"%{text}%"
        cutoff = as_of or date.today()
        sql = f"""
            WITH ranked_tenures AS (
              SELECT fund_symbol, manager_name, begin_date, end_date,
                     row_number() OVER (
                       PARTITION BY fund_symbol
                       ORDER BY CASE WHEN end_date IS NULL OR end_date >= ? THEN 0 ELSE 1 END,
                                COALESCE(end_date, DATE '9999-12-31') DESC,
                                begin_date DESC
                     ) AS rn
              FROM fund_manager_tenures
              WHERE begin_date <= ?
            )
            SELECT p.symbol, p.name, p.management, t.manager_name, t.begin_date, t.end_date
            FROM fund_profiles p
            LEFT JOIN ranked_tenures t ON t.fund_symbol=p.symbol AND t.rn=1
            WHERE {where}
            ORDER BY CASE WHEN p.name=? THEN 0 ELSE 1 END, p.symbol
            LIMIT 20
        """
        connection = self._connect()
        try:
            rows = connection.execute(sql, [cutoff, cutoff, parameter, text]).fetchall()
            if not rows and not exact_symbol:
                normalized_query = normalize_fund_text(text)
                requested_share = (
                    normalized_query[-1].upper()
                    if normalized_query and normalized_query[-1] in "acei"
                    else None
                )
                base_query = normalized_query[:-1] if requested_share else normalized_query
                if len(base_query) >= 3:
                    if self._profile_name_cache is None:
                        self._profile_name_cache = [
                            (str(symbol), str(name or ""))
                            for symbol, name in connection.execute(
                                "SELECT symbol,name FROM fund_profiles"
                            ).fetchall()
                        ]
                    scored: list[tuple[int, str]] = []
                    for symbol, name in self._profile_name_cache:
                        normalized_name = normalize_fund_text(name)
                        name_without_share = (
                            normalized_name[:-1]
                            if normalized_name and normalized_name[-1] in "acei"
                            else normalized_name
                        )
                        if not is_subsequence(base_query, name_without_share):
                            continue
                        name_share = (
                            normalized_name[-1].upper()
                            if normalized_name and normalized_name[-1] in "acei"
                            else None
                        )
                        share_bonus = 100 if requested_share and requested_share == name_share else 0
                        scored.append((share_bonus - len(name_without_share), symbol))
                    symbols = [symbol for _score, symbol in sorted(scored, reverse=True)[:20]]
                    if symbols:
                        placeholders = ",".join("?" for _ in symbols)
                        fuzzy_sql = sql.replace(
                            f"WHERE {where}", f"WHERE p.symbol IN ({placeholders})"
                        )
                        rows = connection.execute(
                            fuzzy_sql,
                            [cutoff, cutoff, *symbols, text],
                        ).fetchall()
        finally:
            connection.close()
        result = []
        for symbol, name, management, manager, begin_date, end_date in rows:
            manifest = self.manifest.get(str(symbol).upper(), {})
            used_manifest = False
            if not manager and manifest:
                manager = manifest.get("manager_name") or manager
                begin_date = manifest.get("tenure_start") or begin_date
                end_date = manifest.get("tenure_end") or end_date
                used_manifest = True
            share_class = None
            match = re.search(r"[-—\s]([A-ZIC])$", str(name), re.IGNORECASE)
            if match:
                share_class = match.group(1).upper()
            warnings = []
            if not manager:
                warnings.append("未找到截止日对应的基金经理任期")
            elif used_manifest:
                warnings.append("基金经理任期来自只读历史基金清单")
            result.append(
                FundIdentity(
                    fund_symbol=str(symbol),
                    fund_name=str(name),
                    share_class=share_class,
                    manager_name=str(manager or ""),
                    manager_tenure_start=begin_date,
                    manager_tenure_end=end_date,
                    company_full_name=str(management or ""),
                    identity_confidence=1.0 if exact_symbol and manager else 0.85,
                    identity_warnings=warnings,
                )
            )
        return result

    def coverage(
        self,
        fund_symbol: str,
        manager_tenure_start: date | None = None,
        as_of: date | None = None,
    ) -> dict[str, Any]:
        queries = {
            "nav": ("fund_nav", "symbol", "nav_date"),
            "holdings": ("fund_holdings", "fund_symbol", "report_period"),
            "reports": ("fund_reports", "fund_symbol", "report_period"),
            "scale": ("fund_scale", "symbol", "report_period"),
            "turnover": ("fund_annual_turnover", "fund_symbol", "report_period"),
        }
        connection = self._connect()
        result: dict[str, Any] = {}
        try:
            exact_holdings = bool(
                connection.execute(
                    "SELECT count(*) FROM fund_holdings WHERE fund_symbol=?",
                    [fund_symbol],
                ).fetchone()[0]
            )
            holding_predicate = (
                "fund_symbol=?"
                if exact_holdings
                else "split_part(fund_symbol, '.', 1)=?"
            )
            holding_value = (
                fund_symbol if exact_holdings else fund_symbol.split(".", 1)[0]
            )
            for label, (table, symbol_field, date_field) in queries.items():
                try:
                    predicate = (
                        holding_predicate
                        if table == "fund_holdings"
                        else f"{symbol_field}=?"
                    )
                    symbol_value = (
                        holding_value
                        if table == "fund_holdings"
                        else fund_symbol
                    )
                    cutoff = as_of or date.today()
                    available = " AND (announce_date IS NULL OR announce_date<=?)" if table in {"fund_nav", "fund_holdings", "fund_reports"} else ""
                    row = connection.execute(
                        f"SELECT count(*), min({date_field}), max({date_field}) "
                        f"FROM {table} WHERE {predicate} AND {date_field}<=?" + available,
                        [symbol_value, cutoff] + ([cutoff] if available else []),
                    ).fetchone()
                    result[label] = {
                        "rows": int(row[0]),
                        "from": str(row[1]) if row[1] else None,
                        "to": str(row[2]) if row[2] else None,
                        "status": "ready" if row[0] else "missing",
                    }
                except duckdb.Error as exc:
                    result[label] = {"rows": 0, "status": "unavailable", "error": str(exc)}
            if manager_tenure_start:
                row = connection.execute(
                    "SELECT count(*),min(report_period),max(report_period) "
                    f"FROM fund_holdings WHERE {holding_predicate} "
                    "AND report_period>=? "
                    "AND report_period<=? AND (announce_date IS NULL OR announce_date<=?)",
                    [holding_value, manager_tenure_start, as_of or date.today(), as_of or date.today()],
                ).fetchone()
                result["holdings_in_manager_tenure"] = {
                    "rows": int(row[0]),
                    "from": str(row[1]) if row[1] else None,
                    "to": str(row[2]) if row[2] else None,
                    "status": "ready" if row[0] else "missing",
                }
                period_rows = connection.execute(
                    "SELECT report_period,count(*) FROM fund_holdings "
                    f"WHERE {holding_predicate} "
                    "AND report_period>=? AND report_period<=? AND (announce_date IS NULL OR announce_date<=?) "
                    "GROUP BY report_period ORDER BY report_period",
                    [holding_value, manager_tenure_start, as_of or date.today(), as_of or date.today()],
                ).fetchall()
                if period_rows:
                    actual = {pd.Timestamp(period) for period, _count in period_rows}
                    expected = set(
                        pd.date_range(min(actual), max(actual), freq="QE-DEC")
                    )
                    missing = sorted(expected - actual)
                    latest_period, latest_rows = period_rows[-1]
                    latest_month = latest_period.month
                    if latest_month in {3, 9}:
                        latest_scope = "top10_disclosure"
                    elif latest_rows <= 10:
                        latest_scope = "top10_or_incomplete_full_disclosure"
                    else:
                        latest_scope = "full_disclosure"
                    result["holdings_in_manager_tenure"].update(
                        {
                            "periods": len(period_rows),
                            "internal_missing_periods": [
                                str(period.date()) for period in missing
                            ],
                            "latest_period_rows": int(latest_rows),
                            "latest_disclosure_scope": latest_scope,
                        }
                    )
        finally:
            connection.close()
        blocking = []
        for label in ("nav", "holdings", "scale"):
            if result.get(label, {}).get("status") != "ready":
                blocking.append(label)
        if manager_tenure_start and result.get("holdings_in_manager_tenure", {}).get(
            "status"
        ) != "ready":
            blocking.append("holdings_in_manager_tenure")
        warnings = []
        if result.get("reports", {}).get("status") != "ready":
            warnings.append("quarterly_report_pending_supplement")
        tenure_coverage = result.get("holdings_in_manager_tenure", {})
        if tenure_coverage.get("internal_missing_periods"):
            warnings.append("holdings_internal_period_gaps")
        if tenure_coverage.get("latest_disclosure_scope") == "top10_or_incomplete_full_disclosure":
            warnings.append("latest_full_holdings_not_available")
        universe_record = (
            self.fund_universe.get(fund_symbol) if self.fund_universe is not None else None
        )
        if self.fund_universe is not None and universe_record is None:
            blocking.append("identity_not_in_report_universe")
        from fund_research_agent.legacy_port.fund_json.periods import last_completed_quarter
        expected = last_completed_quarter(as_of or date.today()).date().isoformat()
        for label in ("scale", "reports", "holdings"):
            item = result.get(label, {})
            item["expected_report_period"] = expected
            if item.get("to") and item["to"] < expected:
                item["freshness"] = "older_than_last_completed_quarter"
                warnings.append(f"{label}_latest_period_missing")
        if "quarterly_report_pending_supplement" in warnings:
            result["reports"]["fetch_state"] = "pending_supplement"
        result["eligibility"] = {
            "identity_source": "fund_universe_xlsx" if universe_record else "lake",
            "eligible_for_full_report": not blocking,
            "blocking_reasons": blocking,
            "warnings": warnings,
        }
        return result

    def metric(
        self,
        fund_symbol: str,
        metric: str,
        *,
        as_of: date | None = None,
        year: int | None = None,
    ) -> dict[str, Any]:
        cutoff = as_of or date.today()
        connection = self._connect()
        try:
            if metric == "annual_return":
                target_year = year or cutoff.year
                from fund_research_agent.domain.report_data import annual_return
                from fund_research_agent.legacy_port.fund_json.lake import LakeRepository
                repo = LakeRepository(self.database, as_of=cutoff)
                try:
                    result = annual_return(repo.nav(fund_symbol), target_year, cutoff)
                finally:
                    repo.close()
                return {"metric": metric, "fund_symbol": fund_symbol, **result, "unit": "ratio"}
            specs = {
                "latest_nav": (
                    "SELECT adjusted_nav,nav_date FROM fund_nav WHERE symbol=? AND nav_date<=? "
                    "AND adjusted_nav IS NOT NULL ORDER BY nav_date DESC LIMIT 1",
                    "nav",
                ),
                "latest_scale": (
                    "SELECT scale_yi,report_period FROM fund_scale WHERE symbol=? AND report_period<=? "
                    "ORDER BY report_period DESC LIMIT 1",
                    "亿元",
                ),
                "latest_turnover": (
                    "SELECT turnover_rate,report_period FROM fund_annual_turnover "
                    "WHERE fund_symbol=? AND report_period<=? AND turnover_rate IS NOT NULL "
                    "ORDER BY report_period DESC,fetched_at DESC LIMIT 1",
                    "ratio",
                ),
            }
            if metric not in specs:
                raise PolicyDenied(f"unsupported deterministic metric: {metric}")
            sql, unit = specs[metric]
            row = connection.execute(sql, [fund_symbol, cutoff]).fetchone()
            return {
                "metric": metric,
                "fund_symbol": fund_symbol,
                "value": float(row[0]) if row and row[0] is not None else None,
                "unit": unit,
                "data_as_of": str(row[1]) if row and row[1] else None,
            }
        finally:
            connection.close()

    def research_snapshot(
        self, fund_symbol: str, *, as_of: date | None = None
    ) -> dict[str, Any]:
        """Return the compact, deterministic dossier commonly needed by chat research."""
        cutoff = as_of or date.today()
        connection = self._connect()
        try:
            nav_rows = connection.execute(
                "SELECT nav_date,adjusted_nav FROM fund_nav WHERE symbol=? AND nav_date<=? "
                "AND adjusted_nav IS NOT NULL ORDER BY nav_date",
                [fund_symbol, cutoff],
            ).fetchall()
            annual_returns: list[dict[str, Any]] = []
            max_drawdown = None
            annualized_volatility = None
            if nav_rows:
                frame = pd.DataFrame(nav_rows, columns=["date", "nav"])
                frame["date"] = pd.to_datetime(frame["date"])
                frame["nav"] = pd.to_numeric(frame["nav"], errors="coerce")
                frame = frame.dropna().drop_duplicates("date", keep="last")
                frame["year"] = frame["date"].dt.year
                from fund_research_agent.domain.report_data import annual_return
                series = frame.set_index("date")["nav"]
                for year in sorted(frame["year"].unique()):
                    result = annual_return(series, int(year), cutoff)
                    if result["value"] is not None:
                        annual_returns.append({**result, "return": result["value"]})
                drawdown = frame["nav"] / frame["nav"].cummax() - 1
                max_drawdown = float(drawdown.min()) if not drawdown.empty else None
                returns = frame["nav"].pct_change().dropna()
                if len(returns) >= 20:
                    annualized_volatility = float(returns.std(ddof=1) * math.sqrt(252))
            scale = connection.execute(
                "SELECT scale_yi,report_period FROM fund_scale WHERE symbol=? AND report_period<=? "
                "ORDER BY report_period DESC LIMIT 1",
                [fund_symbol, cutoff],
            ).fetchone()
            turnover = connection.execute(
                "SELECT turnover_rate,report_period FROM fund_annual_turnover "
                "WHERE fund_symbol=? AND report_period<=? AND turnover_rate IS NOT NULL "
                "ORDER BY report_period DESC,fetched_at DESC LIMIT 1",
                [fund_symbol, cutoff],
            ).fetchone()
            exact_holdings = bool(
                connection.execute(
                    "SELECT count(*) FROM fund_holdings WHERE fund_symbol=?", [fund_symbol]
                ).fetchone()[0]
            )
            holding_symbol = fund_symbol if exact_holdings else fund_symbol.split(".", 1)[0]
            holding_predicate = "fund_symbol=?" if exact_holdings else "split_part(fund_symbol,'.',1)=?"
            latest_period = connection.execute(
                f"SELECT max(report_period) FROM fund_holdings WHERE {holding_predicate} AND report_period<=?",
                [holding_symbol, cutoff],
            ).fetchone()[0]
            holdings: list[dict[str, Any]] = []
            if latest_period:
                rows = connection.execute(
                    f"SELECT holding_symbol,holding_name,weight_nav,market_value,shares "
                    f"FROM fund_holdings WHERE {holding_predicate} AND report_period=? "
                    "ORDER BY weight_nav DESC NULLS LAST,market_value DESC NULLS LAST LIMIT 10",
                    [holding_symbol, latest_period],
                ).fetchall()
                holdings = [
                    {
                        "symbol": row[0],
                        "name": row[1],
                        "weight_nav": float(row[2]) if row[2] is not None else None,
                        "market_value": float(row[3]) if row[3] is not None else None,
                        "shares": float(row[4]) if row[4] is not None else None,
                    }
                    for row in rows
                ]
            return {
                "fund_symbol": fund_symbol,
                "as_of": cutoff.isoformat(),
                "nav": {
                    "from": str(nav_rows[0][0]) if nav_rows else None,
                    "to": str(nav_rows[-1][0]) if nav_rows else None,
                    "latest": float(nav_rows[-1][1]) if nav_rows else None,
                    "observations": len(nav_rows),
                    "annual_returns": annual_returns[-6:],
                    "max_drawdown_since_inception": max_drawdown,
                    "annualized_observation_volatility": annualized_volatility,
                },
                "scale": {
                    "value": float(scale[0]) if scale and scale[0] is not None else None,
                    "unit": "亿元",
                    "data_as_of": str(scale[1]) if scale else None,
                },
                "turnover": {
                    "value": float(turnover[0]) if turnover and turnover[0] is not None else None,
                    "unit": "ratio",
                    "data_as_of": str(turnover[1]) if turnover else None,
                },
                "latest_holdings": {
                    "report_period": str(latest_period) if latest_period else None,
                    "rows": holdings,
                },
                "quality_notes": [
                    "波动率按净值观测收益标准差年化，净值频率不完整时仅作研究参考",
                    "最新持仓受定期报告披露频率和范围限制",
                ],
            }
        finally:
            connection.close()

    def query_dataset(
        self,
        dataset: str,
        *,
        fund_symbol: str | None = None,
        as_of: date | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        allowed = {
            "fund_nav": ("symbol", "nav_date"),
            "fund_holdings": ("fund_symbol", "report_period"),
            "fund_reports": ("fund_symbol", "report_period"),
            "fund_scale": ("symbol", "report_period"),
            "fund_annual_turnover": ("fund_symbol", "report_period"),
            "fund_profiles": ("symbol", None),
            "fund_manager_tenures": ("fund_symbol", "begin_date"),
        }
        if dataset not in allowed:
            raise PolicyDenied(f"数据集不在 Agent 白名单：{dataset}")
        symbol_field, date_field = allowed[dataset]
        clauses: list[str] = []
        params: list[Any] = []
        if fund_symbol:
            clauses.append(f"{symbol_field}=?")
            params.append(fund_symbol)
        if as_of and date_field:
            clauses.append(f"{date_field}<=?")
            params.append(as_of)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        safe_limit = min(max(limit, 1), 1000)
        connection = self._connect()
        try:
            cursor = connection.execute(f"SELECT * FROM {dataset}{where} LIMIT {safe_limit}", params)
            columns = [item[0] for item in cursor.description]
            return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        except duckdb.Error as exc:
            raise DataUnavailable(str(exc)) from exc
        finally:
            connection.close()

    def run_select(self, sql: str, limit: int = 500) -> dict[str, Any]:
        if not SELECT_PATTERN.search(sql) or FORBIDDEN_SQL.search(sql):
            raise PolicyDenied("仅允许单条 SELECT/CTE 查询")
        connection = self._connect()
        try:
            cursor = connection.execute(sql)
            columns = [item[0] for item in cursor.description]
            rows = cursor.fetchmany(min(max(limit, 1), 1000) + 1)
            truncated = len(rows) > limit
            rows = rows[:limit]
            return {"columns": columns, "rows": rows, "truncated": truncated}
        finally:
            connection.close()
