CREATE TABLE IF NOT EXISTS knowledge_documents (
    document_id TEXT PRIMARY KEY,
    logical_key TEXT NOT NULL,
    source_type TEXT NOT NULL,
    fund_symbol TEXT NOT NULL,
    fund_name TEXT NOT NULL,
    manager_name TEXT,
    as_of TEXT,
    title TEXT NOT NULL,
    artifact_id TEXT REFERENCES artifacts(artifact_id) ON DELETE SET NULL,
    job_id TEXT REFERENCES report_jobs(job_id) ON DELETE SET NULL,
    source_uri TEXT,
    source_hash TEXT NOT NULL,
    is_current INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source_type, source_hash)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_documents_logical
ON knowledge_documents(logical_key, is_current, updated_at DESC);

CREATE TABLE IF NOT EXISTS knowledge_chunks (
    chunk_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES knowledge_documents(document_id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    part TEXT,
    topic TEXT,
    year TEXT,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(document_id, ordinal, content_hash)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_document
ON knowledge_chunks(document_id, ordinal);

CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_chunks_fts USING fts5(
    chunk_id UNINDEXED,
    document_id UNINDEXED,
    fund_symbol UNINDEXED,
    fund_name,
    manager_name,
    source_type UNINDEXED,
    part UNINDEXED,
    topic,
    content,
    artifact_id UNINDEXED,
    tokenize='trigram'
);
