"""Stable-escalation routing for stale pending-close claims (INV-SCH-11).

A stale pending-close claim whose durable reconciliation record already exists
must be reported under ``escalated``, not ``errors``: an already-recorded,
human-owned condition must not keep failing the position_manager job. The
routing is reconstructed from the audit table on every pass so it survives a
process restart and a crash between the audit insert and the infra emit.
"""

import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import AuditEvent, Position
from aletheia.storage import repositories as repo

PENDING_CLOSE_CODE = "pending_close_reconciliation_required"


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


def _stuck_position(*, symbol="KORU/USDT", order_id="stuck-close", age_hours=5.0):
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
        pending_close_status="submission_unknown",
        pending_close_requested_at=(
            datetime.now(timezone.utc) - timedelta(hours=age_hours)
        ).isoformat(),
    )


def _durable_escalation(position: Position) -> str:
    """Seed the exact audit row a prior process would have written."""
    from aletheia.portfolio.position_manager import _pending_close_claim_identity

    claim_identity = _pending_close_claim_identity(position.model_dump())
    digest = hashlib.sha256(
        f"{position.id}\0{claim_identity}".encode()
    ).hexdigest()
    audit_id = f"pending-close-reconciliation-{digest}"
    repo.insert_audit_event(AuditEvent(
        id=audit_id,
        actor="system",
        source="position_manager",
        event_type=PENDING_CLOSE_CODE,
        entity_type="position",
        entity_id=position.id,
        message="prior process escalation",
    ))
    return audit_id


def _codes(items):
    return [item["code"] for item in items]


def test_first_stuck_claim_reports_error_then_escalated_on_next_pass(
    tmp_path, monkeypatch
):
    setup_env(tmp_path, monkeypatch)
    p = _stuck_position()
    repo.insert_position(p)

    from aletheia.portfolio.position_manager import evaluate_open_positions

    first = evaluate_open_positions()
    assert PENDING_CLOSE_CODE in _codes(first["errors"])
    assert PENDING_CLOSE_CODE not in _codes(first["escalated"])

    second = evaluate_open_positions()
    assert PENDING_CLOSE_CODE in _codes(second["escalated"])
    assert PENDING_CLOSE_CODE not in _codes(second["errors"])

    escalated_finding = next(
        item for item in second["escalated"] if item["code"] == PENDING_CLOSE_CODE
    )
    assert escalated_finding["position_id"] == p.id
    assert escalated_finding["claim_identity"] == f"order:{p.pending_close_order_id}"
    assert escalated_finding["escalated_at"]

    # The condition is durably recorded exactly once, never re-inserted.
    rows = repo._select_all(
        "audit_events",
        where="event_type=? AND entity_id=?",
        params=(PENDING_CLOSE_CODE, p.id),
        limit=10,
    )
    assert len(rows) == 1


def test_escalation_state_reconstructed_from_audit_after_restart(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    p = _stuck_position()
    repo.insert_position(p)
    _durable_escalation(p)

    from aletheia.portfolio.position_manager import evaluate_open_positions

    # A fresh process has no in-memory escalation memory; the row is rebuilt.
    result = evaluate_open_positions()
    assert PENDING_CLOSE_CODE in _codes(result["escalated"])
    assert PENDING_CLOSE_CODE not in _codes(result["errors"])
    finding = next(i for i in result["escalated"] if i["code"] == PENDING_CLOSE_CODE)
    assert finding["escalated_at"]


def test_crash_between_audit_insert_and_infra_emit_reconstructs_visibility(
    tmp_path, monkeypatch
):
    setup_env(tmp_path, monkeypatch)
    p = _stuck_position()
    repo.insert_position(p)
    _durable_escalation(p)

    from aletheia.agent_runtime import scheduler
    from aletheia.infra.events import clear_infra_events, list_infra_events

    # The prior process wrote the durable record but died before emitting.
    clear_infra_events()
    scheduler._consecutive_failures.clear()

    scheduler._run_position_manager()

    reproduced = [
        event for event in list_infra_events(limit=50)
        if event["code"] == "position_manager_stable_escalation"
    ]
    assert len(reproduced) == 1
    assert reproduced[0]["severity"] == "warning"
    assert reproduced[0]["entity_id"] == p.id
    assert reproduced[0]["metadata"]["claim_identity"] == (
        f"order:{p.pending_close_order_id}"
    )
    assert reproduced[0]["metadata"]["finding_code"] == PENDING_CLOSE_CODE


def test_audit_write_exception_is_treated_as_fresh_error(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    p = _stuck_position()
    repo.insert_position(p)

    from aletheia.storage import repositories as repositories_mod
    from aletheia.portfolio.position_manager import evaluate_open_positions

    def boom(*_args, **_kwargs):
        raise sqlite3.OperationalError("audit sink down")

    monkeypatch.setattr(repositories_mod, "insert_audit_event_once", boom)

    result = evaluate_open_positions()
    assert PENDING_CLOSE_CODE in _codes(result["errors"])
    assert PENDING_CLOSE_CODE not in _codes(result["escalated"])


def test_audit_false_but_row_absent_is_treated_as_fresh_error(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    p = _stuck_position()
    repo.insert_position(p)

    from aletheia.storage import repositories as repositories_mod
    from aletheia.portfolio.position_manager import evaluate_open_positions

    monkeypatch.setattr(
        repositories_mod, "insert_audit_event_once", lambda *_a, **_k: False
    )

    result = evaluate_open_positions()
    assert PENDING_CLOSE_CODE in _codes(result["errors"])
    assert PENDING_CLOSE_CODE not in _codes(result["escalated"])


def test_same_position_can_yield_escalated_and_fresh_findings(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    p = _stuck_position()
    repo.insert_position(p)
    _durable_escalation(p)

    from aletheia.portfolio.position_manager import evaluate_open_positions

    # No market snapshot → the protective mark lookup fails. That fresh finding
    # is per-finding exclusive from the already-escalated pending-close finding.
    result = evaluate_open_positions()

    escalated_for_pos = [i for i in result["escalated"] if i["position_id"] == p.id]
    errors_for_pos = [i for i in result["errors"] if i["position_id"] == p.id]

    assert PENDING_CLOSE_CODE in _codes(escalated_for_pos)
    assert set(_codes(errors_for_pos)) & {
        "protective_mark_unavailable",
        "protective_mark_lookup_failed",
    }


def test_stable_escalation_infra_event_emitted_every_pass_bounded_by_dedupe(
    tmp_path, monkeypatch
):
    setup_env(tmp_path, monkeypatch)

    from aletheia.agent_runtime import scheduler
    from aletheia.portfolio import position_manager as pm
    from aletheia.infra import events as events_mod
    from aletheia.infra.events import clear_infra_events, list_infra_events

    payload = {
        "position_id": "pos-stable",
        "symbol": "KORU/USDT",
        "market_type": "usdt_futures",
        "code": PENDING_CLOSE_CODE,
        "escalated_at": "2026-09-27T00:00:00+00:00",
        "claim_identity": "order:stuck-close",
    }
    monkeypatch.setattr(
        pm, "evaluate_open_positions", lambda: {"errors": [], "escalated": [dict(payload)]}
    )
    clock = {"now": 1_000.0}
    monkeypatch.setattr(events_mod.time, "time", lambda: clock["now"])
    clear_infra_events()
    scheduler._consecutive_failures.clear()

    counts = []
    for pass_index in range(10):
        clock["now"] = 1_000.0 + pass_index * 100.0
        scheduler._run_position_manager()
        counts.append(sum(
            1
            for event in list_infra_events(limit=100)
            if event["code"] == "position_manager_stable_escalation"
        ))

    # Pass 1 (t=0) emits; pass 2 (t=100) is inside the 600s window; pass 7
    # (t=600) re-emits; passes 8-10 fall back inside the refreshed window.
    assert counts == [1] * 6 + [2] * 4
