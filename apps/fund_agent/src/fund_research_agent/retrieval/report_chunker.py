from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

MAX_CHARS = 2400
YEAR_RE = re.compile(r"(20\d{2})")
HEADING_RE = re.compile(r"(?m)(?=^#{2,4}\s+)")


@dataclass(frozen=True)
class ReportChunk:
    ordinal: int
    part: str
    topic: str
    year: str | None
    content: str
    content_hash: str


def _topic(text: str, fallback: str) -> str:
    first = next((line.strip() for line in text.splitlines() if line.strip()), fallback)
    return first.lstrip("# ")[:120] or fallback


def _split_long(text: str, limit: int = MAX_CHARS) -> list[str]:
    if len(text) <= limit:
        return [text]
    paragraphs = [item.strip() for item in re.split(r"\n\s*\n", text) if item.strip()]
    chunks: list[str] = []
    current: list[str] = []
    current_chars = 0
    for paragraph in paragraphs:
        if current and current_chars + len(paragraph) > limit:
            chunks.append("\n\n".join(current))
            current = []
            current_chars = 0
        if len(paragraph) > limit:
            if current:
                chunks.append("\n\n".join(current))
                current = []
                current_chars = 0
            chunks.extend(
                paragraph[start : start + limit]
                for start in range(0, len(paragraph), limit)
            )
            continue
        current.append(paragraph)
        current_chars += len(paragraph)
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def chunk_report_parts(parts: dict[str, str]) -> list[ReportChunk]:
    result: list[ReportChunk] = []
    ordinal = 0
    for part, raw_text in parts.items():
        text = str(raw_text or "").strip()
        if not text:
            continue
        sections = [item.strip() for item in HEADING_RE.split(text) if item.strip()]
        for section in sections or [text]:
            topic = _topic(section, part)
            for piece in _split_long(section):
                if len(piece.strip()) < 8:
                    continue
                year_match = YEAR_RE.search(piece)
                digest = hashlib.sha256(piece.encode("utf-8")).hexdigest()
                result.append(
                    ReportChunk(
                        ordinal=ordinal,
                        part=part,
                        topic=topic,
                        year=year_match.group(1) if year_match else None,
                        content=piece,
                        content_hash=digest,
                    )
                )
                ordinal += 1
    return result
