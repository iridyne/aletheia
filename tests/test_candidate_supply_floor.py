from __future__ import annotations


def test_candidate_supply_snapshot_empty_candidates_survives_event_write_failure(monkeypatch):
    from aletheia.agent_shell.trading_context import _record_candidate_supply_snapshot
    from aletheia.agent_shell import operator_store

    def fail_create_event(**kwargs):  # noqa: ARG001
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(operator_store, "create_event", fail_create_event)
    layer = {
        "executable_candidates": [],
        "excluded_candidates_summary": [],
    }

    _record_candidate_supply_snapshot(layer)

    supply = layer["candidate_supply"]
    assert supply["executable_count"] == 0
    assert supply["below_floor"] is True
    assert supply["main_reason"] == "no_candidates"
    assert supply["event_status"] == "not_persisted"


def test_candidate_supply_drought_requires_two_consecutive_low_cycles():
    from aletheia.analytics.candidate_supply import evaluate_candidate_supply_drought

    latest_normal = {
        "id": "normal",
        "event_type": "candidate_supply_snapshot",
        "created_at": "2026-07-04T10:02:00Z",
        "payload": {"executable_count": 3, "main_reason": "insufficient_executable_candidates"},
    }
    previous_low = {
        "id": "low",
        "event_type": "candidate_supply_snapshot",
        "created_at": "2026-07-04T10:01:00Z",
        "payload": {"executable_count": 1, "main_reason": "ttl_expired"},
    }

    assert evaluate_candidate_supply_drought([latest_normal, previous_low]) is None


def test_candidate_supply_drought_uses_excluded_summary_main_reason():
    from aletheia.analytics.candidate_supply import (
        build_candidate_supply_snapshot,
        evaluate_candidate_supply_drought,
    )

    payload = build_candidate_supply_snapshot(
        executable_candidates=[{"symbol": "OK/USDT"}],
        excluded_candidates_summary=[
            {"reason": "guard_blocked", "count": 1},
            {"reason": "ttl_expired", "count": 4},
        ],
    )
    events = [
        {
            "id": "cycle-2",
            "event_type": "candidate_supply_snapshot",
            "created_at": "2026-07-04T10:02:00Z",
            "payload": payload,
        },
        {
            "id": "cycle-1",
            "event_type": "candidate_supply_snapshot",
            "created_at": "2026-07-04T10:01:00Z",
            "payload": payload,
        },
    ]

    drought = evaluate_candidate_supply_drought(events)

    assert drought is not None
    assert drought["main_reason"] == "ttl_expired"
    assert drought["executable_count"] == 1
    assert drought["floor"] == 3
