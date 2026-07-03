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


def test_prune_universe_forward_outcomes(tmp_path, monkeypatch):
    """Forward-return labels are analytics (not causal-chain) and reference
    snapshots pruned at 30d, so old rows are orphaned. Guards DB-SIZE gap #1:
    the table had no prune path and grew unbounded."""
    conn = setup_env(tmp_path, monkeypatch)
    cols = ("(id, member_id, snapshot_id, symbol, horizon, "
            "entry_price_usd, entry_ts, due_at, status)")
    conn.execute(f"INSERT INTO universe_forward_outcomes {cols} VALUES (?,?,?,?,?,?,?,?,?)",
                 ("o", "m1", "s1", "BTC/USDT", "1d", 1.0, _old(60), _old(59), "settled"))
    conn.execute(f"INSERT INTO universe_forward_outcomes {cols} VALUES (?,?,?,?,?,?,?,?,?)",
                 ("n", "m1", "s2", "BTC/USDT", "1d", 1.0, _recent(), _recent(), "pending"))
    conn.commit()
    assert repo.prune_universe_forward_outcomes(days=30) == 1
    remaining = {r["id"] for r in conn.execute("SELECT id FROM universe_forward_outcomes").fetchall()}
    assert remaining == {"n"}, "prune must drop only outcomes older than the window"


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
    they are the System Operator's audit trail. Regression guard for the PT-1 cleanup."""
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


def test_delete_old_rows_requires_condition_for_pi_decisions(tmp_path, monkeypatch):
    """pi_decisions mixes transient queue rows with causal decisions.

    The shared delete helper may prune it only when the caller supplies an
    explicit action filter; a bare old-row delete would erase decision history.
    """
    setup_env(tmp_path, monkeypatch)

    try:
        repo._delete_old_rows("pi_decisions", days=7)
    except ValueError as exc:
        assert "not prunable" in str(exc)
    else:
        raise AssertionError("pi_decisions must not be bare-prunable")


def test_prune_agent_shell_heartbeats_keeps_recent_deletes_old(tmp_path, monkeypatch):
    """agent_shell_heartbeats is pure liveness telemetry (read latest-row-only)
    and was the dominant app.db bloat (~199k rows / ~85 MB by 2026-06-27). It
    had no prune path; this guards the DB-SIZE-1 retention. Old rows go, the
    latest row stays so get_latest_heartbeat still works."""
    conn = setup_env(tmp_path, monkeypatch)
    cols = "(id, agent_id, status, mode, permission_mode, last_seen_at, created_at)"
    conn.execute(f"INSERT INTO agent_shell_heartbeats {cols} VALUES (?,?,?,?,?,?,?)",
                 ("hb_old", "operator", "alive", "current", "Full", _old(10), _old(10)))
    conn.execute(f"INSERT INTO agent_shell_heartbeats {cols} VALUES (?,?,?,?,?,?,?)",
                 ("hb_new", "operator", "alive", "current", "Full", _recent(), _recent()))
    conn.commit()

    deleted = repo.prune_agent_shell_heartbeats(days=2)
    assert deleted == 1
    remaining = {r["id"] for r in conn.execute("SELECT id FROM agent_shell_heartbeats").fetchall()}
    assert remaining == {"hb_new"}, "prune must keep the latest heartbeat and drop only old ones"


def test_prune_session_logs_keeps_main_and_recent_deletes_old(tmp_path, monkeypatch):
    """Per-session JSONL transcripts (data/agent_shell/sessions/*.jsonl) are
    Axis-1 junk: high file COUNT, low bytes, never read by the learning/PI
    chain. They had no cleanup path (2.6k+ files accumulated). Prune is
    mtime-based and exempts the persistent operator-main session by stem, so it
    can never reap a live or pinned transcript. Guards the DB-SIZE-2 retention."""
    import os
    import time
    from aletheia.agent_shell.file_artifacts import prune_session_logs

    setup_env(tmp_path, monkeypatch)
    sessions = tmp_path / "agent_shell" / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)

    main = sessions / "operator-main.jsonl"          # persistent → KEEP via exempt
    recent = sessions / "task-abc.jsonl"             # fresh → KEEP via mtime
    old = sessions / "ephemeral-xyz.jsonl"           # stale → DELETE
    for p in (main, recent, old):
        p.write_text('{"e":"x"}\n', encoding="utf-8")

    stale_mtime = time.time() - 30 * 86400
    os.utime(main, (stale_mtime, stale_mtime))       # old mtime, but exempt by name
    os.utime(old, (stale_mtime, stale_mtime))        # genuinely stale → reaped

    deleted = prune_session_logs(14, ("operator-main",))
    assert deleted == 1, f"expected only the stale ephemeral file deleted, got {deleted}"
    survivors = {p.name for p in sessions.glob("*.jsonl")}
    assert survivors == {"operator-main.jsonl", "task-abc.jsonl"}, (
        "prune must keep the exempt main session and recent transcripts"
    )


def test_jsonl_rotation(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    import aletheia.files.jsonl as jsonl
    monkeypatch.setattr(jsonl, "_max_bytes", lambda: 200)  # tiny cap for test
    for i in range(50):
        jsonl.append_jsonl("test.jsonl", {"i": i, "pad": "x" * 20})
    logs = tmp_path / "logs"
    assert (logs / "test.jsonl").exists()
    assert (logs / "test.jsonl.1").exists()  # rotated generation exists
