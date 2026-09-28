"""Terminal bound on close-side submission ambiguity (execution_contract §10.2.4).

A close claim that stays ``submission_unknown`` past ``SUBMISSION_UNKNOWN_TERMINAL_HOURS``
(24 h) stops being machine-owned: it is recorded once as the human-owned
``manual_reconciliation_pending`` state, surfaced through a distinct audit
namespace, a P1 must-answer wake, and the PR3 critical registry. It is NOT a
resolution — the order status stays ``submission_unknown``, the close fence is
never released, and no fill is synthesised. The order remains in an open local
state, so a fill that arrives after the bound is still materialized once a human
resolves the claim (PR4).
"""

import sqlite3
from datetime import datetime, timedelta, timezone

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import AuditEvent, Order, Position
from aletheia.storage import repositories as repo
from tests.helpers.fill_reports import DEFAULT_IDENTITY, reports

PENDING_CLOSE_CODE = "pending_close_reconciliation_required"
MANUAL_CLOSE_CODE = "manual_close_reconciliation_required"


def setup_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    monkeypatch.setattr(sq, "_db_conn", None)
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    monkeypatch.setattr(sq, "_db_conn", conn)
    run_migrations()


def _get_position(position_id):
    row = repo.get_position(position_id)
    assert row is not None
    return row


def _get_order(order_id):
    row = repo.get_order(order_id)
    assert row is not None
    return row


def _stuck_position(
    *,
    symbol="KORU/USDT",
    order_id="stuck-close",
    age_hours: float | None = 5.0,
    requested_at=None,
    status="submission_unknown",
):
    if requested_at is None and age_hours is not None:
        requested_at = (
            datetime.now(timezone.utc) - timedelta(hours=age_hours)
        ).isoformat()
    return Position(
        symbol=symbol,
        market_type="usdt_futures",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        current_price=100.0,
        execution_mode="bitget_demo",
        stop_price=98.0,
        pending_close_order_id=order_id,
        pending_close_status=status,
        pending_close_requested_at=requested_at,
    )


def _stuck_order(order_id="stuck-close", *, position_id=None, symbol="KORU/USDT"):
    return Order(
        id=order_id,
        source="adopted",
        symbol=symbol,
        market_type="usdt_futures",
        direction="long",
        order_type="market",
        quantity=1.0,
        price=101.0,
        execution_mode="bitget_demo",
        position_id=position_id,
        status="submission_unknown",
    )


def _codes(items):
    return [item["code"] for item in items]


def _silence_wake(monkeypatch, delivered=False):
    """Keep wake delivery deterministic; return the recorded calls list."""
    from aletheia.portfolio import position_manager as pm

    calls: list[dict] = []

    def fake_emit(position, kind, payload, source, priority=-1):
        calls.append({"kind": kind, "position_id": position["id"]})
        return delivered

    monkeypatch.setattr(pm, "_emit_wake", fake_emit)
    return calls


def _manual_positions():
    return [
        p
        for p in repo.list_all_open_positions()
        if p.get("pending_close_status") == "manual_reconciliation_pending"
    ]


# ── A. state membership / constants ─────────────────────────────────────────


def test_terminal_hours_constants_ordering():
    from aletheia.execution.close_convergence import (
        PENDING_CLOSE_STALE_HOURS,
        SUBMISSION_UNKNOWN_TERMINAL_HOURS,
    )

    assert SUBMISSION_UNKNOWN_TERMINAL_HOURS == 24
    assert SUBMISSION_UNKNOWN_TERMINAL_HOURS > PENDING_CLOSE_STALE_HOURS


def test_manual_state_is_unresolved_but_not_immediate():
    from aletheia.execution.close_convergence import (
        CLOSE_CLAIM_STATES,
        IMMEDIATE_RECONCILIATION_STATES,
        has_unresolved_close_claim,
    )

    assert "manual_reconciliation_pending" in CLOSE_CLAIM_STATES
    assert "manual_reconciliation_pending" not in IMMEDIATE_RECONCILIATION_STATES
    assert has_unresolved_close_claim({
        "pending_close_status": "manual_reconciliation_pending",
        "pending_close_order_id": "stuck-close",
    })


# ── B. threshold routing ────────────────────────────────────────────────────


def test_below_terminal_hours_uses_ordinary_escalation(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    p = _stuck_position(age_hours=5.0)
    repo.insert_position(p)
    _silence_wake(monkeypatch)

    from aletheia.portfolio.position_manager import evaluate_open_positions

    result = evaluate_open_positions()

    stored = _get_position(p.id)
    assert stored["pending_close_status"] == "submission_unknown"
    # The ordinary stale path (PR1), not the terminal bound.
    assert PENDING_CLOSE_CODE in _codes(result["errors"])


def test_at_terminal_hours_transitions_position_but_not_order(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    p = _stuck_position(age_hours=24.1)
    repo.insert_position(p)
    repo.insert_order(_stuck_order())
    _silence_wake(monkeypatch)

    from aletheia.portfolio.position_manager import evaluate_open_positions

    result = evaluate_open_positions()

    stored = _get_position(p.id)
    assert stored["pending_close_status"] == "manual_reconciliation_pending"
    # Durable evidence is preserved.
    assert stored["pending_close_order_id"] == p.pending_close_order_id
    assert stored["pending_close_requested_at"] == p.pending_close_requested_at
    # No order status transition, and nothing fresh routed to errors/escalated.
    assert _get_order("stuck-close")["status"] == "submission_unknown"
    assert result["errors"] == []
    assert result["escalated"] == []


def test_terminal_bound_does_not_touch_order_status(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    p = _stuck_position(age_hours=30.0)
    repo.insert_position(p)
    repo.insert_order(_stuck_order())
    _silence_wake(monkeypatch)

    from aletheia.portfolio.position_manager import evaluate_open_positions

    evaluate_open_positions()

    assert _get_order("stuck-close")["status"] == "submission_unknown"


def test_null_requested_at_treated_as_terminal_fail_closed(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    p = _stuck_position(age_hours=None, requested_at=None)
    assert p.pending_close_requested_at is None
    repo.insert_position(p)
    _silence_wake(monkeypatch)

    from aletheia.portfolio.position_manager import evaluate_open_positions

    result = evaluate_open_positions()
    row = _get_position(p.id)
    assert row["pending_close_status"] == "manual_reconciliation_pending"
    assert row["pending_close_order_id"] == p.pending_close_order_id
    assert row["pending_close_requested_at"] is None
    assert result["closed"] == 0
    assert result["errors"] == []
    assert result["escalated"] == []


def test_malformed_requested_at_treated_as_terminal_fail_closed(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    p = _stuck_position(requested_at="not-a-timestamp")
    repo.insert_position(p)
    _silence_wake(monkeypatch)

    from aletheia.portfolio.position_manager import evaluate_open_positions

    evaluate_open_positions()
    assert (
        _get_position(p.id)["pending_close_status"]
        == "manual_reconciliation_pending"
    )


def test_future_requested_at_treated_as_terminal_fail_closed(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    future = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat()
    p = _stuck_position(requested_at=future)
    repo.insert_position(p)
    _silence_wake(monkeypatch)

    from aletheia.portfolio.position_manager import evaluate_open_positions

    result = evaluate_open_positions()
    row = _get_position(p.id)
    assert row["pending_close_status"] == "manual_reconciliation_pending"
    assert row["pending_close_order_id"] == p.pending_close_order_id
    assert row["pending_close_requested_at"] == future
    assert result["closed"] == 0
    assert result["errors"] == []
    assert result["escalated"] == []


# ── C. distinct audit namespace + fence ─────────────────────────────────────


def test_terminal_bound_uses_distinct_audit_key_namespace(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    p = _stuck_position(age_hours=25.0)
    repo.insert_position(p)
    repo.insert_audit_event(AuditEvent(
        id="pending-close-reconciliation-seeded",
        actor="system",
        source="position_manager",
        event_type=PENDING_CLOSE_CODE,
        entity_type="position",
        entity_id=p.id,
        message="prior 4h escalation",
    ))
    _silence_wake(monkeypatch)

    from aletheia.portfolio.position_manager import evaluate_open_positions

    evaluate_open_positions()

    ids = {
        row["id"]
        for row in repo._select_all(
            "audit_events",
            where="entity_id=? AND entity_type='position'",
            params=(p.id,),
            limit=50,
        )
    }
    assert "pending-close-reconciliation-seeded" in ids
    assert any(i.startswith("manual-close-reconciliation-") for i in ids)


def test_terminal_bound_does_not_release_fence(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    p = _stuck_position(age_hours=25.0)
    repo.insert_position(p)
    _silence_wake(monkeypatch)

    from aletheia.portfolio.position_manager import evaluate_open_positions
    from aletheia.execution.close_convergence import has_unresolved_close_claim
    from aletheia.execution.coordinator import _claim_close

    evaluate_open_positions()

    stored = _get_position(p.id)
    assert has_unresolved_close_claim(stored) is True

    row, order = _claim_close(p.id, "stop_loss", None)
    assert order is None  # the fence short-circuits a second close claim
    assert row["pending_close_status"] == "manual_reconciliation_pending"


# ── D. position_manager reporting + wake + registry ─────────────────────────


def test_manual_state_not_reported_as_position_manager_error(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    p = _stuck_position(age_hours=25.0)
    repo.insert_position(p)
    _silence_wake(monkeypatch)

    from aletheia.portfolio.position_manager import evaluate_open_positions

    for _ in range(10):
        result = evaluate_open_positions()
        assert result["errors"] == []
        assert result["escalated"] == []

    assert _manual_positions()


def test_wake_redelivered_when_delivery_audit_missing(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    p = _stuck_position(age_hours=25.0)
    repo.insert_position(p)

    from aletheia.portfolio import position_manager as pm
    from aletheia.portfolio.position_manager import evaluate_open_positions

    calls: list[dict] = []
    delivered = {"value": False}

    def fake_emit(position, kind, payload, source, priority=-1):
        calls.append({"kind": kind, "position_id": position["id"]})
        return delivered["value"]

    monkeypatch.setattr(pm, "_emit_wake", fake_emit)

    # Pass 1: emit fails → no delivery audit row.
    evaluate_open_positions()
    assert len(calls) == 1
    assert not repo._select_all(
        "audit_events", where="event_type='manual_close_wake_delivered'", limit=10
    )

    # Pass 2: emit succeeds → delivery row written.
    delivered["value"] = True
    evaluate_open_positions()
    assert len(calls) == 2
    deliveries = repo._select_all(
        "audit_events", where="event_type='manual_close_wake_delivered'", limit=10
    )
    assert len(deliveries) == 1
    assert deliveries[0]["entity_id"] == p.id

    # Pass 3: delivery row exists → no further attempt.
    evaluate_open_positions()
    assert len(calls) == 2


def test_terminal_bound_upserts_critical_registry_idempotently(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    p = _stuck_position(age_hours=25.0)
    repo.insert_position(p)
    _silence_wake(monkeypatch)

    from aletheia.infra import events as events_mod
    from aletheia.infra.events import clear_infra_events
    from aletheia.portfolio.position_manager import evaluate_open_positions

    clear_infra_events()
    for _ in range(5):
        evaluate_open_positions()

    matches = [
        entry
        for entry in events_mod._critical_unresolved.values()
        if entry["code"] == MANUAL_CLOSE_CODE and entry["entity_id"] == p.id
    ]
    assert len(matches) == 1


# ── E. reconciler filter ────────────────────────────────────────────────────


def test_reconciler_skips_manual_pending_positions(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)

    manual_position = Position(
        symbol="KORU/USDT", market_type="usdt_futures", direction="long",
        quantity=1.0, entry_price=100.0, execution_mode="bitget_demo",
        pending_close_order_id="order-a", pending_close_status="manual_reconciliation_pending",
    )
    repo.insert_position(manual_position)
    normal_position = Position(
        symbol="ETH/USDT", market_type="usdt_futures", direction="long",
        quantity=1.0, entry_price=3000.0, execution_mode="bitget_demo",
        pending_close_order_id="order-b", pending_close_status="submission_unknown",
    )
    repo.insert_position(normal_position)

    repo.insert_order(_stuck_order("order-a", position_id=manual_position.id, symbol="KORU/USDT"))
    repo.insert_order(_stuck_order("order-b", position_id=normal_position.id, symbol="ETH/USDT"))

    from aletheia.execution.reconciliation import _list_unresolved_core_orders

    ids = {o["id"] for o in _list_unresolved_core_orders()}
    assert "order-b" in ids
    assert "order-a" not in ids


# ── F. late fill is not severed ─────────────────────────────────────────────


def _seed_terminal_close_fixture(*, quantity=1.0, symbol="XRP/USDT"):
    from tests.helpers.execution_authority import seed_order_authority

    proposal, decision = seed_order_authority(
        proposal_id="prop-terminal",
        risk_decision_id="risk-terminal",
        symbol=symbol,
        market_type="usdt_futures",
        direction="short",
        order_type="market",
        quantity=quantity,
        price=100.0,
        execution_mode="bitget_demo",
    )
    identity_json = DEFAULT_IDENTITY.model_dump_json()
    entry = Order(
        proposal_id=proposal.id, risk_decision_id=decision.id,
        client_order_id=proposal.id, symbol=symbol, market_type="usdt_futures",
        direction="short", order_type="market", quantity=quantity, price=100.0,
        execution_mode="bitget_demo", status="filled",
        uta_account_identity_json=identity_json,
    )
    repo.insert_order(entry)
    position = Position(
        order_id=entry.id, proposal_id=proposal.id, symbol=symbol,
        market_type="usdt_futures", direction="short", quantity=quantity,
        entry_price=100.0, execution_mode="bitget_demo", status="open",
        source="native", opened_at="2026-08-22T12:00:00+00:00",
        pending_close_order_id="order-terminal",
        pending_close_status="manual_reconciliation_pending",
        pending_close_requested_at="2026-08-01T00:00:00+00:00",
    )
    repo.insert_position(position)
    close = Order(
        uta_account_identity_json=identity_json,
        id="order-terminal", proposal_id=proposal.id,
        client_order_id=f"close-{position.id}", position_id=position.id,
        close_reason="stop_loss", symbol=symbol, market_type="usdt_futures",
        direction="short", order_type="market", quantity=quantity, price=101.0,
        execution_mode="bitget_demo", status="submission_unknown", source="native",
        exchange_order_id="EX-TERMINAL-1", created_at="2026-08-22T14:30:00+00:00",
    )
    repo.insert_order(close)
    return position, close


def _terminal_fill_row():
    return reports(
        [{
            "orderId": "EX-TERMINAL-1",
            "tradeId": "FILL-EX-TERMINAL-1",
            "fillPrice": "101.0",
            "fillQuantity": "1.0",
            "fee": "0.05",
            "feeCoin": "USDT",
            "cTime": "1787409000000",
        }],
        symbol="XRP/USDT",
        market_type="usdt_futures",
    )[0]


def test_late_fill_after_terminal_bound_still_materializes(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    position, close = _seed_terminal_close_fixture()
    report = _terminal_fill_row()
    fill_rows = [report]
    assert report.price is not None
    assert report.quantity is not None
    assert report.fee is not None

    # The late fill lands locally (as a real execution path would record it).
    from aletheia.execution.exchange_materialization import (
        durable_private_fill_id,
        materialize_bitget_close,
    )

    db = repo.get_db()
    db.execute(
        "INSERT INTO fills (id, order_id, fill_price, fill_quantity, fee, "
        "fee_currency, fee_source, filled_at) VALUES (?,?,?,?,?,?,?,?)",
        (
            durable_private_fill_id("bitget_demo", report.venue_fill_id),
            close.id,
            float(report.price),
            float(report.quantity),
            float(report.fee),
            report.fee_currency,
            "reconciled",
            report.executed_at.astimezone(timezone.utc).isoformat(),
        ),
    )
    db.commit()

    # The ordinary reconciler skips manual positions; the operator's PR4 tool
    # bypasses that filter and calls the materializer directly. Here we call the
    # same helper the reconciler uses.
    result = materialize_bitget_close(close.id, fill_rows)

    assert result.state == "filled"
    stored = _get_position(position.id)
    assert stored["status"] == "closed"
    assert stored["pending_close_order_id"] is None
    assert stored["pending_close_status"] is None
