from __future__ import annotations

from datetime import UTC, datetime

from fund_research_agent.domain.contracts import ClaimRecord, EvidenceRecord
from fund_research_agent.persistence.database import Database


class EvidenceLedger:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def add_evidence(self, record: EvidenceRecord) -> None:
        async with self.database.connection() as connection:
            await connection.execute(
                "INSERT OR REPLACE INTO evidence(evidence_id,source_type,source_uri,source_hash,"
                "data_as_of,excerpt,fact_type,confidence,fund_symbol,manager_name,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.evidence_id,
                    record.source_type,
                    record.source_uri,
                    record.source_hash,
                    record.data_as_of.isoformat() if record.data_as_of else None,
                    record.excerpt,
                    record.fact_type,
                    record.confidence,
                    record.fund_symbol,
                    record.manager_name,
                    datetime.now(UTC).isoformat(),
                ),
            )
            await connection.commit()

    async def add_claim(self, run_id: str, record: ClaimRecord) -> None:
        async with self.database.connection() as connection:
            await connection.execute(
                "INSERT OR REPLACE INTO claims(claim_id,run_id,claim_text,support_status,confidence,"
                "created_by,created_at) VALUES(?,?,?,?,?,?,?)",
                (
                    record.claim_id,
                    run_id,
                    record.claim_text,
                    record.support_status,
                    record.confidence,
                    record.created_by,
                    datetime.now(UTC).isoformat(),
                ),
            )
            for evidence_id in record.evidence_ids:
                await connection.execute(
                    "INSERT OR IGNORE INTO claim_evidence(claim_id,evidence_id) VALUES(?,?)",
                    (record.claim_id, evidence_id),
                )
            await connection.commit()

    async def unsupported_claims(self, run_id: str) -> list[dict]:
        async with self.database.connection() as connection:
            rows = await (
                await connection.execute(
                    "SELECT * FROM claims WHERE run_id=? AND support_status IN ('unsupported','unchecked')",
                    (run_id,),
                )
            ).fetchall()
        return [dict(row) for row in rows]

