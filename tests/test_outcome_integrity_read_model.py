"""Focused tests for central closure semantics and explicit read cohorts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
    (
        "market_type",
        "execution_mode",
        "funding_cost",
        "funding_source",
        "eligible",
    ),
    [
        ("spot", "paper_internal", 0.0, "not_applicable", True),
        ("spot", "paper_internal", 0.0, "local_simulation", False),
        ("usdt_futures", "paper_internal", 0.0, "local_simulation", True),
        ("usdt_futures", "paper_internal", 0.0, "broker_ledger", False),
        ("usdt_futures", "bitget_demo", 0.0, "broker_ledger", True),
        ("usdt_futures", "bitget_demo", 0.0, "local_simulation", False),
        ("usdt_futures", "bitget_live_limited", 0.0, "broker_ledger", True),
        ("usdt_futures", "bitget_live_limited", 0.0, "local_simulation", False),
        ("usdt_futures", "okx_demo", 0.0, "broker_ledger", True),
        ("usdt_futures", "okx_demo", 0.0, "local_simulation", False),
        ("usdt_futures", "okx_live_limited", 0.0, "broker_ledger", True),
        ("usdt_futures", "okx_live_limited", 0.0, "local_simulation", False),
        ("usdt_futures", "binance_testnet", 0.0, "broker_ledger", True),
        ("usdt_futures", "binance_testnet", 0.0, "local_simulation", False),
        ("usdt_futures", "binance_live_limited", 0.0, "broker_ledger", True),
        ("usdt_futures", "binance_live_limited", 0.0, "local_simulation", False),
        ("usdt_futures", "unknown_mode", 0.0, "broker_ledger", False),
    ],
)
def test_funding_provenance_must_match_market_and_execution_mode(
    market_type,
    execution_mode,
    funding_cost,
    funding_source,
    eligible,
):
    from aletheia.analytics.outcome_integrity import assess_position_closure

    assessment = assess_position_closure(
        _closed_row(
            market_type=market_type,
            execution_mode=execution_mode,
            funding_cost=funding_cost,
            funding_source=funding_source,
        )
    )

    assert assessment.eligible is eligible
    assert assessment.reason == (
        "eligible" if eligible else "incomplete_funding_economics"
    )


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
    from aletheia.storage.models import Fill, Order, Position
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
    db = repo.get_db()
    db.execute("UPDATE positions SET status='open' WHERE id='eligible'")
    repo.insert_order(Order(
        id="close-eligible",
        proposal_id=eligible_proposal,
        position_id="eligible",
        client_order_id="close-eligible",
        close_reason="take_profit",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        order_type="market",
        quantity=0.1,
        price=50_100.0,
        execution_mode="paper_internal",
        status="filled",
        created_at="2026-07-12T08:00:00+00:00",
        filled_at="2026-07-12T08:00:00+00:00",
    ))
    repo.insert_fill(Fill(
        id="fill-eligible",
        order_id="close-eligible",
        fill_price=50_100.0,
        fill_quantity=0.1,
        fee=0.0,
        fee_source="estimated",
        filled_at="2026-07-12T08:00:00+00:00",
    ))
    db.execute("UPDATE positions SET status='closed' WHERE id='eligible'")

    kwargs = {
        "execution_mode": "paper_internal",
        "closed_after": "2026-07-12T08:00:00+00:00",
        "closed_before": "2026-07-12T08:04:00+00:00",
    }
    assert {row["id"] for row in raw_account_events(**kwargs)} == {
        "eligible", "admin", "maintenance", "unknown",
    }
    assert [row["id"] for row in eligible_strategy_outcomes(**kwargs)] == ["eligible"]
    assert [
        row["id"]
        for row in eligible_strategy_outcomes(
            immutable_exit_evidence=True, **kwargs
        )
    ] == ["eligible"]
    db.execute("UPDATE positions SET current_price=99_999.0 WHERE id='eligible'")
    assert [
        row["id"]
        for row in eligible_strategy_outcomes(
            immutable_exit_evidence=True, **kwargs
        )
    ] == ["eligible"]
    assert {row["id"] for row in administrative_closures(**kwargs)} == {
        "admin", "maintenance",
    }


def test_raw_account_events_caps_the_latest_rows_then_restores_chronological_order():
    from aletheia.analytics.outcome_integrity import raw_account_events
    from aletheia.storage.sqlite import get_db

    db = get_db()
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    rows = [
        (
            f"history-{index:05d}",
            "BTC/USDT",
            "spot",
            "long",
            1.0,
            100.0,
            "paper_internal",
            "closed",
            (start + timedelta(seconds=index)).isoformat(),
        )
        for index in range(10_001)
    ]
    db.executemany(
        """
        INSERT INTO positions (
            id, symbol, market_type, direction, quantity, entry_price,
            execution_mode, status, closed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    db.commit()

    result = raw_account_events(limit=10_000)

    assert len(result) == 10_000
    assert result[0]["id"] == "history-00001"
    assert result[-1]["id"] == "history-10000"
    assert [row["closed_at"] for row in result] == sorted(
        row["closed_at"] for row in result
    )


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


# ---------------------------------------------------------------------------
# Terminal "funding_permanently_unrecoverable" state (FUND-B, Q3-3 + Q3-4)
# ---------------------------------------------------------------------------
# These pin the read-model behavior that the settler-give-up work relies on.
# Regressing either one would re-freeze the Overview hero card ("— USDT") or
# collapse the distinct terminal signal to "not_marked_eligible" — the
# exact class of bug fixed in aa23804e / d20973d6.


def test_position_realized_pnl_view_prefers_terminal_reason_over_incomplete():
    """Q3-3: `outcome_exclusion_reason='funding_permanently_unrecoverable'`
    wins over the generic `incomplete_funding_economics` fallback so the
    frontend can render the distinct "funding lost" terminal badge instead
    of the muted retry state. Pins pnl.py:1030-1036 elif ordering."""
    from aletheia.portfolio.pnl import position_realized_pnl_view

    terminal_row = _closed_row(
        realized_pnl=None,
        funding_cost=None,
        funding_source="unavailable",
        outcome_eligible=False,
        outcome_exclusion_reason="funding_permanently_unrecoverable",
    )
    terminal_view = position_realized_pnl_view(terminal_row)
    assert terminal_view["realized_pnl"] is None
    assert terminal_view["realized_pnl_complete"] is False
    assert (
        terminal_view["realized_pnl_unavailable_reason"]
        == "funding_permanently_unrecoverable"
    )

    # Companion negative case: an identical unsettled row WITHOUT the
    # terminal reason still surfaces as "incomplete_funding_economics"
    # (i.e. "still retrying"). This locks the branch order — if a refactor
    # were to short-circuit on realized_pnl=None first, the terminal case
    # above would collapse back to "incomplete_funding_economics" and the
    # frontend would lose the distinct terminal signal.
    retrying_row = _closed_row(
        realized_pnl=None,
        funding_cost=None,
        funding_source="unavailable",
        outcome_eligible=False,
        outcome_exclusion_reason="incomplete_funding_economics",
    )
    retrying_view = position_realized_pnl_view(retrying_row)
    assert (
        retrying_view["realized_pnl_unavailable_reason"]
        == "incomplete_funding_economics"
    )


def test_classify_closure_preserves_funding_permanently_unrecoverable_reason():
    """Q3-4: the terminal reason survives `classify_closure` intact, i.e.
    it is present in the `OUTCOME_EXCLUSION_REASONS` whitelist. If a
    refactor drops the whitelist entry, `classify_closure` collapses the
    reason to 'not_marked_eligible' and every downstream consumer
    (frontend badge, settler skip, OutcomeAssessment.reason) silently loses
    the terminal signal — a hard-to-catch regression this one-line
    assertion prevents."""
    from aletheia.analytics.outcome_integrity import (
        OUTCOME_EXCLUSION_REASONS,
        assess_position_closure,
    )

    assert "funding_permanently_unrecoverable" in OUTCOME_EXCLUSION_REASONS

    terminal_row = _closed_row(
        realized_pnl=None,
        funding_cost=None,
        funding_source="unavailable",
        outcome_eligible=False,
        outcome_exclusion_reason="funding_permanently_unrecoverable",
    )
    assessment = assess_position_closure(terminal_row)
    assert assessment.eligible is False
    assert assessment.reason == "funding_permanently_unrecoverable"
