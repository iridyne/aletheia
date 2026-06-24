"""Tests for data retention / cleanup (Component 3).

Invariant: NEVER hard-delete trust-bearing records (audit, trade_proposals,
risk_decisions, orders, fills, positions, knowledge_items). Only high-volume
telemetry is pruned. JSONL rotates at a size cap without losing content.
"""

import sqlite3
from datetime import datetime, timezone, timedelta

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage import repositories as repo


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


def _old(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _recent():
    return datetime.now(timezone.utc).isoformat()


def test_prune_agent_runs_keeps_recent_deletes_old(tmp_path, monkeypatch):
    conn = setup_env(tmp_path, monkeypatch)
    conn.execute("INSERT INTO agent_runs (id, agent_name, permission_mode, started_at, status) VALUES (?,?,?,?,?)",
                 ("old1", "market_observer", "Observe", _old(60), "completed"))
    conn.execute("INSERT INTO agent_runs (id, agent_name, permission_mode, started_at, status) VALUES (?,?,?,?,?)",
                 ("new1", "market_observer", "Observe", _recent(), "completed"))
    conn.commit()

    deleted = repo.prune_agent_runs(days=30)
    assert deleted == 1
    ids = {r["id"] for r in repo.list_agent_runs(limit=100)}
    assert "new1" in ids and "old1" not in ids


def test_prune_market_snapshots(tmp_path, monkeypatch):
    conn = setup_env(tmp_path, monkeypatch)
    conn.execute("INSERT INTO market_snapshots (id, symbol, market_type, price, snapshot_at) VALUES (?,?,?,?,?)",
                 ("o", "BTCUSDT", "spot", 1.0, _old(60)))
    conn.execute("INSERT INTO market_snapshots (id, symbol, market_type, price, snapshot_at) VALUES (?,?,?,?,?)",
                 ("n", "BTCUSDT", "spot", 1.0, _recent()))
    conn.commit()
    assert repo.prune_market_snapshots(days=30) == 1


def test_cleanup_never_touches_causal_chain(tmp_path, monkeypatch):
    conn = setup_env(tmp_path, monkeypatch)
    # Insert old trust-bearing rows; they must survive any cleanup.
    conn.execute("INSERT INTO trade_proposals (id, symbol, market_type, direction, created_at) VALUES (?,?,?,?,?)",
                 ("prop_old", "BTCUSDT", "spot", "long", _old(999)))
    conn.execute("INSERT INTO audit_events (id, timestamp, actor, source, event_type) VALUES (?,?,?,?,?)",
                 ("aud_old", _old(999), "system", "scheduler", "x"))
    conn.commit()

    from aletheia.agent_runtime.scheduler import _run_cleanup
    _run_cleanup()

    assert repo.get_trade_proposal("prop_old") is not None  # proposal survives
    audits = {r["id"] for r in repo.list_audit_events(limit=1000)}
    assert "aud_old" in audits  # audit survives
    # there is no prune function for the causal-chain tables
    assert not hasattr(repo, "prune_trade_proposals")
    assert not hasattr(repo, "prune_orders")
    assert not hasattr(repo, "prune_positions")


def test_cleanup_writes_audit(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime.scheduler import _run_cleanup
    _run_cleanup()
    from aletheia.files.jsonl import read_jsonl
    assert any(e.get("event_type") == "data_pruned" for e in read_jsonl("audit.jsonl"))


def test_prune_pi_decisions_keeps_decisions_deletes_transient(tmp_path, monkeypatch):
    """prune_pi_decisions removes old transient rows (signal_pending/skip/watch)
    but NEVER touches decision-bearing rows (trade/close/recommend_promote) —
    they are the PI Agent's audit trail. Regression guard for the PT-1 cleanup."""
    conn = setup_env(tmp_path, monkeypatch)
    rows = [
        ("sp_old", "signal_pending", _old(30)),   # transient, old → delete
        ("skip_old", "skip", _old(30)),            # transient, old → delete
        ("watch_old", "watch", _old(30)),          # transient, old → delete
        ("sp_new", "signal_pending", _recent()),   # transient, recent → keep
        ("trade_old", "trade", _old(30)),          # decision, old → KEEP
        ("close_old", "close", _old(30)),          # decision, old → KEEP
        ("promote_old", "recommend_promote", _old(30)),  # decision, old → KEEP
    ]
    for rid, action, ts in rows:
        conn.execute(
            "INSERT INTO pi_decisions (id, action, created_at, conviction, debate_ran) "
            "VALUES (?,?,?,0,0)", (rid, action, ts),
        )
    conn.commit()

    deleted = repo.prune_pi_decisions(7)
    assert deleted == 3, f"expected 3 transient rows deleted, got {deleted}"

    remaining = {r["id"] for r in conn.execute("SELECT id FROM pi_decisions").fetchall()}
    assert {"trade_old", "close_old", "promote_old"} <= remaining, (
        "prune_pi_decisions deleted a decision-bearing row — must keep trade/close/recommend_promote"
    )
    assert "sp_new" in remaining, "recent signal_pending wrongly deleted"
    assert not ({"sp_old", "skip_old", "watch_old"} & remaining), "old transient rows not deleted"


def test_jsonl_rotation(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    import aletheia.files.jsonl as jsonl
    monkeypatch.setattr(jsonl, "_max_bytes", lambda: 200)  # tiny cap for test
    for i in range(50):
        jsonl.append_jsonl("test.jsonl", {"i": i, "pad": "x" * 20})
    logs = tmp_path / "logs"
    assert (logs / "test.jsonl").exists()
    assert (logs / "test.jsonl.1").exists()  # rotated generation exists
