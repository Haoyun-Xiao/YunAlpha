from pathlib import Path

from fund_research_agent.config import load_settings, reload_settings
from fund_research_agent.paths import WriteBoundary


def test_environment_paths_override_local_config(monkeypatch, tmp_path: Path):
    project = tmp_path / "app"
    legacy = tmp_path / "legacy"
    var = tmp_path / "persistent"
    universe = tmp_path / "funds.xlsx"
    project.mkdir()
    legacy.mkdir()
    var.mkdir()
    universe.touch()
    monkeypatch.setenv("FUND_AGENT_PROJECT_ROOT", str(project))
    monkeypatch.setenv("FUND_AGENT_LEGACY_ROOT", str(legacy))
    monkeypatch.setenv("FUND_AGENT_VAR_ROOT", str(var))
    monkeypatch.setenv("FUND_AGENT_FUND_UNIVERSE", str(universe))
    monkeypatch.setenv("FUND_AGENT_BASE_PATH", "/Fund_Research_Agent")
    monkeypatch.setenv("FUND_AGENT_HOST", "127.0.0.1")
    monkeypatch.setenv("FUND_AGENT_PORT", "8878")

    settings = reload_settings()

    assert settings.project_root == project
    assert settings.legacy_root == legacy
    assert settings.var_root == var
    assert settings.state_db == var / "state/agent.sqlite"
    assert settings.lake_database == var / "lake_bridge/duckdb/agent-lake.duckdb"
    assert settings.fund_universe == universe
    assert settings.base_path == "/Fund_Research_Agent"
    assert settings.port == 8878
    assert WriteBoundary(project, legacy, var).write_root == var
    load_settings.cache_clear()


def test_yunalpha_credentials_load_independently_of_working_directory(monkeypatch, tmp_path):
    from fund_research_agent.adapters.model import ModelGateway

    project = tmp_path / "yunalpha/apps/fund_agent"
    project.mkdir(parents=True)
    local = project.parents[1] / "config/.env.fund-agent.local"
    local.parent.mkdir()
    local.write_text('OPENCODE_GO_API_KEY="local-test-key"\n', encoding="utf-8")
    (project / ".env.opencode.local").write_text(
        "OPENCODE_GO_API_KEY=application-fallback\n", encoding="utf-8"
    )
    monkeypatch.delenv("OPENCODE_GO_API_KEY", raising=False)
    monkeypatch.setenv("FUND_AGENT_PROJECT_ROOT", str(project))
    monkeypatch.chdir(tmp_path)
    try:
        reload_settings()
        gateway = ModelGateway(Path(__file__).resolve().parents[2] / "config/model_roles.yaml")
        assert gateway.api_key_for("assistant") == "local-test-key"
        assert gateway.api_key_for("report") == "local-test-key"
    finally:
        load_settings.cache_clear()
        monkeypatch.delenv("OPENCODE_GO_API_KEY", raising=False)


def test_process_credentials_take_precedence_over_local_file(monkeypatch, tmp_path):
    from fund_research_agent.config import _load_local_environment

    project = tmp_path / "apps/fund_agent"
    project.mkdir(parents=True)
    local = tmp_path / "config/.env.fund-agent.local"
    local.parent.mkdir()
    local.write_text("OPENCODE_GO_API_KEY=local-test-key\n", encoding="utf-8")
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "process-test-key")
    _load_local_environment(project)
    import os

    assert os.environ["OPENCODE_GO_API_KEY"] == "process-test-key"
