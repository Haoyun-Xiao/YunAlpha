ALTER TABLE sessions ADD COLUMN runtime_kind TEXT NOT NULL DEFAULT 'legacy_readonly';

CREATE TABLE IF NOT EXISTS agent_threads (
    session_id TEXT PRIMARY KEY REFERENCES sessions(session_id) ON DELETE CASCADE,
    thread_id TEXT NOT NULL UNIQUE,
    runtime_name TEXT NOT NULL DEFAULT 'deep_agents',
    runtime_version TEXT NOT NULL,
    bootstrapped_at TEXT NOT NULL,
    last_checkpoint_id TEXT,
    updated_at TEXT NOT NULL
);

ALTER TABLE agent_runs ADD COLUMN decision_json TEXT;
ALTER TABLE agent_runs ADD COLUMN checkpoint_id TEXT;

CREATE INDEX IF NOT EXISTS idx_agent_runs_session_status
ON agent_runs(session_id, status, created_at DESC);
