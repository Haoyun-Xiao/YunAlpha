from datetime import date
from pathlib import Path

from fund_research_agent.adapters.knowledge import FederatedKnowledgeAdapter
from fund_research_agent.paths import WriteBoundary
from fund_research_agent.persistence.database import Database
from fund_research_agent.retrieval.document_index import DocumentIndex
from fund_research_agent.retrieval.report_chunker import chunk_report_parts


async def knowledge_database(tmp_path: Path) -> tuple[Database, WriteBoundary]:
    project = tmp_path / "project"
    legacy = tmp_path / "legacy"
    (project / "var").mkdir(parents=True)
    legacy.mkdir()
    boundary = WriteBoundary(project, legacy)
    database = Database(project / "var/state.sqlite", boundary)
    await database.migrate(Path(__file__).resolve().parents[2] / "sql")
    return database, boundary


def test_report_chunker_preserves_part_and_splits_long_sections():
    chunks = chunk_report_parts(
        {
            "part7": "## 行业配置\n\n" + "电子行业配置与周期判断。" * 400,
            "summary": "## 总结\n\n组合行业分散但成长暴露较高。",
        }
    )

    assert {chunk.part for chunk in chunks} == {"part7", "summary"}
    assert all(len(chunk.content) <= 2400 for chunk in chunks)
    assert any(chunk.topic == "行业配置" for chunk in chunks)


async def test_report_index_is_chunked_searchable_and_idempotent(tmp_path: Path):
    database, boundary = await knowledge_database(tmp_path)
    report_path = boundary.write_text(
        tmp_path / "project/var/jobs/job/output/report.md",
        "# 大成高鑫深度报告\n\n行业配置以价值和制造业为主。\n",
    )
    index = DocumentIndex(database)
    inputs = {
        "fund_symbol": "000628.OF",
        "fund_name": "大成高鑫A",
        "manager_name": "刘旭",
        "as_of": date(2026, 8, 28),
        "report_path": report_path,
        "artifact_id": None,
        "job_id": None,
        "parts": {
            "part7": "## 行业配置\n\n价值、制造业与周期行业是组合的主要暴露。",
            "summary": "## 总结\n\n基金经理强调安全边际。",
        },
    }

    first = await index.index_report(**inputs)
    second = await index.index_report(**inputs)

    assert first["document_id"] == second["document_id"]
    async with database.connection() as connection:
        document_count = (
            await (
                await connection.execute("SELECT count(*) FROM knowledge_documents")
            ).fetchone()
        )[0]
        chunk_count = (
            await (
                await connection.execute("SELECT count(*) FROM knowledge_chunks")
            ).fetchone()
        )[0]
    assert document_count == 1
    assert chunk_count == 2

    adapter = FederatedKnowledgeAdapter(tmp_path / "missing.sqlite", database)
    results = await adapter.search(
        "这篇报告的行业配置怎么看",
        document_id=str(first["document_id"]),
        limit=5,
    )
    assert results
    assert results[0]["part"] == "part7"
    assert results[0]["fund_symbol"] == "000628.OF"
    assert "制造业" in results[0]["content"]


async def test_explicit_old_document_remains_searchable_after_new_version(tmp_path: Path):
    database, boundary = await knowledge_database(tmp_path)
    index = DocumentIndex(database)
    common = dict(fund_symbol='000628.OF',fund_name='大成高鑫A',manager_name='刘旭',as_of=date(2026,11,5),artifact_id=None,job_id=None)
    old = await index.index_report(**common,report_path=boundary.write_text(tmp_path/'project/var/old.md','旧版电子行业配置'),parts={'part7':'电子行业配置旧版分析。'})
    await index.index_report(**common,report_path=boundary.write_text(tmp_path/'project/var/new.md','新版制造业行业配置'),parts={'part7':'制造业行业配置新版分析。'})
    adapter = FederatedKnowledgeAdapter(tmp_path/'missing.sqlite',database)
    result = await adapter.search('行业配置',document_id=str(old['document_id']))
    assert result and result[0]['doc_id']==old['document_id']


async def test_job_fans_out_without_replacing_newer_active_report(tmp_path: Path):
    from fund_research_agent.domain.contracts import ReportRequest
    database, boundary = await knowledge_database(tmp_path)
    first=await database.create_session('first');second=await database.create_session('second')
    job,_=await database.create_report_job(ReportRequest(fund_query='000628.OF',as_of=date.today()), 'shared','report-data-v2')
    await database.update_session_context(first,active_report_job_id=job)
    await database.update_session_context(second,active_report_job_id='newer-job',active_report_document_id='newer-document')
    run_a=await database.create_agent_run(first);run_b=await database.create_agent_run(second)
    await database.link_report_job(job,first,run_a);await database.link_report_job(job,second,run_b)
    await database.publish_report_to_sessions(job,'completed-document')
    assert (await database.get_session_context(first))['active_report_document_id']=='completed-document'
    assert (await database.get_session_context(second))['active_report_document_id']=='newer-document'
    assert (await database.get_report_job(job)).session_id==first


def test_reindex_uses_published_rewrites_not_old_generation_checkpoints(tmp_path):
    from fund_research_agent.retrieval.document_index import report_parts_from_job
    (tmp_path/'parts/old').mkdir(parents=True);(tmp_path/'output').mkdir()
    (tmp_path/'parts/old/part1.md').write_text('已淘汰的旧正文')
    (tmp_path/'output/report.md').write_text('==========  Part1：基本概况  ==========\n\n发布的修改后正文\n\n==========  总结：投资追溯与复盘分析  ==========\n\n发布的总结')
    parts=report_parts_from_job(tmp_path)
    assert parts['part1']=='发布的修改后正文' and parts['summary']=='发布的总结'


async def test_document_chapter_fallback_handles_chinese_title_paraphrases(tmp_path):
    database,boundary=await knowledge_database(tmp_path)
    result=await DocumentIndex(database).index_report(fund_symbol='000628.OF',fund_name='大成高鑫A',manager_name='刘旭',as_of=date.today(),report_path=boundary.write_text(tmp_path/'project/var/report.md','发布报告'),artifact_id=None,job_id=None,parts={'part7':'配置偏好集中于制造赛道，电子是进攻方向。','part3':'净值修复后，最大回撤较历史高位有所收敛。'})
    adapter=FederatedKnowledgeAdapter(tmp_path/'missing.sqlite',database)
    industry=await adapter.search('这篇报告对行业配置怎么看',document_id=str(result['document_id']))
    drawdown=await adapter.search('回撤',document_id=str(result['document_id']))
    assert industry and all(row['part']=='part7' for row in industry)
    assert drawdown and all(row['part']=='part3' for row in drawdown)
