"""Idempotent storage schema for economic runtime facts and viability reports.

The economics tables are intentionally isolated from trading state. Creating or
querying them cannot mutate opportunities, proposals, risk, orders, positions,
or Learning authority.
"""

from __future__ import annotations

import sqlite3


ECONOMICS_SCHEMA_VERSION = "economic-viability-v1"

ECONOMICS_DDL = """
CREATE TABLE IF NOT EXISTS economics_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
INSERT OR IGNORE INTO economics_settings(key,value,updated_at)
VALUES ('cost_tracing_enabled','1',datetime('now'));

CREATE TABLE IF NOT EXISTS pricing_snapshots (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    effective_from TEXT NOT NULL,
    input_usd_per_million REAL NOT NULL CHECK(input_usd_per_million >= 0),
    output_usd_per_million REAL NOT NULL CHECK(output_usd_per_million >= 0),
    reasoning_usd_per_million REAL,
    cached_input_usd_per_million REAL,
    currency TEXT NOT NULL DEFAULT 'USD',
    source TEXT NOT NULL,
    version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(provider, model, effective_from, version)
);
CREATE INDEX IF NOT EXISTS idx_pricing_snapshots_lookup
    ON pricing_snapshots(provider, model, effective_from DESC);

CREATE TABLE IF NOT EXISTS tool_cost_profiles (
    id TEXT PRIMARY KEY,
    tool_name TEXT NOT NULL,
    effective_from TEXT NOT NULL,
    usd_per_call REAL NOT NULL CHECK(usd_per_call >= 0),
    currency TEXT NOT NULL DEFAULT 'USD',
    source TEXT NOT NULL,
    version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(tool_name, effective_from, version)
);
CREATE INDEX IF NOT EXISTS idx_tool_cost_profiles_lookup
    ON tool_cost_profiles(tool_name, effective_from DESC);

CREATE TABLE IF NOT EXISTS runtime_cost_events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL CHECK(event_type IN ('llm','tool','data','infrastructure','other')),
    run_id TEXT,
    runtime_session_id TEXT,
    opportunity_id TEXT,
    decision_id TEXT,
    proposal_id TEXT,
    call_id TEXT,
    provider TEXT,
    model_or_tool TEXT NOT NULL,
    requested_model TEXT,
    attempt_index INTEGER NOT NULL DEFAULT 0 CHECK(attempt_index >= 0),
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    latency_ms REAL,
    usage_json TEXT NOT NULL DEFAULT '{}',
    pricing_snapshot_id TEXT REFERENCES pricing_snapshots(id),
    cost_profile_id TEXT,
    estimated_cost_usd REAL,
    currency TEXT NOT NULL DEFAULT 'USD',
    cost_status TEXT NOT NULL CHECK(cost_status IN ('known','estimated','unknown')),
    error_type TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(call_id, attempt_index, event_type)
);
CREATE INDEX IF NOT EXISTS idx_runtime_cost_events_run
    ON runtime_cost_events(run_id, started_at);
CREATE INDEX IF NOT EXISTS idx_runtime_cost_events_window
    ON runtime_cost_events(event_type, started_at);
CREATE INDEX IF NOT EXISTS idx_runtime_cost_events_entity
    ON runtime_cost_events(opportunity_id, decision_id, proposal_id);

CREATE TABLE IF NOT EXISTS viability_reports (
    id TEXT PRIMARY KEY,
    dedup_key TEXT NOT NULL UNIQUE,
    scope TEXT NOT NULL CHECK(scope IN ('system','operator')),
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    execution_mode TEXT NOT NULL,
    metric_version TEXT NOT NULL,
    outcome_contract_version TEXT,
    net_trading_result_usd REAL,
    baseline_result_usd REAL,
    incremental_value_usd REAL,
    intelligence_cost_usd REAL,
    tool_cost_usd REAL,
    data_cost_usd REAL,
    infrastructure_cost_usd REAL,
    other_cost_usd REAL,
    deployment_cost_usd REAL,
    viability_amount_usd REAL,
    verdict TEXT NOT NULL CHECK(verdict IN ('positive','neutral','negative','insufficient_data','invalid')),
    cost_completeness TEXT NOT NULL,
    sample_count INTEGER NOT NULL DEFAULT 0,
    exclusion_counts_json TEXT NOT NULL DEFAULT '{}',
    assumptions_json TEXT NOT NULL DEFAULT '[]',
    reason TEXT NOT NULL,
    generated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_viability_reports_scope_window
    ON viability_reports(scope, window_end DESC);
"""


def ensure_economics_schema(db: sqlite3.Connection | None = None) -> sqlite3.Connection:
    """Create the append-only economics schema if needed and return the connection."""
    if db is None:
        from aletheia.storage.sqlite import get_db

        db = get_db()
    db.executescript(ECONOMICS_DDL)
    # Existing tool calls have no timing/cost columns. Mirror each durable tool-ledger
    # insert into the economics ledger with an honest unknown cost. Future callers can
    # emit a richer explicit event through record_tool_event without changing this
    # compatibility path.
    tool_table = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_tool_calls'"
    ).fetchone()
    if tool_table:
        db.executescript(
            """
            CREATE TRIGGER IF NOT EXISTS trg_agent_tool_call_cost_event
            AFTER INSERT ON agent_tool_calls
            WHEN COALESCE((SELECT value FROM economics_settings WHERE key='cost_tracing_enabled'),'1')='1'
            BEGIN
                INSERT OR IGNORE INTO runtime_cost_events (
                    id,event_type,run_id,call_id,provider,model_or_tool,attempt_index,
                    status,started_at,finished_at,usage_json,cost_profile_id,
                    estimated_cost_usd,currency,cost_status,metadata_json,created_at
                ) VALUES (
                    'toolcost_' || NEW.id,'tool',NEW.run_id,'tool_' || NEW.id,'tool',
                    NEW.tool_name,0,CASE WHEN NEW.permitted=1 THEN 'recorded' ELSE 'denied' END,
                    NEW.created_at,NEW.created_at,'{}',
                    (SELECT id FROM tool_cost_profiles
                     WHERE tool_name=NEW.tool_name
                       AND replace(effective_from,' ','T') <= replace(NEW.created_at,' ','T')
                     ORDER BY replace(effective_from,' ','T') DESC,rowid DESC LIMIT 1),
                    (SELECT usd_per_call FROM tool_cost_profiles
                     WHERE tool_name=NEW.tool_name
                       AND replace(effective_from,' ','T') <= replace(NEW.created_at,' ','T')
                     ORDER BY replace(effective_from,' ','T') DESC,rowid DESC LIMIT 1),
                    'USD',
                    CASE WHEN EXISTS (
                        SELECT 1 FROM tool_cost_profiles
                        WHERE tool_name=NEW.tool_name
                          AND replace(effective_from,' ','T') <= replace(NEW.created_at,' ','T')
                    ) THEN 'estimated' ELSE 'unknown' END,
                    json_object('tool_class',NEW.tool_class,'permitted',NEW.permitted),
                    NEW.created_at
                );
            END;
            """
        )
    return db
