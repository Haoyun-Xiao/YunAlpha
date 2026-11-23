from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LEGACY_ROOT = Path("/path/to/local-user/项目/多资产智能投研平台-v2/lake")
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")


def _load_local_environment(project_root: Path) -> None:
    """Load the gitignored local environment without overriding process variables."""
    # Keep machine credentials at the YunAlpha root so API and Worker share
    # one local configuration without depending on the source checkout.
    paths = (
        project_root.parents[1] / "config/.env.fund-agent.local",
        project_root / ".env.deepseek.local",
        project_root / ".env.local",
        project_root / ".env.opencode.local",
    )
    for path in paths:
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            name = name.strip()
            if not ENV_NAME.fullmatch(name):
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            os.environ.setdefault(name, value)


class RuntimeSettings(BaseModel):
    # Deep Agents middleware expands one model/tool round into several graph
    # steps. Business work remains bounded by the tool/model limits below.
    graph_recursion_limit: int = 120
    max_tool_calls: int = 16
    subagent_tool_limit: int = 8
    chat_deadline_seconds: int = 180
    max_web_search_calls: int = 3
    worker_lease_seconds: int = 60
    worker_heartbeat_seconds: int = 10
    max_report_jobs: int = 3  # local SQLite/CPU safeguard; supports the three requested reports
    report_part_concurrency: int = 0  # retained for config compatibility; 0 means unbounded


class ContextSettings(BaseModel):
    chat_budget_tokens: int = 32000
    compact_at_tokens: int = 24000
    report_safety_margin: float = 0.0


class HandbookSettings(BaseModel):
    target_chars: int = 30000
    soft_min_chars: int = 18000
    hard_min_chars: int = 10000
    min_part_chars: int = 200
    max_repair_rounds: int = 2


class WebSettings(BaseModel):
    mode: str = "replay"
    search_depth: str = "basic"
    max_results_per_query: int = 15
    min_accepted_results: int = 3
    max_extract_urls: int = 12
    min_score: float = 0.5


class Settings(BaseModel):
    project_root: Path = DEFAULT_PROJECT_ROOT
    legacy_root: Path = DEFAULT_LEGACY_ROOT
    var_root: Path
    inputs_root: Path = DEFAULT_PROJECT_ROOT / ".runtime/inputs"
    state_db: Path
    checkpoint_db: Path
    lake_database: Path
    legacy_kb: Path
    fund_manifest: Path
    fund_universe: Path
    host: str = "127.0.0.1"
    port: int = 8765
    timezone: str = "Asia/Shanghai"
    user_id: str = "local"
    base_path: str = ""
    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)
    context: ContextSettings = Field(default_factory=ContextSettings)
    handbook: HandbookSettings = Field(default_factory=HandbookSettings)
    web: WebSettings = Field(default_factory=WebSettings)
    read_only: bool = True
    trading_tools_enabled: bool = False

    def resolve_project_path(self, value: str | Path) -> Path:
        path = Path(value).expanduser()
        return path.resolve() if path.is_absolute() else (self.project_root / path).resolve()


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    project_root = Path(os.getenv("FUND_AGENT_PROJECT_ROOT", DEFAULT_PROJECT_ROOT)).resolve()
    _load_local_environment(project_root)
    config_path = Path(
        os.getenv("FUND_AGENT_CONFIG", project_root / "config" / "app.local.yaml")
    )
    if not config_path.exists():
        config_path = project_root / "config" / "app.example.yaml"
    raw = _read_yaml(config_path)
    app = raw.get("app", {})
    paths = raw.get("paths", {})
    configured_project = Path(
        os.getenv("FUND_AGENT_PROJECT_ROOT", paths.get("project_root", project_root))
    ).resolve()
    legacy_root = Path(
        os.getenv(
            "YUNALPHA_LAKE_ROOT",
            os.getenv("FUND_AGENT_LEGACY_ROOT", paths.get("legacy_root", DEFAULT_LEGACY_ROOT)),
        )
    ).resolve()

    def project_path(key: str, default: str) -> Path:
        environment_name = f"FUND_AGENT_{key.upper()}"
        value = Path(os.getenv(environment_name, paths.get(key, default))).expanduser()
        return value.resolve() if value.is_absolute() else (configured_project / value).resolve()

    var_root_env = os.getenv("FUND_AGENT_VAR_ROOT")
    runtime_root = Path(
        os.getenv("YUNALPHA_RUNTIME", DEFAULT_PROJECT_ROOT.parents[1] / ".runtime")
    ).expanduser().resolve()
    inputs_root = Path(
        os.getenv("YUNALPHA_INPUTS", DEFAULT_PROJECT_ROOT.parents[1] / ".runtime/phase2-inputs")
    ).expanduser().resolve() / "fund_agent"
    var_root = project_path("var_root", str(runtime_root / "phase2/fund_agent"))

    def var_path(key: str, relative: str) -> Path:
        environment_name = f"FUND_AGENT_{key.upper()}"
        if environment_name in os.environ:
            value = Path(os.environ[environment_name]).expanduser()
            return value.resolve() if value.is_absolute() else (configured_project / value).resolve()
        if not var_root_env and key in paths:
            return project_path(key, str(var_root / relative))
        return (var_root / relative).resolve()

    return Settings(
        project_root=configured_project,
        legacy_root=legacy_root,
        var_root=var_root,
        inputs_root=inputs_root,
        state_db=var_path("state_db", "state/agent.sqlite"),
        checkpoint_db=var_path("checkpoint_db", "state/deep-agent-checkpoints.sqlite"),
        lake_database=var_path("lake_database", "lake_bridge/duckdb/agent-lake.duckdb"),
        legacy_kb=project_path("legacy_kb", str(inputs_root / "materials/kb-index.sqlite")),
        fund_manifest=project_path("fund_manifest", str(inputs_root / "fund-manifest.csv")),
        fund_universe=project_path("fund_universe", str(inputs_root / "fund-universe.xlsx")),
        host=str(os.getenv("FUND_AGENT_HOST", app.get("host", "127.0.0.1"))),
        port=int(os.getenv("FUND_AGENT_PORT", app.get("port", 19241))),
        timezone=str(app.get("timezone", "Asia/Shanghai")),
        user_id=str(app.get("user_id", "local")),
        base_path=str(os.getenv("FUND_AGENT_BASE_PATH", app.get("base_path", "/agent-runtime"))).rstrip("/"),
        runtime=RuntimeSettings(**raw.get("runtime", {})),
        context=ContextSettings(**raw.get("context", {})),
        handbook=HandbookSettings(**raw.get("handbook", {})),
        web=WebSettings(**raw.get("web", {})),
        read_only=bool(raw.get("research", {}).get("read_only", True)),
        trading_tools_enabled=bool(
            raw.get("research", {}).get("trading_tools_enabled", False)
        ),
    )


def reload_settings() -> Settings:
    load_settings.cache_clear()
    return load_settings()
