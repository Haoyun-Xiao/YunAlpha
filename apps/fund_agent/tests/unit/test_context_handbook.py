import json
from pathlib import Path
from types import SimpleNamespace

from fund_research_agent.config import HandbookSettings
from fund_research_agent.paths import WriteBoundary
from fund_research_agent.runtime.context import ContextManager
from fund_research_agent.workflows.handbook.workflow import (
    FINAL_DISCLAIMER,
    merge_repaired_parts,
    validate_handbook,
)
from fund_research_agent.workflows.report.engine import ReportEngine
from fund_research_agent.workflows.report.part_data import PART_FILENAMES


def handbook(chars: int = 220) -> str:
    return "刘旭\n" + "\n".join(
        f"## Part{i}：章节{i}\n" + ("内容" * chars) for i in range(1, 11)
    ) + "\n" + FINAL_DISCLAIMER


def test_handbook_relaxed_quality_levels():
    settings = HandbookSettings(target_chars=6000, soft_min_chars=5000, hard_min_chars=4000)
    result = validate_handbook(handbook(), "刘旭", settings)
    assert result["valid"]
    assert result["quality"] in {"degraded", "short", "target"}


def test_token_preflight_keeps_full_handbook():
    text = handbook()
    result = ContextManager.report_preflight(text, "prompt", "{}", 1000, 100_000, 0.2)
    assert result["protected_handbook_tokens"] > 0
    assert result["fits"]


def test_handbook_repair_merges_only_targeted_parts():
    original = "# 手册\n\n## Part1：\n\n原一\n\n## Part2：\n\n原二\n\n" + FINAL_DISCLAIMER
    repaired = "## Part2：\n\n修复二"
    result = merge_repaired_parts(original, repaired, [2])
    assert "## Part1：\n\n原一" in result
    assert "## Part2：\n\n修复二" in result
    assert result.endswith(FINAL_DISCLAIMER)


def test_token_preflight_fails_instead_of_clipping():
    text = handbook(1000)
    result = ContextManager.report_preflight(text, "prompt", "{}", 1000, 2000, 0.2)
    assert not result["fits"]


def test_part_data_compaction_is_lossless_and_removes_repeated_keys():
    source = {
        "二级行业": [
            {"报告期": f"2024{index:04d}", "行业": "电子", "占比": f"{index}%"}
            for index in range(30)
        ]
    }

    compacted = json.loads(
        ReportEngine._compact_part_data(json.dumps(source, ensure_ascii=False))
    )
    table = compacted["data"]["二级行业"]

    assert table["_format"] == "lossless_table"
    assert table["columns"] == ["报告期", "行业", "占比"]
    assert len(table["rows"]) == 30
    assert table["rows"][7] == ["20240007", "电子", "7%"]


class RoleOnlyModel:
    @staticmethod
    def role(_name: str):
        return SimpleNamespace(max_tokens=1_000, context_window_tokens=8_000)


def test_all_part_preflight_uses_one_fixed_window_check(tmp_path: Path):
    project = tmp_path / "project"
    legacy = tmp_path / "legacy"
    (project / "var").mkdir(parents=True)
    legacy.mkdir()
    boundary = WriteBoundary(project, legacy)
    source = project / "var" / "long.json"
    rows = [
        {"报告期": f"2024{index:04d}", "行业": "电子", "占比": index}
        for index in range(400)
    ]
    boundary.write_text(source, json.dumps({"rows": rows}, ensure_ascii=False))
    files = {part: str(source) for part in PART_FILENAMES}
    engine = ReportEngine(
        RoleOnlyModel(), boundary, ContextManager(), safety_margin=0.10
    )

    result = engine.preflight_parts("手册" * 100, files)

    assert not result["fits"]
    assert result["safety_margin"] == 0.10
    assert all(not item["compacted"] for item in result["parts"].values())


def test_audit_drops_uncertain_and_self_consistent_notes():
    actionable, dropped = ReportEngine._actionable_narrative_issues(
        {
            "part3": ["与手册数字一致，但需确认口径"],
            "part4": ["报告将其他基金数据写成当前基金数据"],
            "part8": ["两个数字属于不同期间，并不矛盾"],
        }
    )

    assert actionable == {"part4": ["报告将其他基金数据写成当前基金数据"]}
    assert len(dropped) == 2


def test_audit_drops_handbook_only_evidence_rejection():
    actionable, dropped = ReportEngine._actionable_narrative_issues(
        {
            "part5": ["仓位数据不在投资手册中，因此属于编造"],
            "part9": ["同一合计数正文为4750万元、分项相加为4893万元"],
        }
    )

    assert actionable == {"part9": ["同一合计数正文为4750万元、分项相加为4893万元"]}
    assert dropped == ["part5:仓位数据不在投资手册中，因此属于编造"]
