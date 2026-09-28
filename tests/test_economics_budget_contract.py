"""Economics budget configuration and storage contract.

These tests pin the dormant defaults, accepted enforcement modes and durable
``economics_budgets`` schema without relying on historical rollout-stage names.

See ``docs/design/proposals/tokenomics_harness.md`` and the dated research under
``docs/research/tokenomics/`` for design history.
"""

from __future__ import annotations

import sqlite3

import pytest

from aletheia.config.schema import AletheiaConfig, EconomicsConfig
from aletheia.economics.schema import ensure_economics_schema


# ── EconomicsConfig defaults ─────────────────────────────────────────────────


def test_economics_config_default_is_fully_dormant():
    """Every flag defaults to a value that preserves pre-tokenomics behaviour."""
    cfg = EconomicsConfig()
    assert cfg.enabled is False
    assert cfg.budgets_shadow_enabled is False
    assert cfg.enforcement_mode == "warn_only"
    assert cfg.harness_header_default_on is False


def test_economics_config_slots_into_aletheia_config():
    """AletheiaConfig() populates .economics with an EconomicsConfig default."""
    cfg = AletheiaConfig()
    assert isinstance(cfg.economics, EconomicsConfig)
    assert cfg.economics.enabled is False


def test_economics_config_accepts_valid_enforcement_modes():
    for mode in ("warn_only", "fallback_to_defaults", "hard_block"):
        cfg = EconomicsConfig(enforcement_mode=mode)
        assert cfg.enforcement_mode == mode


def test_economics_config_rejects_invalid_enforcement_mode():
    """enforcement_mode is regex-constrained; a typo raises at parse time."""
    with pytest.raises(Exception):
        EconomicsConfig(enforcement_mode="hard_block_yolo")


# ── economics_budgets table shape ────────────────────────────────────────────


def _get_columns(db: sqlite3.Connection, table: str) -> dict[str, str]:
    return {
        row[1]: row[2]
        for row in db.execute(f"PRAGMA table_info({table})").fetchall()
    }


def test_economics_budgets_table_exists_after_schema_init():
    db = ensure_economics_schema()
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='economics_budgets'"
    ).fetchone()
    assert row is not None


def test_schema_adds_budgets_to_an_existing_economics_database():
    """Existing installations with cost events also receive the budget table."""
    legacy_db = sqlite3.connect(":memory:")
    legacy_db.executescript("""
        CREATE TABLE runtime_cost_events (
            id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            run_id TEXT,
            opportunity_id TEXT,
            decision_id TEXT,
            proposal_id TEXT,
            started_at TEXT NOT NULL
        );
    """)

    ensure_economics_schema(legacy_db)

    row = legacy_db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='economics_budgets'"
    ).fetchone()
    assert row is not None


def test_economics_budgets_columns_have_expected_shape():
    db = ensure_economics_schema()
    columns = _get_columns(db, "economics_budgets")
    expected = {
        "id", "scope", "subject", "usd_cap", "window",
        "enabled", "source", "created_at", "updated_at",
    }
    assert expected.issubset(columns.keys())
    assert columns["usd_cap"] == "REAL"
    assert columns["enabled"] == "INTEGER"


def test_economics_budgets_scope_check_rejects_invalid_value():
    db = ensure_economics_schema()
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO economics_budgets "
            "(id, scope, subject, usd_cap, window, enabled, source, created_at, updated_at) "
            "VALUES ('b1','not_a_scope','x',10.0,'daily',1,'test','now','now')"
        )


def test_economics_budgets_window_check_rejects_invalid_value():
    db = ensure_economics_schema()
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO economics_budgets "
            "(id, scope, subject, usd_cap, window, enabled, source, created_at, updated_at) "
            "VALUES ('b1','system',NULL,10.0,'never',1,'test','now','now')"
        )


def test_economics_budgets_usd_cap_must_be_positive():
    db = ensure_economics_schema()
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO economics_budgets "
            "(id, scope, subject, usd_cap, window, enabled, source, created_at, updated_at) "
            "VALUES ('b1','system',NULL,0,'daily',1,'test','now','now')"
        )


def test_economics_budgets_unique_scope_subject_window():
    db = ensure_economics_schema()
    db.execute(
        "INSERT INTO economics_budgets "
        "(id, scope, subject, usd_cap, window, enabled, source, created_at, updated_at) "
        "VALUES ('b1','logical_agent','pi_trading_decision',10.0,'daily',1,'test','now','now')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO economics_budgets "
            "(id, scope, subject, usd_cap, window, enabled, source, created_at, updated_at) "
            "VALUES ('b2','logical_agent','pi_trading_decision',20.0,'daily',1,'test','now','now')"
        )


def test_economics_budgets_allows_same_subject_different_window():
    """A daily and a weekly cap on the same subject must coexist."""
    db = ensure_economics_schema()
    db.execute(
        "INSERT INTO economics_budgets "
        "(id, scope, subject, usd_cap, window, enabled, source, created_at, updated_at) "
        "VALUES ('d','logical_agent','market_observer',5.0,'daily',1,'test','now','now')"
    )
    db.execute(
        "INSERT INTO economics_budgets "
        "(id, scope, subject, usd_cap, window, enabled, source, created_at, updated_at) "
        "VALUES ('w','logical_agent','market_observer',30.0,'weekly',1,'test','now','now')"
    )
    count = db.execute(
        "SELECT COUNT(*) FROM economics_budgets WHERE subject='market_observer'"
    ).fetchone()[0]
    assert count == 2


def test_ensure_economics_schema_is_idempotent():
    """Running the installer twice produces the same shape."""
    db_a = ensure_economics_schema()
    row_before = db_a.execute(
        "SELECT COUNT(*) FROM sqlite_master "
        "WHERE type='table' AND name='economics_budgets'"
    ).fetchone()[0]
    db_b = ensure_economics_schema()
    row_after = db_b.execute(
        "SELECT COUNT(*) FROM sqlite_master "
        "WHERE type='table' AND name='economics_budgets'"
    ).fetchone()[0]
    assert row_before == row_after == 1


@pytest.mark.parametrize("table", ["economics_settings", "tool_cost_profiles", "llm_context_artifacts"])
def test_partial_schema_repairs_required_tables_without_changing_existing_rows(table):
    with sqlite3.connect(":memory:") as db:
        ensure_economics_schema(db)
        db.execute("CREATE TABLE caller_state(value TEXT)")
        db.execute("INSERT INTO caller_state VALUES ('preserved')")
        db.commit()
        db.execute(f"DROP TABLE {table}")
        ensure_economics_schema(db)
        db.execute(f"SELECT * FROM {table}").fetchall()
        assert db.execute("SELECT value FROM caller_state").fetchall() == [("preserved",)]
        before = db.execute("SELECT name,sql FROM sqlite_master ORDER BY name").fetchall()
        ensure_economics_schema(db)
        assert db.execute("SELECT name,sql FROM sqlite_master ORDER BY name").fetchall() == before


@pytest.mark.parametrize("table", ["economics_settings", "tool_cost_profiles", "llm_context_artifacts"])
def test_partial_schema_repair_refuses_to_commit_or_rollback_caller(table):
    with sqlite3.connect(":memory:") as db:
        ensure_economics_schema(db)
        db.execute("CREATE TABLE caller_state(value TEXT)")
        db.execute(f"DROP TABLE {table}")
        db.execute("INSERT INTO caller_state VALUES ('pending')")
        with pytest.raises(RuntimeError, match="active transaction"):
            ensure_economics_schema(db)
        assert db.in_transaction
        assert db.execute("SELECT value FROM caller_state").fetchall() == [("pending",)]
        db.rollback()
        assert db.execute("SELECT value FROM caller_state").fetchall() == []


def test_concurrent_first_schema_initialization_is_complete(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    path = tmp_path / "economics.db"
    barrier = Barrier(2)
    def initialize():
        with sqlite3.connect(path, timeout=30) as db:
            barrier.wait(timeout=5)
            ensure_economics_schema(db)
            return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(initialize) for _ in range(2)]
        inventories = [future.result(timeout=30) for future in futures]
    assert inventories[0] == inventories[1]
    assert {"economics_settings", "tool_cost_profiles", "llm_context_artifacts",
            "pricing_snapshots", "runtime_cost_events", "economics_budgets",
            "llm_inference_calls", "llm_structured_attempt_outcomes",
            "llm_context_pair_reviews"} <= inventories[0]


@pytest.mark.parametrize("missing", ["context_column", "tool_trigger"])
def test_schema_upgrade_keeps_caller_transaction_owned(missing):
    db = ensure_economics_schema()
    if missing == "context_column":
        db.execute("DROP INDEX idx_llm_context_artifacts_call")
        db.execute("ALTER TABLE llm_context_artifacts DROP COLUMN logical_call_id")
    else:
        db.execute("DROP TRIGGER trg_agent_tool_call_cost_event")
    db.execute("CREATE TABLE caller_state(value TEXT)")
    db.execute("BEGIN")
    db.execute("INSERT INTO caller_state VALUES ('pending')")
    with pytest.raises(RuntimeError, match="active transaction"):
        ensure_economics_schema(db)
    assert db.in_transaction
    assert db.execute("SELECT value FROM caller_state").fetchone()[0] == "pending"
    db.rollback()
    assert db.execute("SELECT COUNT(*) FROM caller_state").fetchone()[0] == 0
    ensure_economics_schema(db)
    if missing == "context_column":
        assert "logical_call_id" in {row[1] for row in db.execute("PRAGMA table_info(llm_context_artifacts)")}
    else:
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='trg_agent_tool_call_cost_event'").fetchone()


@pytest.mark.parametrize("missing", [None, "economics_settings", "tool_cost_profiles", "llm_context_artifacts"])
def test_missing_retired_viability_table_does_not_recreate_it(missing):
    with sqlite3.connect(":memory:") as db:
        ensure_economics_schema(db)
        db.execute("DROP TABLE IF EXISTS viability_reports")
        if missing:
            db.execute(f"DROP TABLE {missing}")
        ensure_economics_schema(db)
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='viability_reports'").fetchone() is None
