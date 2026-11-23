from __future__ import annotations

import re

FUND_CODE_RE = re.compile(r"(?<!\d)(\d{6}(?:\.(?:OF|SH|SZ))?)(?!\d)", re.IGNORECASE)
ACTIVE_REFERENCE_RE = re.compile(
    r"(这只(?:基金)?|该基金|它|他|她|其|当前(?:这只)?|刚才(?:那只)?|"
    r"上面(?:那只)?|前面(?:那只)?)"
)

# These are request wrappers, not a fund-name dictionary.  Removing them leaves
# entity resolution to the authoritative universe/lake adapters.
REQUEST_WRAPPERS = re.compile(
    r"(请|帮我|给我|麻烦|一份|完整的?|深度的?|"
    r"写|生成|制作|产出|出|分析一下|分析下|分析|研究一下|"
    r"研究|看看|看下|解读|评价|的?基金深度报告|的?深度报告|"
    r"的?基金报告|的?报告)"
)


def extract_fund_query(text: str) -> str:
    """Extract a resolver-oriented query without guessing the fund identity."""
    code = FUND_CODE_RE.search(text)
    if code:
        value = code.group(1).upper()
        return value if "." in value else f"{value}.OF"
    return explicit_fund_query(text) or text.strip()


def explicit_fund_query(text: str) -> str | None:
    """Return the stated entity, or None when the request omits its object."""
    code = FUND_CODE_RE.search(text)
    if code:
        value = code.group(1).upper()
        return value if "." in value else f"{value}.OF"
    cleaned = REQUEST_WRAPPERS.sub("", text).strip(" \t\r\n，,。！!？?:：")
    return cleaned or None


def references_active_fund(text: str) -> bool:
    return bool(ACTIVE_REFERENCE_RE.search(text))


def normalize_fund_text(value: object) -> str:
    return re.sub(r"[\s\-_—·（）()]+", "", str(value or "")).lower()


def is_subsequence(needle: str, haystack: str) -> bool:
    if not needle:
        return False
    iterator = iter(haystack)
    return all(any(char == candidate for candidate in iterator) for char in needle)
