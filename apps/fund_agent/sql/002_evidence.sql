CREATE TABLE IF NOT EXISTS evidence (
    evidence_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_uri TEXT,
    source_hash TEXT,
    data_as_of TEXT,
    excerpt TEXT NOT NULL,
    fact_type TEXT NOT NULL,
    confidence REAL NOT NULL,
    fund_symbol TEXT,
    manager_name TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claims (
    claim_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    claim_text TEXT NOT NULL,
    support_status TEXT NOT NULL,
    confidence REAL NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claim_evidence (
    claim_id TEXT NOT NULL REFERENCES claims(claim_id) ON DELETE CASCADE,
    evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id) ON DELETE CASCADE,
    PRIMARY KEY (claim_id, evidence_id)
);

CREATE TABLE IF NOT EXISTS eval_runs (
    eval_run_id TEXT PRIMARY KEY,
    suite_name TEXT NOT NULL,
    candidate_version TEXT NOT NULL,
    status TEXT NOT NULL,
    metrics_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS eval_cases (
    eval_run_id TEXT NOT NULL REFERENCES eval_runs(eval_run_id) ON DELETE CASCADE,
    case_id TEXT NOT NULL,
    status TEXT NOT NULL,
    result_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (eval_run_id, case_id)
);

