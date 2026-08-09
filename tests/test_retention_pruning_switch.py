"""Regression tests for the retention pruning master switch.

Directive (2026-08-09): the operator forbids automatic row deletion and
database compaction. ``retention.pruning_enabled=False`` must turn the daily
cleanup into an observation/state-transition-only job while keeping the
enabled path (default schema) byte-for-byte the historical behavior.
"""

import sqlite3
from datetime import datetime, timezone, timedelta

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations


def setup_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    return conn


def _old(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _seed_stale_rows(conn: sqlite3.Connection) -> None:
    """Rows well outside every retention window (30d snapshots, 2d heartbeats)."""
    conn.execute(
        "INSERT INTO market_snapshots (id, symbol, market_type, price, snapshot_at) "
        "VALUES (?,?,?,?,?)",
        ("snap-old", "BTC/USDT", "spot", 100.0, _old(60)),
    )
    conn.execute(
        "INSERT INTO agent_shell_heartbeats "
        "(id, agent_id, status, mode, permission_mode, created_at, last_seen_at) "
        "VALUES (?,?,?,?,?,?,?)",
        ("hb-old", "gateway", "running", "current", "Observe", _old(10), _old(10)),
    )
    conn.execute(
        "INSERT INTO opportunities (id, symbol, market, created_at, status) "
        "VALUES (?,?,?,?,?)",
        ("opp-stale", "BTC/USDT", "spot", _old(5), "open"),
    )
    conn.commit()


def _run_cleanup_with(monkeypatch, *, pruning_enabled: bool):
    from aletheia.agent_runtime import scheduler
    from aletheia.config.schema import AletheiaConfig, RetentionConfig

    monkeypatch.setattr(
        scheduler,
        "load_config",
        lambda: AletheiaConfig(retention=RetentionConfig(pruning_enabled=pruning_enabled)),
    )
    scheduler._run_cleanup()


def test_pruning_disabled_deletes_nothing(tmp_path, monkeypatch):
    conn = setup_env(tmp_path, monkeypatch)
    _seed_stale_rows(conn)

    _run_cleanup_with(monkeypatch, pruning_enabled=False)

    # Every stale row must survive — no DELETE may have run.
    assert conn.execute(
        "SELECT COUNT(*) FROM market_snapshots WHERE id='snap-old'"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM agent_shell_heartbeats WHERE id='hb-old'"
    ).fetchone()[0] == 1

    # State transitions still run: the old open opportunity is marked expired
    # (UPDATE, never DELETE).
    status = conn.execute(
        "SELECT status FROM opportunities WHERE id='opp-stale'"
    ).fetchone()[0]
    assert status == "expired"

    # The audit trail must honestly say nothing was pruned.
    audit_row = conn.execute(
        "SELECT message FROM audit_events WHERE event_type='data_pruned' "
        "ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    assert audit_row is not None
    assert "Retention pruning disabled" in audit_row[0]
    # The opportunity row itself was never deleted by the transition.
    assert conn.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0] == 1


def test_pruning_enabled_keeps_historical_behavior(tmp_path, monkeypatch):
    conn = setup_env(tmp_path, monkeypatch)
    _seed_stale_rows(conn)

    _run_cleanup_with(monkeypatch, pruning_enabled=True)

    # Historical behavior: out-of-window telemetry rows are pruned.
    assert conn.execute(
        "SELECT COUNT(*) FROM market_snapshots WHERE id='snap-old'"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM agent_shell_heartbeats WHERE id='hb-old'"
    ).fetchone()[0] == 0
    audit_row = conn.execute(
        "SELECT message FROM audit_events WHERE event_type='data_pruned' "
        "ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    assert audit_row is not None
    assert "Retention pruning disabled" not in audit_row[0]
