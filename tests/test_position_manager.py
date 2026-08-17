"""Tests for automatic position management / closing (Component 1 — the keystone).

Closing a paper position activates realized PnL, the daily-loss breaker, and the
strategy promotion gates. Governance: paper-only (live positions never auto-closed),
kill switch must NOT block closing (closing reduces risk), every close writes an
audit event, no honest exit price → skip (never fabricate an exit).
"""

import sqlite3
from datetime import datetime, timezone, timedelta

import pytest

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import Position, MarketSnapshot
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


def _snap(symbol, price, market_type="spot", exchange="binance"):
    repo.insert_market_snapshot(
        MarketSnapshot(
            symbol=symbol,
            market_type=market_type,
            exchange=exchange,
            price=price,
        )
    )


def _position_with_entry_chain(
    *,
    symbol,
    market_type,
    direction,
    quantity,
    entry_price,
    entry_fee,
    stop_price,
    take_profit_price,
    opened_at=None,
):
    """Build the real Opportunity→Proposal→Risk→Order/Fill→Position entry path."""
    from tests.test_outcome_closure import _insert_entry_chain

    position = Position(
        symbol=symbol,
        market_type=market_type,
        direction=direction,
        quantity=quantity,
        entry_price=entry_price,
        current_price=entry_price,
        execution_mode="paper_internal",
        stop_price=stop_price,
        take_profit_price=take_profit_price,
        entry_fee=entry_fee,
        entry_price_source="local_fill",
        entry_fill_verified=True,
        funding_cost=0.0,
        funding_source=(
            "not_applicable" if market_type == "spot" else "local_simulation"
        ),
    )
    if opened_at is not None:
        position.opened_at = opened_at
    opportunity_id, proposal_id, order_id = _insert_entry_chain(
        position_id=position.id,
        symbol=symbol,
        market_type=market_type,
        direction=direction,
        quantity=quantity,
        price=entry_price,
        execution_mode="paper_internal",
        entry_fee=entry_fee,
        opened_at=position.opened_at,
    )
    position.opportunity_id = opportunity_id
    position.proposal_id = proposal_id
    position.order_id = order_id
    repo.insert_position(position)
    return position


def test_migration_adds_position_columns(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    import aletheia.storage.sqlite as sq
    cols = {r["name"] for r in sq.get_db().execute("PRAGMA table_info(positions)").fetchall()}
    assert {"stop_price", "take_profit_price", "close_reason"} <= cols


def test_stop_loss_closes_long(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    p = _position_with_entry_chain(
        symbol="BTC/USDT", market_type="spot", direction="long",
        quantity=1.0, entry_price=100.0, entry_fee=0.06,
        stop_price=98.0, take_profit_price=110.0,
    )
    _snap("BTC/USDT", 97.0)  # below stop

    from aletheia.portfolio.position_manager import evaluate_open_positions
    result = evaluate_open_positions()
    assert result["closed"] == 1

    row = repo._select_one("positions", p.id)
    assert row["status"] == "closed"
    assert row["close_reason"] == "stop_loss"
    # Stop 98 is the trigger, not a fabricated fill. The observed mark is 97;
    # closing a long sells with the configured default 5 bps adverse slippage.
    fill_price = 97.0 * (1 - 5 / 10_000.0)
    assert row["current_price"] == pytest.approx(fill_price)
    assert row["current_price"] != 98.0
    expected = (fill_price - 100.0) - (100.0 + fill_price) * 0.0006
    assert row["realized_pnl"] == pytest.approx(expected)
    assert row["close_category"] == "strategy"
    assert row["exit_price_source"] == "local_fill"
    assert row["exit_fill_verified"] == 1
    assert row["outcome_eligible"] == 1
    assert row["outcome_exclusion_reason"] == "eligible"
    assert row["outcome_semantics_version"] == "oi-v1"
    assert len(repo.list_positions(status="open")) == 0

    from aletheia.server.routes.analytics import get_equity_curve
    curve = get_equity_curve(execution_mode="paper_internal")
    assert curve["eligible_outcome_count"] == 1
    assert len(curve["genuine_points"]) == 1
    assert curve["genuine_points"][0]["pnl"] == pytest.approx(expected, abs=1e-4)

    close_orders = repo._select_all(
        "orders",
        where="position_id = ? AND close_reason IS NOT NULL",
        params=(p.id,),
        limit=10,
    )
    assert len(close_orders) == 1
    close_fills = repo.get_db().execute(
        "SELECT * FROM fills WHERE order_id=?",
        (close_orders[0]["id"],),
    ).fetchall()
    assert len(close_fills) == 1


def test_take_profit_closes_short(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    # A futures close can expose net PnL only when the public funding schedule
    # covers the complete entry-to-exit window.  The next settlement is in the
    # future, so this is a verified zero-event funding window.
    observed = datetime.now(timezone.utc) - timedelta(minutes=1)
    repo.insert_funding_rate_snapshot({
        "symbol": "ETH/USDT",
        "exchange": "okx",
        "funding_rate": 0.0001,
        "funding_rate_annualized": 0.1095,
        "next_funding_time": (observed + timedelta(hours=4)).isoformat(),
        "observed_at": observed.isoformat(),
    })
    p = _position_with_entry_chain(
        symbol="ETH/USDT", market_type="usdt_futures", direction="short",
        quantity=2.0, entry_price=200.0, entry_fee=0.24,
        stop_price=210.0, take_profit_price=180.0,
    )
    _snap("ETH/USDT", 179.0, "usdt_futures")  # below TP for a short

    from aletheia.portfolio.position_manager import evaluate_open_positions
    result = evaluate_open_positions()
    assert result["closed"] == 1
    row = repo._select_one("positions", p.id)
    assert row["close_reason"] == "take_profit"
    # TP 180 is the trigger. The observed mark is 179; closing a short buys
    # with the configured default 5 bps adverse slippage.
    fill_price = 179.0 * (1 + 5 / 10_000.0)
    assert row["current_price"] == pytest.approx(fill_price)
    assert row["current_price"] != 180.0
    expected = (200.0 - fill_price) * 2.0 - (200.0 + fill_price) * 2.0 * 0.0006
    assert row["realized_pnl"] == pytest.approx(expected)
    assert row["outcome_eligible"] == 1


def test_max_hold_closes(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    old = (datetime.now(timezone.utc) - timedelta(hours=100)).isoformat()
    p = _position_with_entry_chain(
        symbol="SOL/USDT", market_type="spot", direction="long",
        quantity=1.0, entry_price=150.0, entry_fee=0.09,
        stop_price=140.0, take_profit_price=170.0, opened_at=old,
    )
    _snap("SOL/USDT", 150.0)  # no stop/TP trigger, but past max hold

    from aletheia.portfolio.position_manager import evaluate_open_positions
    result = evaluate_open_positions()
    assert result["closed"] == 1
    row = repo._select_one("positions", p.id)
    assert row["close_reason"] == "max_hold"
    assert row["outcome_eligible"] == 1


def test_no_trigger_leaves_open(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    p = Position(symbol="BTCUSDT", market_type="spot", direction="long",
                 quantity=1.0, entry_price=100.0, current_price=100.0,
                 execution_mode="paper_internal", stop_price=95.0, take_profit_price=110.0)
    repo.insert_position(p)
    _snap("BTCUSDT", 100.0)

    from aletheia.portfolio.position_manager import evaluate_open_positions
    result = evaluate_open_positions()
    assert result["closed"] == 0
    assert len(repo.list_positions(status="open")) == 1


def test_close_writes_audit(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    p = _position_with_entry_chain(
        symbol="BTC/USDT", market_type="spot", direction="long",
        quantity=1.0, entry_price=100.0, entry_fee=0.06,
        stop_price=98.0, take_profit_price=110.0,
    )
    _snap("BTC/USDT", 97.0)

    from aletheia.portfolio.position_manager import evaluate_open_positions
    evaluate_open_positions()

    from aletheia.files.jsonl import read_jsonl
    events = read_jsonl("audit.jsonl")
    closed = [e for e in events if e.get("event_type") == "position_closed"]
    assert closed and closed[-1]["entity_id"] == p.id
    assert closed[-1]["entity_type"] == "position"
    assert closed[-1]["after"]["outcome_eligible"] is True

    durable = [
        e for e in repo.list_audit_events(limit=20)
        if e["event_type"] == "position_closed" and e["entity_id"] == p.id
    ]
    assert len(durable) == 1


def test_live_position_never_auto_closed(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    p = Position(symbol="BTCUSDT", market_type="spot", direction="long",
                 quantity=1.0, entry_price=100.0, current_price=100.0,
                 execution_mode="bitget_live_limited", stop_price=98.0, take_profit_price=110.0)
    repo.insert_position(p)
    _snap("BTCUSDT", 90.0)  # well below stop

    from aletheia.portfolio.position_manager import evaluate_open_positions
    result = evaluate_open_positions()
    assert result["closed"] == 0
    assert repo._select_one("positions", p.id)["status"] == "open"  # live left alone


def test_kill_switch_does_not_block_close(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config(); cfg.runtime.kill_switch_active = True; save_config(cfg)

    p = Position(symbol="BTCUSDT", market_type="spot", direction="long",
                 quantity=1.0, entry_price=100.0, current_price=100.0,
                 execution_mode="paper_internal", stop_price=98.0, take_profit_price=110.0)
    repo.insert_position(p)
    _snap("BTCUSDT", 97.0)

    from aletheia.portfolio.position_manager import evaluate_open_positions
    result = evaluate_open_positions()
    assert result["closed"] == 1  # closing reduces risk; kill switch must not block it


def test_no_price_skips(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    p = Position(symbol="BTCUSDT", market_type="spot", direction="long",
                 quantity=1.0, entry_price=100.0, current_price=100.0,
                 execution_mode="paper_internal", stop_price=98.0, take_profit_price=110.0)
    repo.insert_position(p)
    # no snapshot inserted

    from aletheia.portfolio.position_manager import evaluate_open_positions
    result = evaluate_open_positions()
    assert result["closed"] == 0  # no honest exit price -> skip, never fabricate
    assert repo._select_one("positions", p.id)["status"] == "open"


def test_config_default_stop_used_when_no_explicit_stop(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    # No explicit stop_price -> manager uses cfg.trading.default_stop_loss_pct (2%).
    p = Position(symbol="BTCUSDT", market_type="spot", direction="long",
                 quantity=1.0, entry_price=100.0, current_price=100.0,
                 execution_mode="paper_internal")
    repo.insert_position(p)
    _snap("BTCUSDT", 97.0)  # below 2% default stop (98)

    from aletheia.portfolio.position_manager import evaluate_open_positions
    result = evaluate_open_positions()
    assert result["closed"] == 1
    assert repo._select_one("positions", p.id)["close_reason"] == "stop_loss"


# --- Issue #416: live limit exit must be rejected (no executor for it) ---

def test_live_limit_exit_rejected(tmp_path, monkeypatch):
    """Issue #416: operator_exit_position with order_type='limit' on a live_limited
    position was accepted and persisted as pending_limit_exit=True, but evaluate_open_
    positions() only processes _PAPER_MODES — the limit exit could never execute.

    After the fix, the call must return an error response and leave the position
    unchanged (exit_limit_price must NOT be set)."""
    setup_env(tmp_path, monkeypatch)
    p = Position(
        symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        quantity=0.01, entry_price=60000.0, current_price=60000.0,
        execution_mode="bitget_live_limited",
    )
    repo.insert_position(p)

    from aletheia.portfolio.position_manager import proactive_exit
    result = proactive_exit(p.id, order_type="limit", limit_price=65000.0)

    assert result.get("error") == "limit_exit_not_supported_for_live", (
        f"Expected error 'limit_exit_not_supported_for_live', got: {result}"
    )
    # Position must be unchanged — no exit_limit_price written.
    row = repo._select_one("positions", p.id)
    assert row["exit_limit_price"] is None, (
        "exit_limit_price must not be set when the limit exit is rejected"
    )
    assert row["status"] == "open"


def test_live_market_exit_still_works(tmp_path, monkeypatch):
    """Regression: market exit for live positions must still route through
    close_position_routed(), not be blocked by the limit-exit guard."""
    setup_env(tmp_path, monkeypatch)
    p = Position(
        symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        quantity=0.01, entry_price=60000.0, current_price=60000.0,
        execution_mode="bitget_live_limited",
    )
    repo.insert_position(p)

    # close_position_routed raises when live is disabled; patch it to a no-op
    # so we confirm the limit-type guard doesn't interfere with market exit routing.
    from aletheia.portfolio import position_manager as pm_mod
    monkeypatch.setattr(pm_mod, "close_position_routed",
                        lambda *a, **kw: {"closed": True})
    _snap("BTC/USDT", 63000.0)

    from aletheia.portfolio.position_manager import proactive_exit
    result = proactive_exit(p.id, order_type="market")

    # No error — market exit is routed regardless of execution_mode.
    assert "error" not in result, f"Unexpected error on market exit: {result}"


def test_stale_pending_close_is_escalated_without_resubmission(tmp_path, monkeypatch):
    """Each stale close claim remains owned and is escalated exactly once."""
    setup_env(tmp_path, monkeypatch)
    p = Position(
        symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        quantity=1.0, entry_price=100.0, current_price=100.0,
        execution_mode="bitget_demo", stop_price=98.0,
        pending_close_order_id="unresolved-close",
        pending_close_status="submission_unknown",
        pending_close_requested_at=(datetime.now(timezone.utc) - timedelta(hours=5)).isoformat(),
    )
    repo.insert_position(p)

    from aletheia.portfolio.position_manager import evaluate_open_positions
    first = evaluate_open_positions()
    second = evaluate_open_positions()

    assert first["closed"] == second["closed"] == 0
    error_codes = {error["code"] for error in first["errors"]}
    assert "pending_close_reconciliation_required" in error_codes
    assert error_codes & {
        "protective_mark_lookup_failed", "protective_mark_unavailable",
    }
    assert repo._select_one("positions", p.id)["pending_close_order_id"] == "unresolved-close"
    assert repo._select_all("orders", where="position_id=?", params=(p.id,), limit=10) == []
    incidents = [event for event in repo.list_audit_events(limit=20)
                 if event["event_type"] == "pending_close_reconciliation_required"]
    assert len(incidents) == 1

    repo.update_position(
        p.id,
        pending_close_order_id="second-unresolved-close",
        pending_close_status="submission_unknown",
        pending_close_requested_at=(
            datetime.now(timezone.utc) - timedelta(hours=5)
        ).isoformat(),
    )
    third = evaluate_open_positions()
    fourth = evaluate_open_positions()

    assert third["closed"] == fourth["closed"] == 0
    assert repo._select_one("positions", p.id)["pending_close_order_id"] == (
        "second-unresolved-close"
    )
    incidents = [event for event in repo.list_audit_events(limit=20)
                 if event["event_type"] == "pending_close_reconciliation_required"]
    assert len(incidents) == 2
    messages = {event["message"] for event in incidents}
    assert any("Pending close unresolved-close has remained" in message
               for message in messages)
    assert any("Pending close second-unresolved-close has remained" in message
               for message in messages)


@pytest.mark.parametrize("requested_at", [
    None,
    "2099-01-01T00:00:00+00:00",
])
def test_pending_close_with_unknown_age_is_escalated(tmp_path, monkeypatch, requested_at):
    """Missing or future close age is unsafe, not permission to suppress forever."""
    setup_env(tmp_path, monkeypatch)
    p = Position(
        symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        quantity=1.0, entry_price=100.0, execution_mode="bitget_demo",
        pending_close_order_id="unresolved-close", pending_close_status="submission_unknown",
        pending_close_requested_at=requested_at,
    )
    repo.insert_position(p)

    from aletheia.portfolio.position_manager import evaluate_open_positions
    result = evaluate_open_positions()

    assert result["closed"] == 0
    assert any(
        error["code"] == "pending_close_reconciliation_required"
        for error in result["errors"]
    )
    assert repo._select_one("positions", p.id)["pending_close_order_id"] == "unresolved-close"
