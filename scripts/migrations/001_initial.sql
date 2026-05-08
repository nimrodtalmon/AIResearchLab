-- Paper Factory initial schema. See DESIGN.md §5.1.
-- WAL mode is set by the orchestrator on first connect, not here.

CREATE TABLE IF NOT EXISTS papers (
    id TEXT PRIMARY KEY,
    title TEXT,
    status TEXT NOT NULL,                    -- idea|formal|drafting|reviewing|revising|submission_ready|submitted|killed|shelved
    workspace_path TEXT,
    target_venue TEXT,
    paused INTEGER DEFAULT 0,
    started_at TIMESTAMP,
    last_activity TIMESTAMP,
    spend_usd REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY,
    paper_id TEXT REFERENCES papers(id),
    role TEXT NOT NULL,
    persona_name TEXT,
    work_type TEXT,                          -- formalization|theory|simulation|writing|review
    kind TEXT NOT NULL,                      -- decision_needed|fyi|stuck_no_question
    status TEXT NOT NULL,                    -- waiting|resolved|deferred|escalated|archived
    severity TEXT NOT NULL,                  -- green|yellow|red
    summary TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TIMESTAMP,
    resolved_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_reports_paper_status ON reports(paper_id, status);
CREATE INDEX IF NOT EXISTS idx_reports_kind_status ON reports(kind, status);

CREATE TABLE IF NOT EXISTS remarks (
    id TEXT PRIMARY KEY,
    report_id TEXT REFERENCES reports(id),
    paper_id TEXT,
    role TEXT,
    text TEXT NOT NULL,
    pi_tagged_for_principle INTEGER DEFAULT 0,
    suggested_scope TEXT,                    -- card|paper|role|lab
    created_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS principles (
    id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    scope TEXT NOT NULL,                     -- lab | role:<name> | project:<id>
    derived_from_remarks TEXT,
    version INTEGER DEFAULT 1,
    created_at TIMESTAMP,
    deprecated_at TIMESTAMP,
    deprecated_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_principles_scope ON principles(scope, deprecated_at);

CREATE TABLE IF NOT EXISTS principle_candidates (
    id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    suggested_scope TEXT,
    source_remark_ids TEXT,
    created_at TIMESTAMP,
    decided_at TIMESTAMP,
    decision TEXT                            -- promoted|edited|dismissed
);

CREATE TABLE IF NOT EXISTS junior_pools (
    role TEXT PRIMARY KEY,
    tasks_completed INTEGER DEFAULT 0,
    last_task_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS seniors (
    role TEXT NOT NULL,
    name TEXT NOT NULL,
    tasks_completed INTEGER DEFAULT 0,
    last_task_at TIMESTAMP,
    memory_path TEXT,
    promoted_at TIMESTAMP,
    archived_at TIMESTAMP,
    PRIMARY KEY (role, name)
);

CREATE TABLE IF NOT EXISTS task_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL,
    persona_name TEXT,
    paper_id TEXT,
    report_id TEXT REFERENCES reports(id),
    outcome TEXT NOT NULL,                   -- accept|rework|kill
    rated_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_task_outcomes_role ON task_outcomes(role, persona_name, rated_at DESC);

CREATE TABLE IF NOT EXISTS literature (
    id TEXT PRIMARY KEY,
    title TEXT,
    venue TEXT,
    year INTEGER,
    abstract TEXT,
    indexed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS literature_embeddings (
    literature_id TEXT REFERENCES literature(id),
    embedding BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS gaps (
    id TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    supporting_papers TEXT,
    novelty_score REAL,
    status TEXT NOT NULL,                    -- candidate|formalized|killed|shipped
    created_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS budget_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id TEXT,
    role TEXT,
    persona_name TEXT,
    model TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    latency_ms INTEGER,
    timestamp TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_budget_log_paper_ts ON budget_log(paper_id, timestamp);

CREATE TABLE IF NOT EXISTS pi_contributions (
    id TEXT PRIMARY KEY,
    paper_id TEXT REFERENCES papers(id),
    target_kind TEXT NOT NULL,               -- section|claim|sim|reference
    target_id TEXT NOT NULL,
    text TEXT NOT NULL,
    consumed_at TIMESTAMP,
    created_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP
);
