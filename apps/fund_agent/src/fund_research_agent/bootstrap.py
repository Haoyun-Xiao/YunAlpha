from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from fund_research_agent.adapters.company import CompanyShortNameResolver
from fund_research_agent.adapters.fund_universe import FundUniverseAdapter
from fund_research_agent.adapters.knowledge import FederatedKnowledgeAdapter
from fund_research_agent.adapters.lake import ReadOnlyLakeAdapter
from fund_research_agent.adapters.market_proxy import ProxyMCP
from fund_research_agent.adapters.mcp import LakeMCPClient
from fund_research_agent.adapters.model import ModelGateway
from fund_research_agent.adapters.web import ManagerMaterialSearch, configured_tavily_keys
from fund_research_agent.config import Settings, load_settings
from fund_research_agent.evidence.ledger import EvidenceLedger
from fund_research_agent.memory.service import MemoryService
from fund_research_agent.paths import WriteBoundary
from fund_research_agent.persistence.artifacts import ArtifactStore
from fund_research_agent.persistence.database import Database
from fund_research_agent.retrieval.material_screening import MaterialScreeningService
from fund_research_agent.runtime.context import ContextManager
from fund_research_agent.runtime_deep import DeepAgentRuntime
from fund_research_agent.skills import SkillLoader
from fund_research_agent.tools.builtin import build_registry
from fund_research_agent.tools.gateway import ToolGateway, ToolPolicy
from fund_research_agent.tools.industry import register_industry_tools
from fund_research_agent.tools.market import DS_MODELS, register_market_tools
from fund_research_agent.tools.registry import ToolRegistry
from fund_research_agent.workflows.handbook.workflow import HandbookWorkflow
from fund_research_agent.workflows.report.engine import ReportEngine
from fund_research_agent.workflows.report.part_data import PartDataBuilder
from fund_research_agent.workflows.report.service import ReportJobService
from fund_research_agent.workflows.report.workflow import ReportWorkflow
from industry_agent.config import Settings as IndustrySettings
from industry_agent.service import IndustryService


@dataclass(frozen=True)
class Container:
    settings: Settings
    boundary: WriteBoundary
    database: Database
    artifacts: ArtifactStore
    lake: ReadOnlyLakeAdapter
    mcp: LakeMCPClient
    knowledge: FederatedKnowledgeAdapter
    model: ModelGateway
    materials: ManagerMaterialSearch
    material_screening: MaterialScreeningService
    memory: MemoryService
    evidence: EvidenceLedger
    reports: ReportJobService
    registry: ToolRegistry
    gateway: ToolGateway
    runtime: DeepAgentRuntime
    report_workflow: ReportWorkflow
    industry: IndustryService


def build_container(settings: Settings | None = None) -> Container:
    settings = settings or load_settings()
    boundary = WriteBoundary(
        settings.project_root, settings.legacy_root, settings.var_root
    )
    database = Database(settings.state_db, boundary)
    artifacts = ArtifactStore(settings.var_root / "artifacts", boundary, database)
    model = ModelGateway(
        settings.project_root / "config/model_roles.yaml",
        settings.var_root / "replays/model",
        boundary,
        settings.var_root / "logs/model_calls.jsonl",
    )
    skill_loader = SkillLoader(settings.project_root / "src/fund_research_agent/skills")
    material_screening = MaterialScreeningService(model, skill_loader)
    company = CompanyShortNameResolver(settings.project_root / "config/company_aliases.yaml")
    bridge_database = settings.lake_database
    fund_universe = FundUniverseAdapter(settings.fund_universe)
    lake = ReadOnlyLakeAdapter(bridge_database, settings.fund_manifest, fund_universe)
    mcp = LakeMCPClient(
        Path(sys.executable).parent / "mal",
        settings.var_root / "lake_bridge/mcp.toml",
        settings.var_root / "cache/mcp",
        boundary,
    )
    knowledge = FederatedKnowledgeAdapter(settings.legacy_kb, database)
    materials = ManagerMaterialSearch(
        settings.var_root / "replays/tavily",
        boundary,
        company,
        frozen_replay_root=settings.inputs_root / "replays/tavily",
    )
    memory = MemoryService(database, settings.user_id)
    evidence = EvidenceLedger(database)
    reports = ReportJobService(database, artifacts)
    registry = build_registry(
        lake,
        mcp,
        knowledge,
        materials,
        material_screening,
        reports,
        memory,
        artifacts,
    )
    industry = IndustryService(
        IndustrySettings(
            var=boundary.assert_writable(settings.var_root / "industry"),
            lake_root=settings.legacy_root,
            skill_root=settings.project_root / "src/fund_research_agent/deep_skills",
            gateway=model,
            tavily_keys=tuple(configured_tavily_keys()),
            web_mode=settings.web.mode,
            search_depth=settings.web.search_depth,
        ),
        settings.base_path,
        settings.timezone,
    )
    register_industry_tools(registry, industry)
    lake_config = Path(os.getenv(
        "YUNALPHA_LAKE_CONFIG",
        str(settings.legacy_root.parent / "configs/multi-asset-lake.toml"),
    ))
    market_proxy = ProxyMCP(lake_config) if model.role("assistant").model in DS_MODELS else None
    register_market_tools(registry, settings.legacy_root, market_proxy)
    gateway = ToolGateway(
        registry,
        database,
        ToolPolicy(
            read_only=settings.read_only,
            trading_enabled=settings.trading_tools_enabled,
        ),
        artifacts,
    )
    context = ContextManager(
        settings.context.chat_budget_tokens,
        settings.context.compact_at_tokens,
    )
    runtime = DeepAgentRuntime(settings, database, model, registry, gateway, memory, industry=industry)
    handbook = HandbookWorkflow(
        model,
        settings.handbook,
        boundary,
        settings.project_root / "src/fund_research_agent/legacy_port/handbook_engine/skill",
    )
    report_engine = ReportEngine(
        model,
        boundary,
        context,
        settings.runtime.report_part_concurrency,
        settings.context.report_safety_margin,
    )
    part_data_builder = PartDataBuilder(bridge_database, boundary)
    report_workflow = ReportWorkflow(
        settings,
        database,
        boundary,
        artifacts,
        lake,
        materials,
        handbook,
        report_engine,
        company,
        material_screening,
        part_data_builder,
    )
    return Container(
        settings,
        boundary,
        database,
        artifacts,
        lake,
        mcp,
        knowledge,
        model,
        materials,
        material_screening,
        memory,
        evidence,
        reports,
        registry,
        gateway,
        runtime,
        report_workflow,
        industry,
    )
