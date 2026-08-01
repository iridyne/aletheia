"""Tests for Runtime Session + Core Entity Provenance.

Every Gateway process lifecycle creates one Runtime Session row (session_id under a
runtime_id). The seven core trading-fact entities (opportunities, pi_decisions,
trade_proposals, risk_decisions, orders, fills, positions) carry the producing
session's identity via expand-only, nullable columns:

    runtime_session_id   — which process lifecycle produced this row
    producer_commit      — the commit that process booted on
    behavior_fingerprint — fingerprint of the behaviour-affecting config

Positions additionally carry closed_* columns so the CLOSING session (outcome
producer) is distinguishable from the OPENING session — a restart between open and
close must NOT collapse the whole position onto the last run.

Old rows (produced with no active session) stay NULL — history is never fabricated.

These tests are written before the production code (TDD).
"""

import sqlite3

import pytest

from aletheia.config.schema import AletheiaConfig
from aletheia.config.loader import save_config, _invalidate_config_cache


def setup_env(tmp_path, monkeypatch):
    """Point DATA_DIR/CONFIG_PATH at tmp and init a real migrated DB."""
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    from aletheia.storage.migrations import run_migrations

    sq.reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    sq._db_conn = conn
    run_migrations()
    _invalidate_config_cache()


@pytest.fixture(autouse=True)
def _reset_session():
    """Clear any in-process current-session state between tests."""
    from aletheia.agent_runtime import runtime_session as rs
    rs.reset_current_session()
    yield
    rs.reset_current_session()


# --------------------------------------------------------------------------- #
# 1. Fingerprints are deterministic and sensitive to the right changes
# --------------------------------------------------------------------------- #

def test_config_hash_is_deterministic_and_sensitive(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import runtime_session as rs

    cfg = AletheiaConfig()
    h1 = rs.config_hash(cfg)
    h2 = rs.config_hash(cfg)
    assert h1 == h2 and isinstance(h1, str) and h1

    cfg2 = AletheiaConfig()
    cfg2.agent.permission_mode = "Observe" if cfg.agent.permission_mode != "Observe" else "Paper"
    assert rs.config_hash(cfg2) != h1


def test_behavior_fingerprint_tracks_behaviour_config(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import runtime_session as rs

    cfg = AletheiaConfig()
    base = rs.behavior_fingerprint(cfg)
    assert base and rs.behavior_fingerprint(cfg) == base

    cfg2 = AletheiaConfig()
    cfg2.learning.paper_influence_enabled = not cfg.learning.paper_influence_enabled
    assert rs.behavior_fingerprint(cfg2) != base


# --------------------------------------------------------------------------- #
# 2. Session lifecycle: start persists a row + sets current provenance
# --------------------------------------------------------------------------- #

def test_start_session_persists_row_and_sets_current(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import runtime_session as rs
    from aletheia.storage import repositories as repo

    cfg = AletheiaConfig()
    cfg.exchange.paper_execution_mode = "paper_internal"
    save_config(cfg)
    _invalidate_config_cache()

    session = rs.start_runtime_session()
    assert session["session_id"]
    assert session["runtime_id"]
    assert session["execution_mode"] == "paper_internal"
    assert session["active_scope_id"] == "crypto-paper-mainline-v1"
    assert session["config_hash"]
    assert session["schema_version"]

    # Persisted and readable.
    row = repo.get_runtime_session(session["session_id"])
    assert row is not None
    assert row["runtime_id"] == session["runtime_id"]
    assert row["ended_at"] is None
    assert not row["shutdown_clean"]

    # current_provenance mirrors the active session.
    prov = rs.current_provenance()
    assert prov["runtime_session_id"] == session["session_id"]
    assert prov["producer_commit"] == session["boot_commit"]
    assert prov["behavior_fingerprint"] == session["behavior_fingerprint"]


def test_end_session_marks_clean_shutdown(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import runtime_session as rs
    from aletheia.storage import repositories as repo

    session = rs.start_runtime_session()
    rs.end_runtime_session(clean=True)

    row = repo.get_runtime_session(session["session_id"])
    assert row["ended_at"] is not None
    assert bool(row["shutdown_clean"]) is True
    # After end, no active session → provenance is empty.
    assert rs.current_session_id() is None


def test_distinct_sessions_share_runtime_id(tmp_path, monkeypatch):
    """A restart is a new session_id under the SAME runtime_id (version)."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import runtime_session as rs

    a = rs.start_runtime_session()
    rs.end_runtime_session(clean=True)
    b = rs.start_runtime_session()

    assert a["session_id"] != b["session_id"]
    assert a["runtime_id"] == b["runtime_id"]


# --------------------------------------------------------------------------- #
# 3. Inserts stamp provenance for all 7 entity types
# --------------------------------------------------------------------------- #

def test_insert_stamps_opportunity_provenance(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import runtime_session as rs
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Opportunity

    session = rs.start_runtime_session()
    repo.insert_opportunity(Opportunity(id="opp1", symbol="BTC/USDT", market="spot"))

    row = repo._select_one("opportunities", "opp1")
    assert row["runtime_session_id"] == session["session_id"]
    assert row["producer_commit"] == session["boot_commit"]
    assert row["behavior_fingerprint"] == session["behavior_fingerprint"]


def test_insert_stamps_position_provenance_at_open(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import runtime_session as rs
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    session = rs.start_runtime_session()
    repo.insert_position(Position(
        id="pos1", symbol="BTC/USDT", market_type="spot", direction="long",
        quantity=1.0, entry_price=100.0, execution_mode="paper_internal", status="open",
    ))
    row = repo._select_one("positions", "pos1")
    assert row["runtime_session_id"] == session["session_id"]
    assert row["producer_commit"] == session["boot_commit"]
    # Not closed yet → close-provenance is still NULL.
    assert row["closed_runtime_session_id"] is None


def test_all_seven_entities_have_provenance_columns(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.storage.sqlite import get_db

    db = get_db()
    for table in ("opportunities", "pi_decisions", "trade_proposals",
                  "risk_decisions", "orders", "fills", "positions"):
        cols = {r["name"] for r in db.execute(f"PRAGMA table_info({table})").fetchall()}
        assert {"runtime_session_id", "producer_commit", "behavior_fingerprint"} <= cols, (
            f"{table} missing provenance columns: "
            f"{ {'runtime_session_id','producer_commit','behavior_fingerprint'} - cols }"
        )
    pos_cols = {r["name"] for r in db.execute("PRAGMA table_info(positions)").fetchall()}
    assert {"closed_runtime_session_id", "closed_producer_commit",
            "closed_behavior_fingerprint"} <= pos_cols


# --------------------------------------------------------------------------- #
# 4. No active session → new rows stay NULL (never fabricate history)
# --------------------------------------------------------------------------- #

def test_no_session_leaves_provenance_null(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import runtime_session as rs
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Opportunity

    assert rs.current_session_id() is None  # no session started
    repo.insert_opportunity(Opportunity(id="legacy1", symbol="ETH/USDT", market="spot"))
    row = repo._select_one("opportunities", "legacy1")
    assert row["runtime_session_id"] is None
    assert row["producer_commit"] is None
    assert row["behavior_fingerprint"] is None


def test_explicit_provenance_is_not_overwritten(tmp_path, monkeypatch):
    """A caller that already set provenance must win over the ambient session."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import runtime_session as rs
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Opportunity

    rs.start_runtime_session()
    repo.insert_opportunity(Opportunity(
        id="opp-explicit", symbol="BTC/USDT", market="spot",
        runtime_session_id="session-preset",
    ))
    row = repo._select_one("opportunities", "opp-explicit")
    assert row["runtime_session_id"] == "session-preset"


# --------------------------------------------------------------------------- #
# 5. Restart: created session (A) vs closed session (B) are distinguishable
# --------------------------------------------------------------------------- #

def test_position_open_and_close_carry_different_sessions(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import runtime_session as rs
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    # Session A opens the position.
    a = rs.start_runtime_session()
    repo.insert_position(Position(
        id="pos-restart", symbol="BTC/USDT", market_type="spot", direction="long",
        quantity=1.0, entry_price=100.0, execution_mode="paper_internal", status="open",
    ))
    rs.end_runtime_session(clean=True)

    # Gateway restart → Session B closes the position.
    b = rs.start_runtime_session()
    assert a["session_id"] != b["session_id"]
    prov = rs.current_close_provenance()
    repo.update_position("pos-restart", status="closed", **prov)

    row = repo._select_one("positions", "pos-restart")
    assert row["runtime_session_id"] == a["session_id"]          # created by A
    assert row["closed_runtime_session_id"] == b["session_id"]   # closed by B
    assert row["runtime_session_id"] != row["closed_runtime_session_id"]


# --------------------------------------------------------------------------- #
# 6. Durability: persist failure must not create orphan durable references
# --------------------------------------------------------------------------- #

def test_persist_failure_leaves_provenance_null(tmp_path, monkeypatch):
    """If insert_runtime_session raises, child rows must NOT carry the session id."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import runtime_session as rs
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Opportunity

    # Inject a failing insert.
    def _fail_insert(session):
        raise RuntimeError("simulated DB failure")

    monkeypatch.setattr(repo, "insert_runtime_session", _fail_insert)

    session = rs.start_runtime_session()

    # In-memory session is set but marked non-durable.
    assert session["session_id"]
    assert session["durable"] is False
    assert rs.current_session_id() is not None
    assert rs.current_session_durable() is False

    # Child insert must leave runtime_session_id NULL.
    repo.insert_opportunity(Opportunity(id="opp-nodurable", symbol="BTC/USDT", market="spot"))
    row = repo._select_one("opportunities", "opp-nodurable")
    assert row["runtime_session_id"] is None, (
        "orphan durable reference stamped despite non-durable session"
    )
    # Non-durable: commit/fingerprint may still be stamped (diagnostic, not a durable id).
    # The key contract is that runtime_session_id is NULL.


def test_durable_session_stamps_runtime_session_id(tmp_path, monkeypatch):
    """Happy-path: a durably persisted session stamps runtime_session_id on child rows."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import runtime_session as rs
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Opportunity

    session = rs.start_runtime_session()
    assert session["durable"] is True
    assert rs.current_session_durable() is True

    repo.insert_opportunity(Opportunity(id="opp-durable", symbol="ETH/USDT", market="spot"))
    row = repo._select_one("opportunities", "opp-durable")
    assert row["runtime_session_id"] == session["session_id"]
    assert row["producer_commit"] == session["boot_commit"]
    assert row["behavior_fingerprint"] == session["behavior_fingerprint"]


def test_health_reports_degraded_on_persist_failure(tmp_path, monkeypatch):
    """Health check must surface runtime_session_provenance_degraded for non-durable sessions."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import runtime_session as rs
    from aletheia.storage import repositories as repo
    from aletheia.agent_runtime.health import compute_health

    def _fail_insert(session):
        raise RuntimeError("simulated DB failure")

    monkeypatch.setattr(repo, "insert_runtime_session", _fail_insert)
    rs.start_runtime_session()

    health = compute_health()
    assert "runtime_session_provenance_degraded" in health["issues"]
    assert health["healthy"] is False


def test_health_recovers_after_retry_durability(tmp_path, monkeypatch):
    """After retry_session_durability() succeeds health must no longer be degraded."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import runtime_session as rs
    from aletheia.storage import repositories as repo
    from aletheia.agent_runtime.health import compute_health

    inserted = []

    original_insert = repo.insert_runtime_session

    def _fail_first(session):
        if not inserted:
            raise RuntimeError("first attempt fails")
        original_insert(session)

    monkeypatch.setattr(repo, "insert_runtime_session", _fail_first)
    rs.start_runtime_session()
    assert rs.current_session_durable() is False

    # Re-enable inserts for the retry.
    inserted.append(True)
    monkeypatch.setattr(repo, "insert_runtime_session", original_insert)

    recovered = rs.retry_session_durability()
    assert recovered is True
    assert rs.current_session_durable() is True

    health = compute_health()
    assert "runtime_session_provenance_degraded" not in health["issues"]


def test_close_provenance_null_when_non_durable(tmp_path, monkeypatch):
    """closed_runtime_session_id must be NULL when the closing session is non-durable."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import runtime_session as rs
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    # Session A (durable) opens the position.
    a = rs.start_runtime_session()
    assert a["durable"] is True
    repo.insert_position(Position(
        id="pos-close-nondurable", symbol="BTC/USDT", market_type="spot", direction="long",
        quantity=1.0, entry_price=100.0, execution_mode="paper_internal", status="open",
    ))
    rs.end_runtime_session(clean=True)

    # Session B (non-durable) closes the position.
    def _fail_insert(session):
        raise RuntimeError("simulated DB failure")
    monkeypatch.setattr(repo, "insert_runtime_session", _fail_insert)
    b = rs.start_runtime_session()
    assert b["durable"] is False

    prov = rs.current_close_provenance()
    assert prov["closed_runtime_session_id"] is None
    repo.update_position("pos-close-nondurable", status="closed", **prov)

    row = repo._select_one("positions", "pos-close-nondurable")
    assert row["runtime_session_id"] == a["session_id"]     # open provenance intact
    assert row["closed_runtime_session_id"] is None          # no orphan close reference


def test_orphan_scan_finds_orphaned_ids(tmp_path, monkeypatch):
    """scan_orphan_session_ids must detect runtime_session_ids with no parent row."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.storage import repositories as repo
    from aletheia.storage.sqlite import get_db

    # Directly inject a row with a fake session id.
    db = get_db()
    db.execute(
        "INSERT INTO opportunities (id, symbol, market, runtime_session_id) VALUES (?, ?, ?, ?)",
        ("opp-orphan", "BTC/USDT", "spot", "ghost-session-000"),
    )
    db.commit()

    result = repo.scan_orphan_session_ids()
    assert result["total_orphan_refs"] >= 1
    assert "ghost-session-000" in result["orphan_session_ids"]
    assert "opportunities" in result["by_table"]


def test_orphan_scan_empty_when_no_orphans(tmp_path, monkeypatch):
    """scan_orphan_session_ids returns zero totals when all provenance is clean."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import runtime_session as rs
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Opportunity

    rs.start_runtime_session()
    repo.insert_opportunity(Opportunity(id="opp-clean", symbol="ETH/USDT", market="spot"))

    result = repo.scan_orphan_session_ids()
    assert result["total_orphan_refs"] == 0
    assert result["orphan_session_ids"] == []


def test_retry_durability_idempotent_no_fingerprint_change(tmp_path, monkeypatch):
    """retry_session_durability() must not bind a different fingerprint to the same session id."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import runtime_session as rs
    from aletheia.storage import repositories as repo

    # Start a durable session.
    session = rs.start_runtime_session()
    assert session["durable"] is True
    assert rs.current_session_durable() is True

    # Calling retry on an already-durable session must be a no-op that returns True.
    result = rs.retry_session_durability()
    assert result is True
    assert rs.current_session_durable() is True

    # The DB row is unchanged.
    row = repo.get_runtime_session(session["session_id"])
    assert row["behavior_fingerprint"] == session["behavior_fingerprint"]


def test_verification_fails_on_fingerprint_mismatch(tmp_path, monkeypatch):
    """If the persisted row has a different fingerprint verification must fail (non-durable)."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime import runtime_session as rs
    from aletheia.storage import repositories as repo
    from aletheia.storage.sqlite import get_db

    # Let insert succeed normally.
    session = rs.start_runtime_session()
    assert session["durable"] is True

    # Tamper: corrupt the fingerprint in the DB and reset durable state to simulate
    # a hypothetical mismatch scenario via retry_session_durability.
    db = get_db()
    db.execute(
        "UPDATE runtime_sessions SET behavior_fingerprint = 'tampered' WHERE session_id = ?",
        (session["session_id"],),
    )
    db.commit()

    # Force retry to re-verify.
    import aletheia.agent_runtime.runtime_session as rs_mod
    rs_mod._session_durable = False

    result = rs.retry_session_durability()
    assert result is False
    assert rs.current_session_durable() is False
