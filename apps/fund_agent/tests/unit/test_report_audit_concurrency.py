import asyncio

from fund_research_agent.paths import WriteBoundary
from fund_research_agent.runtime.context import ContextManager
from fund_research_agent.workflows.report.engine import ReportEngine


class FailingAuditModel:
    def __init__(self):
        self.calls = 0
        self.cancelled = 0

    async def structured(self, _role, _messages, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            await asyncio.sleep(0.01)
            raise TimeoutError("provider timeout")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise


async def test_audit_failure_cancels_sibling_model_calls(tmp_path):
    project = tmp_path / "project"
    legacy = tmp_path / "legacy"
    var = project / "var"
    project.mkdir()
    legacy.mkdir()
    var.mkdir()
    boundary = WriteBoundary(project, legacy, var)
    model = FailingAuditModel()
    engine = ReportEngine(model, boundary, ContextManager(32_000, 24_000), concurrency=3)
    parts = {f"part{index}": "有效正文" * 80 for index in range(1, 11)}

    _parts, _summary, audit = await engine.audit_and_rewrite(
        "测试基金_测试经理",
        "2026-08-28",
        "完整手册" * 100,
        parts,
        "有效总结" * 80,
        var / "audit",
        max_rounds=0,
    )

    assert model.calls == 3
    assert model.cancelled == 2
    assert audit["passed"] is False
    assert audit["issues"]["evaluator"] == ["model_evaluator_error:TimeoutError"]



async def test_invalid_rewrite_cannot_destroy_valid_summary(tmp_path):
    class Model:
        async def structured(self,*args,**kwargs):
            return {'passed':False,'issues':{'summary':['确认存在内部数字矛盾，需要修改总结']}}
        async def chat(self,*args,**kwargs):
            return {'content':'无需修改。'}
    project=tmp_path/'project';legacy=tmp_path/'legacy';(project/'var').mkdir(parents=True);legacy.mkdir()
    engine=ReportEngine(Model(),WriteBoundary(project,legacy),ContextManager())
    original='有效总结'*100
    _,summary,audit=await engine.audit_and_rewrite('经理_基金','2026-11-05','完整手册',{f'part{i}':'有效正文'*100 for i in range(1,11)},original,project/'var/audit',max_rounds=1)
    assert summary==original
    assert audit['rounds'][0]['rejected_rewrites']['summary']=='rewrite_failed_deterministic_validation'
