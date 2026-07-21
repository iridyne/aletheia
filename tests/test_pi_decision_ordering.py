"""Regression coverage for deterministic PiDecision read ordering."""

from datetime import datetime, timezone

from aletheia.storage import repositories as repo
from aletheia.storage.models import PiDecision


def test_pi_decision_readers_break_timestamp_ties_by_insertion_order():
    tied_at = datetime.now(timezone.utc).isoformat()
    repo.insert_pi_decision(
        PiDecision(
            id="pending-timestamp-tie",
            created_at=tied_at,
            symbol="BTC/USDT",
            direction="long",
            market_type="spot",
            action="signal_pending",
        )
    )
    repo.insert_pi_decision(
        PiDecision(
            id="final-timestamp-tie",
            created_at=tied_at,
            symbol="BTC/USDT",
            direction="long",
            market_type="spot",
            action="trade",
            bear_case="final audit context",
        )
    )

    rows = repo.list_pi_decisions(limit=2)
    assert [row["id"] for row in rows] == [
        "final-timestamp-tie",
        "pending-timestamp-tie",
    ]

    latest = repo.latest_pi_decision("BTC/USDT", max_age_minutes=1)
    assert latest is not None
    assert latest["id"] == "final-timestamp-tie"
    assert latest["bear_case"] == "final audit context"
