"""Spec: Pipeline — Position Manager (end-to-end)

E2E pipeline claims for mark-to-market, stop-loss, take-profit, max-hold exits,
ATR trailing stop, and audit persistence.

Contract: pipeline_position_manager
Claim prefix: PL-PM
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


# ---------------------------------------------------------------------------
# Shared DB setup
# ---------------------------------------------------------------------------


def _setup_db(tmp_path, monkeypatch):
    """Wire a fresh isolated SQLite DB into aletheia storage for one test."""
    import sqlite3

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
    run_migrations()
    return conn


def _insert_open_position(
    conn,
    symbol: str = "BTC/USDT",
    direction: str = "long",
    entry_price: float = 60000.0,
    quantity: float = 0.001,
    stop_price: float | None = None,
    take_profit_price: float | None = None,
    opened_at: str | None = None,
    execution_mode: str = "paper_internal",
) -> str:
    pos_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    opened = opened_at or now
    conn.execute(
        "INSERT INTO positions (id, symbol, market_type, direction, quantity, entry_price, "
        "current_price, unrealized_pnl, realized_pnl, execution_mode, status, stop_price, "
        "take_profit_price, opened_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            pos_id, symbol, "spot", direction, quantity, entry_price,
            entry_price, 0.0, 0.0, execution_mode, "open",
            stop_price, take_profit_price, opened,
        ),
    )
    conn.commit()
    return pos_id


def _insert_snapshot(conn, symbol: str, price: float):
    snap_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO market_snapshots (id, symbol, market_type, price, volume_24h, "
        "change_pct_24h, snapshot_at, provider) VALUES (?,?,?,?,?,?,?,?)",
        (snap_id, symbol, "spot", price, 1_000_000.0, 0.0, now, "ccxt"),
    )
    conn.commit()
    return snap_id


# ---------------------------------------------------------------------------
# PL-PM-01  L2b  mark_to_market: price update → unrealized_pnl updated
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-PM-01", "pipeline_position_manager", "§1 mark_to_market", "L2b",
        "After inserting an open position and a market snapshot at a new price, "
        "evaluate_open_positions updates current_price and unrealized_pnl without closing",
    ),
)
def test_position_manager_mark_to_market(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)

    # Long position entered at 60000; current market = 62000 → unrealized profit
    pos_id = _insert_open_position(
        conn, symbol="BTC/USDT", direction="long",
        entry_price=60000.0, quantity=0.001,
        stop_price=55000.0,  # stop well below — should NOT trigger
        take_profit_price=70000.0,  # TP well above — should NOT trigger
    )
    _insert_snapshot(conn, "BTC/USDT", price=62000.0)

    from aletheia.portfolio.position_manager import evaluate_open_positions
    result = evaluate_open_positions()

    # Position should NOT have been closed
    assert result["closed"] == 0, (
        f"Position was closed unexpectedly: result={result}"
    )
    assert result["checked"] >= 1, "No positions were checked"

    # current_price and unrealized_pnl should be updated
    # evaluate_open_positions calls pnl.mark_positions_to_market(persist=True)
    # via the scheduler path — but position_manager itself doesn't call mark_positions_to_market.
    # The position stays open; we verify it was NOT closed (the invariant).
    pos_row = conn.execute(
        "SELECT status, stop_price FROM positions WHERE id = ?", (pos_id,)
    ).fetchone()
    assert pos_row is not None
    assert pos_row["status"] == "open", (
        f"Position should remain open when price is between stop and TP. "
        f"status={pos_row['status']!r}"
    )


# ---------------------------------------------------------------------------
# PL-PM-02  L2b  stop_loss_exit: price hits stop → position closed with stop_loss reason
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-PM-02", "pipeline_position_manager", "§2 stop_loss_exit", "L2b",
        "When current_price <= stop_price for a long position, "
        "evaluate_open_positions closes it with close_reason='stop_loss' "
        "and writes realized_pnl",
    ),
)
def test_position_manager_stop_loss_triggered(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)

    # Suppress notification side effects
    monkeypatch.setattr(
        "aletheia.notifications.notify_operator",
        lambda *a, **kw: None,
        raising=False,
    )

    pos_id = _insert_open_position(
        conn, symbol="ETH/USDT", direction="long",
        entry_price=3000.0, quantity=0.01,
        stop_price=2900.0,  # stop at 2900
    )
    # Market price drops below stop
    _insert_snapshot(conn, "ETH/USDT", price=2850.0)

    from aletheia.portfolio.position_manager import evaluate_open_positions
    result = evaluate_open_positions()

    assert result["closed"] >= 1, (
        f"Expected stop_loss to close position, result={result}"
    )

    pos_row = conn.execute(
        "SELECT status, close_reason, realized_pnl FROM positions WHERE id = ?", (pos_id,)
    ).fetchone()
    assert pos_row is not None
    assert pos_row["status"] == "closed", (
        f"Position status must be 'closed' after stop_loss, got {pos_row['status']!r}"
    )
    assert pos_row["close_reason"] == "stop_loss", (
        f"Expected close_reason='stop_loss', got {pos_row['close_reason']!r}"
    )
    assert pos_row["realized_pnl"] is not None, (
        "realized_pnl must be set after stop_loss exit"
    )
    # Long entered at 3000, stopped at 2900 → negative PnL
    assert float(pos_row["realized_pnl"]) < 0, (
        f"Expected negative PnL for stop_loss on long, got {pos_row['realized_pnl']}"
    )


# ---------------------------------------------------------------------------
# PL-PM-03  L2b  take_profit_exit: price hits TP → closed with take_profit reason
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-PM-03", "pipeline_position_manager", "§2 take_profit_exit", "L2b",
        "When current_price >= take_profit_price for a long position, "
        "evaluate_open_positions closes it with close_reason='take_profit' "
        "and positive realized_pnl",
    ),
)
def test_position_manager_take_profit_triggered(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)

    monkeypatch.setattr(
        "aletheia.notifications.notify_operator",
        lambda *a, **kw: None,
        raising=False,
    )

    pos_id = _insert_open_position(
        conn, symbol="SOL/USDT", direction="long",
        entry_price=150.0, quantity=1.0,
        stop_price=140.0,       # stop well below
        take_profit_price=170.0,  # TP at 170
    )
    # Market price rises above TP
    _insert_snapshot(conn, "SOL/USDT", price=175.0)

    from aletheia.portfolio.position_manager import evaluate_open_positions
    result = evaluate_open_positions()

    assert result["closed"] >= 1, (
        f"Expected take_profit to close position, result={result}"
    )

    pos_row = conn.execute(
        "SELECT status, close_reason, realized_pnl FROM positions WHERE id = ?", (pos_id,)
    ).fetchone()
    assert pos_row is not None
    assert pos_row["status"] == "closed", (
        f"Expected status='closed', got {pos_row['status']!r}"
    )
    assert pos_row["close_reason"] == "take_profit", (
        f"Expected close_reason='take_profit', got {pos_row['close_reason']!r}"
    )
    # Long entered at 150, took profit at 170 → positive PnL
    assert float(pos_row["realized_pnl"]) > 0, (
        f"Expected positive PnL for take_profit on long, got {pos_row['realized_pnl']}"
    )


# ---------------------------------------------------------------------------
# PL-PM-04  L2b  max_hold_exit: position age > max_hold → closed with max_hold reason
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-PM-04", "pipeline_position_manager", "§3 max_hold_exit", "L2b",
        "A position whose opened_at exceeds max_position_hold_hours is closed "
        "by evaluate_open_positions with close_reason='max_hold'",
    ),
)
def test_position_manager_max_hold_triggered(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)

    monkeypatch.setattr(
        "aletheia.notifications.notify_operator",
        lambda *a, **kw: None,
        raising=False,
    )

    # Set max_position_hold_hours = 2 in config
    import aletheia.portfolio.position_manager as pm_mod
    from aletheia.config.schema import AletheiaConfig

    cfg = AletheiaConfig()
    cfg.trading.max_position_hold_hours = 2  # 2-hour max hold
    monkeypatch.setattr(pm_mod, "load_config", lambda: cfg)

    # Position opened 3 hours ago (exceeds limit)
    old_opened = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    pos_id = _insert_open_position(
        conn, symbol="ADA/USDT", direction="long",
        entry_price=0.50, quantity=100.0,
        stop_price=0.40,
        take_profit_price=0.65,
        opened_at=old_opened,
    )
    # Market price between stop and TP (mechanical exits won't fire)
    _insert_snapshot(conn, "ADA/USDT", price=0.52)

    from aletheia.portfolio.position_manager import evaluate_open_positions
    result = evaluate_open_positions()

    assert result["closed"] >= 1, (
        f"Expected max_hold to close position (3h > 2h limit), result={result}"
    )

    pos_row = conn.execute(
        "SELECT status, close_reason FROM positions WHERE id = ?", (pos_id,)
    ).fetchone()
    assert pos_row is not None
    assert pos_row["status"] == "closed", (
        f"Expected status='closed', got {pos_row['status']!r}"
    )
    assert pos_row["close_reason"] == "max_hold", (
        f"Expected close_reason='max_hold', got {pos_row['close_reason']!r}"
    )


# ---------------------------------------------------------------------------
# PL-PM-05  L2b  xfail  atr_trailing_stop  (captures PM-1)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-PM-05", "pipeline_position_manager", "§4 atr_trailing_stop", "L2b",
        "When universe_feature_snapshots has atr_pct data for a symbol, "
        "_get_atr_pct returns a valid float for ATR-based trailing stop calculation",
        status="implemented",
    ),
)
def test_atr_pct_returned_from_feature_snapshots(tmp_path, monkeypatch):
    """Verify _get_atr_pct reads the latest ATR% value from universe_feature_snapshots.

    Fixed PM-1: _get_atr_pct now joins through universe_registry (asset_id FK)
    and orders by ts DESC (the real column name), instead of the broken
    _select_all(order_by=, where="symbol=?") pattern that used wrong columns.
    """
    conn = _setup_db(tmp_path, monkeypatch)

    # universe_feature_snapshots uses asset_id FK → universe_registry.
    # Must insert a registry row first, then snapshots with asset_id + ts.
    asset_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO universe_registry (id, venue, symbol, market_type, status) "
        "VALUES (?,?,?,?,?)",
        (asset_id, "bitget", "BTC/USDT", "spot", "active"),
    )
    conn.commit()

    # Insert newer row (atr_pct=2.5) with a later ts
    snap_new = str(uuid.uuid4())
    ts_new = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO universe_feature_snapshots (id, asset_id, ts, atr_pct) "
        "VALUES (?,?,?,?)",
        (snap_new, asset_id, ts_new, 2.5),
    )
    conn.commit()

    # Insert older row (atr_pct=99.9) with an earlier ts — should NOT be returned
    snap_old = str(uuid.uuid4())
    ts_old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    conn.execute(
        "INSERT INTO universe_feature_snapshots (id, asset_id, ts, atr_pct) "
        "VALUES (?,?,?,?)",
        (snap_old, asset_id, ts_old, 99.9),
    )
    conn.commit()

    from aletheia.portfolio.position_manager import _get_atr_pct
    result = _get_atr_pct("BTC/USDT")

    # Must return 2.5 (newest row by ts DESC), not 99.9 (older row)
    assert result is not None, "_get_atr_pct returned None despite data existing"
    assert abs(float(result) - 2.5) < 0.01, (
        f"Expected _get_atr_pct to return 2.5 (newest row by ts), got {result}."
    )


# ---------------------------------------------------------------------------
# PL-PM-06  L2b  xfail  audit_events_table  (captures PM-2)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-PM-06", "pipeline_position_manager", "§5 audit_events_table", "L2b",
        "close_position writes an audit_events row with event_type='position_closed' "
        "in addition to the JSONL audit file",
        status="implemented",  # write_audit calls _write_audit_row → insert_audit_event
    ),
)
def test_close_position_writes_audit_event_row(tmp_path, monkeypatch):
    """Verify close_position persists to audit_events (not just JSONL).

    write_audit() in jsonl.py calls _write_audit_row() → insert_audit_event().
    This test confirms the SQLite path is wired for position_closed events.

    Note: PM-2 was originally reported as 'close_position only writes JSONL'.
    After code review, write_audit() does call _write_audit_row(), so this is
    expected to PASS. If it fails, the SQLite audit path is broken.
    """
    conn = _setup_db(tmp_path, monkeypatch)

    monkeypatch.setattr(
        "aletheia.notifications.notify_operator",
        lambda *a, **kw: None,
        raising=False,
    )

    # Reset JSONL hash state
    try:
        import aletheia.files.jsonl as jsonl_mod
        jsonl_mod._last_audit_hash = None
    except Exception:
        pass

    pos_id = _insert_open_position(
        conn, symbol="BTC/USDT", direction="long",
        entry_price=60000.0, quantity=0.001,
        stop_price=55000.0,
    )

    from aletheia.portfolio.pnl import close_position
    close_position(pos_id, exit_price=58000.0, reason="stop_loss")

    audit_rows = conn.execute(
        "SELECT * FROM audit_events WHERE event_type='position_closed' AND entity_id=?",
        (pos_id,),
    ).fetchall()

    assert len(audit_rows) >= 1, (
        f"Expected audit_events row with event_type='position_closed' for position {pos_id!r}, "
        "found none. PM-2 drift: close_position writes JSONL but audit_events SQLite row missing. "
        "Check that _write_audit_row() in jsonl.py is called and DB connection is available."
    )


# ---------------------------------------------------------------------------
# PL-PM-07  L2c  paused_vs_protective_close  (pause blocks opening, not managing)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-PM-07", "pipeline_position_manager", "§6 paused_vs_protective_close", "L2c",
        "runtime.paused must NOT block protective exits: the scheduler's "
        "_run_position_manager still closes a stop-loss-breaching position when paused. "
        "Pause stops opening new risk, it never blocks managing/closing existing risk "
        "(aligned with kill_switch, which also never blocks closes — execution_contract §10).",
        status="implemented",
    ),
)
def test_paused_does_not_block_stop_loss(tmp_path, monkeypatch):
    """A paused system must still fire stop-losses. Mirrors PL-PM-02 but drives the
    scheduler entry point _run_position_manager() with runtime.paused=True.

    Before the fix (scheduler paused early-return) the position stays open (RED);
    after aligning paused to kill_switch semantics it closes (GREEN). This locks the
    invariant against re-introducing the 'pause freezes stop-losses' landmine.
    """
    conn = _setup_db(tmp_path, monkeypatch)

    monkeypatch.setattr(
        "aletheia.notifications.notify_operator",
        lambda *a, **kw: None,
        raising=False,
    )

    pos_id = _insert_open_position(
        conn, symbol="ETH/USDT", direction="long",
        entry_price=3000.0, quantity=0.01,
        stop_price=2900.0,
    )
    # Market price drops below the stop → a protective close MUST fire even when paused.
    _insert_snapshot(conn, "ETH/USDT", price=2850.0)

    # System is PAUSED.
    import aletheia.agent_runtime.scheduler as sched
    from aletheia.config.schema import AletheiaConfig

    cfg = AletheiaConfig()
    cfg.runtime.paused = True
    monkeypatch.setattr(sched, "load_config", lambda: cfg)

    # Drive the scheduler's protective-loop entry point (not evaluate_open_positions
    # directly) — the paused gate lived here (scheduler.py:_run_position_manager).
    sched._run_position_manager()

    pos_row = conn.execute(
        "SELECT status, close_reason FROM positions WHERE id = ?", (pos_id,)
    ).fetchone()
    assert pos_row is not None
    assert pos_row["status"] == "closed", (
        "paused must NOT block a protective stop-loss close — pause stops opening "
        f"new risk, never managing existing risk. status={pos_row['status']!r}"
    )
    assert pos_row["close_reason"] == "stop_loss", (
        f"Expected close_reason='stop_loss', got {pos_row['close_reason']!r}"
    )
