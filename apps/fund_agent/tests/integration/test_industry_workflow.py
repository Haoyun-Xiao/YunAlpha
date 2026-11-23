"""Controlled model and web inputs; exercise the real graph, worker and HTTP routes."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import duckdb
import httpx
import polars as pl
import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from openpyxl import Workbook

from fund_research_agent.adapters.fund_universe import REQUIRED_COLUMNS
from fund_research_agent.api.app import create_app
from fund_research_agent.bootstrap import build_container
from fund_research_agent.config import load_settings
from fund_research_agent.runtime_deep import DeepAgentRuntime
from industry_agent.store import digest
from industry_agent.worker import IndustryWorker
from industry_agent.workflow import ResearchWorkflow

ASK = "请为我生成一份关于测试光模块的完整产业链研究报告。"
FOLLOWUP = "刚才产业链报告的验证指标是什么？"
CONCEPT = "光模块产业链是什么？"
ENDPOINT = "/api/v1/industry-jobs"
EVIDENCE_ID = "controlledevidence01"


class ControlledChat(BaseChatModel):
    disable_streaming: bool = True

    @property
    def _llm_type(self):
        return "industry-controlled"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        latest = messages[-1]
        question = next(m.content for m in reversed(messages) if isinstance(m, HumanMessage))
        if isinstance(latest, ToolMessage) and latest.name != "read_file":
            data = json.loads(latest.content)
            if latest.name == "industry_result":
                assert data["data"]["matches"]
                assert any("验证指标" in x["content"] for x in data["data"]["matches"])
                answer = "验证指标见已生成报告。"
            elif question == CONCEPT:
                assert data["status"] == "policy_denied"
                answer = "光模块由器件、组装和应用组成。"
            else:
                assert data["type"] == "industry_job" and data["status"] == "queued"
                answer = f"任务已提交：{data['job_id']}"
            response = AIMessage(content=answer)
        else:
            if question == ASK and not isinstance(latest, ToolMessage):
                assert any("industry-research" in str(m.content) for m in messages)
                name, args = "read_file", {"file_path": "/skills/industry-research/SKILL.md"}
            elif question == FOLLOWUP:
                name, args = "industry_result", {"query": "验证指标"}
            else:
                if isinstance(latest, ToolMessage):
                    assert "六维评分" in str(latest.content)
                name, args = "industry_research", {"anchor": "测试光模块", "as_of": "2026-10-20"}
            response = AIMessage(
                content="",
                tool_calls=[{"name": name, "args": args, "id": f"controlled-{name}"}],
            )
        return ChatResult(generations=[ChatGeneration(message=response)])


@pytest.fixture
def container(tmp_path):
    fund_universe = tmp_path / "fund-universe.xlsx"
    workbook = Workbook()
    workbook.create_sheet().append(REQUIRED_COLUMNS)
    workbook.save(fund_universe)
    workbook.close()
    lake_database = tmp_path / "fund-bridge.duckdb"
    duckdb.connect(str(lake_database)).close()
    settings = load_settings().model_copy(
        update={
            "var_root": tmp_path / "var",
            "state_db": tmp_path / "var/fund.sqlite",
            "checkpoint_db": tmp_path / "var/conversation.sqlite",
            "legacy_root": tmp_path / "lake",
            "base_path": "/agent-runtime",
            "fund_universe": fund_universe,
            "lake_database": lake_database,
        }
    )
    c = build_container(settings)
    runtime = DeepAgentRuntime(
        settings,
        c.database,
        c.model,
        c.registry,
        c.gateway,
        c.memory,
        chat_model=ControlledChat(),
        industry=c.industry,
    )
    return replace(c, runtime=runtime)


async def chat(client, sid, text):
    response = await client.post("/api/v1/chat/runs", json={"session_id": sid, "message": text})
    assert response.status_code == 202
    run_id = response.json()["run_id"]
    async with asyncio.timeout(15):
        while True:
            run = (await client.get(f"/api/v1/runs/{run_id}")).json()
            if run["status"] != "running":
                assert run["status"] == "completed", run
                return run_id
            await asyncio.sleep(0.02)


class FixtureSources:
    last_errors = []
    calls = 0

    def __init__(self, settings):
        self.settings = settings

    async def collect(self, *args, **kwargs):
        type(self).calls += 1
        text = "测试光模块的验证指标为客户验证批次。"
        return [
            {
                "evidence_id": EVIDENCE_ID,
                "title": "测试材料",
                "content": text,
                "content_hash": digest(text),
                "url": "https://example.test/optical",
                "source_type": "public_web",
            }
        ]

    async def collect_segment(self, *args, **kwargs):
        return []


class FixtureModel:
    def __init__(self, settings):
        pass

    def fingerprint(self):
        return {"test-model": "controlled"}

    async def complete(self, operation, system, user, schema, callback):
        if operation == "structure":
            payload = {
                "title": "测试光模块",
                "scope": "验收测试",
                "nodes": [
                    {
                        "id": key,
                        "name": key,
                        "position": pos,
                        "description": "测试环节",
                        "keywords": ["测试光模块"],
                        "evidence_ids": [EVIDENCE_ID],
                    }
                    for key, pos in [("input", "upstream"), ("output", "downstream")]
                ],
                "edges": [{"source": "input", "target": "output", "label": "供应"}],
            }
        elif operation == "risk_review":
            payload = {"risks": []}
        else:
            assert operation == "synthesis", operation
            payload = {
                "title": "受控测试报告",
                "headline": "测试结论",
                "body_md": f"**验证指标**：客户验证批次 [{EVIDENCE_ID}]",
                "access_advice": [],
                "watch_items": [],
                "value_pool_items": [],
            }
        return schema.model_validate(payload).model_dump()


@pytest.mark.asyncio
async def test_industry_conversation_recovery_and_local_report(container, monkeypatch):
    lake = container.settings.legacy_root
    parquet = lake / "curated/companies/sample.parquet"
    parquet.parent.mkdir(parents=True)
    pl.DataFrame(
        {
            "symbol": ["000001.SZ"],
            "com_name": ["测试公司"],
            "main_business": ["不匹配的业务"],
            "introduction": ["验收用数据"],
            "business_scope": ["验收用业务范围"],
        }
    ).write_parquet(parquet)
    lake_before = {p.relative_to(lake): p.read_bytes() for p in lake.rglob("*") if p.is_file()}

    app = create_app(container)
    FixtureSources.calls = 0
    calls = {"input": 0, "output": 0}
    assert container.industry.store.db_path != container.settings.state_db

    async def expert(self, job_id, node, inputs):
        key = node["id"]
        calls[key] += 1
        if key == "output" and calls[key] == 1:
            raise RuntimeError("controlled interruption")
        return {
            "segment_id": key,
            "segment": node["name"],
            "body_md": "测试环节",
            "findings": [],
            "companies": [],
            "scores": [],
            "gaps": [],
            "watch_items": [],
        }

    monkeypatch.setattr(ResearchWorkflow, "segment_expert", expert)
    monkeypatch.setattr("industry_agent.workflow.SourceService", FixtureSources)
    monkeypatch.setattr("industry_agent.workflow.ModelService", FixtureModel)

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://test",
        ) as client,
    ):
        items = {item["id"]: item for item in (await client.get("/api/v1/skills")).json()["items"]}
        assert {"industry-research", "fund-research", "deep-report"} <= items.keys()
        assert "industry_result" in items["industry-research"]["tools"]
        (method,) = items["industry-research"]["references"]
        assert method["name"] == "references/method.md"
        assert "六维评分" in method["content"] and "财务以公告日" in method["content"]
        sid = (await client.post("/api/v1/sessions", json={"title": "产业链测试"})).json()[
            "session_id"
        ]
        await chat(client, sid, CONCEPT)
        assert container.industry.active_job(sid) is None
        run_id = await chat(client, sid, ASK)
        session = (await client.get(f"/api/v1/sessions/{sid}")).json()
        (block,) = session["messages"][-1]["metadata"]["ui_blocks"]
        jid = block["job_id"]
        assert block["type"] == "industry_job"
        assert any(
            e["event_type"] == "report_job_created" and e["payload"] == block
            for e in session["latest_run"]["events"]
        )
        url = f"{ENDPOINT}/{jid}"
        job = (await client.get(url)).json()
        assert job["status"] == "queued" and job["run_id"] == run_id and job["session_id"] == sid
        assert job["view_url"] == f"/agent-runtime{url}/view"
        assert (await client.get(job["view_url"])).status_code == 409

        worker = IndustryWorker(container.industry, "worker-initial")
        assert await worker.run_once()
        interrupted = (await client.get(url)).json()
        assert interrupted["status"] == "recoverable"
        assert "controlled interruption" in interrupted["error"]
        assert not await worker.run_once()
        assert (await client.post(f"{url}/resume")).json()["status"] == "queued"
        assert await IndustryWorker(container.industry, "worker-restarted").run_once()
        assert calls == {"input": 1, "output": 2} and FixtureSources.calls == 1

        job = (await client.get(url)).json()
        assert job["status"] == "completed_with_warnings"
        assert job["progress"] == 100 and job["report_id"]
        page = await client.get(job["view_url"])
        assert page.status_code == 200
        assert page.text.startswith("<!doctype html>") and "<strong>验证指标</strong>" in page.text
        assert "chain-graph-data" in page.text
        artifacts = {a["label"]: a["url"] for a in job["artifacts"]}
        report = (await client.get(artifacts["JSON"])).json()
        assert report["manifest"]["publication"] == {
            "storage": "local",
            "formal_lake_written": False,
        }
        assert report["evidence"][0]["url"] == "https://example.test/optical"
        assert lake_before == {
            p.relative_to(lake): p.read_bytes() for p in lake.rglob("*") if p.is_file()
        }

        await chat(client, sid, FOLLOWUP)
        refreshed = (await client.get(f"/api/v1/sessions/{sid}")).json()
        assert "验证指标" in refreshed["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_industry_api_idempotency_and_queued_cancellation(container):
    app = create_app(container)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://test",
        ) as client,
    ):
        assert (
            await client.post(ENDPOINT, json={"anchor": "测试", "session_id": "missing"})
        ).status_code == 404
        assert (await client.get(f"{ENDPOINT}/missing")).status_code == 404
        request = {"anchor": "测试", "idempotency_key": "controlled"}
        first = await client.post(ENDPOINT, json=request)
        assert first.status_code == 202
        jid = first.json()["job_id"]
        assert (await client.post(ENDPOINT, json=request)).json()["job_id"] == jid
        assert (await client.post(f"{ENDPOINT}/{jid}/resume")).status_code == 409
        assert (await client.post(f"{ENDPOINT}/{jid}/cancel")).json()["status"] == "cancelled"
        assert not await IndustryWorker(container.industry, "worker").run_once()
        assert (await client.post(f"{ENDPOINT}/{jid}/resume")).json()["status"] == "queued"
