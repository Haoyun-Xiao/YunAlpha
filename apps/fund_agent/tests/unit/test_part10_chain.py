import json
from pathlib import Path

from fund_research_agent.paths import WriteBoundary
from fund_research_agent.runtime.context import ContextManager
from fund_research_agent.workflows.report.engine import ReportEngine


class FakeModel:
    def __init__(self):
        self.calls = []

    async def chat(self, role, messages, **_kwargs):
        prompt = messages[-1]["content"]
        self.calls.append(prompt)
        if "核心事件证据提取" in prompt:
            content = json.dumps({"核心事件证据卡": [{"事件": "测试"}]}, ensure_ascii=False)
        elif "Part10 公开路演证据匹配" in prompt:
            content = json.dumps({"公开路演证据卡": []}, ensure_ascii=False)
        else:
            content = "Part10 正文" * 30
        return {"content": content, "message": {"role": "assistant", "content": content}}


async def test_part10_preserves_three_stage_evidence_chain_and_chunks(tmp_path: Path):
    project = tmp_path / "project"
    legacy = tmp_path / "legacy"
    (project / "var").mkdir(parents=True)
    legacy.mkdir()
    boundary = WriteBoundary(project, legacy)
    source_dir = boundary.ensure_dir(project / "var/source")
    sources = {}
    for kind in ("基金配置", "季度盈亏", "年度盈亏", "季度运作报告"):
        if kind == "基金配置":
            content = {"rows": ["数据" * 120_000]}
        elif kind == "季度运作报告":
            content = {"fund": {}}
        else:
            content = {"rows": [kind]}
        sources[kind] = content
    part10_path = boundary.write_text(
        source_dir / "part10.json",
        json.dumps({"sources": sources}, ensure_ascii=False),
    )
    files = {"part10": str(part10_path)}
    material = boundary.write_text(project / "var/source/roadshow.txt", "路演原文" * 70_000)
    model = FakeModel()
    engine = ReportEngine(model, boundary, ContextManager())
    parts_dir = boundary.ensure_dir(project / "var/parts")
    result = await engine._generate_part10("周寒颖_测试基金", files, [material], parts_dir)
    assert result.startswith("Part10 正文")
    assert (parts_dir / "part10_core_evidence.json").exists()
    assert (parts_dir / "part10_roadshow_evidence.json").exists()
    assert list(parts_dir.glob("part10_core_batch_*.json"))
    assert list(parts_dir.glob("part10_roadshow_batch_*.json"))
    core_calls = [call for call in model.calls if "核心事件证据提取" in call]
    assert len(core_calls) >= 4
    assert not any('"source_kind":"季度运作报告"' in call for call in core_calls)
    assert "季度运作报告为空" in (parts_dir / "part10_core_evidence.json").read_text()
    assert len([call for call in model.calls if "Part10 公开路演证据匹配" in call]) >= 3
