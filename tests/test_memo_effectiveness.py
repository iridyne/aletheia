"""Tests for Module J memo effectiveness multi-memo evaluation (#1263).

Scenarios:
1. Memo A has 2 closes at rotation (insufficient sample); memo B created; 3 more
   A-window losses close later → A gets overturned_rules on re-evaluation.
2. An expired (>72h) memo is still historically evaluated (not blocked by age check).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from aletheia.analytics.memo_effectiveness import evaluate_pending_memos


def _make_memo(generated_at: str, valid_until: str, rules: list | None = None) -> dict:
    return {
        "generated_at": generated_at,
        "valid_until": valid_until,
        "outcome_semantics_version": "v1",
        "clean_window_anchor_id": "test",
        "regime_playbook": rules or [
            {"regime": "bull", "direction": "long", "edge": "strong"},
        ],
        "timing_windows": [],
        "symbol_affinity": [],
        "sample_size": 5,
    }


def _make_item(item_id: str, memo: dict, tags: dict | None = None) -> dict:
    return {
        "id": item_id,
        "content": json.dumps(memo),
        "tags": json.dumps(tags or {}),
    }


def _make_outcome(opened_at: str, regime_tag: str, direction: str, pnl: float) -> dict:
    return {
        "opened_at": opened_at,
        "regime_tag": regime_tag,
        "direction": direction,
        "realized_pnl": pnl,
    }


def test_late_outcomes_overturn_memo_a_after_rotation():
    """Memo A is evaluated with its full window bounded by Memo B's creation.

    Setup:
    - Memo A: generated 2025-01-01, valid_until 2025-01-03
    - Memo B: generated 2025-01-02 (rotation) — already has terminal accuracy_score
    - Memo A's effective window: [2025-01-01, 2025-01-02)
    - 5 A-window losses → strong edge claim overturned
    """
    memo_a_gen = "2025-01-01T00:00:00+00:00"
    memo_b_gen = "2025-01-02T00:00:00+00:00"
    memo_a_valid = "2025-01-03T00:00:00+00:00"

    memo_a = _make_memo(memo_a_gen, memo_a_valid)
    memo_b = _make_memo(memo_b_gen, "2025-01-05T00:00:00+00:00", rules=[])
    item_a = _make_item("memo-a", memo_a)
    item_b = _make_item("memo-b", memo_b, tags={"accuracy_score": 1.0})  # B already terminal

    # 5 A-window outcomes (all before 2025-01-02): all "bull long" losses
    outcomes = [
        _make_outcome("2025-01-01T10:00:00+00:00", "bull", "long", -10.0),
        _make_outcome("2025-01-01T11:00:00+00:00", "bull", "long", -10.0),
        _make_outcome("2025-01-01T12:00:00+00:00", "bull", "long", -10.0),
        _make_outcome("2025-01-01T13:00:00+00:00", "bull", "long", -10.0),
        _make_outcome("2025-01-01T14:00:00+00:00", "bull", "long", -10.0),
    ]

    updated_calls = []

    def fake_update(item_id, **kwargs):
        updated_calls.append({"id": item_id, **kwargs})

    with (
        patch("aletheia.storage.repositories.list_knowledge_items", return_value=[item_b, item_a]),
        patch("aletheia.storage.repositories.update_knowledge_item", side_effect=fake_update),
        patch("aletheia.analytics.outcome_integrity.eligible_strategy_outcomes", return_value=outcomes),
    ):
        verdicts = evaluate_pending_memos()

    # Only Memo A is pending (B has terminal score)
    assert len(verdicts) == 1
    v = verdicts[0]
    assert v["memo_id"] == "memo-a"
    # 5 losses on "bull long" → strong edge claim overturned
    assert len(v["overturned_rules"]) == 1
    assert v["overturned_rules"][0]["claimed_edge"] == "strong"
    assert v["overturned_rules"][0]["regime"] == "bull"
    # update_knowledge_item called; overturned_rules persisted into content
    assert len(updated_calls) == 1
    saved_content = json.loads(updated_calls[0]["content"])
    assert len(saved_content["overturned_rules"]) == 1


def test_expired_memo_evaluated_historically():
    """A memo older than 72h is still evaluated — age check no longer blocks historical eval."""
    old_gen = (datetime.now(timezone.utc) - timedelta(hours=100)).isoformat()
    old_valid = (datetime.now(timezone.utc) - timedelta(hours=28)).isoformat()

    memo = _make_memo(old_gen, old_valid)
    item = _make_item("old-memo", memo)

    # 4 wins within the memo's window
    outcomes = [
        _make_outcome(
            (datetime.now(timezone.utc) - timedelta(hours=90)).isoformat(),
            "bull", "long", 10.0,
        ),
        _make_outcome(
            (datetime.now(timezone.utc) - timedelta(hours=85)).isoformat(),
            "bull", "long", 10.0,
        ),
        _make_outcome(
            (datetime.now(timezone.utc) - timedelta(hours=80)).isoformat(),
            "bull", "long", 10.0,
        ),
        _make_outcome(
            (datetime.now(timezone.utc) - timedelta(hours=75)).isoformat(),
            "bull", "long", 10.0,
        ),
    ]

    updated_calls = []

    def fake_update(item_id, **kwargs):
        updated_calls.append({"id": item_id, **kwargs})

    with (
        patch("aletheia.storage.repositories.list_knowledge_items", return_value=[item]),
        patch("aletheia.storage.repositories.update_knowledge_item", side_effect=fake_update),
        patch("aletheia.analytics.outcome_integrity.eligible_strategy_outcomes", return_value=outcomes),
    ):
        verdicts = evaluate_pending_memos()

    assert len(verdicts) == 1
    v = verdicts[0]
    assert v["memo_id"] == "old-memo"
    assert v["overturned_rules"] == []
    assert v["accuracy_score"] == 1.0
    assert len(updated_calls) == 1
