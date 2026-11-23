from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class RouteMode(StrEnum):
    FAST_PATH = "FAST_PATH"
    AGENT_PATH = "AGENT_PATH"
    REPORT_WORKFLOW = "REPORT_WORKFLOW"
    CLARIFY = "CLARIFY"


class ToolStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    ERROR = "error"


class ReportStatus(StrEnum):
    QUEUED = "queued"
    RESOLVING = "resolving"
    CHECKING_COVERAGE = "checking_coverage"
    PREPARING_JSON = "preparing_json"
    ACQUIRING_MATERIALS = "acquiring_materials"
    BUILDING_HANDBOOK = "building_handbook"
    REUSING_HANDBOOK = "reusing_handbook"
    VALIDATING_HANDBOOK = "validating_handbook"
    SPLITTING_PART_DATA = "splitting_part_data"
    CONTEXT_PREFLIGHT = "context_preflight"
    GENERATING_PARTS = "generating_parts"
    SUMMARIZING = "summarizing"
    AUDITING = "auditing"
    REWRITING = "rewriting"
    RENDERING = "rendering"
    INDEXING = "indexing"
    RECOVERABLE = "recoverable"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    FAILED_VALIDATION = "failed_validation"
    FAILED = "failed"


TERMINAL_REPORT_STATUSES = {
    ReportStatus.CANCELLED,
    ReportStatus.COMPLETED,
    ReportStatus.COMPLETED_WITH_WARNINGS,
    ReportStatus.FAILED_VALIDATION,
    ReportStatus.FAILED,
}


class FundIdentity(BaseModel):
    fund_symbol: str
    fund_name: str
    share_class: str | None = None
    manager_name: str
    manager_tenure_start: date | None = None
    manager_tenure_end: date | None = None
    company_full_name: str | None = None
    company_short_name: str | None = None
    identity_confidence: float = Field(default=1.0, ge=0, le=1)
    identity_warnings: list[str] = Field(default_factory=list)


class RouteDecision(BaseModel):
    mode: RouteMode
    intent: str
    funds: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0, le=1)
    needs_fresh_data: bool = False
    needs_web: bool = False
    required_tools: list[str] = Field(default_factory=list)
    risk_level: Literal["low", "medium", "high"] = "low"
    success_criteria: list[str] = Field(default_factory=list)
    clarification_question: str | None = None


class ToolResultEnvelope(BaseModel):
    status: ToolStatus
    data: Any = None
    artifact_ref: str | None = None
    source: list[str] = Field(default_factory=list)
    origin: str | None = None
    data_as_of: date | None = None
    fetched_at: datetime | None = None
    truncated: bool = False
    quality_flags: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
    duration_ms: int = 0


class EvidenceRecord(BaseModel):
    evidence_id: str
    source_type: str
    source_uri: str | None = None
    source_hash: str | None = None
    data_as_of: date | None = None
    excerpt: str
    fact_type: Literal[
        "source_fact", "deterministic_calculation", "model_summary", "inference"
    ]
    confidence: float = Field(default=1.0, ge=0, le=1)
    fund_symbol: str | None = None
    manager_name: str | None = None


class ClaimRecord(BaseModel):
    claim_id: str
    claim_text: str
    evidence_ids: list[str] = Field(default_factory=list)
    support_status: Literal["supported", "partial", "unsupported", "unchecked"] = "unchecked"
    confidence: float = Field(default=0.5, ge=0, le=1)
    created_by: str


class ReportRequest(BaseModel):
    fund_query: str
    as_of: date
    web_mode: Literal["auto", "live", "replay", "off"] = "auto"
    output_formats: list[Literal["markdown", "html", "docx"]] = Field(
        default_factory=lambda: ["markdown", "html", "docx"]
    )
    force_refresh: bool = False
    idempotency_key: str | None = None


class ReportAccepted(BaseModel):
    status: Literal["accepted"] = "accepted"
    job_id: str
    fund_symbol: str | None = None
    trace_id: str
    poll_after_ms: int = 1500


class ReportJob(BaseModel):
    job_id: str
    trace_id: str
    request: ReportRequest
    status: ReportStatus
    current_stage: str
    progress: float = Field(default=0, ge=0, le=1)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    input_fingerprint: str
    workflow_version: str
    attempt: int = 0
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    artifact_ids: list[str] = Field(default_factory=list)
    session_id: str | None = None
    source_run_id: str | None = None


class MemoryRecord(BaseModel):
    memory_id: str
    user_id: str = "local"
    scope: str = "global"
    kind: Literal["preference", "convention", "project_fact", "episodic_summary"]
    content: str
    source_message_id: str | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)
    confirmed: bool = True
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None


class ArtifactRecord(BaseModel):
    artifact_id: str
    kind: str
    path: str
    sha256: str
    size_bytes: int
    mime_type: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class TraceEvent(BaseModel):
    event_id: int | None = None
    run_id: str
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
