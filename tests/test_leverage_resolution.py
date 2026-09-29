"""Deterministic leverage resolution contract (#3729 §7): target -> minimal financing."""
from __future__ import annotations

import math

import pytest

from aletheia.trading.entry_sizing import SizingResolution
from aletheia.trading.leverage_resolution import (
    REASON_CROSS_MARGIN_SAFETY_UNAVAILABLE,
    REASON_VENUE_MARGIN_TIERS_UNAVAILABLE,
    resolve_execution_financing,
)
from tests.helpers.venue_margin_fixture import seed_venue_margin_envelope

BUFFER_PCT = 5.0


def _sizing(*, target: float | None, margin_budget: float | None, fp: str = "sizing-fp") -> SizingResolution:
    return SizingResolution(
        status="resolved",
        risk_intent_posture="BASELINE",
        risk_intent_multiplier=1.0,
        risk_intent_multiplier_source="owner_baseline",
        worst_entry_price=100.0,
        stop_price=95.0,
        stop_distance_pct=5.0,
        target_notional_usdt=target,
        margin_budget_usdt=margin_budget,
        account_snapshot_id="acct-1",
        policy_snapshot_id="policy-1",
        facts_fingerprint=fp,
    )


def _envelope(tiers, *, margin_mode: str = "isolated", fp: str = "env-fp"):
    return seed_venue_margin_envelope(
        symbol="BTC/USDT:USDT", margin_mode=margin_mode, tiers=tiers, store=False,
    )


# ── (a) reachable target never pulls leverage up to a merely-safe cap ───────


def test_low_leverage_wins_when_it_already_finances_the_full_target():
    """Target is fully financed at 5x; ~20x is also buffer-safe but must lose."""
    envelope = _envelope(((0.0, None, 25.0, 0.0),))
    sizing = _sizing(target=10_000.0, margin_budget=2_000.0)  # 10000 / 2000 = 5x
    resolution = resolve_execution_financing(
        sizing=sizing, envelope=envelope, policy_leverage_cap=25,
        liquidation_buffer_pct=BUFFER_PCT,
    )
    assert resolution.status == "resolved"
    assert resolution.execution_leverage == 5
    assert resolution.execution_notional_usdt == pytest.approx(10_000.0)
    assert resolution.binding_constraint is None
    expected_safe_cap = 1.0 / (BUFFER_PCT / 100.0)  # mmr=0 -> 20x
    assert resolution.safe_leverage_cap == pytest.approx(expected_safe_cap)
    assert resolution.safe_leverage_cap > resolution.execution_leverage


# ── (b) target above the provable-safe cap converges the notional down ──────


def test_execution_notional_shrinks_when_target_needs_more_than_the_safe_cap():
    """20x would hit the target; only 18x clears the liquidation buffer."""
    envelope = _envelope(((0.0, None, 25.0, 0.004),))
    sizing = _sizing(target=20_000.0, margin_budget=1_000.0)  # requires 20x for full target
    resolution = resolve_execution_financing(
        sizing=sizing, envelope=envelope, policy_leverage_cap=25,
        liquidation_buffer_pct=BUFFER_PCT,
    )
    assert resolution.status == "resolved"
    assert resolution.required_leverage_for_target == 20
    assert resolution.execution_leverage == 18
    assert resolution.execution_notional_usdt is not None
    assert resolution.target_notional_usdt is not None
    assert resolution.execution_notional_usdt < resolution.target_notional_usdt
    assert resolution.execution_notional_usdt == pytest.approx(1_000.0 * 18)
    assert resolution.binding_constraint == "margin_budget"


# ── (c) 1x already covers the target ─────────────────────────────────────────


def test_execution_leverage_is_one_when_margin_budget_already_covers_target():
    envelope = _envelope(((0.0, None, 25.0, 0.0),))
    sizing = _sizing(target=5_000.0, margin_budget=6_000.0)
    resolution = resolve_execution_financing(
        sizing=sizing, envelope=envelope, policy_leverage_cap=25,
        liquidation_buffer_pct=BUFFER_PCT,
    )
    assert resolution.status == "resolved"
    assert resolution.execution_leverage == 1
    assert resolution.execution_notional_usdt == pytest.approx(5_000.0)


# ── (d) cross margin is always a typed refusal, never an isolated fallback ──


def test_cross_margin_mode_is_a_typed_refusal_not_an_isolated_estimate():
    envelope = _envelope(((0.0, None, 25.0, 0.004),), margin_mode="cross")
    sizing = _sizing(target=5_000.0, margin_budget=6_000.0)
    resolution = resolve_execution_financing(
        sizing=sizing, envelope=envelope, policy_leverage_cap=25,
        liquidation_buffer_pct=BUFFER_PCT,
    )
    assert resolution.status == "unavailable"
    assert resolution.reason == REASON_CROSS_MARGIN_SAFETY_UNAVAILABLE
    assert resolution.execution_leverage is None
    assert resolution.margin_mode == "cross"


# ── (e) missing venue facts is a typed failure, never a hardcoded MMR ───────


def test_missing_venue_envelope_is_typed_unavailable_with_no_hardcoded_mmr():
    sizing = _sizing(target=5_000.0, margin_budget=6_000.0)
    resolution = resolve_execution_financing(
        sizing=sizing, envelope=None, policy_leverage_cap=25,
        liquidation_buffer_pct=BUFFER_PCT,
    )
    assert resolution.status == "unavailable"
    assert resolution.reason == REASON_VENUE_MARGIN_TIERS_UNAVAILABLE
    assert resolution.execution_leverage is None
    assert resolution.maintenance_margin_rate is None


# ── (f) spot is structurally 1x, no leverage/tier math at all ───────────────


def test_spot_is_structurally_one_x_and_never_reads_the_envelope():
    sizing = _sizing(target=5_000.0, margin_budget=6_000.0)
    resolution = resolve_execution_financing(
        sizing=sizing, envelope=None, policy_leverage_cap=1,
        liquidation_buffer_pct=BUFFER_PCT, spot=True,
    )
    assert resolution.status == "not_applicable"
    assert resolution.execution_leverage == 1
    assert resolution.execution_notional_usdt == pytest.approx(5_000.0)


# ── (g) a candidate that crosses into a higher tier uses that tier's facts ──


def test_candidate_crossing_into_a_higher_tier_uses_that_tiers_leverage_and_mmr():
    """Existing exposure pushes the projected position past the tier-1 ceiling."""
    envelope = _envelope((
        (0.0, 10_000.0, 20.0, 0.01),
        (10_000.0, None, 10.0, 0.02),
    ))
    sizing = _sizing(target=5_000.0, margin_budget=5_000.0)
    resolution = resolve_execution_financing(
        sizing=sizing, envelope=envelope, policy_leverage_cap=20,
        liquidation_buffer_pct=BUFFER_PCT,
        existing_position_notional_usdt=8_000.0,  # 8000 + 5000 = 13000 -> tier 2
    )
    assert resolution.status == "resolved"
    assert resolution.tier == 2
    assert resolution.maintenance_margin_rate == pytest.approx(0.02)
    assert resolution.execution_leverage is not None and resolution.execution_leverage <= 10  # tier 2's cap, not tier 1's 20x
    assert resolution.execution_notional_usdt == pytest.approx(5_000.0)
    assert resolution.projected_position_notional_usdt == pytest.approx(13_000.0)


# ── facts_fingerprint is a real drift-detection signal ──────────────────────


def test_facts_fingerprint_is_reproducible_for_identical_inputs():
    envelope = _envelope(((0.0, None, 25.0, 0.0),))
    sizing = _sizing(target=10_000.0, margin_budget=2_000.0)
    first = resolve_execution_financing(
        sizing=sizing, envelope=envelope, policy_leverage_cap=25,
        liquidation_buffer_pct=BUFFER_PCT,
    )
    second = resolve_execution_financing(
        sizing=sizing, envelope=envelope, policy_leverage_cap=25,
        liquidation_buffer_pct=BUFFER_PCT,
    )
    assert first.facts_fingerprint == second.facts_fingerprint


def test_facts_fingerprint_changes_when_the_policy_cap_changes():
    envelope = _envelope(((0.0, None, 25.0, 0.0),))
    sizing = _sizing(target=10_000.0, margin_budget=2_000.0)
    lower_cap = resolve_execution_financing(
        sizing=sizing, envelope=envelope, policy_leverage_cap=10,
        liquidation_buffer_pct=BUFFER_PCT,
    )
    higher_cap = resolve_execution_financing(
        sizing=sizing, envelope=envelope, policy_leverage_cap=25,
        liquidation_buffer_pct=BUFFER_PCT,
    )
    assert lower_cap.facts_fingerprint != higher_cap.facts_fingerprint
