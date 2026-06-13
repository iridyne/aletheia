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


def run_migrations() -> None:
    """Run all idempotent schema creation statements."""
    db = get_db()
    db.executescript(SCHEMA_SQL)
    _ensure_columns(db)
    _ensure_runtime_baseline(db)
    _ensure_knowledge_fts(db)
    db.commit()
