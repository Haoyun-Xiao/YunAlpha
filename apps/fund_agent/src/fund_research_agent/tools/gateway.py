from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError as PydanticValidationError

from fund_research_agent.domain.contracts import ToolResultEnvelope, ToolStatus
from fund_research_agent.domain.errors import AgentError, PolicyDenied, ToolTimeout
from fund_research_agent.persistence.database import Database, json_text, utc_now
from fund_research_agent.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from fund_research_agent.persistence.artifacts import ArtifactStore


class ToolPolicy:
    def __init__(self, *, read_only: bool = True, trading_enabled: bool = False) -> None:
        self.read_only = read_only
        self.trading_enabled = trading_enabled

    def check(self, tool_name: str, permission: str, *, explicit_report: bool = False) -> None:
        lowered = tool_name.lower()
        if any(token in lowered for token in ("trade", "order", "broker", "account")):
            if not self.trading_enabled:
                raise PolicyDenied("本项目严格限定为研究只读，交易工具被禁用")
        if self.read_only and permission not in {"read", "network", "write_var", "expensive"}:
            raise PolicyDenied(f"只读研究模式不允许权限：{permission}")
        if permission == "expensive" and not explicit_report:
            raise PolicyDenied("报告生成必须由用户明确请求")


class ToolGateway:
    def __init__(
        self,
        registry: ToolRegistry,
        database: Database,
        policy: ToolPolicy,
        artifacts: ArtifactStore | None = None,
    ) -> None:
        self.registry = registry
        self.database = database
        self.policy = policy
        self.artifacts = artifacts

    async def execute(
        self,
        run_id: str,
        name: str,
        arguments: dict[str, Any],
        *,
        skill: str | None = None,
        explicit_report: bool = False,
    ) -> ToolResultEnvelope:
        tool_call_id = f"tool_{uuid.uuid4().hex}"
        started = time.monotonic()
        spec = self.registry.get(name)
        await self.database.add_event(
            run_id, "tool_call_started", {"tool_call_id": tool_call_id, "name": name, "arguments": arguments}
        )
        async with self.database.connection() as connection:
            await connection.execute(
                "INSERT INTO tool_calls(tool_call_id,run_id,tool_name,arguments_json,status,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (tool_call_id, run_id, name, json_text(arguments), "running", utc_now().isoformat()),
            )
            await connection.commit()
        try:
            self.policy.check(name, spec.permission, explicit_report=explicit_report)
            if spec.allowed_skills and skill not in spec.allowed_skills:
                raise PolicyDenied(f"Skill {skill!r} 不允许调用 {name}")
            parsed = spec.input_model.model_validate(arguments)
            result = await self._run_with_retry(spec, parsed)
            envelope = result if isinstance(result, ToolResultEnvelope) else ToolResultEnvelope(
                status=ToolStatus.SUCCESS, data=result
            )
            encoded = json_text(envelope.model_dump(mode="json"))
            if len(encoded) > spec.result_size_limit:
                artifact_id = None
                if self.artifacts is not None:
                    artifact = await self.artifacts.put_json(
                        "tool_result",
                        envelope.data,
                        metadata={"run_id": run_id, "tool_name": name},
                        job_id=run_id,
                    )
                    artifact_id = artifact.artifact_id
                    envelope.artifact_ref = artifact_id
                envelope.data = {
                    "summary": "工具结果过大，完整结果已转存 Artifact；请缩小查询范围或按需读取。",
                    "artifact_id": artifact_id,
                    "encoded_characters": len(encoded),
                }
                envelope.truncated = True
                envelope.quality_flags.append("result_size_limit")
            envelope.duration_ms = int((time.monotonic() - started) * 1000)
            await self._finish(tool_call_id, "success", envelope, None)
            await self.database.add_event(
                run_id,
                "tool_call_finished",
                {"tool_call_id": tool_call_id, "name": name, "result": envelope.model_dump(mode="json")},
            )
            evidence_id = await self._record_evidence(run_id, name, arguments, envelope)
            envelope.quality_flags.append(f"evidence:{evidence_id}")
            return envelope
        except PydanticValidationError as exc:
            envelope = self._error("validation_error", str(exc), False, started)
        except TimeoutError:
            envelope = self._error(ToolTimeout.code, "工具执行超时", True, started)
        except AgentError as exc:
            envelope = self._error(exc.code, str(exc), exc.retryable, started)
        except Exception as exc:
            envelope = self._error("tool_failure", str(exc), False, started)
        await self._finish(tool_call_id, "error", envelope, envelope.error_code)
        await self.database.add_event(
            run_id,
            "tool_error",
            {"tool_call_id": tool_call_id, "name": name, "error": envelope.model_dump(mode="json")},
        )
        return envelope

    async def _run_with_retry(self, spec, parsed):
        last_error = None
        for attempt in range(spec.max_retries + 1):
            try:
                return await asyncio.wait_for(spec.handler(parsed), timeout=spec.timeout_seconds)
            except AgentError as exc:
                last_error = exc
                if not exc.retryable or attempt >= spec.max_retries:
                    raise
            except (TimeoutError, ConnectionError) as exc:
                last_error = exc
                if attempt >= spec.max_retries:
                    raise
            await asyncio.sleep(min(2**attempt, 4))
        raise last_error  # pragma: no cover

    @staticmethod
    def _error(code: str, message: str, retryable: bool, started: float) -> ToolResultEnvelope:
        return ToolResultEnvelope(
            status=ToolStatus.ERROR,
            error_code=code,
            error_message=message,
            retryable=retryable,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    async def _finish(
        self,
        tool_call_id: str,
        status: str,
        envelope: ToolResultEnvelope,
        error_code: str | None,
    ) -> None:
        async with self.database.connection() as connection:
            await connection.execute(
                "UPDATE tool_calls SET status=?,result_json=?,error_code=?,duration_ms=?,finished_at=? "
                "WHERE tool_call_id=?",
                (
                    status,
                    envelope.model_dump_json(),
                    error_code,
                    envelope.duration_ms,
                    utc_now().isoformat(),
                    tool_call_id,
                ),
            )
            await connection.commit()

    async def _record_evidence(
        self,
        run_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        envelope: ToolResultEnvelope,
    ) -> str:
        evidence_id = f"evidence_{uuid.uuid4().hex}"
        excerpt = json_text(envelope.data)[:2000]
        source_hash = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
        fund_symbol = arguments.get("fund_symbol") or arguments.get("fund_query")
        async with self.database.connection() as connection:
            await connection.execute(
                "INSERT INTO evidence(evidence_id,source_type,source_uri,source_hash,excerpt,"
                "fact_type,confidence,fund_symbol,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    evidence_id,
                    "tool_result",
                    f"{tool_name}#{run_id}",
                    source_hash,
                    excerpt,
                    "source_fact",
                    1.0,
                    fund_symbol,
                    utc_now().isoformat(),
                ),
            )
            await connection.commit()
        await self.database.add_event(
            run_id,
            "evidence_added",
            {"evidence_id": evidence_id, "source": tool_name, "fund_symbol": fund_symbol},
        )
        return evidence_id
