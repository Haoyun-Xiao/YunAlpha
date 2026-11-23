from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Iterable
from typing import Any, Literal

from pydantic import BaseModel, Field

from fund_research_agent.adapters.model import ModelGateway
from fund_research_agent.skills import SkillLoader

PRIMARY_TYPES = {
    "raw_transcript",
    "qa_interview",
    "meeting_minutes",
    "manager_speech",
}
SUPPLEMENT_TYPES = {"edited_interview", "third_party_analysis"}
NEAR_DUPLICATE_CONTAINMENT = 0.75


def _content_shingles(text: str) -> set[int]:
    normalized = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", text).lower()
    if not normalized:
        return set()
    if len(normalized) < 48:
        return {hash(normalized)}
    return {
        hash(normalized[index : index + 24])
        for index in range(0, len(normalized) - 23)
    }


def _near_duplicate(left: set[int], right: set[int]) -> bool:
    if not left or not right:
        return False
    return len(left & right) / min(len(left), len(right)) >= NEAR_DUPLICATE_CONTAINMENT


class MaterialAssessment(BaseModel):
    candidate_id: str
    document_type: Literal[
        "raw_transcript",
        "qa_interview",
        "meeting_minutes",
        "manager_speech",
        "edited_interview",
        "third_party_analysis",
        "profile",
        "marketing",
        "navigation",
        "unrelated",
    ]
    identity_match: float = Field(ge=0, le=1)
    rawness_score: float = Field(ge=0, le=1)
    attribution_confidence: float = Field(ge=0, le=1)
    research_value: float = Field(ge=0, le=1)
    event_key: str | None = None
    event_date: str | None = None
    published_date: str | None = None
    date_source: Literal[
        "page_metadata",
        "visible_text",
        "title",
        "url",
        "search_result",
        "inferred",
        "unknown",
    ] = "unknown"
    date_confidence: float = Field(default=0, ge=0, le=1)
    speakers: list[str] = Field(default_factory=list)
    transcript_signals: list[str] = Field(default_factory=list)
    processing_signals: list[str] = Field(default_factory=list)
    identity_warnings: list[str] = Field(default_factory=list)
    summary: str = ""

    @property
    def selection_score(self) -> float:
        return round(
            self.rawness_score * 0.40
            + self.identity_match * 0.30
            + self.research_value * 0.20
            + self.attribution_confidence * 0.10,
            6,
        )


class MaterialAssessmentBatch(BaseModel):
    assessments: list[MaterialAssessment]


def _is_primary(assessment: MaterialAssessment) -> bool:
    return (
        assessment.document_type in PRIMARY_TYPES
        and assessment.identity_match >= 0.80
        and assessment.rawness_score >= 0.65
        and assessment.research_value >= 0.60
    )


def _is_supplement(assessment: MaterialAssessment) -> bool:
    return (
        assessment.document_type in SUPPLEMENT_TYPES
        and assessment.identity_match >= 0.80
        and assessment.research_value >= 0.75
    )


def _temporal_year(assessment: MaterialAssessment) -> int | None:
    for value in (assessment.event_date, assessment.published_date):
        match = re.match(r"^(20\d{2})", value or "")
        if match and int(match.group(1)) >= 2016:
            return int(match.group(1))
    return None


def _rank_with_temporal_diversity(
    assessments: Iterable[MaterialAssessment], limit: int
) -> list[MaterialAssessment]:
    """Keep quality dominant while softly rewarding an uncovered year."""
    remaining = list(assessments)
    ranked: list[MaterialAssessment] = []
    year_counts: dict[int, int] = {}
    while remaining and len(ranked) < limit:
        def adjusted_score(item: MaterialAssessment) -> tuple[float, float]:
            year = _temporal_year(item)
            coverage_bonus = 0.03 if year is not None and year not in year_counts else 0
            concentration_penalty = 0.01 * year_counts.get(year, 0) if year else 0
            return (
                item.selection_score + coverage_bonus - concentration_penalty,
                item.selection_score,
            )

        choice = max(
            remaining,
            key=adjusted_score,
        )
        remaining.remove(choice)
        ranked.append(choice)
        year = _temporal_year(choice)
        if year is not None:
            year_counts[year] = year_counts.get(year, 0) + 1
    return ranked


def select_materials(
    candidates: list[dict[str, Any]],
    assessments: list[MaterialAssessment],
    *,
    max_selected: int = 15,
    minimum_primary_target: int = 5,
    max_supplements: int = 5,
) -> list[dict[str, Any]]:
    """Select transcript-like sources first, then fill a small qualitative gap."""
    by_id = {item["candidate_id"]: item for item in candidates}
    ranked_primary = _rank_with_temporal_diversity(
        (item for item in assessments if _is_primary(item)), max_selected
    )
    ranked_supplements = _rank_with_temporal_diversity(
        (item for item in assessments if _is_supplement(item)), max_supplements
    )

    selected: list[tuple[MaterialAssessment, str]] = []
    used_events: set[str] = set()

    def add(items: Iterable[MaterialAssessment], role: str, limit: int) -> None:
        for assessment in items:
            if len([item for item in selected if item[1] == role]) >= limit:
                return
            event_key = re.sub(r"\s+", "", assessment.event_key or "")
            if event_key and event_key in used_events:
                continue
            candidate = by_id.get(assessment.candidate_id)
            if not candidate:
                continue
            selected.append((assessment, role))
            if event_key:
                used_events.add(event_key)

    add(ranked_primary, "primary", max_selected)
    if len(selected) < minimum_primary_target:
        supplement_limit = min(
            max_supplements,
            minimum_primary_target - len(selected),
            max_selected - len(selected),
        )
        add(ranked_supplements, "supplement", supplement_limit)

    output = []
    for assessment, role in selected[:max_selected]:
        candidate = dict(by_id[assessment.candidate_id])
        candidate["material_role"] = role
        candidate["temporal_year"] = _temporal_year(assessment)
        candidate["assessment"] = assessment.model_dump(mode="json") | {
            "selection_score": assessment.selection_score
        }
        output.append(candidate)
    return output


class MaterialScreeningService:
    """LLM-guided screening with deterministic selection and a conservative fallback."""

    def __init__(
        self,
        model: ModelGateway,
        skills: SkillLoader,
        *,
        batch_max_documents: int = 5,
        batch_max_chars: int = 240_000,
        concurrency: int = 3,
    ) -> None:
        self.model = model
        self.skills = skills
        self.batch_max_documents = batch_max_documents
        self.batch_max_chars = batch_max_chars
        self.semaphore = asyncio.Semaphore(concurrency)

    async def screen(
        self,
        search_result: dict[str, Any],
        *,
        manager_name: str,
        company_short_name: str,
    ) -> dict[str, Any]:
        candidates = []
        assessments: list[MaterialAssessment] = []
        for index, source in enumerate(search_result.get("results", []), start=1):
            candidate = dict(source)
            candidate_id = f"candidate_{index:03d}"
            candidate["candidate_id"] = candidate_id
            candidates.append(candidate)
            content = str(candidate.get("raw_content") or "").strip()
            if not content:
                assessments.append(
                    MaterialAssessment(
                        candidate_id=candidate_id,
                        document_type="navigation",
                        identity_match=0,
                        rawness_score=0,
                        attribution_confidence=0,
                        research_value=0,
                        processing_signals=["正文抽取失败或正文为空"],
                        identity_warnings=["未获得可供AI阅读的正文"],
                    )
                )

        nonempty = [
            item for item in candidates if str(item.get("raw_content") or "").strip()
        ]
        representatives: list[tuple[str, set[int]]] = []
        representative_ids: set[str] = set()
        for candidate in sorted(
            nonempty,
            key=lambda item: len(str(item.get("raw_content") or "")),
            reverse=True,
        ):
            shingles = _content_shingles(str(candidate.get("raw_content") or ""))
            duplicate_of = next(
                (
                    candidate_id
                    for candidate_id, existing in representatives
                    if _near_duplicate(shingles, existing)
                ),
                None,
            )
            if duplicate_of:
                assessments.append(
                    MaterialAssessment(
                        candidate_id=candidate["candidate_id"],
                        document_type="navigation",
                        identity_match=0,
                        rawness_score=0,
                        attribution_confidence=0,
                        research_value=0,
                        processing_signals=[
                            f"与更完整候选 {duplicate_of} 的正文重复度过高"
                        ],
                    )
                )
                continue
            representatives.append((candidate["candidate_id"], shingles))
            representative_ids.add(candidate["candidate_id"])

        readable = [
            item
            for item in candidates
            if item["candidate_id"] in representative_ids
        ]
        method = "llm"
        if readable:
            batches = self._batches(readable)
            results = await asyncio.gather(
                *(
                    self._screen_batch_or_fallback(
                        batch_index, batch, manager_name, company_short_name
                    )
                    for batch_index, batch in enumerate(batches, start=1)
                )
            )
            assessments.extend(item for batch, _error in results for item in batch)
            errors = [error for _batch, error in results if error]
            if errors:
                method = f"llm_with_degraded_batches:{len(errors)}"

        assessed_ids = {item.candidate_id for item in assessments}
        for item in readable:
            if item["candidate_id"] not in assessed_ids:
                assessments.append(self._heuristic(item, manager_name, company_short_name))

        selected = select_materials(candidates, assessments)
        year_distribution: dict[str, int] = {}
        for item in selected:
            year = str(item.get("temporal_year") or "unknown")
            year_distribution[year] = year_distribution.get(year, 0) + 1
        output = dict(search_result)
        output["results"] = candidates
        output["selected_results"] = selected
        output["screening"] = {
            "skill": "material_screening",
            "method": method,
            "candidate_count": len(candidates),
            "readable_count": len(readable),
            "primary_count": sum(item.get("material_role") == "primary" for item in selected),
            "supplement_count": sum(
                item.get("material_role") == "supplement" for item in selected
            ),
            "selected_count": len(selected),
            "max_selected": 15,
            "minimum_primary_target": 5,
            "max_supplements": 5,
            "temporal_coverage": {
                "year_distribution": dict(sorted(year_distribution.items())),
                "dated_count": len(selected) - year_distribution.get("unknown", 0),
                "undated_count": year_distribution.get("unknown", 0),
            },
            "assessments": [item.model_dump(mode="json") for item in assessments],
        }
        return output

    async def _screen_batch_or_fallback(
        self,
        batch_index: int,
        batch: list[dict[str, Any]],
        manager_name: str,
        company_short_name: str,
    ) -> tuple[list[MaterialAssessment], str | None]:
        try:
            return (
                await self._screen_batch(
                    batch_index, batch, manager_name, company_short_name
                ),
                None,
            )
        except Exception as exc:
            return (
                [
                    self._heuristic(item, manager_name, company_short_name)
                    for item in batch
                ],
                type(exc).__name__,
            )

    def _batches(self, candidates: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        batches: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        current_chars = 0
        for candidate in candidates:
            size = len(str(candidate.get("raw_content") or ""))
            if current and (
                len(current) >= self.batch_max_documents
                or current_chars + size > self.batch_max_chars
            ):
                batches.append(current)
                current = []
                current_chars = 0
            current.append(candidate)
            current_chars += size
        if current:
            batches.append(current)
        return batches

    async def _screen_batch(
        self,
        batch_index: int,
        batch: list[dict[str, Any]],
        manager_name: str,
        company_short_name: str,
    ) -> list[MaterialAssessment]:
        skill = self.skills.load("material_screening")
        documents = []
        for item in batch:
            documents.append(
                {
                    "candidate_id": item["candidate_id"],
                    "title": item.get("title"),
                    "url": item.get("url"),
                    "search_snippet": item.get("content"),
                    "search_published_date": item.get("published_date"),
                    "provenance_hint": item.get("source_grade"),
                    "external_untrusted_content": item.get("raw_content"),
                }
            )
        async with self.semaphore:
            raw = await self.model.structured(
                "researcher",
                [
                    {
                        "role": "system",
                        "content": skill["instructions"],
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "target_manager": manager_name,
                                "target_company": company_short_name,
                                "documents": documents,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                operation=f"material_screening_batch_{batch_index}",
            )
        parsed = MaterialAssessmentBatch.model_validate(raw)
        allowed_ids = {item["candidate_id"] for item in batch}
        return [item for item in parsed.assessments if item.candidate_id in allowed_ids]

    @staticmethod
    def _heuristic(
        candidate: dict[str, Any], manager_name: str, company_short_name: str
    ) -> MaterialAssessment:
        text = str(candidate.get("raw_content") or "")
        lines = text.splitlines()
        questions = sum(
            bool(re.match(r"\s*(?:Q\s*\d*|问|提问|主持人)[：: ]", line, re.I))
            for line in lines
        )
        transcript_markers = sum(
            marker in text
            for marker in ("文字实录", "采访实录", "路演纪要", "交流纪要", "电话会议")
        )
        editorial_markers = sum(
            marker in text
            for marker in ("我们认为", "笔者认为", "人物画像", "基金经理简介", "产品介绍")
        )
        identity = 0.95 if manager_name and manager_name in text else 0.25
        if company_short_name and company_short_name in text:
            identity = min(1.0, identity + 0.05)
        research_value = min(0.9, 0.45 + len(text) / 30_000)
        if questions >= 3:
            document_type = "qa_interview"
            rawness = 0.85
        elif transcript_markers:
            document_type = "meeting_minutes"
            rawness = 0.75
        elif editorial_markers:
            document_type = "third_party_analysis"
            rawness = 0.30
            research_value = max(research_value, 0.78)
        else:
            document_type = "edited_interview"
            rawness = 0.45
        return MaterialAssessment(
            candidate_id=candidate["candidate_id"],
            document_type=document_type,
            identity_match=identity,
            rawness_score=rawness,
            attribution_confidence=0.50,
            research_value=research_value,
            published_date=str(candidate.get("published_date") or "") or None,
            date_source=(
                "search_result" if candidate.get("published_date") else "unknown"
            ),
            date_confidence=0.5 if candidate.get("published_date") else 0,
            transcript_signals=[f"显式问题轮次={questions}"] if questions else [],
            processing_signals=[f"编辑性标记={editorial_markers}"] if editorial_markers else [],
            identity_warnings=[] if identity >= 0.8 else ["目标经理身份匹配不足"],
            summary="模型筛选不可用时的保守启发式判断",
        )
