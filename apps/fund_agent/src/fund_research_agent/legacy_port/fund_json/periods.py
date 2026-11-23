from __future__ import annotations

from datetime import date

import pandas as pd


def quarter_label(value: date | str | pd.Timestamp) -> str:
    ts = pd.Timestamp(value)
    return f"{ts.year}Q{(ts.month - 1) // 3 + 1}"


def quarter_end(value: date | str | pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts + pd.offsets.QuarterEnd(0)


def previous_quarter_end(value: date | str | pd.Timestamp) -> pd.Timestamp:
    return quarter_end(value) - pd.offsets.QuarterEnd()


def next_quarter_end(value: date | str | pd.Timestamp) -> pd.Timestamp:
    return quarter_end(value) + pd.offsets.QuarterEnd()


def last_completed_quarter(value: date | str | pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    end = quarter_end(ts)
    return end if ts.normalize() == end.normalize() else end - pd.offsets.QuarterEnd()


def in_research_window(
    value: date | str | pd.Timestamp,
    start: date,
    end: date | None,
    as_of: date,
) -> bool:
    q = quarter_end(value)
    first = quarter_end(start)
    last = last_completed_quarter(min(end, as_of) if end else as_of)
    return first <= q <= last


def display_tenure(start: date, end: date | None) -> str:
    left = f"{start.year}.{start.month}"
    right = f"{end.year}.{end.month}" if end else "至今"
    return f"{left}-{right}"
