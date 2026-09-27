"""PR4 operator tooling: manual-close diagnostic and single-claim resolver.

These are CLI tests for ``scripts/manual_close_resolver.py`` and
``scripts/manual_close_diagnostic.py``. They exercise the resolver's ``main()``
directly with an argv list and a monkeypatched ``stdin``, against a temp DB, and
assert the execution_contract §10.2.4 safety invariants:

* a stale ``--acknowledge`` fingerprint refuses to mutate (exit 2);
* a wrong typed confirmation refuses to mutate (exit 4);
* a non-manual position is refused (exit 3);
* every write happens in one transaction (a mid-resolution audit failure rolls
  the whole resolution back);
* no mode synthesises a fill or changes Order status except as specified.
"""

import contextlib
import io
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

from aletheia.storage import repositories as repo
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import AuditEvent, Order, Position
from aletheia.storage.sqlite import reset_db
from scripts.manual_close_diagnostic import (
    build_snapshot,
    main as diagnostic_main,
    snapshot_fingerprint,
)
from scripts.manual_close_resolver import main as resolver_main

MANUAL = "manual_reconciliation_pending"
PENDING_CLOSE_CODE = "pending_close_reconciliation_required"
RESOLVED_CODE = "manual_close_claim_resolved"
INCIDENT_CODE = "manual_close_reconciliation_required"


def setup_env(tmp_path, monkeypatch):
    """Point the repository at a fresh migrated temp DB (PR2 fixture pattern)."""
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    monkeypatch.setattr(sq, "_db_conn", None)
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    monkeypatch.setattr(sq, "_db_conn", conn)
    run_migrations()
    return tmp_path / "app.db"


def _seed_claim(
    *,
    status=MANUAL,
    order_id="order-terminal",
    symbol="KORU/USDT",
    with_audit=True,
    age_hours=25.0,
):
    requested_at = (
        datetime.now(timezone.utc) - timedelta(hours=age_hours)
    ).isoformat()
    position = Position(
        symbol=symbol,
        market_type="usdt_futures",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        current_price=100.0,
        execution_mode="bitget_demo",
        pending_close_order_id=order_id,
        pending_close_status=status,
        pending_close_requested_at=requested_at,
    )
    repo.insert_position(position)
    close = Order(
        id=order_id,
        source="adopted",
        symbol=symbol,
        market_type="usdt_futures",
        direction="long",
        order_type="market",
        quantity=1.0,
        price=101.0,
        execution_mode="bitget_demo",
        position_id=position.id,
        status="submission_unknown",
        exchange_order_id="EX-1",
    )
    repo.insert_order(close)
    if with_audit:
        repo.insert_audit_event(AuditEvent(
            id=f"pending-close-{order_id}",
            actor="system",
            source="position_manager",
            event_type=PENDING_CLOSE_CODE,
            entity_type="position",
            entity_id=position.id,
            message="seed prior escalation",
        ))
    return position, close


def _fingerprint(position_id):
    snapshot = build_snapshot(repo.get_db(), position_id)
    assert snapshot is not None
    return snapshot_fingerprint(snapshot)


def _stored_position(position_id):
    row = repo.get_position(position_id)
    assert row is not None
    return row


def _stored_order(order_id):
    row = repo.get_order(order_id)
    assert row is not None
    return row


def _diag_json(db, *extra):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        rc = diagnostic_main(["--db", str(db), *extra, "--json"])
    return rc, json.loads(buffer.getvalue())


def _confirm(monkeypatch, position_id, mode, *, literal=None):
    line = literal if literal is not None else f"RESOLVE {position_id} AS {mode}"
    monkeypatch.setattr(sys, "stdin", io.StringIO(line + "\n"))


def _resolve(db, position_id, ack, mode, *, extra=()):
    return resolver_main([
        "--db", str(db),
        "--position-id", position_id,
        "--acknowledge", ack,
        "--mode", mode,
        "--reason", "operator investigated the venue",
        *extra,
    ])


# ── diagnostic ──────────────────────────────────────────────────────────────


def test_diagnostic_fingerprint_deterministic(tmp_path, monkeypatch):
    db = setup_env(tmp_path, monkeypatch)
    position, _ = _seed_claim()

    rc1, records1 = _diag_json(db)
    rc2, records2 = _diag_json(db)

    assert rc1 == 0 and rc2 == 0
    assert len(records1) == 1
    assert records1[0]["fingerprint"] == records2[0]["fingerprint"]
    assert records1[0]["position_id"] == position.id
    # Determinism is also pinned by the standalone function.
    assert records1[0]["fingerprint"] == _fingerprint(position.id)


def test_diagnostic_fingerprint_changes_when_audit_added(tmp_path, monkeypatch):
    db = setup_env(tmp_path, monkeypatch)
    position, _ = _seed_claim()

    _, before = _diag_json(db)
    repo.insert_audit_event(AuditEvent(
        id="manual_close_extra_audit",
        actor="human",
        source="manual_close_resolver",
        event_type=RESOLVED_CODE,
        entity_type="position",
        entity_id=position.id,
        message="an operator note",
    ))
    _, after = _diag_json(db)

    assert before[0]["fingerprint"] != after[0]["fingerprint"]


def test_diagnostic_reports_zero_fills_explicitly(tmp_path, monkeypatch):
    db = setup_env(tmp_path, monkeypatch)
    position, _ = _seed_claim()

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        rc = diagnostic_main(["--db", str(db), "--position-id", position.id])

    assert rc == 0
    assert "0 fills recorded — anti-fabrication invariant preserved" in buffer.getvalue()
    assert f"Snapshot fingerprint: {_fingerprint(position.id)}" in buffer.getvalue()


# ── resolver refusals ───────────────────────────────────────────────────────


def test_resolver_rejects_stale_fingerprint(tmp_path, monkeypatch):
    db = setup_env(tmp_path, monkeypatch)
    position, _ = _seed_claim()
    _confirm(monkeypatch, position.id, "operator_settled_outside_gateway")

    rc = _resolve(db, position.id, "0" * 64, "operator_settled_outside_gateway")

    assert rc == 2
    stored = _stored_position(position.id)
    assert stored["pending_close_status"] == MANUAL
    assert stored["pending_close_order_id"] == "order-terminal"
    assert repo._select_all(
        "audit_events", where="event_type=?", params=(RESOLVED_CODE,), limit=10
    ) == []


def test_resolver_rejects_wrong_confirmation_string(tmp_path, monkeypatch):
    db = setup_env(tmp_path, monkeypatch)
    position, _ = _seed_claim()
    ack = _fingerprint(position.id)
    _confirm(
        monkeypatch,
        position.id,
        "operator_settled_outside_gateway",
        literal=f"resolve {position.id} as operator_settled_outside_gateway",  # wrong case
    )

    rc = _resolve(db, position.id, ack, "operator_settled_outside_gateway")

    assert rc == 4
    assert _stored_position(position.id)["pending_close_status"] == MANUAL
    assert repo._select_all(
        "audit_events", where="event_type=?", params=(RESOLVED_CODE,), limit=10
    ) == []


def test_resolver_rejects_non_manual_position(tmp_path, monkeypatch):
    db = setup_env(tmp_path, monkeypatch)
    position, _ = _seed_claim(status="submission_unknown")
    ack = _fingerprint(position.id)
    _confirm(monkeypatch, position.id, "operator_settled_outside_gateway")

    rc = _resolve(db, position.id, ack, "operator_settled_outside_gateway")

    assert rc == 3
    assert _stored_position(position.id)["pending_close_status"] == "submission_unknown"


# ── resolver modes ──────────────────────────────────────────────────────────


def test_venue_confirmed_fill_inserts_fill_and_clears_claim(tmp_path, monkeypatch):
    db = setup_env(tmp_path, monkeypatch)
    position, close = _seed_claim()
    ack = _fingerprint(position.id)
    _confirm(monkeypatch, position.id, "venue_confirmed_fill")

    rc = _resolve(
        db, position.id, ack, "venue_confirmed_fill",
        extra=["--fill-id", "VF-42", "--fill-quantity", "1.0", "--fill-price", "101.5"],
    )

    assert rc == 0
    fills = repo.list_fills(order_id=close.id)
    assert len(fills) == 1
    assert fills[0]["id"] == "VF-42"
    assert fills[0]["fill_quantity"] == 1.0
    assert fills[0]["fill_price"] == 101.5

    stored = _stored_position(position.id)
    assert stored["pending_close_status"] is None
    assert stored["pending_close_order_id"] is None

    audits = repo._select_all(
        "audit_events", where="event_type=?", params=(RESOLVED_CODE,), limit=10
    )
    assert len(audits) == 1
    assert audits[0]["actor"] == "human"
    assert audits[0]["entity_id"] == position.id
    assert json.loads(audits[0]["after_state"])["resolution_mode"] == "venue_confirmed_fill"
    assert json.loads(audits[0]["after_state"])["venue_fill_id"] == "VF-42"

    # The ordinary reconciler owns the status transition; the resolver must not
    # have touched it.
    assert _stored_order(close.id)["status"] == "submission_unknown"


def test_venue_confirmed_absence_transitions_order_and_clears_claim(tmp_path, monkeypatch):
    db = setup_env(tmp_path, monkeypatch)
    position, close = _seed_claim()
    ack = _fingerprint(position.id)
    _confirm(monkeypatch, position.id, "venue_confirmed_absence")

    rc = _resolve(db, position.id, ack, "venue_confirmed_absence")

    assert rc == 0
    assert _stored_order(close.id)["status"] == "submission_rejected"
    stored = _stored_position(position.id)
    assert stored["pending_close_status"] is None
    assert repo.list_fills(order_id=close.id) == []

    audits = repo._select_all(
        "audit_events", where="event_type=?", params=(RESOLVED_CODE,), limit=10
    )
    assert len(audits) == 1
    assert audits[0]["actor"] == "human"
    assert json.loads(audits[0]["after_state"])["resolution_mode"] == "venue_confirmed_absence"


def test_operator_settled_outside_gateway_is_audit_only(tmp_path, monkeypatch):
    db = setup_env(tmp_path, monkeypatch)
    position, close = _seed_claim()
    ack = _fingerprint(position.id)
    _confirm(monkeypatch, position.id, "operator_settled_outside_gateway")

    rc = _resolve(db, position.id, ack, "operator_settled_outside_gateway")

    assert rc == 0
    stored = _stored_position(position.id)
    assert stored["pending_close_status"] is None
    assert _stored_order(close.id)["status"] == "submission_unknown"
    assert repo.list_fills(order_id=close.id) == []
    audits = repo._select_all(
        "audit_events", where="event_type=?", params=(RESOLVED_CODE,), limit=10
    )
    assert len(audits) == 1
    assert audits[0]["actor"] == "human"
    assert "operator investigated the venue" in (audits[0]["message"] or "")


# ── durable + transactional invariants ──────────────────────────────────────


def test_resolver_calls_mark_resolved_from_durable(tmp_path, monkeypatch):
    db = setup_env(tmp_path, monkeypatch)
    position, _ = _seed_claim()
    ack = _fingerprint(position.id)
    _confirm(monkeypatch, position.id, "operator_settled_outside_gateway")

    from aletheia.infra import events as events_mod
    from aletheia.infra.events import (
        clear_infra_events,
        resolution_seq,
        upsert_critical_unresolved,
    )

    clear_infra_events()
    upsert_critical_unresolved(
        "position_manager", INCIDENT_CODE, position.id, "outstanding close incident"
    )
    assert resolution_seq() == 0

    rc = _resolve(db, position.id, ack, "operator_settled_outside_gateway")

    assert rc == 0
    remaining = [
        entry
        for entry in events_mod._critical_unresolved.values()
        if entry["code"] == INCIDENT_CODE and entry["entity_id"] == position.id
    ]
    assert remaining == []
    assert resolution_seq() == 1


def test_all_writes_are_transactional(tmp_path, monkeypatch):
    db = setup_env(tmp_path, monkeypatch)
    position, close = _seed_claim()
    ack = _fingerprint(position.id)
    _confirm(monkeypatch, position.id, "venue_confirmed_fill")

    real_insert = repo.insert_audit_event_once

    def explode(_event):
        raise RuntimeError("audit write failed")

    monkeypatch.setattr(repo, "insert_audit_event_once", explode)
    try:
        rc = _resolve(
            db, position.id, ack, "venue_confirmed_fill",
            extra=["--fill-id", "VF-rollback", "--fill-quantity", "1.0", "--fill-price", "101.5"],
        )
    finally:
        monkeypatch.setattr(repo, "insert_audit_event_once", real_insert)

    assert rc != 0
    stored = _stored_position(position.id)
    assert stored["pending_close_status"] == MANUAL
    assert stored["pending_close_order_id"] == "order-terminal"
    assert repo.list_fills(order_id=close.id) == []
    assert _stored_order(close.id)["status"] == "submission_unknown"
    assert repo._select_all(
        "audit_events", where="event_type=?", params=(RESOLVED_CODE,), limit=10
    ) == []


# ── CLI argument contract ───────────────────────────────────────────────────


def test_reason_is_required(tmp_path, monkeypatch):
    db = setup_env(tmp_path, monkeypatch)
    position, _ = _seed_claim()

    with pytest.raises(SystemExit) as exc:
        resolver_main([
            "--db", str(db),
            "--position-id", position.id,
            "--acknowledge", _fingerprint(position.id),
            "--mode", "operator_settled_outside_gateway",
        ])

    assert exc.value.code != 0


def test_venue_confirmed_fill_requires_fill_args(tmp_path, monkeypatch):
    db = setup_env(tmp_path, monkeypatch)
    position, _ = _seed_claim()

    with pytest.raises(SystemExit) as exc:
        resolver_main([
            "--db", str(db),
            "--position-id", position.id,
            "--acknowledge", _fingerprint(position.id),
            "--mode", "venue_confirmed_fill",
            "--reason", "operator investigated the venue",
            "--fill-quantity", "1.0",
            "--fill-price", "101.5",
        ])

    assert exc.value.code != 0
