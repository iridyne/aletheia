"""Versioned SQLite schema migrations."""

from dataclasses import dataclass
import hashlib
import logging
import sqlite3
import time
from pathlib import Path
from typing import Callable

from aletheia.storage.sqlite import (
    acquire_write_lock,
    get_db,
    readonly_db,
    release_write_lock,
)

logger = logging.getLogger("aletheia.storage.migrations")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_runs (
    id TEXT PRIMARY KEY,
    agent_name TEXT NOT NULL,
    trigger_type TEXT NOT NULL DEFAULT 'scheduled',
    permission_mode TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    model_provider TEXT,
    model_name TEXT,
    tools_called TEXT DEFAULT '[]',
    outputs_created TEXT DEFAULT '[]',
    errors TEXT DEFAULT '[]',
    cost_estimate REAL
);

CREATE TABLE IF NOT EXISTS agent_run_steps (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs(id),
    step_number INTEGER NOT NULL,
    action TEXT NOT NULL,
    input_data TEXT,
    output_data TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_tool_calls (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs(id),
    tool_name TEXT NOT NULL,
    tool_class TEXT NOT NULL,
    input_data TEXT,
    output_data TEXT,
    permitted INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS opportunities (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    market TEXT NOT NULL,
    direction_bias TEXT,
    thesis TEXT,
    confidence TEXT NOT NULL DEFAULT 'medium_confidence',
    confidence_score INTEGER,
    source_agent TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    status TEXT NOT NULL DEFAULT 'open',
    last_ranked_at TEXT,
    linked_strategy_id TEXT
);

CREATE TABLE IF NOT EXISTS trade_proposals (
    id TEXT PRIMARY KEY,
    strategy_id TEXT,
    opportunity_id TEXT,
    symbol TEXT NOT NULL,
    market_type TEXT NOT NULL,
    direction TEXT NOT NULL,
    order_type TEXT NOT NULL DEFAULT 'market',
    quantity REAL,
    price REAL,
    leverage INTEGER,
    stop_price REAL,
    execution_mode TEXT NOT NULL DEFAULT 'paper_internal',
    agent_rationale TEXT,
    attribution_snapshot TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    source TEXT NOT NULL DEFAULT 'native',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS proposal_execution_claims (
    proposal_id TEXT PRIMARY KEY REFERENCES trade_proposals(id) ON DELETE CASCADE,
    owner_token TEXT NOT NULL,
    claimed_at TEXT NOT NULL,
    lease_expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entry_risk_reservations (
    proposal_id TEXT PRIMARY KEY REFERENCES trade_proposals(id) ON DELETE CASCADE,
    proposal_commit_hash TEXT NOT NULL,
    symbol TEXT NOT NULL,
    market_type TEXT NOT NULL,
    execution_mode TEXT NOT NULL,
    quantity REAL NOT NULL CHECK(quantity > 0),
    price REAL NOT NULL CHECK(price > 0),
    leverage REAL NOT NULL CHECK(leverage > 0),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_entry_risk_reservations_pool
    ON entry_risk_reservations(execution_mode, symbol, created_at);

CREATE TABLE IF NOT EXISTS risk_decisions (
    id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL REFERENCES trade_proposals(id),
    approved INTEGER NOT NULL DEFAULT 0,
    risk_score REAL NOT NULL DEFAULT 0,
    violations TEXT DEFAULT '[]',
    allowed_execution_mode TEXT,
    proposal_execution_mode TEXT,
    decision_role TEXT NOT NULL DEFAULT 'legacy',
    evaluated_quantity REAL,
    proposal_commit_hash TEXT,
    risk_mode TEXT,
    policy_id TEXT,
    policy_revision INTEGER,
    policy_hash TEXT,
    message TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL REFERENCES trade_proposals(id),
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    proposal_id TEXT REFERENCES trade_proposals(id),
    risk_decision_id TEXT REFERENCES risk_decisions(id),
    symbol TEXT NOT NULL,
    market_type TEXT NOT NULL,
    direction TEXT NOT NULL,
    order_type TEXT NOT NULL,
    quantity REAL NOT NULL,
    price REAL,
    execution_mode TEXT NOT NULL,
    exchange_order_id TEXT,
    client_order_id TEXT,
    position_id TEXT,
    close_reason TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    source TEXT NOT NULL DEFAULT 'native',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    filled_at TEXT
);

CREATE TABLE IF NOT EXISTS fills (
    id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES orders(id),
    fill_price REAL NOT NULL,
    fill_quantity REAL NOT NULL,
    fee REAL DEFAULT 0,
    fee_currency TEXT DEFAULT 'USDT',
    fee_source TEXT DEFAULT 'estimated',
    filled_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS positions (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    market_type TEXT NOT NULL,
    direction TEXT NOT NULL,
    quantity REAL NOT NULL,
    entry_price REAL NOT NULL,
    current_price REAL,
    unrealized_pnl REAL DEFAULT 0,
    realized_pnl REAL DEFAULT 0,
    execution_mode TEXT NOT NULL,
    strategy_id TEXT,
    proposal_id TEXT REFERENCES trade_proposals(id),
    order_id TEXT REFERENCES orders(id),
    status TEXT NOT NULL DEFAULT 'open',
    stop_price REAL,
    take_profit_price REAL,
    close_reason TEXT,
    exit_reasoning TEXT,
    close_category TEXT NOT NULL DEFAULT 'unknown',
    entry_price_source TEXT NOT NULL DEFAULT 'unavailable',
    entry_fill_verified INTEGER NOT NULL DEFAULT 0,
    exit_price_source TEXT NOT NULL DEFAULT 'unavailable',
    exit_fill_verified INTEGER NOT NULL DEFAULT 0,
    outcome_eligible INTEGER NOT NULL DEFAULT 0,
    outcome_exclusion_reason TEXT NOT NULL DEFAULT 'unclassified',
    reconciliation_identity TEXT,
    reconciliation_evidence TEXT,
    outcome_semantics_version TEXT,
    funding_cost REAL,
    funding_source TEXT NOT NULL DEFAULT 'unavailable',
    source TEXT NOT NULL DEFAULT 'native',
    opened_at TEXT NOT NULL DEFAULT (datetime('now')),
    closed_at TEXT
);

CREATE TABLE IF NOT EXISTS shadow_trades (
    id TEXT PRIMARY KEY,
    batch_id TEXT,
    opportunity_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    venue TEXT,
    market_type TEXT NOT NULL DEFAULT 'spot',
    direction TEXT NOT NULL,
    scanner_family TEXT,
    trade_score REAL NOT NULL DEFAULT -999,
    rank INTEGER NOT NULL DEFAULT 0,
    entry_price REAL,
    entry_spread_bps REAL,
    funding_rate_at_entry REAL,
    outcome_candidate_key TEXT,
    regime_tag TEXT,
    was_selected INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    exit_price REAL,
    pnl_pct REAL,
    gross_return_pct REAL,
    net_pnl_pct REAL,
    estimated_fee_pct REAL,
    estimated_spread_slippage_pct REAL,
    estimated_funding_pct REAL,
    min_tradable_edge_pct REAL,
    settlement_price_source TEXT,
    settlement_lag_sec REAL,
    cost_model_version TEXT,
    btc_return_pct REAL,
    resolved_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_shadow_trades_batch ON shadow_trades(batch_id);
CREATE INDEX IF NOT EXISTS idx_shadow_trades_opportunity ON shadow_trades(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_shadow_trades_family ON shadow_trades(scanner_family);

CREATE TABLE IF NOT EXISTS alpha_control_observations (
    id TEXT PRIMARY KEY,
    layer TEXT NOT NULL,
    cohort_id TEXT NOT NULL,
    treatment_id TEXT,
    role TEXT NOT NULL,
    symbol TEXT NOT NULL,
    venue TEXT,
    market_type TEXT NOT NULL DEFAULT 'spot',
    scanner_family TEXT,
    direction TEXT,
    horizon TEXT NOT NULL,
    entry_price REAL,
    entry_ts TEXT NOT NULL,
    score REAL,
    module_path TEXT,
    metadata TEXT DEFAULT '{}',
    outcome_candidate_key TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_alpha_control_layer_entry
    ON alpha_control_observations (layer, entry_ts);
CREATE INDEX IF NOT EXISTS idx_alpha_control_cohort
    ON alpha_control_observations (cohort_id, horizon);

CREATE TABLE IF NOT EXISTS filter_decisions (
    id TEXT PRIMARY KEY,
    dedup_key TEXT NOT NULL UNIQUE,
    candidate_key TEXT NOT NULL,
    candidate_kind TEXT NOT NULL,
    parent_candidate_key TEXT,
    run_id TEXT,
    opportunity_id TEXT,
    universe_snapshot_id TEXT,
    universe_member_id TEXT,
    proposal_id TEXT,
    venue TEXT,
    symbol TEXT NOT NULL,
    market_type TEXT NOT NULL DEFAULT 'spot',
    direction TEXT,
    scanner_family TEXT,
    regime_tag TEXT,
    filter_name TEXT NOT NULL,
    stage TEXT NOT NULL,
    sequence_no INTEGER NOT NULL DEFAULT 0,
    decision TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    reason_detail TEXT,
    input_snapshot TEXT NOT NULL DEFAULT '{}',
    parameter_snapshot TEXT NOT NULL DEFAULT '{}',
    filter_version TEXT NOT NULL,
    code_commit TEXT,
    outcome_candidate_key TEXT,
    evaluated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_filter_decisions_filter_time
    ON filter_decisions (filter_name, evaluated_at DESC);
CREATE INDEX IF NOT EXISTS idx_filter_decisions_candidate
    ON filter_decisions (candidate_key, sequence_no);
CREATE INDEX IF NOT EXISTS idx_filter_decisions_outcome
    ON filter_decisions (outcome_candidate_key);

CREATE TABLE IF NOT EXISTS filter_decision_batches (
    id TEXT PRIMARY KEY,
    dedup_key TEXT NOT NULL UNIQUE,
    run_id TEXT NOT NULL,
    candidate_kind TEXT NOT NULL,
    filter_name TEXT NOT NULL,
    stage TEXT NOT NULL,
    candidate_count INTEGER NOT NULL,
    passed_count INTEGER NOT NULL DEFAULT 0,
    rejected_count INTEGER NOT NULL DEFAULT 0,
    not_applicable_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    candidate_facts TEXT NOT NULL DEFAULT '[]',
    parameter_snapshot TEXT NOT NULL DEFAULT '{}',
    filter_version TEXT NOT NULL,
    code_commit TEXT,
    evaluated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_filter_batches_filter_time
    ON filter_decision_batches (filter_name, evaluated_at DESC);

CREATE TABLE IF NOT EXISTS filter_candidate_outcomes (
    id TEXT PRIMARY KEY,
    outcome_candidate_key TEXT NOT NULL,
    venue TEXT NOT NULL,
    symbol TEXT NOT NULL,
    market_type TEXT NOT NULL DEFAULT 'spot',
    direction TEXT NOT NULL,
    scanner_family TEXT,
    regime_tag TEXT,
    horizon TEXT NOT NULL,
    allocation_selected INTEGER,
    entry_price_usd REAL NOT NULL,
    entry_spread_bps REAL,
    funding_rate_at_entry REAL,
    entry_ts TEXT NOT NULL,
    due_at TEXT NOT NULL,
    settled_at TEXT,
    exit_price_usd REAL,
    gross_return_pct REAL,
    directional_return_pct REAL,
    estimated_fee_pct REAL,
    estimated_spread_slippage_pct REAL,
    estimated_funding_pct REAL,
    net_return_pct REAL,
    min_tradable_edge_pct REAL,
    mfe_pct REAL,
    mae_pct REAL,
    settlement_price_source TEXT,
    settlement_lag_sec REAL,
    cost_model_version TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    UNIQUE (outcome_candidate_key, horizon)
);
CREATE INDEX IF NOT EXISTS idx_filter_outcomes_status_due
    ON filter_candidate_outcomes (status, due_at);
CREATE INDEX IF NOT EXISTS idx_filter_outcomes_candidate
    ON filter_candidate_outcomes (outcome_candidate_key, horizon);

CREATE TABLE IF NOT EXISTS serenity_themes (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    demand_wave TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    objective TEXT,
    description TEXT,
    strategy_notes TEXT,
    tags TEXT DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS serenity_evidence (
    id TEXT PRIMARY KEY,
    theme_id TEXT NOT NULL REFERENCES serenity_themes(id),
    symbol TEXT,
    source TEXT NOT NULL DEFAULT 'manual',
    source_ref TEXT,
    title TEXT NOT NULL,
    summary TEXT DEFAULT '',
    tier TEXT NOT NULL DEFAULT 'speculative',
    supports INTEGER NOT NULL DEFAULT 1,
    evidence_type TEXT DEFAULT 'thesis',
    event_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    actor TEXT,
    confidence TEXT DEFAULT 'speculative',
    tags TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS serenity_opportunities (
    id TEXT PRIMARY KEY,
    theme_id TEXT NOT NULL REFERENCES serenity_themes(id),
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    thesis TEXT,
    evidence_tier TEXT NOT NULL DEFAULT 'speculative',
    stage TEXT NOT NULL DEFAULT 'research',
    average_mode TEXT NOT NULL DEFAULT 'none',
    action_intent TEXT NOT NULL DEFAULT 'watch',
    status TEXT NOT NULL DEFAULT 'active',
    risk_flags TEXT DEFAULT '[]',
    risk_metrics TEXT DEFAULT '{}',
    confidence_score INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS serenity_risk_gates (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL REFERENCES serenity_opportunities(id),
    gate_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'warn',
    metric TEXT NOT NULL,
    observed TEXT,
    threshold TEXT,
    message TEXT,
    checked_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS serenity_replay_snapshots (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL REFERENCES serenity_opportunities(id),
    as_of TEXT NOT NULL,
    evidence_count INTEGER NOT NULL DEFAULT 0,
    score_total INTEGER NOT NULL DEFAULT 0,
    stage TEXT NOT NULL DEFAULT 'research',
    event_type TEXT NOT NULL DEFAULT 'snapshot',
    event_title TEXT NOT NULL DEFAULT 'snapshot',
    payload TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS serenity_post_mortems (
    id TEXT PRIMARY KEY,
    position_id TEXT REFERENCES positions(id),
    opportunity_id TEXT NOT NULL REFERENCES serenity_opportunities(id),
    outcome TEXT NOT NULL,
    failure_bucket TEXT,
    score_delta INTEGER,
    attribution TEXT,
    summary TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT 'system',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id TEXT PRIMARY KEY,
    total_value_usdt REAL NOT NULL,
    unrealized_pnl REAL DEFAULT 0,
    realized_pnl REAL DEFAULT 0,
    positions_count INTEGER DEFAULT 0,
    execution_mode TEXT NOT NULL,
    account_equity_usdt REAL,
    snapshot_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS strategy_records (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    state TEXT NOT NULL DEFAULT 'draft',
    parameters TEXT DEFAULT '{}',
    under_review INTEGER NOT NULL DEFAULT 0,
    review_notes TEXT,
    review_updated_at TEXT,
    paper_trading_paused INTEGER NOT NULL DEFAULT 0,
    paper_pause_reason TEXT,
    paper_paused_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS strategy_experiments (
    id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL REFERENCES strategy_records(id),
    experiment_type TEXT NOT NULL,
    parameters TEXT DEFAULT '{}',
    result TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS knowledge_items (
    id TEXT PRIMARY KEY,
    item_type TEXT NOT NULL,
    title TEXT NOT NULL,
    file_path TEXT,
    strategy_id TEXT,
    confidence TEXT NOT NULL DEFAULT 'medium_confidence',
    status TEXT NOT NULL DEFAULT 'active',
    tags TEXT DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS market_snapshots (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    market_type TEXT NOT NULL,
    price REAL NOT NULL,
    volume_24h REAL,
    change_pct_24h REAL,
    snapshot_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    actor TEXT NOT NULL,
    source TEXT NOT NULL,
    event_type TEXT NOT NULL,
    entity_type TEXT,
    entity_id TEXT,
    before_state TEXT,
    after_state TEXT,
    message TEXT
);

CREATE TABLE IF NOT EXISTS news_items (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    source TEXT NOT NULL,
    url TEXT,
    summary TEXT,
    symbols_mentioned TEXT DEFAULT '[]',
    event_type TEXT DEFAULT 'other',
    sentiment TEXT DEFAULT 'neutral',
    relevance_score REAL DEFAULT 0,
    published_at TEXT NOT NULL DEFAULT (datetime('now')),
    fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
    status TEXT NOT NULL DEFAULT 'new'
);

-- OHLCV rolling window store. Each (symbol, timeframe) pair keeps the most
-- recent 500 bars. Older bars are pruned by _prune_ohlcv() after each write.
-- symbol uses canonical BTC/USDT format; provider records which source supplied it.
CREATE TABLE IF NOT EXISTS market_ohlcv (
    exchange TEXT NOT NULL DEFAULT 'legacy_unknown',
    symbol TEXT NOT NULL,
    market_type TEXT NOT NULL DEFAULT 'spot',
    timeframe TEXT NOT NULL,
    ts INTEGER NOT NULL,        -- unix milliseconds (open time of bar)
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    provider TEXT NOT NULL DEFAULT 'ccxt',
    PRIMARY KEY (exchange, symbol, market_type, timeframe, ts)
);

-- Runtime Continuity (PI runtime lifecycle): candidate -> shadow -> promote -> rollback.
CREATE TABLE IF NOT EXISTS runtime_registry (
    runtime_id TEXT PRIMARY KEY,
    state TEXT NOT NULL CHECK (
        state IN (
            'installed',
            'candidate',
            'shadowing',
            'ready_to_promote',
            'current',
            'draining',
            'legacy_position_manager',
            'failed',
            'rolled_back'
        )
    ),
    code_version TEXT,
    config_version TEXT,
    schema_version TEXT,
    build_git_sha TEXT,
    release_bundle_hash TEXT,
    artifact_path TEXT,
    mode TEXT,
    started_at TEXT,
    promoted_at TEXT,
    drained_at TEXT,
    failed_at TEXT,
    rolled_back_at TEXT,
    last_error TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_runtime_registry_single_current
    ON runtime_registry (state)
    WHERE state = 'current';

CREATE UNIQUE INDEX IF NOT EXISTS idx_runtime_registry_single_shadowing
    ON runtime_registry (state)
    WHERE state = 'shadowing';

CREATE TABLE IF NOT EXISTS runtime_checkpoints (
    id TEXT PRIMARY KEY,
    runtime_id TEXT NOT NULL,
    checkpoint_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'available',
    payload TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    restored_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_runtime_checkpoints_runtime
    ON runtime_checkpoints (runtime_id, created_at DESC);

CREATE TABLE IF NOT EXISTS runtime_events (
    id TEXT PRIMARY KEY,
    runtime_id TEXT,
    event_type TEXT NOT NULL,
    phase TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'info',
    actor TEXT NOT NULL DEFAULT 'system',
    cause TEXT,
    kind TEXT NOT NULL DEFAULT 'runtime_transition',
    status TEXT NOT NULL DEFAULT 'recorded',
    operation_id TEXT,
    payload TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_runtime_events_created
    ON runtime_events (created_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS idx_runtime_events_idempotency
    ON runtime_events (kind, status, operation_id)
    WHERE operation_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS runtime_exclusive_lock (
    lock_name TEXT PRIMARY KEY,
    runtime_id TEXT,
    owner TEXT NOT NULL,
    token TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    released_at TEXT,
    metadata TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_runtime_lock_expires
    ON runtime_exclusive_lock (expires_at);

CREATE TABLE IF NOT EXISTS runtime_ownership (
    id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    runtime_id TEXT NOT NULL,
    ownership_kind TEXT NOT NULL DEFAULT 'operator_task',
    status TEXT NOT NULL DEFAULT 'active',
    acquired_at TEXT NOT NULL DEFAULT (datetime('now')),
    released_at TEXT,
    metadata TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_runtime_ownership_runtime
    ON runtime_ownership (runtime_id, status);

CREATE UNIQUE INDEX IF NOT EXISTS idx_runtime_ownership_single_active
    ON runtime_ownership (entity_type, entity_id, status)
    WHERE status = 'active';

-- Agent Shell (Pi Agent, Operator Layer Track B) — session + command audit tables.
CREATE TABLE IF NOT EXISTS agent_shell_sessions (
    id TEXT PRIMARY KEY,
    user_input TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'command_session',
    parent_session_id TEXT,
    title TEXT,
    runtime_id TEXT DEFAULT 'runtime-local-current',
    intent TEXT,
    command TEXT,
    response TEXT,
    permitted INTEGER NOT NULL DEFAULT 1,
    duration_ms INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_shell_commands (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES agent_shell_sessions(id),
    runtime_id TEXT DEFAULT 'runtime-local-current',
    command TEXT NOT NULL,
    parameters TEXT DEFAULT '{}',
    permitted INTEGER NOT NULL DEFAULT 1,
    result_summary TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_shell_messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES agent_shell_sessions(id),
    runtime_id TEXT DEFAULT 'runtime-local-current',
    role TEXT NOT NULL,
    message_type TEXT NOT NULL DEFAULT 'message',
    content TEXT NOT NULL,
    related_event_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_shell_tasks (
    id TEXT PRIMARY KEY,
    runtime_id TEXT DEFAULT 'runtime-local-current',
    task_type TEXT NOT NULL,
    status TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'normal',
    created_by TEXT NOT NULL,
    assigned_to TEXT NOT NULL DEFAULT 'operator',
    input_data TEXT DEFAULT '{}',
    output_data TEXT,
    error TEXT,
    started_at TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_shell_artifacts (
    id TEXT PRIMARY KEY,
    task_id TEXT REFERENCES agent_shell_tasks(id),
    session_id TEXT REFERENCES agent_shell_sessions(id),
    runtime_id TEXT DEFAULT 'runtime-local-current',
    artifact_type TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_shell_heartbeats (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    runtime_id TEXT DEFAULT 'runtime-local-current',
    runtime_mode TEXT NOT NULL DEFAULT 'current',
    runtime_state TEXT NOT NULL DEFAULT 'current',
    status TEXT NOT NULL,
    mode TEXT NOT NULL,
    permission_mode TEXT NOT NULL,
    is_busy INTEGER NOT NULL DEFAULT 0,
    current_task_id TEXT,
    current_intent TEXT,
    queue_size INTEGER NOT NULL DEFAULT 0,
    last_seen_at TEXT NOT NULL,
    last_success_at TEXT,
    last_error TEXT,
    model_provider TEXT,
    session_id TEXT,
    active_branch_id TEXT,
    active_branch_kind TEXT,
    active_branch_title TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_shell_events (
    id TEXT PRIMARY KEY,
    runtime_id TEXT DEFAULT 'runtime-local-current',
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'info',
    source TEXT NOT NULL DEFAULT 'operator',
    session_id TEXT REFERENCES agent_shell_sessions(id),
    task_id TEXT REFERENCES agent_shell_tasks(id),
    related_entity_type TEXT,
    related_entity_id TEXT,
    payload TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS extension_registry (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES agent_shell_sessions(id),
    extension_name TEXT NOT NULL,
    slash_command TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL,
    target_command TEXT NOT NULL,
    manifest TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    created_by TEXT NOT NULL DEFAULT 'operator',
    proposal_artifact_id TEXT,
    latest_run_id TEXT,
    enabled_at TEXT,
    enabled_by TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS extension_runs (
    id TEXT PRIMARY KEY,
    extension_id TEXT NOT NULL REFERENCES extension_registry(id),
    session_id TEXT REFERENCES agent_shell_sessions(id),
    run_type TEXT NOT NULL,
    status TEXT NOT NULL,
    summary TEXT,
    input_data TEXT DEFAULT '{}',
    output_data TEXT,
    error TEXT,
    scratch_path TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS serenity_research_runs (
    id TEXT PRIMARY KEY,
    trigger_type TEXT NOT NULL DEFAULT 'scheduled',
    mode TEXT NOT NULL DEFAULT 'standard',
    status TEXT NOT NULL DEFAULT 'queued',
    stage TEXT,
    parent_run_id TEXT,
    idempotency_key TEXT,
    profile_snapshot_id TEXT,
    brief_snapshot TEXT,
    focus_theme_id TEXT,
    started_at TEXT,
    finished_at TEXT,
    next_retry_at TEXT,
    error_type TEXT,
    error_message TEXT,
    error_stage TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    model_used TEXT,
    token_usage TEXT DEFAULT '{}',
    artifact_counts TEXT DEFAULT '{"themes":0,"opportunities":0,"evidence":0,"claims":0}',
    metadata TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_research_runs_status ON serenity_research_runs(status);
CREATE INDEX IF NOT EXISTS idx_research_runs_idempotency ON serenity_research_runs(idempotency_key);

CREATE TABLE IF NOT EXISTS serenity_run_events (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES serenity_research_runs(id),
    event_type TEXT NOT NULL,
    stage TEXT,
    message TEXT,
    data TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_run_events_run_id ON serenity_run_events(run_id);

CREATE TABLE IF NOT EXISTS serenity_user_profiles (
    id TEXT PRIMARY KEY,
    include_topics TEXT DEFAULT '[]',
    exclude_topics TEXT DEFAULT '[]',
    include_assets TEXT DEFAULT '[]',
    exclude_assets TEXT DEFAULT '[]',
    market_types TEXT DEFAULT '[]',
    time_horizon TEXT,
    risk_appetite TEXT,
    raw_brief TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS serenity_profile_snapshots (
    id TEXT PRIMARY KEY,
    profile_id TEXT,
    include_topics TEXT DEFAULT '[]',
    exclude_topics TEXT DEFAULT '[]',
    include_assets TEXT DEFAULT '[]',
    exclude_assets TEXT DEFAULT '[]',
    market_types TEXT DEFAULT '[]',
    time_horizon TEXT,
    raw_brief TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS serenity_feedback_events (
    id TEXT PRIMARY KEY,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    action TEXT NOT NULL,
    value TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_feedback_target ON serenity_feedback_events(target_type, target_id);

CREATE TABLE IF NOT EXISTS serenity_claims (
    id TEXT PRIMARY KEY,
    run_id TEXT REFERENCES serenity_research_runs(id),
    evidence_doc_id TEXT,
    claim_text TEXT NOT NULL,
    subject TEXT,
    predicate TEXT,
    object TEXT,
    claim_time TEXT,
    confidence REAL NOT NULL DEFAULT 0.5,
    price_relevant INTEGER NOT NULL DEFAULT 0,
    evidence_hash TEXT,
    support_level TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_serenity_claims_run ON serenity_claims(run_id);
CREATE INDEX IF NOT EXISTS idx_serenity_claims_price_relevant ON serenity_claims(price_relevant);

CREATE TABLE IF NOT EXISTS serenity_theme_claims (
    theme_id TEXT NOT NULL REFERENCES serenity_themes(id),
    claim_id TEXT NOT NULL REFERENCES serenity_claims(id),
    relevance_score REAL,
    PRIMARY KEY (theme_id, claim_id)
);

CREATE TABLE IF NOT EXISTS serenity_agent_locks (
    lock_key TEXT PRIMARY KEY,
    owner_run_id TEXT NOT NULL,
    acquired_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL
);

-- ====================================================================
-- Persistence / Sync / Recovery Layer (Plan §7)
-- ====================================================================

CREATE TABLE IF NOT EXISTS ps_event_log (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    run_id TEXT,
    causation_id TEXT,
    correlation_id TEXT,
    idempotency_key TEXT,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    machine_id TEXT NOT NULL,
    schema_version TEXT NOT NULL DEFAULT 'v1'
);

CREATE INDEX IF NOT EXISTS idx_ps_event_aggregate
ON ps_event_log (aggregate_type, aggregate_id, created_at);

CREATE INDEX IF NOT EXISTS idx_ps_event_correlation
ON ps_event_log (correlation_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_ps_event_idempotency
ON ps_event_log (idempotency_key)
WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS ps_gateway_sessions (
    gateway_session_id TEXT PRIMARY KEY,
    gateway_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    heartbeat_at TEXT,
    machine_id TEXT NOT NULL,
    recovery_report_id TEXT,
    freeze_reason TEXT,
    config_hash TEXT
);

CREATE TABLE IF NOT EXISTS ps_gateway_locks (
    lock_name TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    lease_until TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ps_reconciliation_reports (
    reconciliation_report_id TEXT PRIMARY KEY,
    gateway_session_id TEXT NOT NULL,
    status TEXT NOT NULL,
    local_state_hash TEXT,
    remote_state_hash TEXT,
    diffs_json TEXT NOT NULL,
    actions_json TEXT NOT NULL,
    artifact_path TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ps_execution_intents (
    execution_intent_id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    risk_check_json TEXT,
    unlock_token_hash TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ps_order_intents (
    intent_id TEXT PRIMARY KEY,
    execution_intent_id TEXT,
    proposal_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    venue TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    order_type TEXT NOT NULL,
    quantity TEXT NOT NULL,
    limit_price TEXT,
    reduce_only INTEGER DEFAULT 0,
    status TEXT NOT NULL,
    client_order_id TEXT NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ps_orders (
    order_id TEXT PRIMARY KEY,
    intent_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    venue TEXT NOT NULL,
    venue_order_id TEXT,
    client_order_id TEXT NOT NULL UNIQUE,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    order_type TEXT NOT NULL,
    quantity TEXT NOT NULL,
    price TEXT,
    status TEXT NOT NULL,
    submitted_at TEXT,
    acknowledged_at TEXT,
    updated_at TEXT NOT NULL,
    raw_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_ps_orders_status
ON ps_orders (status, updated_at);

CREATE TABLE IF NOT EXISTS ps_fills (
    fill_id TEXT PRIMARY KEY,
    order_id TEXT,
    mode TEXT NOT NULL,
    venue TEXT NOT NULL,
    venue_trade_id TEXT,
    client_order_id TEXT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity TEXT NOT NULL,
    price TEXT NOT NULL,
    fee TEXT,
    fee_currency TEXT,
    filled_at TEXT NOT NULL,
    raw_json TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_ps_fills_venue_trade
ON ps_fills (venue, venue_trade_id)
WHERE venue_trade_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS ps_positions (
    position_id TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    proposal_id TEXT,
    venue TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity TEXT NOT NULL,
    avg_entry_price TEXT,
    realized_pnl TEXT,
    unrealized_pnl TEXT,
    status TEXT NOT NULL,
    opened_at TEXT,
    closed_at TEXT,
    updated_at TEXT NOT NULL,
    raw_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_ps_positions_status
ON ps_positions (status, updated_at);

CREATE TABLE IF NOT EXISTS ps_checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    step_name TEXT NOT NULL,
    state_json TEXT NOT NULL,
    artifact_path TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (run_id, step_name)
);

CREATE TABLE IF NOT EXISTS ps_lessons (
    lesson_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    category TEXT NOT NULL,
    severity TEXT NOT NULL,
    summary TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    recommendation_json TEXT,
    status TEXT NOT NULL,
    artifact_path TEXT,
    created_at TEXT NOT NULL,
    applied_policy_version TEXT,
    activated_at TEXT,
    dry_run_until TEXT,
    expires_at TEXT
);

CREATE TABLE IF NOT EXISTS ps_skills (
    skill_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    scope TEXT NOT NULL,
    description TEXT NOT NULL,
    inputs_json TEXT NOT NULL,
    outputs_json TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    allowed_layers_json TEXT NOT NULL,
    forbidden_layers_json TEXT NOT NULL,
    created_from_lessons_json TEXT,
    version INTEGER NOT NULL,
    artifact_path TEXT,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ps_policy_versions (
    policy_version_id TEXT PRIMARY KEY,
    parent_policy_version_id TEXT,
    policy_type TEXT NOT NULL,
    policy_json TEXT NOT NULL,
    policy_hash TEXT NOT NULL,
    diff_json TEXT,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ps_policy_versions_type_status
ON ps_policy_versions (policy_type, status, created_at);

CREATE TABLE IF NOT EXISTS ps_scheduled_jobs (
    job_id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    schedule_expr TEXT NOT NULL,
    status TEXT NOT NULL,
    next_run_at TEXT NOT NULL,
    last_run_at TEXT,
    lease_owner TEXT,
    lease_until TEXT,
    config_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ps_job_runs (
    job_run_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    attempt INTEGER NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    result_json TEXT,
    error_json TEXT
);

-- ====================================================================
-- Universe Generator tables
-- ====================================================================

CREATE TABLE IF NOT EXISTS universe_registry (
    id TEXT PRIMARY KEY,
    venue TEXT NOT NULL,
    market_type TEXT NOT NULL,
    symbol TEXT NOT NULL,
    base_asset TEXT,
    quote_asset TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    min_notional REAL,
    price_precision INTEGER,
    amount_precision INTEGER,
    first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    is_blacklisted INTEGER DEFAULT 0,
    blacklist_reason TEXT,
    metadata TEXT DEFAULT '{}'
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_universe_registry_venue_symbol
    ON universe_registry (venue, symbol, market_type);

CREATE TABLE IF NOT EXISTS universe_feature_snapshots (
    id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES universe_registry(id),
    ts TEXT NOT NULL DEFAULT (datetime('now')),
    quote_volume_24h REAL,
    volume_1h REAL,
    spread_bps REAL,
    depth_10bps_usd REAL,
    atr_pct REAL,
    rsi_14 REAL,
    rsi_15m REAL,
    atr_15m_pct REAL,
    return_15m REAL,
    last_candle_range_atr_15m REAL,
    return_1h REAL,
    return_4h REAL,
    return_24h REAL,
    funding_rate REAL,
    open_interest REAL,
    oi_delta_1h REAL,
    data_freshness_sec INTEGER,
    rsi_4h REAL,
    atr_4h_pct REAL,
    bb_width_4h REAL,
    vol_ratio_4h REAL,
    bb_squeeze INTEGER,
    raw TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_universe_features_asset_ts
    ON universe_feature_snapshots (asset_id, ts DESC);

CREATE TABLE IF NOT EXISTS universe_scanner_runs (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    policy_id TEXT,
    assets_scanned INTEGER DEFAULT 0,
    assets_passed INTEGER DEFAULT 0,
    snapshot_id TEXT,
    errors TEXT DEFAULT '[]',
    metrics TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS universe_snapshots (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    market_type TEXT NOT NULL DEFAULT 'crypto',
    policy_id TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    expires_at TEXT,
    summary TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_universe_snapshots_status
    ON universe_snapshots (status, created_at DESC);

CREATE TABLE IF NOT EXISTS universe_members (
    id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES universe_snapshots(id),
    asset_id TEXT NOT NULL REFERENCES universe_registry(id),
    symbol TEXT NOT NULL,
    rank INTEGER NOT NULL,
    total_score REAL NOT NULL,
    opportunity_score REAL,
    tradability_score REAL,
    liquidity_score REAL,
    estimated_cost REAL,
    crowding_risk REAL,
    late_entry_penalty REAL,
    scanner_family TEXT,
    scanner_direction TEXT,
    ttl_sec INTEGER NOT NULL DEFAULT 3600,
    expires_at TEXT NOT NULL,
    inclusion_reason TEXT DEFAULT '[]',
    risk_flags TEXT DEFAULT '[]',
    execution_eligible INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_universe_members_snapshot
    ON universe_members (snapshot_id, rank);

CREATE TABLE IF NOT EXISTS universe_forward_outcomes (
    id TEXT PRIMARY KEY,
    outcome_candidate_key TEXT,
    member_id TEXT NOT NULL REFERENCES universe_members(id),
    snapshot_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    venue TEXT,
    market_type TEXT NOT NULL DEFAULT 'spot',
    scanner_family TEXT,
    direction TEXT,
    horizon TEXT NOT NULL,
    entry_price_usd REAL NOT NULL,
    entry_spread_bps REAL,
    funding_rate_at_entry REAL,
    entry_ts TEXT NOT NULL,
    due_at TEXT NOT NULL,
    settled_at TEXT,
    exit_price_usd REAL,
    forward_return_pct REAL,
    gross_return_pct REAL,
    directional_return_pct REAL,
    estimated_fee_pct REAL,
    estimated_spread_slippage_pct REAL,
    estimated_funding_pct REAL,
    net_return_pct REAL,
    min_tradable_edge_pct REAL,
    mfe_pct REAL,
    mae_pct REAL,
    settlement_price_source TEXT,
    settlement_lag_sec REAL,
    cost_model_version TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
);
CREATE INDEX IF NOT EXISTS idx_forward_outcomes_status_due
    ON universe_forward_outcomes (status, due_at);
CREATE INDEX IF NOT EXISTS idx_forward_outcomes_member
    ON universe_forward_outcomes (member_id);
CREATE INDEX IF NOT EXISTS idx_forward_outcomes_family_horizon
    ON universe_forward_outcomes (scanner_family, horizon);
CREATE INDEX IF NOT EXISTS idx_forward_outcomes_candidate_horizon
    ON universe_forward_outcomes (outcome_candidate_key, horizon);

CREATE TABLE IF NOT EXISTS universe_policies (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_by TEXT NOT NULL DEFAULT 'system',
    active INTEGER DEFAULT 0,
    market_type TEXT NOT NULL DEFAULT 'crypto',
    family_quota TEXT NOT NULL DEFAULT '{}',
    scoring_weights TEXT NOT NULL DEFAULT '{}',
    filters TEXT NOT NULL DEFAULT '{}',
    ttl_policy TEXT NOT NULL DEFAULT '{}',
    explore_ratio REAL NOT NULL DEFAULT 0.2,
    max_weight_step REAL DEFAULT 0.15,
    change_reason TEXT,
    previous_policy_id TEXT
);

-- Runtime Session (2026-07-13): one row per Gateway PROCESS lifecycle. runtime_id
-- names the running version; session_id names this specific boot. Core trading
-- facts reference session_id via their runtime_session_id column. ended_at +
-- shutdown_clean stay NULL/0 until a clean shutdown records them, so a crash is
-- distinguishable from a clean exit.
CREATE TABLE IF NOT EXISTS runtime_sessions (
    session_id TEXT PRIMARY KEY,
    runtime_id TEXT NOT NULL DEFAULT 'runtime-local-current',
    boot_commit TEXT,
    config_hash TEXT,
    behavior_fingerprint TEXT,
    schema_version TEXT,
    active_scope_id TEXT,
    execution_mode TEXT,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at TEXT,
    shutdown_clean INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_runtime_sessions_runtime ON runtime_sessions (runtime_id, started_at DESC);
"""

_DATA_COLLECTORS_DDL = """
-- ====================================================================
-- Data Collectors Layer (market data)
-- ====================================================================

CREATE TABLE IF NOT EXISTS orderbook_snapshots (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    market_type TEXT NOT NULL DEFAULT 'unknown',
    bid_price REAL NOT NULL,
    ask_price REAL NOT NULL,
    spread_bps REAL NOT NULL,
    bid_depth_10bps_usd REAL,
    ask_depth_10bps_usd REAL,
    total_depth_10bps_usd REAL,
    imbalance_ratio REAL,
    fetch_latency_ms INTEGER,
    observed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_orderbook_sym_ts
    ON orderbook_snapshots (symbol, observed_at DESC);

CREATE TABLE IF NOT EXISTS funding_rate_snapshots (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    funding_rate REAL NOT NULL,
    funding_rate_annualized REAL NOT NULL,
    next_funding_time TEXT,
    mark_price REAL,
    observed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_funding_sym_ts
    ON funding_rate_snapshots (symbol, observed_at DESC);

CREATE TABLE IF NOT EXISTS open_interest_snapshots (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    open_interest_usd REAL NOT NULL,
    open_interest_coin REAL,
    oi_change_1h_pct REAL,
    long_short_ratio REAL,
    observed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_oi_sym_ts
    ON open_interest_snapshots (symbol, observed_at DESC);

CREATE TABLE IF NOT EXISTS liquidation_events (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity_usd REAL NOT NULL,
    price REAL NOT NULL,
    event_time TEXT NOT NULL,
    observed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_liquidation_sym_ts
    ON liquidation_events (symbol, event_time DESC);

CREATE TABLE IF NOT EXISTS perp_structure_snapshots (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    venue TEXT NOT NULL DEFAULT 'hyperliquid',
    source TEXT NOT NULL DEFAULT 'vergex',
    source_ref TEXT,
    source_hash TEXT,
    as_of_time TEXT NOT NULL,
    observed_at TEXT NOT NULL DEFAULT (datetime('now')),
    data_quality TEXT NOT NULL DEFAULT 'unknown',
    coverage_note TEXT,
    reference_price REAL,
    poc REAL,
    value_area_low REAL,
    value_area_high REAL,
    support REAL,
    resistance REAL,
    magnet REAL,
    short_liq_above_usd REAL,
    long_liq_below_usd REAL,
    long_unrealized_pnl_usd REAL,
    short_unrealized_pnl_usd REAL,
    top10_concentration REAL,
    top50_concentration REAL,
    holder_count INTEGER,
    long_holder_count INTEGER,
    short_holder_count INTEGER,
    cgo_direction TEXT,
    vap_direction TEXT,
    lfa_direction TEXT,
    cascade_risk REAL,
    convexity REAL,
    vendor_bias TEXT,
    vendor_rank INTEGER,
    vendor_universe_size INTEGER,
    structure_bias TEXT,
    composite_z REAL,
    structure_rank INTEGER,
    universe_size INTEGER,
    dimension_scores_json TEXT NOT NULL DEFAULT '{}',
    risk_context_json TEXT NOT NULL DEFAULT '{}',
    raw_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_perp_structure_sym_ts
    ON perp_structure_snapshots (symbol, as_of_time DESC);

CREATE TABLE IF NOT EXISTS perp_structure_shadow_outcomes (
    id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES perp_structure_snapshots(id),
    symbol TEXT NOT NULL,
    venue TEXT NOT NULL,
    market_type TEXT NOT NULL DEFAULT 'perp',
    direction TEXT NOT NULL,
    horizon TEXT NOT NULL,
    entry_price_usd REAL NOT NULL,
    entry_ts TEXT NOT NULL,
    due_at TEXT NOT NULL,
    settled_at TEXT,
    exit_price_usd REAL,
    directional_return_pct REAL,
    settlement_price_source TEXT,
    settlement_lag_sec REAL,
    status TEXT NOT NULL DEFAULT 'pending',
    policy_epoch_id TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    UNIQUE(snapshot_id, horizon)
);
CREATE INDEX IF NOT EXISTS idx_perp_structure_shadow_due
    ON perp_structure_shadow_outcomes(status, due_at);
CREATE INDEX IF NOT EXISTS idx_perp_structure_shadow_epoch
    ON perp_structure_shadow_outcomes(policy_epoch_id, horizon, status);

CREATE TABLE IF NOT EXISTS macro_snapshots (
    id TEXT PRIMARY KEY,
    btc_dominance REAL,
    total_market_cap_usd REAL,
    total_market_cap_change_24h_pct REAL,
    spy_close REAL,
    vix_close REAL,
    dxy_close REAL,
    nasdaq_close REAL,
    risk_regime TEXT NOT NULL DEFAULT 'neutral',
    observed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_macro_ts
    ON macro_snapshots (observed_at DESC);

-- Round 2 — Step 3: CoinGecko top-N markets (no API key, ~10 min cadence).
-- Single-batch atomicity: callers must read via latest_coingecko_top() which
-- pins observed_at to MAX() first, otherwise a 50-row read could mix batches.
CREATE TABLE IF NOT EXISTS coingecko_top_snapshots (
    id TEXT PRIMARY KEY,
    coin_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    name TEXT,
    price_usd REAL,
    market_cap_usd REAL,
    market_cap_rank INTEGER,
    volume_24h_usd REAL,
    price_change_1h_pct REAL,
    price_change_24h_pct REAL,
    price_change_7d_pct REAL,
    observed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_coingecko_top_obs
    ON coingecko_top_snapshots (observed_at DESC, market_cap_rank ASC);

-- Round 2 — Step 5: DefiLlama on-chain aggregates (stablecoin mcap, chain TVL, DEX volume).
-- Three metric rows per collection cycle (metric_type discriminator).
-- source='defillama'; query via latest_onchain_snapshots(metric_type=...).
CREATE TABLE IF NOT EXISTS onchain_snapshots (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    metric_type TEXT NOT NULL,
    total_usd REAL,
    change_24h_pct REAL,
    top_entries TEXT DEFAULT '[]',
    raw_summary TEXT DEFAULT '{}',
    observed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_onchain_source_ts
    ON onchain_snapshots (source, metric_type, observed_at DESC);
"""

_PREDICTION_OBSERVER_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS prediction_observer_runs (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    markets_scanned INTEGER DEFAULT 0,
    opportunities_created INTEGER DEFAULT 0,
    errors TEXT DEFAULT '[]',
    categories TEXT DEFAULT '[]'
);
"""

_PREDICTION_NORMALIZED_DDL = """
CREATE TABLE IF NOT EXISTS prediction_events (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL DEFAULT 'polymarket',
    external_event_id TEXT NOT NULL,
    slug TEXT,
    title TEXT NOT NULL,
    description TEXT,
    category TEXT,
    tags TEXT DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active',
    starts_at TEXT,
    ends_at TEXT,
    resolution_at TEXT,
    liquidity REAL,
    volume_24h REAL,
    raw TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS prediction_markets (
    id TEXT PRIMARY KEY,
    event_id TEXT REFERENCES prediction_events(id),
    source TEXT NOT NULL DEFAULT 'polymarket',
    external_market_id TEXT NOT NULL,
    question TEXT NOT NULL,
    condition_id TEXT,
    outcomes TEXT DEFAULT '[]',
    outcome_prices TEXT DEFAULT '[]',
    yes_probability REAL,
    no_probability REAL,
    probability_change_24h REAL,
    enable_orderbook INTEGER DEFAULT 0,
    best_bid REAL,
    best_ask REAL,
    spread_bps REAL,
    liquidity REAL,
    volume_24h REAL,
    status TEXT NOT NULL DEFAULT 'active',
    raw TEXT DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS prediction_probability_snapshots (
    id TEXT PRIMARY KEY,
    market_id TEXT NOT NULL REFERENCES prediction_markets(id),
    ts TEXT NOT NULL,
    yes_probability REAL,
    no_probability REAL,
    liquidity REAL,
    volume_24h REAL,
    source_latency_ms INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_pred_snap_market_ts ON prediction_probability_snapshots(market_id, ts);

CREATE TABLE IF NOT EXISTS prediction_market_claims (
    id TEXT PRIMARY KEY,
    market_id TEXT NOT NULL REFERENCES prediction_markets(id),
    run_id TEXT,
    claim_text TEXT NOT NULL,
    claim_type TEXT NOT NULL DEFAULT 'observation',
    direction TEXT,
    confidence REAL DEFAULT 0.5,
    source TEXT DEFAULT 'prediction_observer',
    metadata TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_pred_claims_market ON prediction_market_claims(market_id);
"""


# Additive column migrations for already-existing databases. (col, table, type)
# CREATE TABLE above covers fresh DBs; these keep older app.db files current.
_OUTCOME_INTEGRITY_POSITION_COLUMNS = (
    ("close_category", "positions", "TEXT NOT NULL DEFAULT 'unknown'"),
    ("entry_price_source", "positions", "TEXT NOT NULL DEFAULT 'unavailable'"),
    ("entry_fill_verified", "positions", "INTEGER NOT NULL DEFAULT 0"),
    ("exit_price_source", "positions", "TEXT NOT NULL DEFAULT 'unavailable'"),
    ("exit_fill_verified", "positions", "INTEGER NOT NULL DEFAULT 0"),
    ("outcome_eligible", "positions", "INTEGER NOT NULL DEFAULT 0"),
    ("outcome_exclusion_reason", "positions", "TEXT NOT NULL DEFAULT 'unclassified'"),
    ("reconciliation_identity", "positions", "TEXT"),
    ("reconciliation_evidence", "positions", "TEXT"),
    ("outcome_semantics_version", "positions", "TEXT"),
    ("funding_cost", "positions", "REAL"),
    ("funding_source", "positions", "TEXT NOT NULL DEFAULT 'unavailable'"),
)

_OUTCOME_INTEGRITY_DDL = (
    """CREATE TABLE IF NOT EXISTS historical_accounting_reconciliations (
        position_id TEXT PRIMARY KEY REFERENCES positions(id),
        reconciliation_version TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('reconstructed', 'unavailable')),
        reconstructed_realized_pnl REAL,
        funding_cost REAL,
        method TEXT NOT NULL,
        reason TEXT,
        evidence_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
        CHECK((status = 'reconstructed' AND reconstructed_realized_pnl IS NOT NULL)
              OR (status = 'unavailable' AND reconstructed_realized_pnl IS NULL))
    )""",
    """CREATE INDEX IF NOT EXISTS idx_historical_accounting_status
       ON historical_accounting_reconciliations(status, updated_at DESC)""",
    """CREATE TABLE IF NOT EXISTS position_disappearance_candidates (
        id TEXT PRIMARY KEY,
        position_id TEXT NOT NULL REFERENCES positions(id),
        identity_key TEXT NOT NULL,
        broker_id TEXT NOT NULL,
        account_environment TEXT NOT NULL,
        account_fingerprint TEXT NOT NULL,
        execution_mode TEXT NOT NULL,
        symbol TEXT NOT NULL,
        market_type TEXT NOT NULL,
        direction TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        first_observed_at TEXT NOT NULL,
        last_observed_at TEXT NOT NULL,
        confirmation_count INTEGER NOT NULL DEFAULT 1,
        direct_query_state TEXT NOT NULL DEFAULT 'not_attempted',
        semantics_version TEXT NOT NULL,
        resolved_at TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_position_disappearance_pending_identity
       ON position_disappearance_candidates(identity_key) WHERE status='pending'""",
    """CREATE INDEX IF NOT EXISTS idx_position_disappearance_position_status
       ON position_disappearance_candidates(position_id, status, updated_at DESC)""",
    """CREATE TABLE IF NOT EXISTS position_disappearance_observations (
        id TEXT PRIMARY KEY,
        candidate_id TEXT NOT NULL REFERENCES position_disappearance_candidates(id),
        observation_hash TEXT NOT NULL,
        view_identity TEXT NOT NULL,
        view_complete INTEGER NOT NULL,
        position_absent INTEGER NOT NULL,
        direct_query_state TEXT NOT NULL DEFAULT 'not_attempted',
        evidence_json TEXT NOT NULL DEFAULT '{}',
        observed_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(candidate_id, observation_hash)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_position_disappearance_observation_candidate
       ON position_disappearance_observations(candidate_id, observed_at ASC)""",
    """CREATE TABLE IF NOT EXISTS reconciliation_integrity_incidents (
        id TEXT PRIMARY KEY,
        incident_type TEXT NOT NULL DEFAULT 'close_reopen_churn',
        fingerprint TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        window_minutes INTEGER NOT NULL DEFAULT 30,
        churn_count INTEGER NOT NULL,
        identities_json TEXT NOT NULL DEFAULT '[]',
        reopen_sources_json TEXT NOT NULL DEFAULT '{}',
        detected_at TEXT NOT NULL,
        last_detected_at TEXT NOT NULL,
        last_clean_observed_at TEXT,
        clean_observation_count INTEGER NOT NULL DEFAULT 0,
        resolution_reason TEXT,
        resolved_by TEXT,
        resolved_at TEXT,
        semantics_version TEXT NOT NULL,
        details_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_reconciliation_integrity_active_type
       ON reconciliation_integrity_incidents(incident_type) WHERE status='active'""",
    """CREATE INDEX IF NOT EXISTS idx_reconciliation_integrity_status_time
       ON reconciliation_integrity_incidents(status, last_detected_at DESC)""",
    """CREATE TABLE IF NOT EXISTS outcome_hook_jobs (
        id TEXT PRIMARY KEY,
        position_id TEXT NOT NULL REFERENCES positions(id),
        hook_type TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt_at TEXT NOT NULL DEFAULT (datetime('now')),
        locked_at TEXT,
        claim_token TEXT,
        last_error TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
        completed_at TEXT,
        UNIQUE(position_id, hook_type)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_outcome_hook_jobs_ready
       ON outcome_hook_jobs(status, next_attempt_at, created_at)""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_serenity_post_mortems_position
       ON serenity_post_mortems(position_id)""",
)

_ADDITIVE_COLUMNS = [
    # AT-02: immutable stage boundaries around the AT-01 measurement ledger.
    ("parent_experiment_id", "learning_experiments", "TEXT"),
    ("activation_verdict_id", "learning_experiments", "TEXT"),
    ("stage_generation", "learning_experiments", "INTEGER NOT NULL DEFAULT 0"),
    ("experiment_stage", "learning_exposures", "TEXT"),
    ("assignment_unit_key", "learning_exposures", "TEXT"),
    ("behavior_contract_hash", "learning_exposures", "TEXT"),
    ("allocation_selected", "filter_candidate_outcomes", "INTEGER"),
    ("stage", "learning_contribution_verdicts", "TEXT"),
    ("behavior_contract_hash", "learning_contribution_verdicts", "TEXT"),
    ("last_failed_at", "learning_job_health", "TEXT"),
    ("last_failure_reason", "learning_job_health", "TEXT"),
    ("audit_actor", "learning_lifecycle_events", "TEXT NOT NULL DEFAULT 'learning_lifecycle_controller'"),
    ("audit_source", "learning_lifecycle_events", "TEXT NOT NULL DEFAULT 'learning_lifecycle'"),
    ("audit_mirror_status", "learning_lifecycle_events", "TEXT NOT NULL DEFAULT 'mirrored'"),
    ("audit_mirrored_at", "learning_lifecycle_events", "TEXT"),
    ("audit_failure_reason", "learning_lifecycle_events", "TEXT"),
    ("leverage", "trade_proposals", "INTEGER"),
    ("market_type", "orderbook_snapshots", "TEXT NOT NULL DEFAULT 'unknown'"),
    # A13: link signal_pending pi_decisions back to their source opportunity
    ("opportunity_id", "pi_decisions", "TEXT"),
    # Debate trace fields are audit-only: they explain how debate changed a
    # candidate's conviction without changing the decision behavior.
    ("debate_initial_conviction", "pi_decisions", "INTEGER"),
    ("debate_bull_confidence", "pi_decisions", "INTEGER"),
    ("debate_bear_confidence", "pi_decisions", "INTEGER"),
    ("debate_adjustment", "pi_decisions", "INTEGER"),
    # Keep the legacy controller verdict CHECK intact for existing databases.
    # New observational semantics live in additive unconstrained columns; the
    # legacy verdict/direction remain conservative for old readers.
    ("evidence_verdict", "controller_recommendations", "TEXT"),
    ("evidence_direction", "controller_recommendations", "TEXT"),
    ("filter_version", "controller_recommendations", "TEXT"),
    ("direction", "controller_recommendations", "TEXT"),
    ("market_type", "controller_recommendations", "TEXT"),
    ("horizon", "controller_recommendations", "TEXT"),
    ("passed_n", "controller_recommendations", "INTEGER"),
    ("rejected_n", "controller_recommendations", "INTEGER"),
    ("passed_shrunk_avg_net", "controller_recommendations", "REAL"),
    ("rejected_shrunk_avg_net", "controller_recommendations", "REAL"),
    ("outcome_coverage", "controller_recommendations", "REAL"),
    # Phase 2 (Serenity Option A): discriminator for which table opportunity_id
    # points at ('market_observer' → opportunities; 'serenity' → serenity_opportunities).
    ("signal_source", "pi_decisions", "TEXT"),
    # Pending/final Operator rows must remain instrument-scoped even when their
    # source opportunity is unavailable. Legacy rows stay NULL.
    ("market_type", "pi_decisions", "TEXT"),
    ("proposal_status", "pi_decisions", "TEXT"),
    ("proposal_reason", "pi_decisions", "TEXT"),
    ("stop_price", "trade_proposals", "REAL"),
    # CR-RE-01: preserve the proposal's original execution_mode on the decision row
    # (allowed_execution_mode gets downgraded to paper_internal on rejection).
    ("proposal_execution_mode", "risk_decisions", "TEXT"),
    # Risk -> Execution authority repair (2026-07-13). Historical rows remain
    # ``legacy`` so duplicate decisions already on disk cannot be mistaken for,
    # or conflict with, the new authoritative snapshot identity.
    ("decision_role", "risk_decisions", "TEXT NOT NULL DEFAULT 'legacy'"),
    ("evaluated_quantity", "risk_decisions", "REAL"),
    ("proposal_commit_hash", "risk_decisions", "TEXT"),
    ("risk_mode", "risk_decisions", "TEXT"),
    ("policy_id", "risk_decisions", "TEXT"),
    ("policy_revision", "risk_decisions", "INTEGER"),
    ("policy_hash", "risk_decisions", "TEXT"),
    # Durable pre-Order Risk allowances need the same economics used by the
    # account-leverage guard. Existing databases are populated by the verified
    # authority backfill after these additive columns are present.
    ("market_type", "entry_risk_reservations", "TEXT"),
    ("leverage", "entry_risk_reservations", "REAL"),
    # Durable exchange recovery and exact Order -> Position identity.  Nullable
    # keeps historical Orders honest instead of inventing missing linkage.
    ("client_order_id", "orders", "TEXT"),
    ("position_id", "orders", "TEXT"),
    ("close_reason", "orders", "TEXT"),
    ("strategy_id", "knowledge_items", "TEXT"),
    ("stop_price", "positions", "REAL"),
    ("take_profit_price", "positions", "REAL"),
    ("close_reason", "positions", "TEXT"),
    # OI-S01 remains visible in the additive registry, but _ensure_columns skips
    # this group so _ensure_outcome_integrity_schema can apply fields + evidence
    # tables atomically under one savepoint.
    *_OUTCOME_INTEGRITY_POSITION_COLUMNS,
    ("proposal_id", "positions", "TEXT"),
    ("order_id", "positions", "TEXT"),
    # Provenance of the causal-chain row: 'native' (Aletheia opened it normally) or
    # 'adopted' (reconciler adopted a pre-existing/external exchange position into the
    # local books with a synthetic-but-honest chain). Amends trust §1.1 — see
    # docs/governance/decisions/DECISIONS.md (2026-06-12) and
    # position_reconciler.adopt_position.
    ("source", "positions", "TEXT DEFAULT 'native'"),
    ("source", "orders", "TEXT DEFAULT 'native'"),
    ("source", "trade_proposals", "TEXT DEFAULT 'native'"),
    # Numeric calibration the LLM emits alongside the categorical confidence.
    # 0..100; NULL means the model didn't supply one (older rows or bad output).
    ("confidence_score", "opportunities", "INTEGER"),
    # Bounded paper-trader rotation cursor. The reader reserves capacity for
    # both NULL rows and oldest non-NULL retries on every normal run.
    ("last_ranked_at", "opportunities", "TEXT"),
    # Real exchange equity at snapshot time; NULL when exchange is unconfigured/stale.
    ("exchange_equity_usdt", "portfolio_snapshots", "REAL"),
    # Explicit account equity at snapshot time.  Old local rows stored position
    # notional in total_value_usdt, so their value remains NULL rather than being
    # re-labelled as an account balance.
    ("account_equity_usdt", "portfolio_snapshots", "REAL"),
    # Extended ticker fields added with CCXT migration (2026-06-08)
    ("high_24h", "market_snapshots", "REAL"),
    ("low_24h", "market_snapshots", "REAL"),
    ("provider", "market_snapshots", "TEXT"),
    # Fee provenance (2026-06-26): 'estimated' (flat-rate broker fee) vs 'reconciled'
    # (real exchange fillId fee). Honesty: estimated fees must be distinguishable.
    ("fee_source", "fills", "TEXT DEFAULT 'estimated'"),
    # Multi-asset class support (2026-06-08): "crypto" | "equity" | "prediction"
    ("asset_class", "opportunities", "TEXT"),
    # Exchange-aware instrument identity (2026-06-08): which exchange supplied the data.
    # Allows Binance spot BTC/USDT and Bybit spot BTC/USDT to coexist without overwriting.
    ("exchange", "market_snapshots", "TEXT"),
    ("under_review", "strategy_records", "INTEGER"),
    ("review_notes", "strategy_records", "TEXT"),
    ("review_updated_at", "strategy_records", "TEXT"),
    ("paper_trading_paused", "strategy_records", "INTEGER"),
    ("paper_pause_reason", "strategy_records", "TEXT"),
    ("paper_paused_at", "strategy_records", "TEXT"),
    ("kind", "agent_shell_sessions", "TEXT"),
    ("parent_session_id", "agent_shell_sessions", "TEXT"),
    ("title", "agent_shell_sessions", "TEXT"),
    ("runtime_id", "agent_shell_sessions", "TEXT DEFAULT 'runtime-local-current'"),
    ("runtime_id", "agent_shell_commands", "TEXT DEFAULT 'runtime-local-current'"),
    ("runtime_id", "agent_shell_messages", "TEXT DEFAULT 'runtime-local-current'"),
    ("runtime_id", "agent_shell_tasks", "TEXT DEFAULT 'runtime-local-current'"),
    ("runtime_id", "agent_shell_artifacts", "TEXT DEFAULT 'runtime-local-current'"),
    ("runtime_id", "agent_shell_events", "TEXT DEFAULT 'runtime-local-current'"),
    ("runtime_id", "agent_shell_heartbeats", "TEXT DEFAULT 'runtime-local-current'"),
    ("runtime_mode", "agent_shell_heartbeats", "TEXT DEFAULT 'current'"),
    ("runtime_state", "agent_shell_heartbeats", "TEXT DEFAULT 'current'"),
    ("active_branch_id", "agent_shell_heartbeats", "TEXT"),
    ("active_branch_kind", "agent_shell_heartbeats", "TEXT"),
    ("active_branch_title", "agent_shell_heartbeats", "TEXT"),
    # Serenity themes: enhanced fields for research workbench (Plan §7.7)
    ("run_id", "serenity_themes", "TEXT"),
    ("thesis", "serenity_themes", "TEXT"),
    ("confidence", "serenity_themes", "REAL"),
    ("novelty_score", "serenity_themes", "REAL"),
    ("actionability_score", "serenity_themes", "REAL"),
    ("evidence_strength", "serenity_themes", "REAL"),
    ("related_assets", "serenity_themes", "TEXT DEFAULT '[]'"),
    ("related_topics", "serenity_themes", "TEXT DEFAULT '[]'"),
    ("market_types", "serenity_themes", "TEXT DEFAULT '[]'"),
    ("expires_at", "serenity_themes", "TEXT"),
    ("promoted_at", "serenity_themes", "TEXT"),
    ("dismissed_at", "serenity_themes", "TEXT"),
    ("metadata", "serenity_themes", "TEXT DEFAULT '{}'"),
    # Serenity evidence: enhanced fields for research workbench (Plan §7.5)
    ("run_id", "serenity_evidence", "TEXT"),
    ("content_hash", "serenity_evidence", "TEXT"),
    ("quality_score", "serenity_evidence", "REAL"),
    ("url", "serenity_evidence", "TEXT"),
    ("published_at", "serenity_evidence", "TEXT"),
    ("fetched_at", "serenity_evidence", "TEXT"),
    ("metadata", "serenity_evidence", "TEXT DEFAULT '{}'"),
    # Durable Outcome-hook replay: one automatic Serenity post-mortem per Position.
    ("position_id", "serenity_post_mortems", "TEXT"),
    # D2 (2026-06-27): independent-evaluator authoritative tier + provenance.
    # `tier`/`evidence_tier` stay as the generator's self-graded proposal; these
    # columns hold what actually gates scoring/promotion. See serenity_evidence.py.
    ("assessed_tier", "serenity_evidence", "TEXT"),
    ("assessed_by", "serenity_evidence", "TEXT"),
    ("assessed_tier", "serenity_opportunities", "TEXT"),
    ("assessed_by", "serenity_opportunities", "TEXT"),
    # S4 (2026-06-28, AI Berkshire forced-verdict): machine-readable verdict blob.
    ("verdict_json", "serenity_opportunities", "TEXT DEFAULT '{}'"),
    # S2 (2026-06-28, AI Berkshire researchability): A/B/C deterministic grade.
    ("researchability_grade", "serenity_opportunities", "TEXT DEFAULT 'C'"),
    # S1 (2026-06-28, AI Berkshire 4-lens): which research lens produced this evidence.
    ("agent_lens", "serenity_evidence", "TEXT"),
    ("error_reason", "orders", "TEXT"),
    # Prediction Market Lens: structured metadata for prediction/equity opportunities
    ("metadata", "opportunities", "TEXT DEFAULT '{}'"),
    # Universe Generator: link opportunities back to the universe snapshot that sourced them
    ("universe_snapshot_id", "opportunities", "TEXT"),
    ("universe_member_id", "opportunities", "TEXT"),
    ("scanner_family", "opportunities", "TEXT"),
    ("execution_eligible", "opportunities", "INTEGER DEFAULT 1"),
    ("quote_volume_24h", "universe_members", "REAL"),
    ("spread_bps", "universe_members", "REAL"),
    # Edge Lab: structured execution hints on opportunities (signal expiry, zones)
    ("valid_until", "opportunities", "TEXT"),
    ("entry_zone", "opportunities", "TEXT"),
    ("stop_loss_hint", "opportunities", "REAL"),
    ("tp_hint", "opportunities", "REAL"),
    # Edge Lab: BTC benchmark + regime tag on positions for alpha/beta separation
    ("btc_entry_price", "positions", "REAL"),
    ("btc_exit_price", "positions", "REAL"),
    ("regime_tag", "positions", "TEXT"),
    # Universe Forward Outcomes: entry price at member inclusion time
    ("entry_price_usd", "universe_members", "REAL"),
    # Direction-aware calibration: the trade direction the scanner implied.
    ("direction", "universe_forward_outcomes", "TEXT"),
    ("scanner_direction", "universe_members", "TEXT"),
    # Cost-aware shadow rows link to the shared filter-candidate outcome.
    ("venue", "shadow_trades", "TEXT"),
    ("market_type", "shadow_trades", "TEXT NOT NULL DEFAULT 'spot'"),
    ("entry_spread_bps", "shadow_trades", "REAL"),
    ("funding_rate_at_entry", "shadow_trades", "REAL"),
    ("outcome_candidate_key", "shadow_trades", "TEXT"),
    ("gross_return_pct", "shadow_trades", "REAL"),
    ("net_pnl_pct", "shadow_trades", "REAL"),
    ("estimated_fee_pct", "shadow_trades", "REAL"),
    ("estimated_spread_slippage_pct", "shadow_trades", "REAL"),
    ("estimated_funding_pct", "shadow_trades", "REAL"),
    ("min_tradable_edge_pct", "shadow_trades", "REAL"),
    ("settlement_price_source", "shadow_trades", "TEXT"),
    ("settlement_lag_sec", "shadow_trades", "REAL"),
    ("cost_model_version", "shadow_trades", "TEXT"),
    # Counterfactual L0-completeness (causal_counterfactual_research_guidance.md):
    # bind each shadow row to the policy epoch active when it was recorded so cross-
    # epoch comparisons (threshold calibration, family scorecard) can partition
    # correctly. NULL for historical rows written before this column existed.
    ("policy_epoch_id", "shadow_trades", "TEXT"),
    ("venue", "alpha_control_observations", "TEXT"),
    ("market_type", "alpha_control_observations", "TEXT NOT NULL DEFAULT 'spot'"),
    ("scanner_family", "alpha_control_observations", "TEXT"),
    ("outcome_candidate_key", "alpha_control_observations", "TEXT"),
    # Cost-aware fixed-horizon outcome contract (2026-07-10).
    ("venue", "universe_forward_outcomes", "TEXT"),
    ("market_type", "universe_forward_outcomes", "TEXT NOT NULL DEFAULT 'spot'"),
    ("entry_spread_bps", "universe_forward_outcomes", "REAL"),
    ("funding_rate_at_entry", "universe_forward_outcomes", "REAL"),
    ("gross_return_pct", "universe_forward_outcomes", "REAL"),
    ("directional_return_pct", "universe_forward_outcomes", "REAL"),
    ("estimated_fee_pct", "universe_forward_outcomes", "REAL"),
    ("estimated_spread_slippage_pct", "universe_forward_outcomes", "REAL"),
    ("estimated_funding_pct", "universe_forward_outcomes", "REAL"),
    ("net_return_pct", "universe_forward_outcomes", "REAL"),
    ("min_tradable_edge_pct", "universe_forward_outcomes", "REAL"),
    ("mfe_pct", "universe_forward_outcomes", "REAL"),
    ("mae_pct", "universe_forward_outcomes", "REAL"),
    ("settlement_price_source", "universe_forward_outcomes", "TEXT"),
    ("settlement_lag_sec", "universe_forward_outcomes", "REAL"),
    ("cost_model_version", "universe_forward_outcomes", "TEXT"),
    ("outcome_candidate_key", "universe_forward_outcomes", "TEXT"),
    # Knowledge dedup: SHA-256 prefix of content for write-time duplicate detection.
    ("content_hash", "knowledge_items", "TEXT"),
    # US Equity integration: sub-category and canonical ticker on opportunities.
    # asset_subclass: "equity_daily" (yfinance daily bars primary) | "".
    # canonical_ticker: Yahoo Finance ticker for cross-path correlation (e.g. "AAPL").
    ("asset_subclass", "opportunities", "TEXT NOT NULL DEFAULT ''"),
    ("canonical_ticker", "opportunities", "TEXT NOT NULL DEFAULT ''"),
    # Futures integration: market type (spot/perp) carried through the member.
    ("market_type", "universe_members", "TEXT NOT NULL DEFAULT 'spot'"),
    # Market Regime: tag opportunities and universe members with regime context
    ("regime_tag", "opportunities", "TEXT"),
    ("regime_tag", "universe_members", "TEXT"),
    # Strategy Pool: direction affinity and applicable regimes
    ("direction_affinity", "strategy_records", "TEXT DEFAULT 'both'"),
    ("applicable_regimes", "strategy_records", "TEXT DEFAULT 'all'"),
    ("strategy_type", "strategy_records", "TEXT DEFAULT 'scanner'"),
    # Strategy-Exchange Affinity: per-strategy execution venue override
    ("execution_venue", "strategy_records", "TEXT"),
    # Batch 2 ATR sizing: volatility-scaled position notional
    ("atr_pct", "universe_members", "REAL"),
    # Phase 2A+.12: persist all-family scores dict for ensemble analysis
    ("all_scores", "universe_members", "TEXT DEFAULT '{}'"),
    ("entry_market_summary", "positions", "TEXT"),
    # LLM structured observations preserved from market_observer output
    ("key_observations", "opportunities", "TEXT NOT NULL DEFAULT ''"),
    # Trailing stop high-water mark for open positions
    ("trail_high_water", "positions", "REAL"),
    # Backtest equity curve (JSON array of floats, one per bar)
    ("equity_curve_json", "backtest_runs", "TEXT"),
    # Correlation guard: BTC beta for universe assets
    ("btc_beta", "universe_feature_snapshots", "REAL"),
    # Exchange-native TPSL bracket order IDs (JSON {"sl": id, "tp": id})
    ("exchange_tpsl_order_id", "positions", "TEXT"),
    # Exchange close accepted but not confirmed filled; position stays open for exposure.
    ("pending_close_order_id", "positions", "TEXT"),
    ("pending_close_status", "positions", "TEXT"),
    ("pending_close_requested_at", "positions", "TEXT"),
    # Derivatives signal dimensions for System Operator direction guidance
    ("taker_buy_ratio", "universe_feature_snapshots", "REAL"),
    ("vol_ratio", "universe_feature_snapshots", "REAL"),
    ("relative_strength_24h", "universe_feature_snapshots", "REAL"),
    # OI-price divergence: +1=hidden_accumulation, -1=weak_rally, 0=neutral
    ("oi_price_divergence", "universe_feature_snapshots", "INTEGER"),
    # Decision explainability: System Operator reasoning chain at entry time
    ("decision_reasoning", "positions", "TEXT"),
    # Close explainability: immutable-at-close source/actor/trigger record.
    # NULL remains the honest state for historical closures that predate it.
    ("exit_reasoning", "positions", "TEXT"),
    # Cost visibility: itemized fee/slippage for transparency
    ("entry_fee", "positions", "REAL"),
    ("exit_fee", "positions", "REAL"),
    ("slippage_cost", "positions", "REAL"),
    # Knowledge item payload storage (was missing — memo/feedback persist via this column)
    ("content", "knowledge_items", "TEXT"),
    # Market-aware exit audit trail
    ("last_market_signal_action", "positions", "TEXT"),
    ("last_market_signal_reason", "positions", "TEXT"),
    ("last_market_signal_at", "positions", "TEXT"),
    # Futures: leverage + estimated liquidation price recorded at open (Round 2)
    ("leverage", "positions", "INTEGER"),
    ("liquidation_price", "positions", "REAL"),
    # Causal chain: opportunity that opened this position (risk #16 sector check)
    ("opportunity_id", "positions", "TEXT"),
    # Strategy market affinity: spot | usdt_futures | both (Round 2 perp enablement)
    ("market_type_affinity", "strategy_records", "TEXT"),
    # Derivatives state stamped on perp opportunities (Round 2)
    ("funding_rate", "opportunities", "REAL"),
    ("open_interest", "opportunities", "REAL"),
    ("oi_delta_1h", "opportunities", "REAL"),
    # News collector dedup + full content + analysis
    ("dedup_key", "news_items", "TEXT"),
    ("content", "news_items", "TEXT"),
    ("impact_summary", "news_items", "TEXT"),
    ("analyzed_at", "news_items", "TEXT"),
    # Round 2 macro signals (Step 2 + Step 3): Fear & Greed + CoinGecko top movers.
    # All nullable — old rows untouched, new rows fill when their collectors fire.
    ("fng_value", "macro_snapshots", "INTEGER"),
    ("fng_classification", "macro_snapshots", "TEXT"),
    ("top_movers_summary", "macro_snapshots", "TEXT"),
    # Serenity Phase 4: execution-school discriminator. NOT NULL DEFAULT 'short_term'
    # so legacy proposals/positions migrate cleanly onto the existing short-term path;
    # only Serenity-sourced rows are stamped 'serenity' by submit_decision_as_proposal.
    ("trade_school", "trade_proposals", "TEXT NOT NULL DEFAULT 'short_term'"),
    ("trade_school", "positions", "TEXT NOT NULL DEFAULT 'short_term'"),
    # Quant review QR-2026-07-01-04: replay-stable paper attribution. Older
    # proposals keep NULL and the paper broker falls back to live context.
    ("attribution_snapshot", "trade_proposals", "TEXT"),
    # P2 / S4 (2026-06-28, AI Berkshire forced-verdict): PI must provide explicit
    # counter-argument even when recommending a trade.
    ("bear_case", "pi_decisions", "TEXT DEFAULT ''"),
    # P1 (2026-06-28, AI Berkshire belief decomposition): structured assumption list.
    ("key_assumptions", "pi_decisions", "TEXT DEFAULT '[]'"),
    ("learning_manifest_json", "pi_decisions", "TEXT NOT NULL DEFAULT '{}'"),
    ("evidence_manifest_json", "pi_decisions", "TEXT NOT NULL DEFAULT '{}'"),
    ("rsi_15m", "universe_feature_snapshots", "REAL"),
    ("atr_15m_pct", "universe_feature_snapshots", "REAL"),
    ("return_15m", "universe_feature_snapshots", "REAL"),
    ("last_candle_range_atr_15m", "universe_feature_snapshots", "REAL"),
    ("rsi_4h", "universe_feature_snapshots", "REAL"),
    ("atr_4h_pct", "universe_feature_snapshots", "REAL"),
    ("bb_width_4h", "universe_feature_snapshots", "REAL"),
    ("vol_ratio_4h", "universe_feature_snapshots", "REAL"),
    ("bb_squeeze", "universe_feature_snapshots", "INTEGER"),
    ("source_ref", "perp_structure_snapshots", "TEXT"),
    ("source_hash", "perp_structure_snapshots", "TEXT"),
    ("reference_price", "perp_structure_snapshots", "REAL"),
    # 2026-06-29: lesson block guard fields (dry-run window + TTL expiry).
    # Prevent immediately-accepted lessons from blocking all trades without
    # cooldown (sleeping bomb fix; see spec_lesson_guards.py LG-S01..S04).
    ("activated_at", "ps_lessons", "TEXT"),
    ("dry_run_until", "ps_lessons", "TEXT"),
    ("expires_at", "ps_lessons", "TEXT"),
    # P0-1 (2026-06-29, OpenAlice TaG borrow): proposal-level hash chain.
    # NULL on legacy rows; insert_trade_proposal() fills both for new writes.
    ("commit_hash", "trade_proposals", "TEXT"),
    ("parent_hash", "trade_proposals", "TEXT"),
    # Entry discipline (2026-07-03, owner discussion #6): take-profit price +
    # reward:risk committed at proposal time for limit entries. NULL on legacy
    # market proposals (closes, reconciler-adopted chains).
    ("take_profit", "trade_proposals", "REAL"),
    ("risk_reward", "trade_proposals", "REAL"),
    # EQ-R (2026-07-01, r-series/equity_chain): instrument classification on serenity opps.
    # asset_class distinguishes equity stock tokens from crypto so trading_context can
    # skip equity observations in the PI's order-routing context.
    # market_type ("spot"|"swap"|"perp") drives the slash/no-slash display rule.
    # NULL on legacy rows → treat as "crypto" / "spot" (safe default).
    ("asset_class", "serenity_opportunities", "TEXT NOT NULL DEFAULT 'crypto'"),
    ("market_type", "serenity_opportunities", "TEXT NOT NULL DEFAULT 'spot'"),
    ("structure_bias", "perp_structure_snapshots", "TEXT"),
    ("composite_z", "perp_structure_snapshots", "REAL"),
    ("structure_rank", "perp_structure_snapshots", "INTEGER"),
    ("universe_size", "perp_structure_snapshots", "INTEGER"),
    ("dimension_scores_json", "perp_structure_snapshots", "TEXT NOT NULL DEFAULT '{}'"),
    ("risk_context_json", "perp_structure_snapshots", "TEXT NOT NULL DEFAULT '{}'"),
    # P0-4 (2026-07-12): contribution verdicts carry full cohort identity so a stale
    # verdict cannot authorize a treatment after the policy epoch / stage / versions
    # changed. Authority selects the verdict matching the CURRENT (experiment, epoch,
    # stage); a verdict lacking a matching stamp cannot grant authority (fail-closed).
    # NULL on legacy rows → never matches the current cohort → cannot authorize.
    ("policy_epoch", "learning_contribution_verdicts", "TEXT"),
    ("stage", "learning_contribution_verdicts", "TEXT"),
    ("target_horizon", "learning_contribution_verdicts", "TEXT"),
    ("outcome_contract_version", "learning_contribution_verdicts", "TEXT"),
    ("metric_version", "learning_contribution_verdicts", "TEXT"),
    ("code_commit", "learning_contribution_verdicts", "TEXT"),
    # --- Runtime Session provenance (2026-07-13) -------------------------------
    # Expand-only, nullable. Every core trading-fact row carries the identity of
    # the process lifecycle (runtime_session) that PRODUCED it, the commit that
    # process booted on, and a fingerprint of the behaviour-affecting config.
    # Legacy rows stay NULL — history is never fabricated. See
    # aletheia/agent_runtime/runtime_session.py + docs/design/data/data_model.md.
    ("runtime_session_id", "opportunities", "TEXT"),
    ("producer_commit", "opportunities", "TEXT"),
    ("behavior_fingerprint", "opportunities", "TEXT"),
    ("runtime_session_id", "pi_decisions", "TEXT"),
    ("producer_commit", "pi_decisions", "TEXT"),
    ("behavior_fingerprint", "pi_decisions", "TEXT"),
    ("runtime_session_id", "trade_proposals", "TEXT"),
    ("producer_commit", "trade_proposals", "TEXT"),
    ("behavior_fingerprint", "trade_proposals", "TEXT"),
    ("runtime_session_id", "risk_decisions", "TEXT"),
    ("producer_commit", "risk_decisions", "TEXT"),
    ("behavior_fingerprint", "risk_decisions", "TEXT"),
    ("runtime_session_id", "orders", "TEXT"),
    ("producer_commit", "orders", "TEXT"),
    ("behavior_fingerprint", "orders", "TEXT"),
    ("runtime_session_id", "fills", "TEXT"),
    ("producer_commit", "fills", "TEXT"),
    ("behavior_fingerprint", "fills", "TEXT"),
    # Positions carry BOTH the opening producer identity and — stamped at close —
    # the outcome producer identity, so a restart between open and close does not
    # collapse the whole position onto the last run.
    ("runtime_session_id", "positions", "TEXT"),
    ("producer_commit", "positions", "TEXT"),
    ("behavior_fingerprint", "positions", "TEXT"),
    ("closed_runtime_session_id", "positions", "TEXT"),
    ("closed_producer_commit", "positions", "TEXT"),
    ("closed_behavior_fingerprint", "positions", "TEXT"),
    # --- Declarative Exit Intent / ExitPlan (exit-intent plan M1, 2026-07) ------
    # Per-position declarative exit intent authored by the System Operator. Hard
    # price levels reuse stop_price / take_profit_price; these columns carry the
    # rest. All nullable → legacy positions read back as provenance='default'
    # (config fallback), so this data layer is a behavior no-op until M2 wires the
    # executor to consume it. See aletheia/portfolio/exit_plan.py.
    ("expected_horizon_hours", "positions", "REAL"),
    ("trailing_json", "positions", "TEXT"),
    ("thesis_invalidation_json", "positions", "TEXT"),
    ("stop_source", "positions", "TEXT"),
    ("exit_plan_version", "positions", "INTEGER"),
    # M2: set when a horizon-due "must-answer" wake has been emitted for a position,
    # so the executor wakes the Operator once per horizon breach instead of every
    # cycle. Cleared when the Operator re-authors the horizon (M3 adjust_position).
    ("horizon_wake_sent_at", "positions", "TEXT"),
    # M3.5: operator's proactive limit-exit level. When set, the executor market-closes
    # the position once price reaches it (a locally-watched limit exit, OCO with the
    # stop — whichever price hits first closes the position). NULL = no limit exit.
    ("exit_limit_price", "positions", "REAL"),
    # Who authored the resting exit_limit_price: 'human' (dashboard manual close) or
    # 'system_operator' (AI proactive exit). Read by _decide_exit to pick the honest
    # close_reason — human_limit_exit vs operator_limit_exit — so a human's manual
    # limit is never counted as an AI-discretionary exit in exit-driver attribution.
    # NULL on legacy rows → treated as system_operator (the only pre-existing author).
    ("exit_limit_source", "positions", "TEXT"),
    # M2: carry the Operator's exit intent on the proposal so it reaches the position
    # at open (stamped by the execution gateway). NULL on legacy proposals.
    ("expected_horizon_hours", "trade_proposals", "REAL"),
    ("stop_source", "trade_proposals", "TEXT"),
    # Observer Learning outcome identity (#323, 2026-07-22): persist the 4h
    # outcome key on the universe_member row so market observer can carry it
    # into learning exposures after a DB reload. NULL on legacy rows → observer
    # skips outcome tracking for those members (fail-safe, not fail-open).
    ("learning_candidate_key", "universe_members", "TEXT"),
]


# Created only after _ensure_columns has added last_ranked_at on an existing
# database. The leading status key turns status='open' into an indexed SEARCH;
# the following expressions satisfy each oldest-first ORDER BY without a
# temporary sort tree. Partial predicates keep prediction rows and the opposite
# cursor state out of each index.
_OPPORTUNITY_ROTATION_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_opportunities_trade_unranked_rotation
ON opportunities(status, datetime(created_at), id)
WHERE last_ranked_at IS NULL
  AND COALESCE(NULLIF(asset_class, ''), 'crypto') IN ('crypto', 'equity');

CREATE INDEX IF NOT EXISTS idx_opportunities_trade_retry_rotation
ON opportunities(status, datetime(last_ranked_at), datetime(created_at), id)
WHERE last_ranked_at IS NOT NULL
  AND COALESCE(NULLIF(asset_class, ''), 'crypto') IN ('crypto', 'equity');
"""

_LEARNING_ADDITIVE_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_learning_exposures_assignment_unit
ON learning_exposures(
    experiment_id, concurrent_policy_epoch, experiment_stage,
    assignment, assignment_unit_key
);
CREATE INDEX IF NOT EXISTS idx_learning_lifecycle_audit_pending
ON learning_lifecycle_events(audit_mirror_status, created_at, event_id);
"""


def _ensure_columns(db) -> None:
    import sqlite3
    outcome_columns = set(_OUTCOME_INTEGRITY_POSITION_COLUMNS)
    for col, table, coltype in _ADDITIVE_COLUMNS:
        if (col, table, coltype) in outcome_columns:
            continue
        existing = {r["name"] for r in db.execute(f"PRAGMA table_info({table})").fetchall()}
        if col in existing:
            continue
        # ALTER TABLE ADD COLUMN cannot run inside a BEGIN/COMMIT, so the PRAGMA-then-ALTER
        # check above is a TOCTOU race when two threads call run_migrations() concurrently
        # (uvicorn startup + scheduler boot). Catch the duplicate and continue — any other
        # OperationalError still propagates.
        try:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise


def _repair_learning_experiment_activation(db) -> None:
    """Align legacy open experiments with their earliest durable exposure."""
    db.execute(
        "UPDATE learning_experiments SET "
        "status=CASE WHEN status='registered' THEN 'active' ELSE status END, "
        "started_at=("
        "SELECT MIN(exposed_at) FROM learning_exposures x "
        "WHERE x.experiment_id=learning_experiments.experiment_id"
        ") "
        "WHERE ended_at IS NULL "
        "AND EXISTS (SELECT 1 FROM learning_exposures x "
        "WHERE x.experiment_id=learning_experiments.experiment_id)"
    )


def _repair_terminal_serenity_skips(db) -> None:
    """Idempotently align active Serenity sources with exact historical final skips."""
    db.execute(
        "UPDATE serenity_opportunities SET status='reviewed' "
        "WHERE status='active' AND ("
        " SELECT COUNT(*) FROM pi_decisions final "
        " WHERE final.opportunity_id=serenity_opportunities.id "
        " AND COALESCE(final.signal_source, 'market_observer')='serenity' "
        " AND final.action NOT IN ('signal_pending', 'close', 'reduce')"
        ")=1 AND EXISTS ("
        " SELECT 1 FROM pi_decisions decision "
        " WHERE decision.opportunity_id=serenity_opportunities.id "
        " AND COALESCE(decision.signal_source, 'market_observer')='serenity' "
        " AND decision.action='skip' "
        " AND decision.symbol=serenity_opportunities.symbol "
        " AND decision.direction=serenity_opportunities.direction "
        " AND decision.market_type=serenity_opportunities.market_type"
        ")"
    )


def _ensure_outcome_integrity_schema(db) -> None:
    """Atomically add closure evidence fields and durable observation tables."""
    import sqlite3

    db.execute("SAVEPOINT outcome_integrity_schema")
    try:
        existing = {
            row["name"] for row in db.execute("PRAGMA table_info(positions)").fetchall()
        }
        for col, table, coltype in _OUTCOME_INTEGRITY_POSITION_COLUMNS:
            if col in existing:
                continue
            try:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
            existing.add(col)
        for statement in _OUTCOME_INTEGRITY_DDL:
            db.execute(statement)
        db.execute("RELEASE SAVEPOINT outcome_integrity_schema")
    except sqlite3.Error:
        db.execute("ROLLBACK TO SAVEPOINT outcome_integrity_schema")
        db.execute("RELEASE SAVEPOINT outcome_integrity_schema")
        raise


def _ensure_market_ohlcv_instrument_identity(db) -> None:
    """Upgrade legacy symbol-only OHLCV storage to full instrument identity."""
    import sqlite3

    if not db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_ohlcv'"
    ).fetchone():
        return
    info = db.execute("PRAGMA table_info(market_ohlcv)").fetchall()
    columns = {row["name"] for row in info}
    pk = [row["name"] for row in sorted(info, key=lambda row: row["pk"]) if row["pk"]]
    expected_pk = ["exchange", "symbol", "market_type", "timeframe", "ts"]
    if "market_type" in columns and pk == expected_pk:
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_market_ohlcv_instrument_tf_ts "
            "ON market_ohlcv(exchange,symbol,market_type,timeframe,ts DESC)"
        )
        return

    exchange_expr = (
        "CASE "
        "WHEN exchange IS NOT NULL AND trim(exchange) NOT IN ('', 'ccxt') "
        "THEN lower(exchange) "
        "WHEN provider IS NOT NULL AND trim(provider) NOT IN ('', 'ccxt') "
        "THEN lower(provider) "
        "ELSE 'legacy_unknown' END"
        if "exchange" in columns
        else (
            "CASE WHEN provider IS NOT NULL AND trim(provider) NOT IN ('', 'ccxt') "
            "THEN lower(provider) ELSE 'legacy_unknown' END"
        )
    )
    db.execute("SAVEPOINT market_ohlcv_identity_upgrade")
    try:
        db.execute("DROP TABLE IF EXISTS market_ohlcv_identity_new")
        db.execute(
            "CREATE TABLE market_ohlcv_identity_new ("
            "exchange TEXT NOT NULL DEFAULT 'legacy_unknown', symbol TEXT NOT NULL, "
            "market_type TEXT NOT NULL DEFAULT 'spot', "
            "timeframe TEXT NOT NULL, ts INTEGER NOT NULL, open REAL NOT NULL, "
            "high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL, "
            "volume REAL NOT NULL, provider TEXT NOT NULL DEFAULT 'ccxt', "
            "PRIMARY KEY (exchange, symbol, market_type, timeframe, ts))"
        )
        db.execute(
            "INSERT OR REPLACE INTO market_ohlcv_identity_new "
            "(exchange,symbol,market_type,timeframe,ts,open,high,low,close,volume,provider) "
            f"SELECT {exchange_expr},symbol,'spot',timeframe,ts,open,high,low,close,volume,provider "
            "FROM market_ohlcv"
        )
        db.execute("DROP TABLE market_ohlcv")
        db.execute("ALTER TABLE market_ohlcv_identity_new RENAME TO market_ohlcv")
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_market_ohlcv_instrument_tf_ts "
            "ON market_ohlcv(exchange,symbol,market_type,timeframe,ts DESC)"
        )
        db.execute("RELEASE SAVEPOINT market_ohlcv_identity_upgrade")
    except sqlite3.Error:
        db.execute("ROLLBACK TO SAVEPOINT market_ohlcv_identity_upgrade")
        db.execute("RELEASE SAVEPOINT market_ohlcv_identity_upgrade")
        raise


def _ensure_orderbook_instrument_index(db) -> None:
    columns = {
        row["name"] for row in db.execute("PRAGMA table_info(orderbook_snapshots)")
    }
    if {"exchange", "symbol", "market_type", "observed_at"} <= columns:
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_orderbook_instrument_ts "
            "ON orderbook_snapshots(exchange,symbol,market_type,observed_at DESC)"
        )


def _backfill_forward_outcome_instrument_identity(db) -> None:
    """Recover point-in-time instrument fields for legacy pending/settled rows.

    Identity and entry inputs are copied only from the member/feature rows that
    caused the outcome. Historical net returns are deliberately not synthesized:
    old rows keep NULL net fields so gross-only evidence cannot enter learning.
    """
    tables = {
        row["name"]
        for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    required = {
        "universe_forward_outcomes",
        "universe_members",
        "universe_registry",
        "universe_feature_snapshots",
    }
    if not required <= tables:
        return
    db.execute(
        "UPDATE universe_forward_outcomes SET "
        "venue = COALESCE(venue, ("
        "  SELECT ur.venue FROM universe_members um "
        "  JOIN universe_registry ur ON ur.id = um.asset_id "
        "  WHERE um.id = universe_forward_outcomes.member_id"
        ")), "
        "entry_spread_bps = COALESCE(entry_spread_bps, ("
        "  SELECT um.spread_bps FROM universe_members um "
        "  WHERE um.id = universe_forward_outcomes.member_id"
        ")) "
        "WHERE (venue IS NULL AND EXISTS ("
        "  SELECT 1 FROM universe_members um "
        "  JOIN universe_registry ur ON ur.id = um.asset_id "
        "  WHERE um.id = universe_forward_outcomes.member_id "
        "    AND ur.venue IS NOT NULL"
        ")) OR (entry_spread_bps IS NULL AND EXISTS ("
        "  SELECT 1 FROM universe_members um "
        "  WHERE um.id = universe_forward_outcomes.member_id "
        "    AND um.spread_bps IS NOT NULL"
        "))"
    )
    db.execute(
        "UPDATE universe_forward_outcomes SET market_type = ("
        "  SELECT um.market_type FROM universe_members um "
        "  WHERE um.id = universe_forward_outcomes.member_id"
        ") WHERE EXISTS ("
        "  SELECT 1 FROM universe_members um "
        "  WHERE um.id = universe_forward_outcomes.member_id "
        "    AND um.market_type IS NOT NULL "
        "    AND um.market_type != universe_forward_outcomes.market_type"
        ")"
    )
    _backfill_forward_outcome_funding(db)


def _backfill_forward_outcome_funding(db) -> None:
    db.execute(
        "UPDATE universe_forward_outcomes SET funding_rate_at_entry = ("
        "  SELECT ufs.funding_rate FROM universe_members um "
        "  JOIN universe_feature_snapshots ufs ON ufs.asset_id = um.asset_id "
        "  WHERE um.id = universe_forward_outcomes.member_id "
        "    AND datetime(ufs.ts) <= datetime(universe_forward_outcomes.entry_ts) "
        "  ORDER BY datetime(ufs.ts) DESC LIMIT 1"
        ") "
        "WHERE funding_rate_at_entry IS NULL "
        "AND market_type IN ('perp', 'futures', 'swap', 'usdt_futures') "
        "AND EXISTS ("
        "  SELECT 1 FROM universe_members um "
        "  JOIN universe_feature_snapshots ufs ON ufs.asset_id = um.asset_id "
        "  WHERE um.id = universe_forward_outcomes.member_id "
        "    AND ufs.funding_rate IS NOT NULL "
        "    AND datetime(ufs.ts) <= datetime(universe_forward_outcomes.entry_ts)"
        ")"
    )


def _ensure_filter_evidence_indexes(db) -> None:
    """Create indexes that depend on additive columns after those columns exist."""
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_shadow_trades_outcome "
        "ON shadow_trades(outcome_candidate_key)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_alpha_control_outcome "
        "ON alpha_control_observations(outcome_candidate_key)"
    )
    # Learning cohort builder queries filter_candidate_outcomes by outcome_candidate_key.
    # Without this index the IN(...) lookup was a full-table scan on 300k+ rows (#332).
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_filter_candidate_outcomes_key "
        "ON filter_candidate_outcomes(outcome_candidate_key)"
    )
    # Portfolio-guard detection in get_operator_decisions joins filter_decisions by
    # candidate_key + filter_name + decision (#330). The existing idx_filter_decisions_candidate
    # covers (candidate_key, sequence_no) but not the filter_name/decision predicates,
    # so add a covering index for the most common guard lookup pattern.
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_filter_decisions_guard_lookup "
        "ON filter_decisions(candidate_key, filter_name, decision)"
    )


def fts5_available(db) -> bool:
    """True if this SQLite build supports FTS5 (some distro builds omit it)."""
    try:
        db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts5_probe USING fts5(x)")
        db.execute("DROP TABLE IF EXISTS _fts5_probe")
        return True
    except Exception:
        return False


def _ensure_knowledge_fts(db) -> None:
    """Create the knowledge full-text index (experience-loop retrieval, §6).

    Kept OUT of SCHEMA_SQL so a SQLite build without FTS5 can't break every other
    migration — this is a best-effort, optional index. retrieval.py degrades to
    "no lessons" when the table is absent.
    """
    if not fts5_available(db):
        return
    # content-less FTS5: we store the searchable text directly. item_id/item_type
    # are UNINDEXED so they're retrievable but not tokenized.
    db.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5("
        "title, body, item_type UNINDEXED, item_id UNINDEXED, "
        "confidence UNINDEXED, created_at UNINDEXED, outcome UNINDEXED)"
    )


def _ensure_runtime_baseline(db) -> None:
    """Seed the local current runtime and attach legacy operator rows to it."""
    try:
        from aletheia import __version__
    except Exception:
        __version__ = "unknown"

    db.execute(
        """
        INSERT OR IGNORE INTO runtime_registry (
            runtime_id, state, code_version, config_version, schema_version,
            build_git_sha, release_bundle_hash, mode, started_at, promoted_at, metadata
        ) VALUES (
            'runtime-local-current',
            'current',
            ?,
            'local-config',
            'sqlite-runtime-continuity-v1',
            '',
            'local-bootstrap',
            'current',
            datetime('now'),
            datetime('now'),
            '{"source":"migration"}'
        )
        """,
        (__version__,),
    )
    current = db.execute(
        "SELECT runtime_id FROM runtime_registry WHERE state = 'current' "
        "ORDER BY COALESCE(promoted_at, created_at) DESC, rowid DESC LIMIT 1"
    ).fetchone()
    current_id = current["runtime_id"] if current else "runtime-local-current"
    for table in (
        "agent_shell_sessions",
        "agent_shell_commands",
        "agent_shell_messages",
        "agent_shell_tasks",
        "agent_shell_artifacts",
        "agent_shell_events",
        "agent_shell_heartbeats",
    ):
        existing = {r["name"] for r in db.execute(f"PRAGMA table_info({table})").fetchall()}
        if "runtime_id" in existing:
            db.execute(
                f"UPDATE {table} SET runtime_id = ? "
                "WHERE runtime_id IS NULL OR runtime_id = ''",
                (current_id,),
            )
    heartbeat_cols = {r["name"] for r in db.execute("PRAGMA table_info(agent_shell_heartbeats)").fetchall()}
    if "runtime_mode" in heartbeat_cols:
        db.execute(
            "UPDATE agent_shell_heartbeats SET runtime_mode = 'current' "
            "WHERE runtime_mode IS NULL OR runtime_mode = ''"
        )
    if "runtime_state" in heartbeat_cols:
        db.execute(
            "UPDATE agent_shell_heartbeats SET runtime_state = 'current' "
            "WHERE runtime_state IS NULL OR runtime_state = ''"
        )


_CORE_INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_proposals_strategy_id ON trade_proposals(strategy_id);
CREATE INDEX IF NOT EXISTS idx_proposals_commit_hash ON trade_proposals(commit_hash);
CREATE INDEX IF NOT EXISTS idx_orders_proposal_id ON orders(proposal_id);
CREATE INDEX IF NOT EXISTS idx_orders_client_order_id ON orders(client_order_id);
CREATE INDEX IF NOT EXISTS idx_orders_position_id ON orders(position_id);
CREATE INDEX IF NOT EXISTS idx_fills_order_id ON fills(order_id);
CREATE INDEX IF NOT EXISTS idx_risk_decisions_proposal_id ON risk_decisions(proposal_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_risk_authoritative_snapshot_v2
ON risk_decisions(
    proposal_id,
    COALESCE(proposal_commit_hash, ''),
    COALESCE(evaluated_quantity, -1.7976931348623157e308),
    COALESCE(policy_hash, '')
)
WHERE decision_role = 'authoritative';
CREATE INDEX IF NOT EXISTS idx_positions_strategy_id ON positions(strategy_id);
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
CREATE INDEX IF NOT EXISTS idx_knowledge_items_type_status ON knowledge_items(item_type, status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_news_dedup ON news_items(dedup_key);
CREATE INDEX IF NOT EXISTS idx_agent_runs_started_at ON agent_runs(started_at);
CREATE INDEX IF NOT EXISTS idx_agent_run_steps_run_created
ON agent_run_steps(run_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_agent_tool_calls_run_created
ON agent_tool_calls(run_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_funding_symbol_exchange_observed
ON funding_rate_snapshots(symbol, exchange, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_oi_symbol_exchange_observed
ON open_interest_snapshots(symbol, exchange, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_filter_decision_batches_evaluated
ON filter_decision_batches(evaluated_at);
CREATE INDEX IF NOT EXISTS idx_filter_candidate_outcomes_entry
ON filter_candidate_outcomes(entry_ts);
CREATE INDEX IF NOT EXISTS idx_market_ohlcv_timeframe_ts
ON market_ohlcv(timeframe, ts);
CREATE INDEX IF NOT EXISTS idx_opportunities_status_created
ON opportunities(status, created_at);
CREATE INDEX IF NOT EXISTS idx_filter_decisions_opportunity_evaluated
ON filter_decisions(opportunity_id, evaluated_at DESC);
CREATE INDEX IF NOT EXISTS idx_filter_decisions_opportunity_run
ON filter_decisions(opportunity_id, run_id, stage, sequence_no);
"""


_STRATEGY_BACKTEST_DDL = """
CREATE TABLE IF NOT EXISTS backtest_runs (
    id TEXT PRIMARY KEY,
    strategy_id TEXT REFERENCES strategy_records(id),
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    direction TEXT NOT NULL,
    bar_count INTEGER NOT NULL DEFAULT 0,
    initial_capital REAL NOT NULL DEFAULT 1000.0,
    cost_model_json TEXT NOT NULL DEFAULT '{}',
    ruleset_json TEXT NOT NULL DEFAULT '{}',
    trade_count INTEGER NOT NULL DEFAULT 0,
    win_rate REAL NOT NULL DEFAULT 0.0,
    total_return_pct REAL NOT NULL DEFAULT 0.0,
    expectancy REAL NOT NULL DEFAULT 0.0,
    profit_factor REAL NOT NULL DEFAULT 0.0,
    max_drawdown_pct REAL NOT NULL DEFAULT 0.0,
    sharpe_approx REAL NOT NULL DEFAULT 0.0,
    avg_trade_bars REAL NOT NULL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'completed',
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS backtest_trades (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES backtest_runs(id),
    trade_index INTEGER NOT NULL,
    direction TEXT NOT NULL,
    entry_ts INTEGER NOT NULL,
    exit_ts INTEGER NOT NULL,
    entry_bar_index INTEGER NOT NULL,
    exit_bar_index INTEGER NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL NOT NULL,
    quantity REAL NOT NULL,
    entry_fee REAL NOT NULL DEFAULT 0.0,
    exit_fee REAL NOT NULL DEFAULT 0.0,
    entry_slippage REAL NOT NULL DEFAULT 0.0,
    exit_slippage REAL NOT NULL DEFAULT 0.0,
    pnl REAL NOT NULL DEFAULT 0.0,
    return_pct REAL NOT NULL DEFAULT 0.0,
    exit_reason TEXT NOT NULL DEFAULT 'signal'
);

CREATE INDEX IF NOT EXISTS idx_backtest_trades_run ON backtest_trades(run_id);
CREATE INDEX IF NOT EXISTS idx_backtest_runs_strategy ON backtest_runs(strategy_id);
CREATE INDEX IF NOT EXISTS idx_backtest_runs_created ON backtest_runs(created_at DESC);
"""


_MARKET_REGIME_DDL = """
CREATE TABLE IF NOT EXISTS market_regimes (
    id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.5,
    long_bias REAL NOT NULL DEFAULT 0.5,
    short_bias REAL NOT NULL DEFAULT 0.5,
    signals TEXT DEFAULT '{}',
    detected_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_market_regimes_detected ON market_regimes(detected_at DESC);
"""




_PI_DECISIONS_DDL = """
CREATE TABLE IF NOT EXISTS pi_decisions (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    symbol TEXT,
    direction TEXT,
    market_type TEXT,
    action TEXT NOT NULL DEFAULT 'skip',
    conviction INTEGER NOT NULL DEFAULT 0,
    reasoning TEXT DEFAULT '',
    proposal_id TEXT,
    proposal_status TEXT,
    proposal_reason TEXT,
    debate_ran INTEGER NOT NULL DEFAULT 0,
    debate_initial_conviction INTEGER,
    debate_bull_confidence INTEGER,
    debate_bear_confidence INTEGER,
    debate_adjustment INTEGER,
    opportunity_id TEXT,
    learning_manifest_json TEXT NOT NULL DEFAULT '{}',
    evidence_manifest_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_pi_decisions_symbol ON pi_decisions(symbol, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pi_decisions_created ON pi_decisions(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pi_decisions_opportunity ON pi_decisions(opportunity_id);
"""

_OPERATOR_EVALUATION_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS operator_evaluation_events (
    id TEXT PRIMARY KEY,
    dedup_key TEXT NOT NULL UNIQUE,
    event_kind TEXT NOT NULL,
    trigger TEXT NOT NULL,
    pending_unresolved_total INTEGER NOT NULL,
    pending_visible_count INTEGER NOT NULL,
    pending_oldest_created_at TEXT,
    queue_snapshot_json TEXT NOT NULL DEFAULT '{}',
    data_health_json TEXT NOT NULL DEFAULT '{}',
    policy_epoch_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_operator_evaluation_events_created
    ON operator_evaluation_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_operator_evaluation_events_kind
    ON operator_evaluation_events(event_kind, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_operator_evaluation_events_defer_active
    ON operator_evaluation_events(event_kind, created_at DESC)
    WHERE event_kind LIKE 'operator_decision_deferred_%';
"""

_CAPITAL_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS capital_events (
    id TEXT PRIMARY KEY,
    broker_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN ('deposit','withdraw')),
    amount_usdt REAL NOT NULL,
    equity_at_event REAL,
    detected_at TEXT NOT NULL DEFAULT (datetime('now')),
    note TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_capital_events_detected ON capital_events(detected_at);
"""

_PAPER_ACCOUNT_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS paper_account_events (
    id TEXT PRIMARY KEY,
    action TEXT NOT NULL CHECK (action IN (
        'initial_funding', 'manual_credit', 'manual_debit', 'manual_set_balance',
        'position_opened', 'position_closed'
    )),
    amount_usdt REAL NOT NULL,
    cash_balance_before_usdt REAL NOT NULL,
    cash_balance_after_usdt REAL NOT NULL,
    actor TEXT NOT NULL DEFAULT 'system',
    source TEXT NOT NULL DEFAULT 'paper_account',
    position_id TEXT,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(action, position_id)
);
CREATE INDEX IF NOT EXISTS idx_paper_account_events_created
    ON paper_account_events(created_at DESC);
"""

# Per-close ledger (exit-intent plan R1 / M3.5 reduce). One row per close EVENT — a
# full close writes one row; a partial reduce (future) writes one row per reduction —
# so realized PnL is summed from close EVENTS rather than from positions.status='closed'
# (which assumes one-close-per-position and misses partial reductions). Additive: the
# daily-loss breaker + outcome analytics are rewired onto this in a later stage.
_POSITION_CLOSE_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS position_close_events (
    id TEXT PRIMARY KEY,
    position_id TEXT NOT NULL,
    symbol TEXT,
    direction TEXT,
    execution_mode TEXT,
    close_reason TEXT,
    exit_price REAL,
    closed_qty REAL,
    realized_pnl REAL,
    entry_fee REAL,
    exit_fee REAL,
    is_partial INTEGER NOT NULL DEFAULT 0,
    closed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_position_close_events_position
    ON position_close_events(position_id);
CREATE INDEX IF NOT EXISTS idx_position_close_events_closed_at
    ON position_close_events(closed_at DESC);
"""

_VOLUME_RANKING_DDL = """
CREATE TABLE IF NOT EXISTS volume_ranking_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbols_json TEXT NOT NULL,
    observed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_volume_ranking_ts ON volume_ranking_snapshots(observed_at DESC);
"""

_OHLCV_BACKFILL_STATE_DDL = """
CREATE TABLE IF NOT EXISTS ohlcv_backfill_state (
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    market_type TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running','partial','failed','ready')),
    bar_count INTEGER NOT NULL DEFAULT 0,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT,
    next_attempt_at TEXT,
    last_error TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (exchange, symbol, market_type, timeframe)
);
CREATE INDEX IF NOT EXISTS idx_ohlcv_backfill_due
    ON ohlcv_backfill_state(status, next_attempt_at);
"""

_COLLECTOR_HEALTH_STATE_DDL = """
CREATE TABLE IF NOT EXISTS collector_health_state (
    domain TEXT PRIMARY KEY,
    cadence_sec INTEGER NOT NULL,
    last_status TEXT NOT NULL CHECK (last_status IN (
        'success','partial','empty','failed','timed_out','skipped_budget',
        'not_configured','disabled'
    )),
    last_tick INTEGER,
    last_scheduled_at TEXT NOT NULL,
    last_attempt_at TEXT,
    last_finished_at TEXT,
    last_success_at TEXT,
    duration_ms INTEGER,
    consecutive_problems INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    successes INTEGER NOT NULL DEFAULT 0,
    partial_results INTEGER NOT NULL DEFAULT 0,
    empty_results INTEGER NOT NULL DEFAULT 0,
    timeouts INTEGER NOT NULL DEFAULT 0,
    budget_skips INTEGER NOT NULL DEFAULT 0,
    failures INTEGER NOT NULL DEFAULT 0,
    last_error_code TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# Filter feedback controller — append-only shadow log of exact-version pass/reject
# findings. Legacy action columns remain constrained for old readers; evidence_*
# carries observational verdicts. Rows never advance past status='shadow_only'.
_CONTROLLER_RECOMMENDATIONS_DDL = """
CREATE TABLE IF NOT EXISTS controller_recommendations (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    filter_name TEXT NOT NULL,
    scanner_family TEXT NOT NULL,
    stage TEXT NOT NULL,
    current_value TEXT,
    suggested_direction TEXT NOT NULL CHECK (suggested_direction IN ('loosen','hold','tighten','n/a')),
    verdict TEXT NOT NULL CHECK (verdict IN ('loosen','hold','tighten','insufficient_data')),
    error_signal REAL,
    n_samples INTEGER NOT NULL DEFAULT 0,
    reasoning TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'shadow_only' CHECK (status IN ('shadow_only','proposed','approved','applied','rejected')),
    run_id TEXT,
    evidence_verdict TEXT,
    evidence_direction TEXT,
    filter_version TEXT,
    direction TEXT,
    market_type TEXT,
    horizon TEXT,
    passed_n INTEGER,
    rejected_n INTEGER,
    passed_shrunk_avg_net REAL,
    rejected_shrunk_avg_net REAL,
    outcome_coverage REAL
);
CREATE INDEX IF NOT EXISTS idx_controller_recs_created ON controller_recommendations(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_controller_recs_filter ON controller_recommendations(filter_name, scanner_family);
CREATE INDEX IF NOT EXISTS idx_controller_recs_run ON controller_recommendations(run_id);
"""


_FILTER_SHADOW_EXPERIMENTS_DDL = """
CREATE TABLE IF NOT EXISTS filter_shadow_experiments (
    id TEXT PRIMARY KEY,
    experiment_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    started_at TEXT NOT NULL,
    filter_name TEXT NOT NULL,
    filter_version TEXT NOT NULL,
    stage TEXT NOT NULL,
    scanner_family TEXT NOT NULL,
    direction TEXT NOT NULL,
    market_type TEXT NOT NULL,
    horizon TEXT NOT NULL,
    control_parameter TEXT NOT NULL,
    treatment_parameter TEXT NOT NULL,
    assignment_policy TEXT NOT NULL DEFAULT 'prospective_first_exposure',
    status TEXT NOT NULL DEFAULT 'collecting'
        CHECK (status IN ('collecting','ready_for_review','completed_no_support','invalidated')),
    min_samples INTEGER NOT NULL,
    min_coverage REAL NOT NULL,
    min_edge_delta_pct REAL NOT NULL,
    source_replay TEXT NOT NULL DEFAULT '{}',
    production_mutation_allowed INTEGER NOT NULL DEFAULT 0
        CHECK (production_mutation_allowed = 0),
    last_evaluated_at TEXT,
    completed_at TEXT,
    invalidation_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_filter_shadow_experiments_status
    ON filter_shadow_experiments(status, started_at);
CREATE INDEX IF NOT EXISTS idx_filter_shadow_experiments_scope
    ON filter_shadow_experiments(
        filter_name, filter_version, scanner_family, direction, market_type, horizon
    );
CREATE UNIQUE INDEX IF NOT EXISTS idx_filter_shadow_experiments_fixed_scope
    ON filter_shadow_experiments(
        filter_name, filter_version, stage, scanner_family, direction, market_type, horizon
    );

CREATE TABLE IF NOT EXISTS filter_shadow_evaluations (
    id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL REFERENCES filter_shadow_experiments(id),
    created_at TEXT NOT NULL,
    evidence_through_at TEXT NOT NULL,
    disposition TEXT NOT NULL
        CHECK (disposition IN ('collecting','ready_for_review','completed_no_support','invalidated')),
    control_candidate_count INTEGER NOT NULL DEFAULT 0,
    control_settled_n INTEGER NOT NULL DEFAULT 0,
    control_outcome_coverage REAL NOT NULL DEFAULT 0,
    control_raw_avg_net_pct REAL,
    treatment_candidate_count INTEGER NOT NULL DEFAULT 0,
    treatment_settled_n INTEGER NOT NULL DEFAULT 0,
    treatment_outcome_coverage REAL NOT NULL DEFAULT 0,
    treatment_raw_avg_net_pct REAL,
    treatment_shrunk_avg_net_pct REAL,
    edge_delta_pct REAL,
    note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_filter_shadow_evaluations_experiment
    ON filter_shadow_evaluations(experiment_id, created_at DESC);
"""

_LEARNING_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS learning_hypotheses (
    hypothesis_id TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL UNIQUE,
    cohort_id TEXT NOT NULL,
    path_id TEXT NOT NULL,
    overlap_group TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_artifact_ids_json TEXT NOT NULL DEFAULT '[]',
    scope_json TEXT NOT NULL,
    action_json TEXT NOT NULL,
    consumer TEXT NOT NULL,
    target_horizon TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'proposed',
    version INTEGER NOT NULL DEFAULT 1,
    effect_threshold REAL NOT NULL DEFAULT 0,
    harm_budget_json TEXT NOT NULL DEFAULT '{}',
    evidence_summary_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_learning_hypotheses_path_status
    ON learning_hypotheses(path_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS learning_experiments (
    experiment_id TEXT PRIMARY KEY,
    hypothesis_id TEXT NOT NULL REFERENCES learning_hypotheses(hypothesis_id),
    stage TEXT NOT NULL,
    assignment_salt TEXT NOT NULL,
    allocation_pct REAL NOT NULL,
    min_samples_per_arm INTEGER NOT NULL,
    min_market_days INTEGER NOT NULL,
    primary_metric TEXT NOT NULL,
    promotion_gate_json TEXT NOT NULL DEFAULT '{}',
    harm_gate_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'registered',
    parent_experiment_id TEXT REFERENCES learning_experiments(experiment_id),
    activation_verdict_id TEXT REFERENCES learning_contribution_verdicts(verdict_id),
    stage_generation INTEGER NOT NULL DEFAULT 0,
    started_at TEXT,
    ended_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_learning_experiments_hypothesis
    ON learning_experiments(hypothesis_id, status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_learning_open_experiment
    ON learning_experiments(hypothesis_id) WHERE ended_at IS NULL;

CREATE TABLE IF NOT EXISTS learning_exposures (
    exposure_id TEXT PRIMARY KEY,
    dedup_key TEXT NOT NULL UNIQUE,
    experiment_id TEXT NOT NULL REFERENCES learning_experiments(experiment_id),
    hypothesis_id TEXT NOT NULL REFERENCES learning_hypotheses(hypothesis_id),
    assignment TEXT NOT NULL,
    consumer TEXT NOT NULL,
    consumer_stage TEXT NOT NULL,
    path_id TEXT NOT NULL,
    overlap_group TEXT NOT NULL,
    assignment_unit_key TEXT,
    candidate_key TEXT,
    opportunity_id TEXT,
    proposal_id TEXT,
    decision_id TEXT,
    observer_run_id TEXT,
    position_id TEXT,
    before_json TEXT NOT NULL DEFAULT '{}',
    after_json TEXT NOT NULL DEFAULT '{}',
    manifest_json TEXT NOT NULL DEFAULT '{}',
    outcome_candidate_key TEXT,
    outcome_source TEXT,
    outcome_contract_version TEXT NOT NULL,
    experiment_stage TEXT,
    code_commit TEXT,
    behavior_contract_hash TEXT,
    policy_version TEXT,
    concurrent_policy_epoch TEXT NOT NULL,
    exposed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_learning_exposures_experiment
    ON learning_exposures(experiment_id, assignment, exposed_at);
CREATE INDEX IF NOT EXISTS idx_learning_exposures_entity
    ON learning_exposures(candidate_key, opportunity_id, decision_id);

CREATE TABLE IF NOT EXISTS learning_contribution_verdicts (
    verdict_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL REFERENCES learning_experiments(experiment_id),
    hypothesis_id TEXT NOT NULL REFERENCES learning_hypotheses(hypothesis_id),
    stage TEXT,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    unique_treatment_n INTEGER NOT NULL,
    unique_control_n INTEGER NOT NULL,
    baseline REAL,
    observed REAL,
    delta REAL,
    confidence_low REAL,
    confidence_high REAL,
    harm_metrics_json TEXT NOT NULL DEFAULT '{}',
    verdict TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_learning_verdicts_experiment
    ON learning_contribution_verdicts(experiment_id, created_at DESC);

CREATE TABLE IF NOT EXISTS learning_lifecycle_events (
    event_id TEXT PRIMARY KEY,
    dedup_key TEXT NOT NULL UNIQUE,
    hypothesis_id TEXT NOT NULL REFERENCES learning_hypotheses(hypothesis_id),
    experiment_id TEXT NOT NULL REFERENCES learning_experiments(experiment_id),
    successor_experiment_id TEXT,
    verdict_id TEXT REFERENCES learning_contribution_verdicts(verdict_id),
    from_stage TEXT NOT NULL,
    to_stage TEXT,
    action TEXT NOT NULL,
    reason TEXT NOT NULL,
    policy_epoch TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_learning_lifecycle_experiment
    ON learning_lifecycle_events(experiment_id, created_at DESC);

CREATE TABLE IF NOT EXISTS learning_job_health (
    job_id TEXT PRIMARY KEY,
    schedule_key TEXT NOT NULL,
    last_expected_at TEXT,
    last_started_at TEXT,
    last_succeeded_at TEXT,
    next_due_at TEXT,
    input_watermark TEXT,
    backlog_count INTEGER NOT NULL DEFAULT 0,
    oldest_backlog_at TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    last_error TEXT,
    last_failed_at TEXT,
    last_failure_reason TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS learning_policy_epochs (
    epoch_id TEXT PRIMARY KEY,
    epoch_kind TEXT NOT NULL,
    effective_values_hash TEXT NOT NULL,
    effective_values_json TEXT NOT NULL,
    affected_path_ids_json TEXT NOT NULL,
    source_event_id TEXT,
    safety_required INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(epoch_kind, effective_values_hash, started_at)
);
"""



def _ensure_knowledge_fts_outcome_column(db) -> None:
    """Recreate knowledge_fts with outcome column if missing.

    FTS5 does not support ALTER TABLE ADD COLUMN on external-content-less
    tables, so we DROP + CREATE IF NOT EXISTS. The next reindex_all() call
    rebuilds the index from knowledge_items.
    """
    try:
        cols = [row[1] for row in db.execute("PRAGMA table_info(knowledge_fts)").fetchall()]
        if "outcome" not in cols:
            db.execute("DROP TABLE IF EXISTS knowledge_fts")
    except Exception:
        pass
    # _ensure_knowledge_fts will recreate with outcome column


def _backfill_entry_risk_reservations(db) -> None:
    """Carry only provable pre-Order authority across a schema upgrade.

    Historical/partial Risk rows are deliberately ignored. A reservation is
    created only when the stored Proposal hash, evaluated quantity, native
    execution identity and approved authoritative row agree exactly and no
    durable Order has taken over the allowance.
    """
    # Upgrade reservations that already existed in the immediately preceding
    # schema. Only fill the new fields when the row still matches the exact
    # Proposal and approved authority that created it.
    db.execute(
        """
        UPDATE entry_risk_reservations
        SET market_type = (
                SELECT tp.market_type FROM trade_proposals tp
                WHERE tp.id = entry_risk_reservations.proposal_id
            ),
            leverage = (
                SELECT COALESCE(tp.leverage, 1) FROM trade_proposals tp
                WHERE tp.id = entry_risk_reservations.proposal_id
            )
        WHERE (market_type IS NULL OR leverage IS NULL)
          AND EXISTS (
              SELECT 1
              FROM trade_proposals tp
              JOIN risk_decisions rd
                ON rd.proposal_id = tp.id
               AND rd.decision_role = 'authoritative'
               AND rd.approved = 1
               AND rd.proposal_commit_hash = tp.commit_hash
               AND rd.evaluated_quantity = entry_risk_reservations.quantity
               AND rd.allowed_execution_mode = tp.execution_mode
               AND rd.proposal_execution_mode = tp.execution_mode
              WHERE tp.id = entry_risk_reservations.proposal_id
                AND entry_risk_reservations.proposal_commit_hash = tp.commit_hash
                AND entry_risk_reservations.symbol = tp.symbol
                AND entry_risk_reservations.execution_mode = tp.execution_mode
                AND entry_risk_reservations.price = tp.price
                AND tp.market_type IN ('spot', 'usdt_futures')
                AND COALESCE(tp.leverage, 1) > 0
          )
        """
    )
    db.execute(
        """
        INSERT OR IGNORE INTO entry_risk_reservations (
            proposal_id,
            proposal_commit_hash,
            symbol,
            market_type,
            execution_mode,
            quantity,
            price,
            leverage,
            created_at
        )
        SELECT
            tp.id,
            tp.commit_hash,
            tp.symbol,
            tp.market_type,
            tp.execution_mode,
            rd.evaluated_quantity,
            tp.price,
            COALESCE(tp.leverage, 1),
            MIN(rd.created_at)
        FROM trade_proposals tp
        JOIN risk_decisions rd
          ON rd.proposal_id = tp.id
         AND rd.decision_role = 'authoritative'
         AND rd.approved = 1
         AND rd.proposal_commit_hash = tp.commit_hash
         AND rd.allowed_execution_mode = tp.execution_mode
         AND rd.proposal_execution_mode = tp.execution_mode
        WHERE tp.source = 'native'
          AND tp.status NOT IN ('rejected', 'error', 'executed')
          AND trim(COALESCE(tp.commit_hash, '')) <> ''
          AND trim(COALESCE(tp.symbol, '')) <> ''
          AND tp.market_type IN ('spot', 'usdt_futures')
          AND tp.execution_mode IN (
              'paper_internal',
              'bitget_demo',
              'bitget_live_limited',
              'binance_testnet',
              'binance_live_limited',
              'okx_demo',
              'okx_live_limited'
          )
          AND rd.evaluated_quantity > 0
          AND tp.price > 0
          AND COALESCE(tp.leverage, 1) > 0
          AND NOT EXISTS (
              SELECT 1 FROM orders o WHERE o.proposal_id = tp.id
          )
        GROUP BY tp.id
        HAVING COUNT(*) = 1
        """
    )

def _clear_learning_verdict_runner_zombie(db) -> None:
    """Remove the stale 'learning_verdict_runner' health record (GAP-16).

    This job_id existed in an earlier deployment and is not registered in
    AUTONOMOUS_LEARNING_JOBS; its status='failed'/last_succeeded_at=NULL entry
    is misleading noise that never gets updated. Idempotent.
    """
    db.execute("DELETE FROM learning_job_health WHERE job_id = 'learning_verdict_runner'")


def _backfill_fco_allocation_selected(db) -> None:
    """Backfill allocation_selected = 0 for settled/failed filter candidate outcome rows.

    The filter-evidence path (record_filter_decision) creates rows via
    INSERT OR IGNORE with allocation_selected = NULL, then
    _register_experiment_candidate_outcomes tries to write the authoritative
    True/False — but the earlier INSERT OR IGNORE blocked any update.

    After the upsert fix in insert_filter_candidate_outcomes_batch this is
    prevented going forward. For existing settled/failed rows whose window has
    already elapsed and where allocation_selected is still NULL the only safe
    interpretation is 0 (not selected): had they been selected the universe
    service would have written True.
    """
    db.execute(
        "UPDATE filter_candidate_outcomes "
        "SET allocation_selected = 0 "
        "WHERE allocation_selected IS NULL "
        "AND status IN ('settled', 'failed')"
    )


def _run_all_ddl(db) -> None:
    """Execute every DDL statement against db (used for both real and blueprint DBs)."""
    db.executescript(SCHEMA_SQL)
    db.executescript(_DATA_COLLECTORS_DDL)
    db.executescript(_PREDICTION_OBSERVER_RUNS_DDL)
    db.executescript(_PREDICTION_NORMALIZED_DDL)
    db.executescript(_STRATEGY_BACKTEST_DDL)
    db.executescript(_MARKET_REGIME_DDL)
    db.executescript(_PI_DECISIONS_DDL)
    db.executescript(_OPERATOR_EVALUATION_EVENTS_DDL)
    db.executescript(_CAPITAL_EVENTS_DDL)
    db.executescript(_PAPER_ACCOUNT_EVENTS_DDL)
    db.executescript(_POSITION_CLOSE_EVENTS_DDL)
    db.executescript(_VOLUME_RANKING_DDL)
    db.executescript(_OHLCV_BACKFILL_STATE_DDL)
    db.executescript(_COLLECTOR_HEALTH_STATE_DDL)
    db.executescript(_CONTROLLER_RECOMMENDATIONS_DDL)
    db.executescript(_FILTER_SHADOW_EXPERIMENTS_DDL)
    db.executescript(_LEARNING_LEDGER_DDL)
    _ensure_columns(db)
    # v2 authoritative identity includes the exact Risk Policy hash. The former
    # three-column unique index would incorrectly reuse/block a decision after a
    # policy revision, so remove it once the additive policy_hash column exists.
    db.execute("DROP INDEX IF EXISTS idx_risk_authoritative_snapshot")
    db.executescript(_LEARNING_ADDITIVE_INDEX_DDL)
    _repair_learning_experiment_activation(db)
    _repair_terminal_serenity_skips(db)
    db.executescript(_OPPORTUNITY_ROTATION_INDEX_DDL)
    _backfill_entry_risk_reservations(db)
    _ensure_outcome_integrity_schema(db)
    _ensure_market_ohlcv_instrument_identity(db)
    _ensure_orderbook_instrument_index(db)
    _ensure_filter_evidence_indexes(db)
    _backfill_forward_outcome_instrument_identity(db)
    _ensure_runtime_baseline(db)
    _ensure_knowledge_fts_outcome_column(db)
    _ensure_knowledge_fts(db)
    db.executescript(_CORE_INDEXES_SQL)
    _clear_learning_verdict_runner_zombie(db)
    _backfill_fco_allocation_selected(db)
    db.commit()


# MIGRATION_LEDGER_START
# Everything above this marker is the immutable implementation of the imported
# legacy baseline. Future schema changes must be new MigrationStep entries below;
# editing the baseline changes its checksum and fails CI/runtime verification.

# Recorded on Runtime Sessions so every process lifecycle can be attributed to the
# exact schema contract it verified.
SCHEMA_VERSION = "aletheia-sqlite-v36"
_LEGACY_BASELINE_SCHEMA_VERSION = "aletheia-sqlite-v2"

_MIGRATION_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS aletheia_schema_migrations (
    migration_id TEXT PRIMARY KEY,
    checksum TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    duration_ms REAL NOT NULL CHECK (duration_ms >= 0)
);
"""

_LEGACY_BASELINE_ID = "20260806_0001_legacy_schema_v2"
# Filled from the normalized source preceding MIGRATION_LEDGER_START. The value is
# deliberately stable: changing an applied migration is drift, not a reason to replay
# it. Add a new MigrationStep for every future schema or data upgrade.
_LEGACY_BASELINE_CHECKSUM = "b210b180fa4392a542a5275941ca15d1a443374b3e951055d8a4bbc12d32f8ee"
_MIGRATION_LOCK_TIMEOUT_SEC = 120.0

_STARTUP_HOT_PATH_INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_agent_runs_status_started
ON agent_runs(status, started_at);
CREATE INDEX IF NOT EXISTS idx_universe_scanner_runs_status_started
ON universe_scanner_runs(status, started_at);
"""
_OPTIONAL_HOT_PATH_INDEXES = (
    (
        "runtime_cost_events",
        "CREATE INDEX IF NOT EXISTS idx_runtime_cost_events_normalized_started "
        "ON runtime_cost_events(replace(started_at,' ','T'))",
    ),
    (
        "alpha_control_observations",
        "CREATE INDEX IF NOT EXISTS idx_alpha_control_layer_created "
        "ON alpha_control_observations(layer, created_at DESC)",
    ),
)
_STARTUP_DATASET_BOUNDARY_CHECKSUM = hashlib.sha256(
    (
        _STARTUP_HOT_PATH_INDEXES_SQL
        + "\n".join(sql for _, sql in _OPTIONAL_HOT_PATH_INDEXES)
        + "\nknowledge_fts_active_high_confidence_reindex_v1"
        + "\nPRAGMA optimize"
    ).encode("utf-8")
).hexdigest()

_CAPACITY_GUARDRAIL_INDEX_SQL = """
DROP INDEX IF EXISTS idx_filter_outcomes_candidate;
DROP INDEX IF EXISTS idx_filter_candidate_outcomes_key;
DROP INDEX IF EXISTS idx_market_ohlcv_instrument_tf_ts;
CREATE INDEX IF NOT EXISTS idx_audit_events_type_timestamp
ON audit_events(event_type, timestamp DESC);
"""
_CAPACITY_GUARDRAIL_CHECKSUM = hashlib.sha256(
    (_CAPACITY_GUARDRAIL_INDEX_SQL + "\nPRAGMA optimize").encode("utf-8")
).hexdigest()

_PENDING_OUTCOME_PARTIAL_INDEX_SQL = """
DROP INDEX IF EXISTS idx_filter_outcomes_status_due;
CREATE INDEX IF NOT EXISTS idx_filter_outcomes_pending_due
ON filter_candidate_outcomes(due_at, id) WHERE status='pending';
"""
_PENDING_OUTCOME_PARTIAL_INDEX_CHECKSUM = hashlib.sha256(
    (_PENDING_OUTCOME_PARTIAL_INDEX_SQL + "\nPRAGMA optimize").encode("utf-8")
).hexdigest()

_LLM_INFERENCE_LINKAGE_COLUMNS = (
    ("inference_call_id", "agent_shell_commands", "TEXT"),
    ("inference_call_id", "agent_tool_calls", "TEXT"),
)
_LLM_INFERENCE_LINKAGE_CHECKSUM = hashlib.sha256(
    (repr(_LLM_INFERENCE_LINKAGE_COLUMNS) + "\nadditive_columns_v1").encode("utf-8")
).hexdigest()

_AGENT_DURATION_COLUMNS = (
    ("duration_ms", "agent_tool_calls", "INTEGER"),
    ("duration_ms", "agent_shell_commands", "INTEGER"),
)
_AGENT_DURATION_CHECKSUM = hashlib.sha256(
    (repr(_AGENT_DURATION_COLUMNS) + "\nadditive_columns_v1").encode("utf-8")
).hexdigest()

_AGENT_USAGE_STORAGE_COLUMNS = (
    ("economics_tracing_enabled", "runtime_sessions", "INTEGER"),
)
_AGENT_RUN_ARTIFACTS_SQL = """
CREATE TABLE IF NOT EXISTS agent_run_artifacts (
    run_id TEXT NOT NULL REFERENCES agent_runs(id),
    artifact_type TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (run_id, artifact_type, artifact_id)
);
CREATE INDEX IF NOT EXISTS idx_agent_run_artifacts_artifact
ON agent_run_artifacts(artifact_type, artifact_id);
"""
_AGENT_USAGE_STORAGE_CHECKSUM = hashlib.sha256(
    (
        repr(_AGENT_USAGE_STORAGE_COLUMNS)
        + _AGENT_RUN_ARTIFACTS_SQL
        + "\nadditive_agent_usage_storage_v1"
    ).encode("utf-8")
).hexdigest()

_LESSON_REVIEWER_ATTEMPTS_SQL = """
CREATE TABLE IF NOT EXISTS learning_lesson_reviewer_attempts (
    attempt_id TEXT PRIMARY KEY,
    input_watermark TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    outcome TEXT NOT NULL CHECK (outcome IN ('running', 'succeeded', 'failed')),
    error_class TEXT,
    error_message TEXT,
    retry_of_attempt_id TEXT REFERENCES learning_lesson_reviewer_attempts(attempt_id)
);
CREATE INDEX IF NOT EXISTS idx_learning_lesson_reviewer_attempts_watermark
ON learning_lesson_reviewer_attempts(input_watermark, started_at DESC);
"""
_LESSON_REVIEWER_ATTEMPTS_CHECKSUM = hashlib.sha256(
    _LESSON_REVIEWER_ATTEMPTS_SQL.encode("utf-8")
).hexdigest()

_EVENT_JSONL_OUTBOX_COLUMNS = (
    ("jsonl_mirror_status", "ps_event_log", "TEXT NOT NULL DEFAULT 'pending'"),
    ("jsonl_attempt_count", "ps_event_log", "INTEGER NOT NULL DEFAULT 0"),
    ("jsonl_next_attempt_at", "ps_event_log", "TEXT"),
    ("jsonl_last_error_class", "ps_event_log", "TEXT"),
    ("jsonl_mirrored_at", "ps_event_log", "TEXT"),
)
_EVENT_JSONL_OUTBOX_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_ps_event_jsonl_outbox
ON ps_event_log(jsonl_mirror_status, jsonl_next_attempt_at, created_at);
"""
_EVENT_JSONL_OUTBOX_CHECKSUM = hashlib.sha256(
    (
        repr(_EVENT_JSONL_OUTBOX_COLUMNS)
        + _EVENT_JSONL_OUTBOX_INDEX_SQL
        + "\nadditive_event_jsonl_outbox_v1"
    ).encode("utf-8")
).hexdigest()

_EXIT_TRAILING_RECOVERY_COLUMNS = (
    ("recovery_observation_json", "position_protection_state", "TEXT"),
    ("recovery_observation_hash", "position_protection_state", "TEXT"),
)
_EXIT_TRAILING_RECOVERY_SQL = """
CREATE TRIGGER IF NOT EXISTS trg_position_recovery_observation_complete
BEFORE UPDATE OF recovery_observation_json, recovery_observation_hash
ON position_protection_state
WHEN (NEW.recovery_observation_json IS NULL) != (NEW.recovery_observation_hash IS NULL)
     OR (NEW.recovery_observation_json IS NOT NULL
         AND (json_valid(NEW.recovery_observation_json) = 0
              OR length(NEW.recovery_observation_hash) != 64
              OR NEW.recovery_observation_hash != lower(NEW.recovery_observation_hash)
              OR NEW.recovery_observation_hash GLOB '*[^0-9a-f]*'))
BEGIN
    SELECT RAISE(ABORT, 'Trailing recovery observation must be complete and valid');
END;

CREATE TRIGGER IF NOT EXISTS trg_position_recovery_observation_write_once
BEFORE UPDATE OF recovery_observation_json, recovery_observation_hash
ON position_protection_state
WHEN OLD.recovery_observation_json IS NOT NULL
     AND (NEW.recovery_observation_json IS NOT OLD.recovery_observation_json
          OR NEW.recovery_observation_hash IS NOT OLD.recovery_observation_hash)
BEGIN
    SELECT RAISE(ABORT, 'Trailing recovery observation is immutable');
END;
"""
_EXIT_TRAILING_RECOVERY_CHECKSUM = hashlib.sha256(
    (
        repr(_EXIT_TRAILING_RECOVERY_COLUMNS)
        + _EXIT_TRAILING_RECOVERY_SQL
        + "\nadditive_exit_trailing_recovery_v1"
    ).encode("utf-8")
).hexdigest()

_LEARNING_HYPOTHESIS_IDENTITY_COLUMNS = (
    ("behavior_contract_version", "learning_hypotheses", "TEXT"),
    ("fingerprint_schema_version", "learning_hypotheses", "INTEGER"),
)
_LEARNING_HYPOTHESIS_IDENTITY_CHECKSUM = hashlib.sha256(
    (
        repr(_LEARNING_HYPOTHESIS_IDENTITY_COLUMNS)
        + "\nadditive_learning_hypothesis_identity_v1"
    ).encode("utf-8")
).hexdigest()
_LEARNING_DREAMING_BACKLOG_COLUMNS = (
    ("input_cursor", "learning_job_health", "TEXT"),
)
_LEARNING_DREAMING_BACKLOG_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_knowledge_trade_case_created
    ON knowledge_items(item_type, status, created_at, id);
CREATE INDEX IF NOT EXISTS idx_ps_lessons_trade_case_source
    ON ps_lessons(source_type, source_id);
"""
_LEARNING_DREAMING_BACKLOG_CHECKSUM = hashlib.sha256(
    (
        repr(_LEARNING_DREAMING_BACKLOG_COLUMNS)
        + _LEARNING_DREAMING_BACKLOG_INDEX_SQL
        + "\ndurable_dreaming_backlog_v1"
    ).encode("utf-8")
).hexdigest()

_POSITION_REALTIME_REVISION_SQL = (
    """
    CREATE TABLE IF NOT EXISTS position_realtime_revision (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0)
    )
    """,
    (
        "INSERT OR IGNORE INTO position_realtime_revision(singleton, revision) "
        "VALUES (1, 0)"
    ),
    """
    CREATE TRIGGER IF NOT EXISTS trg_positions_realtime_revision_insert
    AFTER INSERT ON positions
    BEGIN
        UPDATE position_realtime_revision
        SET revision = revision + 1
        WHERE singleton = 1;
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_positions_realtime_revision_update
    AFTER UPDATE ON positions
    BEGIN
        UPDATE position_realtime_revision
        SET revision = revision + 1
        WHERE singleton = 1;
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_positions_realtime_revision_delete
    AFTER DELETE ON positions
    BEGIN
        UPDATE position_realtime_revision
        SET revision = revision + 1
        WHERE singleton = 1;
    END
    """,
)
_POSITION_REALTIME_REVISION_CHECKSUM = hashlib.sha256(
    (
        repr(_POSITION_REALTIME_REVISION_SQL) + "\nposition_realtime_revision_v1"
    ).encode("utf-8")
).hexdigest()


_UFC_CANONICAL_IDENTITY = (
    "universe_family_calibration",
    "universe-family-calibration/v11",
    1,
)
# Published by #922. Databases may already have this exact checksum in their
# migration ledger; v25's equivalent verifier repair must not rotate it.
_UFC_CANONICAL_IDENTITY_CHECKSUM = hashlib.sha256(
    (repr(_UFC_CANONICAL_IDENTITY) + "\nmaterialize_active_paper_identity_v1").encode(
        "utf-8"
    )
).hexdigest()

_POSITION_PATH_OBSERVATIONS_SQL = """
CREATE TABLE IF NOT EXISTS position_path_observations (
    position_id TEXT NOT NULL REFERENCES positions(id),
    bucket_open_ms INTEGER NOT NULL CHECK (
        bucket_open_ms >= 0 AND bucket_open_ms % 300000 = 0
    ),
    observed_at TEXT NOT NULL CHECK (julianday(observed_at) IS NOT NULL),
    venue TEXT,
    symbol TEXT NOT NULL,
    market_type TEXT,
    direction TEXT,
    entry_price REAL,
    quantity REAL,
    bar_open REAL CHECK (bar_open > 0),
    bar_high REAL CHECK (bar_high > 0),
    bar_low REAL CHECK (bar_low > 0),
    bar_close REAL CHECK (bar_close > 0),
    bar_volume REAL CHECK (bar_volume >= 0),
    bar_provider TEXT,
    mark_price REAL CHECK (mark_price > 0),
    unrealized_pnl REAL,
    entry_fee REAL,
    funding_cost REAL,
    funding_source TEXT NOT NULL,
    stop_price REAL,
    take_profit_price REAL,
    exit_plan_version INTEGER NOT NULL CHECK (exit_plan_version >= 0),
    evidence_status TEXT NOT NULL CHECK (
        evidence_status IN ('available', 'unavailable')
    ),
    evidence_reason TEXT NOT NULL,
    producer_commit TEXT,
    runtime_session_id TEXT,
    capture_mode TEXT NOT NULL CHECK (
        capture_mode IN ('open_snapshot', 'post_close_final_snapshot')
    ),
    PRIMARY KEY (position_id, bucket_open_ms),
    CHECK (
        (evidence_status = 'available'
         AND market_type IS NOT NULL
         AND market_type IN ('spot', 'perp')
         AND direction IS NOT NULL
         AND direction IN ('long', 'short')
         AND entry_price IS NOT NULL
         AND entry_price > 0
         AND quantity IS NOT NULL
         AND quantity > 0
         AND venue IS NOT NULL
         AND bar_open IS NOT NULL
         AND bar_high IS NOT NULL
         AND bar_low IS NOT NULL
         AND bar_close IS NOT NULL
         AND bar_volume IS NOT NULL
         AND bar_provider IS NOT NULL
         AND evidence_reason = 'complete_exact_5m_bar')
        OR
        (evidence_status = 'unavailable'
         AND bar_open IS NULL
         AND bar_high IS NULL
         AND bar_low IS NULL
         AND bar_close IS NULL
         AND bar_volume IS NULL
         AND bar_provider IS NULL
         AND evidence_reason != '')
    )
);
CREATE TRIGGER IF NOT EXISTS trg_position_path_observations_no_update
BEFORE UPDATE ON position_path_observations
BEGIN
    SELECT RAISE(ABORT, 'Position path observations are append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_position_path_observations_no_delete
BEFORE DELETE ON position_path_observations
BEGIN
    SELECT RAISE(ABORT, 'Position path observations are append-only');
END;
"""
_POSITION_PATH_OBSERVATIONS_CHECKSUM = hashlib.sha256(
    (
        _POSITION_PATH_OBSERVATIONS_SQL
        + "\nappend_only_position_path_observations_v1\nPRAGMA optimize"
    ).encode("utf-8")
).hexdigest()

_POSITION_PATH_DEFERRED_BUCKETS_SQL = """
CREATE TABLE IF NOT EXISTS position_path_observation_deferred_buckets (
    position_id TEXT NOT NULL REFERENCES positions(id),
    bucket_open_ms INTEGER NOT NULL CHECK (
        bucket_open_ms >= 0 AND bucket_open_ms % 300000 = 0
    ),
    first_deferred_at TEXT NOT NULL CHECK (julianday(first_deferred_at) IS NOT NULL),
    last_attempt_at TEXT NOT NULL CHECK (julianday(last_attempt_at) IS NOT NULL),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    reason TEXT NOT NULL,
    PRIMARY KEY (position_id, bucket_open_ms)
);
CREATE INDEX IF NOT EXISTS idx_position_path_deferred_bucket
ON position_path_observation_deferred_buckets(bucket_open_ms, position_id);
"""
_POSITION_PATH_DEFERRED_BUCKETS_CHECKSUM = hashlib.sha256(
    (
        _POSITION_PATH_DEFERRED_BUCKETS_SQL
        + "\ndurable_position_path_retry_v1\nPRAGMA optimize"
    ).encode("utf-8")
).hexdigest()

_POSITION_PATH_OBSERVER_CURSOR_SQL = """
CREATE TABLE IF NOT EXISTS position_path_observer_cursor (
    stream_id TEXT PRIMARY KEY CHECK(stream_id='position_path_observer'),
    last_enqueued_bucket_open_ms INTEGER NOT NULL CHECK(
        last_enqueued_bucket_open_ms >= 0
        AND last_enqueued_bucket_open_ms % 300000 = 0
    ),
    updated_at TEXT NOT NULL
);
"""
_POSITION_PATH_OBSERVER_CURSOR_CHECKSUM = hashlib.sha256(
    (
        _POSITION_PATH_OBSERVER_CURSOR_SQL
        + "\ndurable_position_path_observer_cursor_v1\nPRAGMA optimize"
    ).encode("utf-8")
).hexdigest()


def _ufc_v25_normalize(value):
    """Frozen schema-1 normalizer; never delegate an applied migration to live code."""

    import json

    if isinstance(value, dict):
        return {
            str(key).lower(): _ufc_v25_normalize(item)
            for key, item in sorted(value.items())
            if str(key).lower()
            not in {"summary", "title", "created_at", "updated_at"}
        }
    if isinstance(value, list):
        return sorted(
            (_ufc_v25_normalize(item) for item in value),
            key=lambda item: json.dumps(item, sort_keys=True, default=str),
        )
    if isinstance(value, str):
        return value.strip().lower()
    return value


def _ufc_v25_fingerprint(
    *, path_id, scope, action, target_horizon, source_cohort, behavior_version,
    schema_version,
) -> str:
    """Frozen UFC schema-1 fingerprint implementation for migration v25 only."""

    import json

    payload = {
        "path_id": path_id.lower(),
        "scope": _ufc_v25_normalize(scope),
        "action": _ufc_v25_normalize(action),
        "target_horizon": target_horizon.lower(),
        "source_cohort": source_cohort.lower(),
        "schema_version": schema_version,
        "behavior_contract_version": behavior_version.lower(),
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

_MARKET_SIGNAL_IDENTITY_COLUMNS = (
    ("last_market_signal_evidence", "positions", "TEXT"),
)
_MARKET_SIGNAL_IDENTITY_CHECKSUM = hashlib.sha256(
    (repr(_MARKET_SIGNAL_IDENTITY_COLUMNS) + "\nadditive_columns_v1").encode("utf-8")
).hexdigest()

_UNIVERSE_TWIN_SHADOW_EVIDENCE_SQL = """
CREATE TABLE IF NOT EXISTS universe_twin_shadow_batches (
    scanner_run_id TEXT PRIMARY KEY,
    authority_snapshot_id TEXT,
    record_version TEXT NOT NULL,
    authority_behavior_version TEXT NOT NULL,
    shadow_behavior_version TEXT NOT NULL,
    allocation_replay_contract TEXT NOT NULL,
    policy_epoch_id TEXT,
    feature_source_fingerprint TEXT NOT NULL,
    availability_profile_id TEXT,
    metric_version TEXT NOT NULL,
    allocator_random_state_json TEXT NOT NULL,
    policy_json TEXT NOT NULL,
    allocation_context_json TEXT NOT NULL,
    candidate_count INTEGER NOT NULL CHECK (candidate_count >= 0),
    promotion_eligible INTEGER NOT NULL DEFAULT 0 CHECK (promotion_eligible IN (0, 1)),
    blocking_reasons_json TEXT NOT NULL DEFAULT '[]',
    batch_hash TEXT NOT NULL,
    captured_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS universe_twin_shadow_records (
    id TEXT PRIMARY KEY,
    scanner_run_id TEXT NOT NULL,
    candidate_key TEXT NOT NULL,
    candidate_index INTEGER NOT NULL CHECK (candidate_index >= 0),
    feature_snapshot_id TEXT,
    asset_id TEXT,
    symbol TEXT NOT NULL,
    market_type TEXT NOT NULL,
    authority_total_score REAL NOT NULL,
    shadow_point_score REAL,
    shadow_pessimistic_score REAL,
    authority_rank INTEGER,
    shadow_point_rank INTEGER,
    shadow_pessimistic_rank INTEGER,
    fine_filter_passed INTEGER CHECK (fine_filter_passed IN (0, 1)),
    authority_selected INTEGER CHECK (authority_selected IN (0, 1)),
    shadow_point_selected INTEGER CHECK (shadow_point_selected IN (0, 1)),
    shadow_pessimistic_selected INTEGER CHECK (shadow_pessimistic_selected IN (0, 1)),
    shadow_authority_applied INTEGER NOT NULL DEFAULT 0 CHECK (shadow_authority_applied IN (0, 1)),
    promotion_eligible INTEGER NOT NULL DEFAULT 0 CHECK (promotion_eligible IN (0, 1)),
    shadow_valid INTEGER NOT NULL DEFAULT 0 CHECK (shadow_valid IN (0, 1)),
    compact_output_json TEXT NOT NULL,
    input_json TEXT NOT NULL,
    record_hash TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    UNIQUE(scanner_run_id, candidate_key),
    UNIQUE(scanner_run_id, candidate_index)
);

CREATE INDEX IF NOT EXISTS idx_universe_twin_shadow_batches_captured
ON universe_twin_shadow_batches(captured_at);
CREATE INDEX IF NOT EXISTS idx_universe_twin_shadow_records_run
ON universe_twin_shadow_records(scanner_run_id);
"""
_UNIVERSE_TWIN_SHADOW_EVIDENCE_CHECKSUM = hashlib.sha256(
    (_UNIVERSE_TWIN_SHADOW_EVIDENCE_SQL + "\nPRAGMA optimize").encode("utf-8")
).hexdigest()

_POSITION_PROTECTION_COLUMNS = (("exit_plan_effective_at", "positions", "TEXT"),)
_POSITION_PROTECTION_SQL = """
CREATE TABLE IF NOT EXISTS position_exit_plan_versions (
    position_id TEXT NOT NULL REFERENCES positions(id),
    version INTEGER NOT NULL CHECK (version >= 1),
    effective_at TEXT NOT NULL CHECK (julianday(effective_at) IS NOT NULL),
    stop_price REAL CHECK (stop_price IS NULL OR stop_price > 0),
    take_profit_price REAL CHECK (
        take_profit_price IS NULL OR take_profit_price > 0
    ),
    exit_limit_price REAL CHECK (exit_limit_price IS NULL OR exit_limit_price > 0),
    exit_limit_source TEXT CHECK (
        exit_limit_source IS NULL
        OR exit_limit_source IN ('human', 'system_operator')
    ),
    plan_json TEXT NOT NULL CHECK (json_valid(plan_json)),
    plan_hash TEXT NOT NULL CHECK (
        length(plan_hash) = 64
        AND plan_hash = lower(plan_hash)
        AND plan_hash NOT GLOB '*[^0-9a-f]*'
    ),
    provenance TEXT NOT NULL CHECK (length(trim(provenance)) > 0),
    created_at TEXT NOT NULL CHECK (julianday(created_at) IS NOT NULL),
    PRIMARY KEY(position_id, version),
    UNIQUE(position_id, version, effective_at, plan_hash),
    CHECK (
        (exit_limit_price IS NULL AND exit_limit_source IS NULL)
        OR (exit_limit_price IS NOT NULL AND exit_limit_source IS NOT NULL)
    ),
    CHECK (
        stop_price IS NOT NULL
        OR take_profit_price IS NOT NULL
        OR exit_limit_price IS NOT NULL
    )
);

CREATE INDEX IF NOT EXISTS idx_position_exit_plan_versions_effective
ON position_exit_plan_versions(position_id, effective_at);

CREATE TABLE IF NOT EXISTS position_protection_state (
    position_id TEXT PRIMARY KEY REFERENCES positions(id),
    venue TEXT NOT NULL CHECK (length(trim(venue)) > 0),
    symbol TEXT NOT NULL CHECK (length(trim(symbol)) > 0),
    market_type TEXT NOT NULL CHECK (market_type IN ('spot', 'perp')),
    timeframe TEXT NOT NULL DEFAULT '1m' CHECK (timeframe = '1m'),
    activated_at TEXT NOT NULL CHECK (julianday(activated_at) IS NOT NULL),
    next_bar_open_ms INTEGER NOT NULL CHECK (next_bar_open_ms >= 0),
    last_evaluated_bar_open_ms INTEGER CHECK (
        last_evaluated_bar_open_ms IS NULL OR last_evaluated_bar_open_ms >= 0
    ),
    last_inspected_bar_open_ms INTEGER CHECK (
        last_inspected_bar_open_ms IS NULL OR last_inspected_bar_open_ms >= 0
    ),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN (
        'active', 'gap', 'triggered', 'materialized', 'superseded',
        'integrity_blocked'
    )),
    gap_reason TEXT,
    gap_detected_at TEXT CHECK (
        gap_detected_at IS NULL OR julianday(gap_detected_at) IS NOT NULL
    ),
    gap_count INTEGER NOT NULL DEFAULT 0 CHECK (gap_count >= 0),
    recent_error TEXT,
    trigger_id TEXT UNIQUE,
    close_order_id TEXT REFERENCES orders(id),
    close_fill_id TEXT REFERENCES fills(id),
    close_fill_price REAL,
    close_filled_at TEXT CHECK (
        close_filled_at IS NULL OR julianday(close_filled_at) IS NOT NULL
    ),
    final_state TEXT CHECK (
        final_state IS NULL OR final_state IN ('materialized', 'integrity_blocked')
    ),
    created_at TEXT NOT NULL CHECK (julianday(created_at) IS NOT NULL),
    updated_at TEXT NOT NULL CHECK (julianday(updated_at) IS NOT NULL),
    FOREIGN KEY (
        position_id,
        trigger_id
    ) REFERENCES position_protection_triggers(
        position_id,
        trigger_id
    ),
    CHECK (
        last_evaluated_bar_open_ms IS NULL
        OR (
            last_inspected_bar_open_ms IS NOT NULL
            AND last_evaluated_bar_open_ms <= last_inspected_bar_open_ms
        )
    ),
    CHECK (
        last_inspected_bar_open_ms IS NULL
        OR last_inspected_bar_open_ms <= next_bar_open_ms
    ),
    CHECK (
        status != 'gap'
        OR (
            length(trim(gap_reason)) > 0
            AND gap_detected_at IS NOT NULL
            AND gap_count >= 1
        )
    ),
    CHECK (
        (trigger_id IS NULL AND status NOT IN ('triggered', 'materialized'))
        OR trigger_id IS NOT NULL
    ),
    CHECK (
        close_order_id IS NULL
        OR (
            trigger_id IS NOT NULL
            AND status IN ('triggered', 'materialized', 'integrity_blocked')
        )
    ),
    CHECK (
        (
            close_fill_id IS NULL
            AND close_fill_price IS NULL
            AND close_filled_at IS NULL
            AND final_state IS NULL
        )
        OR (
            close_fill_id IS NOT NULL
            AND close_order_id IS NOT NULL
            AND close_fill_price > 0
            AND close_filled_at IS NOT NULL
            AND final_state IS NOT NULL
        )
    ),
    CHECK (
        status != 'materialized'
        OR (
            trigger_id IS NOT NULL
            AND close_order_id IS NOT NULL
            AND close_fill_id IS NOT NULL
            AND final_state = 'materialized'
        )
    )
);

CREATE INDEX IF NOT EXISTS idx_position_protection_state_due
ON position_protection_state(status, next_bar_open_ms);

CREATE TABLE IF NOT EXISTS position_protection_triggers (
    trigger_id TEXT PRIMARY KEY CHECK (
        length(trigger_id) = 36
        AND length(replace(trigger_id, '-', '')) = 32
        AND substr(trigger_id, 9, 1) = '-'
        AND substr(trigger_id, 14, 1) = '-'
        AND substr(trigger_id, 19, 1) = '-'
        AND substr(trigger_id, 24, 1) = '-'
        AND lower(trigger_id) = trigger_id
        AND replace(trigger_id, '-', '') NOT GLOB '*[^0-9a-f]*'
    ),
    position_id TEXT NOT NULL UNIQUE REFERENCES position_protection_state(position_id),
    detected_at TEXT NOT NULL CHECK (julianday(detected_at) IS NOT NULL),
    venue TEXT NOT NULL CHECK (length(trim(venue)) > 0),
    symbol TEXT NOT NULL CHECK (length(trim(symbol)) > 0),
    market_type TEXT NOT NULL CHECK (market_type IN ('spot', 'perp')),
    timeframe TEXT NOT NULL CHECK (timeframe = '1m'),
    bar_open_ms INTEGER NOT NULL CHECK (bar_open_ms >= 0),
    bar_close_ms INTEGER NOT NULL CHECK (bar_close_ms = bar_open_ms + 60000),
    bar_open REAL NOT NULL CHECK (bar_open > 0),
    bar_high REAL NOT NULL CHECK (bar_high >= bar_open),
    bar_low REAL NOT NULL CHECK (bar_low > 0 AND bar_low <= bar_open),
    bar_close REAL NOT NULL CHECK (bar_close > 0),
    bar_volume REAL NOT NULL CHECK (bar_volume >= 0),
    provider TEXT NOT NULL CHECK (length(trim(provider)) > 0),
    bar_hash TEXT NOT NULL CHECK (
        length(bar_hash) = 64
        AND bar_hash = lower(bar_hash)
        AND bar_hash NOT GLOB '*[^0-9a-f]*'
    ),
    plan_version INTEGER NOT NULL CHECK (plan_version >= 1),
    plan_effective_at TEXT NOT NULL CHECK (julianday(plan_effective_at) IS NOT NULL),
    plan_hash TEXT NOT NULL CHECK (
        length(plan_hash) = 64
        AND plan_hash = lower(plan_hash)
        AND plan_hash NOT GLOB '*[^0-9a-f]*'
    ),
    stop_price REAL CHECK (stop_price IS NULL OR stop_price > 0),
    upside_price REAL CHECK (upside_price IS NULL OR upside_price > 0),
    stop_hit INTEGER NOT NULL CHECK (stop_hit IN (0, 1)),
    target_hit INTEGER NOT NULL CHECK (target_hit IN (0, 1)),
    reason TEXT NOT NULL CHECK (reason IN ('stop_loss', 'take_profit', 'exit_limit')),
    collision_policy TEXT NOT NULL CHECK (collision_policy = 'stop_first_v1'),
    UNIQUE(position_id, trigger_id),
    FOREIGN KEY (
        position_id,
        plan_version,
        plan_effective_at,
        plan_hash
    ) REFERENCES position_exit_plan_versions(
        position_id,
        version,
        effective_at,
        plan_hash
    ),
    CHECK (bar_high >= bar_close AND bar_low <= bar_close),
    CHECK (stop_price IS NOT NULL OR upside_price IS NOT NULL),
    CHECK (stop_hit + target_hit >= 1),
    CHECK (stop_hit = 0 OR stop_price IS NOT NULL),
    CHECK (target_hit = 0 OR upside_price IS NOT NULL),
    CHECK (stop_hit = 0 OR reason = 'stop_loss')
);

CREATE TRIGGER IF NOT EXISTS trg_positions_exit_plan_projection_insert_guard
BEFORE INSERT ON positions
WHEN NEW.exit_plan_effective_at IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Position must start without an ExitPlan projection');
END;

CREATE TRIGGER IF NOT EXISTS trg_position_exit_plan_versions_contiguous
BEFORE INSERT ON position_exit_plan_versions
BEGIN
    SELECT CASE
        WHEN (SELECT status FROM positions WHERE id = NEW.position_id) != 'open'
        THEN RAISE(ABORT, 'ExitPlan requires an open Position')
    END;
    SELECT CASE
        WHEN NEW.version != COALESCE(
            (SELECT MAX(version) + 1
             FROM position_exit_plan_versions
             WHERE position_id = NEW.position_id),
            1
        )
        THEN RAISE(ABORT, 'exit plan versions must be contiguous')
    END;
    SELECT CASE
        WHEN NEW.version = 1
         AND (SELECT exit_plan_effective_at FROM positions WHERE id = NEW.position_id)
             IS NOT NULL
        THEN RAISE(ABORT, 'exit plan projection conflicts with first version')
    END;
    SELECT CASE
        WHEN NEW.version > 1
         AND (SELECT exit_plan_effective_at FROM positions WHERE id = NEW.position_id)
             IS NOT (
                 SELECT effective_at
                 FROM position_exit_plan_versions
                 WHERE position_id = NEW.position_id
                 ORDER BY version DESC
                 LIMIT 1
             )
        THEN RAISE(ABORT, 'exit plan projection is stale')
    END;
    SELECT CASE
        WHEN NEW.version > 1
         AND julianday(NEW.effective_at) <= julianday((
             SELECT effective_at
             FROM position_exit_plan_versions
             WHERE position_id = NEW.position_id
             ORDER BY version DESC
             LIMIT 1
         ))
        THEN RAISE(ABORT, 'exit plan effective_at must advance')
    END;
END;

CREATE TRIGGER IF NOT EXISTS trg_position_exit_plan_versions_project
AFTER INSERT ON position_exit_plan_versions
BEGIN
    UPDATE positions
    SET exit_plan_effective_at = NEW.effective_at
    WHERE id = NEW.position_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_positions_exit_plan_projection_guard
BEFORE UPDATE OF exit_plan_effective_at ON positions
WHEN NEW.exit_plan_effective_at IS NOT OLD.exit_plan_effective_at
BEGIN
    SELECT CASE
        WHEN NEW.exit_plan_effective_at IS NULL
        THEN RAISE(ABORT, 'exit plan projection cannot be cleared')
    END;
    SELECT CASE
        WHEN NOT EXISTS (
            SELECT 1
            FROM position_exit_plan_versions
            WHERE position_id = NEW.id
              AND effective_at = NEW.exit_plan_effective_at
        )
        THEN RAISE(ABORT, 'exit plan projection requires an immutable version')
    END;
    SELECT CASE
        WHEN OLD.exit_plan_effective_at IS NOT NULL
         AND julianday(NEW.exit_plan_effective_at) < julianday(OLD.exit_plan_effective_at)
        THEN RAISE(ABORT, 'exit plan projection cannot regress')
    END;
END;

CREATE TRIGGER IF NOT EXISTS trg_position_exit_plan_versions_no_update
BEFORE UPDATE ON position_exit_plan_versions
BEGIN
    SELECT RAISE(ABORT, 'position exit plan version is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_position_exit_plan_versions_no_delete
BEFORE DELETE ON position_exit_plan_versions
BEGIN
    SELECT RAISE(ABORT, 'position exit plan versions are retained');
END;

CREATE TRIGGER IF NOT EXISTS trg_position_protection_state_no_delete
BEFORE DELETE ON position_protection_state
BEGIN
    SELECT RAISE(ABORT, 'position protection state is retained');
END;

CREATE TRIGGER IF NOT EXISTS trg_position_protection_triggers_no_update
BEFORE UPDATE ON position_protection_triggers
BEGIN
    SELECT RAISE(ABORT, 'position protection trigger evidence is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_position_protection_triggers_no_delete
BEFORE DELETE ON position_protection_triggers
BEGIN
    SELECT RAISE(ABORT, 'position protection trigger evidence is retained');
END;

CREATE TRIGGER IF NOT EXISTS trg_position_protection_state_identity_immutable
BEFORE UPDATE OF venue, symbol, market_type, timeframe, activated_at, created_at
ON position_protection_state
BEGIN
    SELECT RAISE(ABORT, 'position protection identity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_position_protection_state_cursor_monotonic
BEFORE UPDATE OF next_bar_open_ms, last_evaluated_bar_open_ms,
                 last_inspected_bar_open_ms
ON position_protection_state
WHEN NEW.next_bar_open_ms < OLD.next_bar_open_ms
  OR (
      OLD.last_evaluated_bar_open_ms IS NOT NULL
      AND (
          NEW.last_evaluated_bar_open_ms IS NULL
          OR NEW.last_evaluated_bar_open_ms < OLD.last_evaluated_bar_open_ms
      )
  )
  OR (
      OLD.last_inspected_bar_open_ms IS NOT NULL
      AND (
          NEW.last_inspected_bar_open_ms IS NULL
          OR NEW.last_inspected_bar_open_ms < OLD.last_inspected_bar_open_ms
      )
  )
BEGIN
    SELECT RAISE(ABORT, 'position protection cursor cannot regress');
END;

CREATE TRIGGER IF NOT EXISTS trg_position_protection_state_gap_count_monotonic
BEFORE UPDATE OF gap_count ON position_protection_state
WHEN NEW.gap_count < OLD.gap_count
BEGIN
    SELECT RAISE(ABORT, 'position protection gap count cannot regress');
END;

CREATE TRIGGER IF NOT EXISTS trg_position_protection_state_trigger_write_once
BEFORE UPDATE OF trigger_id
ON position_protection_state
WHEN OLD.trigger_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'position protection trigger evidence is write-once');
END;

CREATE TRIGGER IF NOT EXISTS trg_position_protection_triggers_plan_exact
BEFORE INSERT ON position_protection_triggers
WHEN NOT EXISTS (
        SELECT 1
        FROM position_exit_plan_versions
        WHERE position_id = NEW.position_id
          AND version = NEW.plan_version
          AND effective_at = NEW.plan_effective_at
          AND plan_hash = NEW.plan_hash
          AND stop_price IS NEW.stop_price
          AND COALESCE(exit_limit_price, take_profit_price) IS NEW.upside_price
          AND julianday(effective_at) <= julianday(
              NEW.bar_open_ms / 1000.0,
              'unixepoch'
          )
    )
 OR EXISTS (
        SELECT 1
        FROM position_exit_plan_versions AS later
        WHERE later.position_id = NEW.position_id
          AND later.version > NEW.plan_version
          AND julianday(later.effective_at) < julianday(
              NEW.bar_close_ms / 1000.0,
              'unixepoch'
          )
    )
BEGIN
    SELECT RAISE(ABORT, 'position protection trigger ExitPlan is inconsistent');
END;

CREATE TRIGGER IF NOT EXISTS trg_position_protection_triggers_project
AFTER INSERT ON position_protection_triggers
BEGIN
    UPDATE position_protection_state
    SET trigger_id = NEW.trigger_id,
        last_inspected_bar_open_ms = NEW.bar_open_ms,
        last_evaluated_bar_open_ms = NEW.bar_open_ms,
        next_bar_open_ms = NEW.bar_close_ms,
        status = 'triggered',
        updated_at = NEW.detected_at
    WHERE position_id = NEW.position_id
      AND trigger_id IS NULL
      AND status IN ('active', 'gap')
      AND venue = NEW.venue
      AND symbol = NEW.symbol
      AND market_type = NEW.market_type
      AND timeframe = NEW.timeframe
      AND next_bar_open_ms = NEW.bar_open_ms;
    SELECT CASE
        WHEN changes() != 1
        THEN RAISE(ABORT, 'position protection first-trigger CAS failed')
    END;
END;

CREATE TRIGGER IF NOT EXISTS trg_position_protection_state_close_order_write_once
BEFORE UPDATE OF close_order_id ON position_protection_state
WHEN OLD.close_order_id IS NOT NULL
 AND NEW.close_order_id IS NOT OLD.close_order_id
BEGIN
    SELECT RAISE(ABORT, 'position protection close order is write-once');
END;

CREATE TRIGGER IF NOT EXISTS trg_position_protection_state_close_fill_write_once
BEFORE UPDATE OF close_fill_id, close_fill_price, close_filled_at, final_state
ON position_protection_state
WHEN OLD.close_fill_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'position protection close fill evidence is write-once');
END;

CREATE TRIGGER IF NOT EXISTS trg_position_protection_state_terminal_status
BEFORE UPDATE OF status ON position_protection_state
WHEN (OLD.status IN ('materialized', 'superseded') AND NEW.status != OLD.status)
  OR (OLD.trigger_id IS NOT NULL AND NEW.status IN ('active', 'gap', 'superseded'))
BEGIN
    SELECT RAISE(ABORT, 'position protection terminal status cannot regress');
END;
"""
_POSITION_PROTECTION_CHECKSUM = hashlib.sha256(
    (
        repr(_POSITION_PROTECTION_COLUMNS)
        + "\n"
        + _POSITION_PROTECTION_SQL
        + "\nposition_protection_storage_v1\nPRAGMA optimize"
    ).encode("utf-8")
).hexdigest()

_LEARNING_FIXED_LOOK_COLUMNS = (
    ("inference_contract_version", "learning_contribution_verdicts", "TEXT"),
    (
        "inference_role",
        "learning_contribution_verdicts",
        "TEXT NOT NULL DEFAULT 'monitoring' CHECK (inference_role IN ('monitoring', 'confirmatory'))",
    ),
    (
        "authority_eligible",
        "learning_contribution_verdicts",
        "INTEGER NOT NULL DEFAULT 0 CHECK (authority_eligible IN (0, 1))",
    ),
    ("inference_plan_hash", "learning_contribution_verdicts", "TEXT"),
    ("inference_cutoff_fingerprint", "learning_contribution_verdicts", "TEXT"),
)
_LEARNING_FIXED_LOOK_SQL = """
CREATE TABLE IF NOT EXISTS learning_inference_decisions (
    experiment_id TEXT PRIMARY KEY REFERENCES learning_experiments(experiment_id),
    inference_family_id TEXT NOT NULL CHECK (length(trim(inference_family_id)) > 0),
    inference_contract_version TEXT NOT NULL CHECK (
        inference_contract_version = 'ufc-fixed-look/v1'
    ),
    plan_json TEXT NOT NULL CHECK (json_valid(plan_json)),
    plan_hash TEXT NOT NULL CHECK (
        length(plan_hash) = 64
        AND plan_hash = lower(plan_hash)
        AND plan_hash NOT GLOB '*[^0-9a-f]*'
    ),
    status TEXT NOT NULL CHECK (
        status IN ('collecting', 'cutoff_frozen', 'consumed', 'invalidated')
    ),
    cutoff_json TEXT CHECK (cutoff_json IS NULL OR json_valid(cutoff_json)),
    cutoff_fingerprint TEXT CHECK (
        cutoff_fingerprint IS NULL
        OR (
            length(cutoff_fingerprint) = 64
            AND cutoff_fingerprint = lower(cutoff_fingerprint)
            AND cutoff_fingerprint NOT GLOB '*[^0-9a-f]*'
        )
    ),
    cutoff_frozen_at TEXT CHECK (
        cutoff_frozen_at IS NULL OR julianday(cutoff_frozen_at) IS NOT NULL
    ),
    consumed_verdict_id TEXT REFERENCES learning_contribution_verdicts(verdict_id),
    consumed_at TEXT CHECK (consumed_at IS NULL OR julianday(consumed_at) IS NOT NULL),
    authority_alpha REAL NOT NULL CHECK (authority_alpha > 0 AND authority_alpha < 0.5),
    created_at TEXT NOT NULL CHECK (julianday(created_at) IS NOT NULL),
    updated_at TEXT NOT NULL CHECK (julianday(updated_at) IS NOT NULL),
    CHECK (
        (status = 'collecting' AND cutoff_json IS NULL AND cutoff_fingerprint IS NULL
            AND cutoff_frozen_at IS NULL AND consumed_verdict_id IS NULL AND consumed_at IS NULL)
        OR (status = 'cutoff_frozen' AND cutoff_json IS NOT NULL
            AND cutoff_fingerprint IS NOT NULL AND cutoff_frozen_at IS NOT NULL
            AND consumed_verdict_id IS NULL AND consumed_at IS NULL)
        OR (status = 'consumed' AND cutoff_json IS NOT NULL
            AND cutoff_fingerprint IS NOT NULL AND cutoff_frozen_at IS NOT NULL
            AND consumed_verdict_id IS NOT NULL AND consumed_at IS NOT NULL)
        OR status = 'invalidated'
    )
);

CREATE INDEX IF NOT EXISTS idx_learning_inference_family
ON learning_inference_decisions(inference_family_id, status);

CREATE TRIGGER IF NOT EXISTS trg_learning_inference_decisions_no_delete
BEFORE DELETE ON learning_inference_decisions
BEGIN
    SELECT RAISE(ABORT, 'Learning inference decisions are retained');
END;

CREATE TRIGGER IF NOT EXISTS trg_learning_inference_plan_immutable
BEFORE UPDATE OF experiment_id, inference_family_id, inference_contract_version, plan_json,
                 plan_hash, authority_alpha, created_at
ON learning_inference_decisions
BEGIN
    SELECT RAISE(ABORT, 'Learning inference plan is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_learning_inference_cutoff_write_once
BEFORE UPDATE OF cutoff_json, cutoff_fingerprint, cutoff_frozen_at
ON learning_inference_decisions
WHEN (
    OLD.status != 'collecting'
    OR OLD.cutoff_fingerprint IS NOT NULL
 ) AND (
    NEW.cutoff_json IS NOT OLD.cutoff_json
    OR NEW.cutoff_fingerprint IS NOT OLD.cutoff_fingerprint
    OR NEW.cutoff_frozen_at IS NOT OLD.cutoff_frozen_at
 )
BEGIN
    SELECT RAISE(ABORT, 'Learning inference cutoff is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_learning_inference_consumption_write_once
BEFORE UPDATE OF consumed_verdict_id, consumed_at
ON learning_inference_decisions
WHEN (
    OLD.status != 'cutoff_frozen'
    OR OLD.consumed_verdict_id IS NOT NULL
 ) AND (
    NEW.consumed_verdict_id IS NOT OLD.consumed_verdict_id
    OR NEW.consumed_at IS NOT OLD.consumed_at
 )
BEGIN
    SELECT RAISE(ABORT, 'Learning inference decision is already consumed');
END;

CREATE TRIGGER IF NOT EXISTS trg_learning_inference_status_monotonic
BEFORE UPDATE OF status ON learning_inference_decisions
WHEN NOT (
    NEW.status = OLD.status
    OR (OLD.status = 'collecting' AND NEW.status IN ('cutoff_frozen', 'invalidated'))
    OR (OLD.status = 'cutoff_frozen' AND NEW.status IN ('consumed', 'invalidated'))
)
BEGIN
    SELECT RAISE(ABORT, 'Learning inference status cannot regress');
END;

CREATE TRIGGER IF NOT EXISTS trg_learning_inference_consumed_verdict_exact
BEFORE UPDATE OF consumed_verdict_id ON learning_inference_decisions
WHEN NEW.consumed_verdict_id IS NOT NULL
 AND NOT EXISTS (
    SELECT 1
    FROM learning_contribution_verdicts AS verdict
    WHERE verdict.verdict_id = NEW.consumed_verdict_id
      AND verdict.experiment_id = NEW.experiment_id
      AND verdict.inference_contract_version = NEW.inference_contract_version
      AND verdict.inference_role = 'confirmatory'
      AND verdict.authority_eligible = 1
      AND verdict.inference_plan_hash = NEW.plan_hash
      AND verdict.inference_cutoff_fingerprint = NEW.cutoff_fingerprint
 )
BEGIN
    SELECT RAISE(ABORT, 'consumed Learning verdict does not match frozen inference');
END;

CREATE TRIGGER IF NOT EXISTS trg_learning_authority_verdict_inference_immutable
BEFORE UPDATE ON learning_contribution_verdicts
WHEN OLD.authority_eligible = 1 OR NEW.authority_eligible = 1
BEGIN
    SELECT RAISE(ABORT, 'authority-eligible Learning verdict is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_learning_authority_verdict_no_delete
BEFORE DELETE ON learning_contribution_verdicts
WHEN OLD.authority_eligible = 1
BEGIN
    SELECT RAISE(ABORT, 'authority-eligible Learning verdict is retained');
END;
"""
_LEARNING_FIXED_LOOK_CHECKSUM = hashlib.sha256(
    (
        repr(_LEARNING_FIXED_LOOK_COLUMNS)
        + "\n"
        + _LEARNING_FIXED_LOOK_SQL
        + "\nlearning_fixed_look_v1\nPRAGMA optimize"
    ).encode("utf-8")
).hexdigest()

_EXIT_BREAKEVEN_SHADOW_SQL = """
CREATE TABLE IF NOT EXISTS exit_breakeven_shadow_assignments (
    position_id TEXT PRIMARY KEY REFERENCES positions(id),
    experiment_version TEXT NOT NULL CHECK (experiment_version = 'exit-breakeven-shadow/v1'),
    treatment_id TEXT NOT NULL CHECK (treatment_id = 'entry-lock-after-1.5pct/v1'),
    assigned_at TEXT NOT NULL CHECK (julianday(assigned_at) IS NOT NULL),
    venue TEXT NOT NULL CHECK (length(trim(venue)) > 0),
    symbol TEXT NOT NULL CHECK (length(trim(symbol)) > 0),
    market_type TEXT NOT NULL CHECK (market_type IN ('spot', 'perp')),
    timeframe TEXT NOT NULL CHECK (timeframe = '1m'),
    direction TEXT NOT NULL CHECK (direction IN ('long', 'short')),
    entry_price REAL NOT NULL CHECK (entry_price > 0),
    quantity REAL NOT NULL CHECK (quantity > 0),
    entry_fee REAL NOT NULL CHECK (entry_fee >= 0),
    exit_fee_rate REAL NOT NULL CHECK (exit_fee_rate >= 0),
    slippage_bps REAL NOT NULL CHECK (slippage_bps >= 0),
    trigger_return_pct REAL NOT NULL CHECK (trigger_return_pct > 0),
    trigger_price REAL NOT NULL CHECK (trigger_price > 0),
    shadow_stop_price REAL NOT NULL CHECK (shadow_stop_price > 0),
    exit_plan_version INTEGER NOT NULL CHECK (exit_plan_version >= 1),
    exit_plan_effective_at TEXT NOT NULL CHECK (julianday(exit_plan_effective_at) IS NOT NULL),
    exit_plan_hash TEXT NOT NULL CHECK (
        length(exit_plan_hash) = 64 AND exit_plan_hash = lower(exit_plan_hash)
        AND exit_plan_hash NOT GLOB '*[^0-9a-f]*'
    ),
    rule_json TEXT NOT NULL CHECK (json_valid(rule_json)),
    rule_hash TEXT NOT NULL CHECK (
        length(rule_hash) = 64 AND rule_hash = lower(rule_hash)
        AND rule_hash NOT GLOB '*[^0-9a-f]*'
    ),
    behavior_authority INTEGER NOT NULL DEFAULT 0 CHECK (behavior_authority = 0),
    runtime_session_id TEXT,
    code_commit TEXT,
    behavior_fingerprint TEXT,
    created_at TEXT NOT NULL CHECK (julianday(created_at) IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS exit_breakeven_shadow_outcomes (
    position_id TEXT PRIMARY KEY REFERENCES exit_breakeven_shadow_assignments(position_id),
    status TEXT NOT NULL CHECK (status IN ('unchanged', 'counterfactual_close', 'unavailable')),
    reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
    treatment_changed INTEGER NOT NULL CHECK (treatment_changed IN (0, 1)),
    actual_closed_at TEXT NOT NULL CHECK (julianday(actual_closed_at) IS NOT NULL),
    trigger_bar_open_ms INTEGER CHECK (trigger_bar_open_ms IS NULL OR trigger_bar_open_ms >= 0),
    shadow_exit_bar_open_ms INTEGER CHECK (shadow_exit_bar_open_ms IS NULL OR shadow_exit_bar_open_ms >= 0),
    shadow_exit_at TEXT CHECK (shadow_exit_at IS NULL OR julianday(shadow_exit_at) IS NOT NULL),
    shadow_reference_price REAL CHECK (shadow_reference_price IS NULL OR shadow_reference_price > 0),
    shadow_fill_price REAL CHECK (shadow_fill_price IS NULL OR shadow_fill_price > 0),
    shadow_entry_fee REAL CHECK (shadow_entry_fee IS NULL OR shadow_entry_fee >= 0),
    shadow_exit_fee REAL CHECK (shadow_exit_fee IS NULL OR shadow_exit_fee >= 0),
    shadow_funding_cost REAL,
    actual_net_pnl REAL,
    shadow_net_pnl REAL,
    incremental_net_pnl REAL,
    cost_model_version TEXT,
    evidence_json TEXT NOT NULL CHECK (json_valid(evidence_json)),
    evidence_hash TEXT NOT NULL CHECK (
        length(evidence_hash) = 64 AND evidence_hash = lower(evidence_hash)
        AND evidence_hash NOT GLOB '*[^0-9a-f]*'
    ),
    settled_at TEXT NOT NULL CHECK (julianday(settled_at) IS NOT NULL),
    CHECK (
        (status = 'unavailable' AND treatment_changed = 0
            AND shadow_net_pnl IS NULL AND incremental_net_pnl IS NULL)
        OR (status = 'unchanged' AND treatment_changed = 0
            AND actual_net_pnl IS NOT NULL AND shadow_net_pnl IS NOT NULL
            AND incremental_net_pnl IS NOT NULL)
        OR (status = 'counterfactual_close' AND treatment_changed = 1
            AND trigger_bar_open_ms IS NOT NULL AND shadow_exit_bar_open_ms IS NOT NULL
            AND shadow_exit_at IS NOT NULL AND shadow_fill_price IS NOT NULL
            AND actual_net_pnl IS NOT NULL AND shadow_net_pnl IS NOT NULL
            AND incremental_net_pnl IS NOT NULL AND cost_model_version IS NOT NULL)
    )
);

CREATE TRIGGER IF NOT EXISTS trg_exit_breakeven_assignment_no_update
BEFORE UPDATE ON exit_breakeven_shadow_assignments
BEGIN
    SELECT RAISE(ABORT, 'Exit breakeven shadow assignment is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_exit_breakeven_assignment_no_delete
BEFORE DELETE ON exit_breakeven_shadow_assignments
BEGIN
    SELECT RAISE(ABORT, 'Exit breakeven shadow assignment is retained');
END;

CREATE TRIGGER IF NOT EXISTS trg_exit_breakeven_outcome_no_update
BEFORE UPDATE ON exit_breakeven_shadow_outcomes
BEGIN
    SELECT RAISE(ABORT, 'Exit breakeven shadow outcome is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_exit_breakeven_outcome_no_delete
BEFORE DELETE ON exit_breakeven_shadow_outcomes
BEGIN
    SELECT RAISE(ABORT, 'Exit breakeven shadow outcome is retained');
END;
"""
_EXIT_BREAKEVEN_SHADOW_CHECKSUM = hashlib.sha256(
    (_EXIT_BREAKEVEN_SHADOW_SQL + "\nexit_breakeven_shadow_v1\nPRAGMA optimize").encode("utf-8")
).hexdigest()

_LEARNING_RANDOMIZATION_INFERENCE_TABLE_SQL = """
CREATE TABLE learning_inference_decisions (
    experiment_id TEXT PRIMARY KEY REFERENCES learning_experiments(experiment_id),
    inference_family_id TEXT NOT NULL CHECK (length(trim(inference_family_id)) > 0),
    inference_contract_version TEXT NOT NULL CHECK (
        inference_contract_version IN ('ufc-fixed-look/v1', 'ufc-fixed-look/v2')
    ),
    plan_json TEXT NOT NULL CHECK (json_valid(plan_json)),
    plan_hash TEXT NOT NULL CHECK (
        length(plan_hash) = 64
        AND plan_hash = lower(plan_hash)
        AND plan_hash NOT GLOB '*[^0-9a-f]*'
    ),
    status TEXT NOT NULL CHECK (
        status IN ('collecting', 'cutoff_frozen', 'consumed', 'invalidated')
    ),
    cutoff_json TEXT CHECK (cutoff_json IS NULL OR json_valid(cutoff_json)),
    cutoff_fingerprint TEXT CHECK (
        cutoff_fingerprint IS NULL
        OR (
            length(cutoff_fingerprint) = 64
            AND cutoff_fingerprint = lower(cutoff_fingerprint)
            AND cutoff_fingerprint NOT GLOB '*[^0-9a-f]*'
        )
    ),
    cutoff_frozen_at TEXT CHECK (
        cutoff_frozen_at IS NULL OR julianday(cutoff_frozen_at) IS NOT NULL
    ),
    consumed_verdict_id TEXT REFERENCES learning_contribution_verdicts(verdict_id),
    consumed_at TEXT CHECK (consumed_at IS NULL OR julianday(consumed_at) IS NOT NULL),
    authority_alpha REAL NOT NULL CHECK (authority_alpha > 0 AND authority_alpha < 0.5),
    created_at TEXT NOT NULL CHECK (julianday(created_at) IS NOT NULL),
    updated_at TEXT NOT NULL CHECK (julianday(updated_at) IS NOT NULL),
    CHECK (
        (status = 'collecting' AND cutoff_json IS NULL AND cutoff_fingerprint IS NULL
            AND cutoff_frozen_at IS NULL AND consumed_verdict_id IS NULL AND consumed_at IS NULL)
        OR (status = 'cutoff_frozen' AND cutoff_json IS NOT NULL
            AND cutoff_fingerprint IS NOT NULL AND cutoff_frozen_at IS NOT NULL
            AND consumed_verdict_id IS NULL AND consumed_at IS NULL)
        OR (status = 'consumed' AND cutoff_json IS NOT NULL
            AND cutoff_fingerprint IS NOT NULL AND cutoff_frozen_at IS NOT NULL
            AND consumed_verdict_id IS NOT NULL AND consumed_at IS NOT NULL)
        OR status = 'invalidated'
    )
);
"""
_LEARNING_RANDOMIZATION_INFERENCE_CHECKSUM = hashlib.sha256(
    (
        _LEARNING_RANDOMIZATION_INFERENCE_TABLE_SQL
        + "\nconditional_randomization_inference_v1\nPRAGMA optimize"
    ).encode("utf-8")
).hexdigest()


_UNIVERSE_TWIN_PROSPECTIVE_COLUMNS = (
    ("experiment_id", "universe_twin_shadow_batches", "TEXT"),
    ("behavior_contract_hash", "universe_twin_shadow_batches", "TEXT"),
    ("assignment_unit_key", "universe_twin_shadow_batches", "TEXT"),
    ("assignment_contract_version", "universe_twin_shadow_batches", "TEXT"),
    ("assignment_arm", "universe_twin_shadow_batches", "TEXT"),
    ("assignment_propensity", "universe_twin_shadow_batches", "REAL"),
    ("treatment_coefficient", "universe_twin_shadow_batches", "REAL"),
    ("treatment_policy", "universe_twin_shadow_batches", "TEXT"),
    ("control_policy", "universe_twin_shadow_batches", "TEXT"),
    ("primary_horizon", "universe_twin_shadow_batches", "TEXT"),
    ("outcome_contract_version", "universe_twin_shadow_batches", "TEXT"),
    ("experiment_id", "universe_twin_shadow_records", "TEXT"),
    ("policy_epoch_id", "universe_twin_shadow_records", "TEXT"),
    ("behavior_contract_hash", "universe_twin_shadow_records", "TEXT"),
    ("availability_profile_id", "universe_twin_shadow_records", "TEXT"),
    ("assignment_unit_key", "universe_twin_shadow_records", "TEXT"),
    ("assignment_arm", "universe_twin_shadow_records", "TEXT"),
    ("assignment_propensity", "universe_twin_shadow_records", "REAL"),
    ("treatment_coefficient", "universe_twin_shadow_records", "REAL"),
    ("primary_horizon", "universe_twin_shadow_records", "TEXT"),
    ("outcome_contract_version", "universe_twin_shadow_records", "TEXT"),
    ("metric_version", "universe_twin_shadow_records", "TEXT"),
    ("outcome_candidate_key", "universe_twin_shadow_records", "TEXT"),
    ("assigned_policy_selected", "universe_twin_shadow_records", "INTEGER"),
)
_UNIVERSE_TWIN_PROSPECTIVE_CHECKSUM = hashlib.sha256(
    (repr(_UNIVERSE_TWIN_PROSPECTIVE_COLUMNS) + "\nadditive_columns_and_indexes_v2_atomic").encode(
        "utf-8"
    )
).hexdigest()

_TECHNICAL_SNAPSHOT_METADATA_COLUMNS = (
    ("technical_algorithm_version", "universe_feature_snapshots", "TEXT"),
    (
        "technical_snapshot_manifest",
        "universe_feature_snapshots",
        "TEXT NOT NULL DEFAULT '{}'",
    ),
)
_TECHNICAL_SNAPSHOT_METADATA_CHECKSUM = hashlib.sha256(
    (
        repr(_TECHNICAL_SNAPSHOT_METADATA_COLUMNS)
        + "\nadditive_columns_v1"
    ).encode("utf-8")
).hexdigest()

_EXECUTION_REFERENCE_COLUMNS = (
    ("reference_price", "orders", "REAL"),
    ("reference_price_source", "orders", "TEXT"),
)
_EXECUTION_REFERENCE_SQL = """
CREATE TRIGGER IF NOT EXISTS trg_orders_execution_reference_immutable
BEFORE UPDATE OF reference_price, reference_price_source ON orders
WHEN NEW.reference_price IS NOT OLD.reference_price
  OR NEW.reference_price_source IS NOT OLD.reference_price_source
BEGIN
    SELECT RAISE(ABORT, 'Order execution reference is immutable');
END;
"""
_EXECUTION_REFERENCE_CHECKSUM = hashlib.sha256(
    (
        repr(_EXECUTION_REFERENCE_COLUMNS)
        + "\n"
        + _EXECUTION_REFERENCE_SQL
        + "\nadditive_execution_reference_v1"
    ).encode("utf-8")
).hexdigest()

_SHADOW_LEARNING_TRACE_COLUMNS = (
    ("learning_score_trace_json", "shadow_trades", "TEXT"),
)
_SHADOW_LEARNING_TRACE_CHECKSUM = hashlib.sha256(
    (repr(_SHADOW_LEARNING_TRACE_COLUMNS) + "\nadditive_shadow_learning_trace_v1").encode(
        "utf-8"
    )
).hexdigest()

_UNIVERSE_POSITIONING_COLUMNS = (
    ("long_short_ratio", "universe_feature_snapshots", "REAL"),
    ("top_trader_position_ratio", "universe_feature_snapshots", "REAL"),
    ("top_trader_position_ratio", "open_interest_snapshots", "REAL"),
)
_UNIVERSE_RELATIVE_RANK_SHADOW_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS universe_relative_rank_shadow_batches (
    scanner_run_id TEXT PRIMARY KEY,
    authority_snapshot_id TEXT,
    experiment_id TEXT NOT NULL,
    policy_epoch_id TEXT NOT NULL,
    behavior_contract_hash TEXT NOT NULL,
    assignment_unit_key TEXT NOT NULL,
    assignment_contract_version TEXT NOT NULL,
    assignment_arm TEXT NOT NULL CHECK (assignment_arm IN ('control', 'treatment')),
    assignment_propensity REAL NOT NULL CHECK (assignment_propensity > 0 AND assignment_propensity <= 1),
    treatment_coefficient REAL NOT NULL CHECK (treatment_coefficient IN (0, 1)),
    treatment_policy TEXT NOT NULL,
    control_policy TEXT NOT NULL,
    primary_horizon TEXT NOT NULL,
    outcome_contract_version TEXT NOT NULL,
    metric_version TEXT NOT NULL,
    feature_source_fingerprint TEXT NOT NULL,
    record_version TEXT NOT NULL,
    behavior_version TEXT NOT NULL,
    min_candidate_count INTEGER NOT NULL CHECK (min_candidate_count >= 2),
    candidate_count INTEGER NOT NULL CHECK (candidate_count >= 0),
    eligible_count INTEGER NOT NULL CHECK (eligible_count >= 0),
    available INTEGER NOT NULL CHECK (available IN (0, 1)),
    promotion_eligible INTEGER NOT NULL DEFAULT 0 CHECK (promotion_eligible = 0),
    blocking_reasons_json TEXT NOT NULL DEFAULT '[]',
    records_json TEXT NOT NULL,
    batch_hash TEXT NOT NULL,
    captured_at TEXT NOT NULL
)
"""
_UNIVERSE_RELATIVE_RANK_SHADOW_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_relative_rank_shadow_epoch_captured "
    "ON universe_relative_rank_shadow_batches(policy_epoch_id, captured_at)"
)
_UNIVERSE_POSITIONING_CHECKSUM = hashlib.sha256(
    (
        repr(_UNIVERSE_POSITIONING_COLUMNS)
        + _UNIVERSE_RELATIVE_RANK_SHADOW_TABLE_SQL
        + _UNIVERSE_RELATIVE_RANK_SHADOW_INDEX_SQL
        + "\nadditive_universe_positioning_relative_rank_v1"
    ).encode("utf-8")
).hexdigest()

_SERENITY_RETRY_IDEMPOTENCY_SQL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_serenity_retry_attempt_identity "
    "ON serenity_research_runs(parent_run_id, retry_count) "
    "WHERE idempotency_key LIKE 'serenity-retry:%'"
)
_SERENITY_RETRY_IDEMPOTENCY_CHECKSUM = hashlib.sha256(
    _SERENITY_RETRY_IDEMPOTENCY_SQL.encode("utf-8")
).hexdigest()

_PERSONA_ROLLOUT_SQL = (
    "CREATE TABLE IF NOT EXISTS persona_rollouts ("
    "rollout_id TEXT PRIMARY KEY,"
    "persona_id TEXT NOT NULL,"
    "persona_fingerprint TEXT NOT NULL,"
    "policy_epoch TEXT NOT NULL,"
    "runtime_version TEXT,"
    "current_step INTEGER NOT NULL DEFAULT 0,"
    "current_parameter TEXT,"
    "step_started_at TEXT NOT NULL,"
    "baseline_snapshot_json TEXT NOT NULL DEFAULT '{}',"
    "evidence_watermark TEXT,"
    "status TEXT NOT NULL,"
    "authority TEXT NOT NULL,"
    "reason TEXT NOT NULL,"
    "created_at TEXT NOT NULL,"
    "updated_at TEXT NOT NULL,"
    "ended_at TEXT"
    ");"
    "CREATE INDEX IF NOT EXISTS idx_persona_rollouts_active "
    "ON persona_rollouts(persona_id, policy_epoch, ended_at, updated_at);"
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_persona_rollouts_one_active_identity "
    "ON persona_rollouts(persona_id, policy_epoch) WHERE ended_at IS NULL;"
    "CREATE TABLE IF NOT EXISTS persona_rollout_events ("
    "event_id TEXT PRIMARY KEY,"
    "rollout_id TEXT NOT NULL REFERENCES persona_rollouts(rollout_id),"
    "step INTEGER NOT NULL,"
    "parameter TEXT,"
    "status TEXT NOT NULL,"
    "event_type TEXT NOT NULL,"
    "authority TEXT NOT NULL,"
    "reason TEXT NOT NULL,"
    "evidence_json TEXT NOT NULL DEFAULT '{}',"
    "actor TEXT NOT NULL,"
    "source TEXT NOT NULL,"
    "created_at TEXT NOT NULL"
    ");"
    "CREATE INDEX IF NOT EXISTS idx_persona_rollout_events_rollout "
    "ON persona_rollout_events(rollout_id, created_at, event_id);"
    "CREATE TRIGGER IF NOT EXISTS trg_persona_rollout_events_no_update "
    "BEFORE UPDATE ON persona_rollout_events BEGIN "
    "SELECT RAISE(ABORT, 'persona_rollout_events is append-only'); END;"
    "CREATE TRIGGER IF NOT EXISTS trg_persona_rollout_events_no_delete "
    "BEFORE DELETE ON persona_rollout_events BEGIN "
    "SELECT RAISE(ABORT, 'persona_rollout_events is append-only'); END;"
)
_PERSONA_ROLLOUT_COLUMNS = (("sizing_manifest", "positions", "TEXT"),)
_PERSONA_ROLLOUT_CHECKSUM = hashlib.sha256(
    (
        _PERSONA_ROLLOUT_SQL
        + repr(_PERSONA_ROLLOUT_COLUMNS)
        + "\nprospective_persona_rollout_v1"
    ).encode("utf-8")
).hexdigest()

_EXCHANGE_UPNL_IDENTITY_COLUMNS = (
    ("unrealized_pnl_source", "positions", "TEXT NOT NULL DEFAULT 'local_estimate'"),
)
_EXCHANGE_UPNL_IDENTITY_CHECKSUM = hashlib.sha256(
    (
        repr(_EXCHANGE_UPNL_IDENTITY_COLUMNS)
        + "\nexchange_upnl_identity_v1"
    ).encode("utf-8")
).hexdigest()

# Observation Lab v1 (Issue #1072). The frozen-unit table lives in the Lab's
# own SQLite file (``~/.aletheia/observation-lab/lab.db``, initialized by
# ``aletheia.observation_lab.db``) — physical isolation from ``app.db`` is what
# lets unrelated production schema changes miss the Lab entirely. This app.db
# migration only adds the ``adapter_identity_hash`` column on the spec row so
# the identity-vector invalidation check (owner review §4) has a place to
# stash its hash.
_OBSERVATION_LAB_FROZEN_UNITS_COLUMNS = (
    ("adapter_identity_hash", "filter_shadow_experiments", "TEXT"),
)
_OBSERVATION_LAB_FROZEN_UNITS_CHECKSUM = hashlib.sha256(
    (
        repr(_OBSERVATION_LAB_FROZEN_UNITS_COLUMNS)
        + "\nobservation_lab_frozen_units_v2"
    ).encode("utf-8")
).hexdigest()

# EX-04 (#1004): bind the opaque live-authorization scope hash to every
# newly created live Proposal so the execution gateway can revalidate that
# the exact authorization is still active before an order goes out. The
# column is nullable — every historical Proposal was paper/demo and has no
# live authorization to carry.
_LIVE_AUTHORIZATION_REF_COLUMNS = (
    ("live_authorization_ref", "trade_proposals", "TEXT"),
)
_LIVE_AUTHORIZATION_REF_CHECKSUM = hashlib.sha256(
    (
        repr(_LIVE_AUTHORIZATION_REF_COLUMNS)
        + "\nlive_authorization_ref_v1"
    ).encode("utf-8")
).hexdigest()

_MOMENTUM_SHORT_EXHAUSTION_COLUMNS = (
    ("rsi_1h_at_entry", "filter_candidate_outcomes", "REAL"),
    ("rsi_15m_at_entry", "filter_candidate_outcomes", "REAL"),
    ("return_24h_at_entry", "filter_candidate_outcomes", "REAL"),
    ("direction_exhaustion_signal", "filter_candidate_outcomes", "INTEGER"),
)
_MOMENTUM_SHORT_EXHAUSTION_CHECKSUM = hashlib.sha256(
    (
        repr(_MOMENTUM_SHORT_EXHAUSTION_COLUMNS)
        + "\nmomentum_short_exhaustion_shadow_v1"
    ).encode("utf-8")
).hexdigest()


_OPTIONS_MARKET_STRUCTURE_SQL = """
CREATE TABLE IF NOT EXISTS options_market_structure_snapshots (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    symbol TEXT NOT NULL CHECK (symbol IN ('BTC', 'ETH')),
    source TEXT NOT NULL DEFAULT 'deribit_public',
    gex_convention TEXT NOT NULL DEFAULT 'v1_retail_call_minus_put',
    underlying_price REAL,
    net_gex_estimate REAL,
    gamma_regime TEXT CHECK (gamma_regime IN ('positive', 'negative', 'neutral')),
    gamma_flip_estimate REAL,
    call_gamma_wall REAL,
    put_gamma_wall REAL,
    nearest_gamma_wall_distance REAL,
    options_open_interest REAL,
    call_oi REAL,
    put_oi REAL,
    valid_strike_count INTEGER,
    data_quality TEXT NOT NULL DEFAULT 'ok',
    observed_at TEXT NOT NULL CHECK (julianday(observed_at) IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_options_structure_sym_ts
ON options_market_structure_snapshots (symbol, observed_at DESC);
"""
_OPTIONS_MARKET_STRUCTURE_CHECKSUM = hashlib.sha256(
    (_OPTIONS_MARKET_STRUCTURE_SQL + "\noptions_market_structure_v1").encode("utf-8")
).hexdigest()


class MigrationStateError(RuntimeError):
    """Base class for durable migration-ledger contract failures."""


class PendingMigrationsError(MigrationStateError):
    """The database has not applied every migration required by this code."""


class MigrationChecksumMismatch(MigrationStateError):
    """An applied migration ID no longer has its immutable checksum."""


class DatabaseSchemaAheadError(MigrationStateError):
    """The database contains migration IDs unknown to this older code."""


@dataclass(frozen=True)
class MigrationStep:
    migration_id: str
    checksum: str
    schema_version: str
    apply: Callable[[sqlite3.Connection], None]


@dataclass(frozen=True)
class MigrationProgressEvent:
    """Storage-owned progress fact; presentation remains a CLI concern."""

    phase: str
    migration_id: str
    description: str
    step_index: int
    step_count: int
    duration_sec: float | None = None
    estimated_remaining_sec: tuple[float, float] | None = None
    estimate_basis: str | None = None


MigrationProgressCallback = Callable[[MigrationProgressEvent], None]


_MIGRATION_DESCRIPTIONS = {
    _LEGACY_BASELINE_ID: "legacy schema, indexes, and idempotent backfills",
    "20260806_0002_startup_dataset_boundary_v3": "startup indexes and one-time FTS rebuild",
    "20260806_0003_capacity_guardrails_v4": "capacity indexes and cleanup lookup",
    "20260806_0004_pending_outcome_partial_index_v5": "pending-outcome partial index",
    "20260806_0005_llm_inference_linkage_v6": "LLM inference audit linkage columns",
    "20260807_0006_market_signal_identity_v7": "exact market-signal evidence column",
    "20260808_0007_universe_twin_shadow_evidence_v8": (
        "non-authoritative Universe twin Shadow evidence tables"
    ),
    "20260808_0008_position_protection_state_v9": (
        "immutable ExitPlan history and dormant protective-barrier state"
    ),
    "20260808_0009_learning_fixed_look_v10": (
        "prospective fixed-look Learning inference decisions"
    ),
    "20260808_0010_exit_breakeven_shadow_v11": (
        "prospective read-only exit breakeven shadow evidence"
    ),
    "20260808_0011_learning_randomization_inference_v12": (
        "conditional-randomization Learning inference contract"
    ),
    "20260809_0012_universe_twin_prospective_v13": (
        "prospective Shadow policy identity, assignment, and outcome linkage"
    ),
    "20260809_0014_serenity_retry_idempotency_v15": (
        "one newly-created Serenity retry per parent and retry count"
    ),
    "20260809_0015_execution_reference_v16": (
        "immutable pre-fill reference prices for paired Paper execution accounting"
    ),
    "20260810_0016_shadow_learning_trace_v17": (
        "prospective Ranker Learning score trace on existing Shadow rows"
    ),
    "20260810_0017_universe_positioning_v18": (
        "public positioning ratios and isolated Relative Rank Shadow evidence"
    ),
    "20260811_0022_exit_trailing_recovery_v23": (
        "write-once trailing recovery evidence on frozen protection identity"
    ),
    "20260811_0023_learning_hypothesis_identity_v24": (
        "explicit behavior-contract and fingerprint-schema hypothesis provenance"
    ),
    "20260812_0024_ufc_canonical_identity_v25": (
        "materialize fingerprint-verified identity for the active UFC Paper"
    ),
    "20260815_0028_learning_dreaming_backlog_v29": (
        "durable Dreaming case cursor and pending-backlog indexes"
    ),
    "20260816_0029_position_realtime_revision_v30": (
        "monotonic Position revisions for real-time invalidation"
    ),
    "20260817_0030_persona_rollout_v31": (
        "prospective Persona rollout state, evidence events, and sizing manifests"
    ),
    "20260817_0031_exchange_upnl_identity_v32": (
        "owner-facing exchange uPnL identity and fallback source"
    ),
    "20260817_0032_observation_lab_frozen_units_v33": (
        "Observation Lab v1 POC: adapter identity hash + frozen first-exposure units"
    ),
    "20260819_0035_options_market_structure_v36": (
        "BTC/ETH Deribit options GEX and gamma-structure snapshot table"
    ),
}


def _migration_duration_estimate(
    migration_id: str,
    *,
    database_bytes: int,
) -> tuple[tuple[float, float], str] | None:
    """Return a deliberately broad local calibration range, never a deadline."""

    size_gib = max(0.0, int(database_bytes)) / (1024**3)
    if migration_id == _LEGACY_BASELINE_ID:
        # CASI measured 29-47 seconds at 1.78 GiB. Keep a wider model to
        # acknowledge disk/antivirus variance rather than showing fake precision.
        return (
            (max(5.0, 5.0 + 12.0 * size_gib), max(15.0, 10.0 + 25.0 * size_gib)),
            "broad range calibrated from a 1.78 GiB CASI migration",
        )
    if migration_id == "20260806_0002_startup_dataset_boundary_v3":
        return (
            (1.0, max(5.0, 5.0 + 2.0 * size_gib)),
            "dataset-scaled index/FTS estimate",
        )
    if migration_id == "20260806_0003_capacity_guardrails_v4":
        return (
            (1.0, max(5.0, 3.0 + 5.0 * size_gib)),
            "dataset-scaled index maintenance estimate",
        )
    if migration_id == "20260806_0004_pending_outcome_partial_index_v5":
        return (
            (1.0, max(5.0, 3.0 + 4.0 * size_gib)),
            "dataset-scaled partial-index estimate",
        )
    return None


def _emit_migration_progress(
    callback: MigrationProgressCallback | None,
    event: MigrationProgressEvent,
) -> None:
    if callback is None:
        return
    try:
        callback(event)
    except Exception as exc:  # presentation must never decide schema safety
        logger.debug("migration progress callback failed: %s", exc)


def _apply_startup_dataset_boundary_v3(db: sqlite3.Connection) -> None:
    """Install startup indexes and rebuild FTS once, never on every boot."""

    db.executescript(_STARTUP_HOT_PATH_INDEXES_SQL)
    for table, sql in _OPTIONAL_HOT_PATH_INDEXES:
        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if exists:
            db.execute(sql)
    from aletheia.knowledge.retrieval import reindex_all

    reindex_all(db=db, strict=True)
    db.execute("PRAGMA optimize")


def _apply_capacity_guardrails_v4(db: sqlite3.Connection) -> None:
    """Remove indexes already covered by a UNIQUE constraint or primary key."""

    db.executescript(_CAPACITY_GUARDRAIL_INDEX_SQL)
    db.execute("PRAGMA optimize")


def _apply_pending_outcome_partial_index_v5(db: sqlite3.Connection) -> None:
    """Index only unsettled outcomes consumed by the due-settlement queue."""

    db.executescript(_PENDING_OUTCOME_PARTIAL_INDEX_SQL)
    db.execute("PRAGMA optimize")


def _apply_llm_inference_linkage_v6(db: sqlite3.Connection) -> None:
    """Add content-free inference linkage without rewriting the frozen baseline."""

    for column, table, column_type in _LLM_INFERENCE_LINKAGE_COLUMNS:
        existing = {
            str(row[1]) for row in db.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")


def _apply_agent_duration_v19(db: sqlite3.Connection) -> None:
    """Add nullable elapsed-time fields without rewriting historical records."""

    for column, table, column_type in _AGENT_DURATION_COLUMNS:
        existing = {
            str(row[1]) for row in db.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")


def _apply_agent_usage_storage_v20(db: sqlite3.Connection) -> None:
    """Add current-run artifact links and optional tracing snapshots without backfill."""

    for column, table, column_type in _AGENT_USAGE_STORAGE_COLUMNS:
        existing = {
            str(row[1]) for row in db.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
    db.executescript(_AGENT_RUN_ARTIFACTS_SQL)


def _apply_lesson_reviewer_attempts_v21(db: sqlite3.Connection) -> None:
    """Add append-only execution history for the mutating lesson reviewer."""

    db.executescript(_LESSON_REVIEWER_ATTEMPTS_SQL)


def _apply_event_jsonl_outbox_v22(db: sqlite3.Connection) -> None:
    """Make the existing EventStore JSONL mirror retryable after DB commit."""

    db.execute("SAVEPOINT event_jsonl_outbox_v22")
    try:
        existing = {
            str(row[1])
            for row in db.execute("PRAGMA table_info(ps_event_log)").fetchall()
        }
        status_was_missing = "jsonl_mirror_status" not in existing
        for column, table, column_type in _EVENT_JSONL_OUTBOX_COLUMNS:
            if column not in existing:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
                existing.add(column)
        if status_was_missing:
            # Existing rows were already handled by the legacy dual-write path. Do not
            # replay the entire historical ledger merely because retry facts are new.
            db.execute(
                "UPDATE ps_event_log SET jsonl_mirror_status='mirrored', "
                "jsonl_mirrored_at=created_at"
            )
        db.execute(_EVENT_JSONL_OUTBOX_INDEX_SQL)
        db.execute("RELEASE SAVEPOINT event_jsonl_outbox_v22")
    except Exception:
        db.execute("ROLLBACK TO SAVEPOINT event_jsonl_outbox_v22")
        db.execute("RELEASE SAVEPOINT event_jsonl_outbox_v22")
        raise


def _apply_exit_trailing_recovery_v23(db: sqlite3.Connection) -> None:
    """Retain one immutable recovery observation per protected Position."""

    existing = {
        str(row[1])
        for row in db.execute("PRAGMA table_info(position_protection_state)").fetchall()
    }
    for column, table, column_type in _EXIT_TRAILING_RECOVERY_COLUMNS:
        if column not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
            existing.add(column)
    db.executescript(_EXIT_TRAILING_RECOVERY_SQL)
    db.execute("PRAGMA optimize")


def _apply_learning_dreaming_backlog_v29(db: sqlite3.Connection) -> None:
    """Add the existing job-health cursor and backlog lookup indexes."""

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS learning_job_health (
            job_id TEXT PRIMARY KEY,
            schedule_key TEXT NOT NULL,
            last_expected_at TEXT,
            last_started_at TEXT,
            last_succeeded_at TEXT,
            next_due_at TEXT,
            input_watermark TEXT,
            input_cursor TEXT,
            backlog_count INTEGER NOT NULL DEFAULT 0,
            oldest_backlog_at TEXT,
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            last_error TEXT,
            last_failed_at TEXT,
            last_failure_reason TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    for column, table, column_type in _LEARNING_DREAMING_BACKLOG_COLUMNS:
        existing = {
            str(row[1])
            for row in db.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
    tables = {
        str(row[0])
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "knowledge_items" in tables:
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_knowledge_trade_case_created "
            "ON knowledge_items(item_type, status, created_at, id)"
        )
    if "ps_lessons" in tables:
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_ps_lessons_trade_case_source "
            "ON ps_lessons(source_type, source_id)"
        )
    db.execute("PRAGMA optimize")


def _apply_position_realtime_revision_v30(db: sqlite3.Connection) -> None:
    """Give every Position change a cheap, schema-backed WS fingerprint."""

    for statement in _POSITION_REALTIME_REVISION_SQL:
        db.execute(statement)
    db.execute("PRAGMA optimize")


def _apply_persona_rollout_v31(db: sqlite3.Connection) -> None:
    """Persist prospective Persona rollout state and entry manifests."""

    for column, table, column_type in _PERSONA_ROLLOUT_COLUMNS:
        existing = {
            str(row[1]) for row in db.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
    db.executescript(_PERSONA_ROLLOUT_SQL)
    db.execute("PRAGMA optimize")


def _apply_exchange_upnl_identity_v32(db: sqlite3.Connection) -> None:
    """Persist the owner-facing source of each open-position uPnL mark."""

    for column, table, column_type in _EXCHANGE_UPNL_IDENTITY_COLUMNS:
        existing = {
            str(row[1]) for row in db.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
    db.execute("PRAGMA optimize")


def _apply_observation_lab_frozen_units_v33(db: sqlite3.Connection) -> None:
    """Add the Observation Lab identity-vector hash column on the spec row.

    The frozen-unit table itself lives in ``lab.db`` (see
    ``aletheia.observation_lab.db``); only the column addition touches
    ``app.db`` because the spec is the identity anchor operators query.
    """
    for column, table, column_type in _OBSERVATION_LAB_FROZEN_UNITS_COLUMNS:
        existing = {
            str(row[1]) for row in db.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
    db.execute("PRAGMA optimize")


def _apply_live_authorization_ref_v34(db: sqlite3.Connection) -> None:
    """Add the EX-04 opaque live authorization ref column on trade_proposals."""
    for column, table, column_type in _LIVE_AUTHORIZATION_REF_COLUMNS:
        existing = {
            str(row[1]) for row in db.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
    db.execute("PRAGMA optimize")


def _apply_momentum_short_exhaustion_shadow_v35(db: sqlite3.Connection) -> None:
    """Add direction-aware exhaustion feature lineage to filter_candidate_outcomes.

    Captures RSI 1h/15m and 24h return at entry time plus an observation-only
    exhaustion signal for momentum:short:perp candidates. No behavior change —
    these columns support shadow/counterfactual outcome tracking only.
    """
    table_exists = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='filter_candidate_outcomes'"
    ).fetchone() is not None
    if not table_exists:
        db.execute("PRAGMA optimize")
        return
    for column, table, column_type in _MOMENTUM_SHORT_EXHAUSTION_COLUMNS:
        existing = {
            str(row[1]) for row in db.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
    db.execute("PRAGMA optimize")


def _apply_options_market_structure_v36(db: sqlite3.Connection) -> None:
    """Add options_market_structure_snapshots for Deribit GEX/gamma support factors."""
    db.executescript(_OPTIONS_MARKET_STRUCTURE_SQL)
    db.execute("PRAGMA optimize")


def _apply_learning_hypothesis_identity_v24(db: sqlite3.Connection) -> None:
    """Persist future fingerprint inputs without rewriting historical identity."""

    for column, table, column_type in _LEARNING_HYPOTHESIS_IDENTITY_COLUMNS:
        existing = {
            str(row[1]) for row in db.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")


def _apply_ufc_canonical_identity_v25(db: sqlite3.Connection) -> None:
    """Materialize the identity already committed by an active UFC fingerprint."""

    path_id, behavior_version, schema_version = _UFC_CANONICAL_IDENTITY
    savepoint = "ufc_canonical_identity_v25"
    db.execute(f"SAVEPOINT {savepoint}")
    try:
        rows = db.execute(
            "SELECT e.experiment_id, h.hypothesis_id, h.fingerprint, "
            "h.scope_json, h.action_json, h.target_horizon, h.source_type, "
            "h.behavior_contract_version, h.fingerprint_schema_version "
            "FROM learning_experiments e "
            "JOIN learning_hypotheses h ON h.hypothesis_id=e.hypothesis_id "
            "WHERE h.path_id=? AND e.ended_at IS NULL "
            "AND e.stage IN ('paper_10','paper_25','paper_50','paper_active')",
            (path_id,),
        ).fetchall()
        if len(rows) > 1:
            raise MigrationStateError(
                "UFC identity migration found multiple open Paper experiments"
            )
        if rows:
            (
                _experiment_id,
                hypothesis_id,
                stored_fingerprint,
                scope_json,
                action_json,
                target_horizon,
                source_cohort,
                stored_behavior_version,
                stored_schema_version,
            ) = rows[0]
            if stored_behavior_version not in (None, behavior_version):
                raise MigrationStateError(
                    "active UFC Paper behavior contract does not match v25"
                )
            if stored_schema_version not in (None, schema_version):
                raise MigrationStateError(
                    "active UFC Paper fingerprint schema does not match v25"
                )

            import json

            fingerprint = _ufc_v25_fingerprint(
                path_id=path_id,
                scope=json.loads(scope_json),
                action=json.loads(action_json),
                target_horizon=target_horizon,
                source_cohort=source_cohort,
                behavior_version=behavior_version,
                schema_version=schema_version,
            )
            if fingerprint != stored_fingerprint:
                raise MigrationStateError(
                    "active UFC Paper fingerprint cannot prove canonical v25 identity"
                )
            db.execute(
                "UPDATE learning_hypotheses SET behavior_contract_version=?, "
                "fingerprint_schema_version=? WHERE hypothesis_id=?",
                (behavior_version, schema_version, hypothesis_id),
            )
        db.execute(f"RELEASE SAVEPOINT {savepoint}")
    except Exception:
        db.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        db.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise


def _apply_position_path_observations_v26(db: sqlite3.Connection) -> None:
    """Persist append-only five-minute facts for open Position analysis."""

    db.executescript(_POSITION_PATH_OBSERVATIONS_SQL)
    db.execute("PRAGMA optimize")

def _apply_position_path_deferred_buckets_v27(db: sqlite3.Connection) -> None:
    """Persist the narrow retry queue for transiently unavailable path buckets."""

    db.executescript(_POSITION_PATH_DEFERRED_BUCKETS_SQL)
    db.execute("PRAGMA optimize")


def _apply_position_path_observer_cursor_v28(db: sqlite3.Connection) -> None:
    """Persist the observer's durable bucket-enrollment cursor."""

    db.executescript(_POSITION_PATH_OBSERVER_CURSOR_SQL)
    db.execute("PRAGMA optimize")




def _apply_market_signal_identity_v7(db: sqlite3.Connection) -> None:
    """Add structured evidence without changing legacy wake-summary fields."""

    for column, table, column_type in _MARKET_SIGNAL_IDENTITY_COLUMNS:
        existing = {
            str(row[1]) for row in db.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")


def _apply_universe_twin_shadow_evidence_v8(db: sqlite3.Connection) -> None:
    """Add replayable diagnostic evidence without granting it authority."""

    db.executescript(_UNIVERSE_TWIN_SHADOW_EVIDENCE_SQL)
    db.execute("PRAGMA optimize")


def _apply_position_protection_state_v9(db: sqlite3.Connection) -> None:
    """Add dormant protection state without initializing or evaluating Positions."""

    for column, table, column_type in _POSITION_PROTECTION_COLUMNS:
        existing = {
            str(row[1]) for row in db.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
    db.executescript(_POSITION_PROTECTION_SQL)
    db.execute("PRAGMA optimize")


def _apply_learning_fixed_look_v10(db: sqlite3.Connection) -> None:
    """Add prospective inference state without granting existing verdicts authority."""

    for column, table, column_type in _LEARNING_FIXED_LOOK_COLUMNS:
        existing = {
            str(row[1]) for row in db.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
    db.executescript(_LEARNING_FIXED_LOOK_SQL)
    db.execute("PRAGMA optimize")


def _apply_exit_breakeven_shadow_v11(db: sqlite3.Connection) -> None:
    """Add prospective shadow evidence without changing Position behavior."""

    db.executescript(_EXIT_BREAKEVEN_SHADOW_SQL)
    db.execute("PRAGMA optimize")


def _apply_learning_randomization_inference_v12(db: sqlite3.Connection) -> None:
    """Allow v2 plans while retaining every immutable v1 inference fact."""

    expected_triggers = {
        "trg_learning_inference_consumed_verdict_exact",
        "trg_learning_inference_consumption_write_once",
        "trg_learning_inference_cutoff_write_once",
        "trg_learning_inference_decisions_no_delete",
        "trg_learning_inference_plan_immutable",
        "trg_learning_inference_status_monotonic",
    }
    row = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' "
        "AND name='learning_inference_decisions'"
    ).fetchone()
    if row is None:
        raise MigrationStateError("learning inference decision table is missing")
    table_sql = str(row[0] or "")
    if "ufc-fixed-look/v2" in table_sql:
        db.execute("PRAGMA optimize")
        return
    if "ufc-fixed-look/v1" not in table_sql:
        raise MigrationStateError("unsupported Learning inference decision schema")

    db.execute("SAVEPOINT learning_randomization_inference_v12")
    try:
        old_count = int(
            db.execute(
                "SELECT COUNT(*) FROM learning_inference_decisions"
            ).fetchone()[0]
        )
        trigger_names = [
            str(item[0])
            for item in db.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' "
                "AND tbl_name='learning_inference_decisions'"
            ).fetchall()
        ]
        if set(trigger_names) != expected_triggers:
            raise MigrationStateError(
                "Learning inference decision trigger contract drifted"
            )
        index_names = {
            str(item[0])
            for item in db.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND tbl_name='learning_inference_decisions' AND sql IS NOT NULL"
            ).fetchall()
        }
        if index_names != {"idx_learning_inference_family"}:
            raise MigrationStateError(
                "Learning inference decision index contract drifted"
            )
        for name in trigger_names:
            quoted = name.replace('"', '""')
            db.execute(f'DROP TRIGGER "{quoted}"')
        db.execute("DROP INDEX IF EXISTS idx_learning_inference_family")
        db.execute(
            "ALTER TABLE learning_inference_decisions "
            "RENAME TO learning_inference_decisions_v1"
        )
        db.execute(_LEARNING_RANDOMIZATION_INFERENCE_TABLE_SQL)
        columns = (
            "experiment_id, inference_family_id, inference_contract_version, plan_json, "
            "plan_hash, status, cutoff_json, cutoff_fingerprint, cutoff_frozen_at, "
            "consumed_verdict_id, consumed_at, authority_alpha, created_at, updated_at"
        )
        db.execute(
            f"INSERT INTO learning_inference_decisions ({columns}) "
            f"SELECT {columns} FROM learning_inference_decisions_v1"
        )
        new_count = int(
            db.execute(
                "SELECT COUNT(*) FROM learning_inference_decisions"
            ).fetchone()[0]
        )
        if new_count != old_count:
            raise MigrationStateError("learning inference decision row count changed")
        db.execute("DROP TABLE learning_inference_decisions_v1")

        # Reuse the immutable v10 object definitions without executescript:
        # executescript commits implicitly and would break this savepoint.
        pending_sql = ""
        for line in _LEARNING_FIXED_LOOK_SQL.splitlines(keepends=True):
            pending_sql += line
            if not sqlite3.complete_statement(pending_sql):
                continue
            statement = pending_sql.strip()
            pending_sql = ""
            if statement.upper().startswith("CREATE TABLE"):
                continue
            db.execute(statement)
        if pending_sql.strip():
            raise MigrationStateError("incomplete Learning inference object DDL")
        db.execute("PRAGMA optimize")
        db.execute("RELEASE SAVEPOINT learning_randomization_inference_v12")
    except (sqlite3.Error, MigrationStateError):
        db.execute("ROLLBACK TO SAVEPOINT learning_randomization_inference_v12")
        db.execute("RELEASE SAVEPOINT learning_randomization_inference_v12")
        raise


def _apply_universe_twin_prospective_v13(db: sqlite3.Connection) -> None:
    """Add prospective identities without changing step-v1 authority."""

    savepoint = "universe_twin_prospective_v13"
    db.execute(f"SAVEPOINT {savepoint}")
    try:
        for column, table, column_type in _UNIVERSE_TWIN_PROSPECTIVE_COLUMNS:
            existing = {
                str(row[1])
                for row in db.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if column not in existing:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_universe_twin_shadow_batches_epoch "
            "ON universe_twin_shadow_batches(policy_epoch_id, captured_at)"
        )
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_universe_twin_shadow_records_outcome "
            "ON universe_twin_shadow_records(outcome_candidate_key) "
            "WHERE outcome_candidate_key IS NOT NULL"
        )
        db.execute("PRAGMA optimize")
        db.execute(f"RELEASE SAVEPOINT {savepoint}")
    except sqlite3.Error:
        db.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        db.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise


def _apply_technical_snapshot_metadata_v14(db: sqlite3.Connection) -> None:
    """Persist versioned technical-snapshot identity on Universe features."""

    for column, table, column_type in _TECHNICAL_SNAPSHOT_METADATA_COLUMNS:
        existing = {
            str(row[1]) for row in db.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
    db.execute("PRAGMA optimize")


def _apply_execution_reference_v16(db: sqlite3.Connection) -> None:
    """Persist immutable pre-fill references without changing order behavior."""

    for column, table, column_type in _EXECUTION_REFERENCE_COLUMNS:
        existing = {
            str(row[1]) for row in db.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
    db.execute(_EXECUTION_REFERENCE_SQL)
    db.execute("PRAGMA optimize")


def _apply_shadow_learning_trace_v17(db: sqlite3.Connection) -> None:
    """Freeze Ranker Learning inputs on existing report-only Shadow rows."""

    for column, table, column_type in _SHADOW_LEARNING_TRACE_COLUMNS:
        existing = {
            str(row[1]) for row in db.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
    db.execute("PRAGMA optimize")


def _apply_universe_positioning_v18(db: sqlite3.Connection) -> None:
    """Persist public positioning ratios and isolated Shadow clusters."""

    for column, table, column_type in _UNIVERSE_POSITIONING_COLUMNS:
        existing = {
            str(row[1]) for row in db.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
    db.execute(_UNIVERSE_RELATIVE_RANK_SHADOW_TABLE_SQL)
    db.execute(_UNIVERSE_RELATIVE_RANK_SHADOW_INDEX_SQL)
    db.execute("PRAGMA optimize")


def _apply_serenity_retry_idempotency_v15(db: sqlite3.Connection) -> None:
    """Enforce one newly-created retry attempt per parent/count identity."""

    db.execute(_SERENITY_RETRY_IDEMPOTENCY_SQL)
    db.execute("PRAGMA optimize")


_MIGRATIONS = (
    MigrationStep(
        migration_id=_LEGACY_BASELINE_ID,
        checksum=_LEGACY_BASELINE_CHECKSUM,
        schema_version=_LEGACY_BASELINE_SCHEMA_VERSION,
        apply=_run_all_ddl,
    ),
    MigrationStep(
        migration_id="20260806_0002_startup_dataset_boundary_v3",
        checksum=_STARTUP_DATASET_BOUNDARY_CHECKSUM,
        schema_version="aletheia-sqlite-v3",
        apply=_apply_startup_dataset_boundary_v3,
    ),
    MigrationStep(
        migration_id="20260806_0003_capacity_guardrails_v4",
        checksum=_CAPACITY_GUARDRAIL_CHECKSUM,
        schema_version="aletheia-sqlite-v4",
        apply=_apply_capacity_guardrails_v4,
    ),
    MigrationStep(
        migration_id="20260806_0004_pending_outcome_partial_index_v5",
        checksum=_PENDING_OUTCOME_PARTIAL_INDEX_CHECKSUM,
        schema_version="aletheia-sqlite-v5",
        apply=_apply_pending_outcome_partial_index_v5,
    ),
    MigrationStep(
        migration_id="20260806_0005_llm_inference_linkage_v6",
        checksum=_LLM_INFERENCE_LINKAGE_CHECKSUM,
        schema_version="aletheia-sqlite-v6",
        apply=_apply_llm_inference_linkage_v6,
    ),
    MigrationStep(
        migration_id="20260807_0006_market_signal_identity_v7",
        checksum=_MARKET_SIGNAL_IDENTITY_CHECKSUM,
        schema_version="aletheia-sqlite-v7",
        apply=_apply_market_signal_identity_v7,
    ),
    MigrationStep(
        migration_id="20260808_0007_universe_twin_shadow_evidence_v8",
        checksum=_UNIVERSE_TWIN_SHADOW_EVIDENCE_CHECKSUM,
        schema_version="aletheia-sqlite-v8",
        apply=_apply_universe_twin_shadow_evidence_v8,
    ),
    MigrationStep(
        migration_id="20260808_0008_position_protection_state_v9",
        checksum=_POSITION_PROTECTION_CHECKSUM,
        schema_version="aletheia-sqlite-v9",
        apply=_apply_position_protection_state_v9,
    ),
    MigrationStep(
        migration_id="20260808_0009_learning_fixed_look_v10",
        checksum=_LEARNING_FIXED_LOOK_CHECKSUM,
        schema_version="aletheia-sqlite-v10",
        apply=_apply_learning_fixed_look_v10,
    ),
    MigrationStep(
        migration_id="20260808_0010_exit_breakeven_shadow_v11",
        checksum=_EXIT_BREAKEVEN_SHADOW_CHECKSUM,
        schema_version="aletheia-sqlite-v11",
        apply=_apply_exit_breakeven_shadow_v11,
    ),
    MigrationStep(
        migration_id="20260808_0011_learning_randomization_inference_v12",
        checksum=_LEARNING_RANDOMIZATION_INFERENCE_CHECKSUM,
        schema_version="aletheia-sqlite-v12",
        apply=_apply_learning_randomization_inference_v12,
    ),
    MigrationStep(
        migration_id="20260809_0012_universe_twin_prospective_v13",
        checksum=_UNIVERSE_TWIN_PROSPECTIVE_CHECKSUM,
        schema_version="aletheia-sqlite-v13",
        apply=_apply_universe_twin_prospective_v13,
    ),
    MigrationStep(
        migration_id="20260809_0013_technical_snapshot_metadata_v14",
        checksum=_TECHNICAL_SNAPSHOT_METADATA_CHECKSUM,
        schema_version="aletheia-sqlite-v14",
        apply=_apply_technical_snapshot_metadata_v14,
    ),
    MigrationStep(
        migration_id="20260809_0014_serenity_retry_idempotency_v15",
        checksum=_SERENITY_RETRY_IDEMPOTENCY_CHECKSUM,
        schema_version="aletheia-sqlite-v15",
        apply=_apply_serenity_retry_idempotency_v15,
    ),
    MigrationStep(
        migration_id="20260809_0015_execution_reference_v16",
        checksum=_EXECUTION_REFERENCE_CHECKSUM,
        schema_version="aletheia-sqlite-v16",
        apply=_apply_execution_reference_v16,
    ),
    MigrationStep(
        migration_id="20260810_0016_shadow_learning_trace_v17",
        checksum=_SHADOW_LEARNING_TRACE_CHECKSUM,
        schema_version="aletheia-sqlite-v17",
        apply=_apply_shadow_learning_trace_v17,
    ),
    MigrationStep(
        migration_id="20260810_0017_universe_positioning_v18",
        checksum=_UNIVERSE_POSITIONING_CHECKSUM,
        schema_version="aletheia-sqlite-v18",
        apply=_apply_universe_positioning_v18,
    ),
    MigrationStep(
        migration_id="20260810_0018_agent_duration_v19",
        checksum=_AGENT_DURATION_CHECKSUM,
        schema_version="aletheia-sqlite-v19",
        apply=_apply_agent_duration_v19,
    ),
    MigrationStep(
        migration_id="20260810_0019_agent_usage_storage_v20",
        checksum=_AGENT_USAGE_STORAGE_CHECKSUM,
        schema_version="aletheia-sqlite-v20",
        apply=_apply_agent_usage_storage_v20,
    ),
    MigrationStep(
        migration_id="20260810_0020_lesson_reviewer_attempts_v21",
        checksum=_LESSON_REVIEWER_ATTEMPTS_CHECKSUM,
        schema_version="aletheia-sqlite-v21",
        apply=_apply_lesson_reviewer_attempts_v21,
    ),
    MigrationStep(
        migration_id="20260810_0021_event_jsonl_outbox_v22",
        checksum=_EVENT_JSONL_OUTBOX_CHECKSUM,
        schema_version="aletheia-sqlite-v22",
        apply=_apply_event_jsonl_outbox_v22,
    ),
    MigrationStep(
        migration_id="20260811_0022_exit_trailing_recovery_v23",
        checksum=_EXIT_TRAILING_RECOVERY_CHECKSUM,
        schema_version="aletheia-sqlite-v23",
        apply=_apply_exit_trailing_recovery_v23,
    ),
    MigrationStep(
        migration_id="20260811_0023_learning_hypothesis_identity_v24",
        checksum=_LEARNING_HYPOTHESIS_IDENTITY_CHECKSUM,
        schema_version="aletheia-sqlite-v24",
        apply=_apply_learning_hypothesis_identity_v24,
    ),
    MigrationStep(
        migration_id="20260812_0024_ufc_canonical_identity_v25",
        checksum=_UFC_CANONICAL_IDENTITY_CHECKSUM,
        schema_version="aletheia-sqlite-v25",
        apply=_apply_ufc_canonical_identity_v25,
    ),
    MigrationStep(
        migration_id="20260813_0025_position_path_observations_v26",
        checksum=_POSITION_PATH_OBSERVATIONS_CHECKSUM,
        schema_version="aletheia-sqlite-v26",
        apply=_apply_position_path_observations_v26,
    ),
    MigrationStep(
        migration_id="20260813_0026_position_path_deferred_buckets_v27",
        checksum=_POSITION_PATH_DEFERRED_BUCKETS_CHECKSUM,
        schema_version="aletheia-sqlite-v27",
        apply=_apply_position_path_deferred_buckets_v27,
    ),
    MigrationStep(
        migration_id="20260814_0027_position_path_observer_cursor_v28",
        checksum=_POSITION_PATH_OBSERVER_CURSOR_CHECKSUM,
        schema_version="aletheia-sqlite-v28",
        apply=_apply_position_path_observer_cursor_v28,
    ),
    MigrationStep(
        migration_id="20260815_0028_learning_dreaming_backlog_v29",
        checksum=_LEARNING_DREAMING_BACKLOG_CHECKSUM,
        schema_version="aletheia-sqlite-v29",
        apply=_apply_learning_dreaming_backlog_v29,
    ),
    MigrationStep(
        migration_id="20260816_0029_position_realtime_revision_v30",
        checksum=_POSITION_REALTIME_REVISION_CHECKSUM,
        schema_version="aletheia-sqlite-v30",
        apply=_apply_position_realtime_revision_v30,
    ),
    MigrationStep(
        migration_id="20260817_0030_persona_rollout_v31",
        checksum=_PERSONA_ROLLOUT_CHECKSUM,
        schema_version="aletheia-sqlite-v31",
        apply=_apply_persona_rollout_v31,
    ),
    MigrationStep(
        migration_id="20260817_0031_exchange_upnl_identity_v32",
        checksum=_EXCHANGE_UPNL_IDENTITY_CHECKSUM,
        schema_version="aletheia-sqlite-v32",
        apply=_apply_exchange_upnl_identity_v32,
    ),
    MigrationStep(
        migration_id="20260817_0032_observation_lab_frozen_units_v33",
        checksum=_OBSERVATION_LAB_FROZEN_UNITS_CHECKSUM,
        schema_version="aletheia-sqlite-v33",
        apply=_apply_observation_lab_frozen_units_v33,
    ),
    MigrationStep(
        migration_id="20260819_0033_live_authorization_ref_v34",
        checksum=_LIVE_AUTHORIZATION_REF_CHECKSUM,
        schema_version="aletheia-sqlite-v34",
        apply=_apply_live_authorization_ref_v34,
    ),
    MigrationStep(
        migration_id="20260819_0034_momentum_short_exhaustion_shadow_v35",
        checksum=_MOMENTUM_SHORT_EXHAUSTION_CHECKSUM,
        schema_version="aletheia-sqlite-v35",
        apply=_apply_momentum_short_exhaustion_shadow_v35,
    ),
    MigrationStep(
        migration_id="20260819_0035_options_market_structure_v36",
        checksum=_OPTIONS_MARKET_STRUCTURE_CHECKSUM,
        schema_version="aletheia-sqlite-v36",
        apply=_apply_options_market_structure_v36,
    ),
)


def _legacy_baseline_implementation_checksum() -> str:
    """Hash only the frozen legacy baseline, normalized across Git line endings."""

    try:
        source = Path(__file__).read_bytes().replace(b"\r\n", b"\n")
        baseline, marker, _ = source.partition(b"# MIGRATION_LEDGER_START")
        if not marker:
            raise MigrationStateError("migration ledger source marker is missing")
        return hashlib.sha256(baseline).hexdigest()
    except OSError:
        # Frozen builds may not expose their original source. CI verifies the literal
        # against source before packaging; the signed artifact then carries that value.
        return _LEGACY_BASELINE_CHECKSUM


def _validate_migration_registry() -> None:
    seen: set[str] = set()
    previous_id: str | None = None
    for migration in _MIGRATIONS:
        if migration.migration_id in seen:
            raise MigrationStateError(
                f"duplicate migration id: {migration.migration_id}"
            )
        seen.add(migration.migration_id)
        if previous_id is not None and migration.migration_id <= previous_id:
            raise MigrationStateError(
                "migration registry must be strictly ordered by migration id"
            )
        previous_id = migration.migration_id
        if len(migration.checksum) != 64:
            raise MigrationStateError(
                f"invalid checksum for migration {migration.migration_id}"
            )
        try:
            int(migration.checksum, 16)
        except ValueError as exc:
            raise MigrationStateError(
                f"invalid checksum for migration {migration.migration_id}"
            ) from exc

    legacy = next(
        (m for m in _MIGRATIONS if m.migration_id == _LEGACY_BASELINE_ID),
        None,
    )
    if legacy and legacy.checksum != _legacy_baseline_implementation_checksum():
        raise MigrationChecksumMismatch(
            "legacy migration implementation changed without a new migration id"
        )


def _read_applied_migrations(db) -> dict[str, sqlite3.Row]:
    try:
        rows = db.execute(
            "SELECT migration_id, checksum, schema_version, applied_at, duration_ms "
            "FROM aletheia_schema_migrations ORDER BY migration_id"
        ).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table: aletheia_schema_migrations" in str(exc).lower():
            return {}
        raise
    return {str(row["migration_id"]): row for row in rows}


def _validate_applied_migrations(
    applied: dict[str, sqlite3.Row],
) -> tuple[MigrationStep, ...]:
    registered = {migration.migration_id: migration for migration in _MIGRATIONS}
    unknown = sorted(set(applied) - set(registered))
    if unknown:
        raise DatabaseSchemaAheadError(
            "database contains migrations unknown to this code: " + ", ".join(unknown)
        )

    for migration_id, row in applied.items():
        expected = registered[migration_id]
        if row["checksum"] != expected.checksum:
            raise MigrationChecksumMismatch(
                f"migration checksum mismatch for {migration_id}"
            )
        if row["schema_version"] != expected.schema_version:
            raise MigrationStateError(
                f"migration schema version mismatch for {migration_id}"
            )

    applied_ids = set(applied)
    first_pending_index = next(
        (
            index
            for index, migration in enumerate(_MIGRATIONS)
            if migration.migration_id not in applied_ids
        ),
        len(_MIGRATIONS),
    )
    out_of_order = [
        migration.migration_id
        for migration in _MIGRATIONS[first_pending_index + 1 :]
        if migration.migration_id in applied_ids
    ]
    if out_of_order:
        raise MigrationStateError(
            "migration ledger is not a contiguous prefix; applied after a gap: "
            + ", ".join(out_of_order)
        )
    return tuple(
        migration for migration in _MIGRATIONS if migration.migration_id not in applied
    )


def migration_status() -> dict:
    """Return the durable, read-only migration state for diagnostics and CLI output."""

    _validate_migration_registry()
    try:
        with readonly_db() as db:
            applied = _read_applied_migrations(db)
    except FileNotFoundError:
        applied = {}
    pending = _validate_applied_migrations(applied)
    return {
        "schema_version": SCHEMA_VERSION,
        "applied": list(applied),
        "pending": [migration.migration_id for migration in pending],
        "current": not pending,
    }


def assert_migrations_current() -> None:
    """Verify migration state without creating or mutating the database."""

    status = migration_status()
    if status["pending"]:
        raise PendingMigrationsError(
            "pending database migrations: "
            + ", ".join(status["pending"])
            + "; run `uv run aletheia data migrate` before starting the backend"
        )


def _ensure_migration_ledger(db) -> None:
    db.executescript(_MIGRATION_LEDGER_DDL)


def _record_migration_applied(
    db,
    migration: MigrationStep,
    *,
    duration_ms: float,
) -> None:
    db.execute(
        "INSERT INTO aletheia_schema_migrations "
        "(migration_id, checksum, schema_version, applied_at, duration_ms) "
        "VALUES (?, ?, ?, datetime('now'), ?)",
        (
            migration.migration_id,
            migration.checksum,
            migration.schema_version,
            max(float(duration_ms), 0.0),
        ),
    )
    db.commit()


def _apply_production_migrations(
    progress: MigrationProgressCallback | None = None,
) -> bool:
    _validate_migration_registry()
    db = get_db()
    acquire_write_lock(timeout=_MIGRATION_LOCK_TIMEOUT_SEC)
    try:
        _ensure_migration_ledger(db)
        pending = _validate_applied_migrations(_read_applied_migrations(db))
        page_size = int(db.execute("PRAGMA page_size").fetchone()[0])
        page_count = int(db.execute("PRAGMA page_count").fetchone()[0])
        database_bytes = page_size * page_count
        estimates = {
            migration.migration_id: _migration_duration_estimate(
                migration.migration_id,
                database_bytes=database_bytes,
            )
            for migration in pending
        }
        for index, migration in enumerate(pending, start=1):
            remaining_estimates = [
                estimates[item.migration_id]
                for item in pending[index - 1 :]
                if estimates[item.migration_id] is not None
            ]
            estimated_remaining = (
                (
                    sum(item[0][0] for item in remaining_estimates),
                    sum(item[0][1] for item in remaining_estimates),
                )
                if len(remaining_estimates) == len(pending[index - 1 :])
                else None
            )
            current_estimate = estimates[migration.migration_id]
            _emit_migration_progress(
                progress,
                MigrationProgressEvent(
                    phase="started",
                    migration_id=migration.migration_id,
                    description=_MIGRATION_DESCRIPTIONS.get(
                        migration.migration_id, "versioned database upgrade"
                    ),
                    step_index=index,
                    step_count=len(pending),
                    estimated_remaining_sec=estimated_remaining,
                    estimate_basis=current_estimate[1] if current_estimate else None,
                ),
            )
            started = time.perf_counter()
            migration.apply(db)
            duration_ms = (time.perf_counter() - started) * 1000.0
            _record_migration_applied(db, migration, duration_ms=duration_ms)
            future_estimates = [
                estimates[item.migration_id]
                for item in pending[index:]
                if estimates[item.migration_id] is not None
            ]
            future_remaining = (
                (
                    sum(item[0][0] for item in future_estimates),
                    sum(item[0][1] for item in future_estimates),
                )
                if len(future_estimates) == len(pending[index:])
                else None
            )
            _emit_migration_progress(
                progress,
                MigrationProgressEvent(
                    phase="completed",
                    migration_id=migration.migration_id,
                    description=_MIGRATION_DESCRIPTIONS.get(
                        migration.migration_id, "versioned database upgrade"
                    ),
                    step_index=index,
                    step_count=len(pending),
                    duration_sec=duration_ms / 1000.0,
                    estimated_remaining_sec=future_remaining,
                ),
            )
            logger.info(
                "database_migration_applied id=%s checksum=%s duration_ms=%.1f",
                migration.migration_id,
                migration.checksum[:12],
                duration_ms,
            )
        return bool(pending)
    finally:
        release_write_lock()


# In-memory blueprint used during pytest runs to avoid re-running DDL for every test.
# Populated on first call to run_migrations(); subsequent calls clone it via backup().
_blueprint: "sqlite3.Connection | None" = None


def _is_pytest() -> bool:
    import os
    return "PYTEST_CURRENT_TEST" in os.environ


def _apply_pytest_migrations() -> bool:
    db = get_db()
    global _blueprint
    if _blueprint is None:
        _blueprint = sqlite3.connect(":memory:", check_same_thread=False)
        _blueprint.row_factory = sqlite3.Row
        _run_all_ddl(_blueprint)
        _ensure_migration_ledger(_blueprint)
        _record_migration_applied(
            _blueprint,
            _MIGRATIONS[0],
            duration_ms=0.0,
        )
        for migration in _MIGRATIONS[1:]:
            migration.apply(_blueprint)
            _record_migration_applied(_blueprint, migration, duration_ms=0.0)

    # Only use backup when the destination is empty (no tables yet).
    # Tests that pre-populate data before calling run_migrations() (e.g. migration
    # backfill tests) must go through normal DDL so their existing rows are preserved.
    table_count = db.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='table'"
    ).fetchone()[0]
    if table_count == 0:
        try:
            _blueprint.backup(db)
            db.commit()
            return True
        except sqlite3.OperationalError:
            pass  # destination busy — fall through to normal DDL

    _run_all_ddl(db)
    _ensure_migration_ledger(db)
    applied = _read_applied_migrations(db)
    if _LEGACY_BASELINE_ID not in applied:
        _record_migration_applied(db, _MIGRATIONS[0], duration_ms=0.0)
        applied = _read_applied_migrations(db)
    for migration in _validate_applied_migrations(applied):
        migration.apply(db)
        _record_migration_applied(db, migration, duration_ms=0.0)
    return True


def apply_migrations(
    progress: MigrationProgressCallback | None = None,
) -> bool:
    """Apply pending migrations under one process/cross-process writer permit."""

    if _is_pytest():
        return _apply_pytest_migrations()
    return _apply_production_migrations(progress=progress)


def run_migrations() -> bool:
    """Compatibility alias for test fixtures; production owners use apply_migrations."""

    return apply_migrations()
