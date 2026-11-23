from datetime import date
from pathlib import Path

from fund_research_agent.paths import WriteBoundary
from fund_research_agent.runtime.context import ContextManager
from fund_research_agent.workflows.report.engine import ReportEngine


async def test_render_wraps_model_outputs_in_stable_part_boundaries(
    tmp_path: Path, monkeypatch
):
    project = tmp_path / "project"
    legacy = tmp_path / "legacy"
    (project / "var").mkdir(parents=True)
    legacy.mkdir()
    boundary = WriteBoundary(project, legacy)
    captured: dict[str, str] = {}

    def fake_render(
        _fund_name,
        markdown_path,
        _part_data_dir,
        _render_data_dir,
        _output_dir,
        _as_of,
        _boundary,
    ):
        captured["markdown"] = markdown_path.read_text(encoding="utf-8")
        return {"markdown": markdown_path}, {"valid": True}

    monkeypatch.setattr(
        "fund_research_agent.workflows.report.engine.render_report", fake_render
    )
    engine = ReportEngine(object(), boundary, ContextManager())
    await engine.render(
        "刘旭_大成高鑫A",
        {f"part{number}": f"第{number}章模型原文" for number in range(1, 11)},
        "总结模型原文",
        project / "var",
        project / "var",
        project / "var/output",
        date(2026, 8, 26),
    )
    markdown = captured["markdown"]
    for number in range(1, 11):
        assert f"==========  Part{number}：" in markdown
        assert f"第{number}章模型原文" in markdown
    assert "==========  总结：投资追溯与复盘分析  ==========" in markdown
