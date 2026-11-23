from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator

FactType = Literal["source_fact", "deterministic_calculation", "model_summary", "inference"]


class ChainNode(BaseModel):
    id: str
    name: str
    position: Literal["upstream", "midstream", "downstream", "supporting"]
    description: str
    keywords: list[str] = Field(default_factory=list)
    candidate_companies: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    fact_type: FactType = "inference"


class ChainEdge(BaseModel):
    source: str
    target: str
    kind: str = "supply"
    label: str
    evidence_ids: list[str] = Field(default_factory=list)
    fact_type: FactType = "inference"


class ChainSpec(BaseModel):
    title: str
    scope: str
    exclusions: list[str] = Field(default_factory=list)
    nodes: list[ChainNode] = Field(min_length=2, max_length=12)
    edges: list[ChainEdge]
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_graph(self):
        ids = {n.id for n in self.nodes}
        if len(ids) != len(self.nodes):
            raise ValueError("产业环节ID重复")
        if any(
            e.source not in ids or e.target not in ids or e.source == e.target for e in self.edges
        ):
            raise ValueError("产业关系指向不存在或相同的环节")
        return self


class ResearchClaim(BaseModel):
    text: str
    evidence_ids: list[str] = Field(default_factory=list)
    fact_type: FactType = "model_summary"
    counter_evidence: str = ""


class ScoreItem(BaseModel):
    dimension: Literal[
        "景气度", "价值池厚度", "卡位强度", "国产替代弹性", "估值性价比", "拥挤度反向"
    ]
    score: float | None = Field(default=None, ge=0, le=10)
    note: str
    evidence_ids: list[str] = Field(default_factory=list)
    counter_factor: str = ""


class CompanyAssessment(BaseModel):
    symbol: str
    name: str
    relevance: Literal["direct", "indirect", "unconfirmed"]
    exposure_note: str
    evidence_ids: list[str] = Field(default_factory=list)
    thesis: str = ""
    risks: list[str] = Field(default_factory=list)


class CandidateDecision(BaseModel):
    node_id: str
    symbol: str
    relevance: Literal["direct", "indirect", "unconfirmed", "excluded"]
    reason: str
    source_id: str = ""
    support_quote: str = ""


class CandidateReview(BaseModel):
    decisions: list[CandidateDecision]


class DiscoveredCompany(BaseModel):
    node_id: str
    name: str
    evidence_id: str
    support_quote: str


class CompanyDiscovery(BaseModel):
    companies: list[DiscoveredCompany] = Field(default_factory=list, max_length=36)


class ResearchPriority(BaseModel):
    priority: Literal["高", "中", "低", "待验证"]
    target: str
    rationale: str
    verification: str
    invalidation: str
    evidence_ids: list[str] = Field(default_factory=list)


class SegmentResearch(BaseModel):
    segment_id: str
    segment: str
    body_md: str
    findings: list[ResearchClaim]
    companies: list[CompanyAssessment] = Field(default_factory=list)
    scores: list[ScoreItem] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    watch_items: list[str] = Field(default_factory=list)


class RiskReview(BaseModel):
    risks: list[ResearchClaim]
    source_conflicts: list[str] = Field(default_factory=list)
    required_fixes: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


class Synthesis(BaseModel):
    title: str
    headline: str
    body_md: str
    access_advice: list[str]
    watch_items: list[str]
    value_pool_items: list[ResearchClaim]
    research_priorities: list[ResearchPriority] = Field(default_factory=list, max_length=5)
    gaps: list[str] = Field(default_factory=list)


class ResearchInput(BaseModel):
    anchor: str = Field(
        min_length=1, max_length=240, description="用户确认的产业链、主题或公司产业链"
    )
    as_of: date | None = None


class JobLookup(BaseModel):
    job_id: str | None = Field(default=None, description="省略时使用本会话最近的产业链任务")


class ResultInput(JobLookup):
    query: str | None = Field(
        default=None, max_length=200, description="按关键词读取对应报告段落和证据"
    )
    offset: int = Field(default=0, ge=0, description="全文模式使用上次返回的 next_offset")


class JobRequest(ResearchInput):
    session_id: str | None = None
    idempotency_key: str | None = Field(default=None, max_length=240)
