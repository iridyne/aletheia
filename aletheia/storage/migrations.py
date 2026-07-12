"""Idempotent SQLite schema migrations."""

import sqlite3

from aletheia.storage.sqlite import get_db

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

CREATE TABLE IF NOT EXISTS risk_decisions (
    id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL REFERENCES trade_proposals(id),
    approved INTEGER NOT NULL DEFAULT 0,
    risk_score REAL NOT NULL DEFAULT 0,
    violations TEXT DEFAULT '[]',
    allowed_execution_mode TEXT,
    proposal_execution_mode TEXT,
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
    close_category TEXT NOT NULL DEFAULT 'unknown',
    exit_price_source TEXT NOT NULL DEFAULT 'unavailable',
    exit_fill_verified INTEGER NOT NULL DEFAULT 0,
    outcome_eligible INTEGER NOT NULL DEFAULT 0,
    outcome_exclusion_reason TEXT NOT NULL DEFAULT 'unclassified',
    reconciliation_identity TEXT,
    reconciliation_evidence TEXT,
    outcome_semantics_version TEXT,
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
    return_1h REAL,
    return_4h REAL,
    return_24h REAL,
    funding_rate REAL,
    open_interest REAL,
    oi_delta_1h REAL,
    data_freshness_sec INTEGER,
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
    as_of_time TEXT NOT NULL,
    observed_at TEXT NOT NULL DEFAULT (datetime('now')),
    data_quality TEXT NOT NULL DEFAULT 'unknown',
    coverage_note TEXT,
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
    ("exit_price_source", "positions", "TEXT NOT NULL DEFAULT 'unavailable'"),
    ("exit_fill_verified", "positions", "INTEGER NOT NULL DEFAULT 0"),
    ("outcome_eligible", "positions", "INTEGER NOT NULL DEFAULT 0"),
    ("outcome_exclusion_reason", "positions", "TEXT NOT NULL DEFAULT 'unclassified'"),
    ("reconciliation_identity", "positions", "TEXT"),
    ("reconciliation_evidence", "positions", "TEXT"),
    ("outcome_semantics_version", "positions", "TEXT"),
)

_OUTCOME_INTEGRITY_DDL = (
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
)

_ADDITIVE_COLUMNS = [
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
    ("stop_price", "trade_proposals", "REAL"),
    # CR-RE-01: preserve the proposal's original execution_mode on the decision row
    # (allowed_execution_mode gets downgraded to paper_internal on rejection).
    ("proposal_execution_mode", "risk_decisions", "TEXT"),
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
    # docs/DECISIONS.md (2026-06-12) and position_reconciler.adopt_position.
    ("source", "positions", "TEXT DEFAULT 'native'"),
    ("source", "orders", "TEXT DEFAULT 'native'"),
    ("source", "trade_proposals", "TEXT DEFAULT 'native'"),
    # Numeric calibration the LLM emits alongside the categorical confidence.
    # 0..100; NULL means the model didn't supply one (older rows or bad output).
    ("confidence_score", "opportunities", "INTEGER"),
    # Real exchange equity at snapshot time; NULL when exchange is unconfigured/stale.
    ("exchange_equity_usdt", "portfolio_snapshots", "REAL"),
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
]


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
CREATE INDEX IF NOT EXISTS idx_fills_order_id ON fills(order_id);
CREATE INDEX IF NOT EXISTS idx_risk_decisions_proposal_id ON risk_decisions(proposal_id);
CREATE INDEX IF NOT EXISTS idx_positions_strategy_id ON positions(strategy_id);
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
CREATE INDEX IF NOT EXISTS idx_knowledge_items_type_status ON knowledge_items(item_type, status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_news_dedup ON news_items(dedup_key);
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
    action TEXT NOT NULL DEFAULT 'skip',
    conviction INTEGER NOT NULL DEFAULT 0,
    reasoning TEXT DEFAULT '',
    proposal_id TEXT,
    debate_ran INTEGER NOT NULL DEFAULT 0,
    debate_initial_conviction INTEGER,
    debate_bull_confidence INTEGER,
    debate_bear_confidence INTEGER,
    debate_adjustment INTEGER,
    opportunity_id TEXT,
    learning_manifest_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_pi_decisions_symbol ON pi_decisions(symbol, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pi_decisions_created ON pi_decisions(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pi_decisions_opportunity ON pi_decisions(opportunity_id);
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
    code_commit TEXT,
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

def _run_all_ddl(db) -> None:
    """Execute every DDL statement against db (used for both real and blueprint DBs)."""
    db.executescript(SCHEMA_SQL)
    db.executescript(_DATA_COLLECTORS_DDL)
    db.executescript(_PREDICTION_OBSERVER_RUNS_DDL)
    db.executescript(_PREDICTION_NORMALIZED_DDL)
    db.executescript(_STRATEGY_BACKTEST_DDL)
    db.executescript(_MARKET_REGIME_DDL)
    db.executescript(_PI_DECISIONS_DDL)
    db.executescript(_CAPITAL_EVENTS_DDL)
    db.executescript(_PAPER_ACCOUNT_EVENTS_DDL)
    db.executescript(_VOLUME_RANKING_DDL)
    db.executescript(_OHLCV_BACKFILL_STATE_DDL)
    db.executescript(_COLLECTOR_HEALTH_STATE_DDL)
    db.executescript(_CONTROLLER_RECOMMENDATIONS_DDL)
    db.executescript(_FILTER_SHADOW_EXPERIMENTS_DDL)
    db.executescript(_LEARNING_LEDGER_DDL)
    _ensure_columns(db)
    _ensure_outcome_integrity_schema(db)
    _ensure_market_ohlcv_instrument_identity(db)
    _ensure_orderbook_instrument_index(db)
    _ensure_filter_evidence_indexes(db)
    _backfill_forward_outcome_instrument_identity(db)
    _ensure_runtime_baseline(db)
    _ensure_knowledge_fts_outcome_column(db)
    _ensure_knowledge_fts(db)
    db.executescript(_CORE_INDEXES_SQL)
    db.commit()


# In-memory blueprint used during pytest runs to avoid re-running DDL for every test.
# Populated on first call to run_migrations(); subsequent calls clone it via backup().
_blueprint: "sqlite3.Connection | None" = None


def _is_pytest() -> bool:
    import os
    return "PYTEST_CURRENT_TEST" in os.environ


def run_migrations() -> None:
    """Run all idempotent schema creation statements.

    In pytest: builds a shared in-memory blueprint on first call, then clones it into
    the current test connection via backup() — skipping 80+ CREATE TABLE statements on
    every subsequent call. Production paths (no PYTEST_CURRENT_TEST) always run DDL.
    """
    import sqlite3

    db = get_db()

    if not _is_pytest():
        _run_all_ddl(db)
        return

    global _blueprint
    if _blueprint is None:
        _blueprint = sqlite3.connect(":memory:", check_same_thread=False)
        _blueprint.row_factory = sqlite3.Row
        _run_all_ddl(_blueprint)

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
            return
        except sqlite3.OperationalError:
            pass  # destination busy — fall through to normal DDL

    _run_all_ddl(db)
