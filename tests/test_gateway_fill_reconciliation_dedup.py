"""Regression: issue #1115 — fill reconciliation passed trade ID as venue arg.

Before fix: fill_exists(rf.venue_trade_id, rf.venue_trade_id)  ← always missed
After fix:  fill_exists(local_order["venue"], rf.venue_trade_id)

Covers all four test requirements from the issue:
- Pre-persisted fill → reconciliation skips it without INSERT or error
- Two fills sharing the same timestamp → each correctly deduped by identity
- Two distinct venues with the same trade_id → tracked as separate identities
- Overlapping remote response → GatewayRecoveryService stays out of SAFE_FREEZE
"""

from __future__ import annotations

import asyncio

import pytest

from aletheia.gateway.exchange_adapter import MockExchangeAdapter, RemoteFill
from aletheia.gateway.reconciliation import reconcile_fills
from aletheia.gateway.state_store import GatewayStateStore
from aletheia.storage.models import PSFill, new_id
from aletheia.storage.sqlite import get_db


def _insert_order(venue: str, client_order_id: str, mode: str = "paper", status: str = "submitted") -> str:
    order_id = new_id()
    db = get_db()
    db.execute(
        "INSERT INTO ps_orders "
        "(order_id, intent_id, mode, venue, client_order_id, symbol, side, order_type, quantity, status, updated_at) "
        "VALUES (?, ?, ?, ?, ?, 'BTC/USDT', 'buy', 'market', '0.01', ?, '2026-01-01T00:00:00+00:00')",
        (order_id, new_id(), mode, venue, client_order_id, status),
    )
    db.commit()
    return order_id


def _insert_fill(venue: str, venue_trade_id: str, filled_at: str, mode: str = "paper") -> None:
    db = get_db()
    db.execute(
        "INSERT INTO ps_fills "
        "(fill_id, mode, venue, venue_trade_id, symbol, side, quantity, price, filled_at) "
        "VALUES (?, ?, ?, ?, 'BTC/USDT', 'buy', '0.01', '50000', ?)",
        (new_id(), mode, venue, venue_trade_id, filled_at),
    )
    db.commit()


def _remote(venue_trade_id: str, client_order_id: str, filled_at: str = "2026-01-01T12:00:00+00:00") -> RemoteFill:
    return RemoteFill(
        venue_trade_id=venue_trade_id,
        client_order_id=client_order_id,
        symbol="BTC/USDT",
        side="buy",
        quantity="0.01",
        price="50000",
        filled_at=filled_at,
    )


# ---------------------------------------------------------------------------
# 1. Pre-persisted fill is skipped — no duplicate INSERT, no diff, no error
# ---------------------------------------------------------------------------

def test_known_fill_is_skipped_without_insert_or_error():
    _insert_order("bitget", "aletheia_ord-1")
    _insert_fill("bitget", "trade-123", "2026-01-01T12:00:00+00:00")

    state_store = GatewayStateStore()
    diffs, backfilled = reconcile_fills([_remote("trade-123", "aletheia_ord-1")], state_store, "paper")

    assert backfilled == []
    assert diffs == []
    count = get_db().execute(
        "SELECT COUNT(*) FROM ps_fills WHERE venue_trade_id = 'trade-123'"
    ).fetchone()[0]
    assert count == 1


# ---------------------------------------------------------------------------
# 2. Two fills sharing the same timestamp — each deduped by identity, not time
# ---------------------------------------------------------------------------

def test_same_timestamp_fills_deduped_by_venue_identity():
    ts = "2026-01-01T12:00:00+00:00"
    _insert_order("bitget", "aletheia_ord-a")
    _insert_order("bitget", "aletheia_ord-b")
    _insert_fill("bitget", "trade-aaa", ts)
    _insert_fill("bitget", "trade-bbb", ts)

    state_store = GatewayStateStore()
    remote = [
        _remote("trade-aaa", "aletheia_ord-a", ts),
        _remote("trade-bbb", "aletheia_ord-b", ts),
    ]
    diffs, backfilled = reconcile_fills(remote, state_store, "paper")

    assert backfilled == []
    assert diffs == []
    count = get_db().execute("SELECT COUNT(*) FROM ps_fills").fetchone()[0]
    assert count == 2  # no extras inserted


# ---------------------------------------------------------------------------
# 3. Two venues with the same trade_id — tracked as separate identities
# ---------------------------------------------------------------------------

def test_same_trade_id_different_venues_are_independent():
    ts = "2026-01-01T12:00:00+00:00"
    _insert_order("bitget", "aletheia_ord-bg")
    _insert_order("okx", "aletheia_ord-okx")
    # Only bitget's fill is pre-persisted; okx's is new
    _insert_fill("bitget", "trade-xyz", ts)

    state_store = GatewayStateStore()
    remote = [
        _remote("trade-xyz", "aletheia_ord-bg", ts),   # already known for bitget
        _remote("trade-xyz", "aletheia_ord-okx", ts),  # new — same id, different venue
    ]
    diffs, backfilled = reconcile_fills(remote, state_store, "paper")

    assert len(backfilled) == 1
    assert backfilled[0].venue == "okx"
    assert backfilled[0].venue_trade_id == "trade-xyz"
    count = get_db().execute("SELECT COUNT(*) FROM ps_fills WHERE venue_trade_id = 'trade-xyz'").fetchone()[0]
    assert count == 2  # one per venue


# ---------------------------------------------------------------------------
# 4. Overlapping remote response → GatewayRecoveryService must NOT SAFE_FREEZE
# ---------------------------------------------------------------------------

def test_overlapping_remote_fill_does_not_cause_safe_freeze():
    from aletheia.gateway.locks import GatewayLockService
    from aletheia.gateway.recovery import GatewayRecoveryService
    from aletheia.gateway.session import GatewaySessionStore
    from aletheia.gateway.state_machine import GatewayStateMachine
    from aletheia.gateway.exchange_adapter import RemoteOrder

    ts = "2026-01-01T12:00:00+00:00"
    _insert_order("bitget", "aletheia_ord-overlap", status="filled")
    _insert_fill("bitget", "trade-overlap", ts)

    adapter = MockExchangeAdapter(
        open_orders=[],
        positions=[],
        recent_fills=[_remote("trade-overlap", "aletheia_ord-overlap", ts)],
    )

    svc = GatewayRecoveryService(
        state_store=GatewayStateStore(),
        session_store=GatewaySessionStore(),
        lock_service=GatewayLockService(),
        state_machine=GatewayStateMachine(),
        exchange_adapter=adapter,
        gateway_id="test-gateway",
        mode="paper",
    )
    result = asyncio.run(svc.recover())

    assert result.status == "ok", f"Expected ok, got {result.status}: {result.freeze_reason}"
    assert result.freeze_reason is None
    count = get_db().execute("SELECT COUNT(*) FROM ps_fills WHERE venue_trade_id = 'trade-overlap'").fetchone()[0]
    assert count == 1  # still exactly one fill, not a duplicate
