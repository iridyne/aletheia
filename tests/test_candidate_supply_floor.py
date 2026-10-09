from __future__ import annotations




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


def test_candidate_supply_drought_reports_recent_main_reason():
    from aletheia.analytics.candidate_supply import evaluate_candidate_supply_drought

    payload = {"executable_count": 1, "main_reason": "ttl_expired"}
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
