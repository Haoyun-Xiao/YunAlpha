import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from fund_research_agent.paths import WriteBoundary
from fund_research_agent.runtime.context import ContextManager
from fund_research_agent.workflows.report.engine import ReportEngine


class ConcurrentModel:
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0

    def role(self, _name: str):
        return SimpleNamespace(max_tokens=256, context_window_tokens=131072)

    async def chat(self, _role: str, _messages: list[dict], **kwargs):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.02)
        self.active -= 1
        operation = kwargs.get("operation", "")
        if operation == "part9_planning":
            content = json.dumps({"案例": []}, ensure_ascii=False)
        elif operation.startswith("part10_") and "writing" not in operation:
            content = json.dumps({"核心事件证据卡": [{"事件": "测试"}]}, ensure_ascii=False)
        else:
            content = "有效正文" * 80
        return {"content": content, "message": {"role": "assistant", "content": content}}


async def test_all_report_parts_start_together(tmp_path: Path):
    project = tmp_path / "project"
    legacy = tmp_path / "legacy"
    var = project / "var"
    project.mkdir()
    legacy.mkdir()
    var.mkdir()
    boundary = WriteBoundary(project, legacy, var)
    source_dir = boundary.ensure_dir(var / "source")
    part_files = {}
    for number in range(1, 11):
        path = boundary.write_text(
            source_dir / f"part{number}.json",
            json.dumps({"基金代码": "TEST", "基金简称": "测试基金", "基金经理": "测试经理"}),
        )
        part_files[f"part{number}"] = str(path)

    model = ConcurrentModel()
    engine = ReportEngine(model, boundary, ContextManager(), concurrency=2)
    result = await engine.generate_parts(
        "测试经理_测试基金", "完整手册", part_files, boundary.ensure_dir(var / "parts")
    )

    assert set(result) == {f"part{number}" for number in range(1, 11)}
    assert model.max_active >= 9


async def test_only_interrupted_part_is_recomputed_on_resume(tmp_path):
    import pytest
    project=tmp_path/'project';legacy=tmp_path/'legacy'
    (project/'var').mkdir(parents=True);legacy.mkdir()
    boundary=WriteBoundary(project,legacy)
    class FailingModel(ConcurrentModel):
        failed=False
        calls=[]
        async def chat(self,role,messages,**kwargs):
            operation=kwargs.get('operation')
            self.calls.append(operation)
            if operation=='part7_writing' and not self.failed:
                self.failed=True
                raise RuntimeError('injected_disconnect')
            return await super().chat(role,messages,**kwargs)
    model=FailingModel();engine=ReportEngine(model,boundary,ContextManager())
    files={}
    for n in range(1,11):
        files[f'part{n}']=str(boundary.write_text(project/f'var/input/part{n}.json',json.dumps({'sources':{},'基金代码':'TEST'})))
    parts=boundary.ensure_dir(project/'var/parts')
    with pytest.raises(RuntimeError,match='injected_disconnect'):
        await engine.generate_parts('测试经理_测试基金','完整手册',files,parts)
    completed={p.name:p.read_bytes() for p in parts.glob('part*.md')}
    before=list(model.calls)
    result=await engine.generate_parts('测试经理_测试基金','完整手册',files,parts)
    assert len(result)==10 and model.calls[len(before):]==['part7_writing']
    assert all((parts/name).read_bytes()==content for name,content in completed.items())
