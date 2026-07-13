"""Focused tests for central closure semantics and explicit read cohorts."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from hypothesis import given, strategies as st


def _closed_row(**overrides) -> dict:
    row = {
        "id": "position-1",
        "status": "closed",
        "symbol": "BTC/USDT",
        "market_type": "usdt_futures",
        "direction": "long",
        "quantity": 0.1,
        "entry_price": 50_000.0,
        "current_price": 50_250.0,
        "entry_fee": 0.0,
        "exit_fee": 0.0,
        "funding_cost": 0.0,
        "funding_source": "local_simulation",
        "realized_pnl": 25.0,
        "execution_mode": "paper_internal",
        "opportunity_id": "opportunity-1",
        "proposal_id": "proposal-1",
        "order_id": "order-1",
        "_attribution_complete": 1,
        "close_reason": "take_profit",
        "closed_at": "2026-07-12T05:00:00+00:00",
        "close_category": "strategy",
        "entry_price_source": "local_fill",
        "entry_fill_verified": True,
        "exit_price_source": "local_fill",
        "exit_fill_verified": True,
        "outcome_eligible": True,
        "outcome_exclusion_reason": "eligible",
        "outcome_semantics_version": "oi-v1",
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize(
    ("reason", "category"),
    [
        ("reconciled_gone", "administrative"),
        ("demo_validation_cleanup", "maintenance"),
        ("units_bug_cleanup", "maintenance"),
        ("retry_after_fix", "maintenance"),
    ],
)
def test_known_synthetic_reasons_are_centrally_classified(reason, category):
    from aletheia.analytics.outcome_integrity import assess_position_closure

    assessment = assess_position_closure(_closed_row(close_reason=reason))
    assert assessment.eligible is False
    assert assessment.category == category
    assert assessment.reason in {"administrative_close", "maintenance_close"}


def test_verified_structured_strategy_close_is_eligible():
    from aletheia.analytics.outcome_integrity import assess_position_closure

    assessment = assess_position_closure(_closed_row())
    assert assessment.eligible is True
    assert assessment.reason == "eligible"
    assert assessment.category == "strategy"
    assert assessment.outcome_key == "position:position-1"
    assert assessment.net_return_pct == pytest.approx(0.5)
    assert assessment.evidence_source == "structured"


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"exit_fill_verified": False}, "unverified_exit_fill"),
        ({"outcome_eligible": False}, "not_marked_eligible"),
        ({"outcome_eligible": False, "outcome_exclusion_reason": "made_up"}, "not_marked_eligible"),
        ({"outcome_semantics_version": "oi-v0"}, "unknown_semantics_version"),
        ({"close_category": "unknown"}, "unknown_close_category"),
        ({"exit_price_source": "mark_fallback"}, "unverified_exit_price_source"),
        ({"realized_pnl": None}, "incomplete_realized_economics"),
        ({"id": None}, "missing_outcome_identity"),
        ({"closed_at": None}, "missing_close_time"),
    ],
)
def test_structured_contradictions_fail_closed(changes, reason):
    from aletheia.analytics.outcome_integrity import assess_position_closure

    assessment = assess_position_closure(_closed_row(**changes))
    assert assessment.eligible is False
    assert assessment.reason == reason


def test_legacy_or_unclassified_close_never_becomes_eligible_from_pnl_alone():
    from aletheia.analytics.outcome_integrity import assess_position_closure

    legacy = {
        key: value
        for key, value in _closed_row().items()
        if key not in {
            "close_category", "entry_price_source", "entry_fill_verified",
            "exit_price_source", "exit_fill_verified",
            "outcome_eligible", "outcome_exclusion_reason",
            "outcome_semantics_version",
        }
    }
    assessment = assess_position_closure(legacy)
    assert assessment.eligible is False
    assert assessment.category == "unknown"
    assert assessment.reason == "legacy_unverified_close"


@given(
    outcome_eligible=st.booleans(),
    category=st.sampled_from(["strategy", "administrative", "maintenance", "unknown"]),
    price_source=st.sampled_from(["broker_fill", "local_fill", "mark_fallback", "unavailable"]),
)
def test_unverified_fill_can_never_be_eligible(outcome_eligible, category, price_source):
    from aletheia.analytics.outcome_integrity import assess_position_closure

    assessment = assess_position_closure(_closed_row(
        outcome_eligible=outcome_eligible,
        close_category=category,
        exit_price_source=price_source,
        exit_fill_verified=False,
    ))
    assert assessment.eligible is False


def test_explicit_read_models_partition_closed_positions():
    from aletheia.analytics.outcome_integrity import (
        administrative_closures,
        eligible_strategy_outcomes,
        raw_account_events,
    )
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position
    from tests.test_outcome_closure import _insert_entry_chain

    common = {
        "symbol": "BTC/USDT", "market_type": "usdt_futures", "direction": "long",
        "quantity": 0.1, "entry_price": 50_000.0, "realized_pnl": 10.0,
        "execution_mode": "paper_internal", "status": "closed",
    }
    eligible_opportunity, eligible_proposal, eligible_order = _insert_entry_chain(
        position_id="eligible",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.1,
        price=50_000.0,
        execution_mode="paper_internal",
    )
    other_opportunity, other_proposal, other_order = _insert_entry_chain(
        position_id="other-mode",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.1,
        price=50_000.0,
        execution_mode="okx_demo",
    )
    rows = [
        Position(
            **common, id="eligible", close_reason="take_profit",
            closed_at="2026-07-12T08:00:00+00:00", close_category="strategy",
            current_price=50_100.0, entry_fee=0.0, exit_fee=0.0,
            funding_cost=0.0, funding_source="local_simulation",
            entry_price_source="local_fill", entry_fill_verified=True,
            exit_price_source="local_fill", exit_fill_verified=True,
            outcome_eligible=True, outcome_exclusion_reason="eligible",
            outcome_semantics_version="oi-v1",
            opportunity_id=eligible_opportunity, proposal_id=eligible_proposal,
            order_id=eligible_order,
        ),
        Position(
            **common, id="admin", close_reason="reconciled_gone",
            closed_at="2026-07-12T08:01:00+00:00",
        ),
        Position(
            **common, id="maintenance", close_reason="units_bug_cleanup",
            closed_at="2026-07-12T08:02:00+00:00",
        ),
        Position(
            **common, id="unknown", close_reason="manual",
            closed_at="2026-07-12T08:03:00+00:00",
        ),
        Position(
            **{**common, "execution_mode": "okx_demo"}, id="other-mode",
            close_reason="take_profit", closed_at="2026-07-12T08:04:00+00:00",
            close_category="strategy", exit_price_source="broker_fill",
            current_price=50_100.0, entry_fee=0.0, exit_fee=0.0,
            funding_cost=0.0, funding_source="broker_ledger",
            entry_price_source="broker_fill", entry_fill_verified=True,
            exit_fill_verified=True, outcome_eligible=True,
            outcome_exclusion_reason="eligible", outcome_semantics_version="oi-v1",
            opportunity_id=other_opportunity, proposal_id=other_proposal,
            order_id=other_order,
        ),
    ]
    for row in rows:
        repo.insert_position(row)

    kwargs = {
        "execution_mode": "paper_internal",
        "closed_after": "2026-07-12T08:00:00+00:00",
        "closed_before": "2026-07-12T08:04:00+00:00",
    }
    assert {row["id"] for row in raw_account_events(**kwargs)} == {
        "eligible", "admin", "maintenance", "unknown",
    }
    assert [row["id"] for row in eligible_strategy_outcomes(**kwargs)] == ["eligible"]
    assert {row["id"] for row in administrative_closures(**kwargs)} == {
        "admin", "maintenance",
    }


def test_learning_realized_assessment_composes_central_semantics():
    from aletheia.learning.outcomes import assess_realized_position

    clean = _closed_row(closed_at=datetime.now(timezone.utc).isoformat())
    eligible = assess_realized_position(clean)
    assert eligible.eligible is True
    assert eligible.outcome_key == "position:position-1"

    assert assess_realized_position({**clean, "exit_fill_verified": False}).eligible is False
    assert assess_realized_position({**clean, "close_reason": "reconciled_gone"}).eligible is False


def test_close_reason_compatibility_api_delegates_to_central_registry():
    from aletheia.analytics.close_reasons import (
        SYNTHETIC_CLOSE_REASONS,
        is_synthetic_close,
    )
    from aletheia.analytics.outcome_integrity import SYNTHETIC_CLOSE_REASONS as CENTRAL

    assert SYNTHETIC_CLOSE_REASONS is CENTRAL
    assert is_synthetic_close("reconciled_gone")
    assert not is_synthetic_close("take_profit")
