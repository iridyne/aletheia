"""Versioned policy attribution for the deterministic post-debate leverage resolver.

The resolver (v1) replaced free-form AI leverage selection with a deterministic
formula: stop-distance safety, conviction scaling, ATR volatility cap, and account
headroom constraint. AI input is kept advisory-only; the final value is auditable
and reproducible from stored resolver_inputs.
"""
from __future__ import annotations

from aletheia.learning.policy_epochs import build_policy_epoch, persist_policy_epoch


_EPOCH_KIND = "system_operator_leverage_policy"

_EFFECTIVE_VALUES = {
    "version": "v1",
    # AI leverage request is advisory only; deterministic resolver owns the final value.
    "ai_leverage_advisory_only": True,
    # Resolver inputs used to compute final leverage.
    "resolver_stop_distance_safety": True,
    "resolver_conviction_scaling": True,
    "resolver_atr_volatility_cap": True,
    "resolver_account_headroom_constraint": True,
    # Fallback to 1x when any required input (entry_price, stop_price) is missing.
    "missing_input_fallback_1x": True,
    # Spot positions are structurally unleveraged.
    "spot_always_1x": True,
    # Constants (informational — changing these rotates the epoch hash).
    "liq_safety_margin": 0.80,
    "conviction_max_lev": 5,
}

_AFFECTED_PATHS = (
    "system_operator_leverage_resolution",
    "operator_futures_entry_sizing",
    "operator_leverage_audit_trail",
)


def ensure_leverage_policy_epoch() -> str:
    """Return the durable v1 leverage policy epoch, rotating only if policy changes."""
    epoch = build_policy_epoch(
        _EPOCH_KIND,
        _EFFECTIVE_VALUES,
        affected_path_ids=_AFFECTED_PATHS,
        safety_required=False,
    )
    return persist_policy_epoch(epoch)


def leverage_policy_context() -> dict:
    """Return the non-secret effective values for the current leverage policy."""
    return dict(_EFFECTIVE_VALUES)
