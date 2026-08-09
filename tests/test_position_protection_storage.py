from __future__ import annotations

import json
import sqlite3

import pytest

from aletheia.storage import repositories as repo
from aletheia.storage.models import (
    Fill,
    Order,
    Position,
    PositionExitPlanVersion,
    PositionProtectionState,
    PositionProtectionTrigger,
)
from aletheia.storage.sqlite import get_db


_ACTIVATED_AT = "2026-08-08T03:00:00+00:00"
_FIRST_BAR_OPEN_MS = 1_786_158_060_000


def _insert_position(
    *,
    position_id: str = "position-protected",
    market_type: str = "usdt_futures",
) -> Position:
    position = Position(
        id=position_id,
        symbol="BTC/USDT",
        market_type=market_type,
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        current_price=100.0,
        execution_mode="paper_internal",
        stop_price=90.0,
        take_profit_price=110.0,
        opened_at=_ACTIVATED_AT,
    )
    repo.insert_position(position)
    return position


def _plan(
    position_id: str,
    *,
    version: int = 1,
    effective_at: str = _ACTIVATED_AT,
    stop_price: float = 90.0,
    take_profit_price: float = 110.0,
) -> PositionExitPlanVersion:
    payload = json.dumps(
        {
            "exit_limit_price": None,
            "exit_limit_source": None,
            "stop_price": stop_price,
            "take_profit_price": take_profit_price,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return PositionExitPlanVersion(
        position_id=position_id,
        version=version,
        effective_at=effective_at,
        stop_price=stop_price,
        take_profit_price=take_profit_price,
        exit_limit_price=None,
        exit_limit_source=None,
        plan_json=payload,
        plan_hash=repo.hash_position_exit_plan_json(payload),
        provenance="position_entry_v1",
        created_at=effective_at,
    )


def _state(position_id: str) -> PositionProtectionState:
    return PositionProtectionState(
        position_id=position_id,
        venue="bitget",
        symbol="BTC/USDT",
        market_type="perp",
        activated_at=_ACTIVATED_AT,
        next_bar_open_ms=_FIRST_BAR_OPEN_MS,
        created_at=_ACTIVATED_AT,
        updated_at=_ACTIVATED_AT,
    )


def _trigger(position_id: str) -> PositionProtectionTrigger:
    bar = {
        "venue": "bitget",
        "symbol": "BTC/USDT",
        "market_type": "perp",
        "timeframe": "1m",
        "bar_open_ms": _FIRST_BAR_OPEN_MS,
        "bar_close_ms": _FIRST_BAR_OPEN_MS + 60_000,
        "bar_open": 100.0,
        "bar_high": 111.0,
        "bar_low": 89.0,
        "bar_close": 105.0,
        "bar_volume": 123.0,
        "provider": "ccxt:bitget",
    }
    return PositionProtectionTrigger(
        position_id=position_id,
        trigger_id="00000000-0000-4000-8000-000000000001",
        detected_at="2026-08-08T03:02:00+00:00",
        **bar,
        bar_hash=repo.hash_position_protection_bar(**bar),
        plan_version=1,
        plan_effective_at=_ACTIVATED_AT,
        plan_hash=_plan(position_id).plan_hash,
        stop_price=90.0,
        upside_price=110.0,
        stop_hit=True,
        target_hit=True,
        reason="stop_loss",
    )


def _prepare(position_id: str = "position-protected") -> Position:
    position = _insert_position(position_id=position_id)
    assert repo.append_position_exit_plan_version(
        _plan(position.id),
        expected_effective_at=None,
    )
    assert repo.insert_position_protection_state(_state(position.id))
    return position


def test_exit_plan_append_is_atomic_contiguous_and_immutable():
    position = _insert_position()
    first = _plan(position.id)

    with pytest.raises(sqlite3.IntegrityError, match="must start without"):
        repo.insert_position(
            position.model_copy(
                update={
                    "id": "position-projection-without-version",
                    "exit_plan_effective_at": _ACTIVATED_AT,
                }
            )
        )
    assert repo.append_position_exit_plan_version(
        first,
        expected_effective_at=None,
    )
    assert repo.get_position(position.id)["exit_plan_effective_at"] == first.effective_at
    assert (
        repo.append_position_exit_plan_version(
            first,
            expected_effective_at=first.effective_at,
        )
        is False
    )

    conflicting_payload = _plan(position.id, stop_price=91.0)
    with pytest.raises(repo.PositionProtectionConflict, match="version already exists"):
        repo.append_position_exit_plan_version(
            conflicting_payload,
            expected_effective_at=first.effective_at,
        )

    second = _plan(
        position.id,
        version=2,
        effective_at="2026-08-08T03:05:00+00:00",
        stop_price=92.0,
    )
    assert repo.append_position_exit_plan_version(
        second,
        expected_effective_at=first.effective_at,
    )
    assert repo.get_current_position_exit_plan(position.id)["version"] == 2
    with pytest.raises(repo.PositionProtectionConflict, match="no longer the current"):
        repo.append_position_exit_plan_version(
            first,
            expected_effective_at=first.effective_at,
        )

    gap = _plan(
        position.id,
        version=4,
        effective_at="2026-08-08T03:06:00+00:00",
    )
    with pytest.raises(repo.PositionProtectionConflict, match="expected version 3"):
        repo.append_position_exit_plan_version(
            gap,
            expected_effective_at=second.effective_at,
        )
    assert repo.get_position(position.id)["exit_plan_effective_at"] == second.effective_at

    db = get_db()
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute(
            "UPDATE position_exit_plan_versions SET stop_price=95 WHERE position_id=? AND version=1",
            (position.id,),
        )
    with pytest.raises(sqlite3.IntegrityError, match="retained"):
        db.execute(
            "DELETE FROM position_exit_plan_versions WHERE position_id=? AND version=1",
            (position.id,),
        )


def test_protection_state_freezes_exact_identity_and_unknown_market_fails_closed():
    position = _insert_position()
    assert repo.append_position_exit_plan_version(
        _plan(position.id),
        expected_effective_at=None,
    )
    with pytest.raises(repo.PositionProtectionConflict, match="canonical BASE/QUOTE"):
        repo.insert_position_protection_state(_state(position.id).model_copy(update={"symbol": "BTCUSDT"}))
    assert repo.insert_position_protection_state(_state(position.id))
    assert repo.insert_position_protection_state(_state(position.id)) is False

    stored = repo.get_position_protection_state(position.id)
    assert (stored["venue"], stored["symbol"], stored["market_type"], stored["timeframe"]) == (
        "bitget",
        "BTC/USDT",
        "perp",
        "1m",
    )
    with pytest.raises(sqlite3.IntegrityError, match="identity is immutable"):
        get_db().execute(
            "UPDATE position_protection_state SET venue='okx' WHERE position_id=?",
            (position.id,),
        )

    unknown = _insert_position(position_id="unknown-market", market_type="futures")
    assert repo.append_position_exit_plan_version(
        _plan(unknown.id),
        expected_effective_at=None,
    )
    with pytest.raises(repo.PositionProtectionConflict, match="unknown market_type"):
        repo.insert_position_protection_state(_state(unknown.id))
    assert repo.get_position_protection_state(unknown.id) is None


def test_cursor_and_gap_updates_are_monotonic_compare_and_set():
    position = _prepare()

    with pytest.raises(repo.PositionProtectionConflict, match="before the bar closes"):
        repo.record_position_protection_gap(
            position.id,
            expected_next_bar_open_ms=_FIRST_BAR_OPEN_MS,
            inspected_bar_open_ms=_FIRST_BAR_OPEN_MS,
            reason="missing_contiguous_bar",
            detected_at="2026-08-08T03:01:30+00:00",
        )
    assert repo.record_position_protection_gap(
        position.id,
        expected_next_bar_open_ms=_FIRST_BAR_OPEN_MS,
        inspected_bar_open_ms=_FIRST_BAR_OPEN_MS,
        reason="missing_contiguous_bar",
        detected_at="2026-08-08T03:02:00+00:00",
        recent_error="provider returned no exact 1m bar",
    )
    assert (
        repo.record_position_protection_gap(
            position.id,
            expected_next_bar_open_ms=_FIRST_BAR_OPEN_MS,
            inspected_bar_open_ms=_FIRST_BAR_OPEN_MS,
            reason="missing_contiguous_bar",
            detected_at="2026-08-08T03:02:30+00:00",
        )
        is False
    )

    assert repo.advance_position_protection_cursor(
        position.id,
        expected_next_bar_open_ms=_FIRST_BAR_OPEN_MS,
        evaluated_bar_open_ms=_FIRST_BAR_OPEN_MS,
        next_bar_open_ms=_FIRST_BAR_OPEN_MS + 60_000,
        evaluated_at="2026-08-08T03:03:00+00:00",
    )
    assert (
        repo.advance_position_protection_cursor(
            position.id,
            expected_next_bar_open_ms=_FIRST_BAR_OPEN_MS,
            evaluated_bar_open_ms=_FIRST_BAR_OPEN_MS,
            next_bar_open_ms=_FIRST_BAR_OPEN_MS + 60_000,
            evaluated_at="2026-08-08T03:03:00+00:00",
        )
        is False
    )

    stored = repo.get_position_protection_state(position.id)
    assert stored["status"] == "active"
    assert stored["gap_count"] == 1
    assert stored["next_bar_open_ms"] == _FIRST_BAR_OPEN_MS + 60_000
    assert stored["last_evaluated_bar_open_ms"] == _FIRST_BAR_OPEN_MS
    with pytest.raises(sqlite3.IntegrityError, match="cursor cannot regress"):
        get_db().execute(
            "UPDATE position_protection_state SET next_bar_open_ms=? WHERE position_id=?",
            (_FIRST_BAR_OPEN_MS, position.id),
        )


def test_first_trigger_is_coherent_write_once_and_stops_cursor_updates():
    position = _prepare()
    trigger = _trigger(position.id)

    with pytest.raises(repo.PositionProtectionConflict, match="trigger_id must be a UUID"):
        repo.record_position_protection_trigger(trigger.model_copy(update={"trigger_id": "not-a-uuid"}))
    assert repo.record_position_protection_trigger(trigger)
    assert repo.record_position_protection_trigger(trigger) is False

    stored = repo.get_position_protection_state(position.id)
    assert stored["status"] == "triggered"
    assert stored["trigger_id"] == trigger.trigger_id
    evidence = repo.get_position_protection_trigger(position.id)
    assert evidence is not None
    assert evidence["collision_policy"] == "stop_first_v1"
    assert evidence["stop_hit"] == 1
    assert evidence["target_hit"] == 1
    assert (
        repo.advance_position_protection_cursor(
            position.id,
            expected_next_bar_open_ms=_FIRST_BAR_OPEN_MS + 60_000,
            evaluated_bar_open_ms=_FIRST_BAR_OPEN_MS + 60_000,
            next_bar_open_ms=_FIRST_BAR_OPEN_MS + 120_000,
            evaluated_at="2026-08-08T03:04:00+00:00",
        )
        is False
    )

    conflicting = trigger.model_copy(update={"trigger_id": "00000000-0000-4000-8000-000000000002"})
    with pytest.raises(repo.PositionProtectionConflict, match="first trigger already recorded"):
        repo.record_position_protection_trigger(conflicting)
    with pytest.raises(sqlite3.IntegrityError, match="trigger evidence is immutable"):
        get_db().execute(
            "UPDATE position_protection_triggers SET stop_price=95 WHERE position_id=?",
            (position.id,),
        )


def test_trigger_rejects_stale_or_midbar_exit_plan_chronology():
    stale_position = _prepare("stale-plan")
    stale_second = _plan(
        stale_position.id,
        version=2,
        effective_at="2026-08-08T03:00:30+00:00",
        stop_price=92.0,
    )
    assert repo.append_position_exit_plan_version(
        stale_second,
        expected_effective_at=_ACTIVATED_AT,
    )
    with pytest.raises(
        repo.PositionProtectionConflict,
        match="not authoritative at bar open",
    ):
        repo.record_position_protection_trigger(_trigger(stale_position.id))

    boundary_position = _prepare("midbar-plan")
    boundary_second = _plan(
        boundary_position.id,
        version=2,
        effective_at="2026-08-08T03:01:30+00:00",
        stop_price=92.0,
    )
    assert repo.append_position_exit_plan_version(
        boundary_second,
        expected_effective_at=_ACTIVATED_AT,
    )
    boundary_trigger = _trigger(boundary_position.id)
    with pytest.raises(
        repo.PositionProtectionConflict,
        match="crosses an ExitPlan effective boundary",
    ):
        repo.record_position_protection_trigger(boundary_trigger)

    data = boundary_trigger.model_dump()
    columns = ", ".join(data)
    placeholders = ", ".join("?" for _ in data)
    with pytest.raises(sqlite3.IntegrityError, match="ExitPlan is inconsistent"):
        get_db().execute(
            f"INSERT INTO position_protection_triggers ({columns}) "
            f"VALUES ({placeholders})",
            tuple(data.values()),
        )


def test_close_order_and_fill_linkage_are_exact_and_write_once():
    position = _prepare()
    trigger = _trigger(position.id)
    assert repo.record_position_protection_trigger(trigger)

    order = Order(
        id="close-order",
        proposal_id=position.proposal_id,
        risk_decision_id=None,
        symbol=position.symbol,
        market_type=position.market_type,
        direction=position.direction,
        order_type="market",
        quantity=position.quantity,
        price=105.0,
        execution_mode=position.execution_mode,
        client_order_id=f"close-{position.id}",
        position_id=position.id,
        close_reason="protective_barrier",
        status="filled",
        created_at="2026-08-08T03:02:00+00:00",
        filled_at="2026-08-08T03:02:01+00:00",
    )
    repo.insert_order(order)
    fill = Fill(
        id="close-fill",
        order_id=order.id,
        fill_price=105.0,
        fill_quantity=position.quantity,
        fee=0.01,
        filled_at="2026-08-08T03:02:01+00:00",
    )
    repo.insert_fill(fill)

    assert repo.link_position_protection_close_order(
        position.id,
        trigger_id=trigger.trigger_id,
        close_order_id=order.id,
    )
    assert (
        repo.link_position_protection_close_order(
            position.id,
            trigger_id=trigger.trigger_id,
            close_order_id=order.id,
        )
        is False
    )
    with pytest.raises(repo.PositionProtectionConflict, match="already write-once"):
        repo.link_position_protection_close_order(
            position.id,
            trigger_id=trigger.trigger_id,
            close_order_id="different-close-order",
        )

    repo.update_position(position.id, status="closed")
    assert repo.materialize_position_protection_close(
        position.id,
        trigger_id=trigger.trigger_id,
        close_order_id=order.id,
        close_fill_id=fill.id,
    )
    assert (
        repo.materialize_position_protection_close(
            position.id,
            trigger_id=trigger.trigger_id,
            close_order_id=order.id,
            close_fill_id=fill.id,
        )
        is False
    )

    stored = repo.get_position_protection_state(position.id)
    assert stored["status"] == "materialized"
    assert stored["close_order_id"] == order.id
    assert stored["close_fill_id"] == fill.id
    assert stored["close_fill_price"] == fill.fill_price
    assert stored["close_filled_at"] == fill.filled_at
    assert stored["final_state"] == "materialized"
    assert get_db().execute("PRAGMA foreign_key_check").fetchall() == []
    with pytest.raises(sqlite3.IntegrityError, match="close fill evidence is write-once"):
        get_db().execute(
            "UPDATE position_protection_state SET close_fill_price=99 WHERE position_id=?",
            (position.id,),
        )


def test_paper_close_timestamp_recovery_is_offline_bounded_and_idempotent(monkeypatch):
    def seed(position_id: str, trigger_id: str, *, skew_us: int) -> tuple[Order, Fill]:
        position = _prepare(position_id)
        trigger = _trigger(position.id).model_copy(update={"trigger_id": trigger_id})
        assert repo.record_position_protection_trigger(trigger)
        filled_at = "2026-08-08T03:02:01.000000+00:00"
        created_at = f"2026-08-08T03:02:01.{skew_us:06d}+00:00"
        order = Order(
            id=f"close-{position_id}",
            proposal_id=position.proposal_id,
            symbol=position.symbol,
            market_type=position.market_type,
            direction=position.direction,
            order_type="market",
            quantity=position.quantity,
            price=105.0,
            execution_mode="paper_internal",
            client_order_id=f"close-{position.id}",
            position_id=position.id,
            close_reason="protective_barrier",
            status="filled",
            created_at=created_at,
            filled_at=filled_at,
        )
        fill = Fill(
            id=f"fill-{position_id}",
            order_id=order.id,
            fill_price=105.0,
            fill_quantity=position.quantity,
            filled_at=filled_at,
        )
        repo.insert_order(order)
        repo.insert_fill(fill)
        assert repo.link_position_protection_close_order(
            position.id,
            trigger_id=trigger.trigger_id,
            close_order_id=order.id,
        )
        repo.update_position(position.id, status="closed")
        return order, fill

    eligible_order, eligible_fill = seed(
        "recoverable-close",
        "00000000-0000-4000-8000-000000000011",
        skew_us=20,
    )
    blocked_order, _ = seed(
        "out-of-scope-close",
        "00000000-0000-4000-8000-000000000012",
        skew_us=2000,
    )

    from aletheia.maintenance.paper_close_timestamp_recovery import (
        apply_paper_close_timestamp_recovery,
        inspect_paper_close_timestamp_recovery,
    )

    report = inspect_paper_close_timestamp_recovery()
    assert report["summary"] == {
        "already_consistent": 0,
        "repairs_planned": 1,
        "unavailable": 1,
    }
    assert repo.get_order(eligible_order.id)["created_at"] == eligible_order.created_at

    monkeypatch.setattr("aletheia.gateway.pidfile.is_running", lambda: True)
    with pytest.raises(RuntimeError, match="Gateway is running"):
        apply_paper_close_timestamp_recovery(
            expected_fingerprint=report["report_fingerprint"],
        )

    monkeypatch.setattr("aletheia.gateway.pidfile.is_running", lambda: False)
    monkeypatch.setattr(
        "aletheia.files.jsonl.write_audits_deduplicated",
        lambda _events: set(),
    )
    applied = apply_paper_close_timestamp_recovery(
        expected_fingerprint=report["report_fingerprint"],
    )
    assert applied["applied"] == 1
    assert applied["audit_jsonl_complete"] is False
    assert repo.get_order(eligible_order.id)["created_at"] == eligible_fill.filled_at
    assert repo.get_order(blocked_order.id)["created_at"] == blocked_order.created_at
    assert get_db().execute(
        "SELECT COUNT(*) FROM audit_events "
        "WHERE event_type='paper_close_order_timestamp_repaired'"
    ).fetchone()[0] == 1

    retry = inspect_paper_close_timestamp_recovery()
    assert retry["summary"] == {
        "already_consistent": 1,
        "repairs_planned": 0,
        "unavailable": 1,
    }
    monkeypatch.undo()
    retried = apply_paper_close_timestamp_recovery(
        expected_fingerprint=retry["report_fingerprint"],
    )
    assert retried["applied"] == 0
    assert retried["audit_jsonl_complete"] is True
    assert repo.materialize_position_protection_close(
        "recoverable-close",
        trigger_id="00000000-0000-4000-8000-000000000011",
        close_order_id=eligible_order.id,
        close_fill_id=eligible_fill.id,
    )
