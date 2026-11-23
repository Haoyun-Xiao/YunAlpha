from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml

from fund_research_agent.paths import WriteBoundary

ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True)
class ModelRole:
    name: str
    model: str
    temperature: float
    max_tokens: int
    timeout: int
    context_window_tokens: int
    structured_timeout: int | None = None
    thinking: str | None = None
    reasoning_effort: str | None = None


class ModelGateway:
    def __init__(
        self,
        config_path: Path,
        replay_root: Path | None = None,
        boundary: WriteBoundary | None = None,
        log_path: Path | None = None,
    ) -> None:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        self.base_url = str(raw.get("base_url", "https://api.deepseek.com")).rstrip("/")
        # OpenCode Go requires a stable client session header for routing and
        # prompt caching.  Keep it stable for the lifetime of this gateway
        # (the API and report worker each own one gateway instance).
        self.opencode_session = os.getenv("OPENCODE_SESSION_ID") or f"fundscope-{uuid.uuid4().hex}"
        self.roles = {
            name: ModelRole(name=name, **config) for name, config in raw.get("roles", {}).items()
        }
        self.replay_root = boundary.ensure_dir(replay_root) if replay_root and boundary else replay_root
        self.boundary = boundary
        self.log_path = boundary.assert_writable(log_path) if log_path and boundary else log_path
        self._progress_callback: ContextVar[ProgressCallback | None] = ContextVar(
            "model_progress_callback", default=None
        )
        self._session_id: ContextVar[str | None] = ContextVar(
            "model_session_id", default=None
        )

    def set_progress_callback(self, callback: ProgressCallback) -> Token:
        return self._progress_callback.set(callback)

    def reset_progress_callback(self, token: Token) -> None:
        self._progress_callback.reset(token)

    def set_session_id(self, session_id: str) -> Token:
        """Set the provider session for the current async task."""
        return self._session_id.set(session_id)

    def reset_session_id(self, token: Token) -> None:
        self._session_id.reset(token)

    async def _emit_progress(self, payload: dict[str, Any]) -> None:
        callback = self._progress_callback.get()
        if callback is not None:
            try:
                await callback(payload)
            except Exception:
                # Observability must never abort the underlying model request.
                return

    async def _progress_ticker(
        self,
        *,
        call_id: str,
        role_name: str,
        operation: str,
        started: float,
        state: dict[str, Any],
    ) -> None:
        while True:
            await asyncio.sleep(2)
            now = time.monotonic()
            content_chars = int(state["content_chars"])
            reasoning_chars = int(state["reasoning_chars"])
            if content_chars:
                phase = "writing"
            elif reasoning_chars or state["stream_events"]:
                phase = "thinking"
            else:
                phase = "waiting_response"
            last_activity = state.get("last_activity")
            await self._emit_progress(
                {
                    "call_id": call_id,
                    "role": role_name,
                    "operation": operation,
                    "phase": phase,
                    "attempt": state["attempt"],
                    "elapsed_seconds": round(now - started, 1),
                    "first_event_ms": state.get("first_event_ms"),
                    "last_activity_seconds": (
                        round(now - float(last_activity), 1)
                        if last_activity is not None
                        else None
                    ),
                    "stream_events": state["stream_events"],
                    "reasoning_chars": reasoning_chars,
                    "content_chars": content_chars,
                }
            )

    def _record_call(self, payload: dict[str, Any]) -> None:
        if self.log_path is None:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        record = {"recorded_at": datetime.now(UTC).isoformat(), **payload}
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    @property
    def api_key(self) -> str:
        return self.api_key_for("assistant")

    def api_key_for(self, role_name: str) -> str:
        if self.base_url.startswith("https://opencode.ai/"):
            return os.getenv("OPENCODE_GO_API_KEY") or ""
        if role_name in {"report", "handbook"}:
            return os.getenv("DEEPSEEK_REPORT_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or ""
        return os.getenv("DEEPSEEK_ASSISTANT_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or ""

    def role(self, name: str) -> ModelRole:
        return self.roles[name]

    def descriptor(self, role_name: str) -> dict[str, str]:
        role = self.role(role_name)
        return {"provider": self.base_url, "model": role.model, "role": role_name}

    async def chat(
        self,
        role_name: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        max_tokens: int | None = None,
        operation: str | None = None,
    ) -> dict[str, Any]:
        role = self.role(role_name)
        if role_name in {"report", "handbook", "evaluator"}:
            from fund_research_agent.runtime.context import estimate_tokens

            required = sum(estimate_tokens(json.dumps(message, ensure_ascii=False)) for message in messages)
            required += max_tokens if max_tokens is not None else role.max_tokens
            if required > role.context_window_tokens:
                raise ValueError(f"context_overflow:{operation or role_name}:{required}>{role.context_window_tokens}")
        available_output_tokens = role.context_window_tokens - sum(estimate_tokens(json.dumps(message, ensure_ascii=False)) for message in messages) if role_name in {"report", "handbook", "evaluator"} else role.max_tokens
        call_id = f"model_{uuid.uuid4().hex}"
        call_started = time.monotonic()
        operation = operation or role_name
        replay_path = self._replay_path(
            role_name, messages, tools, response_format, tool_choice, max_tokens
        )
        mode = os.getenv("FUND_AGENT_MODEL_MODE", "auto").lower()
        if mode in {"auto", "replay"} and replay_path and replay_path.exists():
            recorded = json.loads(replay_path.read_text(encoding="utf-8"))
            if recorded.get("finish_reason") not in {"stop", "tool_calls"}:
                raise RuntimeError("model_replay_incomplete")
            return recorded
        if mode == "replay":
            raise RuntimeError(f"未找到模型回放：{replay_path.name if replay_path else role_name}")
        api_key = self.api_key_for(role_name)
        if not api_key:
            raise RuntimeError("缺少模型 API Key；请设置新项目环境变量")
        payload: dict[str, Any] = {
            "model": role.model,
            "messages": messages,
            "temperature": role.temperature,
            "max_tokens": max_tokens if max_tokens is not None else role.max_tokens,
        }
        if role.thinking:
            payload["thinking"] = {"type": role.thinking}
        if role.reasoning_effort:
            payload["reasoning_effort"] = role.reasoning_effort
        if tools:
            payload["tools"] = tools
        if response_format:
            payload["response_format"] = response_format
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        response: httpx.Response | None = None
        streamed_result: dict[str, Any] | None = None
        use_stream = role_name in {"handbook", "report", "evaluator"} and not tools
        # Evaluation is advisory after deterministic validation. Let it degrade to a
        # warning quickly instead of holding the entire report in a retry loop.
        max_attempts = 2 if role_name == "evaluator" else 3
        for attempt in range(max_attempts):
            progress_state: dict[str, Any] = {
                "attempt": attempt + 1,
                "stream_events": 0,
                "reasoning_chars": 0,
                "content_chars": 0,
                "first_event_ms": None,
                "last_activity": None,
            }
            await self._emit_progress(
                {
                    "call_id": call_id,
                    "role": role_name,
                    "operation": operation,
                    "phase": "request_started",
                    "attempt": attempt + 1,
                    "elapsed_seconds": round(time.monotonic() - call_started, 1),
                    "stream_events": 0,
                    "reasoning_chars": 0,
                    "content_chars": 0,
                }
            )
            ticker = asyncio.create_task(
                self._progress_ticker(
                    call_id=call_id,
                    role_name=role_name,
                    operation=operation,
                    started=call_started,
                    state=progress_state,
                )
            )
            try:
                if use_stream:
                    streamed_result = await self._stream_chat(
                        payload,
                        api_key,
                        role.timeout,
                        call_id=call_id,
                        role_name=role_name,
                        attempt=attempt + 1,
                        progress_state=progress_state,
                    )
                else:
                    request_timeout = role.structured_timeout or role.timeout
                    async with httpx.AsyncClient(timeout=request_timeout) as client:
                        response = await client.post(
                            f"{self.base_url}/chat/completions",
                            headers=self._request_headers(api_key),
                            json=payload,
                        )
                        response.raise_for_status()
                break
            except (TimeoutError, httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                self._record_call(
                    {
                        "event": "model_call_attempt_failed",
                        "call_id": call_id,
                        "role": role_name,
                        "model": role.model,
                        "transport": "stream" if use_stream else "json",
                        "attempt": attempt + 1,
                        "elapsed_ms": round((time.monotonic() - call_started) * 1000),
                        "error_type": type(exc).__name__,
                        "retryable": attempt < max_attempts - 1,
                    }
                )
                if attempt == max_attempts - 1:
                    raise
            except RuntimeError as exc:
                old_limit = int(payload["max_tokens"])
                new_limit = min(old_limit * 2, 64000, available_output_tokens)
                if not str(exc).startswith("model_output_incomplete:length") or role_name not in {"report", "handbook", "evaluator"} or attempt == max_attempts - 1 or new_limit <= old_limit:
                    raise RuntimeError(f"{exc}:{operation}") from exc
                payload["max_tokens"] = new_limit
                self._record_call({"event": "model_output_limit_retry", "call_id": call_id, "role": role_name, "operation": operation, "attempt": attempt + 1, "previous_max_tokens": old_limit, "max_tokens": new_limit})
                await self._emit_progress({"call_id": call_id, "role": role_name, "operation": operation, "phase": "output_limit_retry", "attempt": attempt + 1, "max_tokens": new_limit})
            except httpx.HTTPStatusError as exc:
                self._record_call(
                    {
                        "event": "model_call_attempt_failed",
                        "call_id": call_id,
                        "role": role_name,
                        "model": role.model,
                        "transport": "stream" if use_stream else "json",
                        "attempt": attempt + 1,
                        "elapsed_ms": round((time.monotonic() - call_started) * 1000),
                        "error_type": "HTTPStatusError",
                        "status_code": exc.response.status_code,
                        "retryable": attempt < max_attempts - 1,
                    }
                )
                if attempt == max_attempts - 1 or (
                    exc.response.status_code != 429
                    and exc.response.status_code < 500
                ):
                    raise
            finally:
                ticker.cancel()
                await asyncio.gather(ticker, return_exceptions=True)
            await asyncio.sleep(2.0 * (2**attempt))
        if streamed_result is not None:
            result = streamed_result
        else:
            assert response is not None
            data = response.json()
            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            result = {
                "message": message,
                "content": message.get("content") or "",
                "tool_calls": message.get("tool_calls") or [],
                "finish_reason": choice.get("finish_reason") or "",
                "usage": data.get("usage") or {},
            }
            self._record_call(
                {
                    "event": "model_call_completed",
                    "call_id": call_id,
                    "role": role_name,
                    "model": role.model,
                    "transport": "json",
                    "attempt": attempt + 1,
                    "elapsed_ms": round((time.monotonic() - call_started) * 1000),
                    "content_chars": len(result["content"]),
                }
            )
        if result.get("finish_reason") not in {"stop", "tool_calls"}:
            raise RuntimeError(f"model_output_incomplete:{result.get('finish_reason')}")
        if replay_path and os.getenv("FUND_AGENT_RECORD_MODEL_REPLAY") == "1":
            assert self.boundary is not None
            self.boundary.write_text(
                replay_path, json.dumps(result, ensure_ascii=False, indent=2) + "\n"
            )
        await self._emit_progress(
            {
                "call_id": call_id,
                "role": role_name,
                "operation": operation,
                "phase": "completed",
                "attempt": attempt + 1,
                "elapsed_seconds": round(time.monotonic() - call_started, 1),
                "stream_events": progress_state["stream_events"],
                "reasoning_chars": progress_state["reasoning_chars"],
                "content_chars": len(result["content"]),
            }
        )
        return result

    async def _stream_chat(
        self,
        payload: dict[str, Any],
        api_key: str,
        timeout: int,
        *,
        call_id: str,
        role_name: str,
        attempt: int,
        progress_state: dict[str, Any],
    ) -> dict[str, Any]:
        content: list[str] = []
        finish_reason = ""
        usage: dict[str, Any] = {}
        event_count = 0
        reasoning_chars = 0
        first_event_ms: int | None = None
        started = time.monotonic()
        stream_payload = payload | {"stream": True}
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers=self._request_headers(api_key),
                    json=stream_payload,
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        raw = line.removeprefix("data:").strip()
                        if not raw or raw == "[DONE]":
                            continue
                        event = json.loads(raw)
                        event_count += 1
                        progress_state["stream_events"] = event_count
                        progress_state["last_activity"] = time.monotonic()
                        if first_event_ms is None:
                            first_event_ms = round((time.monotonic() - started) * 1000)
                            progress_state["first_event_ms"] = first_event_ms
                        usage.update(event.get("usage") or {})
                        choice = (event.get("choices") or [{}])[0]
                        delta = choice.get("delta") or {}
                        reasoning_chars += len(str(delta.get("reasoning_content") or ""))
                        progress_state["reasoning_chars"] = reasoning_chars
                        if delta.get("content"):
                            content.append(str(delta["content"]))
                            progress_state["content_chars"] = sum(len(item) for item in content)
                        if choice.get("finish_reason"):
                            finish_reason = str(choice["finish_reason"])
        except (Exception, asyncio.CancelledError) as exc:
            self._record_call(
                {
                    "event": "model_stream_interrupted",
                    "call_id": call_id,
                    "role": role_name,
                    "model": str(payload.get("model") or ""),
                    "transport": "stream",
                    "attempt": attempt,
                    "elapsed_ms": round((time.monotonic() - started) * 1000),
                    "first_event_ms": first_event_ms,
                    "stream_events": event_count,
                    "reasoning_chars": reasoning_chars,
                    "content_chars": sum(len(item) for item in content),
                    "error_type": type(exc).__name__,
                    "had_stream_progress": event_count > 0,
                }
            )
            raise
        if finish_reason not in {"stop", "tool_calls"}:
            if not finish_reason:
                raise httpx.RemoteProtocolError("model_stream_ended_without_finish_reason")
            self._record_call({"event": "model_output_incomplete", "call_id": call_id, "role": role_name, "finish_reason": finish_reason, "reasoning_chars": reasoning_chars, "content_chars": sum(len(item) for item in content)})
            raise RuntimeError(f"model_output_incomplete:{finish_reason}")
        text = "".join(content)
        self._record_call(
            {
                "event": "model_call_completed",
                "call_id": call_id,
                "role": role_name,
                "model": str(payload.get("model") or ""),
                "transport": "stream",
                "attempt": attempt,
                "elapsed_ms": round((time.monotonic() - started) * 1000),
                "first_event_ms": first_event_ms,
                "stream_events": event_count,
                "reasoning_chars": reasoning_chars,
                "content_chars": len(text),
                "finish_reason": finish_reason,
            }
        )
        return {
            "message": {"role": "assistant", "content": text},
            "content": text,
            "tool_calls": [],
            "finish_reason": finish_reason,
            "usage": usage,
        }

    def _request_headers(self, api_key: str) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "FundScopeAgent/1.0",
        }
        if self.base_url.startswith("https://opencode.ai/"):
            headers["x-opencode-session"] = self._session_id.get() or self.opencode_session
        return headers

    def _replay_path(
        self,
        role_name: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        response_format: dict[str, Any] | None,
        tool_choice: str | dict[str, Any] | None,
        max_tokens: int | None,
    ) -> Path | None:
        if self.replay_root is None:
            return None
        payload = json.dumps(
            {
                "role": role_name,
                "provider": self.base_url,
                "configuration": __import__("dataclasses").asdict(self.role(role_name)),
                "replay_version": 2,
                "messages": messages,
                "tools": tools,
                "response_format": response_format,
                "tool_choice": tool_choice,
                "max_tokens": max_tokens,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return self.replay_root / role_name / f"{digest}.json"

    async def structured(
        self,
        role_name: str,
        messages: list[dict[str, Any]],
        *,
        operation: str | None = None,
    ) -> dict[str, Any]:
        last_error: json.JSONDecodeError | None = None
        decoder = json.JSONDecoder(strict=False)
        for _attempt in range(2):
            result = await self.chat(
                role_name,
                messages,
                response_format={"type": "json_object"},
                operation=operation,
            )
            text = result["content"].strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
            try:
                start = text.find("{")
                value, end = decoder.raw_decode(text[max(0, start):])
                if text[max(0, start) + end:].strip():
                    raise json.JSONDecodeError("trailing JSON content", text, max(0, start) + end)
                if not isinstance(value, dict):
                    raise json.JSONDecodeError("expected JSON object", text, 0)
                return value
            except json.JSONDecodeError as exc:
                last_error = exc
        assert last_error is not None
        raise last_error
