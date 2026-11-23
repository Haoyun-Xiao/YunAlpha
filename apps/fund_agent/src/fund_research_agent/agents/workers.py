from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ResearchFinding(BaseModel):
    agent: str
    summary: str
    facts: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)
    status: Literal["complete", "partial", "failed"] = "complete"


class Supervisor:
    worker_names = (
        "DataResearcher",
        "QualitativeResearcher",
        "RiskResearcher",
        "EvidenceReviewer",
    )

    @staticmethod
    def should_delegate(independent_branches: int) -> bool:
        return independent_branches >= 3

    @staticmethod
    def merge(findings: list[ResearchFinding]) -> dict:
        conflicts = []
        facts: dict[str, set[str]] = {}
        for finding in findings:
            for fact in finding.facts:
                key = fact.split("：", 1)[0]
                facts.setdefault(key, set()).add(fact)
        for key, values in facts.items():
            if len(values) > 1:
                conflicts.append({"topic": key, "values": sorted(values)})
        return {
            "findings": [item.model_dump() for item in findings],
            "conflicts": conflicts,
            "requires_evidence_review": bool(conflicts),
        }

