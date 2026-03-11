"""SQL schema definitions for state.db and logs.db.

Two separate databases:
- state.db: persistent state, memory, knowledge, goals (small, frequent writes)
- logs.db: action logs, UIA events (append-heavy, periodic cleanup)
"""

SCHEMA_VERSION = 2

STATE_SCHEMA = """
-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER NOT NULL,
    applied_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    description TEXT
);

-- Key-value system state
CREATE TABLE IF NOT EXISTS system_state (
    key         TEXT PRIMARY KEY,
    value       TEXT,
    value_type  TEXT DEFAULT 'string',
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    category    TEXT DEFAULT 'general'
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_system_state_category
    ON system_state(category);

-- Tiered memory store
CREATE TABLE IF NOT EXISTS memory (
    id            TEXT PRIMARY KEY,
    tier          TEXT NOT NULL CHECK (tier IN ('mid', 'long')),
    category      TEXT NOT NULL,
    content       TEXT NOT NULL,
    relevance     REAL DEFAULT 1.0,
    access_count  INTEGER DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    expires_at    TEXT,
    last_accessed TEXT,
    tags          TEXT DEFAULT '[]'
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_memory_tier
    ON memory(tier);
CREATE INDEX IF NOT EXISTS idx_memory_category
    ON memory(tier, category);
CREATE INDEX IF NOT EXISTS idx_memory_expires
    ON memory(expires_at) WHERE expires_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_memory_relevance
    ON memory(tier, relevance DESC);

-- Application knowledge base
CREATE TABLE IF NOT EXISTS app_knowledge (
    app_name        TEXT PRIMARY KEY,
    display_name    TEXT,
    framework       TEXT,
    preferred_input TEXT DEFAULT 'uia',
    reliability     REAL DEFAULT 0.5,
    total_actions   INTEGER DEFAULT 0,
    success_actions INTEGER DEFAULT 0,
    known_elements  TEXT DEFAULT '{}',
    known_dialogs   TEXT DEFAULT '[]',
    quirks          TEXT DEFAULT '{}',
    cdp_port        INTEGER,
    first_seen      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    last_seen       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) WITHOUT ROWID;

-- Cached element locations per app
CREATE TABLE IF NOT EXISTS app_elements (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    app_name      TEXT NOT NULL REFERENCES app_knowledge(app_name) ON DELETE CASCADE,
    element_name  TEXT,
    automation_id TEXT,
    class_name    TEXT,
    control_type  TEXT,
    method        TEXT DEFAULT 'uia',
    selector      TEXT,
    coordinates   TEXT,
    confidence    REAL DEFAULT 0.5,
    hit_count     INTEGER DEFAULT 0,
    miss_count    INTEGER DEFAULT 0,
    last_used     TEXT
);

CREATE INDEX IF NOT EXISTS idx_app_elements_app
    ON app_elements(app_name);
CREATE INDEX IF NOT EXISTS idx_app_elements_lookup
    ON app_elements(app_name, element_name);

-- Goal tracking (hierarchical)
CREATE TABLE IF NOT EXISTS goals (
    id           TEXT PRIMARY KEY,
    parent_id    TEXT REFERENCES goals(id) ON DELETE CASCADE,
    title        TEXT NOT NULL,
    description  TEXT,
    status       TEXT DEFAULT 'pending'
                 CHECK (status IN ('pending', 'active', 'completed', 'failed', 'cancelled')),
    priority     INTEGER DEFAULT 5,
    plan         TEXT,
    result       TEXT,
    error        TEXT,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    started_at   TEXT,
    completed_at TEXT,
    metadata     TEXT DEFAULT '{}'
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_goals_parent
    ON goals(parent_id);
CREATE INDEX IF NOT EXISTS idx_goals_status
    ON goals(status);
CREATE INDEX IF NOT EXISTS idx_goals_active
    ON goals(status, priority) WHERE status IN ('pending', 'active');

-- Error patterns and solutions
CREATE TABLE IF NOT EXISTS error_patterns (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    app_name         TEXT,
    tool_name        TEXT,
    error_type       TEXT,
    error_message    TEXT,
    context          TEXT DEFAULT '{}',
    solution         TEXT,
    solution_worked  INTEGER DEFAULT 0,
    occurrence_count INTEGER DEFAULT 1,
    first_seen       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    last_seen        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_error_patterns_lookup
    ON error_patterns(app_name, tool_name, error_type, solution_worked, last_seen DESC);

-- User behavior patterns
CREATE TABLE IF NOT EXISTS user_patterns (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_type     TEXT,
    app_name         TEXT,
    action           TEXT,
    day_of_week      INTEGER,
    hour             INTEGER,
    occurrence_count INTEGER DEFAULT 1,
    confidence       REAL DEFAULT 0.0,
    context          TEXT DEFAULT '{}',
    first_seen       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    last_seen        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(pattern_type, app_name, action, day_of_week, hour)
);

CREATE INDEX IF NOT EXISTS idx_user_patterns_schedule
    ON user_patterns(day_of_week, hour);
CREATE INDEX IF NOT EXISTS idx_user_patterns_app
    ON user_patterns(app_name, day_of_week, hour);

-- Hourly aggregated metrics
CREATE TABLE IF NOT EXISTS metrics_hourly (
    period_start   TEXT,
    tool_name      TEXT,
    app_name       TEXT,
    total_actions  INTEGER DEFAULT 0,
    success_count  INTEGER DEFAULT 0,
    failure_count  INTEGER DEFAULT 0,
    avg_score      REAL,
    avg_duration_ms REAL,
    p95_duration_ms REAL,
    PRIMARY KEY(period_start, tool_name, app_name)
) WITHOUT ROWID;

-- State snapshots for rollback
CREATE TABLE IF NOT EXISTS snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    reason      TEXT,
    state_json  TEXT,
    goals_json  TEXT,
    size_bytes  INTEGER
);

-- Method selection journal (migrated from error_journal.json)
CREATE TABLE IF NOT EXISTS method_journal (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tool            TEXT NOT NULL,
    app             TEXT NOT NULL,
    window          TEXT,
    method_failed   TEXT NOT NULL,
    method_worked   TEXT,
    error_message   TEXT,
    params          TEXT,
    success_count   INTEGER DEFAULT 0,
    failure_count   INTEGER DEFAULT 1,
    first_seen      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    last_seen       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(tool, app, method_failed)
);

CREATE INDEX IF NOT EXISTS idx_method_journal_lookup
    ON method_journal(tool, app);
"""

LOGS_SCHEMA = """
-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER NOT NULL,
    applied_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    description TEXT
);

-- Action logs (append-heavy)
CREATE TABLE IF NOT EXISTS action_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id         TEXT,
    tool_name       TEXT NOT NULL,
    app_name        TEXT,
    action_type     TEXT NOT NULL,
    parameters      TEXT DEFAULT '{}',
    state_before    TEXT,
    state_after     TEXT,
    result          TEXT,
    success         INTEGER DEFAULT 1,
    score           REAL,
    duration_ms     INTEGER,
    error_message   TEXT,
    decision_reason TEXT,
    timestamp       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    ts_date         TEXT GENERATED ALWAYS AS (substr(timestamp, 1, 10)) STORED,
    ts_hour         INTEGER GENERATED ALWAYS AS (CAST(substr(timestamp, 12, 2) AS INTEGER)) STORED
);

CREATE INDEX IF NOT EXISTS idx_logs_timestamp
    ON action_logs(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_logs_goal
    ON action_logs(goal_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_logs_tool
    ON action_logs(tool_name, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_logs_app_tool
    ON action_logs(app_name, tool_name, success);
CREATE INDEX IF NOT EXISTS idx_logs_date
    ON action_logs(ts_date);
CREATE INDEX IF NOT EXISTS idx_logs_score
    ON action_logs(tool_name, score) WHERE score IS NOT NULL;

-- UIA events (high-volume, short retention)
CREATE TABLE IF NOT EXISTS uia_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type   TEXT NOT NULL,
    window_title TEXT,
    process_name TEXT,
    element_name TEXT,
    details      TEXT DEFAULT '{}',
    timestamp    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_uia_timestamp
    ON uia_events(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_uia_type
    ON uia_events(event_type, timestamp DESC);
"""
