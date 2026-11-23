from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

from fund_research_agent.adapters.knowledge import FederatedKnowledgeAdapter
from fund_research_agent.adapters.lake import ReadOnlyLakeAdapter
from fund_research_agent.adapters.mcp import LakeMCPClient
from fund_research_agent.adapters.web import ManagerMaterialSearch
from fund_research_agent.domain.contracts import ReportRequest
from fund_research_agent.memory.service import MemoryService
from fund_research_agent.persistence.artifacts import ArtifactStore
from fund_research_agent.retrieval.material_screening import MaterialScreeningService
from fund_research_agent.tools.registry import ToolRegistry, ToolSpec
from fund_research_agent.workflows.report.service import ReportJobService


class FundQuery(BaseModel):
    query: str
    as_of: date | None = None


class CoverageQuery(BaseModel):
    fund_symbol: str
    manager_tenure_start: date | None = None
    as_of: date | None = None


class ResearchSnapshotQuery(BaseModel):
    fund_symbol: str
    as_of: date | None = None


class MetricQuery(BaseModel):
    fund_symbol: str
    metric: Literal["annual_return", "latest_nav", "latest_scale", "latest_turnover"]
    as_of: date | None = None
    year: int | None = Field(default=None, ge=1990, le=2100)


class DatasetQuery(BaseModel):
    dataset: str
    fund_symbol: str | None = None
    as_of: date | None = None
    limit: int = Field(default=200, ge=1, le=1000)


class SelectQuery(BaseModel):
    sql: str
    limit: int = Field(default=500, ge=1, le=1000)


class BarsQuery(BaseModel):
    dataset: Literal["daily_bars", "index_bars", "minute_bars", "minute_bars_5m"] = "daily_bars"
    symbols: list[str] = Field(default_factory=list)
    start: date | None = None
    end: date | None = None
    adjust: Literal["qfq", "hfq"] | None = None
    universe: Literal["all_a"] | None = None
    limit: int = Field(default=200, ge=1, le=2000)


class FundamentalsQuery(BaseModel):
    as_of: date
    symbols: list[str] = Field(default_factory=list)
    items: list[str] = Field(default_factory=list)
    all_vintages: bool = False
    limit: int = Field(default=200, ge=1, le=2000)


class KnowledgeQuery(BaseModel):
    query: str
    limit: int = Field(default=10, ge=1, le=30)
    fund_symbol: str | None = None
    document_id: str | None = None
    parts: list[str] = Field(default_factory=list)


class MaterialQuery(BaseModel):
    company_full_name: str
    manager_name: str
    mode: Literal["auto", "live", "replay", "off"] = "auto"
    start_year: int = Field(default=2016, ge=2016, le=2100)
    end_year: int | None = Field(default=None, ge=2016, le=2100)


class ReportGenerateInput(ReportRequest):
    pass


class JobIdInput(BaseModel):
    job_id: str


class ArtifactIdInput(BaseModel):
    artifact_id: str


class RememberInput(BaseModel):
    kind: Literal["preference", "convention", "project_fact", "episodic_summary"]
    content: str
    scope: str = "global"


class ForgetInput(BaseModel):
    memory_id: str


class EmptyInput(BaseModel):
    pass


class TaskStatusInput(BaseModel):
    run_id: str


class ScheduleInput(BaseModel):
    name: str
    task_type: Literal[
        "check_quarterly_reports",
        "refresh_public_materials",
        "generate_research_draft",
        "run_evaluation",
    ]
    cron_expression: str
    payload: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = False


class ScheduleIdInput(BaseModel):
    schedule_id: str


def build_registry(
    lake: ReadOnlyLakeAdapter,
    mcp: LakeMCPClient,
    knowledge: FederatedKnowledgeAdapter,
    materials: ManagerMaterialSearch,
    material_screening: MaterialScreeningService,
    reports: ReportJobService,
    memory: MemoryService,
    artifacts: ArtifactStore,
) -> ToolRegistry:
    registry = ToolRegistry()

    async def resolve_fund(value: FundQuery):
        return [item.model_dump(mode="json") for item in lake.resolve_fund(value.query, value.as_of)]

    async def coverage(value: CoverageQuery):
        return lake.coverage(**value.model_dump())

    async def metric(value: MetricQuery):
        return lake.metric(**value.model_dump())

    async def research_snapshot(value: ResearchSnapshotQuery):
        return lake.research_snapshot(**value.model_dump())

    async def dataset(value: DatasetQuery):
        fund_datasets = {
            "fund_nav",
            "fund_holdings",
            "fund_reports",
            "fund_scale",
            "fund_annual_turnover",
            "fund_profiles",
            "fund_manager_tenures",
        }
        if value.dataset in fund_datasets:
            return lake.query_dataset(**value.model_dump())
        arguments: dict[str, Any] = {"dataset": value.dataset, "limit": value.limit}
        if value.fund_symbol:
            arguments["symbols"] = [value.fund_symbol]
        if value.as_of:
            arguments["end"] = value.as_of.isoformat()
        return await mcp.call("query_dataset", arguments)

    async def describe(_: EmptyInput):
        return await mcp.call("describe_lake", {})

    async def bars(value: BarsQuery):
        return await mcp.call("query_bars", value.model_dump(mode="json", exclude_none=True))

    async def fundamentals(value: FundamentalsQuery):
        return await mcp.call(
            "query_fundamentals", value.model_dump(mode="json", exclude_none=True)
        )

    async def select(value: SelectQuery):
        return await mcp.call("run_sql", value.model_dump())

    async def kb_search(value: KnowledgeQuery):
        return await knowledge.search(**value.model_dump())

    async def web_search(value: MaterialQuery):
        discovered = await materials.search(**value.model_dump())
        return await material_screening.screen(
            discovered,
            manager_name=value.manager_name,
            company_short_name=materials.company_resolver.resolve(value.company_full_name),
        )

    async def report_generate(value: ReportGenerateInput):
        return (await reports.create(ReportRequest(**value.model_dump()))).model_dump(mode="json")

    async def report_status(value: JobIdInput):
        job = await reports.get(value.job_id)
        return job.model_dump(mode="json") if job else None

    async def report_result(value: JobIdInput):
        return await reports.result(value.job_id)

    async def report_cancel(value: JobIdInput):
        await reports.cancel(value.job_id)
        return {"job_id": value.job_id, "status": "cancelling"}

    async def report_resume(value: JobIdInput):
        await reports.resume(value.job_id)
        return {"job_id": value.job_id, "status": "recoverable"}

    async def artifact_read(value: ArtifactIdInput):
        record = await artifacts.get(value.artifact_id)
        if record is None:
            return None
        path = artifacts.boundary.assert_writable(record.path)
        text = path.read_text(encoding="utf-8", errors="ignore")
        return {"artifact": record.model_dump(mode="json"), "content": text[:80_000], "truncated": len(text) > 80_000}

    async def remember(value: RememberInput):
        return (await memory.remember(**value.model_dump())).model_dump(mode="json")

    async def list_memories(_: EmptyInput):
        return [item.model_dump(mode="json") for item in await memory.list()]

    async def forget(value: ForgetInput):
        return {"deleted": await memory.forget(value.memory_id)}

    async def artifact_list(value: JobIdInput):
        return [item.model_dump(mode="json") for item in await artifacts.list_for_job(value.job_id)]

    async def task_status(value: TaskStatusInput):
        async with reports.database.connection() as connection:
            rows = await (
                await connection.execute(
                    "SELECT * FROM tasks WHERE run_id=? ORDER BY created_at", (value.run_id,)
                )
            ).fetchall()
        return [dict(row) for row in rows]

    async def schedule_create(value: ScheduleInput):
        return await reports.database.create_schedule(**value.model_dump())

    async def schedule_list(_: EmptyInput):
        return await reports.database.list_schedules()

    async def schedule_cancel(value: ScheduleIdInput):
        return {"deleted": await reports.database.delete_schedule(value.schedule_id)}

    specs = [
        ToolSpec("fund.resolve", "按代码或名称解析基金、份额、公司和经理任期。", FundQuery, resolve_fund),
        ToolSpec("fund.inspect_coverage", "检查基金净值、持仓、规模、换手和季报覆盖。", CoverageQuery, coverage),
        ToolSpec("fund.metric", "计算单只基金的确定性指标，返回日期、单位和口径。", MetricQuery, metric),
        ToolSpec(
            "fund.research_snapshot",
            "返回单只基金对话研究所需的紧凑快照：近年收益、回撤、波动、规模、换手和最新持仓。",
            ResearchSnapshotQuery,
            research_snapshot,
        ),
        ToolSpec("lake.describe", "通过真实 MCP 返回数据湖覆盖、新鲜度与查询契约。", EmptyInput, describe, timeout_seconds=60),
        ToolSpec("lake.query_dataset", "查询白名单基金数据集，支持 as_of。", DatasetQuery, dataset),
        ToolSpec("lake.query_bars", "通过真实 MCP 查询复权日线或分钟线。", BarsQuery, bars, timeout_seconds=60),
        ToolSpec("lake.query_fundamentals", "通过真实 MCP 执行 PIT 财务指标查询。", FundamentalsQuery, fundamentals, timeout_seconds=60),
        ToolSpec("lake.run_select", "通过真实 MCP 执行单条 SELECT/CTE，只写新项目视图缓存。", SelectQuery, select, timeout_seconds=60),
        ToolSpec("kb.search", "联邦检索旧知识库和新项目报告。", KnowledgeQuery, kb_search),
        ToolSpec("web.search_manager_materials", "使用基金公司简称和经理名搜索路演、交流和访谈。", MaterialQuery, web_search, permission="network", timeout_seconds=90, max_retries=1),
        ToolSpec("report.generate", "启动可恢复的后台基金深度报告任务。", ReportGenerateInput, report_generate, risk_level="medium", permission="expensive"),
        ToolSpec("report.inspect_coverage", "在创建报告前检查数据覆盖。", CoverageQuery, coverage),
        ToolSpec("report.status", "查询报告任务状态。", JobIdInput, report_status),
        ToolSpec("report.result", "获取报告任务产物和质量结果。", JobIdInput, report_result),
        ToolSpec("report.cancel", "取消报告任务。", JobIdInput, report_cancel, permission="write_var"),
        ToolSpec("report.resume", "恢复失败或中断的报告任务。", JobIdInput, report_resume, permission="write_var"),
        ToolSpec("artifact.read", "读取新项目 Artifact。", ArtifactIdInput, artifact_read),
        ToolSpec("artifact.list", "列出某报告任务的产物。", JobIdInput, artifact_list),
        ToolSpec("memory.remember", "显式保存用户偏好或研究约定；不得保存实时金融事实。", RememberInput, remember, permission="write_var"),
        ToolSpec("memory.list", "列出有效记忆。", EmptyInput, list_memories),
        ToolSpec("memory.forget", "删除指定记忆。", ForgetInput, forget, permission="write_var"),
        ToolSpec("task.status", "查询 Agent Run 的任务 DAG 状态。", TaskStatusInput, task_status),
        ToolSpec("schedule.create", "创建默认禁用的本地研究调度。", ScheduleInput, schedule_create, permission="write_var"),
        ToolSpec("schedule.list", "列出本地研究调度。", EmptyInput, schedule_list),
        ToolSpec("schedule.cancel", "删除本地研究调度。", ScheduleIdInput, schedule_cancel, permission="write_var"),
    ]
    for spec in specs:
        registry.register(spec)
    return registry
