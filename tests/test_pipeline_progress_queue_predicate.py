"""Tests for pipeline_progress queue depth canonical predicate (#1236)."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from aletheia.diagnostics.pipeline_progress import summarize
from aletheia.storage.repositories import count_unresolved_signal_pending
from aletheia.storage.sqlite import get_db


def _insert_opp(db, opp_id: str, *, valid_until: str | None, created_at: str) -> None:
    db.execute(
        "INSERT INTO opportunities "
        "(id, symbol, market, status, valid_until, created_at) "
        "VALUES (?, 'BTC/USDT', 'spot', 'queued', ?, ?)",
        (opp_id, valid_until, created_at),
    )


def _insert_pi(db, pi_id: str, opp_id: str, *, created_at: str) -> None:
    db.execute(
        "INSERT INTO pi_decisions "
        "(id, opportunity_id, action, symbol, direction, created_at) "
        "VALUES (?, ?, 'signal_pending', 'BTC/USDT', 'long', ?)",
        (pi_id, opp_id, created_at),
    )


def test_signal_pending_expired_valid_until_not_counted():
    """A signal_pending with valid_until in the past → NOT counted."""
    db = get_db()
    now = datetime.now(timezone.utc)
    past = (now - timedelta(hours=2)).isoformat()
    future = (now + timedelta(hours=2)).isoformat()
    recent = (now - timedelta(minutes=1)).isoformat()

    # Expired: valid_until in past
    _insert_opp(db, "opp-test-exp", valid_until=past, created_at=recent)
    _insert_pi(db, "pi-test-exp", "opp-test-exp", created_at=recent)

    # Live: valid_until in future
    _insert_opp(db, "opp-test-live", valid_until=future, created_at=recent)
    _insert_pi(db, "pi-test-live", "opp-test-live", created_at=recent)
    db.commit()

    canonical_count = count_unresolved_signal_pending(max_age_minutes=180)
    assert canonical_count == 1, f"Expected 1 (only live), got {canonical_count}"

    # pipeline_progress must match canonical count
    summary = summarize(hours=12)
    pending_stats = summary.get("signal_pending")
    assert pending_stats is not None
    assert pending_stats["count"] == canonical_count, (
        f"pipeline_progress count {pending_stats['count']} != canonical {canonical_count}"
    )


def test_signal_pending_future_valid_until_is_counted():
    """A signal_pending with valid_until in the future → IS counted."""
    db = get_db()
    now = datetime.now(timezone.utc)
    future = (now + timedelta(hours=1)).isoformat()
    recent = (now - timedelta(minutes=5)).isoformat()

    _insert_opp(db, "opp-future-ok", valid_until=future, created_at=recent)
    _insert_pi(db, "pi-future-ok", "opp-future-ok", created_at=recent)
    db.commit()

    canonical_count = count_unresolved_signal_pending(max_age_minutes=30)
    assert canonical_count >= 1, f"Expected >= 1, got {canonical_count}"

    summary = summarize(hours=12)
    pending_stats = summary.get("signal_pending")
    assert pending_stats is not None
    assert pending_stats["count"] >= 1


def test_signal_pending_deferred_not_counted():
    """Active deferred decision does NOT count against queue depth."""
    db = get_db()
    now = datetime.now(timezone.utc)
    past = (now - timedelta(hours=1)).isoformat()
    defer_until = (now + timedelta(hours=1)).isoformat()
    future = (now + timedelta(hours=2)).isoformat()

    _insert_opp(db, "opp-deferred-test", valid_until=future, created_at=past)
    _insert_pi(db, "pi-deferred-test", "opp-deferred-test", created_at=past)
    # Insert an active defer event
    db.execute(
        "INSERT INTO operator_evaluation_events "
        "(id, dedup_key, event_kind, trigger, pending_unresolved_total, "
        "pending_visible_count, queue_snapshot_json, created_at) "
        "VALUES (?, ?, 'operator_decision_deferred_market_quiet', 'test', 1, 1, ?, ?)",
        (
            "defer-ev-test",
            "defer-ev-test",
            f'{{"opportunity_id": "opp-deferred-test", "defer_until": "{defer_until}"}}',
            past,
        ),
    )
    db.commit()

    canonical_count = count_unresolved_signal_pending(max_age_minutes=180)
    assert canonical_count == 0, f"Expected 0 (deferred), got {canonical_count}"

    summary = summarize(hours=12)
    pending_stats = summary.get("signal_pending")
    assert pending_stats is not None
    assert pending_stats["count"] == 0
