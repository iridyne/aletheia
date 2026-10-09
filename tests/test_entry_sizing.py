"""Adaptive entry sizing contract (#3729): loss budget, stop geometry, target exposure."""
from __future__ import annotations

import pytest

from aletheia.trading.entry_sizing import (
    BINDING_ACCOUNT_LEVERAGE,
    BINDING_MARKET_LIQUIDITY,
    BINDING_PER_SYMBOL_RISK,
    BINDING_TOTAL_EXPOSURE,
    POLICY_VERSION,
    REASON_CAPITAL_UNAVAILABLE,
    REASON_GEOMETRY_INVALID,
    REASON_NO_EXECUTABLE_NOTIONAL,
    AccountSizingState,
    SizingPolicyFacts,
    SizingUnavailable,
    resolve_entry_sizing,
    resolve_trade_loss_budget,
)


def _policy(**changes):
    defaults = dict(
        risk_pct=20.0,
        max_position_margin_usdt=50.0,
        max_per_symbol_entries=3,
        max_total_exposure_pct=0.0,
        account_leverage_cap=10.0,
        policy_snapshot_id="demo@1:hash",
    )
    return SizingPolicyFacts(**{**defaults, **changes})


def _account(**changes):
    defaults = dict(
        capital_base_usdt=5000.0,
        available_balance_usdt=5000.0,
        equity_usdt=5000.0,
        open_pool_notional_usdt=0.0,
        symbol_position_notional_usdt=0.0,
        symbol_projected_stop_risk_usdt=0.0,
        executable_notional_cap_usdt=None,
        instrument_min_notional_usdt=1.0,
        instrument_max_notional_usdt=None,
        snapshot_id="acct-1",
    )
    return AccountSizingState(**{**defaults, **changes})


def _target(resolution) -> float:
    assert resolution.target_notional_usdt is not None
    return resolution.target_notional_usdt


def _effective_budget(resolution) -> float:
    assert resolution.effective_loss_budget_usdt is not None
    return resolution.effective_loss_budget_usdt


def _margin_budget(resolution) -> float:
    assert resolution.margin_budget_usdt is not None
    return resolution.margin_budget_usdt


def _size(*, stop_price=95.0, worst_entry_price=100.0, multiplier=1.0, posture="BASELINE",
          policy=None, account=None):
    return resolve_entry_sizing(
        risk_intent_posture=posture,
        risk_intent_multiplier=multiplier,
        risk_intent_multiplier_source="policy.loss.reduced_risk_multiplier" if multiplier != 1.0 else "baseline",
        worst_entry_price=worst_entry_price,
        stop_price=stop_price,
        policy=policy or _policy(),
        account=account or _account(),
    )


def test_baseline_budget_is_share_of_the_smaller_capital_authority():
    budget = resolve_trade_loss_budget(
        capital_base_usdt=5000.0, available_balance_usdt=1000.0, risk_pct=20.0,
        multiplier=1.0, multiplier_source="baseline",
    )
    assert budget.status == "resolved"
    assert budget.baseline_usdt == pytest.approx(200.0)  # min(5000, 1000) x 20%
    assert budget.effective_usdt == pytest.approx(200.0)


def test_loss_budget_is_unavailable_without_an_account_authority():
    for missing in ("capital_base_usdt", "available_balance_usdt"):
        budget = resolve_trade_loss_budget(
            capital_base_usdt=None if missing == "capital_base_usdt" else 5000.0,
            available_balance_usdt=None if missing == "available_balance_usdt" else 5000.0,
            risk_pct=20.0, multiplier=1.0, multiplier_source="baseline",
        )
        assert budget.status == "unavailable"
        assert budget.reason == REASON_CAPITAL_UNAVAILABLE
        assert budget.effective_usdt is None


def test_reduced_intent_only_shrinks_the_target_exposure():
    baseline = _size(multiplier=1.0)
    reduced = _size(multiplier=0.5, posture="REDUCED")

    assert _target(baseline) == pytest.approx(20000.0)  # 1000 / 5%
    assert _target(reduced) == pytest.approx(10000.0)
    assert _target(reduced) < _target(baseline)
    assert reduced.raw_target_notional_usdt is not None and baseline.raw_target_notional_usdt is not None
    assert reduced.raw_target_notional_usdt < baseline.raw_target_notional_usdt
    assert reduced.stop_distance_pct == baseline.stop_distance_pct


def test_wider_stop_buys_less_exposure_for_the_same_budget():
    tight = _size(stop_price=95.0)
    wide = _size(stop_price=90.0)

    assert _effective_budget(tight) == _effective_budget(wide)
    assert _target(wide) == pytest.approx(_target(tight) / 2)


def test_loss_at_stop_of_the_target_never_exceeds_the_budget():
    """The one guarantee check #13 relies on: risk == target x stop_fraction."""
    resolution = _size(stop_price=95.0)
    risk_at_stop = _target(resolution) * resolution.stop_distance_pct / 100.0
    assert risk_at_stop <= _effective_budget(resolution) + 1e-9


def test_margin_budget_is_separate_from_the_loss_budget():
    resolution = _size()
    assert _margin_budget(resolution) == pytest.approx(50.0)  # policy margin cap
    assert _effective_budget(resolution) == pytest.approx(1000.0)
    assert _margin_budget(resolution) != _effective_budget(resolution)


def test_binding_constraints_are_recorded():
    liquidity = _size(account=_account(executable_notional_cap_usdt=300.0))
    assert _target(liquidity) == pytest.approx(300.0)
    assert BINDING_MARKET_LIQUIDITY in liquidity.binding_constraints

    exposure = _size(policy=_policy(max_total_exposure_pct=1.0))
    assert _target(exposure) == pytest.approx(50.0)  # 1% of 5000
    assert BINDING_TOTAL_EXPOSURE in exposure.binding_constraints

    accounted = _size(account=_account(open_pool_notional_usdt=400.0), policy=_policy(account_leverage_cap=0.1))
    assert _target(accounted) == pytest.approx(100.0)  # 0.1 x 5000 - 400
    assert BINDING_ACCOUNT_LEVERAGE in accounted.binding_constraints


def test_per_symbol_allowance_is_measured_in_risk_units():
    """A first entry can never trip the same-symbol bound; prior risk can."""
    first = _size(account=_account(symbol_projected_stop_risk_usdt=0.0))
    assert BINDING_PER_SYMBOL_RISK not in first.binding_constraints
    assert _target(first) == pytest.approx(20000.0)

    spent = _size(account=_account(symbol_projected_stop_risk_usdt=2900.0))
    assert _target(spent) == pytest.approx(2000.0)  # (3 x 1000 - 2900) / 5%
    assert BINDING_PER_SYMBOL_RISK in spent.binding_constraints

    exhausted = _size(account=_account(symbol_projected_stop_risk_usdt=3000.0))
    assert exhausted.status == "unavailable"
    assert exhausted.reason == REASON_NO_EXECUTABLE_NOTIONAL


def test_geometry_and_capital_failures_are_typed():
    with pytest.raises(SizingUnavailable) as same:
        _size(stop_price=100.0)
    assert same.value.reason == REASON_GEOMETRY_INVALID

    unavailable = _size(account=_account(capital_base_usdt=None, available_balance_usdt=None))
    assert unavailable.status == "unavailable"
    assert unavailable.reason == REASON_CAPITAL_UNAVAILABLE


def test_resolution_is_reproducible_and_persists_its_provenance():
    first = _size()
    second = _size()

    assert first.policy_version == POLICY_VERSION
    assert first.facts_fingerprint == second.facts_fingerprint
    assert first.account_snapshot_id == "acct-1"
    assert first.policy_snapshot_id == "demo@1:hash"
    assert first.status == "resolved" and first.reason is None

    changed = _size(account=_account(available_balance_usdt=1000.0))
    assert changed.facts_fingerprint != first.facts_fingerprint
    assert _target(changed) < _target(first)
