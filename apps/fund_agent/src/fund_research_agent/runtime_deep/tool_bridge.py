from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from fund_research_agent.domain.contracts import ToolResultEnvelope, ToolStatus
from fund_research_agent.persistence.database import Database
from fund_research_agent.tools.gateway import ToolGateway
from fund_research_agent.tools.industry import INDUSTRY_TOOLS
from fund_research_agent.tools.market import LAKE_TOOL, PROXY_TOOL
from fund_research_agent.tools.registry import ToolRegistry
from industry_agent.contracts import JobLookup, ResearchInput, ResultInput

REPORT_ACTION = re.compile(
    r"(?:写|生成|制作|撰写|创建|出)[^。！？!?\n]*报告|"
    r"报告.{0,8}(?:写|生成|制作|撰写|创建|出)"
)
INDUSTRY_REPORT_ACTION = re.compile(
    r"(?:做|交付|给我|来)[^。！？!?\n]{0,70}报告|(?:生成|撰写|制作|创建|编制|写|出)[^。！？!?\n]*报告",
)
INDUSTRY_REPORT_NEGATION = re.compile(r"(?:不要|不用|不必|无需|不需要|先不|别)[^。！？!?，,\n]{0,24}报告")
REPORT_STATUS = re.compile(r"进度|状态|好了吗|完成了吗|写完了吗|到哪了")
MEMORY_ACTION = re.compile(r"请?记住|记下来|保存.{0,8}(?:偏好|习惯|约定)|以后.{0,12}(?:按|优先|关注)")
MEMORY_FORGET_ACTION = re.compile(r"忘掉|忘记|删除|清除|取消保存")
REPORT_CANCEL_ACTION = re.compile(r"取消|停止|终止|不要(?:再)?写")
REPORT_RESUME_ACTION = re.compile(r"继续|恢复|重试|从断点")
REPORT_REFERENCE = re.compile(r"这篇报告|刚才的报告|上面的报告|当前报告|该报告")
REPORT_FORCE_REFRESH = re.compile(
    r"(?:重新|重写|新写|再写|另写|从头(?:重新)?)(?:一份|一篇|个)?[^。！？!?\n]*报告|"
    r"报告[^\n]{0,10}(?:重新写|重新生成|重写|新写|从头生成)|"
    r"写(?:一份|一篇|个)?新(?:的)?报告"
)
REPORT_REUSE_DAYS = 90


class ActiveFundInput(BaseModel):
    fund_symbol: str | None = Field(default=None, description="基金代码；省略时使用当前会话已确认的基金")
    as_of: date | None = None


class ActiveMetricInput(ActiveFundInput):
    metric: Literal["annual_return", "latest_nav", "latest_scale", "latest_turnover"]
    year: int | None = Field(default=None, ge=1990, le=2100)


class ActiveCoverageInput(ActiveFundInput):
    manager_tenure_start: date | None = None


class KnowledgeInput(BaseModel):
    query: str
    limit: int = Field(default=10, ge=1, le=30)
    fund_symbol: str | None = None
    document_id: str | None = None
    parts: list[str] = Field(default_factory=list)


class ReportRequestInput(BaseModel):
    fund_query: str | None = Field(
        default=None,
        description="基金名称或代码；用户指代当前基金时省略",
    )
    as_of: date | None = None
    web_mode: Literal["auto", "live", "replay", "off"] = "auto"
    output_formats: list[Literal["markdown", "html", "docx"]] = Field(
        default_factory=lambda: ["markdown", "html", "docx"]
    )
    force_refresh: bool = Field(
        default=False,
        description="仅当用户明确要求重新写、新写或从头生成时设为 true",
    )


@dataclass
class BridgeRunContext:
    run_id: str
    session_id: str
    user_text: str
    session_context: dict[str, Any]
    timezone: str = "Asia/Shanghai"
    tool_calls: int = 0
    report_calls: int = 0
    web_calls: int = 0
    report_blocks: list[dict[str, Any]] = field(default_factory=list)
    resolved_identities: list[dict[str, Any]] = field(default_factory=list)

    @property
    def pending_action(self) -> dict[str, Any] | None:
        value = self.session_context.get("pending_action")
        return value if isinstance(value, dict) else None

    @property
    def active_fund(self) -> str | None:
        values = self.session_context.get("active_funds") or []
        return str(values[0]) if values else None


class DeepToolBridge:
    """Expose existing governed business tools as LangChain tools."""

    def __init__(
        self,
        registry: ToolRegistry,
        gateway: ToolGateway,
        database: Database,
        context: BridgeRunContext,
    ) -> None:
        self.registry = registry
        self.gateway = gateway
        self.database = database
        self.context = context
        self._tools = self._build_tools()

    @property
    def tools(self) -> list[BaseTool]:
        return list(self._tools.values())

    def select(self, *names: str) -> list[BaseTool]:
        return [self._tools[name] for name in names]

    def select_for_subagent(self, *names: str) -> list[BaseTool]:
        """Return expert tools without permission to mutate main-session identity."""
        selected: list[BaseTool] = []
        for name in names:
            if name != "fund_resolve":
                selected.append(self._tools[name])
                continue

            async def resolve_readonly(**kwargs: Any) -> str:
                query = str(kwargs.get("query") or "").strip()
                if not query:
                    return json.dumps(
                        {"status": "error", "message": "请提供基金名称或代码"},
                        ensure_ascii=False,
                    )
                as_of = kwargs.get("as_of")
                return await self._execute(
                    "fund_resolve",
                    "fund.resolve",
                    {"query": query, "as_of": as_of.isoformat() if as_of else None},
                )

            spec = self._tools["fund_resolve"]
            selected.append(
                self._structured(
                    "fund_resolve",
                    spec.description,
                    spec.args_schema,
                    resolve_readonly,
                )
            )
        return selected

    @staticmethod
    def _json(envelope: ToolResultEnvelope) -> str:
        return envelope.model_dump_json(exclude_none=True)

    async def _execute(
        self,
        wire_name: str,
        internal_name: str,
        arguments: dict[str, Any],
        *,
        explicit_report: bool = False,
    ) -> str:
        self.context.tool_calls += 1
        if wire_name == "web_search_manager_materials":
            self.context.web_calls += 1
        envelope = await self.gateway.execute(
            self.context.run_id,
            internal_name,
            arguments,
            explicit_report=explicit_report,
        )
        return self._json(envelope)

    async def _resolve(self, query: str, as_of: date | None = None) -> str:
        envelope = await self.gateway.execute(
            self.context.run_id,
            "fund.resolve",
            {"query": query, "as_of": as_of.isoformat() if as_of else None},
        )
        self.context.tool_calls += 1
        candidates = envelope.data if isinstance(envelope.data, list) else []
        identity = self._unique_identity(candidates, query)
        if identity is not None:
            self.context.resolved_identities.append(identity)
            report_updates = {} if self.context.active_fund == identity["fund_symbol"] else {"active_report_job_id": None, "active_report_document_id": None}
            self.context.session_context.update(
                **report_updates,
                active_funds=[identity["fund_symbol"]],
                active_manager=identity.get("manager_name"),
            )
            await self.database.update_session_context(
                self.context.session_id,
                active_funds=[identity["fund_symbol"]],
                active_manager=identity.get("manager_name"),
                **report_updates,
            )
        return self._json(envelope)

    @staticmethod
    def _unique_identity(candidates: list[dict[str, Any]], query: str) -> dict[str, Any] | None:
        if len(candidates) == 1:
            return candidates[0]
        normalized = query.strip().upper()
        exact = [
            item
            for item in candidates
            if normalized
            in {
                str(item.get("fund_symbol") or "").upper(),
                str(item.get("fund_name") or "").upper(),
            }
        ]
        return exact[0] if len(exact) == 1 else None

    def _active_or_error(self, value: str | None) -> str | None:
        return value or self.context.active_fund

    async def _report_request(self, **kwargs: Any) -> str:
        value = ReportRequestInput.model_validate(kwargs)
        self.context.report_calls += 1
        pending_report = (self.context.pending_action or {}).get("type") == "report_request"
        if (
            self.context.report_calls > 1
            or (not REPORT_ACTION.search(self.context.user_text) and not pending_report)
            or REPORT_STATUS.search(self.context.user_text)
        ):
            return json.dumps(
                {
                    "status": "policy_denied",
                    "message": "报告生成必须由用户在当前轮明确要求；本轮未获得明确授权。",
                },
                ensure_ascii=False,
            )
        today = datetime.now(ZoneInfo(self.context.timezone)).date()
        as_of = value.as_of or today
        pending_force_refresh = bool(
            (self.context.pending_action or {}).get("force_refresh")
        )
        explicit_force_refresh = bool(REPORT_FORCE_REFRESH.search(self.context.user_text))
        force_refresh = pending_force_refresh or explicit_force_refresh
        query = value.fund_query or self.context.active_fund
        if not query:
            return json.dumps(
                {
                    "status": "clarification_required",
                    "message": "还没有已确认的基金，请说明基金名称或代码。",
                },
                ensure_ascii=False,
            )
        resolved = await self.gateway.execute(
            self.context.run_id,
            "fund.resolve",
            {"query": query, "as_of": as_of.isoformat()},
            explicit_report=True,
        )
        self.context.tool_calls += 1
        candidates = resolved.data if isinstance(resolved.data, list) else []
        identity = self._unique_identity(candidates, query)
        if identity is None:
            if candidates:
                pending = {
                    "type": "report_request",
                    "authorized": True,
                    "candidates": candidates[:6],
                    "as_of": as_of.isoformat(),
                    "web_mode": value.web_mode,
                    "output_formats": ["markdown", "html", "docx"],
                    "force_refresh": force_refresh,
                }
                self.context.session_context["pending_action"] = pending
                await self.database.set_agent_pending_action(
                    self.context.session_id, pending
                )
            return json.dumps(
                {
                    "status": "clarification_required",
                    "message": (
                        "没有识别到唯一基金，请补充代码或完整名称。"
                        if not candidates
                        else "识别到多个候选，请用户选择。"
                    ),
                    "candidates": candidates[:6],
                },
                ensure_ascii=False,
                default=str,
            )
        await self.database.update_session_context(
            self.context.session_id,
            active_funds=[identity["fund_symbol"]],
            active_manager=identity.get("manager_name"),
        )
        self.context.session_context.update(
            active_funds=[identity["fund_symbol"]],
            active_manager=identity.get("manager_name"),
        )
        if value.as_of is None and not force_refresh:
            recent = await self.database.recent_completed_report(
                identity["fund_symbol"],
                today - timedelta(days=REPORT_REUSE_DAYS),
                manager_name=identity.get("manager_name"),
                manager_tenure_start=identity.get("manager_tenure_start"),
            )
            if recent:
                job_id = str(recent["job_id"])
                await self.database.update_session_context(
                    self.context.session_id,
                    active_funds=[identity["fund_symbol"]],
                    active_manager=identity.get("manager_name"),
                    active_report_job_id=job_id,
                    active_report_document_id=recent["document_id"],
                )
                block = {
                    "type": "report_job",
                    "job_id": job_id,
                    "trace_id": recent["trace_id"],
                    "fund_symbol": identity["fund_symbol"],
                    "fund_name": identity["fund_name"],
                    "manager_name": identity.get("manager_name"),
                    "as_of": recent["as_of"],
                    "status": recent["status"],
                    "output_formats": ["markdown", "html", "docx"],
                    "reused_existing": True,
                }
                self.context.report_blocks.append(block)
                self.context.session_context.update(
                    active_report_job_id=job_id,
                    active_report_document_id=recent["document_id"],
                    pending_action=None,
                )
                await self.database.set_agent_pending_action(self.context.session_id, None)
                await self.database.add_event(self.context.run_id, "report_job_created", block)
                return json.dumps(
                    {
                        **block,
                        "status": "reused",
                        "message": "已找到近 90 天内的已完成报告，直接返回原报告，未创建新任务。",
                        "directive": "立即告知用户已调取现有报告并结束本轮；不要说新报告正在生成。",
                    },
                    ensure_ascii=False,
                    default=str,
                )
        coverage = await self.gateway.execute(
            self.context.run_id,
            "fund.inspect_coverage",
            {
                "fund_symbol": identity["fund_symbol"],
                "manager_tenure_start": identity.get("manager_tenure_start"),
                "as_of": as_of.isoformat(),
            },
            explicit_report=True,
        )
        self.context.tool_calls += 1
        if coverage.status == ToolStatus.ERROR or not isinstance(coverage.data, dict):
            return json.dumps(
                {"status": "error", "message": "数据覆盖检查失败，未创建报告。"},
                ensure_ascii=False,
            )
        eligibility = coverage.data.get("eligibility") or {}
        if not eligibility.get("eligible_for_full_report", False):
            return json.dumps(
                {
                    "status": "not_eligible",
                    "fund": identity,
                    "blocking_reasons": eligibility.get("blocking_reasons") or [],
                    "warnings": eligibility.get("warnings") or [],
                    "message": "该基金当前不满足完整报告数据门槛，未创建任务。",
                },
                ensure_ascii=False,
                default=str,
            )
        generated = await self.gateway.execute(
            self.context.run_id,
            "report.generate",
            {
                "fund_query": identity["fund_symbol"],
                "as_of": as_of.isoformat(),
                "web_mode": value.web_mode,
                "output_formats": ["markdown", "html", "docx"],
                "force_refresh": force_refresh,
            },
            explicit_report=True,
        )
        self.context.tool_calls += 1
        if generated.status == ToolStatus.ERROR or not isinstance(generated.data, dict):
            return self._json(generated)
        job_id = generated.data.get("job_id")
        if not job_id:
            return json.dumps(
                {"status": "error", "message": "报告工具未返回 Job ID。"},
                ensure_ascii=False,
            )
        await self.database.link_report_job(job_id, self.context.session_id, self.context.run_id)
        job = await self.database.get_report_job(job_id)
        indexed = await self.database.report_document_for_job(job_id)
        await self.database.update_session_context(
            self.context.session_id,
            active_funds=[identity["fund_symbol"]],
            active_manager=identity.get("manager_name"),
            active_report_job_id=job_id,
            active_report_document_id=indexed["document_id"] if indexed else None,
        )
        block = {
            "type": "report_job",
            "job_id": job_id,
            "trace_id": generated.data.get("trace_id"),
            "fund_symbol": identity["fund_symbol"],
            "fund_name": identity["fund_name"],
            "manager_name": identity.get("manager_name"),
            "as_of": as_of.isoformat(),
            "status": job.status.value if job else generated.data.get("status", "accepted"),
            "output_formats": ["markdown", "html", "docx"],
            "reused_existing": False,
        }
        self.context.report_blocks.append(block)
        self.context.session_context["pending_action"] = None
        await self.database.set_agent_pending_action(self.context.session_id, None)
        await self.database.add_event(self.context.run_id, "report_job_created", block)
        return json.dumps(
            {
                **block,
                "job_status": block["status"],
                "status": "accepted",
                "directive": "立即向用户告知任务已创建并结束本轮；不要调用 report_status 轮询。",
            },
            ensure_ascii=False,
        )

    async def _industry_research(self, **kwargs: Any) -> str:
        user_text = self.context.user_text
        if (not INDUSTRY_REPORT_ACTION.search(user_text)
                or INDUSTRY_REPORT_NEGATION.search(user_text)
                or re.search(r"报告.{0,10}(?:进度|状态|好了吗|完成了吗|到哪了)", user_text)):
            return json.dumps({"status": "policy_denied", "message": "仅在用户明确要求生成完整产业链报告时创建任务。"}, ensure_ascii=False)

        value = ResearchInput.model_validate(kwargs)
        envelope = await self.gateway.execute(
            self.context.run_id, "industry.research",
            value.model_dump(mode="json") | {
                "session_id": self.context.session_id, "run_id": self.context.run_id,
            },
            explicit_report=True,
        )
        self.context.tool_calls += 1
        if envelope.status != ToolStatus.SUCCESS:
            return self._json(envelope)
        job = envelope.data
        block = {"type": "industry_job"} | {k: job[k] for k in ("job_id", "title", "status", "view_url")}
        if not any(item.get("job_id") == job["job_id"] for item in self.context.report_blocks):
            self.context.report_blocks.append(block)
            await self.database.add_event(self.context.run_id, "report_job_created", block)
        self.context.session_context["active_industry_job"] = job
        return json.dumps(
            block | {"directive": "立即告知用户已提交后台任务及任务 ID；进度由任务卡显示，不在本轮轮询。"},
            ensure_ascii=False,
        )

    async def _industry_lookup(self, wire_name: str, **kwargs: Any) -> str:
        active = self.context.session_context.get("active_industry_job") or {}
        job_id = kwargs.get("job_id") or active.get("job_id")
        if not job_id:
            return json.dumps({"status": "error", "message": "当前会话没有产业链任务，请提供本会话的任务 ID。"}, ensure_ascii=False)
        if any(item["type"] == "industry_job" and item["job_id"] == job_id for item in self.context.report_blocks):
            return json.dumps({"status": "accepted", "job_id": job_id, "message": "报告交给后台，结束本轮。"}, ensure_ascii=False)
        pattern = {"industry_cancel": REPORT_CANCEL_ACTION, "industry_resume": REPORT_RESUME_ACTION}.get(wire_name)
        if pattern and not pattern.search(self.context.user_text):
            return json.dumps({"status": "policy_denied", "message": "只有用户明确要求时才恢复或取消任务。"}, ensure_ascii=False)
        return await self._execute(
            wire_name, INDUSTRY_TOOLS[wire_name][0],
            kwargs | {"job_id": job_id, "session_id": self.context.session_id},
        )

    def _industry_tools(self) -> dict[str, BaseTool]:
        tools: dict[str, BaseTool] = {}
        registered = self.registry.names()
        for wire, (name, description) in INDUSTRY_TOOLS.items():
            if name not in registered:
                continue

            def lookup(tool_name):
                async def invoke(**kwargs: Any) -> str:
                    return await self._industry_lookup(tool_name, **kwargs)
                return invoke

            schema = {"industry_research": ResearchInput, "industry_result": ResultInput}.get(wire, JobLookup)
            handler = self._industry_research if wire == "industry_research" else lookup(wire)
            tools[wire] = self._structured(wire, description, schema, handler)
        return tools

    def _market_tools(self) -> dict[str, BaseTool]:
        def build(wire_name: str, internal_name: str) -> BaseTool:
            spec = self.registry.get(internal_name)

            async def lookup(**kwargs: Any) -> str:
                arguments = spec.input_model.model_validate(kwargs).model_dump(mode="json")
                return await self._execute(wire_name, internal_name, arguments)

            return self._structured(wire_name, spec.description, spec.input_model, lookup)

        return {
            wire: build(wire, name)
            for wire, name in (("market_lake_lookup", LAKE_TOOL), ("market_proxy_lookup", PROXY_TOOL))
            if name in self.registry.names()
        }

    def _structured(
        self,
        name: str,
        description: str,
        args_schema: type[BaseModel],
        coroutine,
    ) -> StructuredTool:
        return StructuredTool(
            name=name,
            description=description,
            args_schema=args_schema,
            coroutine=coroutine,
            handle_validation_error=True,
        )

    def _build_tools(self) -> dict[str, BaseTool]:
        async def fund_resolve(**kwargs: Any) -> str:
            query = str(kwargs.get("query") or "").strip()
            if not query:
                return json.dumps({"status": "error", "message": "请提供基金名称或代码"}, ensure_ascii=False)
            as_of = kwargs.get("as_of")
            return await self._resolve(query, as_of)

        async def fund_snapshot(**kwargs: Any) -> str:
            symbol = self._active_or_error(kwargs.get("fund_symbol"))
            if not symbol:
                return json.dumps({"status": "clarification_required", "message": "请先确认基金"}, ensure_ascii=False)
            return await self._execute(
                "fund_research_snapshot",
                "fund.research_snapshot",
                {"fund_symbol": symbol, "as_of": kwargs.get("as_of")},
            )

        async def fund_metric(**kwargs: Any) -> str:
            symbol = self._active_or_error(kwargs.get("fund_symbol"))
            if not symbol:
                return json.dumps({"status": "clarification_required", "message": "请先确认基金"}, ensure_ascii=False)
            return await self._execute(
                "fund_metric",
                "fund.metric",
                {
                    "fund_symbol": symbol,
                    "metric": kwargs["metric"],
                    "as_of": kwargs.get("as_of"),
                    "year": kwargs.get("year"),
                },
            )

        async def coverage(**kwargs: Any) -> str:
            symbol = self._active_or_error(kwargs.get("fund_symbol"))
            if not symbol:
                return json.dumps({"status": "clarification_required", "message": "请先确认基金"}, ensure_ascii=False)
            return await self._execute(
                "fund_inspect_coverage",
                "fund.inspect_coverage",
                {
                    "fund_symbol": symbol,
                    "as_of": kwargs.get("as_of"),
                    "manager_tenure_start": kwargs.get("manager_tenure_start"),
                },
            )

        async def kb_search(**kwargs: Any) -> str:
            query = str(kwargs["query"])
            document_id = kwargs.get("document_id")
            if not document_id and REPORT_REFERENCE.search(query):
                document_id = self.context.session_context.get("active_report_document_id")
            return await self._execute(
                "kb_search",
                "kb.search",
                {
                    **kwargs,
                    "fund_symbol": kwargs.get("fund_symbol") or self.context.active_fund,
                    "document_id": document_id,
                },
            )

        async def generic(internal_name: str, wire_name: str, **kwargs: Any) -> str:
            return await self._execute(wire_name, internal_name, kwargs)

        async def memory_remember(**kwargs: Any) -> str:
            if not MEMORY_ACTION.search(self.context.user_text):
                return json.dumps(
                    {"status": "policy_denied", "message": "只有用户当前轮明确要求记住时才能写长期记忆。"},
                    ensure_ascii=False,
                )
            return await self._execute("memory_remember", "memory.remember", kwargs)

        async def report_resume(**kwargs: Any) -> str:
            if not REPORT_RESUME_ACTION.search(self.context.user_text):
                return json.dumps(
                    {"status": "policy_denied", "message": "用户当前轮没有明确要求恢复报告。"},
                    ensure_ascii=False,
                )
            return await generic("report.resume", "report_resume", **kwargs)

        async def report_cancel(**kwargs: Any) -> str:
            if not REPORT_CANCEL_ACTION.search(self.context.user_text):
                return json.dumps(
                    {"status": "policy_denied", "message": "用户当前轮没有明确要求取消报告。"},
                    ensure_ascii=False,
                )
            return await generic("report.cancel", "report_cancel", **kwargs)

        async def memory_forget(**kwargs: Any) -> str:
            if not MEMORY_FORGET_ACTION.search(self.context.user_text):
                return json.dumps(
                    {"status": "policy_denied", "message": "用户当前轮没有明确要求删除记忆。"},
                    ensure_ascii=False,
                )
            return await generic("memory.forget", "memory_forget", **kwargs)

        async def web_materials(**kwargs: Any) -> str:
            return await generic(
                "web.search_manager_materials",
                "web_search_manager_materials",
                **kwargs,
            )

        async def report_status(**kwargs: Any) -> str:
            return await generic("report.status", "report_status", **kwargs)

        async def report_result(**kwargs: Any) -> str:
            return await generic("report.result", "report_result", **kwargs)

        async def artifact_read(**kwargs: Any) -> str:
            return await generic("artifact.read", "artifact_read", **kwargs)

        async def memory_list(**kwargs: Any) -> str:
            return await generic("memory.list", "memory_list", **kwargs)

        fund_query_model = self.registry.get("fund.resolve").input_model
        material_model = self.registry.get("web.search_manager_materials").input_model
        job_model = self.registry.get("report.status").input_model
        artifact_model = self.registry.get("artifact.read").input_model
        remember_model = self.registry.get("memory.remember").input_model
        forget_model = self.registry.get("memory.forget").input_model
        empty_model = self.registry.get("memory.list").input_model
        return {
            **self._industry_tools(),
            **self._market_tools(),
            "fund_resolve": self._structured(
                "fund_resolve",
                "按基金代码、简称、全称或经理名解析候选基金。用户提到新基金时必须先调用。",
                fund_query_model,
                fund_resolve,
            ),
            "fund_research_snapshot": self._structured(
                "fund_research_snapshot",
                "返回基金近年收益、回撤、波动、规模、换手和最新持仓的紧凑研究快照。",
                ActiveFundInput,
                fund_snapshot,
            ),
            "fund_metric": self._structured(
                "fund_metric",
                "查询单一确定性指标，如年度收益、最新净值、规模或换手率。",
                ActiveMetricInput,
                fund_metric,
            ),
            "fund_inspect_coverage": self._structured(
                "fund_inspect_coverage",
                "检查基金净值、持仓、规模、换手和季报数据覆盖。",
                ActiveCoverageInput,
                coverage,
            ),
            "kb_search": self._structured(
                "kb_search",
                "检索历史投资手册、路演、季报和新生成的基金报告；用于定性研究和报告问答。",
                KnowledgeInput,
                kb_search,
            ),
            "web_search_manager_materials": self._structured(
                "web_search_manager_materials",
                "搜索并筛选基金经理的路演、访谈和交流纪要。",
                material_model,
                web_materials,
            ),
            "report_request": self._structured(
                "report_request",
                "只在用户当前轮明确要求完整基金报告时调用。默认优先返回近 90 天内的现有报告；仅当用户明确说重写、新写或从头生成时才强制创建新任务。",
                ReportRequestInput,
                self._report_request,
            ),
            "report_status": self._structured("report_status", "查询报告任务状态。", job_model, report_status),
            "report_result": self._structured("report_result", "获取已完成报告的产物。", job_model, report_result),
            "report_resume": self._structured("report_resume", "用户明确要求时恢复中断的报告。", job_model, report_resume),
            "report_cancel": self._structured("report_cancel", "用户明确要求时取消报告。", job_model, report_cancel),
            "artifact_read": self._structured("artifact_read", "按 Artifact ID 读取新项目内的大型工具结果或报告产物。", artifact_model, artifact_read),
            "memory_list": self._structured("memory_list", "列出用户已确认的长期偏好和研究约定。", empty_model, memory_list),
            "memory_remember": self._structured("memory_remember", "仅在用户明确要求记住偏好或约定时保存；禁止保存动态金融事实。", remember_model, memory_remember),
            "memory_forget": self._structured("memory_forget", "用户明确要求时删除某条长期记忆。", forget_model, memory_forget),
        }
