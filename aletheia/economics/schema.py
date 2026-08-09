"""Idempotent storage schema for economic runtime facts and viability reports.

The economics tables are intentionally isolated from trading state. Creating or
querying them cannot mutate opportunities, proposals, risk, orders, positions,
or Learning authority.
"""

from __future__ import annotations

import sqlite3


ECONOMICS_SCHEMA_VERSION = "economic-viability-v1"
_TOOL_BACKFILL_KEY = "tool_cost_backfill_v1"

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
CREATE INDEX IF NOT EXISTS idx_runtime_cost_events_normalized_started
    ON runtime_cost_events(replace(started_at,' ','T'));
CREATE INDEX IF NOT EXISTS idx_runtime_cost_events_entity
    ON runtime_cost_events(opportunity_id, decision_id, proposal_id);

-- One content-free row per logical inference. Physical provider attempts keep
-- their existing append-only rows in runtime_cost_events and share this ID.
CREATE TABLE IF NOT EXISTS llm_inference_calls (
    logical_call_id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    run_id TEXT,
    runtime_session_id TEXT,
    caller TEXT NOT NULL,
    prompt_template_id TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    prompt_static_sha256 TEXT NOT NULL,
    rendered_prompt_sha256 TEXT NOT NULL,
    context_adapter_id TEXT NOT NULL,
    tool_policy_id TEXT NOT NULL,
    allowed_tools_sha256 TEXT NOT NULL,
    output_schema_id TEXT,
    output_schema_sha256 TEXT,
    requested_provider TEXT,
    temperature REAL NOT NULL,
    max_tokens INTEGER NOT NULL CHECK(max_tokens > 0),
    allow_model_fallback INTEGER NOT NULL CHECK(allow_model_fallback IN (0,1)),
    status TEXT NOT NULL CHECK(status IN ('running','success','error')),
    parse_valid INTEGER CHECK(parse_valid IN (0,1)),
    schema_valid INTEGER CHECK(schema_valid IN (0,1)),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK(retry_count >= 0),
    fallback_used INTEGER CHECK(fallback_used IN (0,1)),
    final_provider TEXT,
    final_model TEXT,
    error_type TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_inference_calls_run
    ON llm_inference_calls(run_id, started_at);
CREATE INDEX IF NOT EXISTS idx_llm_inference_calls_caller
    ON llm_inference_calls(caller, started_at DESC);

-- One content-free result per outer structured-repair attempt. This is separate
-- from physical provider attempts: a provider success can still fail JSON/schema
-- validation, and one repair attempt can use a provider fallback.
CREATE TABLE IF NOT EXISTS llm_structured_attempt_outcomes (
    logical_call_id TEXT NOT NULL REFERENCES llm_inference_calls(logical_call_id),
    structured_attempt_index INTEGER NOT NULL CHECK(structured_attempt_index >= 0),
    outcome TEXT NOT NULL CHECK(outcome IN (
        'accepted','provider_error','timeout','cancelled','empty_content',
        'non_object_json','parse_error','truncated_output','schema_invalid'
    )),
    created_at TEXT NOT NULL,
    PRIMARY KEY(logical_call_id, structured_attempt_index)
);

-- Aggregate-only blinded owner review. Raw model responses remain in an
-- explicitly chosen ephemeral review packet outside the repository/ledger.
CREATE TABLE IF NOT EXISTS llm_context_pair_reviews (
    evaluation_pair_id TEXT PRIMARY KEY,
    fixture_id TEXT NOT NULL,
    rubric_version TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    control_call_id TEXT NOT NULL,
    treatment_call_id TEXT NOT NULL,
    control_quality_score INTEGER NOT NULL CHECK(control_quality_score BETWEEN 1 AND 5),
    treatment_quality_score INTEGER NOT NULL CHECK(treatment_quality_score BETWEEN 1 AND 5),
    control_grounded_use TEXT NOT NULL CHECK(control_grounded_use IN ('yes','no','not_applicable')),
    treatment_grounded_use TEXT NOT NULL CHECK(treatment_grounded_use IN ('yes','no','not_applicable')),
    control_context_harm INTEGER NOT NULL CHECK(control_context_harm IN (0,1)),
    treatment_context_harm INTEGER NOT NULL CHECK(treatment_context_harm IN (0,1)),
    verdict TEXT NOT NULL CHECK(verdict IN ('treatment_better','control_better','tie')),
    reviewed_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_llm_context_pair_reviews_fixture
    ON llm_context_pair_reviews(fixture_id, reviewed_at DESC);

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

-- Phase 2A scaffold (see docs/design/proposals/tokenomics_harness_phase_plan_2026-07-30.md).
-- Idempotent CREATE. No writer wired yet; no check executed against it in the
-- provider layer until Phase 2B ships the shadow-mode hook.
CREATE TABLE IF NOT EXISTS economics_budgets (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL CHECK(scope IN
        ('system','agent_run','logical_agent','provider','provider_tier')),
    subject TEXT,                 -- run_id / logical_agent name / provider name / tier; NULL for system-wide
    usd_cap REAL NOT NULL CHECK(usd_cap > 0),
    window TEXT NOT NULL CHECK(window IN ('daily','weekly','session')),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
    source TEXT NOT NULL,         -- audit trail: 'cli:budget-set', 'system:seed', 'test'
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(scope, subject, window)
);
CREATE INDEX IF NOT EXISTS idx_economics_budgets_scope
    ON economics_budgets(scope, subject, enabled);

-- Prompt-free context consumption metadata. It cannot carry prompts, responses,
-- or source bodies, and is isolated from trading state.
CREATE TABLE IF NOT EXISTS llm_context_artifacts (
    id TEXT PRIMARY KEY, schema_version TEXT NOT NULL, run_id TEXT, caller TEXT NOT NULL,
    logical_call_id TEXT, evaluation_pair_id TEXT, evaluation_arm TEXT, fixture_id TEXT,
    symbol TEXT, kind TEXT NOT NULL CHECK(kind IN ('environment','news','learning')),
    stable_id TEXT NOT NULL, source TEXT, selected_reason TEXT, trust_class TEXT,
    freshness_status TEXT NOT NULL CHECK(freshness_status IN ('fresh','stale','unknown')),
    published_at TEXT, observed_at TEXT, rendered_chars INTEGER NOT NULL CHECK(rendered_chars >= 0),
    metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_context_artifacts_run ON llm_context_artifacts(run_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_context_artifacts_window ON llm_context_artifacts(created_at DESC, kind);
CREATE INDEX IF NOT EXISTS idx_llm_context_artifacts_call ON llm_context_artifacts(logical_call_id, created_at);
"""

_TOOL_COST_SELECT = """
SELECT
    'toolcost_' || calls.id,
    'tool',
    calls.run_id,
    NULL,
    NULL,
    NULL,
    NULL,
    'tool_' || calls.id,
    'tool',
    calls.tool_name,
    NULL,
    0,
    CASE WHEN calls.permitted=1 THEN 'recorded' ELSE 'denied' END,
    calls.created_at,
    calls.created_at,
    NULL,
    '{}',
    NULL,
    CASE WHEN calls.permitted=0 THEN NULL ELSE (
        SELECT id FROM tool_cost_profiles
        WHERE tool_name=calls.tool_name
          AND effective_from <= replace(calls.created_at,' ','T')
        ORDER BY effective_from DESC,rowid DESC LIMIT 1
    ) END,
    CASE WHEN calls.permitted=0 THEN 0.0 ELSE (
        SELECT usd_per_call FROM tool_cost_profiles
        WHERE tool_name=calls.tool_name
          AND effective_from <= replace(calls.created_at,' ','T')
        ORDER BY effective_from DESC,rowid DESC LIMIT 1
    ) END,
    'USD',
    CASE
        WHEN calls.permitted=0 THEN 'known'
        WHEN EXISTS (
            SELECT 1 FROM tool_cost_profiles
            WHERE tool_name=calls.tool_name
              AND effective_from <= replace(calls.created_at,' ','T')
        ) THEN 'estimated'
        ELSE 'unknown'
    END,
    NULL,
    json_object(
        'tool_class',calls.tool_class,
        'permitted',calls.permitted,
        'inference_call_id',calls.inference_call_id,
        'legacy_backfill',1
    ),
    calls.created_at
FROM agent_tool_calls AS calls
"""


def _backfill_existing_tool_calls(db: sqlite3.Connection) -> None:
    marker = db.execute(
        "SELECT 1 FROM economics_settings WHERE key=?",
        (_TOOL_BACKFILL_KEY,),
    ).fetchone()
    if marker:
        return
    db.execute(
        """INSERT OR IGNORE INTO runtime_cost_events (
            id,event_type,run_id,runtime_session_id,opportunity_id,decision_id,
            proposal_id,call_id,provider,model_or_tool,requested_model,attempt_index,
            status,started_at,finished_at,latency_ms,usage_json,pricing_snapshot_id,
            cost_profile_id,estimated_cost_usd,currency,cost_status,error_type,
            metadata_json,created_at
        ) """ + _TOOL_COST_SELECT
    )
    db.execute(
        "INSERT OR REPLACE INTO economics_settings(key,value,updated_at) "
        "VALUES (?,?,datetime('now'))",
        (_TOOL_BACKFILL_KEY, "complete"),
    )


def _install_tool_trigger(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        DROP TRIGGER IF EXISTS trg_agent_tool_call_cost_event;
        CREATE TRIGGER IF NOT EXISTS trg_agent_tool_call_cost_event
        AFTER INSERT ON agent_tool_calls
        WHEN COALESCE((SELECT value FROM economics_settings WHERE key='cost_tracing_enabled'),'1')='1'
        BEGIN
            INSERT OR IGNORE INTO runtime_cost_events (
                id,event_type,run_id,call_id,provider,model_or_tool,attempt_index,
                status,started_at,finished_at,usage_json,cost_profile_id,
                estimated_cost_usd,currency,cost_status,metadata_json,created_at
            ) VALUES (
                'toolcost_' || NEW.id,
                'tool',
                NEW.run_id,
                'tool_' || NEW.id,
                'tool',
                NEW.tool_name,
                0,
                CASE WHEN NEW.permitted=1 THEN 'recorded' ELSE 'denied' END,
                replace(NEW.created_at,' ','T'),
                replace(NEW.created_at,' ','T'),
                '{}',
                CASE WHEN NEW.permitted=0 THEN NULL ELSE (
                    SELECT id FROM tool_cost_profiles
                    WHERE tool_name=NEW.tool_name
                      AND effective_from <= replace(NEW.created_at,' ','T')
                    ORDER BY effective_from DESC,rowid DESC LIMIT 1
                ) END,
                CASE WHEN NEW.permitted=0 THEN 0.0 ELSE (
                    SELECT usd_per_call FROM tool_cost_profiles
                    WHERE tool_name=NEW.tool_name
                      AND effective_from <= replace(NEW.created_at,' ','T')
                    ORDER BY effective_from DESC,rowid DESC LIMIT 1
                ) END,
                'USD',
                CASE
                    WHEN NEW.permitted=0 THEN 'known'
                    WHEN EXISTS (
                        SELECT 1 FROM tool_cost_profiles
                        WHERE tool_name=NEW.tool_name
                          AND effective_from <= replace(NEW.created_at,' ','T')
                    ) THEN 'estimated'
                    ELSE 'unknown'
                END,
                json_object(
                    'tool_class',NEW.tool_class,
                    'permitted',NEW.permitted,
                    'inference_call_id',NEW.inference_call_id,
                    'legacy_backfill',0
                ),
                NEW.created_at
            );
        END;
        """
    )


def ensure_economics_schema(db: sqlite3.Connection | None = None) -> sqlite3.Connection:
    """Create the append-only economics schema if needed and return the connection.

    ``sqlite3.Connection.executescript`` commits any active transaction before it
    runs. Runtime measurement must never cross that boundary. First-time DDL
    initialization refuses to run inside a caller transaction. DML backfill may
    join a caller transaction but only commits when it opened the transaction.
    """
    if db is None:
        from aletheia.storage.sqlite import get_db

        db = get_db()
    required_tables = {
        "runtime_cost_events",
        "economics_budgets",
        "llm_inference_calls",
        "llm_structured_attempt_outcomes",
        "llm_context_pair_reviews",
    }
    existing_tables = {
        str(row[0])
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    trigger_row = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='trigger' "
        "AND name='trg_agent_tool_call_cost_event'"
    ).fetchone()
    trigger_ready = bool(
        trigger_row and "inference_call_id" in str(trigger_row[0] or "")
    )
    tool_table = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_tool_calls'"
    ).fetchone()
    tool_columns = (
        {
            str(row[1])
            for row in db.execute("PRAGMA table_info(agent_tool_calls)").fetchall()
        }
        if tool_table
        else set()
    )
    tool_inference_link_ready = "inference_call_id" in tool_columns

    if not required_tables.issubset(existing_tables):
        if db.in_transaction:
            raise RuntimeError(
                "economics schema initialization refused inside an active transaction"
            )
        db.executescript(ECONOMICS_DDL)

    context_columns = {
        str(row[1]) for row in db.execute("PRAGMA table_info(llm_context_artifacts)")
    }
    missing_context_columns = [
        ("logical_call_id", "TEXT"),
        ("evaluation_pair_id", "TEXT"),
        ("evaluation_arm", "TEXT"),
        ("fixture_id", "TEXT"),
    ]
    missing_context_columns = [
        item for item in missing_context_columns if item[0] not in context_columns
    ]
    if missing_context_columns:
        if db.in_transaction:
            raise RuntimeError(
                "economics context schema upgrade refused inside an active transaction"
            )
        for column, column_type in missing_context_columns:
            db.execute(
                f"ALTER TABLE llm_context_artifacts ADD COLUMN {column} {column_type}"
            )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_llm_context_artifacts_call "
            "ON llm_context_artifacts(logical_call_id, created_at)"
        )

    if tool_table and tool_inference_link_ready and not trigger_ready:
        if db.in_transaction:
            raise RuntimeError(
                "economics trigger initialization refused inside an active transaction"
            )
        _install_tool_trigger(db)

    if tool_table and tool_inference_link_ready:
        marker = db.execute(
            "SELECT 1 FROM economics_settings WHERE key=?",
            (_TOOL_BACKFILL_KEY,),
        ).fetchone()
        if not marker:
            caller_owned_transaction = db.in_transaction
            _backfill_existing_tool_calls(db)
            if not caller_owned_transaction and db.in_transaction:
                db.commit()

    return db
