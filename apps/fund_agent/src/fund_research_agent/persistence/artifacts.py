from __future__ import annotations

import json
import mimetypes
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fund_research_agent.domain.contracts import ArtifactRecord
from fund_research_agent.paths import WriteBoundary, sha256_file
from fund_research_agent.persistence.database import Database, json_text


class ArtifactStore:
    def __init__(self, root: Path, boundary: WriteBoundary, database: Database) -> None:
        self.root = boundary.ensure_dir(root)
        self.boundary = boundary
        self.database = database

    async def put_text(
        self,
        kind: str,
        content: str,
        *,
        suffix: str = ".txt",
        metadata: dict[str, Any] | None = None,
        job_id: str | None = None,
    ) -> ArtifactRecord:
        artifact_id = f"artifact_{uuid.uuid4().hex}"
        relative = Path(job_id or "shared") / f"{artifact_id}{suffix}"
        path = self.boundary.write_text(self.root / relative, content)
        record = ArtifactRecord(
            artifact_id=artifact_id,
            kind=kind,
            path=str(path),
            sha256=sha256_file(path),
            size_bytes=path.stat().st_size,
            mime_type=mimetypes.guess_type(path.name)[0] or "text/plain",
            metadata=metadata or {},
            created_at=datetime.now(UTC),
        )
        await self._save(record)
        return record

    async def put_json(
        self,
        kind: str,
        value: Any,
        *,
        metadata: dict[str, Any] | None = None,
        job_id: str | None = None,
    ) -> ArtifactRecord:
        return await self.put_text(
            kind,
            json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
            suffix=".json",
            metadata=metadata,
            job_id=job_id,
        )

    async def put_file(
        self,
        kind: str,
        source: Path,
        *,
        metadata: dict[str, Any] | None = None,
        job_id: str | None = None,
    ) -> ArtifactRecord:
        """Copy a generated file into the immutable Artifact Store."""
        source = source.resolve(strict=True)
        artifact_id = f"artifact_{uuid.uuid4().hex}"
        relative = Path(job_id or "shared") / f"{artifact_id}{source.suffix}"
        destination = self.boundary.assert_writable(self.root / relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        record = ArtifactRecord(
            artifact_id=artifact_id,
            kind=kind,
            path=str(destination),
            sha256=sha256_file(destination),
            size_bytes=destination.stat().st_size,
            mime_type=mimetypes.guess_type(destination.name)[0] or "application/octet-stream",
            metadata=metadata or {},
            created_at=datetime.now(UTC),
        )
        await self._save(record)
        return record

    async def _save(self, record: ArtifactRecord) -> None:
        async with self.database.connection() as connection:
            await connection.execute(
                "INSERT INTO artifacts(artifact_id,kind,path,sha256,size_bytes,mime_type,metadata_json,created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    record.artifact_id,
                    record.kind,
                    record.path,
                    record.sha256,
                    record.size_bytes,
                    record.mime_type,
                    json_text(record.metadata),
                    record.created_at.isoformat(),
                ),
            )
            await connection.commit()

    async def get(self, artifact_id: str) -> ArtifactRecord | None:
        async with self.database.connection() as connection:
            row = await (
                await connection.execute(
                    "SELECT * FROM artifacts WHERE artifact_id=?", (artifact_id,)
                )
            ).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["metadata"] = json.loads(data.pop("metadata_json"))
        return ArtifactRecord(**data)

    async def list_for_job(self, job_id: str) -> list[ArtifactRecord]:
        async with self.database.connection() as connection:
            rows = await (
                await connection.execute(
                    "SELECT * FROM artifacts WHERE json_extract(metadata_json,'$.job_id')=? "
                    "ORDER BY created_at",
                    (job_id,),
                )
            ).fetchall()
        records = []
        for row in rows:
            data = dict(row)
            data["metadata"] = json.loads(data.pop("metadata_json"))
            records.append(ArtifactRecord(**data))
        return records
