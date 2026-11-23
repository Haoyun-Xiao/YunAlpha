from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from fund_research_agent.adapters.company import CompanyShortNameResolver
from fund_research_agent.adapters.lake import ReadOnlyLakeAdapter
from fund_research_agent.adapters.quarterly_reports import QuarterlyReportProvider
from fund_research_agent.adapters.report_scale import ReportScaleProvider
from fund_research_agent.adapters.web import ManagerMaterialSearch, contains_prompt_injection
from fund_research_agent.config import Settings
from fund_research_agent.domain.contracts import FundIdentity, ReportStatus
from fund_research_agent.domain.report_data import PIPELINE_VERSION
from fund_research_agent.legacy_port.fund_json.lake import LakeRepository
from fund_research_agent.paths import WriteBoundary, sha256_file
from fund_research_agent.persistence.artifacts import ArtifactStore
from fund_research_agent.persistence.database import Database
from fund_research_agent.retrieval.document_index import DocumentIndex
from fund_research_agent.retrieval.material_screening import MaterialScreeningService
from fund_research_agent.workflows.handbook.workflow import (
    HandbookResult,
    HandbookWorkflow,
    validate_handbook,
)
from fund_research_agent.workflows.report.context import ReportRunContext
from fund_research_agent.workflows.report.engine import ReportEngine
from fund_research_agent.workflows.report.fund_json import generate_single_fund
from fund_research_agent.workflows.report.part_data import PartDataBuilder
from fund_research_agent.workflows.report.provenance import fingerprint, lake_snapshot

STAGE_PROGRESS = {
    "resolving": 0.04,
    "checking_coverage": 0.08,
    "preparing_json": 0.18,
    "acquiring_materials": 0.28,
    "building_handbook": 0.42,
    "reusing_handbook": 0.42,
    "validating_handbook": 0.47,
    "splitting_part_data": 0.52,
    "context_preflight": 0.56,
    "generating_parts": 0.78,
    "summarizing": 0.84,
    "auditing": 0.89,
    "rewriting": 0.93,
    "rendering": 0.96,
    "indexing": 0.99,
}


class ReportWorkflow:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        boundary: WriteBoundary,
        artifacts: ArtifactStore,
        lake: ReadOnlyLakeAdapter,
        material_search: ManagerMaterialSearch,
        handbook: HandbookWorkflow,
        report_engine: ReportEngine,
        company_resolver: CompanyShortNameResolver,
        material_screening: MaterialScreeningService,
        part_data_builder: PartDataBuilder,
    ) -> None:
        self._input_keys: dict[tuple[str, str], str] = {}
        self.settings = settings
        self.database = database
        self.boundary = boundary
        self.artifacts = artifacts
        self.lake = lake
        self.material_search = material_search
        self.handbook = handbook
        self.report_engine = report_engine
        self.company_resolver = company_resolver
        self.material_screening = material_screening
        self.part_data_builder = part_data_builder
        self.document_index = DocumentIndex(database)
        self.quarterly_reports = QuarterlyReportProvider(
            settings.var_root / "cache/fund_reports", boundary,
            frozen_root=settings.inputs_root / "quarterly_reports",
        )

    async def _stage(self, job_id: str, stage: str) -> None:
        status = ReportStatus(stage)
        await self.database.update_report_job(
            job_id, status=status, stage=stage, progress=STAGE_PROGRESS[stage]
        )
        await self.database.add_event(
            job_id, "report_stage_started", {"stage": stage, "progress": STAGE_PROGRESS[stage]}
        )

    async def _checkpoint(self, job_id: str, step: str, value: dict, context: ReportRunContext) -> None:
        path = self.boundary.write_text(
            context.checkpoints_dir / f"{step}.json",
            json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
        )
        artifact = await self.artifacts.put_json(
            "workflow_checkpoint", value, metadata={"job_id": job_id, "step": step}, job_id=job_id
        )
        key = self._input_keys.get((job_id, step), "")
        output_hashes = {path: sha256_file(Path(path)) for path in self._output_paths(value, context)}
        self.boundary.write_text(context.checkpoints_dir / f"{step}.inputs.json", json.dumps({"input_hash": key, "outputs": output_hashes}))
        await self.database.upsert_workflow_step(
            job_id,
            step,
            "completed",
            input_hash=key,
            output_artifact_id=artifact.artifact_id,
        )
        await self.database.add_event(job_id, "report_stage_finished", {"stage": step, "checkpoint": str(path)})

    @staticmethod
    def _output_paths(value, context):
        if isinstance(value, dict):
            return [p for item in value.values() for p in ReportWorkflow._output_paths(item, context)]
        if isinstance(value, list):
            return [p for item in value for p in ReportWorkflow._output_paths(item, context)]
        if isinstance(value, str) and value.startswith(str(context.root)) and Path(value).is_file():
            return [value]
        return []

    def _set_inputs(self, context, step, value):
        self._input_keys[(context.job_id, step)] = fingerprint({"version": PIPELINE_VERSION, "inputs": value})

    def _cached(self, context: ReportRunContext, step: str) -> dict | None:
        path = context.checkpoints_dir / f"{step}.json"
        if not path.exists():
            return None
        try:
            metadata = json.loads((context.checkpoints_dir / f"{step}.inputs.json").read_text(encoding="utf-8"))
            if not metadata.get("input_hash") or metadata["input_hash"] != self._input_keys.get((context.job_id, step)):
                return None
            if any(not Path(p).is_file() or sha256_file(Path(p)) != digest for p, digest in metadata.get("outputs", {}).items()):
                return None
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    async def _skipped(self, job_id: str, stage: str) -> None:
        await self.database.add_event(
            job_id, "report_stage_finished", {"stage": stage, "checkpoint_reused": True}
        )

    async def _timing_manifest(self, job_id: str) -> dict:
        events = await self.database.list_events(job_id)
        starts: dict[str, datetime] = {}
        durations: dict[str, list[float]] = {}
        first_start: datetime | None = None
        for event in events:
            created_at = datetime.fromisoformat(str(event["created_at"]))
            stage = str(event["payload"].get("stage") or "")
            if event["event_type"] == "report_stage_started" and stage:
                starts[stage] = created_at
                first_start = first_start or created_at
            elif event["event_type"] == "report_stage_finished" and stage in starts:
                durations.setdefault(stage, []).append(
                    max(0.0, (created_at - starts.pop(stage)).total_seconds())
                )
        now = datetime.now(UTC)
        return {
            "wall_seconds_since_first_stage": round(
                (now - first_start).total_seconds(), 3
            )
            if first_start
            else 0,
            "completed_stage_seconds": {
                stage: round(sum(values), 3) for stage, values in durations.items()
            },
            "attempt_counts": {
                stage: len(values) for stage, values in durations.items()
            },
            "last_activity_at": str(events[-1]["created_at"]) if events else None,
        }

    async def run(self, job_id: str) -> None:
        job = await self.database.get_report_job(job_id)
        if job is None:
            return
        context = ReportRunContext.create(self.settings.var_root, job_id, self.boundary)
        warnings: list[str] = [
            warning
            for warning in job.warnings
            if warning not in {"report_audit_issues", "report_audit_unavailable"}
        ]
        artifact_ids: list[str] = list(job.artifact_ids)
        progress_token = self.report_engine.model.set_progress_callback(
            lambda payload: self.database.add_event(
                job_id, "model_call_progress", payload
            )
        )
        session_token = self.report_engine.model.set_session_id(f"fundscope-report-{job_id}")
        try:
            self.boundary.write_text(context.root / "request.json", job.request.model_dump_json(indent=2) + "\n")
            await self._stage(job_id, "resolving")
            source_snapshot = await asyncio.to_thread(lake_snapshot, self.settings.lake_database)
            self.boundary.write_text(context.root / "source_snapshot.json", json.dumps(source_snapshot, ensure_ascii=False))
            self._set_inputs(context, "resolving", job.request.model_dump(mode="json"))
            cached = self._cached(context, "resolving")
            if cached:
                identity = FundIdentity.model_validate(cached)
                await self._skipped(job_id, "resolving")
            else:
                identities = await asyncio.to_thread(
                    self.lake.resolve_fund, job.request.fund_query, job.request.as_of
                )
                if len(identities) != 1:
                    raise ValueError(f"基金身份不唯一，候选数量={len(identities)}")
                identity = identities[0]
                identity.company_short_name = self.company_resolver.resolve(identity.company_full_name or "")
                await self._checkpoint(job_id, "resolving", identity.model_dump(mode="json"), context)
            await self._cancel_if_requested(job_id)

            await self._stage(job_id, "checking_coverage")
            self._set_inputs(context, "checking_coverage", [identity.model_dump(mode="json"), job.request.as_of, source_snapshot["fingerprint"]])
            coverage = self._cached(context, "checking_coverage")
            if coverage:
                await self._skipped(job_id, "checking_coverage")
            else:
                coverage = await asyncio.to_thread(
                    self.lake.coverage,
                    identity.fund_symbol,
                    identity.manager_tenure_start,
                    job.request.as_of,
                )
                await self._checkpoint(job_id, "checking_coverage", coverage, context)
            self.boundary.write_text(
                context.root / "coverage_manifest.json",
                json.dumps(coverage, ensure_ascii=False, indent=2) + "\n",
            )
            eligibility = coverage.get("eligibility", {})
            if not eligibility.get("eligible_for_full_report", False):
                raise ValueError(
                    "report_not_eligible:"
                    + ",".join(eligibility.get("blocking_reasons", []))
                )
            warnings.extend(eligibility.get("warnings", []))

            await self._stage(job_id, "preparing_json")
            quarterly_report_info = {
                "path": None,
                "records": int(coverage.get("reports", {}).get("rows") or 0),
                "source": "legacy_lake",
                "warning": None,
            }
            # Official quarterly coverage is independent of roadshow search mode.
            fetched_reports = await asyncio.to_thread(
                self.quarterly_reports.ensure,
                identity.fund_symbol,
                identity.fund_name,
                identity.manager_tenure_start,
                job.request.as_of,
            )
            if fetched_reports.path or not quarterly_report_info["records"]:
                quarterly_report_info = {
                    "path": str(fetched_reports.path) if fetched_reports.path else None,
                    "records": fetched_reports.records,
                    "source": fetched_reports.source,
                    "warning": fetched_reports.warning,
                }
            if fetched_reports.path:
                warnings = [item for item in warnings if item not in {"quarterly_report_missing", "quarterly_report_pending_supplement"}]
                report_payload = json.loads(Path(fetched_reports.path).read_text(encoding="utf-8"))
                periods = [row["report_period"] for row in report_payload.get("records", [])]
                coverage["reports"] = {"rows": fetched_reports.records, "status": "ready", "source": fetched_reports.source, "from": min(periods) if periods else None, "to": max(periods) if periods else None, "fetch_state": "completed"}
                coverage["eligibility"]["warnings"] = [
                    item
                    for item in coverage["eligibility"].get("warnings", [])
                    if item not in {"quarterly_report_missing", "quarterly_report_pending_supplement"}
                ]
                self.boundary.write_text(
                    context.root / "coverage_manifest.json",
                    json.dumps(coverage, ensure_ascii=False, indent=2) + "\n",
                )
                if fetched_reports.warning:
                    warnings.append(fetched_reports.warning)
            elif fetched_reports.warning and not quarterly_report_info["records"]:
                warnings = [item for item in warnings if item != "quarterly_report_pending_supplement"]
                warnings.extend(["quarterly_report_missing", fetched_reports.warning])
            if quarterly_report_info.get("path"):
                source = Path(quarterly_report_info["path"])
                copied = self.boundary.write_bytes(context.materials_dir / "quarterly_reports.json", source.read_bytes())
                quarterly_report_info["path"] = str(copied)
            scales_overlay = context.materials_dir / "scales.json"
            if ReportScaleProvider.needs_supplement(coverage) and not scales_overlay.exists():
                frozen_scales = self.settings.inputs_root / "scales" / (
                    f"{identity.fund_symbol}-{job.request.as_of}.json"
                )
                if frozen_scales.is_file():
                    self.boundary.write_bytes(scales_overlay, frozen_scales.read_bytes())
                else:
                    repo = LakeRepository(self.settings.lake_database, as_of=job.request.as_of)
                    try:
                        symbols = repo._share_symbols(identity.fund_symbol)
                    finally:
                        repo.close()
                    await asyncio.to_thread(
                        ReportScaleProvider(self.boundary).ensure,
                        symbols, job.request.as_of, scales_overlay, fetched_reports.path,
                    )
            self._set_inputs(context, "preparing_json", [source_snapshot["fingerprint"], identity.model_dump(mode="json"), job.request.as_of, sha256_file(Path(quarterly_report_info["path"])) if quarterly_report_info.get("path") else None, sha256_file(scales_overlay) if scales_overlay.exists() else None])
            json_result = self._cached(context, "preparing_json")
            report_source_matches = (
                json_result
                and json_result.get("quarterly_reports", {}).get("path")
                == quarterly_report_info.get("path")
            )
            if json_result and report_source_matches and all(
                Path(path).exists() for path in json_result.get("files", {}).values()
            ):
                await self._skipped(job_id, "preparing_json")
            else:
                json_result = await asyncio.to_thread(
                    generate_single_fund,
                    identity,
                    self.settings.lake_database,
                    context.json_dir,
                    job.request.as_of,
                    self.boundary,
                    Path(quarterly_report_info["path"])
                    if quarterly_report_info.get("path")
                    else None,
                    scales_overlay if scales_overlay.exists() else None,
                )
                json_result["quarterly_reports"] = quarterly_report_info
                await self._checkpoint(job_id, "preparing_json", json_result, context)
            warnings.extend(json_result.get("metadata", {}).get("quality_flags", []))
            effective_scale = json_result.get("metadata", {}).get("metric_coverage", {}).get("scale", {}).get("report_period")
            if effective_scale:
                coverage["scale"]["effective_to"] = effective_scale
                if effective_scale >= (coverage.get("scale", {}).get("expected_report_period") or coverage.get("holdings", {}).get("to") or ""):
                    warnings = [item for item in warnings if item != "scale_latest_period_missing"]
            if coverage.get("reports", {}).get("to") and coverage["reports"]["to"] >= (coverage.get("holdings", {}).get("to") or ""):
                warnings = [item for item in warnings if item != "reports_latest_period_missing"]
            self.boundary.write_text(context.root / "coverage_manifest.json", json.dumps(coverage, ensure_ascii=False, indent=2))
            await self._cancel_if_requested(job_id)

            await self._stage(job_id, "acquiring_materials")
            formal = self.handbook.find_formal(
                self.settings.inputs_root / "materials/handbooks",
                identity.manager_name,
            )
            if formal and job.request.as_of < datetime.now().date():
                warnings.append("historical_formal_handbook_date_unverified")
                formal = None
            local_candidates = self._local_materials(identity.manager_name)
            self._set_inputs(context, "acquiring_materials", [job.request.as_of, job.request.web_mode, {str(p): sha256_file(p) for p in local_candidates}, sha256_file(formal) if formal else None, asdict(self.report_engine.model.role("assistant"))])
            cached_materials = self._cached(context, "acquiring_materials")
            material_paths = []
            web_result = {"results": [], "mode": job.request.web_mode}
            if formal:
                web_result = {
                    "results": [],
                    "mode": "skipped_formal_handbook",
                    "formal_handbook": str(formal),
                }
            elif cached_materials:
                material_paths = [
                    Path(path) for path in cached_materials.get("local_materials", []) if Path(path).exists()
                ]
                web_result = cached_materials.get("web", web_result)
                await self._skipped(job_id, "acquiring_materials")
            else:
                material_paths = []
                for original in local_candidates:
                    text = original.read_text(encoding="utf-8-sig", errors="replace")
                    dates = re.findall(r"(?:发布时间|发布日期|交流时间)[：:\s]*(20\d{2}[-/]\d{1,2}[-/]\d{1,2})", text)
                    if dates and any(datetime.strptime(d.replace("/", "-"), "%Y-%m-%d").date() > job.request.as_of for d in dates):
                        continue
                    if not dates:
                        if job.request.as_of < datetime.now().date():
                            continue
                        text = "发布时间：未知；不得将本材料观点视为某历史日期已公开。\n" + text
                        warnings.append("material_publication_date_unknown")
                    copied = self.boundary.write_text(context.materials_dir / original.name, text)
                    material_paths.append(copied)
            if (
                not formal
                and not cached_materials
                and job.request.web_mode != "off"
                and identity.company_full_name
            ):
                try:
                    web_result = await self.material_search.search(
                        identity.company_full_name,
                        identity.manager_name,
                        mode=job.request.web_mode,
                        start_year=max(
                            2016,
                            identity.manager_tenure_start.year
                            if identity.manager_tenure_start
                            else 2016,
                        ),
                        as_of=job.request.as_of,
                        end_year=min(
                            job.request.as_of.year,
                            identity.manager_tenure_end.year
                            if identity.manager_tenure_end
                            else job.request.as_of.year,
                        ),
                    )
                    web_result = await self.material_screening.screen(
                        web_result,
                        manager_name=identity.manager_name,
                        company_short_name=identity.company_short_name or "",
                    )
                except Exception as exc:
                    warnings.append(f"web_materials_unavailable:{exc}")
                for index, item in enumerate(
                    web_result.get("selected_results", []), start=1
                ):
                    if not item.get("raw_content"):
                        continue
                    published = item.get("assessment", {}).get("published_date") or item.get("published_date")
                    if published and str(published)[:10] > job.request.as_of.isoformat():
                        continue
                    if not published and job.request.as_of < datetime.now().date():
                        warnings.append("undated_web_material_excluded")
                        continue
                    raw_content = item["raw_content"]
                    if contains_prompt_injection(raw_content):
                        warnings.append(f"prompt_injection_marker:{item.get('url')}")
                    path = self.boundary.write_text(
                        context.materials_dir / f"web_material_{index}.md",
                        f"来源URL：{item.get('url')}\n标题：{item.get('title')}\n"
                        f"发布时间：{item.get('assessment', {}).get('published_date') or '未知'}\n"
                        f"交流时间：{item.get('assessment', {}).get('event_date') or '未知'}\n"
                        f"时间来源：{item.get('assessment', {}).get('date_source') or 'unknown'}\n"
                        f"时间置信度：{item.get('assessment', {}).get('date_confidence', 0)}\n"
                        f"来源提示：{item.get('source_grade')}\n"
                        f"材料角色：{item.get('material_role')}\n"
                        f"AI筛选：{json.dumps(item.get('assessment', {}), ensure_ascii=False)}\n"
                        f"信任边界：external_untrusted\n\n{raw_content}",
                    )
                    material_paths.append(path)
            if formal or not cached_materials:
                source_manifest = {
                    "local_materials": [str(path) for path in material_paths],
                    "web": web_result,
                }
                await self._checkpoint(job_id, "acquiring_materials", source_manifest, context)

            handbook_result: HandbookResult
            existing_handbook = context.handbook_dir / "handbook.md"
            previous_input_manifest = context.root / "input_manifest.json"
            if existing_handbook.exists() and not material_paths and previous_input_manifest.exists():
                try:
                    previous_inputs = json.loads(
                        previous_input_manifest.read_text(encoding="utf-8")
                    )
                except (OSError, json.JSONDecodeError):
                    previous_inputs = {}
                material_paths = [
                    Path(path)
                    for path in previous_inputs.get("material_source_hashes", {})
                    if Path(path).exists()
                ]
            handbook_source_manifest = context.handbook_dir / "source_manifest.json"
            if existing_handbook.exists() and not material_paths and handbook_source_manifest.exists():
                try:
                    handbook_sources = json.loads(
                        handbook_source_manifest.read_text(encoding="utf-8")
                    )
                except (OSError, json.JSONDecodeError):
                    handbook_sources = {}
                material_paths = [
                    Path(item["path"])
                    for item in handbook_sources.get("inputs", [])
                    if "/inputs/materials/" in str(item.get("path", ""))
                    and Path(item["path"]).exists()
                ]
            handbook_inputs = fingerprint({"json": {kind: sha256_file(Path(path)) for kind, path in json_result["files"].items()}, "materials": {str(path): sha256_file(path) for path in material_paths}, "skill": {str(path): sha256_file(path) for path in self.handbook.skill_root.rglob("*.md")}, "formal": sha256_file(formal) if formal else None, "model": asdict(self.report_engine.model.role("handbook"))})
            handbook_key_path = context.handbook_dir / "input_fingerprint.txt"
            if existing_handbook.exists() and (not handbook_key_path.exists() or handbook_key_path.read_text() != handbook_inputs):
                self.boundary.assert_writable(existing_handbook).unlink()
            if existing_handbook.exists():
                text = existing_handbook.read_text(encoding="utf-8")
                validation = validate_handbook(text, identity.manager_name, self.settings.handbook)
                candidate_manifest = context.handbook_dir / "handbook_manifest.json"
                candidate_used = candidate_manifest.exists()
                if candidate_used and not validation["valid"]:
                    raise ValueError(f"handbook_校验失败:{validation['problems']}")
                handbook_result = HandbookResult(
                    existing_handbook,
                    validation["quality"],
                    validation["warnings"],
                    candidate_used,
                    {"checkpoint_reused": True},
                )
                await self.database.add_event(
                    job_id,
                    "report_stage_finished",
                    {"stage": "handbook", "checkpoint_reused": True},
                )
            if not existing_handbook.exists() and formal:
                await self._stage(job_id, "reusing_handbook")
                text = self.handbook.read_handbook(formal)
                copied = self.boundary.write_text(context.handbook_dir / "handbook.md", text)
                validation = validate_handbook(text, identity.manager_name, self.settings.handbook)
                if not validation["valid"]:
                    # Historical formal handbooks remain authoritative even if they predate the candidate format.
                    warnings.append("formal_handbook_legacy_contract")
                handbook_result = HandbookResult(
                    copied,
                    validation["quality"],
                    validation["warnings"],
                    False,
                    {"formal_source": str(formal), "sha256": sha256_file(formal)},
                )
            elif not existing_handbook.exists():
                await self._stage(job_id, "building_handbook")
                if not material_paths:
                    fallback = self.boundary.write_text(
                        context.materials_dir / "structured_data_fallback.md",
                        "# 非结构化资料缺口说明\n\n"
                        "未取得合格的路演、访谈、交流纪要或第三方观点。"
                        "本次候选投资手册只允许使用结构化 JSON 和已取得的季度运作报告；"
                        "不得编造基金经理原话、路演时间或第三方观点。\n",
                    )
                    material_paths.append(fallback)
                    warnings.append("structured_data_handbook_fallback")
                    await self._checkpoint(
                        job_id,
                        "acquiring_materials",
                        {
                            "local_materials": [str(path) for path in material_paths],
                            "web": web_result,
                        },
                        context,
                    )
                handbook_result = await self.handbook.build(
                    f"{identity.manager_name}_{identity.fund_name}",
                    json_result["files"],
                    material_paths,
                    context.handbook_dir,
                )
            self.boundary.write_text(handbook_key_path, handbook_inputs)
            warnings.extend(handbook_result.warnings)
            await self._stage(job_id, "validating_handbook")
            handbook_text = handbook_result.path.read_text(encoding="utf-8")
            handbook_meta = self._cached(context, "validating_handbook")
            if handbook_meta and handbook_meta.get("sha256") == sha256_file(handbook_result.path):
                await self._skipped(job_id, "validating_handbook")
            else:
                handbook_meta = {
                    "path": str(handbook_result.path),
                    "sha256": sha256_file(handbook_result.path),
                    "quality": handbook_result.quality,
                    "candidate_used": handbook_result.candidate_used,
                    "characters": len(handbook_text),
                    "protected_context": True,
                    "source_manifest": handbook_result.source_manifest,
                }
                await self._checkpoint(job_id, "validating_handbook", handbook_meta, context)

            input_manifest = {
                "fund_symbol": identity.fund_symbol,
                "manager_name": identity.manager_name,
                "manager_tenure_start": identity.manager_tenure_start,
                "manager_tenure_end": identity.manager_tenure_end,
                "as_of": job.request.as_of,
                "json_source_hashes": {
                    kind: sha256_file(Path(path)) for kind, path in json_result["files"].items()
                },
                "material_source_hashes": {
                    str(path): sha256_file(path) for path in material_paths if path.exists()
                },
                "handbook_hash": handbook_meta["sha256"],
                "handbook_skill_hash": fingerprint({str(path): sha256_file(path) for path in self.handbook.skill_root.rglob("*.md")}),
                "report_prompt_hash": sha256_file(
                    self.settings.project_root
                    / "src/fund_research_agent/legacy_port/report_engine/prompts.py"
                ),
                "generation_model_config_hash": hashlib.sha256(
                    json.dumps(
                        {
                            "report": asdict(self.report_engine.model.role("report")),
                            "handbook": asdict(self.report_engine.model.role("handbook")),
                        },
                        sort_keys=True,
                    ).encode()
                ).hexdigest(),
                "evaluator_model_config_hash": hashlib.sha256(
                    json.dumps(
                        asdict(self.report_engine.model.role("evaluator")),
                        sort_keys=True,
                    ).encode()
                ).hexdigest(),
                "output_formats": job.request.output_formats,
                "workflow_version": job.workflow_version,
                "request_fingerprint": job.input_fingerprint,
            }
            await self._stage(job_id, "splitting_part_data")
            self._set_inputs(context, "splitting_part_data", [source_snapshot["fingerprint"], job.request.as_of, {kind: sha256_file(Path(path)) for kind, path in json_result["files"].items()}, {str(p): sha256_file(p) for p in material_paths}])
            part_mapping = self._cached(context, "splitting_part_data")
            current_json_hashes = {
                kind: sha256_file(Path(path)) for kind, path in json_result["files"].items()
            }
            if part_mapping and all(
                Path(path).exists() for path in part_mapping.get("files", {}).values()
            ) and part_mapping.get("source_hashes") == current_json_hashes:
                await self._skipped(job_id, "splitting_part_data")
            else:
                part_mapping = await asyncio.to_thread(
                    self.part_data_builder.build,
                    identity,
                    json_result["files"],
                    context.part_data_dir,
                    job.request.as_of,
                    material_paths,
                )
                await self._checkpoint(job_id, "splitting_part_data", part_mapping, context)
            ending_snapshot = await asyncio.to_thread(lake_snapshot, self.settings.lake_database)
            if ending_snapshot["fingerprint"] != source_snapshot["fingerprint"]:
                raise RuntimeError("source_changed_during_preparation:resume_to_prepare_consistent_inputs")
            self.boundary.write_text(
                context.root / "part_contract_manifest.json",
                json.dumps(part_mapping, ensure_ascii=False, indent=2) + "\n",
            )

            input_manifest["part_json_hashes"] = {
                part: sha256_file(Path(path))
                for part, path in part_mapping["files"].items()
            }
            generation_manifest = {
                key: value
                for key, value in input_manifest.items()
                if key != "evaluator_model_config_hash"
            }
            input_manifest["effective_input_fingerprint"] = hashlib.sha256(
                json.dumps(
                    generation_manifest,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                ).encode()
            ).hexdigest()
            self.boundary.write_text(
                context.root / "input_manifest.json",
                json.dumps(input_manifest, ensure_ascii=False, indent=2, default=str) + "\n",
            )
            generation_key = input_manifest["effective_input_fingerprint"][:16]
            audit_key = hashlib.sha256(
                (
                    input_manifest["effective_input_fingerprint"]
                    + input_manifest["evaluator_model_config_hash"]
                ).encode()
            ).hexdigest()[:16]
            generation_parts_dir = self.boundary.ensure_dir(
                context.parts_dir / generation_key
            )
            generation_audit_dir = self.boundary.ensure_dir(
                context.audit_dir / audit_key
            )

            await self._stage(job_id, "context_preflight")
            preflight = self.report_engine.preflight_parts(
                handbook_text, part_mapping["files"]
            )
            if not preflight["fits"]:
                raise ValueError(f"context_overflow:{preflight}")
            await self._checkpoint(job_id, "context_preflight", preflight, context)

            await self._stage(job_id, "generating_parts")
            parts = await self.report_engine.generate_parts(
                f"{identity.manager_name}_{identity.fund_name}",
                handbook_text,
                part_mapping["files"],
                generation_parts_dir,
                material_paths,
            )
            await self._checkpoint(
                job_id, "generating_parts", {key: len(value) for key, value in parts.items()}, context
            )
            await self._cancel_if_requested(job_id)

            await self._stage(job_id, "summarizing")
            report_target = f"{identity.manager_name}_{identity.fund_name}"
            summary = await self.report_engine.summary(
                report_target, handbook_text, parts, generation_parts_dir
            )
            await self._checkpoint(job_id, "summarizing", {"characters": len(summary)}, context)

            await self._stage(job_id, "auditing")
            await self._stage(job_id, "rewriting")
            parts, summary, audit = await self.report_engine.audit_and_rewrite(
                report_target,
                job.request.as_of.isoformat(),
                handbook_text,
                parts,
                summary,
                generation_audit_dir,
                max_rounds=2,
                max_rewrite_targets=11,
            )
            self.boundary.write_text(
                generation_audit_dir / "audit.json",
                json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
            )
            hard_audit = self.report_engine.deterministic_audit(parts, summary)
            if not hard_audit["passed"]:
                raise ValueError(f"report_校验失败:{hard_audit}")
            if not audit["passed"]:
                warnings.append(
                    "report_audit_unavailable"
                    if set(audit.get("issues", {})) == {"evaluator"}
                    else "report_audit_issues"
                )
            await self._checkpoint(job_id, "auditing", audit, context)

            await self._stage(job_id, "rendering")
            rendered, render_validation = await self.report_engine.render(
                f"{identity.manager_name}_{identity.fund_name}",
                parts,
                summary,
                context.part_data_dir,
                Path(part_mapping["render_data_dir"]),
                context.output_dir,
                job.request.as_of,
            )
            warnings.extend(render_validation.get("warnings", []))
            artifact_by_format: dict[str, str] = {}
            for kind, path in rendered.items():
                if kind not in job.request.output_formats:
                    continue
                artifact = await self.artifacts.put_file(
                    f"report_{kind}",
                    path,
                    metadata={"job_id": job_id, "source_path": str(path), "format": kind},
                    job_id=job_id,
                )
                artifact_ids.append(artifact.artifact_id)
                artifact_by_format[kind] = artifact.artifact_id
            await self._checkpoint(job_id, "rendering", {key: str(value) for key, value in rendered.items()}, context)

            await self._stage(job_id, "indexing")
            indexed = await self.document_index.index_report(
                fund_symbol=identity.fund_symbol,
                fund_name=identity.fund_name,
                manager_name=identity.manager_name,
                as_of=job.request.as_of,
                report_path=rendered["markdown"],
                artifact_id=artifact_by_format.get("markdown"),
                job_id=job_id,
                parts={**parts, "summary": summary},
                data_metadata=json_result.get("metadata", {}),
                pipeline_version=PIPELINE_VERSION,
                manager_tenure_start=identity.manager_tenure_start,
            )
            await self.database.publish_report_to_sessions(job_id, indexed["document_id"], job.session_id, fund_symbol=identity.fund_symbol)
            await self._checkpoint(job_id, "indexing", indexed, context)
            await self.database.add_event(
                job_id,
                "report_published",
                {
                    **indexed,
                    "job_id": job_id,
                    "fund_symbol": identity.fund_symbol,
                    "fund_name": identity.fund_name,
                    "manager_name": identity.manager_name,
                    "artifacts": artifact_by_format,
                },
            )

            warnings = list(dict.fromkeys(warnings))
            timing = await self._timing_manifest(job_id)
            result_manifest = {
                "job_id": job_id,
                "pipeline_version": PIPELINE_VERSION,
                "data_metadata": json_result.get("metadata", {}),
                "fund": identity.model_dump(mode="json"),
                "coverage": coverage,
                "handbook": handbook_meta,
                "handbook_source_mode": (
                    "formal"
                    if not handbook_result.candidate_used
                    else "structured_data_fallback"
                    if "structured_data_handbook_fallback" in warnings
                    else "candidate_materials"
                ),
                "audit": audit,
                "render_validation": render_validation,
                "model": {
                    "report": self.report_engine.model.descriptor("report"),
                    "handbook": self.report_engine.model.descriptor("handbook"),
                    "evaluator": self.report_engine.model.descriptor("evaluator"),
                },
                "effective_input_fingerprint": input_manifest[
                    "effective_input_fingerprint"
                ],
                "timing": timing,
                "warnings": warnings,
                "artifacts": artifact_ids,
                "knowledge": indexed,
                "completed_at": datetime.now(UTC).isoformat(),
            }
            self.boundary.write_text(context.root / "result_manifest.json", json.dumps(result_manifest, ensure_ascii=False, indent=2, default=str) + "\n")
            final_status = ReportStatus.COMPLETED_WITH_WARNINGS if warnings or not audit["passed"] else ReportStatus.COMPLETED
            await self.database.update_report_job(
                job_id,
                status=final_status,
                stage=final_status.value,
                progress=1,
                warnings=warnings,
                errors=[],
                artifact_ids=artifact_ids,
            )
            await self.database.add_event(job_id, "done", result_manifest)
        except CancelledError:
            await self.database.update_report_job(job_id, status=ReportStatus.CANCELLED, stage="cancelled")
            await self.database.add_event(job_id, "done", {"status": "cancelled"})
        except ValueError as exc:
            status = ReportStatus.FAILED_VALIDATION if any(
                marker in str(exc) for marker in ("校验", "context_overflow", "身份", "JSON")
            ) else ReportStatus.FAILED
            await self.database.update_report_job(
                job_id, status=status, stage=status.value, errors=[str(exc)], warnings=warnings
            )
            await self.database.add_event(job_id, "done", {"status": status.value, "error": str(exc)})
        except Exception as exc:
            await self.database.update_report_job(
                job_id, status=ReportStatus.RECOVERABLE, stage="recoverable", errors=[f"{type(exc).__name__}: {exc}"], warnings=warnings
            )
            await self.database.add_event(
                job_id, "report_recoverable", {"error": f"{type(exc).__name__}: {exc}"}
            )
        finally:
            for key in [key for key in self._input_keys if key[0] == job_id]:
                self._input_keys.pop(key, None)
            self.report_engine.model.reset_session_id(session_token)
            self.report_engine.model.reset_progress_callback(progress_token)

    def _local_materials(self, manager: str) -> list[Path]:
        root = self.settings.inputs_root / "materials/roadshows"
        return sorted(
            path.resolve()
            for path in root.iterdir()
            if path.is_file() and manager in path.name and path.suffix.lower() in {".txt", ".md"}
        ) if root.exists() else []

    async def _cancel_if_requested(self, job_id: str) -> None:
        if await self.database.cancel_requested(job_id):
            raise CancelledError

class CancelledError(RuntimeError):
    pass
