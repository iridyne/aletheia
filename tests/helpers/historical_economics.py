"""Archived economics schema used only to exercise retained history readers."""

VIABILITY_REPORTS_DDL = """
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
