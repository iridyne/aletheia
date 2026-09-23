"""Focused tests for central closure semantics and explicit read cohorts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import given
from hypothesis import strategies as st


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
        "funding_source": "broker_ledger",
        "realized_pnl": 25.0,
        "execution_mode": "bitget_demo",
        "opportunity_id": "opportunity-1",
        "proposal_id": "proposal-1",
        "order_id": "order-1",
        "_attribution_complete": 1,
        "close_reason": "take_profit",
        "closed_at": "2026-07-12T05:00:00+00:00",
        "close_category": "strategy",
        "entry_price_source": "broker_fill",
        "entry_fill_verified": True,
        "exit_price_source": "broker_fill",
        "exit_fill_verified": True,
        "outcome_eligible": True,
        "outcome_exclusion_reason": "eligible",
        "outcome_semantics_version": "oi-v2",
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


@pytest.mark.parametrize("fill_model_version", [None, "paper-order-fill/v1"])
def test_archived_fill_models_preserve_economics_without_current_learning_authority(
    fill_model_version,
):
    from aletheia.analytics.outcome_integrity import (
        assess_position_closure,
        assess_reportable_outcome,
    )
    from aletheia.analytics.outcome_semantics import assess_realized_position

    archived = _closed_row(
        execution_mode="paper_internal", funding_source="local_simulation",
        entry_price_source="local_fill", exit_price_source="local_fill",
        closed_at="2026-09-23T17:00:00+00:00",
        _paper_fill_model_version=fill_model_version,
    )
    original = archived.copy()
    historical = assess_position_closure(archived)
    assert historical.eligible is True
    assert historical.net_return_pct == pytest.approx(0.5)
    for assess in (assess_reportable_outcome, assess_realized_position):
        current = assess(archived)
        assert current.eligible is False
        assert current.reason == "archived_execution_mode"
    assert archived == original


@pytest.mark.parametrize(
    (
        "market_type",
        "execution_mode",
        "funding_cost",
        "funding_source",
        "eligible",
    ),
    [
        # Archived rows retain their recorded funding provenance.
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
    from aletheia.storage.models import Position
    from tests.test_outcome_closure import _insert_closed_position_via_producer

    common = {
        "symbol": "BTC/USDT", "market_type": "usdt_futures", "direction": "long",
        "quantity": 0.1, "entry_price": 50_000.0, "realized_pnl": 10.0,
        "execution_mode": "bitget_demo", "status": "closed",
    }
    for position_id, mode, closed_at in (
        ("eligible", "bitget_demo", "2026-09-23T17:00:00+00:00"),
        ("other-mode", "okx_demo", "2026-09-23T17:00:30+00:00"),
    ):
        _insert_closed_position_via_producer(
            position_id=position_id, symbol="BTC/USDT", market_type="usdt_futures",
            direction="long", quantity=0.1, entry_price=50_000.0, realized_pnl=10.0,
            execution_mode=mode, closed_at=closed_at, close_reason="take_profit",
        )
    rows = [
        Position(
            **common, id="admin", close_reason="reconciled_gone",
            closed_at="2026-09-23T17:01:00+00:00",
        ),
        Position(
            **common, id="maintenance", close_reason="units_bug_cleanup",
            closed_at="2026-09-23T17:02:00+00:00",
        ),
        Position(
            **common, id="unknown", close_reason="manual",
            closed_at="2026-09-23T17:03:00+00:00",
        ),
    ]
    for row in rows:
        repo.insert_position(row)

    kwargs = {
        "execution_mode": "bitget_demo",
        "closed_after": "2026-09-23T17:00:00+00:00",
        "closed_before": "2026-09-23T17:04:00+00:00",
    }
    assert {row["id"] for row in raw_account_events(**kwargs)} == {
        "eligible", "admin", "maintenance", "unknown",
    }
    assert [row["id"] for row in eligible_strategy_outcomes(**kwargs)] == ["eligible"]
    assert {row["id"] for row in administrative_closures(**kwargs)} == {
        "admin", "maintenance",
    }


def test_archived_account_events_caps_the_latest_rows_then_restores_chronological_order():
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


@pytest.mark.parametrize("row_count", [9_999, 10_000, 10_001])
def test_complete_outcome_cohort_has_no_10000_boundary_ambiguity(row_count):
    from aletheia.analytics.outcome_integrity import read_complete_outcome_cohort
    from aletheia.storage.sqlite import get_db

    db = get_db()
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    db.executemany(
        """
        INSERT INTO positions (
            id, symbol, market_type, direction, quantity, entry_price,
            execution_mode, status, closed_at
        ) VALUES (?, 'BTC/USDT', 'spot', 'long', 1, 100,
                  'bitget_demo', 'closed', ?)
        """,
        (
            (f"complete-{index:05d}", (start + timedelta(seconds=index)).isoformat())
            for index in range(row_count)
        ),
    )
    db.commit()

    cohort = read_complete_outcome_cohort(execution_mode="bitget_demo", db=db)

    assert cohort.source_raw_event_count == row_count
    assert cohort.loaded_raw_event_count == row_count
    assert cohort.cohort_complete is True
    assert cohort.ordered_source_digest


def test_complete_outcome_cohort_preserves_read_failure_as_unavailable(monkeypatch):
    import aletheia.analytics.outcome_integrity as outcome_integrity

    def fail_read(**_filters):
        raise RuntimeError("read failed")

    monkeypatch.setattr(outcome_integrity, "raw_account_events", fail_read)

    cohort = outcome_integrity.read_complete_outcome_cohort(
        execution_mode="bitget_demo"
    )

    assert cohort.source_raw_event_count is None
    assert cohort.loaded_raw_event_count == 0
    assert cohort.cohort_complete is False
    assert cohort.cohort_state == "unavailable"
    assert cohort.ordered_source_digest is None


def test_archived_account_events_preserves_exact_subsecond_instants():
    from aletheia.analytics.outcome_integrity import raw_account_events
    from aletheia.storage.sqlite import get_db

    db = get_db()
    rows = [
        ("zz-earliest", "2026-08-23 00:00:00.100000"),
        ("zy-cutoff", "2026-08-23T08:00:00.100001+08:00"),
        ("aa-middle", "2026-08-23T00:00:00.100499Z"),
        ("bb-latest", "2026-08-23T00:00:00.100500+00:00"),
        ("cc-latest", "2026-08-23T00:00:00.100500+00:00"),
        ("invalid", "not-a-time"),
        ("missing", None),
    ]
    db.executemany(
        """
        INSERT INTO positions (
            id, symbol, market_type, direction, quantity, entry_price,
            execution_mode, status, closed_at
        ) VALUES (?, 'BTC/USDT', 'spot', 'long', 1, 100,
                  'paper_internal', 'closed', ?)
        """,
        rows,
    )
    db.commit()

    window = raw_account_events(
        closed_after="2026-08-23T00:00:00.100001Z",
        closed_before="2026-08-23T00:00:00.100500+00:00",
    )
    assert [row["id"] for row in window] == ["zy-cutoff", "aa-middle"]

    before = raw_account_events(
        closed_before="2026-08-23T00:00:00.100001+00:00"
    )
    assert [row["id"] for row in before] == ["zz-earliest"]

    raw_ids = {row["id"] for row in raw_account_events()}
    assert {"invalid", "missing"} <= raw_ids

    newest = raw_account_events(limit=3)
    assert [row["id"] for row in newest] == [
        "aa-middle",
        "bb-latest",
        "cc-latest",
    ]


def test_learning_realized_assessment_composes_central_semantics():
    from aletheia.analytics.outcome_semantics import assess_realized_position

    clean = _closed_row(closed_at=datetime.now(timezone.utc).isoformat())
    eligible = assess_realized_position(clean)
    assert eligible.eligible is True
    assert eligible.outcome_key == "position:position-1"

    assert assess_realized_position({**clean, "exit_fill_verified": False}).eligible is False
    assert assess_realized_position({**clean, "close_reason": "reconciled_gone"}).eligible is False


def test_close_reason_consumers_use_central_registry_without_facade():
    from importlib.util import find_spec

    from aletheia.analytics.outcome_integrity import (
        SYNTHETIC_CLOSE_REASONS,
        is_synthetic_close,
    )
    from extensions.analytics.trade_case_builder import _SYNTHETIC_CLOSE_REASONS

    assert find_spec("aletheia.analytics.close_reasons") is None
    assert _SYNTHETIC_CLOSE_REASONS is SYNTHETIC_CLOSE_REASONS
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


@pytest.mark.parametrize("direction,gross", [("long", 25.0), ("short", -25.0)])
def test_known_economics_is_a_read_projection_not_final_authority(direction, gross):
    from aletheia.portfolio.pnl import position_realized_pnl_view
    from aletheia.server.routes.data import PositionView

    row = _closed_row(
        direction=direction, entry_fee=1.0, exit_fee=None, funding_cost=None,
        funding_source="unavailable", realized_pnl=None, outcome_eligible=False,
        outcome_exclusion_reason="incomplete_realized_economics", slippage_cost=99.0,
    )
    original = row.copy()
    view = position_realized_pnl_view(row)
    assert view["known_gross_pnl"] == gross
    assert view["known_net_before_unresolved_costs"] == gross - 1.0
    assert set(view["known_economics_missing_components"]) == {"exit_fee", "funding_cost"}
    assert view["realized_pnl"] is None
    assert view["realized_pnl_complete"] is False
    assert view["outcome_eligible"] is False
    assert row == original
    assert PositionView.model_validate(view).model_dump()["known_gross_pnl"] == gross

    complete = position_realized_pnl_view({
        **row, "exit_fee": 2.0, "funding_cost": -3.0, "funding_source": "broker_ledger",
        "realized_pnl": gross,
    })
    assert complete["known_net_before_unresolved_costs"] == gross
    assert complete["known_economics_missing_components"] == []
    # Reading complete economics still cannot promote Outcome eligibility.
    assert complete["outcome_eligible"] is False


@pytest.mark.parametrize("overrides", [
    {"entry_fill_verified": False}, {"exit_fill_verified": False},
    {"exit_price_source": "unavailable"}, {"quantity": float("nan")},
    {"entry_price": True}, {"direction": "unknown"},
])
def test_known_economics_rejects_unproven_or_invalid_execution(overrides):
    from aletheia.portfolio.pnl import position_realized_pnl_view

    view = position_realized_pnl_view(_closed_row(**overrides))
    assert view["known_gross_pnl"] is None
    assert view["known_net_before_unresolved_costs"] is None
    assert "verified_entry_exit_economics" in view["known_economics_missing_components"]


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
