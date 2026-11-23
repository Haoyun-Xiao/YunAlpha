"""Controlled model boundary; the API, graph, tools, jobs and persistence are real."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from fund_research_agent.api.app import create_app
from fund_research_agent.bootstrap import build_container
from fund_research_agent.config import load_settings
from fund_research_agent.runtime_deep import DeepAgentRuntime
from fund_research_agent.workflows.handbook.workflow import HandbookResult, validate_handbook
from fund_research_agent.workflows.report.worker import ReportWorker

FUND = "013840.OF"
AS_OF = "2026-11-05"
QUESTIONS = (
    f"{FUND} 是什么基金，由谁管理？",
    f"它截至 {AS_OF} 的最近复权净值是多少？",
    f"请给它写一份完整的基金深度报告，截至 {AS_OF}。",
)


class ControlledModel(BaseChatModel):
    disable_streaming: bool = True
    interrupt_metric: bool = True

    @property
    def _llm_type(self):
        return "yunalpha-acceptance-controlled"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        latest = messages[-1]
        if isinstance(latest, ToolMessage):
            tool = json.loads(latest.content)
            if latest.name == "fund_resolve":
                identity = tool["data"][0]
                assert identity["fund_symbol"] == FUND
                text = f"{FUND} 是{identity['fund_name']}，基金经理是{identity['manager_name']}。"
            elif latest.name == "fund_metric":
                result = tool["data"]
                assert result["fund_symbol"] == FUND and result["data_as_of"] <= AS_OF
                text = f"{FUND} 最近复权净值为 {result['value']:.4f}（{result['data_as_of']}）。"
            else:
                assert latest.name == "report_request" and tool["status"] == "accepted", tool
                text = "固定验收报告任务已排队。"
            response = AIMessage(content=text)
        else:
            question = next(message.content for message in reversed(messages) if isinstance(message, HumanMessage))
            if question == QUESTIONS[0]:
                name, args = "fund_resolve", {"query": FUND, "as_of": AS_OF}
            elif question == QUESTIONS[1]:
                # Use a real LangGraph checkpoint and real context; the follow-up
                # intentionally does not provide the fund code to the tool.
                if self.interrupt_metric:
                    self.interrupt_metric = False
                    raise RuntimeError("controlled_chat_interruption")
                assert any("基金经理是" in str(message.content) for message in messages)
                name, args = "fund_metric", {"metric": "latest_nav", "as_of": AS_OF}
            else:
                assert question == QUESTIONS[2]
                name, args = "report_request", {"as_of": AS_OF}
            response = AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"controlled-{name}"}])
        return ChatResult(generations=[ChatGeneration(message=response)])


def event_frames(response):
    response.raise_for_status()
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = []
    for block in response.text.strip().split("\n\n"):
        event_id, event_type, data = block.splitlines()
        frames.append({
            "id": int(event_id.removeprefix("id: ")),
            "event": event_type.removeprefix("event: "),
            "data": json.loads(data.removeprefix("data: ")),
        })
    return frames


def await_run(client, run_id, expected):
    for _ in range(200):
        result = client.get(f"/api/v1/runs/{run_id}").json()
        if result["status"] == expected:
            return result
        time.sleep(0.05)
    detail = client.get(f"/api/v1/traces/{run_id}").json()
    pytest.fail(f"Run did not reach {expected}: {detail}")


@pytest.mark.timeout(240)
def test_chat_and_report_migration(tmp_path: Path, monkeypatch):
    settings = load_settings()
    sample = settings.inputs_root / "acceptance/report"
    if not (sample / "provenance.json").is_file():
        pytest.skip("已验收报告未冻结")
    provenance = json.loads((sample / "provenance.json").read_text())
    assert provenance["fund"] == FUND and provenance["as_of"] == AS_OF
    monkeypatch.setenv("FUND_AGENT_MODEL_MODE", "replay")
    isolated = settings.model_copy(update={
        "var_root": tmp_path,
        "state_db": tmp_path / "agent.sqlite",
        "checkpoint_db": tmp_path / "checkpoints.sqlite",
    })
    container = build_container(isolated)
    runtime = DeepAgentRuntime(
        isolated, container.database, container.model, container.registry,
        container.gateway, container.memory, chat_model=ControlledModel(),
    )
    container = replace(container, runtime=runtime)

    # There are no model recordings for the old Job. Reuse the approved
    # narrative and exact historical output bytes at the model/renderer seam.
    chunks = re.split(r"(?m)^==========  (?:Part(10|[1-9])：|总结：)[^\n]+\n", (sample / "report.md").read_text())
    parts = {f"part{number}": body.strip() for number, body in zip(chunks[1:-2:2], chunks[2:-2:2], strict=True)}
    assert set(parts) == {f"part{i}" for i in range(1, 11)}
    summary = chunks[-1].strip()

    workflow = container.report_workflow
    engine = workflow.report_engine
    assert engine.deterministic_audit(parts, summary)["passed"]

    async def replay_handbook(_target, _json_files, _materials, directory):
        text = (sample / "handbook.md").read_text()
        output = container.boundary.write_text(directory / "handbook.md", text)
        validation = validate_handbook(text, "方建", settings.handbook)
        assert validation["valid"], validation
        return HandbookResult(output, validation["quality"], validation["warnings"], True, {"acceptance_replay": True})

    async def interrupt_generation(*_args):
        raise RuntimeError("controlled_report_interruption")

    async def replay_parts(*_args):
        return parts

    async def replay_summary(*_args):
        return summary

    async def replay_audit(*_args, **_kwargs):
        return parts, summary, {
            "passed": False, "issues": {"historical": provenance["warnings"]},
            "acceptance_replay": True,
        }

    async def replay_render(_name, _parts, _summary, _data, _render_input, output_dir, as_of):
        assert as_of == date.fromisoformat(AS_OF)
        outputs = {}
        for kind, suffix in {"markdown": "md", "html": "html", "docx": "docx"}.items():
            filename = f"report.{suffix}"
            outputs[kind] = container.boundary.write_bytes(output_dir / filename, (sample / filename).read_bytes())
        return outputs, {"warnings": ["acceptance_historical_report_replay"], "acceptance_replay": True}

    monkeypatch.setattr(workflow.handbook, "build", replay_handbook)
    monkeypatch.setattr(engine, "generate_parts", interrupt_generation)
    monkeypatch.setattr(engine, "summary", replay_summary)
    monkeypatch.setattr(engine, "audit_and_rewrite", replay_audit)
    monkeypatch.setattr(engine, "render", replay_render)
    monkeypatch.setattr("fund_research_agent.workflows.report.worker.build_container", lambda: container)

    capture = {}
    with TestClient(create_app(container), root_path="/agent-runtime") as client:
        assert client.get("/agent-runtime/api/v1/sessions").json() == []
        session = client.post("/api/v1/sessions", json={"title": "验收：银华集成电路"}).json()
        sid = session["session_id"]
        capture["session_created"] = session
        capture["sessions"] = client.get("/api/v1/sessions").json()

        def chat(question):
            accepted = client.post("/api/v1/chat/runs", json={"session_id": sid, "message": question})
            assert accepted.status_code == 202
            return accepted.json()

        first = chat(QUESTIONS[0])
        capture["run_accepted"] = first
        first_id = first["run_id"]
        await_run(client, first_id, "completed")
        first_events = event_frames(client.get(f"/api/v1/runs/{first_id}/events"))
        assert first_events[-1]["event"] == "done" and "方建" in first_events[-1]["data"]["answer"]
        capture["first_events"] = first_events

        second = chat(QUESTIONS[1])["run_id"]
        await_run(client, second, "recoverable")
        detail = client.get(f"/api/v1/sessions/{sid}").json()
        recovery_event = detail["latest_run"]["events"][-1]
        assert recovery_event["event_type"] == "run_recoverable"
        resumed = client.post(f"/api/v1/runs/{second}/resume")
        assert resumed.status_code == 202 and resumed.json()["run_id"] == second
        await_run(client, second, "completed")
        next_events = event_frames(client.get(
            f"/api/v1/runs/{second}/events",
            headers={"Last-Event-ID": str(recovery_event["event_id"])},
        ))
        assert next_events[0]["id"] > recovery_event["event_id"]
        assert next_events[-1]["event"] == "done" and "最近复权净值" in next_events[-1]["data"]["answer"]
        reopened = client.get(f"/api/v1/sessions/{sid}").json()
        assert reopened["context"]["active_funds"] == [FUND]
        assert [item["role"] for item in reopened["messages"]] == ["user", "assistant"] * 2
        assert [item["content"] for item in reopened["messages"] if item["role"] == "user"] == list(QUESTIONS[:2])
        capture.update(chat_recoverable=recovery_event, chat_resume=resumed.json(), chat_resumed_events=next_events, session_reopened=reopened)

        third = chat(QUESTIONS[2])["run_id"]
        await_run(client, third, "completed")
        third_events = event_frames(client.get(f"/api/v1/runs/{third}/events"))
        block = third_events[-1]["data"]["ui_blocks"][0]
        assert block["type"] == "report_job" and block["fund_symbol"] == FUND
        assert any(item["event"] == "report_job_created" for item in third_events)
        job_id = block["job_id"]
        path = f"/api/v1/report-jobs/{job_id}"
        created = client.get(path).json()
        assert created["request"]["as_of"] == AS_OF and created["status"] == "queued"
        capture["chat_report_block"] = block

        assert client.portal.call(ReportWorker("phase2-acceptance-1").run_once)
        failed = client.get(path).json()
        assert failed["status"] == "recoverable" and failed["attempt"] == 1, failed
        assert "controlled_report_interruption" in failed["errors"][0], failed
        trace = client.get(f"/api/v1/traces/{failed['trace_id']}").json()["events"]
        checkpoint = trace[-1]["event_id"]
        assert trace[-1]["event_type"] == "report_recoverable"

        response = client.post(f"{path}/resume")
        assert response.status_code == 200 and response.json() == {"job_id": job_id, "status": "recoverable"}
        monkeypatch.setattr(engine, "generate_parts", replay_parts)
        assert client.portal.call(ReportWorker("phase2-acceptance-2").run_once)
        job = client.get(path).json()
        assert job["status"] == "completed_with_warnings" and job["attempt"] == 2, job
        assert "acceptance_historical_report_replay" in job["warnings"]

        events = event_frames(client.get(f"{path}/events", headers={"Last-Event-ID": str(checkpoint)}))
        assert all(event["id"] > checkpoint for event in events)
        assert events[0]["data"] == {"reason": "manual_resume"}
        assert any(item["data"].get("checkpoint_reused") for item in events)
        published = next(event["data"] for event in events if event["event"] == "report_published")
        assert events[-1]["event"] == "done"
        report = client.get(f"/agent-runtime{path}/view")
        assert report.status_code == 200 and published["fund_name"] in report.text
        artifact_root = "/agent-runtime/api/v1/artifacts/"
        assert f'href="{artifact_root}{published["artifacts"]["docx"]}"' in report.text
        report_files = client.get(f"{path}/artifacts").json()["artifacts"]
        for kind, suffix in {"markdown": "md", "html": "html", "docx": "docx"}.items():
            artifact_id = published["artifacts"][kind]
            response = client.get(artifact_root + artifact_id)
            assert response.status_code == 200
            assert hashlib.sha256(response.content).hexdigest() == hashlib.sha256((sample / f"report.{suffix}").read_bytes()).hexdigest()
            assert any(record["artifact_id"] == artifact_id for record in report_files)
        assert client.get(f"/api/v1/sessions/{sid}").json()["context"]["active_report_document_id"] == published["document_id"]
        capture.update(report_progress_before_resume=trace, report_resume={"job_id": job_id, "status": "recoverable"}, report_resumed_events=events, report_job=job, report_view=f"/agent-runtime{path}/view")

    # Reopen on a fresh app/container (as after an API restart).
    with TestClient(create_app(build_container(isolated))) as client:
        reopened = client.get(f"/api/v1/sessions/{sid}").json()
        assert len(reopened["messages"]) == 6
        assert reopened["context"]["active_funds"] == [FUND]
        assert reopened["context"]["active_report_job_id"] == job_id

    if output := os.getenv("YUNALPHA_AGENT_VERIFY_CAPTURE"):
        Path(output).write_text(json.dumps(capture, ensure_ascii=False, indent=2) + "\n")


def test_resume_after_first_model_call_fails_before_checkpoint(tmp_path, monkeypatch):
    class FirstCallFails(ControlledModel):
        first_call: bool = True

        def _generate(self, *args, **kwargs):
            if self.first_call:
                self.first_call = False
                raise RuntimeError("controlled_no_checkpoint")
            return super()._generate(*args, **kwargs)

    settings = load_settings().model_copy(update={
        "var_root": tmp_path, "state_db": tmp_path / "agent.sqlite",
        "checkpoint_db": tmp_path / "checkpoints.sqlite",
    })
    container = build_container(settings)
    runtime = DeepAgentRuntime(
        settings, container.database, container.model, container.registry,
        container.gateway, container.memory, chat_model=FirstCallFails(),
    )
    container = replace(container, runtime=runtime)
    with TestClient(create_app(container)) as client:
        sid = client.post("/api/v1/sessions", json={"title": "首次模型失败"}).json()["session_id"]
        run = client.post("/api/v1/chat/runs", json={"session_id": sid, "message": QUESTIONS[0]}).json()["run_id"]
        await_run(client, run, "recoverable")
        assert client.get(f"/api/v1/runs/{run}").json()["checkpoint_id"] is None
        assert client.post(f"/api/v1/runs/{run}/resume").status_code == 202
        await_run(client, run, "completed")
        detail = client.get(f"/api/v1/sessions/{sid}").json()
        assert [item["role"] for item in detail["messages"]] == ["user", "assistant"]
        assert "方建" in detail["messages"][-1]["content"]
