ALTER TABLE knowledge_documents ADD COLUMN pipeline_version TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE knowledge_documents ADD COLUMN data_as_of TEXT;
ALTER TABLE knowledge_documents ADD COLUMN coverage_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE knowledge_documents ADD COLUMN manager_tenure_start TEXT;
CREATE TABLE report_job_sessions (
    job_id TEXT NOT NULL REFERENCES report_jobs(job_id),
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    source_run_id TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(job_id, session_id)
);
