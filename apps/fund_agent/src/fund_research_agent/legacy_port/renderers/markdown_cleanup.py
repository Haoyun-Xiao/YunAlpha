import re

_HEADING_PLACEHOLDER_BRACKET_RE = re.compile(r"([：:—-])\s*\[([^\]\n]{1,100})\]")
_REPEATED_SUMMARY_TERM_RE = re.compile(r"([\u4e00-\u9fff]{2,6})\1")


def strip_heading_placeholder_brackets(markdown: str) -> str:
    ends_with_newline = markdown.endswith("\n")
    lines = []
    for line in markdown.splitlines():
        if line.lstrip().startswith("#"):
            line = _HEADING_PLACEHOLDER_BRACKET_RE.sub(r"\1\2", line)
        lines.append(line)
    return "\n".join(lines) + ("\n" if ends_with_newline else "")


def collapse_repeated_summary_terms(text: str) -> str:
    cleaned = text
    for _ in range(4):
        next_cleaned = _REPEATED_SUMMARY_TERM_RE.sub(r"\1", cleaned)
        if next_cleaned == cleaned:
            break
        cleaned = next_cleaned
    return cleaned
