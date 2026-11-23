from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from fund_research_agent.paths import sha256_file
from fund_research_agent.persistence.database import Database, json_text, utc_now
from fund_research_agent.retrieval.report_chunker import chunk_report_parts


class DocumentIndex:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def index_report(
        self,
        *,
        fund_symbol: str,
        fund_name: str,
        manager_name: str,
        as_of: date,
        report_path: Path,
        artifact_id: str | None,
        job_id: str | None,
        parts: dict[str, str],
        data_metadata: dict | None = None,
        pipeline_version: str = "legacy",
        manager_tenure_start: date | None = None,
    ) -> dict[str, object]:
        report_path = report_path.resolve(strict=True)
        source_hash = sha256_file(report_path)
        document_id = f"report:{source_hash[:24]}"
        logical_key = f"report:{fund_symbol}:{manager_name}:{as_of.isoformat()}"
        title = report_path.stem
        chunks = chunk_report_parts(parts)
        if not chunks:
            raise ValueError("报告没有可索引的章节内容")
        data_metadata = data_metadata or {}
        now = utc_now().isoformat()
        async with self.database.connection() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            await connection.execute(
                "UPDATE knowledge_documents SET is_current=0,updated_at=? "
                "WHERE logical_key=? AND document_id<>?",
                (now, logical_key, document_id),
            )
            await connection.execute(
                "INSERT INTO knowledge_documents(document_id,logical_key,source_type,"
                "fund_symbol,fund_name,manager_name,as_of,title,artifact_id,job_id,source_uri,"
                "source_hash,is_current,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1,?,?) "
                "ON CONFLICT(document_id) DO UPDATE SET logical_key=excluded.logical_key,"
                "fund_symbol=excluded.fund_symbol,fund_name=excluded.fund_name,"
                "manager_name=excluded.manager_name,as_of=excluded.as_of,title=excluded.title,"
                "artifact_id=excluded.artifact_id,job_id=excluded.job_id,"
                "source_uri=excluded.source_uri,is_current=1,updated_at=excluded.updated_at",
                (
                    document_id,
                    logical_key,
                    "report_md",
                    fund_symbol,
                    fund_name,
                    manager_name,
                    as_of.isoformat(),
                    title,
                    artifact_id,
                    job_id,
                    str(report_path),
                    source_hash,
                    now,
                    now,
                ),
            )
            await connection.execute(
                "UPDATE knowledge_documents SET pipeline_version=?,data_as_of=?,coverage_json=?,manager_tenure_start=? WHERE document_id=?",
                (pipeline_version, data_metadata.get("data_as_of"), json_text(data_metadata), str(manager_tenure_start) if manager_tenure_start else None, document_id),
            )
            await connection.execute(
                "DELETE FROM knowledge_chunks_fts WHERE document_id=?", (document_id,)
            )
            await connection.execute(
                "DELETE FROM knowledge_chunks WHERE document_id=?", (document_id,)
            )
            for chunk in chunks:
                chunk_id = f"{document_id}:{chunk.ordinal}:{chunk.content_hash[:12]}"
                metadata = {
                    "job_id": job_id,
                    "artifact_id": artifact_id,
                    "source_uri": str(report_path),
                    "fund_symbol": fund_symbol,
                    "fund_name": fund_name,
                    "manager_name": manager_name,
                    "as_of": as_of.isoformat(),
                    "data_metadata": data_metadata,
                    "pipeline_version": pipeline_version,
                }
                await connection.execute(
                    "INSERT INTO knowledge_chunks(chunk_id,document_id,ordinal,part,topic,year,"
                    "content,content_hash,metadata_json) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        chunk_id,
                        document_id,
                        chunk.ordinal,
                        chunk.part,
                        chunk.topic,
                        chunk.year,
                        chunk.content,
                        chunk.content_hash,
                        json_text(metadata),
                    ),
                )
                await connection.execute(
                    "INSERT INTO knowledge_chunks_fts(chunk_id,document_id,fund_symbol,fund_name,"
                    "manager_name,source_type,part,topic,content,artifact_id) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        chunk_id,
                        document_id,
                        fund_symbol,
                        fund_name,
                        manager_name,
                        "report_md",
                        chunk.part,
                        chunk.topic,
                        chunk.content,
                        artifact_id,
                    ),
                )
            await connection.commit()
        return {
            "document_id": document_id,
            "source_hash": source_hash,
            "logical_key": logical_key,
            "chunk_count": len(chunks),
        }


def report_parts_from_job(job_root: Path) -> dict[str, str]:
    """Best-effort helper for one-time indexing of reports generated before chunked RAG."""
    published = job_root / "output/report.md"
    if published.exists():
        text = published.read_text(encoding="utf-8")
        boundaries = list(re.finditer(r"(?m)^==========\s+(Part\d+|总结)[：:].*?==========\s*$", text))
        if boundaries:
            return {
                "summary" if match.group(1) == "总结" else match.group(1).lower(): text[match.end():boundaries[index + 1].start() if index + 1 < len(boundaries) else len(text)].strip()
                for index, match in enumerate(boundaries)
            }
    candidates = sorted((job_root / "parts").glob("*/part*.md"))
    parts: dict[str, str] = {}
    for path in candidates:
        name = path.stem
        if name.startswith("part") and name[4:].isdigit():
            parts[name] = path.read_text(encoding="utf-8")
    summary_candidates = sorted((job_root / "parts").glob("*/summary.md"))
    if summary_candidates:
        parts["summary"] = summary_candidates[-1].read_text(encoding="utf-8")
    return parts
