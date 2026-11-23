"""Restricted, version-pinned Deep Agents construction shared by chat and experts."""

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
from deepagents.middleware.filesystem import FilesystemMiddleware, FilesystemPermission
from langchain.agents.middleware import AgentMiddleware, ModelRetryMiddleware

from .llm import ModelProviderError, _finish_reason, _is_retryable, _message_text


class ValidateModelResponse(AgentMiddleware):
    async def awrap_model_call(self, request, handler):
        response = await handler(request)
        messages = [m for m in response.result if getattr(m, "type", "") == "ai"]
        if messages:
            message = messages[-1]
            reason = _finish_reason(message)
            if reason == "length":
                raise ModelProviderError(
                    "model_output_incomplete:finish_reason=length", finish_reason=reason
                )
            if (
                not _message_text(message)
                and not message.tool_calls
                and response.structured_response is None
            ):
                raise ModelProviderError(
                    "empty_model_response", retryable=True, finish_reason=reason
                )
        return response


def agent(
    settings,
    model,
    *,
    tools,
    prompt,
    checkpointer=None,
    response_format=None,
    name="industry",
    middleware=(),
    offload_tokens=20000,
):
    # A model-specific profile does not alter other applications or providers.
    register_harness_profile(
        f"openai:{model.model_name}",
        HarnessProfile(
            general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
            excluded_tools=frozenset({"write_file", "edit_file", "delete", "execute", "task"}),
        ),
    )
    backend = CompositeBackend(
        StateBackend(),
        {
            "/skills/": FilesystemBackend(settings.skill_root, virtual_mode=True),
        },
    )
    permissions = [FilesystemPermission(operations=["write"], paths=["/**"], mode="deny")]
    custom = [
        ModelRetryMiddleware(
            max_retries=2,
            retry_on=lambda exc: _is_retryable(exc)[0],
            on_failure="error",
            max_delay=10,
        ),
        ValidateModelResponse(),
        *middleware,
    ]
    if offload_tokens is None:
        # Bounded research packs fit the context. Do not force an expert to
        # recover them through dozens of file reads merely to save tokens.
        custom.append(
            FilesystemMiddleware(
                backend=backend,
                tool_token_limit_before_evict=None,
                _permissions=permissions,
                tools=["ls", "read_file", "glob", "grep"],
            )
        )
    return create_deep_agent(
        model,
        tools=tools,
        system_prompt=prompt,
        backend=backend,
        skills=["/skills/"],
        memory=None,
        subagents=[],
        checkpointer=checkpointer,
        middleware=custom,
        permissions=permissions,
        response_format=response_format,
        name=name,
    )
