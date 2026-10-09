"""Adaptive entry sizing: AI risk intent + stop geometry -> target exposure.

The canonical sizing path (#3729). One pure function owns each decision:

- ``resolve_trade_loss_budget()``  how much this trade may lose at its stop
- ``resolve_entry_sizing()``       how much exposure that budget buys

Leverage is deliberately absent from both. It is financing, resolved afterwards
by ``aletheia.trading.leverage_resolution.resolve_execution_financing()``, so a
higher leverage can never enlarge the target exposure -- it can only decide how
much margin that exposure requires.

``max_single_trade_risk_pct`` has exactly one meaning here: the fraction of the
shared capital base (capped by fresh available balance) that may be lost if the
stop is hit. The previous "cost budget" reading multiplied the same percentage
by leverage, which is why the compiler and Risk check #13 disagreed (#3726).
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Policy epoch. Changing how the budget is derived requires a new version so an
# outcome cohort cannot mix two treatments.
POLICY_VERSION = "adaptive-risk-intent-sizing/v2"

# Stable failure reasons (Issue #3729 §12). Never carry exchange exception text.
REASON_CAPITAL_UNAVAILABLE = "SIZING_CAPITAL_UNAVAILABLE"
REASON_GEOMETRY_INVALID = "SIZING_GEOMETRY_INVALID"
REASON_NO_EXECUTABLE_NOTIONAL = "NO_EXECUTABLE_NOTIONAL"

# Binding-constraint tokens, in the order they can bind.
BINDING_STOP_RISK = "stop_risk_implied"
BINDING_TOTAL_EXPOSURE = "remaining_total_exposure"
BINDING_ACCOUNT_LEVERAGE = "remaining_account_leverage"
BINDING_MARKET_LIQUIDITY = "market_liquidity"
BINDING_PER_SYMBOL_RISK = "per_symbol_risk_units"
BINDING_INSTRUMENT_MAX = "instrument_max_notional"
BINDING_MARGIN_BUDGET = "margin_budget"


class SizingUnavailable(Exception):
    """A typed sizing failure. ``reason`` is one of the stable reason codes."""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(reason)


def _finite_positive(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _non_negative_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number


@dataclass(frozen=True)
class LossBudget:
    """What one trade may lose at its stop, and where every input came from."""

    status: Literal["resolved", "unavailable"]
    reason: str | None
    multiplier: float
    multiplier_source: str
    capital_base_usdt: float | None
    available_balance_usdt: float | None
    baseline_usdt: float | None
    effective_usdt: float | None


def resolve_trade_loss_budget(
    *,
    capital_base_usdt: object,
    available_balance_usdt: object,
    risk_pct: object,
    multiplier: float,
    multiplier_source: str,
) -> LossBudget:
    """The single loss-budget authority shared by Compiler and Risk.

    ``min(shared_hard_capital_base, fresh_available_balance) x risk_pct`` is the
    Owner baseline; the intent multiplier can only reduce it. A missing capital
    base, balance, or policy percentage is a typed unavailable: configured
    capital is never a substitute for an unreadable account authority.
    """
    capital_base = _finite_positive(capital_base_usdt)
    available = _finite_positive(available_balance_usdt)
    percentage = _finite_positive(risk_pct)
    scaling = float(multiplier)
    if (
        capital_base is None
        or available is None
        or percentage is None
        or not math.isfinite(scaling)
        or scaling <= 0
    ):
        return LossBudget(
            status="unavailable",
            reason=REASON_CAPITAL_UNAVAILABLE,
            multiplier=scaling,
            multiplier_source=multiplier_source,
            capital_base_usdt=capital_base,
            available_balance_usdt=available,
            baseline_usdt=None,
            effective_usdt=None,
        )
    baseline = min(capital_base, available) * percentage / 100.0
    effective = baseline * scaling
    if not math.isfinite(baseline) or baseline <= 0 or not math.isfinite(effective):
        return LossBudget(
            status="unavailable",
            reason=REASON_CAPITAL_UNAVAILABLE,
            multiplier=scaling,
            multiplier_source=multiplier_source,
            capital_base_usdt=capital_base,
            available_balance_usdt=available,
            baseline_usdt=None,
            effective_usdt=None,
        )
    return LossBudget(
        status="resolved",
        reason=None,
        multiplier=scaling,
        multiplier_source=multiplier_source,
        capital_base_usdt=capital_base,
        available_balance_usdt=available,
        baseline_usdt=baseline,
        effective_usdt=effective,
    )


class SizingResolution(BaseModel):
    """Persisted output of the adaptive sizer (Issue #3729 §6.5)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_version: str = POLICY_VERSION
    status: Literal["resolved", "unavailable"]
    reason: str | None = None
    risk_intent_posture: str
    risk_intent_multiplier: float = Field(gt=0)
    risk_intent_multiplier_source: str
    baseline_loss_budget_usdt: float | None = None
    effective_loss_budget_usdt: float | None = None
    worst_entry_price: float = Field(gt=0)
    stop_price: float = Field(gt=0)
    stop_distance_pct: float = Field(gt=0)
    raw_target_notional_usdt: float | None = None
    target_notional_usdt: float | None = None
    margin_budget_usdt: float | None = None
    binding_constraints: tuple[str, ...] = ()
    capital_base_usdt: float | None = None
    available_balance_usdt: float | None = None
    equity_usdt: float | None = None
    # Market-quality inputs are frozen with the resolution so Risk can recompute
    # from fresh account facts with the same geometry and liquidity envelope, and
    # therefore isolate account drift from market drift.
    executable_notional_cap_usdt: float | None = None
    instrument_min_notional_usdt: float | None = None
    instrument_max_notional_usdt: float | None = None
    account_snapshot_id: str
    policy_snapshot_id: str
    facts_fingerprint: str


@dataclass(frozen=True)
class SizingPolicyFacts:
    """The Owner-policy inputs to one sizing decision."""

    risk_pct: float
    max_position_margin_usdt: float
    max_per_symbol_entries: int
    max_total_exposure_pct: float
    account_leverage_cap: float
    policy_snapshot_id: str


@dataclass(frozen=True)
class AccountSizingState:
    """Fresh account/portfolio facts. No defaults: absent means unavailable."""

    capital_base_usdt: float | None
    available_balance_usdt: float | None
    equity_usdt: float | None
    # Same account pool: open Position + unresolved entry Order + pre-Order
    # Risk reservation notional already committed.
    open_pool_notional_usdt: float
    # Same account pool and same symbol: the part of the above that sits in
    # this instrument's venue tier bracket (Contract C step 4).
    symbol_position_notional_usdt: float
    # Same symbol: stop-loss risk already projected by existing exposure.
    symbol_projected_stop_risk_usdt: float
    # Executable depth the venue can absorb at the entry (market-quality cap).
    executable_notional_cap_usdt: float | None
    instrument_min_notional_usdt: float | None
    instrument_max_notional_usdt: float | None
    snapshot_id: str


def _canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def resolve_entry_sizing(
    *,
    risk_intent_posture: str,
    risk_intent_multiplier: float,
    risk_intent_multiplier_source: str,
    worst_entry_price: float,
    stop_price: float,
    policy: SizingPolicyFacts,
    account: AccountSizingState,
) -> SizingResolution:
    """Turn a loss budget plus stop geometry into a target exposure.

    ``stop_fraction = |worst_entry - stop| / worst_entry`` is the only
    volatility adaptation in V1: a wider stop buys less exposure for the same
    loss budget. Leverage is not an input.
    """
    entry = _finite_positive(worst_entry_price)
    stop = _finite_positive(stop_price)
    if entry is None or stop is None or entry == stop:
        raise SizingUnavailable(REASON_GEOMETRY_INVALID, "worst_entry_or_stop_invalid")
    stop_fraction = abs(entry - stop) / entry
    if not math.isfinite(stop_fraction) or stop_fraction <= 0:
        raise SizingUnavailable(REASON_GEOMETRY_INVALID, "stop_fraction_invalid")

    budget = resolve_trade_loss_budget(
        capital_base_usdt=account.capital_base_usdt,
        available_balance_usdt=account.available_balance_usdt,
        risk_pct=policy.risk_pct,
        multiplier=risk_intent_multiplier,
        multiplier_source=risk_intent_multiplier_source,
    )

    def _unavailable(reason: str) -> SizingResolution:
        return SizingResolution(
            status="unavailable",
            reason=reason,
            risk_intent_posture=risk_intent_posture,
            risk_intent_multiplier=float(risk_intent_multiplier),
            risk_intent_multiplier_source=risk_intent_multiplier_source,
            baseline_loss_budget_usdt=budget.baseline_usdt,
            effective_loss_budget_usdt=budget.effective_usdt,
            worst_entry_price=entry,
            stop_price=stop,
            stop_distance_pct=stop_fraction * 100.0,
            capital_base_usdt=budget.capital_base_usdt,
            available_balance_usdt=budget.available_balance_usdt,
            equity_usdt=_non_negative_or_none(account.equity_usdt),
            executable_notional_cap_usdt=_non_negative_or_none(account.executable_notional_cap_usdt),
            instrument_min_notional_usdt=_non_negative_or_none(account.instrument_min_notional_usdt),
            instrument_max_notional_usdt=_non_negative_or_none(account.instrument_max_notional_usdt),
            account_snapshot_id=account.snapshot_id,
            policy_snapshot_id=policy.policy_snapshot_id,
            facts_fingerprint=_fingerprint(
                entry, stop, stop_fraction,
                budget.effective_usdt, budget.baseline_usdt, reason,
                account, policy,
            ),
        )

    if budget.status != "resolved" or budget.effective_usdt is None:
        return _unavailable(budget.reason or REASON_CAPITAL_UNAVAILABLE)

    raw_target = budget.effective_usdt / stop_fraction
    if not math.isfinite(raw_target) or raw_target <= 0:
        return _unavailable(REASON_GEOMETRY_INVALID)

    binding: list[str] = []
    target = raw_target

    exposure_pct = _non_negative_or_none(policy.max_total_exposure_pct)
    if exposure_pct and budget.capital_base_usdt:
        remaining = exposure_pct / 100.0 * budget.capital_base_usdt - account.open_pool_notional_usdt
        if remaining <= 0:
            return _unavailable(REASON_NO_EXECUTABLE_NOTIONAL)
        if remaining < target:
            target = remaining
            binding.append(BINDING_TOTAL_EXPOSURE)

    equity = _non_negative_or_none(account.equity_usdt)
    leverage_cap = _non_negative_or_none(policy.account_leverage_cap)
    if equity and leverage_cap and equity > 0:
        remaining = leverage_cap * equity - account.open_pool_notional_usdt
        if remaining <= 0:
            return _unavailable(REASON_NO_EXECUTABLE_NOTIONAL)
        if remaining < target:
            target = remaining
            binding.append(BINDING_ACCOUNT_LEVERAGE)

    executable = _non_negative_or_none(account.executable_notional_cap_usdt)
    if executable and executable > 0 and executable < target:
        target = executable
        binding.append(BINDING_MARKET_LIQUIDITY)

    entries = int(policy.max_per_symbol_entries or 0)
    if entries > 0:
        # `max_per_symbol_entries` is a count of full-size *risk units*, so the
        # same-symbol bound is projected stop-loss risk, never risk_pct x
        # leverage. A first entry can therefore never trip it.
        unit_risk = budget.effective_usdt * entries - account.symbol_projected_stop_risk_usdt
        if unit_risk <= 0:
            return _unavailable(REASON_NO_EXECUTABLE_NOTIONAL)
        remaining = unit_risk / stop_fraction
        if remaining < target:
            target = remaining
            binding.append(BINDING_PER_SYMBOL_RISK)

    instrument_max = _non_negative_or_none(account.instrument_max_notional_usdt)
    if instrument_max and instrument_max > 0 and instrument_max < target:
        target = instrument_max
        binding.append(BINDING_INSTRUMENT_MAX)

    instrument_min = _non_negative_or_none(account.instrument_min_notional_usdt)
    if instrument_min and instrument_min > 0 and target < instrument_min:
        return _unavailable(REASON_NO_EXECUTABLE_NOTIONAL)

    margin_candidates = [
        candidate for candidate in (
            budget.available_balance_usdt,
            _finite_positive(policy.max_position_margin_usdt),
        ) if candidate is not None
    ]
    margin_budget = min(margin_candidates) if margin_candidates else None
    if margin_budget is None or margin_budget <= 0:
        return _unavailable(REASON_CAPITAL_UNAVAILABLE)

    if not math.isfinite(target) or target <= 0:
        return _unavailable(REASON_NO_EXECUTABLE_NOTIONAL)

    return SizingResolution(
        status="resolved",
        reason=None,
        risk_intent_posture=risk_intent_posture,
        risk_intent_multiplier=float(risk_intent_multiplier),
        risk_intent_multiplier_source=risk_intent_multiplier_source,
        baseline_loss_budget_usdt=budget.baseline_usdt,
        effective_loss_budget_usdt=budget.effective_usdt,
        worst_entry_price=entry,
        stop_price=stop,
        stop_distance_pct=stop_fraction * 100.0,
        raw_target_notional_usdt=raw_target,
        target_notional_usdt=target,
        margin_budget_usdt=margin_budget,
        binding_constraints=tuple(binding),
        capital_base_usdt=budget.capital_base_usdt,
        available_balance_usdt=budget.available_balance_usdt,
        equity_usdt=equity,
        executable_notional_cap_usdt=_non_negative_or_none(account.executable_notional_cap_usdt),
        instrument_min_notional_usdt=_non_negative_or_none(account.instrument_min_notional_usdt),
        instrument_max_notional_usdt=_non_negative_or_none(account.instrument_max_notional_usdt),
        account_snapshot_id=account.snapshot_id,
        policy_snapshot_id=policy.policy_snapshot_id,
        facts_fingerprint=_fingerprint(
            entry, stop, stop_fraction, budget.effective_usdt, budget.baseline_usdt,
            "resolved", account, policy,
        ),
    )


def _fingerprint(
    entry: float,
    stop: float,
    stop_fraction: float,
    effective: float | None,
    baseline: float | None,
    reason: str,
    account: AccountSizingState,
    policy: SizingPolicyFacts,
) -> str:
    payload = {
        "worst_entry_price": round(entry, 10),
        "stop_price": round(stop, 10),
        "stop_distance_pct": round(stop_fraction * 100.0, 10),
        "baseline_loss_budget_usdt": baseline,
        "effective_loss_budget_usdt": effective,
        "reason": reason,
        "capital_base_usdt": account.capital_base_usdt,
        "available_balance_usdt": account.available_balance_usdt,
        "equity_usdt": account.equity_usdt,
        "open_pool_notional_usdt": round(float(account.open_pool_notional_usdt), 10),
        "symbol_projected_stop_risk_usdt": round(float(account.symbol_projected_stop_risk_usdt), 10),
        "executable_notional_cap_usdt": account.executable_notional_cap_usdt,
        "instrument_min_notional_usdt": account.instrument_min_notional_usdt,
        "instrument_max_notional_usdt": account.instrument_max_notional_usdt,
        "risk_pct": policy.risk_pct,
        "max_position_margin_usdt": policy.max_position_margin_usdt,
        "max_per_symbol_entries": policy.max_per_symbol_entries,
        "max_total_exposure_pct": policy.max_total_exposure_pct,
        "account_leverage_cap": policy.account_leverage_cap,
        "policy_snapshot_id": policy.policy_snapshot_id,
        "account_snapshot_id": account.snapshot_id,
    }
    return hashlib.sha256(_canonical(payload).encode()).hexdigest()
