from __future__ import annotations

import asyncio

from fund_research_agent.tools.registry import ToolRegistry, ToolSpec
from industry_agent.contracts import ResearchInput, ResultInput
from industry_agent.service import IndustryService


class CreateIndustryJob(ResearchInput):
    session_id: str
    run_id: str


class ReadIndustryJob(ResultInput):
    session_id: str
    job_id: str


# These are the real model-facing names; the gateway retains its dotted names.
INDUSTRY_TOOLS = {
    "industry_research": (
        "industry.research",
        "仅在用户明确要求完整产业链研究报告时创建后台任务。",
    ),
    "industry_status": ("industry.status", "查询产业链任务进度和状态。"),
    "industry_result": ("industry.result", "读取已生成的产业链报告正文、证据和下载链接。"),
    "industry_resume": ("industry.resume", "按用户要求从断点恢复产业链任务。"),
    "industry_cancel": ("industry.cancel", "按用户要求取消产业链任务。"),
}


def register_industry_tools(registry: ToolRegistry, service: IndustryService) -> None:
    async def create(value: CreateIndustryJob):
        return await asyncio.to_thread(
            service.create,
            value.anchor,
            value.as_of,
            value.session_id,
            run_id=value.run_id,
        )

    def read(action):
        def execute(value: ReadIndustryJob):
            job = service.job(value.job_id, session_id=value.session_id)
            if action == "result":
                return service.result(job, value.query, value.offset)
            return getattr(service, action)(job)

        async def handler(value: ReadIndustryJob):
            return await asyncio.to_thread(execute, value)

        return handler

    for wire, (name, description) in INDUSTRY_TOOLS.items():
        action = name.split(".")[1]
        registry.register(
            ToolSpec(
                name=name,
                description=description,
                input_model=CreateIndustryJob if wire == "industry_research" else ReadIndustryJob,
                handler=create if wire == "industry_research" else read(action),
                permission={
                    "research": "expensive",
                    "resume": "write_var",
                    "cancel": "write_var",
                }.get(action, "read"),
            )
        )
