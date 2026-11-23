import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from httpx import AsyncByteStream, Response

from fund_research_agent.adapters.model import ModelGateway


def test_deepseek_direct_ignores_opencode_key(tmp_path, monkeypatch):
    config = tmp_path / "models.yaml"
    config.write_text("base_url: https://api.deepseek.com\nroles: {}\n")
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "old-provider-key")
    monkeypatch.setenv("DEEPSEEK_ASSISTANT_API_KEY", "assistant-key")
    monkeypatch.setenv("DEEPSEEK_REPORT_API_KEY", "report-key")
    gateway = ModelGateway(config)
    assert gateway.api_key == "assistant-key"
    assert gateway.api_key_for("report") == "report-key"
    assert gateway.api_key_for("handbook") == "report-key"


class SlowReasoningStream(AsyncByteStream):
    async def __aiter__(self):
        yield (
            'data: {"choices":[{"delta":{"reasoning_content":"正在分析"},'
            '"finish_reason":null}]}\n\n'
        ).encode()
        await asyncio.sleep(2.2)
        yield (
            b'data: {"choices":[{"delta":{"content":"OK"},'
            b'"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
        )


async def test_model_without_thinking_does_not_send_thinking_field(
    tmp_path: Path, monkeypatch
):
    config = tmp_path / "models.yaml"
    config.write_text(
        """
base_url: https://opencode.ai/zen/go/v1
roles:
  assistant:
    model: ox-alpha-free
    temperature: 0
    max_tokens: 128
    timeout: 30
    context_window_tokens: 131072
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "test-key")
    gateway = ModelGateway(config)

    with respx.mock:
        route = respx.post(
            "https://opencode.ai/zen/go/v1/chat/completions"
        ).mock(
            return_value=Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": "OK"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {},
                },
            )
        )
        result = await gateway.chat("assistant", [{"role": "user", "content": "test"}])

    payload = json.loads(route.calls[0].request.content)
    assert payload["model"] == "ox-alpha-free"
    assert "thinking" not in payload
    assert result["content"] == "OK"
    assert route.calls[0].request.headers["x-opencode-session"].startswith("fundscope-")


@pytest.mark.parametrize("failure", [Response(429, json={"error": "rate limited"}), httpx.RemoteProtocolError("disconnected")])
async def test_model_retries_transient_429(tmp_path: Path, monkeypatch, failure):
    config = tmp_path / "models.yaml"
    config.write_text(
        """
base_url: https://opencode.ai/zen/go/v1
roles:
  assistant:
    model: ox-alpha-free
    temperature: 0
    max_tokens: 128
    timeout: 30
    context_window_tokens: 131072
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "test-key")
    gateway = ModelGateway(config)
    with respx.mock:
        route = respx.post(
            "https://opencode.ai/zen/go/v1/chat/completions"
        ).mock(
            side_effect=[
                failure,
                Response(
                    200,
                    json={
                        "choices": [
                            {
                                "message": {"role": "assistant", "content": "OK"},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {},
                    },
                ),
            ]
        )
        result = await gateway.chat("assistant", [{"role": "user", "content": "test"}])
    assert route.call_count == 2
    assert result["content"] == "OK"


@pytest.mark.parametrize("response_format", [None, {"type": "json_object"}])
async def test_long_report_role_uses_streaming(tmp_path: Path, monkeypatch, response_format):
    config = tmp_path / "models.yaml"
    config.write_text(
        """
base_url: https://opencode.ai/zen/go/v1
roles:
  report:
    model: ox-alpha-free
    reasoning_effort: low
    temperature: 0
    max_tokens: 128
    timeout: 30
    context_window_tokens: 131072
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "test-key")
    log_path = tmp_path / "model_calls.jsonl"
    gateway = ModelGateway(config, log_path=log_path)
    stream = (
        'data: {"choices":[{"delta":{"content":"O"},"finish_reason":null}]}\n\n'
        'data: {"choices":[{"delta":{"content":"K"},"finish_reason":"stop"}]}\n\n'
        "data: [DONE]\n\n"
    )
    with respx.mock:
        route = respx.post(
            "https://opencode.ai/zen/go/v1/chat/completions"
        ).mock(return_value=Response(200, text=stream))
        result = await gateway.chat("report", [{"role": "user", "content": "test"}], response_format=response_format)
    payload = json.loads(route.calls[0].request.content)
    assert payload["stream"] is True
    if response_format:
        assert payload["response_format"] == response_format
    assert payload["reasoning_effort"] == "low"
    assert result["content"] == "OK"
    assert result["finish_reason"] == "stop"
    record = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
    assert record["event"] == "model_call_completed"
    assert record["transport"] == "stream"
    assert record["stream_events"] == 2
    assert record["content_chars"] == 2


async def test_slow_stream_emits_live_thinking_progress(tmp_path: Path, monkeypatch):
    config = tmp_path / "models.yaml"
    config.write_text(
        """
base_url: https://opencode.ai/zen/go/v1
roles:
  report:
    model: ox-alpha-free
    temperature: 0
    max_tokens: 128
    timeout: 30
    context_window_tokens: 131072
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "test-key")
    gateway = ModelGateway(config)
    progress: list[dict] = []

    async def capture(payload: dict):
        progress.append(payload)

    token = gateway.set_progress_callback(capture)
    try:
        with respx.mock:
            respx.post("https://opencode.ai/zen/go/v1/chat/completions").mock(
                return_value=Response(200, stream=SlowReasoningStream())
            )
            result = await gateway.chat(
                "report",
                [{"role": "user", "content": "test"}],
                operation="part9_writing",
            )
    finally:
        gateway.reset_progress_callback(token)

    assert result["content"] == "OK"
    thinking = [item for item in progress if item["phase"] == "thinking"]
    assert thinking
    assert thinking[-1]["operation"] == "part9_writing"
    assert thinking[-1]["stream_events"] == 1
    assert thinking[-1]["reasoning_chars"] == 4
    assert progress[-1]["phase"] == "completed"


async def test_structured_retries_one_invalid_model_response(tmp_path: Path):
    config = tmp_path / "models.yaml"
    config.write_text(
        """
roles:
  evaluator:
    model: ox-alpha-free
    temperature: 0
    max_tokens: 128
    timeout: 30
    context_window_tokens: 131072
""".strip(),
        encoding="utf-8",
    )
    gateway = ModelGateway(config)
    gateway.chat = AsyncMock(
        side_effect=[
            {"content": "not-json"},
            {"content": 'result: {"passed": true, "issues": {}}'},
        ]
    )
    result = await gateway.structured("evaluator", [{"role": "user", "content": "audit"}])
    assert result["passed"] is True
    assert gateway.chat.await_count == 2


def test_report_role_can_extend_only_the_nonstream_structured_timeout(tmp_path: Path):
    config = tmp_path / "models.yaml"
    config.write_text(
        """
roles:
  report:
    model: ox-alpha-free
    temperature: 0
    max_tokens: 128
    timeout: 300
    structured_timeout: 600
    context_window_tokens: 262144
""".strip(),
        encoding="utf-8",
    )

    role = ModelGateway(config).role("report")

    assert role.timeout == 300
    assert role.structured_timeout == 600


async def test_output_length_retries_only_current_call_with_larger_budget(tmp_path,monkeypatch):
    config=tmp_path/'models.yaml'
    config.write_text('roles:\n  report:\n    model: test\n    temperature: 0\n    max_tokens: 128\n    timeout: 30\n    context_window_tokens: 4096\n')
    monkeypatch.setenv('DEEPSEEK_REPORT_API_KEY','test-key')
    gateway=ModelGateway(config)
    limits=[]
    async def stream(payload,*args,**kwargs):
        limits.append(payload['max_tokens'])
        if len(limits)==1:
            raise RuntimeError('model_output_incomplete:length')
        return {'finish_reason':'stop','content':'OK','usage':{},'tool_calls':[]}
    gateway._stream_chat=stream
    result=await gateway.chat('report',[{'role':'user','content':'test'}])
    assert result['content']=='OK' and limits==[128,256]
