"""Spec: Trading personas + dynamic sizing (dynamic_sizing_operator_persona_design.md).

P1: persona-as-bounds schema + the resolver seam. The load-bearing invariant is
BACKWARD COMPATIBILITY — with persona sizing disabled (the default), sizing must
behave EXACTLY as before (legacy flat config). Persona `conservative` init values
equal the flat-config defaults, so even enabling it with the default persona is a
zero behavior change. Claim prefix: PS.
"""
from __future__ import annotations

import sqlite3

from tests.specs.spec_meta import SpecClaim, spec_claim


def _setup_db(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    return conn


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


@spec_claim(
    SpecClaim("PS-S04", "persona_sizing", "§3 Operator sizing context (risk_appetite)", "L2b",
              "build_sizing_context blends regime/F&G/volatility into risk_appetite∈[0,1] "
              "(0=defensive→range min, 1=aggressive→max): a high-confidence bear regime "
              "lowers appetite for a long and raises it for a short; missing data → 0.5."),
)
def test_build_sizing_context_risk_appetite(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.storage.models import MarketRegimeRecord
    from aletheia.storage.repositories import insert_market_regime
    from aletheia.trading.persona import build_sizing_context

    # No data yet → neutral.
    assert build_sizing_context()["risk_appetite"] == 0.5

    # High-confidence BEAR regime.
    insert_market_regime(MarketRegimeRecord(state="bear", confidence=0.9,
                                            long_bias=0.2, short_bias=0.8))
    long_ctx = build_sizing_context(direction="long")
    short_ctx = build_sizing_context(direction="short")
    assert long_ctx["risk_appetite"] < 0.5, (
        f"bear regime must be defensive for a long: {long_ctx['risk_appetite']}"
    )
    assert short_ctx["risk_appetite"] > 0.5, (
        f"bear regime must favor a short: {short_ctx['risk_appetite']}"
    )
    assert 0.0 <= long_ctx["risk_appetite"] <= 1.0


@spec_claim(
    SpecClaim("PS-S05", "persona_sizing", "§1 decide_param_value interpolation", "L2b",
              "decide_param_value maps risk_appetite linearly into [min,max]: "
              "0→min, 1→max, 0.5→midpoint; clamps out-of-range appetite."),
)
def test_decide_param_value_interpolates():
    from aletheia.config.schema import PersonaParam
    from aletheia.trading.persona import decide_param_value
    r = PersonaParam(min=20.0, init=50.0, max=200.0)
    assert decide_param_value(r, 0.0) == 20.0
    assert decide_param_value(r, 1.0) == 200.0
    assert decide_param_value(r, 0.5) == 110.0
    assert decide_param_value(r, 2.0) == 200.0   # clamp high
    assert decide_param_value(r, -1.0) == 20.0   # clamp low


@spec_claim(
    SpecClaim("PS-S06", "persona_sizing", "§6 operator_decidable params are context-decided", "L2b",
              "effective_sizing_params context-decides a param marked operator_decidable "
              "(value within [min,max], not the init), while a non-decidable param stays "
              "pinned at init. Default personas mark none → dormant."),
)
def test_operator_decidable_param_is_context_decided(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.config.schema import AletheiaConfig
    from aletheia.trading.persona import effective_sizing_params
    from aletheia.storage.models import MarketRegimeRecord
    from aletheia.storage.repositories import insert_market_regime

    insert_market_regime(MarketRegimeRecord(state="bear", confidence=0.9))

    cfg = AletheiaConfig()
    cfg.trading.persona_sizing_enabled = True
    cfg.trading.active_persona = "roll"
    roll = cfg.trading.personas["roll"]
    # Unlock ONE param (notional) — the rest stay pinned at init.
    roll.operator_decidable = {"notional_usdt": True}

    eff = effective_sizing_params(cfg, symbol="BTC/USDT", direction="long")
    nr = roll.params["notional_usdt"]
    # decidable → within range, driven by context (bear+long = defensive → below init)
    assert nr.min <= eff["notional_usdt"] <= nr.max
    assert eff["notional_usdt"] != nr.init or eff["_risk_appetite"] == 0.5, (
        "operator_decidable notional should be context-decided, not pinned at init"
    )
    # kelly is NOT decidable → stays at init
    assert eff["kelly_fraction"] == roll.params["kelly_fraction"].init
    assert eff["_risk_appetite"] is not None
