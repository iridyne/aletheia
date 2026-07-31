"""Phase 2A scaffold: EconomicsConfig defaults and economics_budgets DDL.

Phase 2A is a passive scaffold — the config class is inert (no consumer
reads it yet) and the table exists but has no writer. These tests fix
the shape so Phase 2B can rely on it without re-negotiating field
names or constraint semantics.

See docs/design/proposals/tokenomics_harness_phase_plan_2026-07-30.md
§4 Phase 2 and docs/research/tokenomics/phase2_design_decisions_2026-07-31.md
for the design context.
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


def test_economics_budgets_columns_have_expected_shape():
    db = ensure_economics_schema()
    columns = _get_columns(db, "economics_budgets")
    expected = {
        "id", "scope", "subject", "usd_cap", "window",
        "enabled", "source", "created_at", "updated_at",
    }
    assert expected.issubset(columns.keys())
    # Numeric columns match the DDL intent.
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
