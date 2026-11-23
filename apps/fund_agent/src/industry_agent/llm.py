"""Model access for the industry-chain research application.

The module intentionally keeps provider-specific code in one place.  Research
code receives a ``ModelService`` and never reads an API key or constructs a
provider client itself.  OpenCode exposes an OpenAI-compatible endpoint, so
``ChatOpenAI`` is used as the transport while the service owns validation,
retry and progress semantics.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from .config import Settings

try:  # Imported lazily by ``model`` as well, but these types help callers.
    from langchain_core.language_models.chat_models import BaseChatModel
except ImportError:  # pragma: no cover - dependency doctor reports this case
    BaseChatModel = Any  # type: ignore[misc,assignment]


ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]
T = TypeVar("T", bound=BaseModel)


class ModelProviderError(RuntimeError):
    """A visible provider failure; callers must not treat it as empty output."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
        finish_reason: str | None = None,
    ):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code
        self.finish_reason = finish_reason


@dataclass
class _InvocationResult:
    """Visible streamed message plus facts about real stream activity."""

    message: Any
    progress: dict[str, Any]


def _message_text(message: Any) -> str:
    """Extract visible text from an AIMessage without exposing reasoning blocks."""

    if isinstance(message, _InvocationResult):
        message = message.message
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if not isinstance(content, Sequence):
        return str(content or "")
    chunks: list[str] = []
    for block in content:
        if isinstance(block, str):
            chunks.append(block)
        elif isinstance(block, dict) and block.get("type") in {"text", "output_text"}:
            value = block.get("text", block.get("content", ""))
            if value:
                chunks.append(str(value))
    return "".join(chunks)


def _extract_json(value: str) -> Any:
    """Parse the common JSON shapes returned by OpenAI-compatible models."""

    text = value.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Some compatible endpoints put a short explanation around the object.
        # Only scan balanced-looking outer objects; do not attempt to repair
        # arbitrary JSON with model-generated values.
        starts = [index for index, char in enumerate(text) if char in "[{]"]
        for start in starts:
            opener = text[start]
            closer = "}" if opener == "{" else "]"
            end = text.rfind(closer)
            if end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    continue
        raise


def _is_retryable(exc: BaseException) -> tuple[bool, int | None]:
    if isinstance(exc, ModelProviderError):
        return exc.retryable, exc.status_code
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    name = type(exc).__name__.lower()
    retryable = (isinstance(status, int) and (status == 429 or status >= 500)) or any(
        token in name for token in ("timeout", "connection", "network", "ratelimit")
    )
    return retryable, status if isinstance(status, int) else None


def _finish_reason(message: Any) -> str | None:
    if isinstance(message, _InvocationResult):
        if message.progress.get("finish_reason"):
            return str(message.progress["finish_reason"])
        message = message.message
    metadata = getattr(message, "response_metadata", {}) or {}
    if isinstance(metadata, dict):
        reason = metadata.get("finish_reason") or metadata.get("finishReason")
        if reason:
            return str(reason)
    return None


class ModelService:
    """OpenCode-backed model service with structured validation and progress."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._models: dict[str, BaseChatModel] = {}
        self._var = settings.var.resolve()
        self._log_path = self._var / "logs" / "model-calls.jsonl"

    def _gateway_role(self, role: str) -> str:
        name = f"industry_{role}"
        if name in self.settings.gateway.roles:
            return name
        return "report" if role == "synthesis" else "researcher"

    def _role_config(self, role: str) -> dict[str, Any]:
        name = self._gateway_role(role)
        config = self.settings.gateway.role(name)
        # Keep the source workflow's output and inactivity budgets by default.
        # An explicit industry role can override them in the shared gateway.
        dedicated = name.startswith("industry_")
        return {
            "name": name,
            "model": config.model,
            "context_window_tokens": config.context_window_tokens,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens if dedicated else 32768,
            "timeout": config.timeout if dedicated else 300,
            "thinking": config.thinking if dedicated or role == "synthesis" else "disabled",
            "reasoning_effort": config.reasoning_effort
            if dedicated or role == "synthesis"
            else None,
        }

    def fingerprint(self) -> dict[str, Any]:
        return {role: self._role_config(role) for role in ("structure", "researcher", "synthesis")}

    def model(self, role: str = "researcher") -> BaseChatModel:
        """Return a cached ChatOpenAI client for a research role."""

        if role in self._models:
            return self._models[role]
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:  # pragma: no cover - environment failure
            raise ModelProviderError(
                "缺少 langchain-openai；请先安装项目依赖", retryable=False
            ) from exc
        cfg = self._role_config(role)
        api_key = self.settings.gateway.api_key_for(cfg["name"])
        if not api_key:
            raise ModelProviderError("未配置产业链研究模型 API Key")
        kwargs: dict[str, Any] = {
            "model": cfg["model"],
            "api_key": api_key,
            "profile": {"max_input_tokens": cfg["context_window_tokens"]},
            "base_url": self.settings.base_url.rstrip("/"),
            "temperature": cfg["temperature"],
            "max_tokens": cfg["max_tokens"],
            "timeout": cfg["timeout"],
            "max_retries": 0,  # service-level retry has visible attempts
            "stream_usage": True,
        }
        if cfg.get("reasoning_effort"):
            kwargs["reasoning_effort"] = cfg["reasoning_effort"]
        if cfg.get("thinking"):
            # OpenCode's thinking switch is an OpenAI-compatible extension;
            # ChatOpenAI forwards extra_body without changing the provider.
            kwargs["extra_body"] = {"thinking": {"type": cfg["thinking"]}}
        if self.settings.base_url.startswith("https://opencode.ai/"):
            kwargs["default_headers"] = {
                "x-opencode-session": self.settings.gateway.opencode_session
            }
        try:
            client = ChatOpenAI(**kwargs)
        except Exception as exc:  # pragma: no cover - constructor varies by SDK
            raise ModelProviderError(f"模型客户端初始化失败：{type(exc).__name__}") from exc
        self._models[role] = client
        return client

    async def _emit(self, callback: ProgressCallback | None, payload: dict[str, Any]) -> None:
        if callback is None:
            return
        try:
            await callback(payload)
        except Exception:
            # Telemetry must never abort a provider request.
            return

    def _record(self, payload: dict[str, Any]) -> None:
        try:
            path = self._log_path.resolve()
            if self._var not in path.parents:
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8") if not path.exists() else None
            with path.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        {"recorded_at": datetime.now(UTC).isoformat(), **payload},
                        ensure_ascii=False,
                        default=str,
                    )
                    + "\n"
                )
        except OSError:
            return

    @staticmethod
    def _usage(message: Any) -> dict[str, Any]:
        if isinstance(message, _InvocationResult):
            message = message.message
        usage = getattr(message, "usage_metadata", None) or getattr(
            message, "response_metadata", {}
        ).get("token_usage", {})
        if not isinstance(usage, dict):
            return {}
        keys = {
            "input_tokens": "input_tokens",
            "prompt_tokens": "input_tokens",
            "output_tokens": "output_tokens",
            "completion_tokens": "output_tokens",
            "total_tokens": "total_tokens",
            "cached_tokens": "cached_tokens",
        }
        result: dict[str, Any] = {}
        for source, target in keys.items():
            if source in usage and target not in result:
                result[target] = usage[source]
        return result

    async def _invoke(
        self,
        role: str,
        messages: list[tuple[str, str]],
        *,
        operation: str,
        callback: ProgressCallback | None,
    ) -> Any:
        client = self.model(role)
        started = time.monotonic()
        state: dict[str, Any] = {
            "content_chars": 0,
            "stream_events": 0,
            "first_event_ms": None,
            "last_activity": None,
            "last_activity_wall": None,
            "finish_reason": None,
            "phase": "waiting_response",
        }
        await self._emit(
            callback,
            {
                "operation": operation,
                "phase": "request_started",
                "elapsed_seconds": 0.0,
                "last_activity": None,
                "last_activity_seconds": None,
                "content_chars": 0,
                "stream_events": 0,
            },
        )

        async def ticker() -> None:
            while True:
                await asyncio.sleep(2)
                elapsed = round(time.monotonic() - started, 1)
                await self._emit(
                    callback,
                    {
                        "operation": operation,
                        "phase": state["phase"],
                        "elapsed_seconds": elapsed,
                        "last_activity": state["last_activity_wall"],
                        "last_activity_seconds": (
                            round(time.monotonic() - float(state["last_activity"]), 1)
                            if state["last_activity"] is not None
                            else None
                        ),
                        "content_chars": state["content_chars"],
                        "stream_events": state["stream_events"],
                        "first_event_ms": state["first_event_ms"],
                    },
                )

        ticker_task = asyncio.create_task(ticker())
        try:
            chunks: list[Any] = []
            aggregated: Any = None
            async for chunk in client.astream(messages):
                now = time.monotonic()
                visible = _message_text(chunk)
                state["stream_events"] += 1
                state["last_activity"] = now
                state["last_activity_wall"] = datetime.now(UTC).isoformat()
                if state["first_event_ms"] is None:
                    state["first_event_ms"] = round((now - started) * 1000, 1)
                state["content_chars"] += len(visible)
                state["phase"] = "writing" if visible else self._empty_chunk_phase(role)
                metadata = getattr(chunk, "response_metadata", {}) or {}
                if isinstance(metadata, dict) and metadata.get("finish_reason"):
                    state["finish_reason"] = metadata["finish_reason"]
                await self._emit(
                    callback,
                    {
                        "operation": operation,
                        "phase": state["phase"],
                        "elapsed_seconds": round(now - started, 1),
                        "last_activity": state["last_activity_wall"],
                        "last_activity_seconds": 0.0,
                        "content_chars": state["content_chars"],
                        "stream_events": state["stream_events"],
                        "first_event_ms": state["first_event_ms"],
                    },
                )
                chunks.append(chunk)
                if aggregated is None:
                    aggregated = chunk
                else:
                    try:
                        aggregated = aggregated + chunk
                    except TypeError:
                        # A test/future adapter may return a plain chunk type.
                        # Preserve visible text and the last chunk's metadata.
                        aggregated = None
            if aggregated is None:
                text = "".join(_message_text(chunk) for chunk in chunks)
                latest = chunks[-1] if chunks else type("Message", (), {"content": ""})()
                aggregated = type(
                    "StreamMessage",
                    (),
                    {
                        "content": text,
                        "usage_metadata": getattr(latest, "usage_metadata", None),
                        "response_metadata": getattr(latest, "response_metadata", {}),
                    },
                )()
            return _InvocationResult(aggregated, dict(state))
        except Exception as exc:
            retryable, status = _is_retryable(exc)
            raise ModelProviderError(
                f"模型调用失败：{type(exc).__name__}", retryable=retryable, status_code=status
            ) from exc
        finally:
            ticker_task.cancel()
            await asyncio.gather(ticker_task, return_exceptions=True)

    @staticmethod
    def _role_for_operation(operation: str) -> str:
        lowered = operation.lower()
        return (
            "synthesis"
            if any(
                marker in lowered for marker in ("synthesis", "final_report", "aggregate", "report")
            )
            else ("structure" if "structure" in lowered else "researcher")
        )

    def _empty_chunk_phase(self, role: str) -> str:
        """Name an empty stream chunk without claiming hidden reasoning."""

        config = self._role_config(role)
        if config.get("reasoning_effort"):
            return "thinking"
        thinking = config.get("thinking")
        if thinking and str(thinking).lower() != "disabled":
            return "thinking"
        return "processing"

    async def _invoke_with_retry(
        self,
        role: str,
        messages: list[tuple[str, str]],
        *,
        operation: str,
        callback: ProgressCallback | None,
    ) -> Any:
        """Retry only transport/provider failures, never a bad business result."""

        for attempt in range(1, 4):
            try:
                return await self._invoke(role, messages, operation=operation, callback=callback)
            except ModelProviderError as exc:
                if not exc.retryable or attempt >= 3:
                    raise
                await asyncio.sleep(0.5 * attempt)
        raise AssertionError("unreachable")

    async def complete(
        self,
        operation: str,
        system: str,
        user: str,
        schema: type[T] | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any] | str:
        """Complete a call and validate structured results.

        A schema request gets one bounded correction attempt.  This is enough
        to repair a missing quote or wrapper without turning malformed model
        output into an unbounded loop.
        """

        schema_directive = ""
        if schema is not None:
            schema_directive = (
                "\n\n严格输出约束：只返回一个符合以下 JSON Schema 的 JSON 对象，"
                "不要 Markdown、解释或思考内容。Schema:\n"
                + json.dumps(schema.model_json_schema(), ensure_ascii=False)
            )
        messages = [("system", system), ("user", user + schema_directive)]
        started = time.monotonic()
        last_error: Exception | None = None
        role = self._role_for_operation(operation)
        for attempt in range(1, 3 if schema else 2):
            if attempt == 2 and schema is not None:
                messages = [
                    ("system", system),
                    (
                        "user",
                        user
                        + schema_directive
                        + "\n\n上一次返回未通过结构校验，请重新生成符合约束的 JSON。",
                    ),
                ]
            try:
                response = await self._invoke_with_retry(
                    role, messages, operation=operation, callback=on_progress
                )
                text = _message_text(response)
                finish_reason = _finish_reason(response)
                if finish_reason == "length":
                    raise ModelProviderError(
                        f"模型输出被截断：operation={operation}, finish_reason=length",
                        retryable=False,
                        finish_reason="length",
                    )
                if schema is None:
                    result: dict[str, Any] | str = text
                else:
                    parsed = _extract_json(text)
                    validated = schema.model_validate(parsed)
                    result = validated.model_dump(mode="json")
                self._record(
                    {
                        "operation": operation,
                        "provider": self.settings.base_url,
                        "model": self._role_config(role)["model"],
                        "role": role,
                        "max_tokens": self._role_config(role)["max_tokens"],
                        "reasoning_effort": self._role_config(role).get("reasoning_effort"),
                        "thinking": self._role_config(role).get("thinking"),
                        "attempt": attempt,
                        "duration_seconds": round(time.monotonic() - started, 3),
                        "usage": self._usage(response),
                        "status": "success",
                        "content_chars": len(text),
                    }
                )
                await self._emit(
                    on_progress,
                    {
                        "operation": operation,
                        "phase": "completed",
                        "elapsed_seconds": round(time.monotonic() - started, 1),
                        "last_activity": (
                            response.progress.get("last_activity_wall")
                            if isinstance(response, _InvocationResult)
                            else None
                        ),
                        "last_activity_seconds": (
                            round(time.monotonic() - float(response.progress["last_activity"]), 1)
                            if isinstance(response, _InvocationResult)
                            and response.progress.get("last_activity") is not None
                            else None
                        ),
                        "content_chars": len(text),
                    },
                )
                return result
            except (ValidationError, json.JSONDecodeError) as exc:
                last_error = exc
                if schema is None or attempt >= 2:
                    break
            except ModelProviderError as exc:
                last_error = exc
                break
        self._record(
            {
                "operation": operation,
                "provider": self.settings.base_url,
                "model": self._role_config(role)["model"],
                "role": role,
                "max_tokens": self._role_config(role)["max_tokens"],
                "reasoning_effort": self._role_config(role).get("reasoning_effort"),
                "thinking": self._role_config(role).get("thinking"),
                "duration_seconds": round(time.monotonic() - started, 3),
                "status": "error",
                "error_type": type(last_error).__name__ if last_error else "unknown",
                "finish_reason": getattr(last_error, "finish_reason", None),
            }
        )
        if isinstance(last_error, ModelProviderError):
            raise last_error
        raise ModelProviderError(
            f"结构化模型输出未通过校验：{type(last_error).__name__ if last_error else 'unknown'}"
        ) from last_error
