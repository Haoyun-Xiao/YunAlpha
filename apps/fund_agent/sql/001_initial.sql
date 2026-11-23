PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL DEFAULT 'local',
    title TEXT NOT NULL DEFAULT '新研究会话',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_runs (
    run_id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(session_id) ON DELETE SET NULL,
    status TEXT NOT NULL,
    route_json TEXT,
    stop_reason TEXT,
    usage_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    status TEXT NOT NULL,
    owner_agent TEXT,
    input_json TEXT NOT NULL DEFAULT '{}',
    output_json TEXT NOT NULL DEFAULT '{}',
    acceptance_json TEXT NOT NULL DEFAULT '[]',
    attempt INTEGER NOT NULL DEFAULT 0,
    idempotency_key TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_dependencies (
    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    depends_on TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    PRIMARY KEY (task_id, depends_on)
);

CREATE TABLE IF NOT EXISTS tool_calls (
    tool_call_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
    tool_name TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    status TEXT NOT NULL,
    result_json TEXT,
    error_code TEXT,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS report_jobs (
    job_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    request_json TEXT NOT NULL,
    status TEXT NOT NULL,
    current_stage TEXT NOT NULL,
    progress REAL NOT NULL DEFAULT 0,
    warnings_json TEXT NOT NULL DEFAULT '[]',
    errors_json TEXT NOT NULL DEFAULT '[]',
    input_fingerprint TEXT NOT NULL,
    workflow_version TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 0,
    lease_owner TEXT,
    lease_expires_at TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    artifact_ids_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_report_jobs_fingerprint_active
ON report_jobs(input_fingerprint)
WHERE status NOT IN ('failed', 'failed_validation', 'cancelled');

CREATE TABLE IF NOT EXISTS workflow_steps (
    job_id TEXT NOT NULL REFERENCES report_jobs(job_id) ON DELETE CASCADE,
    step_id TEXT NOT NULL,
    status TEXT NOT NULL,
    input_hash TEXT,
    output_artifact_id TEXT,
    attempt INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    started_at TEXT,
    finished_at TEXT,
    PRIMARY KEY (job_id, step_id)
);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    mime_type TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, event_id);

CREATE TABLE IF NOT EXISTS memories (
    memory_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL DEFAULT 'local',
    scope TEXT NOT NULL DEFAULT 'global',
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    source_message_id TEXT,
    confidence REAL NOT NULL DEFAULT 1,
    confirmed INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS new_knowledge_fts USING fts5(
    doc_id UNINDEXED,
    fund_symbol UNINDEXED,
    source_type UNINDEXED,
    title,
    content,
    artifact_id UNINDEXED,
    tokenize='unicode61'
);

