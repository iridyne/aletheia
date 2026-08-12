"""Spec: Trading personas + dynamic sizing (dynamic_sizing_operator_persona_design.md).

P1: persona-as-bounds schema + the resolver seam. The load-bearing invariant is
BACKWARD COMPATIBILITY — with persona sizing disabled (the default), sizing must
behave EXACTLY as before (legacy flat config). Persona `conservative` init values
equal the flat-config defaults, so even enabling it with the default persona is a
zero behavior change. Claim prefix: PS.
"""
from __future__ import annotations

import asyncio
import sqlite3
from itertools import count
from pathlib import Path

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


_PAPER_TRADING_SETTINGS = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "src"
    / "features"
    / "settings"
    / "execution"
    / "PaperTradingSettings.tsx"
)


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


def test_vol_target_size_emits_complete_non_kelly_policy_trace(monkeypatch):
    from aletheia.config.schema import AletheiaConfig
    import aletheia.config.loader as loader
    from aletheia.trading.sizing import vol_target_size

    cfg = AletheiaConfig()
    monkeypatch.setattr(loader, "load_config", lambda: cfg)
    trace: dict = {}

    result = vol_target_size(
        "BTC/USDT",
        conviction=80,
        atr_pct_override=2.0,
        paper_trade_count=0,
        sizing_trace=trace,
    )

    assert trace["policy_version"] == "conviction-weighted-notional/v1"
    assert trace["value_source"] == "deterministic_sizing_policy"
    assert trace["legacy_parameter_name"] == "kelly_fraction"
    assert trace["conviction"] == 80
    assert trace["baseline_calculation"]["atr_pct"] == 2.0
    assert trace["baseline_calculation"]["effective_scaling_fraction"] == 0.1
    assert trace["baseline_calculation"]["final_notional_usdt"] == result
    assert trace["atr_snapshot"] is None


def test_vol_target_size_freezes_selected_atr_instrument_lineage(monkeypatch):
    from aletheia.config.schema import AletheiaConfig
    import aletheia.config.loader as loader
    from aletheia.trading import sizing

    cfg = AletheiaConfig()
    cfg.trading.vol_sizing_enabled = True
    snapshot = {
        "feature_snapshot_id": "feature-1",
        "feature_ts": "2026-08-12T20:00:00+00:00",
        "asset_id": "asset-1",
        "venue": "bitget",
        "symbol": "BTC/USDT",
        "market_type": "perp",
        "atr_pct": 2.0,
    }
    monkeypatch.setattr(loader, "load_config", lambda: cfg)
    monkeypatch.setattr(sizing, "_lookup_atr_snapshot", lambda _symbol: snapshot)
    trace: dict = {}

    sizing.vol_target_size("BTC/USDT", sizing_trace=trace)

    assert trace["atr_snapshot"] == snapshot
    assert trace["baseline_calculation"]["atr_pct"] == 2.0


def test_trading_settings_reject_invalid_legacy_sizing_scalars(tmp_path, monkeypatch):
    import asyncio

    from fastapi import HTTPException
    import pytest

    from aletheia.config.schema import AletheiaConfig
    from aletheia.server.routes import settings as settings_routes

    cfg = AletheiaConfig()
    monkeypatch.setattr(settings_routes, "load_config", lambda: cfg)
    monkeypatch.setattr(settings_routes, "save_config", lambda _cfg: None)

    for field, value in (
        ("default_notional_usdt", 0),
        ("vol_target_risk_usdt", -1),
        ("vol_target_pct", 0),
        ("vol_kelly_cap_fraction", 0.75),
    ):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(settings_routes.update_trading_config(
                settings_routes.TradingConfigUpdate(**{field: value})
            ))
        assert exc.value.status_code == 400


@spec_claim(
    SpecClaim(
        "PS-S14", "persona_sizing", "§6 persona sizing reaches Proposal quantity", "L2b",
        "The active persona's notional and Kelly values cross the System Operator "
        "sizing seam into the immutable TradeProposal quantity; non-sizing persona "
        "ranges are not claimed by this capability.",
    ),
)
def test_persona_notional_reaches_proposal_quantity(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.agent_shell import trading_decision as td
    from aletheia.config.schema import AletheiaConfig
    import aletheia.config.loader as loader

    cfg = AletheiaConfig()
    cfg.trading.vol_sizing_enabled = False
    cfg.trading.persona_sizing_enabled = True
    cfg.trading.active_persona = "roll"
    monkeypatch.setattr(loader, "load_config", lambda: cfg)
    monkeypatch.setattr(td, "_proposal_direction_overheated", lambda *_args: None)
    monkeypatch.setattr(
        td,
        "_latest_exact_entry_snapshot",
        lambda *_args: {
            "symbol": "BTC/USDT",
            "market_type": "spot",
            "exchange": cfg.market_data.ccxt_exchange,
            "price": 100.0,
        },
    )
    monkeypatch.setattr(
        "aletheia.data.price_feed.get_fresh_price_for_protective_close",
        lambda *_args, **_kwargs: 100.0,
    )
    monkeypatch.setattr("aletheia.trust.guard_chain.run_guards", lambda _ctx: [])
    monkeypatch.setattr(
        "aletheia.trust.guard_chain.summarize",
        lambda _items: {"blocks": [], "details": []},
    )
    monkeypatch.setattr(
        "aletheia.trading.entry_snapshot.capture_attribution_snapshot_json",
        lambda _symbol: None,
    )

    decision = td.TradingDecision(
        action="trade",
        symbol="BTC/USDT",
        direction="long",
        conviction=70,
        reasoning="persona sizing acceptance",
        market_type="spot",
    )
    decision.position_size_usdt = td._compute_position_size(
        decision.conviction,
        symbol=decision.symbol,
        direction=decision.direction,
        paper_trade_count=50,
    )

    prepared = asyncio.run(
        td.prepare_decision_as_proposal(decision, "paper_internal")
    )
    proposal = prepared["proposal"]
    assert decision.position_size_usdt == 50.0
    assert proposal.price == 100.0
    assert proposal.quantity == 0.5

    settings_src = _PAPER_TRADING_SETTINGS.read_text(encoding="utf-8")
    assert "Active entry consumers:" in settings_src
    assert "Leverage, stop, and add parameters remain experimental/reference-only." in settings_src


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


_CLOSED_IDS = count()


def _closed(repo, Position, regime, pnl, reason="take_profit", entry=100.0, qty=1.0):
    from tests.test_outcome_closure import _insert_entry_chain

    synthetic = reason == "reconciled_gone"
    position_id = f"persona-close-{next(_CLOSED_IDS)}"
    causal_ids = (None, None, None)
    if not synthetic:
        causal_ids = _insert_entry_chain(
            position_id=position_id,
            symbol="BTC/USDT",
            market_type="usdt_futures",
            direction="long",
            quantity=qty,
            price=entry,
            execution_mode="okx_demo",
        )
    opportunity_id, proposal_id, order_id = causal_ids
    repo.insert_position(Position(
        id=position_id,
        symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        quantity=qty, entry_price=entry, execution_mode="okx_demo",
        current_price=entry + pnl / qty,
        status="closed", realized_pnl=pnl, close_reason=reason, regime_tag=regime,
        closed_at="2026-07-12T08:00:00+00:00",
        entry_fee=0.0, exit_fee=0.0,
        funding_cost=0.0, funding_source="broker_ledger",
        entry_price_source="unavailable" if synthetic else "broker_fill",
        entry_fill_verified=not synthetic,
        close_category="administrative" if synthetic else "strategy",
        exit_price_source="unavailable" if synthetic else "broker_fill",
        exit_fill_verified=not synthetic,
        outcome_eligible=not synthetic,
        outcome_exclusion_reason="administrative_close" if synthetic else "eligible",
        outcome_semantics_version="oi-v1",
        opportunity_id=opportunity_id,
        proposal_id=proposal_id,
        order_id=order_id,
    ))


@spec_claim(
    SpecClaim("PS-S07", "persona_sizing", "§7 learned appetite nudge (dormant + bounded)", "L2b",
              "learned_appetite_adjustment returns 0.0 when learning disabled OR clean "
              "samples < gate (self-refreeze); when enabled with enough clean wins in a "
              "bucket → positive bounded nudge; clean losses → negative nudge."),
)
def test_learned_appetite_adjustment(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.config.schema import AletheiaConfig
    import aletheia.config.loader as loader
    from aletheia.storage.models import Position
    from aletheia.storage import repositories as repo
    from aletheia.trading import sizing_policy

    cfg = AletheiaConfig()
    cfg.trading.persona_learning_min_samples = 3
    monkeypatch.setattr(loader, "load_config", lambda: cfg)

    # 4 clean BULL wins (+2% each).
    for _ in range(4):
        _closed(repo, Position, "bull", pnl=2.0)

    # Disabled → dormant (0.0) even with data.
    cfg.trading.persona_learning_enabled = False
    assert sizing_policy.learned_appetite_adjustment("bull", cfg) == 0.0

    # Enabled → positive bounded nudge for the profitable bucket.
    cfg.trading.persona_learning_enabled = True
    adj = sizing_policy.learned_appetite_adjustment("bull", cfg)
    assert 0 < adj <= sizing_policy.MAX_ADJ, f"profitable bucket → positive bounded nudge, got {adj}"

    # A bucket below the sample gate → 0 (self-refreeze on thin data).
    assert sizing_policy.learned_appetite_adjustment("volatile", cfg) == 0.0

    # A losing bucket → negative nudge (defensive).
    for _ in range(4):
        _closed(repo, Position, "bear", pnl=-2.0, reason="stop_loss")
    assert sizing_policy.learned_appetite_adjustment("bear", cfg) < 0


@spec_claim(
    SpecClaim("PS-S08", "persona_sizing", "§7 learning uses CLEAN data only", "L2b",
              "synthetic/administrative closes (reconciled_gone) are excluded from the "
              "learner's reward — a huge fake reconciled_gone 'win' must not move the "
              "appetite nudge (the reconciler-thrash-poisons-learning lesson)."),
)
def test_learning_excludes_synthetic_closes(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.config.schema import AletheiaConfig
    import aletheia.config.loader as loader
    from aletheia.storage.models import Position
    from aletheia.storage import repositories as repo
    from aletheia.trading import sizing_policy

    cfg = AletheiaConfig()
    cfg.trading.persona_learning_enabled = True
    cfg.trading.persona_learning_min_samples = 3
    monkeypatch.setattr(loader, "load_config", lambda: cfg)

    # 4 clean bull LOSSES (-2%) + 10 synthetic reconciled_gone huge "wins" (+50%).
    for _ in range(4):
        _closed(repo, Position, "bull", pnl=-2.0, reason="stop_loss")
    for _ in range(10):
        _closed(repo, Position, "bull", pnl=50.0, reason="reconciled_gone")

    # If synthetic wins leaked in, the nudge would be strongly POSITIVE. It must stay
    # negative (only the 4 genuine losses count).
    adj = sizing_policy.learned_appetite_adjustment("bull", cfg)
    assert adj < 0, f"synthetic closes must not move the learner; got {adj}"


@spec_claim(
    SpecClaim("PS-S09", "persona_sizing", "§9 sizing_policy_health observability", "L2b",
              "sizing_policy_health reports enabled flag + per-regime clean sample "
              "counts / mean reward / active flag for the doctor probe; dormant when "
              "learning disabled."),
)
def test_sizing_policy_health(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.config.schema import AletheiaConfig
    import aletheia.config.loader as loader
    from aletheia.storage.models import Position
    from aletheia.storage import repositories as repo
    from aletheia.trading import sizing_policy

    cfg = AletheiaConfig()
    cfg.trading.persona_learning_enabled = True
    cfg.trading.persona_learning_min_samples = 3
    monkeypatch.setattr(loader, "load_config", lambda: cfg)
    for _ in range(4):
        _closed(repo, Position, "bull", pnl=2.0)

    h = sizing_policy.sizing_policy_health(cfg)
    assert h["enabled"] is True
    assert h["buckets"]["bull"]["clean_samples"] == 4
    assert h["buckets"]["bull"]["active"] is True
    assert h["buckets"]["volatile"]["active"] is False  # no samples


@spec_claim(
    SpecClaim("PS-S10", "persona_sizing", "§8 progressive unlock ladder + distress refreeze", "L2b",
              "effective_operator_decidable: OFF → static dict (P1-P3); ON → unlock only "
              "production-consumed entry-sizing params in ladder order as eligible closes "
              "accrue; reference-only params are ignored and distress refreezes ALL."),
)
def test_progressive_unlock_ladder(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.config.schema import AletheiaConfig
    import aletheia.config.loader as loader
    from aletheia.storage.models import Position
    from aletheia.storage import repositories as repo
    from aletheia.trading.persona import effective_operator_decidable

    cfg = AletheiaConfig()
    cfg.trading.persona_unlock_step_samples = 5
    monkeypatch.setattr(loader, "load_config", lambda: cfg)
    roll = cfg.trading.personas["roll"]
    assert roll.unlock_ladder == ["notional_usdt", "kelly_fraction"]

    # OFF → static (empty) regardless of samples.
    assert effective_operator_decidable(roll, cfg) == {}

    cfg.trading.persona_progressive_unlock_enabled = True
    # 0 clean closes → nothing unlocked.
    assert effective_operator_decidable(roll, cfg) == {}

    # Persisted/custom ladders may still contain old reference-only fields. Runtime
    # must ignore those fields rather than granting authority without a consumer.
    roll.unlock_ladder = [
        "notional_usdt", "leverage_max", "per_trade_loss_pct",
        "max_adds", "kelly_fraction",
    ]

    # 12 clean closes, step 5 → both production-consumed params unlock.
    for _ in range(12):
        _closed(repo, Position, "bull", pnl=1.0)
    dec = effective_operator_decidable(roll, cfg)
    assert dec == {"notional_usdt": True, "kelly_fraction": True}

    # Distress (kill switch) → refreeze ALL.
    cfg.runtime.kill_switch_active = True
    assert effective_operator_decidable(roll, cfg) == {}, "distress must refreeze all unlocked params"

    settings_src = _PAPER_TRADING_SETTINGS.read_text(encoding="utf-8")
    assert "only active entry parameters" in settings_src
    assert "counts eligible historical" in settings_src
    assert "does not establish a fresh Persona-specific evidence window" in settings_src


@spec_claim(
    SpecClaim("PS-S11", "persona_sizing", "§6.3 pyramid add-on policy (gated + hard-capped)", "L2b",
              "pyramid_plan is a bounded pure helper for winners; no production caller or "
              "add-order materializer exists, so Settings/API block new enablement while "
              "legacy configurations can switch off. Helper output remains hard-capped."),
)
def test_pyramid_plan(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.config.schema import AletheiaConfig
    import aletheia.config.loader as loader
    from aletheia.trading import sizing_policy

    cfg = AletheiaConfig()
    cfg.trading.persona_sizing_enabled = True
    cfg.trading.active_persona = "roll"   # max_adds init 2, add_fraction init 0.5
    cfg.trading.persona_pyramid_add_trigger_pct = 1.0
    cfg.risk.max_per_symbol_notional_usdt = 400.0
    monkeypatch.setattr(loader, "load_config", lambda: cfg)

    winner = {"entry_price": 100.0, "quantity": 1.0, "unrealized_pnl": 5.0}  # +5% → past trigger

    # Disabled → no add.
    assert sizing_policy.pyramid_plan(winner, adds_done=0, cfg=cfg)["add"] is False

    # Enabled → add = 0.5 * 100 base = 50 notional.
    cfg.trading.persona_pyramid_enabled = True
    plan = sizing_policy.pyramid_plan(winner, adds_done=0, cfg=cfg)
    assert plan["add"] is True and abs(plan["add_notional"] - 50.0) < 1e-6, plan

    # Not a winner (0 profit) → no add.
    flat = {"entry_price": 100.0, "quantity": 1.0, "unrealized_pnl": 0.0}
    assert sizing_policy.pyramid_plan(flat, adds_done=0, cfg=cfg)["add"] is False

    # TOTAL added ≤ 1× base (owner 2026-07-02): after 2 adds of 0.5×base each, the
    # cumulative pyramid = base, so a 3rd add is refused by the total cap.
    assert sizing_policy.pyramid_plan(winner, adds_done=2, cfg=cfg)["add"] is False

    # max_adds hard limit (roll init 3) also refuses beyond the count.
    assert sizing_policy.pyramid_plan(winner, adds_done=3, cfg=cfg)["add"] is False

    # HARD CAP: a huge base near the per-symbol cap → add clamped to remaining room.
    big = {"entry_price": 100.0, "quantity": 3.9, "unrealized_pnl": 20.0}  # base 390, cap 400
    capped = sizing_policy.pyramid_plan(big, adds_done=0, cfg=cfg)
    assert capped["add_notional"] <= 10.0 + 1e-6, f"add must be clamped to per-symbol cap room: {capped}"

    # Product boundary: the helper has no production trigger/materializer, so the
    # API and Settings must not let a user newly enable a no-op capability.
    from fastapi import HTTPException
    from aletheia.server.routes import settings as settings_routes

    saved_configs = []
    monkeypatch.setattr(settings_routes, "load_config", lambda: cfg)
    monkeypatch.setattr(settings_routes, "save_config", lambda c: saved_configs.append(c))
    monkeypatch.setattr("aletheia.files.jsonl.write_audit", lambda *_a, **_k: None)

    cfg.trading.persona_pyramid_enabled = False
    with pytest.raises(HTTPException) as exc:
        asyncio.run(settings_routes.update_trading_config(
            settings_routes.TradingConfigUpdate(persona_pyramid_enabled=True)
        ))
    assert exc.value.status_code == 409
    assert "no production trigger" in str(exc.value.detail)
    assert saved_configs == []

    with pytest.raises(HTTPException) as exc:
        asyncio.run(settings_routes.update_trading_config(
            settings_routes.TradingConfigUpdate(persona_auto_advance_enabled=True)
        ))
    assert exc.value.status_code == 409
    assert "prospective" in str(exc.value.detail)

    # Legacy enabled configurations retain an explicit off-ramp.
    cfg.trading.persona_pyramid_enabled = True
    result = asyncio.run(settings_routes.update_trading_config(
        settings_routes.TradingConfigUpdate(persona_pyramid_enabled=False)
    ))
    assert result["trading"]["persona_pyramid_enabled"] is False
    assert len(saved_configs) == 1

    settings_src = _PAPER_TRADING_SETTINGS.read_text(encoding="utf-8")
    assert "Pyramid add-on policy helper (no runtime trigger)" in settings_src
    assert "does not create a paper, demo, or live order" in settings_src
    assert "new enablement is blocked" in settings_src


@spec_claim(
    SpecClaim("PS-S12", "persona_sizing", "§4.2 risk envelope stays the outer bound", "L2c",
              "Invariant: persona/learning only produce DECISION INPUTS. The pyramid "
              "hard-cap never lets base+adds exceed max_per_symbol_notional_usdt — the "
              "risk envelope is immutable regardless of persona/learning state."),
)
def test_pyramid_never_exceeds_exposure_cap(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.config.schema import AletheiaConfig
    import aletheia.config.loader as loader
    from aletheia.trading import sizing_policy

    cfg = AletheiaConfig()
    cfg.trading.persona_sizing_enabled = True
    cfg.trading.persona_pyramid_enabled = True
    cfg.trading.active_persona = "roll"
    cfg.risk.max_per_symbol_notional_usdt = 300.0
    monkeypatch.setattr(loader, "load_config", lambda: cfg)

    base = {"entry_price": 100.0, "quantity": 1.0, "unrealized_pnl": 10.0}  # base 100
    total = 100.0
    for i in range(5):  # try to over-add
        plan = sizing_policy.pyramid_plan(base, adds_done=i, cfg=cfg)
        if not plan["add"]:
            break
        total += plan["add_notional"]
    assert total <= 300.0 + 1e-6, f"cumulative exposure must never exceed the cap: {total}"


@spec_claim(
    SpecClaim("PS-S13", "persona_sizing", "§8 rollout automation safety hold", "L2b",
              "advance_persona_rollout is read-only: disabled unless explicitly configured, "
              "retains specific distress/integrity hold reasons, and never changes Persona "
              "flags without a prospective Persona/policy/step verdict. Settings blocks new "
              "enablement while allowing legacy enabled configurations to be switched off."),
)
def test_advance_persona_rollout(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.config.schema import AletheiaConfig
    import aletheia.config.loader as loader
    from aletheia.trading.persona_rollout import advance_persona_rollout

    saved = {}
    monkeypatch.setattr(loader, "save_config", lambda c: saved.update({"cfg": c}))
    monkeypatch.setattr("aletheia.files.jsonl.write_audit", lambda *a, **k: None, raising=False)

    cfg = AletheiaConfig()
    monkeypatch.setattr(loader, "load_config", lambda: cfg)

    # Master switch OFF → disabled no-op.
    assert advance_persona_rollout()["action"] == "disabled"

    # ON but persona_sizing not started → hold.
    cfg.trading.persona_auto_advance_enabled = True
    assert advance_persona_rollout()["action"] == "hold"

    # Sizing on and broadly healthy is still not prospective rollout evidence.
    cfg.trading.persona_sizing_enabled = True
    r = advance_persona_rollout()
    assert r == {
        "action": "hold",
        "reason": "prospective_persona_policy_step_verdict_unavailable",
    }
    assert cfg.trading.persona_progressive_unlock_enabled is False
    assert cfg.trading.persona_learning_enabled is False
    assert cfg.trading.persona_pyramid_enabled is False
    assert cfg.trading.active_persona == "conservative"
    assert saved == {}, "rollout safety probe must never persist config changes"

    # A legacy config with progressive unlock already on must not auto-enable Learning.
    cfg.trading.persona_progressive_unlock_enabled = True
    assert advance_persona_rollout()["action"] == "hold"
    assert cfg.trading.persona_learning_enabled is False
    assert saved == {}

    # distress → hold even mid-ladder.
    cfg2 = AletheiaConfig()
    cfg2.trading.persona_auto_advance_enabled = True
    cfg2.trading.persona_sizing_enabled = True
    cfg2.runtime.kill_switch_active = True   # distress
    monkeypatch.setattr(loader, "load_config", lambda: cfg2)
    assert advance_persona_rollout()["action"] == "hold"
    assert cfg2.trading.persona_progressive_unlock_enabled is False  # not advanced under distress

    settings_src = _PAPER_TRADING_SETTINGS.read_text(encoding="utf-8")
    assert "Auto-advance rollout (Experimental safety hold)" in settings_src
    assert "The 8-hour job is currently read-only" in settings_src
    assert "new enablement is blocked" in settings_src
