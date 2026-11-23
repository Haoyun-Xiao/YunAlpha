from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from industry_agent.config import Settings


def uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def now() -> str:
    return datetime.now(UTC).isoformat()


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, allow_nan=False).encode()
    ).hexdigest()


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, allow_nan=False)


class Store:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.var = settings.var.resolve()
        self.var.mkdir(parents=True, exist_ok=True)
        self.db_path = self.path("state/industry.sqlite")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as c:
            c.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,session_id TEXT,status TEXT NOT NULL,idempotency_key TEXT UNIQUE,payload TEXT NOT NULL,lease_owner TEXT,lease_expires REAL);
                CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,entity_id TEXT NOT NULL,session_id TEXT,type TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS event_entity ON events(entity_id,id);
                CREATE TABLE IF NOT EXISTS steps(job_id TEXT NOT NULL,name TEXT NOT NULL,input_hash TEXT NOT NULL,status TEXT NOT NULL,payload TEXT,attempt INTEGER NOT NULL DEFAULT 1,PRIMARY KEY(job_id,name));
                CREATE TABLE IF NOT EXISTS reports(id TEXT PRIMARY KEY,job_id TEXT UNIQUE,chain_id TEXT,version TEXT,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS knowledge(id TEXT PRIMARY KEY,report_id TEXT,title TEXT NOT NULL,content TEXT NOT NULL,kind TEXT NOT NULL);
                CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(id UNINDEXED,report_id UNINDEXED,title,content,tokenize='trigram');
                CREATE TABLE IF NOT EXISTS services(name TEXT PRIMARY KEY,payload TEXT NOT NULL);
            """)

    @contextmanager
    def connection(self):
        c = sqlite3.connect(self.db_path, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def path(self, relative: str) -> Path:
        p = (self.var / relative).resolve()
        if not p.is_relative_to(self.var):
            raise ValueError("write outside project var is forbidden")
        return p

    def write_json(self, relative: str, value: Any) -> Path:
        return self.write_text(relative, encode(value))

    def write_text(self, relative: str, text: str) -> Path:
        p = self.path(relative)
        p.parent.mkdir(parents=True, exist_ok=True)
        temp = p.with_name(p.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            temp.write_text(text, encoding="utf-8")
            temp.replace(p)
        finally:
            temp.unlink(missing_ok=True)
        return p

    def create_job(
        self,
        anchor: str,
        sid: str | None,
        as_of: str,
        idempotency_key: str | None = None,
        *,
        run_id: str | None = None,
    ) -> dict:
        j = {
            "job_id": uid("job"),
            "session_id": sid,
            "run_id": run_id,
            "anchor": anchor,
            "as_of": as_of,
            "status": "queued",
            "stage": "queued",
            "progress": 0,
            "created_at": now(),
            "updated_at": now(),
            "warnings": [],
            "error": None,
            "report_id": None,
            "segments": [],
            "live": {},
            "attempt": 0,
            "cancel_requested": False,
        }
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            if idempotency_key:
                old = c.execute(
                    "SELECT payload FROM jobs WHERE idempotency_key=?", [idempotency_key]
                ).fetchone()
                if old:
                    return json.loads(old[0])
            self.write_json(f"jobs/{j['job_id']}/request.json", j)
            c.execute(
                "INSERT INTO jobs(id,session_id,status,idempotency_key,payload) VALUES(?,?,?,?,?)",
                [j["job_id"], sid, j["status"], idempotency_key, encode(j)],
            )
        self.emit(j["job_id"], sid, "job_created", j)
        return j

    def job(self, jid: str) -> dict:
        with self.connection() as c:
            r = c.execute("SELECT payload FROM jobs WHERE id=?", [jid]).fetchone()
        if not r:
            raise KeyError("研究任务不存在")
        return json.loads(r[0])

    def latest_job(self, sid: str) -> dict | None:
        with self.connection() as c:
            row = c.execute(
                "SELECT payload FROM jobs WHERE session_id=? ORDER BY rowid DESC LIMIT 1", [sid]
            ).fetchone()
        return json.loads(row[0]) if row else None

    def update_job(self, jid: str, **changes) -> dict:
        return self.transition_job(jid, lambda _: changes)

    def transition_job(self, jid: str, transition, *, owner: str | None = None) -> dict:
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT payload,lease_owner FROM jobs WHERE id=?", [jid]).fetchone()
            if not row:
                raise KeyError(jid)
            j = json.loads(row["payload"])
            if owner is not None and owner != row["lease_owner"]:
                return j
            changes = transition(j)
            if changes:
                j.update(changes, updated_at=now())
                c.execute(
                    "UPDATE jobs SET status=?,payload=? WHERE id=?", [j["status"], encode(j), jid]
                )
        return j

    def claim(self, owner: str, limit: int) -> dict | None:
        t = time.time()
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            n = c.execute("SELECT count(*) FROM jobs WHERE lease_expires>?", [t]).fetchone()[0]
            if n >= limit:
                return None
            row = c.execute(
                "SELECT id,payload FROM jobs WHERE status IN ('queued','running') AND COALESCE(lease_expires,0)<? ORDER BY rowid LIMIT 1",
                [t],
            ).fetchone()
            if not row:
                return None
            j = {**json.loads(row["payload"]), "status": "running", "updated_at": now()}
            j["attempt"] += 1
            c.execute(
                "UPDATE jobs SET status='running',lease_owner=?,lease_expires=?,payload=? WHERE id=?",
                [owner, t + 90, encode(j), row["id"]],
            )
        return j

    def heartbeat(self, jid: str, owner: str) -> bool:
        with self.connection() as c:
            r = c.execute(
                "UPDATE jobs SET lease_expires=? WHERE id=? AND lease_owner=? AND status='running'",
                [time.time() + 90, jid, owner],
            )
        return r.rowcount > 0

    def release(self, jid: str, owner: str):
        with self.connection() as c:
            c.execute(
                "UPDATE jobs SET lease_owner=NULL,lease_expires=NULL WHERE id=? AND lease_owner=?",
                [jid, owner],
            )

    def emit(self, entity_id: str, sid: str | None, kind: str, data: dict) -> int:
        payload = {"session_id": sid, **data}
        with self.connection() as c:
            cursor = c.execute(
                "INSERT INTO events(entity_id,session_id,type,payload,created_at) VALUES(?,?,?,?,?)",
                [entity_id, sid, kind, encode(payload), now()],
            )
            return cursor.lastrowid

    def events(self, entity_id: str, after: int = 0) -> list[dict]:
        with self.connection() as c:
            rows = c.execute(
                "SELECT * FROM events WHERE entity_id=? AND id>? ORDER BY id LIMIT 300",
                [entity_id, after],
            ).fetchall()
        return [
            {
                "id": r["id"],
                "type": r["type"],
                "data": json.loads(r["payload"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def step(self, jid: str, name: str, input_hash: str) -> dict | None:
        with self.connection() as c:
            row = c.execute(
                "SELECT payload FROM steps WHERE job_id=? AND name=? AND input_hash=? AND status='completed'",
                [jid, name, input_hash],
            ).fetchone()
        return json.loads(row[0]) if row else None

    def save_step(self, jid: str, name: str, input_hash: str, status: str, payload: Any = None):
        with self.connection() as c:
            c.execute(
                """INSERT INTO steps(job_id,name,input_hash,status,payload) VALUES(?,?,?,?,?)
                ON CONFLICT(job_id,name) DO UPDATE SET input_hash=excluded.input_hash,status=excluded.status,payload=excluded.payload,
                attempt=CASE WHEN excluded.status='running' THEN steps.attempt+1 ELSE steps.attempt END""",
                [jid, name, input_hash, status, encode(payload)],
            )

    def save_report(self, report: dict):
        with self.connection() as c:
            c.execute(
                "INSERT INTO reports VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                [
                    report["report_id"],
                    report["job_id"],
                    report["chain_id"],
                    report["version"],
                    encode(report),
                ],
            )

    def report(self, rid: str) -> dict:
        with self.connection() as c:
            r = c.execute("SELECT payload FROM reports WHERE id=?", [rid]).fetchone()
        if not r:
            raise KeyError("报告不存在")
        return json.loads(r[0])

    def reports(self) -> list[dict]:
        with self.connection() as c:
            rows = c.execute("SELECT payload FROM reports ORDER BY rowid DESC").fetchall()
        return [json.loads(r[0]) for r in rows]

    def index(self, report: dict, documents: list[dict]):
        from industry_agent.render import render_markdown

        text = render_markdown(report)
        items = [
            (
                f"{report['report_id']}_{i}",
                report["report_id"],
                report["title"],
                text[start : start + 3000],
                "report",
            )
            for i, start in enumerate(range(0, len(text), 2700))
        ]
        items.extend(
            (
                d["evidence_id"],
                report["report_id"],
                d.get("title", ""),
                d.get("content", ""),
                "document",
            )
            for d in documents
            if d.get("content")
        )
        with self.connection() as c:
            c.execute(
                "DELETE FROM knowledge_fts WHERE id IN (SELECT id FROM knowledge WHERE report_id=?)",
                [report["report_id"]],
            )
            c.execute("DELETE FROM knowledge WHERE report_id=?", [report["report_id"]])
            for item in items:
                # One physical document may support multiple reports; include report in index identity.
                item = (digest([item[0], report["report_id"]]), *item[1:])
                c.execute("INSERT OR REPLACE INTO knowledge VALUES(?,?,?,?,?)", item)
                c.execute("DELETE FROM knowledge_fts WHERE id=?", [item[0]])
                c.execute("INSERT INTO knowledge_fts VALUES(?,?,?,?)", item[:4])

    def search(self, query: str, report_id: str | None = None) -> list[dict]:
        query = query.strip()[:200]
        if not query:
            return []
        with self.connection() as c:
            try:
                rows = c.execute(
                    "SELECT id,report_id,title,content FROM knowledge_fts WHERE knowledge_fts MATCH ?"
                    + (" AND report_id=?" if report_id else "")
                    + " ORDER BY rank LIMIT 5",
                    ['"' + query.replace('"', '""') + '"', *([report_id] if report_id else [])],
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
            if not rows:
                rows = c.execute(
                    "SELECT id,report_id,title,content FROM knowledge WHERE content LIKE ?"
                    + (" AND report_id=?" if report_id else "")
                    + " LIMIT 5",
                    ["%" + query + "%", *([report_id] if report_id else [])],
                ).fetchall()
        return [{**dict(r), "content": r["content"][:5000]} for r in rows]

    def service(self, name: str, **payload):
        with self.connection() as c:
            c.execute(
                "INSERT OR REPLACE INTO services VALUES(?,?)",
                [name, encode({"last_seen": now(), **payload})],
            )

    def service_status(self, name: str) -> dict:
        with self.connection() as c:
            r = c.execute("SELECT payload FROM services WHERE name=?", [name]).fetchone()
        return json.loads(r[0]) if r else {}
