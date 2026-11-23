"""Read the configured market lake via Polars Parquet scans."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import polars as pl

RESEARCH_DATASETS = (
    "research_documents",
    "industry_chain_nodes",
    "industry_chain_edges",
    "industry_chain_company_links",
)

_SYMBOL_RE = re.compile(r"^\d{6}\.(?:SH|SZ|BJ)$")
_SAFE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_METRIC_UNITS = {
    "revenue": "CNY",
    "total_revenue": "CNY",
    "net_profit": "CNY",
    "net_income": "CNY",
    "operating_cost": "CNY",
    "operating_profit": "CNY",
    "total_profit": "CNY",
    "total_assets": "CNY",
    "total_equity": "CNY",
    "total_liabilities": "CNY",
    "net_cash_operate": "CNY",
    "net_cash_invest": "CNY",
    "net_cash_finance": "CNY",
    "capex": "CNY",
    "end_cash": "CNY",
    "total_mv": "CNY",
    "float_mv": "CNY",
    "close": "CNY/share",
    "pe_ttm": "multiple",
    "pb": "multiple",
    "ps_ttm": "multiple",
    "roe": "%",
    "gross_margin": "%",
    "revenue_yoy": "%",
    "net_profit_yoy": "%",
    "eps": "CNY/share",
    "eps_deducted": "CNY/share",
    "bps": "CNY/share",
    "ocf_per_share": "CNY/share",
    "pe_percentile_3y": "%",
    "pb_percentile_3y": "%",
    "ytd_return_hfq": "%",
}


class LakeDataError(ValueError):
    """Raised when a lake query cannot satisfy its data contract."""


def _finite(value: Any) -> float | int | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    # Keep integers as integers when the source already supplied one.
    return int(number) if isinstance(value, int) and not isinstance(value, bool) else number


def _json_safe(value: Any) -> Any:
    """Convert Polars/Python values to deterministic JSON-safe primitives."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _json_dumps(value: Any) -> str:
    return json.dumps(_json_safe(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(_json_dumps(value).encode("utf-8"))


def _parse_as_of(value: str | date) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise LakeDataError(f"invalid as_of date: {value!r}") from exc


def _subtract_years(value: date, years: int) -> date:
    """Subtract calendar years while keeping leap-day inputs valid."""
    try:
        return value.replace(year=value.year - years)
    except ValueError:
        return value.replace(year=value.year - years, day=28)


def _safe_component(value: str, field: str) -> str:
    text = str(value).strip()
    if not text or not _SAFE_COMPONENT_RE.fullmatch(text):
        raise LakeDataError(f"invalid {field}: {value!r}")
    return text


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve()


def _ensure_inside(path: Path, root: Path, *, field: str) -> Path:
    resolved = _resolved(path)
    root_resolved = _resolved(root)
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise LakeDataError(f"{field} escapes its allowed root: {path}") from exc
    return resolved


def _dataset_files(root: Path, dataset: str) -> list[Path]:
    """Return parquet files below a dataset root without opening DuckDB."""
    if not root.exists():
        return []
    return sorted(root.glob("**/*.parquet"))


def _read_dataset(root: Path, dataset: str, *, columns: list[str] | None = None) -> pl.DataFrame:
    files = _dataset_files(root, dataset)
    if not files:
        return pl.DataFrame()
    return pl.read_parquet([str(p) for p in files], columns=columns)


def _read_filtered(
    root: Path,
    dataset: str,
    *,
    symbols: list[str] | None = None,
    columns: list[str] | None = None,
) -> pl.DataFrame:
    files = _dataset_files(root, dataset)
    if not files:
        return pl.DataFrame()
    lf = pl.scan_parquet([str(p) for p in files])
    schema = lf.collect_schema().names()
    if columns:
        columns = [c for c in columns if c in schema]
    if symbols and "symbol" in schema:
        lf = lf.filter(pl.col("symbol").is_in(symbols))
    return lf.select(columns).collect() if columns else lf.collect()


class LakeRepository:
    """Read-only adapter over the old project's Parquet lake.

    ``lake_root`` may point at the real lake or a temporary copy used by tests.
    No constructor or read method creates files, opens DuckDB, or updates the
    old project's metadata.  Only ``freeze_bundle`` writes to the caller's
    explicit job directory.
    """

    def __init__(self, lake_root: Path):
        self.lake_root = _resolved(Path(lake_root))
        self.curated_root = self.lake_root / "curated"
        self.derived_root = self.lake_root / "derived"

    def _curated(self, dataset: str) -> Path:
        return self.curated_root / dataset

    def _derived(self, dataset: str) -> Path:
        return self.derived_root / dataset

    @staticmethod
    def _normalise_symbol(value: str) -> str:
        text = str(value).strip().upper()
        if _SYMBOL_RE.fullmatch(text):
            return text
        if re.fullmatch(r"\d{6}", text):
            # The caller may omit an exchange; resolve it through instruments.
            return text
        return text

    def resolve_company(self, query: str, limit: int = 10) -> list[dict]:
        """Resolve an A-share company by code or disclosed identity name.

        This is intentionally an identity resolver, not a business keyword
        search.  ``main_business``/``introduction`` may mention competitors or
        customers and therefore belong only in :meth:`find_candidates`.
        """
        text = str(query).strip()
        if not text:
            return []
        limit = max(1, min(int(limit), 100))
        companies = _read_dataset(
            self._curated("companies"),
            "companies",
            columns=["symbol", "com_name", "main_business"],
        )
        instruments = _read_dataset(
            self._curated("instruments"),
            "instruments",
            columns=["symbol", "name", "asset_type"],
        )
        if companies.is_empty() and instruments.is_empty():
            return []
        listed_symbols: set[str] = set()
        identity_by_symbol: dict[str, dict[str, Any]] = {}
        if not instruments.is_empty():
            for row in instruments.to_dicts():
                symbol = str(row.get("symbol") or "").strip().upper()
                asset_type = str(row.get("asset_type") or "").strip().lower()
                if not symbol or not (
                    asset_type in {"stock", "equity", "a"} or _SYMBOL_RE.fullmatch(symbol)
                ):
                    continue
                listed_symbols.add(symbol)
                identity_by_symbol.setdefault(symbol, {}).update(
                    {"symbol": symbol, "instrument_name": row.get("name")}
                )
        for row in companies.to_dicts() if not companies.is_empty() else []:
            symbol = str(row.get("symbol") or "").strip().upper()
            if not symbol or (listed_symbols and symbol not in listed_symbols):
                continue
            identity_by_symbol.setdefault(symbol, {}).update(
                {
                    "symbol": symbol,
                    "company_name": row.get("com_name"),
                    "main_business": row.get("main_business"),
                }
            )
        if not identity_by_symbol:
            return []

        query_key = self._normalise_identity_name(text)
        query_upper = text.upper()
        code_query = query_upper if _SYMBOL_RE.fullmatch(query_upper) else None
        if re.fullmatch(r"\d{6}", query_upper):
            code_matches = [
                symbol for symbol in identity_by_symbol if symbol.startswith(query_upper)
            ]
        else:
            code_matches = [code_query] if code_query in identity_by_symbol else []
        if code_matches:
            matches = [(0, symbol) for symbol in code_matches]
        else:
            matches = []
            for symbol, identity in identity_by_symbol.items():
                names = {
                    self._normalise_identity_name(identity.get("instrument_name")),
                    self._normalise_identity_name(identity.get("company_name")),
                }
                names.discard("")
                if query_key in names:
                    matches.append((1, symbol))
        matches.sort(key=lambda item: (item[0], item[1]))
        return [
            {
                "symbol": symbol,
                "name": identity_by_symbol[symbol].get("instrument_name")
                or identity_by_symbol[symbol].get("company_name"),
                "main_business": identity_by_symbol[symbol].get("main_business"),
            }
            for _, symbol in matches[:limit]
        ]

    @staticmethod
    def _normalise_identity_name(value: Any) -> str:
        """Normalize names for exact identity matching, never for substring search."""
        text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
        return re.sub(r"[\s\u3000()（）·,，.。\-—_]", "", text)

    def find_candidates(self, keywords: list[str], limit: int = 24) -> list[dict]:
        """Find real listed A-shares whose disclosed business matches keywords."""
        terms = [str(k).strip() for k in keywords if str(k).strip()]
        if not terms:
            return []
        limit = max(1, min(int(limit), 200))
        companies = _read_dataset(
            self._curated("companies"),
            "companies",
            columns=["symbol", "com_name", "main_business", "business_scope", "introduction"],
        )
        instruments = _read_dataset(
            self._curated("instruments"),
            "instruments",
            columns=["symbol", "name", "asset_type"],
        )
        if companies.is_empty() and instruments.is_empty():
            return []
        if companies.is_empty():
            companies = pl.DataFrame(
                {
                    "symbol": [],
                    "com_name": [],
                    "main_business": [],
                    "business_scope": [],
                    "introduction": [],
                }
            )
        # Keep only actual listed A-share symbols when instruments is available.
        if not instruments.is_empty():
            listed = instruments.filter(
                pl.col("asset_type")
                .fill_null("")
                .str.to_lowercase()
                .is_in(["stock", "equity", "a"])
                | pl.col("symbol").str.contains(r"\.(SH|SZ|BJ)$")
            ).select(["symbol", pl.col("name").alias("instrument_name")])
            companies = companies.join(listed, on="symbol", how="inner")
        else:
            companies = companies.filter(pl.col("symbol").str.contains(r"\.(SH|SZ|BJ)$"))
        if companies.is_empty():
            return []
        text_expr = pl.concat_str(
            [
                pl.col(c).fill_null("")
                for c in ["com_name", "main_business", "business_scope", "introduction"]
                if c in companies.columns
            ],
            separator=" ",
        ).str.to_lowercase()
        # Count terms in the disclosed fields; a term is counted once per company.
        count_expr = sum(
            text_expr.str.contains(t.lower(), literal=True).cast(pl.Int32) for t in terms
        )
        any_expr = count_expr > 0
        companies = companies.filter(any_expr).with_columns(count_expr.alias("_matched_count"))
        # Attach segment evidence, but use the latest non-PIT disclosure only.
        segments = _read_dataset(
            self._curated("business_segments"),
            "business_segments",
            columns=[
                "symbol",
                "report_period",
                "segment_type",
                "segment_name",
                "revenue",
                "profit",
                "source",
            ],
        )
        if not segments.is_empty():
            seg_text = pl.concat_str(
                [pl.col(c).fill_null("") for c in ["segment_name"]], separator=" "
            ).str.to_lowercase()
            seg_matches = segments.filter(
                sum(seg_text.str.contains(t.lower(), literal=True).cast(pl.Int32) for t in terms)
                > 0
            )
            if not seg_matches.is_empty():
                seg_counts = seg_matches.group_by("symbol").agg(
                    pl.len().alias("segment_match_count"),
                    pl.col("segment_name")
                    .drop_nulls()
                    .unique()
                    .sort()
                    .head(8)
                    .alias("segment_names"),
                )
                companies = companies.join(seg_counts, on="symbol", how="left").with_columns(
                    pl.col("segment_match_count").fill_null(0)
                )
        if "segment_match_count" not in companies.columns:
            companies = companies.with_columns(pl.lit(0).alias("segment_match_count"))
        companies = companies.sort(
            ["_matched_count", "segment_match_count", "symbol"], descending=[True, True, False]
        ).head(limit)
        results: list[dict] = []
        for row in companies.to_dicts():
            direct = int(row.get("_matched_count") or 0)
            seg_count = int(row.get("segment_match_count") or 0)
            results.append(
                {
                    "symbol": row.get("symbol"),
                    "name": row.get("com_name") or row.get("instrument_name"),
                    "matched_terms": [
                        t
                        for t in terms
                        if any(
                            t.lower() in str(row.get(c) or "").lower()
                            for c in ["com_name", "main_business", "business_scope", "introduction"]
                        )
                    ],
                    "business_evidence": row.get("main_business") or row.get("introduction"),
                    "segment_evidence": row.get("segment_names") or [],
                    "confidence": "high" if direct >= 2 or seg_count >= 2 else "medium",
                    "limitations": ["业务匹配来自公开披露文本；未证明收入纯度或客户关系"],
                }
            )
        return results

    def _company_row(self, symbol: str) -> dict | None:
        text = self._normalise_symbol(symbol)
        companies = _read_dataset(
            self._curated("companies"),
            "companies",
            columns=["symbol", "com_name", "main_business", "business_scope", "introduction"],
        )
        instruments = _read_dataset(
            self._curated("instruments"), "instruments", columns=["symbol", "name", "asset_type"]
        )
        if not instruments.is_empty() and re.fullmatch(r"\d{6}", text):
            found = instruments.filter(pl.col("symbol").str.starts_with(text)).head(1)
            if not found.is_empty():
                text = str(found[0, "symbol"])
        if companies.is_empty():
            return {
                "symbol": text,
                "name": None,
                "main_business": None,
                "identity_warning": "companies dataset unavailable",
            }
        found = companies.filter(pl.col("symbol") == text).head(1)
        if found.is_empty():
            return None
        row = found.to_dicts()[0]
        return {
            "symbol": text,
            "name": row.get("com_name"),
            "main_business": row.get("main_business"),
            "business_scope": row.get("business_scope"),
            "introduction": row.get("introduction"),
        }

    @staticmethod
    def _dedupe_time_series(frame: pl.DataFrame, keys: list[str]) -> pl.DataFrame:
        if frame.is_empty() or not all(key in frame.columns for key in keys):
            return frame
        return frame.unique(subset=keys, keep="last", maintain_order=True).sort(keys)

    @staticmethod
    def _source_label(*sources: Any) -> str | None:
        values = sorted({str(source) for source in sources if source not in (None, "")})
        return ";".join(values) if values else None

    def _valuation_percentiles(
        self,
        valuation: pl.DataFrame,
        *,
        as_of: date,
        metric_names: tuple[str, ...] = ("pe_ttm", "pb"),
    ) -> tuple[dict[str, dict], dict[str, Any], list[str]]:
        """Calculate inclusive 3-year positive-value percentiles.

        The window is ``[as_of - 3 calendar years, as_of]``.  Only finite,
        strictly positive observations are valid.  The percentile is the
        inclusive empirical rank ``100 * count(value <= current) / n``; it is
        therefore explicitly sample-based and does not interpolate between
        unavailable observations.
        """
        metrics: dict[str, dict] = {}
        inputs: dict[str, Any] = {}
        warnings: list[str] = []
        if valuation.is_empty() or "trade_date" not in valuation.columns:
            return metrics, inputs, ["valuation_percentile_3y_unavailable"]
        start = _subtract_years(as_of, 3)
        window = valuation.filter((pl.col("trade_date") >= start) & (pl.col("trade_date") <= as_of))
        inputs["window_start"] = start.isoformat()
        inputs["window_end"] = as_of.isoformat()
        for metric_name in metric_names:
            if metric_name not in window.columns:
                warnings.append(f"{metric_name}_percentile_3y_unavailable")
                continue
            valid = (
                window.select(["trade_date", metric_name, "source"])
                .with_columns(pl.col(metric_name).cast(pl.Float64, strict=False))
                .filter(
                    pl.col(metric_name).is_not_null()
                    & pl.col(metric_name).is_finite()
                    & (pl.col(metric_name) > 0)
                )
                .sort("trade_date")
            )
            current = window.sort("trade_date").tail(1)
            values = [float(value) for value in valid.get_column(metric_name).to_list()]
            current_value = (
                _finite(current.item(0, metric_name)) if not current.is_empty() else None
            )
            if current_value is None or current_value <= 0 or not values:
                warnings.append(f"{metric_name}_percentile_3y_unavailable")
                inputs[metric_name] = {
                    "valid_sample_count": len(values),
                    "valid_min": None,
                    "valid_max": None,
                    "reason": "latest valuation unavailable/nonpositive; do not use a historical positive PE as current",
                }
                continue
            percentile = 100.0 * sum(value <= current_value for value in values) / len(values)
            current_row = current.to_dicts()[0]
            output_name = f"{metric_name.removesuffix('_ttm')}_percentile_3y"
            metrics[output_name] = self._metric(
                output_name,
                percentile,
                period=f"{start.isoformat()}..{as_of.isoformat()}",
                as_of=as_of,
                source=self._source_label(*valid.get_column("source").drop_nulls().to_list()),
                formula=(
                    f"100 * count({metric_name} <= current_{metric_name}) / n; "
                    f"positive finite {metric_name} observations, trade_date in "
                    f"[{start.isoformat()}, {as_of.isoformat()}], n={len(values)}, "
                    f"current_date={current_row['trade_date']}"
                ),
            )
            inputs[metric_name] = {
                "valid_sample_count": len(values),
                "valid_min": min(values),
                "valid_max": max(values),
                "current_value": current_value,
                "current_date": _json_safe(current_row["trade_date"]),
            }
        return metrics, inputs, warnings

    def _ytd_return_hfq(
        self,
        bars: pl.DataFrame,
        factors: pl.DataFrame,
        *,
        as_of: date,
    ) -> tuple[dict | None, dict[str, Any], list[str]]:
        """Calculate YTD from fully adjusted prices, or return explicit N/A."""
        inputs: dict[str, Any] = {"adjust_type": "hfq", "as_of": as_of.isoformat()}
        if bars.is_empty() or "trade_date" not in bars.columns or "close" not in bars.columns:
            return None, inputs, ["ytd_return_hfq_bars_unavailable"]
        bars = self._dedupe_time_series(
            bars.filter(pl.col("trade_date") <= as_of), ["symbol", "trade_date"]
        )
        year_start = date(as_of.year, 1, 1)
        end = bars.filter(pl.col("trade_date") >= year_start).tail(1)
        baseline = bars.filter(pl.col("trade_date") < year_start).tail(1)
        if end.is_empty():
            return None, inputs, ["ytd_return_hfq_no_current_year_bar"]
        if baseline.is_empty():
            inputs["end_trade_date"] = _json_safe(end.item(0, "trade_date"))
            return None, inputs, ["ytd_return_hfq_baseline_missing"]
        if factors.is_empty() or "factor" not in factors.columns:
            return None, inputs, ["ytd_return_hfq_factors_unavailable"]
        factors = factors.filter(
            (pl.col("adjust_type") == "hfq") if "adjust_type" in factors.columns else pl.lit(True)
        )
        factors = self._dedupe_time_series(factors, ["symbol", "trade_date"])
        joined = pl.concat([baseline, end], how="vertical_relaxed").join(
            factors.select(
                [c for c in ["symbol", "trade_date", "factor", "source"] if c in factors.columns]
            ),
            on=["symbol", "trade_date"],
            how="left",
            suffix="_factor",
        )
        if joined.height < 2 or joined.get_column("factor").null_count() > 0:
            inputs["baseline_trade_date"] = _json_safe(baseline.item(0, "trade_date"))
            inputs["end_trade_date"] = _json_safe(end.item(0, "trade_date"))
            return None, inputs, ["ytd_return_hfq_factor_missing"]
        values = joined.with_columns(
            (
                pl.col("close").cast(pl.Float64, strict=False)
                * pl.col("factor").cast(pl.Float64, strict=False)
            ).alias("adjusted_close")
        ).sort("trade_date")
        start_row, end_row = values.row(0, named=True), values.row(-1, named=True)
        start_price, end_price = (
            _finite(start_row.get("adjusted_close")),
            _finite(end_row.get("adjusted_close")),
        )
        if start_price is None or end_price is None or start_price <= 0 or end_price <= 0:
            return None, inputs, ["ytd_return_hfq_invalid_adjusted_price"]
        result = 100.0 * (float(end_price) / float(start_price) - 1.0)
        inputs.update(
            {
                "baseline_trade_date": _json_safe(start_row.get("trade_date")),
                "end_trade_date": _json_safe(end_row.get("trade_date")),
                "baseline_adjusted_close": float(start_price),
                "end_adjusted_close": float(end_price),
            }
        )
        metric = self._metric(
            "ytd_return_hfq",
            result,
            period=f"{start_row['trade_date']}..{end_row['trade_date']}",
            as_of=as_of,
            source=self._source_label(
                start_row.get("source"),
                end_row.get("source"),
                start_row.get("source_factor"),
                end_row.get("source_factor"),
            ),
            formula=(
                "100 * (close_end * hfq_factor_end) / "
                "(close_baseline * hfq_factor_baseline) - 100; "
                "baseline is the last trading day before Jan 1 and end is the "
                "last trading day <= as_of"
            ),
        )
        return metric, inputs, []

    @staticmethod
    def _metric(
        metric: str, value: Any, *, period: Any, as_of: date, source: str | None, formula: str
    ) -> dict:
        finite = _finite(value)
        return {
            "value": finite,
            "unit": _METRIC_UNITS.get(metric, "source_defined"),
            "period": _json_safe(period),
            "as_of": as_of.isoformat(),
            "formula": formula,
            "source": source,
        }

    def company_pack(self, symbol: str, as_of: str) -> dict:
        """Build a frozen, auditable company research pack with strict PIT rules."""
        as_of_date = _parse_as_of(as_of)
        identity = self._company_row(symbol)
        normalized_symbol = self._normalise_symbol(symbol)
        if identity is None:
            raise LakeDataError(f"company not found: {symbol}")
        normalized_symbol = identity["symbol"]
        warnings: list[str] = []
        metrics: dict[str, dict] = {}
        inputs: dict[str, Any] = {}

        bars = _read_filtered(
            self._curated("daily_bars"),
            "daily_bars",
            symbols=[normalized_symbol],
            columns=["symbol", "trade_date", "close", "source", "data_version"],
        )
        if not bars.is_empty() and "trade_date" in bars.columns:
            bars = self._dedupe_time_series(
                bars.filter(pl.col("trade_date") <= as_of_date), ["symbol", "trade_date"]
            )
            if not bars.is_empty():
                row = bars.tail(1).to_dicts()[0]
                metrics["close"] = self._metric(
                    "close",
                    row.get("close"),
                    period=row.get("trade_date"),
                    as_of=as_of_date,
                    source=row.get("source"),
                    formula="daily_bars.close at max(trade_date <= as_of)",
                )
                inputs["daily_bars"] = {
                    "rows": [_json_safe(x) for x in bars.tail(30).to_dicts()],
                    "latest_date": _json_safe(row.get("trade_date")),
                }
                factors = _read_filtered(
                    self._derived("adj_factors"),
                    "adj_factors",
                    symbols=[normalized_symbol],
                    columns=[
                        "symbol",
                        "trade_date",
                        "factor",
                        "adjust_type",
                        "source",
                        "data_version",
                    ],
                )
                ytd_metric, ytd_inputs, ytd_warnings = self._ytd_return_hfq(
                    bars, factors, as_of=as_of_date
                )
                if ytd_metric is not None:
                    metrics["ytd_return_hfq"] = ytd_metric
                warnings.extend(ytd_warnings)
                inputs["ytd_return_hfq"] = ytd_inputs
            else:
                warnings.append("daily_bars_missing_before_as_of")
                warnings.append("ytd_return_hfq_bars_unavailable")
        else:
            warnings.append("daily_bars_unavailable")
            warnings.append("ytd_return_hfq_bars_unavailable")

        valuation = _read_filtered(
            self._curated("valuation_metrics"),
            "valuation_metrics",
            symbols=[normalized_symbol],
            columns=[
                "symbol",
                "trade_date",
                "pe_ttm",
                "pb",
                "ps_ttm",
                "total_mv",
                "float_mv",
                "source",
                "data_version",
            ],
        )
        if not valuation.is_empty():
            valuation = self._dedupe_time_series(
                valuation.filter(pl.col("trade_date") <= as_of_date), ["symbol", "trade_date"]
            )
            if not valuation.is_empty():
                row = valuation.tail(1).to_dicts()[0]
                for metric in ["pe_ttm", "pb", "ps_ttm", "total_mv", "float_mv"]:
                    metrics[metric] = self._metric(
                        metric,
                        row.get(metric),
                        period=row.get("trade_date"),
                        as_of=as_of_date,
                        source=row.get("source"),
                        formula=f"valuation_metrics.{metric} at max(trade_date <= as_of)",
                    )
                percentile_metrics, percentile_inputs, percentile_warnings = (
                    self._valuation_percentiles(valuation, as_of=as_of_date)
                )
                metrics.update(percentile_metrics)
                warnings.extend(percentile_warnings)
                inputs["valuation_metrics"] = {
                    "row": _json_safe(row),
                    "percentile_3y": percentile_inputs,
                }
            else:
                warnings.append("valuation_metrics_missing_before_as_of")
                warnings.extend(["pe_percentile_3y_unavailable", "pb_percentile_3y_unavailable"])
        else:
            warnings.append("valuation_metrics_unavailable")
            warnings.extend(["pe_percentile_3y_unavailable", "pb_percentile_3y_unavailable"])

        financial = _read_filtered(
            self._curated("financial_statement_items"),
            "financial_statement_items",
            symbols=[normalized_symbol],
            columns=[
                "symbol",
                "report_period",
                "statement_type",
                "item_code",
                "item_value",
                "announce_date",
                "source",
                "data_version",
            ],
        )
        financial_rows: list[dict] = []
        if not financial.is_empty():
            if "announce_date" not in financial.columns:
                warnings.append("financial_statement_items_missing_announce_date")
            else:
                # PIT filter is mandatory.  Keep all accepted rows in the pack so
                # an evaluator can inspect which vintage was selected.
                financial = financial.filter(pl.col("announce_date") <= as_of_date)
                if not financial.is_empty():
                    financial = financial.sort(["item_code", "report_period", "announce_date"])
                    # The old lake stores one row per item/report/vintage.  The
                    # final row after this deterministic sort is the latest
                    # report period and latest known vintage for that item.
                    latest = financial.group_by("item_code", maintain_order=True).tail(1)
                    for row in latest.to_dicts():
                        metric_name = row.get("item_code")
                        if metric_name:
                            metrics[metric_name] = self._metric(
                                metric_name,
                                row.get("item_value"),
                                period=row.get("report_period"),
                                as_of=as_of_date,
                                source=row.get("source"),
                                formula=f"financial_statement_items.{metric_name}; announce_date <= {as_of_date.isoformat()}, latest report_period",
                            )
                    financial_rows = [_json_safe(x) for x in financial.to_dicts()]
                    financial_periods = {
                        str(metric_name): _json_safe(metric.get("period"))
                        for metric_name, metric in metrics.items()
                        if metric_name in {str(row.get("item_code")) for row in latest.to_dicts()}
                    }
                    inputs["financial_statement_items"] = {
                        "accepted_rows": financial_rows,
                        "pit": True,
                        "as_of": as_of_date.isoformat(),
                        "latest_metric_periods": financial_periods,
                    }
                    comparison_periods = {
                        period for period in financial_periods.values() if period is not None
                    }
                    if len(comparison_periods) > 1:
                        warnings.append("financial_report_period_mismatch")
                        inputs["financial_statement_items"]["period_mismatch"] = sorted(
                            comparison_periods
                        )
                else:
                    warnings.append("financial_statement_items_no_vintage_known_at_as_of")
        else:
            warnings.append("financial_statement_items_unavailable")

        segments = _read_filtered(
            self._curated("business_segments"),
            "business_segments",
            symbols=[normalized_symbol],
            columns=[
                "symbol",
                "report_period",
                "segment_type",
                "segment_name",
                "revenue",
                "profit",
                "cost",
                "currency",
                "source",
                "data_version",
            ],
        )
        segment_rows: list[dict] = []
        if not segments.is_empty():
            # business_segments has no announce_date in the old lake.  It is
            # intentionally non-PIT and must carry that limitation forward.
            if "report_period" in segments.columns:
                segments = segments.filter(pl.col("report_period") <= as_of_date).sort(
                    "report_period"
                )
            segment_rows = [
                {
                    **_json_safe(row),
                    "fact_type": "source_fact",
                    "pit": "non_pit",
                    "limitation": "business_segments source has no announce_date",
                }
                for row in segments.to_dicts()
            ]
            inputs["business_segments"] = {
                "rows": segment_rows,
                "pit": False,
                "latest_report_period": _json_safe(
                    segments.select(pl.col("report_period").max()).item()
                )
                if not segments.is_empty()
                else None,
            }
            warnings.append("business_segments_non_pit")
        else:
            warnings.append("business_segments_unavailable")

        if not any(metrics):
            warnings.append("no_company_metrics")
        if "roic" not in metrics:
            warnings.append("roic_not_computed_no_guessed_formula")
        pack = {
            "symbol": normalized_symbol,
            "name": identity.get("name"),
            "as_of": as_of_date.isoformat(),
            "metrics": metrics,
            "business_segments": segment_rows,
            "warnings": sorted(set(warnings)),
            "inputs": inputs,
            "identity": {
                k: identity.get(k)
                for k in ["symbol", "name", "main_business", "business_scope", "introduction"]
            },
            "source_policy": {
                "financial_statement_items": "PIT announce_date <= as_of",
                "business_segments": "non-PIT source; report_period filtered only",
            },
        }
        # Ensure no NaN/Infinity can escape through future adapter changes.
        return _json_safe(pack)

    def market_anchor(self, as_of: str) -> str:
        as_of_date = _parse_as_of(as_of)
        dates: list[date] = []
        for dataset, column in [("daily_bars", "trade_date"), ("valuation_metrics", "trade_date")]:
            files = _dataset_files(self._curated(dataset), dataset)
            if not files:
                continue
            frame = (
                pl.scan_parquet([str(p) for p in files])
                .select(pl.col(column).max().alias("max_date"))
                .collect()
            )
            if not frame.is_empty() and frame.item() is not None:
                max_date = frame.item()
                if isinstance(max_date, datetime):
                    max_date = max_date.date()
                if max_date <= as_of_date:
                    dates.append(max_date)
                else:
                    frame = (
                        pl.scan_parquet([str(p) for p in files])
                        .filter(pl.col(column) <= as_of_date)
                        .select(pl.col(column).max().alias("max_date"))
                        .collect()
                    )
                    if not frame.is_empty() and frame.item() is not None:
                        dates.append(frame.item())
        if not dates:
            raise LakeDataError(f"no market data on or before {as_of_date.isoformat()}")
        return max(dates).isoformat()

    def read_research_dataset(self, dataset: str, chain_id: str, version: str) -> pl.DataFrame:
        """Read one committed research dataset version without opening DuckDB."""
        if dataset not in RESEARCH_DATASETS:
            raise LakeDataError(f"unknown research dataset: {dataset}")
        chain_id = _safe_component(chain_id, "chain_id")
        version = _safe_component(version, "version")
        manifest_path = (
            self.lake_root
            / "meta"
            / "research"
            / "industry_chain"
            / chain_id
            / version
            / "manifest.json"
        )
        _ensure_inside(manifest_path, self.lake_root, field="research manifest")
        if not manifest_path.exists():
            raise LakeDataError(
                f"research dataset version is not committed: {dataset}/{chain_id}/{version}"
            )
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LakeDataError(f"invalid research manifest: {manifest_path}") from exc
        if manifest.get("status") != "committed":
            raise LakeDataError(
                f"research dataset version is not committed: {dataset}/{chain_id}/{version}"
            )
        root = self.curated_root / dataset / f"chain_id={chain_id}" / f"version={version}"
        _ensure_inside(root, self.lake_root, field="research dataset")
        entry = (manifest.get("datasets") or {}).get(dataset) or {}
        expected_relative = entry.get("curated_path")
        expected = root / "part-merged.parquet"
        if expected_relative and expected_relative != str(expected.relative_to(self.lake_root)):
            raise LakeDataError(f"research manifest path mismatch: {dataset}/{chain_id}/{version}")
        files = [expected] if expected.exists() else []
        if not files:
            raise LakeDataError(
                f"committed research dataset file is missing: {dataset}/{chain_id}/{version}"
            )
        expected_hash = entry.get("curated_sha256")
        if expected_hash and _sha256_bytes(expected.read_bytes()) != expected_hash:
            raise LakeDataError(
                f"committed research dataset hash mismatch: {dataset}/{chain_id}/{version}"
            )
        return pl.read_parquet([str(path) for path in files])

    def freeze_bundle(self, symbols: list[str], as_of: str, output_dir: Path) -> dict:
        output = _resolved(Path(output_dir))
        if output == self.lake_root or self.lake_root in output.parents:
            raise LakeDataError("freeze_bundle output_dir cannot be inside the source lake")
        output.mkdir(parents=True, exist_ok=True)
        packs: dict[str, dict] = {}
        files: dict[str, str] = {}
        for symbol in dict.fromkeys(str(s).strip() for s in symbols if str(s).strip()):
            pack = self.company_pack(symbol, as_of)
            key = pack["symbol"]
            packs[key] = pack
            payload = _json_dumps(pack).encode("utf-8")
            path = output / f"company_pack_{key.replace('.', '_')}.json"
            path.write_bytes(payload)
            files[str(path.name)] = _sha256_bytes(payload)
        manifest = {
            "as_of": _parse_as_of(as_of).isoformat(),
            "symbols": sorted(packs),
            "packs": {symbol: _sha256_json(pack) for symbol, pack in sorted(packs.items())},
            "files": files,
            "created_at": datetime.now(UTC).isoformat(),
            "source_lake": str(self.lake_root),
        }
        (output / "input_manifest.json").write_text(_json_dumps(manifest), encoding="utf-8")
        return {"as_of": manifest["as_of"], "packs": packs, "manifest": manifest}
