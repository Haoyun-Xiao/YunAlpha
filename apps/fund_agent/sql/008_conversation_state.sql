ALTER TABLE session_context ADD COLUMN pending_intent TEXT;
ALTER TABLE session_context ADD COLUMN pending_request_json TEXT NOT NULL DEFAULT '{}';
