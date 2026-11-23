from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fund_research_agent.adapters.model import ModelGateway


@dataclass(frozen=True)
class Settings:
    var: Path
    lake_root: Path
    skill_root: Path
    gateway: ModelGateway = field(repr=False)
    tavily_keys: tuple[str, ...] = field(default=(), repr=False)
    web_mode: str = "auto"
    search_depth: str = "basic"
    max_jobs: int = 1
    segment_concurrency: int = 3

    @property
    def base_url(self) -> str:
        return self.gateway.base_url

    @property
    def model(self) -> str:
        return self.gateway.role("researcher").model
