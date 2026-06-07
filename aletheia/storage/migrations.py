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
    status TEXT NOT NULL DEFAULT 'open',
    stop_price REAL,
    take_profit_price REAL,
    close_reason TEXT,
    opened_at TEXT NOT NULL DEFAULT (datetime('now')),
    closed_at TEXT
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
]


def _ensure_columns(db) -> None:
    for col, table, coltype in _ADDITIVE_COLUMNS:
        existing = {r["name"] for r in db.execute(f"PRAGMA table_info({table})").fetchall()}
        if col not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")


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


def run_migrations() -> None:
    """Run all idempotent schema creation statements."""
    db = get_db()
    db.executescript(SCHEMA_SQL)
    _ensure_columns(db)
    _ensure_knowledge_fts(db)
    db.commit()
