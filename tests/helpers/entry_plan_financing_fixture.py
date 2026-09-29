"""Financing for hand-built Entry Plans in tests (#3729).

The execution boundary only builds an order intent from a Plan that carries a
resolved financing (Issue #3729 §10), so a fixture that constructs an
`EntryPlan` literal must resolve one. This helper runs the *same* canonical
sizer and resolver the compiler runs — it is not a second copy of the
arithmetic — so a Plan it returns is exactly the Plan Risk re-derives, and the
`SIZING_RESOLUTION_DRIFT` convergence check cannot fire on fixture noise.
"""
from __future__ import annotations

from tests.helpers.venue_margin_fixture import seed_venue_margin_envelope


def apply_entry_financing(
    plan,
    *,
    risk_intent_posture: str = "BASELINE",
    risk_intent_multiplier: float = 1.0,
    risk_intent_multiplier_source: str = "baseline",
    executable_notional_cap_usdt: float | None = None,
    instrument_min_notional_usdt: float | None = None,
    cfg=None,
    seed_envelope: bool = True,
):
    """Return `plan` with the financing fields a real compilation would freeze."""
    from aletheia.config.exchanges import is_live_execution_mode
    from aletheia.config.loader import load_config
    from aletheia.execution.margin_mode_policy import submission_margin_mode
    from aletheia.risk.engine import account_sizing_state, sizing_policy_facts
    from aletheia.risk.policy import resolve_risk_policy
    from aletheia.trading.entry_sizing import resolve_entry_sizing
    from aletheia.trading.instruments import get_max_leverage
    from aletheia.trading.leverage_resolution import resolve_execution_financing
    from aletheia.trading.venue_margin import load_venue_margin_envelope

    cfg = cfg or load_config()
    mode = plan.execution_account_mode
    is_spot = plan.market_type == "spot"
    margin_mode = submission_margin_mode(mode)
    if not is_spot and margin_mode is None:
        raise AssertionError(f"no qualified submission margin mode for {mode!r}")
    if seed_envelope and not is_spot:
        seed_venue_margin_envelope(symbol=plan.symbol, market_type=plan.market_type)
    envelope = None if is_spot else load_venue_margin_envelope(
        venue=plan.venue,
        symbol=plan.symbol,
        market_type=plan.market_type,
        margin_mode=margin_mode or "isolated",
        cfg=cfg,
    )
    account = account_sizing_state(
        mode,
        plan.symbol,
        cfg=cfg,
        executable_notional_cap_usdt=executable_notional_cap_usdt,
        instrument_min_notional_usdt=instrument_min_notional_usdt,
    )
    worst_entry = float(plan.worst_price if plan.worst_price is not None else max(plan.entry_zone))
    sizing = resolve_entry_sizing(
        risk_intent_posture=risk_intent_posture,
        risk_intent_multiplier=risk_intent_multiplier,
        risk_intent_multiplier_source=risk_intent_multiplier_source,
        worst_entry_price=worst_entry,
        stop_price=float(plan.stop_price),
        policy=sizing_policy_facts(mode, cfg=cfg),
        account=account,
    )
    policy_leverage_cap = 1 if is_spot else int(
        get_max_leverage(plan.symbol, is_live_execution_mode(mode), cfg)
    )
    financing = resolve_execution_financing(
        sizing=sizing,
        envelope=envelope,
        policy_leverage_cap=policy_leverage_cap,
        liquidation_buffer_pct=float(
            resolve_risk_policy(cfg, mode).policy.margin.min_entry_liquidation_buffer_pct
        ),
        existing_position_notional_usdt=account.open_pool_notional_usdt,
        spot=is_spot,
    )
    assert sizing.status == "resolved", sizing.reason
    assert financing.status in {"resolved", "not_applicable"}, financing.reason
    return plan.model_copy(update={
        "risk_intent_posture": risk_intent_posture,
        "risk_intent_multiplier": risk_intent_multiplier,
        "sizing_resolution": sizing,
        "leverage_resolution": financing,
        "policy_leverage_cap": policy_leverage_cap,
        "execution_leverage": int(financing.execution_leverage or 1),
        "target_notional_usdt": sizing.target_notional_usdt,
        "execution_notional_usdt": financing.execution_notional_usdt,
        "margin_mode": None if is_spot else financing.margin_mode,
        # Legacy mirrors stay coherent with the financed values.
        "max_leverage": int(financing.execution_leverage or 1),
        "max_notional": float(financing.execution_notional_usdt or 0.0),
    })
