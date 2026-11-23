ALTER TABLE report_jobs ADD COLUMN session_id TEXT REFERENCES sessions(session_id) ON DELETE SET NULL;
ALTER TABLE report_jobs ADD COLUMN source_run_id TEXT REFERENCES agent_runs(run_id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_report_jobs_session
ON report_jobs(session_id, created_at DESC);

CREATE TABLE IF NOT EXISTS session_context (
    session_id TEXT PRIMARY KEY REFERENCES sessions(session_id) ON DELETE CASCADE,
    active_funds_json TEXT NOT NULL DEFAULT '[]',
    active_manager TEXT,
    active_report_job_id TEXT,
    active_report_document_id TEXT,
    last_intent TEXT,
    last_metric_terms_json TEXT NOT NULL DEFAULT '[]',
    last_years_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL
);
