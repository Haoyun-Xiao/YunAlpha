from __future__ import annotations

import hashlib
import json
import re

from fund_research_agent.domain.contracts import ReportAccepted, ReportJob, ReportRequest
from fund_research_agent.domain.report_data import PIPELINE_VERSION
from fund_research_agent.persistence.artifacts import ArtifactStore
from fund_research_agent.persistence.database import Database

WORKFLOW_VERSION = PIPELINE_VERSION


class ReportJobService:
    def __init__(self, database: Database, artifacts: ArtifactStore) -> None:
        self.database = database
        self.artifacts = artifacts

    @staticmethod
    def normalized_fund_query(value: str) -> str:
        match = re.search(
            r"(?<!\d)(\d{6}\.(?:OF|SH|SZ))(?!\w)", value, re.IGNORECASE
        )
        return match.group(1).upper() if match else value.upper().strip()

    @staticmethod
    def fingerprint(request: ReportRequest) -> str:
        if request.idempotency_key:
            seed = WORKFLOW_VERSION + ":" + request.idempotency_key
        else:
            seed = json.dumps(
                {
                    "fund_query": ReportJobService.normalized_fund_query(
                        request.fund_query
                    ),
                    "as_of": request.as_of.isoformat(),
                    "web_mode": request.web_mode,
                    "formats": sorted(request.output_formats),
                    "workflow_version": WORKFLOW_VERSION,
                },
                sort_keys=True,
                ensure_ascii=False,
            )
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()

    async def create(self, request: ReportRequest) -> ReportAccepted:
        job_id, _created = await self.database.create_report_job(
            request,
            self.fingerprint(request),
            WORKFLOW_VERSION,
        )
        job = await self.database.get_report_job(job_id)
        assert job is not None
        normalized_query = self.normalized_fund_query(request.fund_query)
        fund_symbol = (
            normalized_query
            if re.fullmatch(r"\d{6}\.(?:OF|SH|SZ)", normalized_query)
            else None
        )
        return ReportAccepted(
            job_id=job_id, trace_id=job.trace_id, fund_symbol=fund_symbol
        )

    async def get(self, job_id: str) -> ReportJob | None:
        return await self.database.get_report_job(job_id)

    async def result(self, job_id: str) -> dict:
        job = await self.get(job_id)
        if job is None:
            return {"job_id": job_id, "status": "not_found", "artifacts": []}
        artifacts = await self.artifacts.list_for_job(job_id)
        return {
            "job": job.model_dump(mode="json"),
            "artifacts": [artifact.model_dump(mode="json") for artifact in artifacts],
        }

    async def cancel(self, job_id: str) -> None:
        await self.database.request_cancel(job_id)

    async def resume(self, job_id: str) -> None:
        await self.database.resume_report_job(job_id)
