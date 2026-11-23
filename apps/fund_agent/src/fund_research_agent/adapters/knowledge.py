from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from fund_research_agent.persistence.database import Database

PART_HINTS = {
    "规模": "part2",
    "业绩": "part3",
    "收益": "part3",
    "回撤": "part3",
    "盈亏": "part4",
    "换手": "part5",
    "仓位": "part5",
    "集中": "part5",
    "组合特征": "part6",
    "估值": "part6",
    "行业": "part7",
    "行业配置": "part7",
    "个股": "part8",
    "重仓": "part8",
    "案例": "part9",
    "中观": "part10",
    "轮动": "part10",
    "选股": "part10",
    "总结": "summary",
}


def _query_terms(query: str) -> list[str]:
    terms: list[str] = []
    for hint, part in PART_HINTS.items():
        if hint in query:
            terms.extend((hint, part))
    terms.extend(re.findall(r"20\d{2}|\d{6}\.(?:OF|SH|SZ)", query, re.IGNORECASE))
    compact = re.sub(r"[\s，。！？?、；：:]+", "", query)
    if len(compact) >= 3:
        terms.append(compact[:80])
    return list(dict.fromkeys(term for term in terms if len(term) >= 3))


def _match_query(query: str) -> str:
    terms = _query_terms(query)
    return " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)


def _target_parts(query: str, explicit: list[str] | None = None) -> list[str]:
    parts = list(explicit or [])
    for hint, part in PART_HINTS.items():
        if hint in query and part not in parts:
            parts.append(part)
    return parts


class FederatedKnowledgeAdapter:
    def __init__(self, legacy_index: Path, database: Database) -> None:
        self.legacy_index = legacy_index.resolve()
        self.database = database

    def _legacy_connect(self) -> sqlite3.Connection | None:
        if not self.legacy_index.exists():
            return None
        connection = sqlite3.connect(
            f"file:{self.legacy_index}?mode=ro&immutable=1", uri=True
        )
        connection.row_factory = sqlite3.Row
        return connection

    def search_legacy(
        self,
        query: str,
        limit: int = 10,
        *,
        fund_symbol: str | None = None,
        parts: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        connection = self._legacy_connect()
        if connection is None:
            return []
        target_parts = set(_target_parts(query, parts))
        try:
            manager_row = connection.execute(
                "SELECT manager FROM chunks WHERE manager<>'' AND instr(?,manager)>0 "
                "ORDER BY length(manager) DESC LIMIT 1",
                (query,),
            ).fetchone()
            manager_filter = str(manager_row[0]) if manager_row else None
            match = _match_query(query)
            rows = []
            if match:
                try:
                    rows = connection.execute(
                        "SELECT c.data,bm25(chunks_fts) AS score "
                        "FROM chunks_fts JOIN chunks c ON c.chunk_id=chunks_fts.chunk_id "
                        "WHERE chunks_fts MATCH ? LIMIT ?",
                        (match, max(limit * 8, 60)),
                    ).fetchall()
                except sqlite3.Error:
                    rows = []
            if not rows:
                like_term = next(iter(_query_terms(query)), query.strip())
                rows = connection.execute(
                    "SELECT data,0 AS score FROM chunks WHERE content LIKE ? LIMIT ?",
                    (f"%{like_term}%", max(limit * 8, 60)),
                ).fetchall()
            results = []
            for row in rows:
                item = json.loads(row["data"])
                item_symbol = str(item.get("fund_code") or item.get("fund_id") or "")
                if fund_symbol and item_symbol.upper() != fund_symbol.upper():
                    continue
                if manager_filter and item.get("manager") != manager_filter:
                    continue
                if target_parts and item.get("part") not in target_parts:
                    continue
                results.append(
                    {
                        "doc_id": item.get("doc_id"),
                        "chunk_id": item.get("chunk_id"),
                        "fund_symbol": item_symbol,
                        "fund_name": item.get("fund_name"),
                        "manager_name": item.get("manager"),
                        "source_type": item.get("source_type"),
                        "part": item.get("part"),
                        "topic": item.get("topic"),
                        "year": item.get("year"),
                        "title": item.get("title"),
                        "content": item.get("content", ""),
                        "source_uri": item.get("source_path"),
                        "html_path": item.get("html_path"),
                        "score": float(row["score"]),
                        "origin": "legacy_kb",
                    }
                )
                if len(results) >= limit:
                    break
            return results
        except (sqlite3.Error, json.JSONDecodeError, TypeError):
            return []
        finally:
            connection.close()

    async def search_new(
        self,
        query: str,
        limit: int = 10,
        *,
        fund_symbol: str | None = None,
        document_id: str | None = None,
        parts: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        match = _match_query(query)
        if not match:
            return []
        where = ["knowledge_chunks_fts MATCH ?"]
        if not document_id:
            where.append("d.is_current=1")
        parameters: list[Any] = [match]
        if fund_symbol:
            where.append("f.fund_symbol=?")
            parameters.append(fund_symbol)
        if document_id:
            where.append("f.document_id=?")
            parameters.append(document_id)
        target_parts = _target_parts(query, parts)
        if target_parts:
            where.append(f"f.part IN ({','.join('?' for _ in target_parts)})")
            parameters.extend(target_parts)
        parameters.append(max(limit * 4, 20))
        async with self.database.connection() as connection:
            manager_row = await (
                await connection.execute(
                    "SELECT manager_name FROM knowledge_documents "
                    "WHERE manager_name<>'' AND instr(?,manager_name)>0 "
                    "ORDER BY length(manager_name) DESC LIMIT 1",
                    (query,),
                )
            ).fetchone()
            if manager_row:
                where.append("f.manager_name=?")
                parameters.insert(-1, str(manager_row[0]))
            rows = await (
                await connection.execute(
                    "SELECT f.document_id AS doc_id,f.chunk_id,f.fund_symbol,f.fund_name,"
                    "f.manager_name,f.source_type,f.part,f.topic,c.year,f.content,d.title,"
                    "d.source_uri,f.artifact_id,bm25(knowledge_chunks_fts) AS score "
                    "FROM knowledge_chunks_fts f "
                    "JOIN knowledge_chunks c ON c.chunk_id=f.chunk_id "
                    "JOIN knowledge_documents d ON d.document_id=f.document_id "
                    f"WHERE {' AND '.join(where)} ORDER BY score LIMIT ?",
                    parameters,
                )
            ).fetchall()
            if not rows:
                # Trigram FTS cannot match two-character Chinese terms. A known
                # document/chapter is also a stronger scope than a literal title.
                terms = [hint for hint in PART_HINTS if hint in query]
                terms += [term for term in _query_terms(query) if not re.fullmatch(r"part\d+|summary", term)]
                terms = list(dict.fromkeys(terms))[:12]
                scoped_where, scoped_parameters = where[1:], parameters[1:-1]
                base_sql = (
                    "SELECT f.document_id AS doc_id,f.chunk_id,f.fund_symbol,f.fund_name,"
                    "f.manager_name,f.source_type,f.part,f.topic,c.year,f.content,d.title,"
                    "d.source_uri,f.artifact_id,0 AS score FROM knowledge_chunks_fts f "
                    "JOIN knowledge_chunks c ON c.chunk_id=f.chunk_id "
                    "JOIN knowledge_documents d ON d.document_id=f.document_id "
                )
                if terms:
                    like = "(" + " OR ".join("instr(f.content,?)>0" for _ in terms) + ")"
                    rows = await (await connection.execute(
                        base_sql + f"WHERE {' AND '.join([*scoped_where, like])} ORDER BY c.ordinal LIMIT ?",
                        [*scoped_parameters, *terms, limit],
                    )).fetchall()
                if not rows and document_id and target_parts:
                    rows = await (await connection.execute(
                        base_sql + f"WHERE {' AND '.join(scoped_where)} ORDER BY c.ordinal LIMIT ?",
                        [*scoped_parameters, limit],
                    )).fetchall()
        return [dict(row) | {"origin": "new_kb"} for row in rows[:limit]]

    async def search(
        self,
        query: str,
        limit: int = 10,
        *,
        fund_symbol: str | None = None,
        document_id: str | None = None,
        parts: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        legacy = (
            []
            if document_id
            else self.search_legacy(
                query, limit=limit, fund_symbol=fund_symbol, parts=parts
            )
        )
        new = await self.search_new(
            query,
            limit=limit,
            fund_symbol=fund_symbol,
            document_id=document_id,
            parts=parts,
        )
        mentioned_managers = {
            str(item.get("manager_name"))
            for item in [*legacy, *new]
            if item.get("manager_name") and str(item.get("manager_name")) in query
        }
        if mentioned_managers:
            legacy = [
                item for item in legacy if item.get("manager_name") in mentioned_managers
            ]
            new = [item for item in new if item.get("manager_name") in mentioned_managers]
        combined: dict[tuple[str, str], dict[str, Any]] = {}
        target_parts = set(_target_parts(query, parts))
        for source_results in (legacy, new):
            for rank, item in enumerate(source_results, start=1):
                key = (
                    str(item.get("doc_id", "")),
                    str(item.get("content", ""))[:200],
                )
                score = 1 / (60 + rank)
                if item.get("part") in target_parts:
                    score += 0.005
                if document_id and item.get("doc_id") == document_id:
                    score += 0.01
                if key not in combined:
                    combined[key] = item | {"rrf_score": score}
                else:
                    combined[key]["rrf_score"] += score
        return sorted(
            combined.values(), key=lambda row: row["rrf_score"], reverse=True
        )[:limit]
