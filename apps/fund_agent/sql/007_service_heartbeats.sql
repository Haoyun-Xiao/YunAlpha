CREATE TABLE IF NOT EXISTS service_heartbeats (
    service_name TEXT PRIMARY KEY,
    instance_id TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
