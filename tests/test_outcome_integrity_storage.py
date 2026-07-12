"""Focused persistence tests for the outcome-integrity evidence layer."""

from __future__ import annotations

import sqlite3

import pytest


def test_position_outcome_defaults_fail_closed_and_round_trip():
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    position = Position(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=50_000.0,
        execution_mode="paper_internal",
    )

    assert position.close_category == "unknown"
    assert position.exit_price_source == "unavailable"
    assert position.exit_fill_verified is False
    assert position.outcome_eligible is False
    assert position.outcome_exclusion_reason == "unclassified"
    assert position.reconciliation_identity is None
    assert position.reconciliation_evidence is None
    assert position.outcome_semantics_version is None

    repo.insert_position(position)
    stored = repo._select_one("positions", position.id)
    assert stored is not None
    assert stored["close_category"] == "unknown"
    assert stored["exit_price_source"] == "unavailable"
    assert stored["exit_fill_verified"] == 0
    assert stored["outcome_eligible"] == 0
    assert stored["outcome_exclusion_reason"] == "unclassified"
    assert stored["reconciliation_identity"] is None
    assert stored["reconciliation_evidence"] is None
    assert stored["outcome_semantics_version"] is None

    repo.update_position(
        position.id,
        close_category="strategy",
        exit_price_source="broker_fill",
        exit_fill_verified=True,
        outcome_eligible=True,
        outcome_exclusion_reason="eligible",
        reconciliation_identity='{"broker_id":"paper_internal"}',
        reconciliation_evidence='{"fill_id":"fill-1"}',
        outcome_semantics_version="oi-v1",
    )
    verified = repo._select_one("positions", position.id)
    assert verified is not None
    assert verified["close_category"] == "strategy"
    assert verified["exit_price_source"] == "broker_fill"
    assert verified["exit_fill_verified"] == 1
    assert verified["outcome_eligible"] == 1
    assert verified["outcome_exclusion_reason"] == "eligible"
    assert verified["outcome_semantics_version"] == "oi-v1"


def test_disappearance_candidate_and_observation_round_trip_is_idempotent():
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import (
        Position,
        PositionDisappearanceCandidate,
        PositionDisappearanceObservation,
    )

    position = Position(
        symbol="ETH/USDT",
        market_type="usdt_futures",
        direction="short",
        quantity=0.2,
        entry_price=3_000.0,
        execution_mode="okx_demo",
    )
    repo.insert_position(position)

    candidate = PositionDisappearanceCandidate(
        position_id=position.id,
        identity_key="sha256:account-instrument-direction",
        broker_id="okx_demo",
        account_environment="demo",
        account_fingerprint="sha256:non-secret-account-id",
        execution_mode="okx_demo",
        symbol="ETH/USDT",
        market_type="usdt_futures",
        direction="short",
        first_observed_at="2026-07-12T05:00:00+00:00",
        last_observed_at="2026-07-12T05:00:00+00:00",
        semantics_version="oi-v1",
    )
    repo.insert_position_disappearance_candidate(candidate)

    active = repo.get_active_position_disappearance_candidate(candidate.identity_key)
    assert active is not None
    assert active["id"] == candidate.id
    assert active["confirmation_count"] == 1
    assert active["direct_query_state"] == "not_attempted"

    observation = PositionDisappearanceObservation(
        candidate_id=candidate.id,
        observation_hash="sha256:view-1",
        view_identity="okx_demo:demo:sequence-1",
        view_complete=True,
        position_absent=True,
        observed_at="2026-07-12T05:00:00+00:00",
        evidence_json='{"source":"unified_cache"}',
    )
    assert repo.insert_position_disappearance_observation(observation) is True
    assert repo.insert_position_disappearance_observation(observation) is False

    rows = repo.list_position_disappearance_observations(candidate.id)
    assert len(rows) == 1
    assert rows[0]["observation_hash"] == "sha256:view-1"
    assert rows[0]["view_complete"] == 1
    assert rows[0]["position_absent"] == 1

    # One active candidate per full scoped identity. Resolving it permits a later
    # independent incident without deleting the prior candidate/evidence.
    duplicate = candidate.model_copy(update={"id": "candidate-duplicate"})
    with pytest.raises(sqlite3.IntegrityError):
        repo.insert_position_disappearance_candidate(duplicate)
    repo.update_position_disappearance_candidate(
        candidate.id,
        status="resolved",
        resolved_at="2026-07-12T05:05:00+00:00",
    )
    later = candidate.model_copy(
        update={
            "id": "candidate-later",
            "first_observed_at": "2026-07-12T06:00:00+00:00",
            "last_observed_at": "2026-07-12T06:00:00+00:00",
        }
    )
    repo.insert_position_disappearance_candidate(later)
    assert (
        repo.get_active_position_disappearance_candidate(candidate.identity_key)["id"]
        == "candidate-later"
    )


def test_legacy_position_migration_preserves_original_economics(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    sq._db_conn = conn

    conn.execute(
        "CREATE TABLE positions ("
        "id TEXT PRIMARY KEY, symbol TEXT NOT NULL, market_type TEXT NOT NULL, "
        "direction TEXT NOT NULL, quantity REAL NOT NULL, entry_price REAL NOT NULL, "
        "current_price REAL, unrealized_pnl REAL DEFAULT 0, realized_pnl REAL DEFAULT 0, "
        "execution_mode TEXT NOT NULL, strategy_id TEXT, status TEXT NOT NULL DEFAULT 'open', "
        "close_reason TEXT, entry_fee REAL, exit_fee REAL, slippage_cost REAL, "
        "opened_at TEXT, closed_at TEXT)"
    )
    original = (
        "legacy-1", "BTC/USDT", "usdt_futures", "long", 0.25, 40_000.0,
        41_000.0, 250.0, -17.25, "paper_internal", "closed",
        "reconciled_gone", 0.25, 0.4, 0.15, "2026-07-10T00:00:00+00:00",
        "2026-07-10T01:00:00+00:00",
    )
    conn.execute(
        "INSERT INTO positions "
        "(id,symbol,market_type,direction,quantity,entry_price,current_price,"
        "unrealized_pnl,realized_pnl,execution_mode,status,close_reason,entry_fee,"
        "exit_fee,slippage_cost,opened_at,closed_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        original,
    )
    conn.commit()

    run_migrations()
    run_migrations()

    row = conn.execute("SELECT * FROM positions WHERE id='legacy-1'").fetchone()
    assert tuple(row[name] for name in (
        "id", "symbol", "market_type", "direction", "quantity", "entry_price",
        "current_price", "unrealized_pnl", "realized_pnl", "execution_mode",
        "status", "close_reason", "entry_fee", "exit_fee", "slippage_cost",
        "opened_at", "closed_at",
    )) == original
    assert row["close_category"] == "unknown"
    assert row["exit_price_source"] == "unavailable"
    assert row["exit_fill_verified"] == 0
    assert row["outcome_eligible"] == 0
    assert row["outcome_exclusion_reason"] == "unclassified"
    assert row["outcome_semantics_version"] is None

    tables = {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "position_disappearance_candidates" in tables
    assert "position_disappearance_observations" in tables


def test_disappearance_evidence_tables_are_not_prunable():
    from aletheia.storage.repositories import _PRUNABLE, _delete_old_rows

    protected = {
        "position_disappearance_candidates",
        "position_disappearance_observations",
        "reconciliation_integrity_incidents",
    }
    assert protected.isdisjoint(_PRUNABLE)
    for table in protected:
        with pytest.raises(ValueError):
            _delete_old_rows(table, days=1)


def test_outcome_integrity_schema_batch_rolls_back_on_failure(monkeypatch):
    from aletheia.storage import migrations

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE positions (id TEXT PRIMARY KEY, symbol TEXT NOT NULL)"
    )
    original_ddl = migrations._OUTCOME_INTEGRITY_DDL
    monkeypatch.setattr(
        migrations,
        "_OUTCOME_INTEGRITY_DDL",
        (*original_ddl, "CREATE TABLE deliberately_invalid ("),
    )

    with pytest.raises(sqlite3.Error):
        migrations._ensure_outcome_integrity_schema(conn)

    columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(positions)").fetchall()
    }
    assert "close_category" not in columns
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "position_disappearance_candidates" not in tables
    assert "position_disappearance_observations" not in tables
