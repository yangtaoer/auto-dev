"""Additive schema; artwork is data, not a new application release."""
SCHEMA = """
CREATE TABLE IF NOT EXISTS theme_designs (
 id TEXT PRIMARY KEY, owner_id INTEGER NOT NULL REFERENCES users(id), name TEXT NOT NULL,
 base_theme_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft', is_public INTEGER NOT NULL DEFAULT 0,
 approved_design_id TEXT, current_revision_id TEXT, live_revision_id TEXT,
 create_key TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(owner_id,create_key)
);
CREATE TABLE IF NOT EXISTS theme_jobs (
 id TEXT PRIMARY KEY, design_id TEXT NOT NULL REFERENCES theme_designs(id), actor_id INTEGER REFERENCES users(id),
 kind TEXT NOT NULL, question TEXT NOT NULL DEFAULT '', answer TEXT NOT NULL DEFAULT '',
 input_json TEXT NOT NULL DEFAULT '{}', output_json TEXT NOT NULL DEFAULT '{}',
 status TEXT NOT NULL DEFAULT 'queued', runner_id TEXT NOT NULL, model_config TEXT NOT NULL DEFAULT '{}',
 claim_token TEXT NOT NULL DEFAULT '', thread_id TEXT NOT NULL DEFAULT '', progress TEXT NOT NULL DEFAULT '',
 error_message TEXT NOT NULL DEFAULT '', idempotency_key TEXT NOT NULL, payload_hash TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, started_at TEXT, completed_at TEXT,
 UNIQUE(design_id,idempotency_key)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_theme_design_active ON theme_jobs(design_id)
 WHERE status IN ('queued','running') AND kind IN ('design','production','publish','share');
CREATE INDEX IF NOT EXISTS ix_theme_jobs_design_time ON theme_jobs(design_id,created_at DESC,id DESC);
CREATE INDEX IF NOT EXISTS ix_theme_jobs_claim ON theme_jobs(runner_id,status,created_at);
CREATE INDEX IF NOT EXISTS ix_theme_design_owner ON theme_designs(owner_id,updated_at DESC);
CREATE TABLE IF NOT EXISTS theme_assets (
 id TEXT PRIMARY KEY, design_id TEXT NOT NULL REFERENCES theme_designs(id), job_id TEXT REFERENCES theme_jobs(id),
 kind TEXT NOT NULL, name TEXT NOT NULL, local_path TEXT NOT NULL, mime TEXT NOT NULL,
 sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL,
 external_url TEXT NOT NULL DEFAULT '', url_expires_at TEXT, oss_key TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS theme_revisions (
 id TEXT PRIMARY KEY, design_id TEXT NOT NULL REFERENCES theme_designs(id), job_id TEXT NOT NULL UNIQUE REFERENCES theme_jobs(id),
 number INTEGER NOT NULL, stage TEXT NOT NULL, preview_asset_id TEXT NOT NULL REFERENCES theme_assets(id),
 background_asset_id TEXT REFERENCES theme_assets(id), palette TEXT NOT NULL DEFAULT '{}',
 created_at TEXT NOT NULL, UNIQUE(design_id,number)
);
CREATE INDEX IF NOT EXISTS ix_theme_assets_design ON theme_assets(design_id,created_at);
CREATE TABLE IF NOT EXISTS theme_usage_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id), theme_id TEXT NOT NULL,
 kind TEXT NOT NULL, dedupe_key TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(user_id,theme_id,kind,dedupe_key)
);
CREATE INDEX IF NOT EXISTS ix_theme_usage_theme ON theme_usage_events(theme_id,kind,created_at);
CREATE TABLE IF NOT EXISTS theme_mail_preferences (
 user_id INTEGER PRIMARY KEY REFERENCES users(id), recommendations INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS theme_mail_deliveries (
 delivery_key TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES theme_jobs(id),
 status TEXT NOT NULL, result_json TEXT NOT NULL DEFAULT '{}', error_message TEXT NOT NULL DEFAULT '',
 updated_at TEXT NOT NULL
);
"""
