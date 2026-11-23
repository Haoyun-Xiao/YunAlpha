"""Shared deterministic date/return rules. Values are ratios, never percentages."""

from __future__ import annotations

import pandas as pd

PIPELINE_VERSION = "report-data-v2"


def annual_return(nav: pd.Series, year: int, as_of) -> dict:
    series = pd.to_numeric(nav, errors="coerce").dropna().sort_index()
    series = series[~series.index.duplicated(keep="last")]
    series = series[series.index <= pd.Timestamp(as_of)]
    current = series[series.index.year == year]
    prior = series[series.index < pd.Timestamp(year, 1, 1)]
    base = prior.iloc[-1:] if not prior.empty else current.iloc[:1]
    valid = not current.empty and not base.empty and float(base.iloc[0]) > 0
    return {
        "year": year,
        "value": float(current.iloc[-1] / base.iloc[0] - 1) if valid else None,
        "from": base.index[0].date().isoformat() if not base.empty else None,
        "to": current.index[-1].date().isoformat() if not current.empty else None,
        "basis": "previous_year_close" if not prior.empty else "partial_first_year",
    }
