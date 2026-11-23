from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

ToolHandler = Callable[[BaseModel], Awaitable[Any]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: ToolHandler
    risk_level: Literal["low", "medium", "high"] = "low"
    allowed_skills: tuple[str, ...] = ()
    timeout_seconds: int = 30
    max_retries: int = 0
    result_size_limit: int = 80_000
    permission: Literal["read", "network", "write_var", "expensive"] = "read"

    def openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name.replace(".", "__"),
                "description": self.description,
                "parameters": self.input_model.model_json_schema(),
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"工具重复注册：{spec.name}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        if name not in self._tools:
            raise KeyError(f"未知工具：{name}")
        return self._tools[name]

    def schemas(
        self,
        skill: str | None = None,
        names: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        allowed_names = set(names) if names is not None else None
        return [
            spec.openai_schema()
            for spec in self._tools.values()
            if (not spec.allowed_skills or skill in spec.allowed_skills)
            and (allowed_names is None or spec.name in allowed_names)
        ]

    def names(self) -> list[str]:
        return sorted(self._tools)

    @staticmethod
    def from_wire_name(name: str) -> str:
        return name.replace("__", ".")
