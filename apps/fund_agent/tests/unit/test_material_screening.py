import json
from pathlib import Path

from fund_research_agent.retrieval.material_screening import (
    MaterialAssessment,
    MaterialScreeningService,
    select_materials,
)
from fund_research_agent.skills import SkillLoader


def assessment(
    candidate_id: str,
    document_type: str,
    score: float = 0.9,
    event_date: str | None = None,
):
    return MaterialAssessment(
        candidate_id=candidate_id,
        document_type=document_type,
        identity_match=score,
        rawness_score=score,
        attribution_confidence=0.6,
        research_value=score,
        event_key=candidate_id,
        event_date=event_date,
    )


def test_selection_ignores_domain_grade_and_allows_fifteen_primary_materials():
    candidates = [
        {"candidate_id": f"candidate_{index:03d}", "source_grade": "C", "url": f"u{index}"}
        for index in range(18)
    ]
    assessments = [
        assessment(item["candidate_id"], "meeting_minutes") for item in candidates
    ]

    selected = select_materials(candidates, assessments)

    assert len(selected) == 15
    assert all(item["material_role"] == "primary" for item in selected)
    assert all(item["source_grade"] == "C" for item in selected)


def test_selection_uses_time_as_soft_tiebreaker_after_filling_quantity():
    candidates = [
        {"candidate_id": f"candidate_{index:03d}", "url": f"u{index}"}
        for index in range(16)
    ]
    assessments = [
        assessment(
            f"candidate_{index:03d}",
            "meeting_minutes",
            0.9,
            "2024-01-01",
        )
        for index in range(15)
    ] + [assessment("candidate_015", "meeting_minutes", 0.89, "2018-01-01")]

    selected = select_materials(candidates, assessments)

    assert len(selected) == 15
    assert {item["temporal_year"] for item in selected} == {2018, 2024}


def test_selection_fills_a_small_primary_gap_with_at_most_five_commentaries():
    candidates = [
        {"candidate_id": f"candidate_{index:03d}", "url": f"u{index}"}
        for index in range(8)
    ]
    assessments = [
        assessment("candidate_000", "qa_interview"),
        assessment("candidate_001", "raw_transcript"),
        *[
            assessment(f"candidate_{index:03d}", "third_party_analysis", 0.85)
            for index in range(2, 8)
        ],
    ]

    selected = select_materials(candidates, assessments)

    assert [item["material_role"] for item in selected] == [
        "primary",
        "primary",
        "supplement",
        "supplement",
        "supplement",
    ]


class FakeScreeningModel:
    async def structured(self, role_name, messages, **kwargs):
        assert role_name == "researcher"
        assert kwargs["operation"].startswith("material_screening_batch_")
        assert "external_untrusted" in messages[0]["content"]
        documents = json.loads(messages[1]["content"])["documents"]
        return {
            "assessments": [
                {
                    "candidate_id": item["candidate_id"],
                    "document_type": "qa_interview",
                    "identity_match": 0.98,
                    "rawness_score": 0.91,
                    "attribution_confidence": 0.7,
                    "research_value": 0.9,
                    "event_key": item["candidate_id"],
                    "speakers": ["刘旭", "主持人"],
                    "transcript_signals": ["连续问答"],
                    "processing_signals": [],
                    "identity_warnings": [],
                    "summary": "完整问答",
                }
                for item in documents
            ]
        }


async def test_screening_reads_c_grade_content_and_removes_exact_duplicates():
    skill_root = Path(__file__).resolve().parents[2] / "src/fund_research_agent/skills"
    service = MaterialScreeningService(FakeScreeningModel(), SkillLoader(skill_root))
    text = "以下为采访文字实录\n问：怎么研究？\n刘旭：先看企业价值。" * 80
    result = await service.screen(
        {
            "results": [
                {"url": "u1", "source_grade": "C", "raw_content": text, "content_hash": "same"},
                {"url": "u2", "source_grade": "C", "raw_content": text, "content_hash": "same"},
            ]
        },
        manager_name="刘旭",
        company_short_name="大成",
    )

    assert result["screening"]["readable_count"] == 1
    assert result["screening"]["selected_count"] == 1
    assert result["screening"]["temporal_coverage"] == {
        "year_distribution": {"unknown": 1},
        "dated_count": 0,
        "undated_count": 1,
    }
    assert result["selected_results"][0]["source_grade"] == "C"


async def test_screening_removes_near_duplicate_mobile_and_desktop_versions():
    skill_root = Path(__file__).resolve().parents[2] / "src/fund_research_agent/skills"
    service = MaterialScreeningService(FakeScreeningModel(), SkillLoader(skill_root))
    body = ("问：如何看安全边际？\n刘旭：从企业长期现金流出发。\n" * 120)
    result = await service.screen(
        {
            "results": [
                {
                    "url": "https://example.com/desktop",
                    "source_grade": "C",
                    "raw_content": "完整访谈导语\n" + body + "\n结尾",
                    "content_hash": "desktop",
                },
                {
                    "url": "https://m.example.com/mobile",
                    "source_grade": "C",
                    "raw_content": "手机页导航\n" + body,
                    "content_hash": "mobile",
                },
            ]
        },
        manager_name="刘旭",
        company_short_name="大成",
    )

    assert result["screening"]["readable_count"] == 1
    assert result["screening"]["selected_count"] == 1
