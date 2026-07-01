"""Spec: Trading personas + dynamic sizing (dynamic_sizing_operator_persona_design.md).

P1: persona-as-bounds schema + the resolver seam. The load-bearing invariant is
BACKWARD COMPATIBILITY — with persona sizing disabled (the default), sizing must
behave EXACTLY as before (legacy flat config). Persona `conservative` init values
equal the flat-config defaults, so even enabling it with the default persona is a
zero behavior change. Claim prefix: PS.
"""
from __future__ import annotations

from tests.specs.spec_meta import SpecClaim, spec_claim


@spec_claim(
    SpecClaim("PS-S01", "persona_sizing", "§5 persona bounds schema", "L2a",
              "TradingConfig exposes persona_sizing_enabled (default False), "
              "active_persona (default 'conservative'), and personas with "
              "conservative/balanced/roll; conservative init values equal the flat "
              "config defaults (enabling it = zero behavior change)."),
)
def test_persona_schema_and_conservative_equals_flat_defaults():
    from aletheia.config.schema import AletheiaConfig
    t = AletheiaConfig().trading
    assert t.persona_sizing_enabled is False, "persona sizing must default OFF"
    assert t.active_persona == "conservative"
    assert {"conservative", "balanced", "roll"} <= set(t.personas)
    cons = t.personas["conservative"].params
    assert cons["notional_usdt"].init == t.default_notional_usdt, (
        "conservative notional init must equal default_notional_usdt (zero-change)"
    )
    assert cons["kelly_fraction"].init == t.vol_kelly_cap_fraction
    # roll is the high-leverage 滚仓 tier
    assert t.personas["roll"].params["leverage_max"].max >= 10


@spec_claim(
    SpecClaim("PS-S02", "persona_sizing", "§1 resolver backward-compat + persona init", "L2b",
              "effective_sizing_params returns LEGACY flat-config values when persona "
              "sizing is disabled (zero change), and the active persona's init when "
              "enabled. Bounds are decision-inputs only; risk caps clamp downstream."),
)
def test_effective_sizing_params_flag_gated():
    from aletheia.config.schema import AletheiaConfig
    from aletheia.trading.persona import effective_sizing_params

    # Disabled (default) → legacy flat config.
    cfg = AletheiaConfig()
    cfg.trading.default_notional_usdt = 25.0
    cfg.trading.vol_kelly_cap_fraction = 0.25
    eff = effective_sizing_params(cfg)
    assert eff["_persona"] == "legacy"
    assert eff["notional_usdt"] == 25.0 and eff["kelly_fraction"] == 0.25

    # Enabled + roll → persona init (bigger base, per the 滚仓 tier).
    cfg.trading.persona_sizing_enabled = True
    cfg.trading.active_persona = "roll"
    eff = effective_sizing_params(cfg)
    assert eff["_persona"] == "roll"
    assert eff["notional_usdt"] == cfg.trading.personas["roll"].params["notional_usdt"].init

    # Enabled + conservative → still equals the flat defaults (zero change).
    cfg.trading.active_persona = "conservative"
    eff = effective_sizing_params(cfg)
    assert eff["notional_usdt"] == 25.0 and eff["kelly_fraction"] == 0.25


@spec_claim(
    SpecClaim("PS-S03", "persona_sizing", "§6 sizing reads persona base", "L2b",
              "vol_target_size sources its base notional from the persona resolver: "
              "disabled → legacy default_notional_usdt; enabled+roll → the roll init. "
              "Switching persona changes the resulting size (the seam is live)."),
)
def test_vol_target_size_uses_persona_base(monkeypatch):
    from aletheia.config.schema import AletheiaConfig
    import aletheia.config.loader as loader
    from aletheia.trading import sizing

    cfg = AletheiaConfig()
    cfg.trading.vol_sizing_enabled = False  # → returns compute_position_size(base) directly
    monkeypatch.setattr(loader, "load_config", lambda: cfg)

    # Disabled → base = legacy default_notional_usdt (25).
    legacy_size = sizing.vol_target_size("BTC/USDT", conviction=70, paper_trade_count=50)

    # Enabled + roll (init 50) → larger base → larger size than legacy 25 base.
    cfg.trading.persona_sizing_enabled = True
    cfg.trading.active_persona = "roll"
    roll_size = sizing.vol_target_size("BTC/USDT", conviction=70, paper_trade_count=50)
    assert roll_size > legacy_size, (
        f"roll persona (base 50) must size larger than legacy (base 25): "
        f"roll={roll_size} legacy={legacy_size}"
    )
