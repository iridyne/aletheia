"""Idempotent SQLite schema migrations."""

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
    source TEXT NOT NULL DEFAULT 'native',
    opened_at TEXT NOT NULL DEFAULT (datetime('now')),
    closed_at TEXT
);

CREATE TABLE IF NOT EXISTS shadow_trades (
    id TEXT PRIMARY KEY,
    batch_id TEXT,
    opportunity_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    scanner_family TEXT,
    trade_score REAL NOT NULL DEFAULT -999,
    rank INTEGER NOT NULL DEFAULT 0,
    entry_price REAL,
    regime_tag TEXT,
    was_selected INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    exit_price REAL,
    pnl_pct REAL,
    btc_return_pct REAL,
    resolved_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_shadow_trades_batch ON shadow_trades(batch_id);
CREATE INDEX IF NOT EXISTS idx_shadow_trades_opportunity ON shadow_trades(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_shadow_trades_family ON shadow_trades(scanner_family);

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
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    ts INTEGER NOT NULL,        -- unix milliseconds (open time of bar)
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    provider TEXT NOT NULL DEFAULT 'ccxt',
    PRIMARY KEY (symbol, timeframe, ts)
);

CREATE INDEX IF NOT EXISTS idx_market_ohlcv_sym_tf_ts
    ON market_ohlcv (symbol, timeframe, ts DESC);

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
    applied_policy_version TEXT
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
    member_id TEXT NOT NULL REFERENCES universe_members(id),
    snapshot_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    scanner_family TEXT,
    direction TEXT,
    horizon TEXT NOT NULL,
    entry_price_usd REAL NOT NULL,
    entry_ts TEXT NOT NULL,
    due_at TEXT NOT NULL,
    settled_at TEXT,
    exit_price_usd REAL,
    forward_return_pct REAL,
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
-- Data Collectors Layer (9-domain market data)
-- ====================================================================

CREATE TABLE IF NOT EXISTS orderbook_snapshots (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
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
_ADDITIVE_COLUMNS = [
    ("leverage", "trade_proposals", "INTEGER"),
    ("stop_price", "trade_proposals", "REAL"),
    ("strategy_id", "knowledge_items", "TEXT"),
    ("stop_price", "positions", "REAL"),
    ("take_profit_price", "positions", "REAL"),
    ("close_reason", "positions", "TEXT"),
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
    # Multi-asset class support (2026-06-08): "crypto" | "equity" | "prediction"
    ("asset_class", "opportunities", "TEXT"),
    # Exchange-aware instrument identity (2026-06-08): which exchange supplied the data.
    # Allows Binance spot BTC/USDT and Bybit spot BTC/USDT to coexist without overwriting.
    ("exchange", "market_snapshots", "TEXT"),
    # exchange column on OHLCV (supplement to provider — same value, explicit naming).
    # Primary key stays (symbol, timeframe, ts); exchange stored for query filtering only.
    ("exchange", "market_ohlcv", "TEXT"),
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
    # Knowledge dedup: SHA-256 prefix of content for write-time duplicate detection.
    ("content_hash", "knowledge_items", "TEXT"),
    # US Equity integration: sub-category and canonical ticker on opportunities.
    # asset_subclass: "equity_daily" (yfinance daily bars primary) | "".
    # canonical_ticker: Yahoo Finance ticker for cross-path correlation (e.g. "AAPL").
    ("asset_subclass", "opportunities", "TEXT NOT NULL DEFAULT ''"),
    ("canonical_ticker", "opportunities", "TEXT NOT NULL DEFAULT ''"),
    # Futures integration: market type (spot/perp) carried through the member.
    ("market_type", "universe_members", "TEXT NOT NULL DEFAULT 'spot'"),
]


def _ensure_columns(db) -> None:
    import sqlite3
    for col, table, coltype in _ADDITIVE_COLUMNS:
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
        "confidence UNINDEXED, created_at UNINDEXED)"
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
CREATE INDEX IF NOT EXISTS idx_orders_proposal_id ON orders(proposal_id);
CREATE INDEX IF NOT EXISTS idx_fills_order_id ON fills(order_id);
CREATE INDEX IF NOT EXISTS idx_risk_decisions_proposal_id ON risk_decisions(proposal_id);
CREATE INDEX IF NOT EXISTS idx_positions_strategy_id ON positions(strategy_id);
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
CREATE INDEX IF NOT EXISTS idx_knowledge_items_type_status ON knowledge_items(item_type, status);
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


def _run_all_ddl(db) -> None:
    """Execute every DDL statement against db (used for both real and blueprint DBs)."""
    db.executescript(SCHEMA_SQL)
    db.executescript(_DATA_COLLECTORS_DDL)
    db.executescript(_PREDICTION_OBSERVER_RUNS_DDL)
    db.executescript(_PREDICTION_NORMALIZED_DDL)
    db.executescript(_STRATEGY_BACKTEST_DDL)
    _ensure_columns(db)
    _ensure_runtime_baseline(db)
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
