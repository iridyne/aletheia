"""Spec: Universe Pipeline integrity (scanner → allocator → signal_emitter → outcomes)

Executable claims for the Scanner/Observer audit fixes (2026-07-01).
Source: docs/plans/scanner_observer_audit_fix_plan_2026-07-01.md
        docs/research/UNIVERSE_SCANNER_AUDIT_2026-07-01.md

Locks the highest-leverage fix (forward-outcome direction must come from the same
source as the emitted opportunity) and the config-consumption / market_type /
feature-keying invariants against regression.
"""

import inspect

from tests.specs.spec_meta import SpecClaim, spec_claim


# ---------------------------------------------------------------------------
# L2b — Behavioral (direction inference + score consumption)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("UP-01", "universe_pipeline", "§2 US-4 direction inference", "L2b",
              "_infer_direction has an 'event' branch: sharp 1h move → long/short, "
              "small move → None (mirrors score_event thresholds)"),
)
def test_infer_direction_event_branch():
    from aletheia.universe.signal_emitter import _infer_direction

    assert _infer_direction("event", {"return_1h": 9.0}) == "long"
    assert _infer_direction("event", {"return_1h": -9.0}) == "short"
    # Below the ±4% event threshold → no directional call.
    assert _infer_direction("event", {"return_1h": 2.0}) is None


@spec_claim(
    SpecClaim("UP-02", "universe_pipeline", "§2 US-1 scanner_weights consumed", "L2b",
              "compute_total_score multiplies opportunity_score by the operator's "
              "cfg.scanner_weights[family] (PI-adjustable weight is no longer inert)"),
)
def test_scanner_weights_scale_opportunity_score():
    from aletheia.universe.allocator import compute_total_score
    from aletheia.config.schema import UniverseConfig

    feat = {"tradability_score": 50.0, "quote_volume_24h": 1e12, "spread_bps": 2.0}
    sr = {"opportunity_score": 50.0, "scanner_family": "momentum"}

    base = compute_total_score(dict(feat), sr, UniverseConfig())  # weights={} → 1.0
    boosted = compute_total_score(
        dict(feat), sr, UniverseConfig(scanner_weights={"momentum": 2.0}),
    )
    suppressed = compute_total_score(
        dict(feat), sr, UniverseConfig(scanner_weights={"momentum": 0.1}),
    )
    assert boosted > base > suppressed


# ---------------------------------------------------------------------------
# L2a — Structural (same-source direction, market_type propagation, feature keying)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("UP-03", "universe_pipeline", "§1.A US-4 same-source direction", "L2a",
              "forward-outcome (inserted_members) direction prefers scanner_direction "
              "— the SAME source the emitted opportunity uses — instead of "
              "re-inferring (which dropped direction to None for 54% of momentum)"),
)
def test_forward_outcome_direction_uses_scanner_direction():
    from aletheia.universe import service

    src = inspect.getsource(service)
    assert 'item.get("scanner_direction")' in src, (
        "inserted_members direction must prefer scanner_direction (same source as "
        "the emitted opportunity), not re-infer with _infer_direction alone"
    )


@spec_claim(
    SpecClaim("UP-04", "universe_pipeline", "§2 US-3 market_type propagation", "L2a",
              "inserted_members carries market_type so signal_emitter labels perp "
              "opportunities correctly (was fallback-'spot' for all)"),
)
def test_inserted_members_carry_market_type():
    from aletheia.universe import service

    src = inspect.getsource(service)
    assert '"market_type": item.get("market_type"' in src, (
        "inserted_members must include market_type; otherwise signal_emitter:70 "
        "falls back to 'spot' for every opportunity, mislabeling perp"
    )


@spec_claim(
    SpecClaim("UP-05", "universe_pipeline", "§2 US-8 feature keying", "L2a",
              "signal_emitter keys features by (symbol, market_type) so a symbol's "
              "spot and perp feature rows don't overwrite each other"),
)
def test_feature_lookup_uses_composite_key():
    from aletheia.universe import signal_emitter

    src = inspect.getsource(signal_emitter)
    assert "feat_by_key" in src and 'f.get("market_type"' in src, (
        "feature lookup must be keyed by (symbol, market_type), not symbol alone"
    )


# ---------------------------------------------------------------------------
# Observer side — key_observations exposure, per-symbol guards, scanner hint
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("UP-06", "universe_pipeline", "§2 MO-1 key_observations to PI", "L2b",
              "PI layer1 exposes market_observer's key_observations (numeric "
              "citations); previously only a truncated thesis reached the PI"),
)
def test_key_observations_reach_pi():
    from aletheia.agent_shell.trading_context import _top_observations

    # Parser: JSON array → first n bullets; junk/empty → [].
    assert _top_observations('["1h RSI 68", "price > SMA20", "funding +0.03%"]', n=2) == [
        "1h RSI 68", "price > SMA20",
    ]
    assert _top_observations("") == []
    assert _top_observations(None) == []
    assert _top_observations("not json") == []

    # Structural: layer1 carries it AND the PI prompt renders it.
    from aletheia.agent_shell import trading_context, trading_decision
    assert '"key_observations": _top_observations' in inspect.getsource(trading_context)
    assert "key_observations" in inspect.getsource(trading_decision)


@spec_claim(
    SpecClaim("UP-07", "universe_pipeline", "§2 MO-2 per-symbol guard (断口F)", "L2a",
              "market_observer builds guard advisory PER-SYMBOL with scanner_family "
              "(the run-level symbol-LIST context never matched any guard)"),
)
def test_market_observer_per_symbol_guard():
    from aletheia.knowledge import router
    from aletheia.agents import market_observer

    assert hasattr(router, "build_guard_advisory")
    mo_src = inspect.getsource(market_observer)
    assert "build_guard_advisory" in mo_src
    assert '"scanner_family": scanner_family' in mo_src


@spec_claim(
    SpecClaim("UP-08", "universe_pipeline", "§2 MO-4 scanner_family in prompt", "L2a",
              "the market_observer LLM prompt names the scanner family that surfaced "
              "the symbol so the read validates that family, not a generic scan"),
)
def test_market_observer_prompt_scanner_hint():
    from aletheia.agents import market_observer

    src = inspect.getsource(market_observer)
    assert "scanner_hint" in src and "surfaced by the" in src


@spec_claim(
    SpecClaim("UP-09", "universe_pipeline", "§2 MO-3 F&G/top_movers populated", "L2a",
              "build_market_context_from_db populates MacroSnapshot.fng_value / "
              "fng_classification / top_movers_summary (collected + rendered but "
              "never read on the runtime path → the LLM always saw None)"),
)
def test_macro_context_populates_fng():
    from aletheia.data import context_builder

    src = inspect.getsource(context_builder.build_market_context_from_db)
    assert "fng_value=macro_row.get" in src
    assert "top_movers_summary=macro_row.get" in src


@spec_claim(
    SpecClaim("UP-10", "universe_pipeline", "§2 US-9 short hard-disable", "L2b",
              "when short_enabled=False, NO score overrides the regime gate for a "
              "short (previously score>=66 slipped through)"),
)
def test_short_disable_is_hard():
    from aletheia.universe.signal_emitter import _passes_regime_gate

    class _Regime:
        long_bias = 0.0
        short_bias = 0.0
        state = "bull"

    r = _Regime()
    # short_enabled=False → even a 99 score short is blocked
    assert _passes_regime_gate("short", 99.0, r, short_enabled=False) is False
    # short_enabled=True → high score still overrides (unchanged behavior)
    assert _passes_regime_gate("short", 99.0, r, short_enabled=True) is True
    # long is never affected by the short switch
    assert _passes_regime_gate("long", 99.0, r, short_enabled=False) is True


@spec_claim(
    SpecClaim("UP-11", "universe_pipeline", "§2 MO-6 single-symbol RS rank", "L2b",
              "cross-asset RS rank is omitted for <2 symbols (a lone 'Rank 1 of 1' "
              "reads to the LLM as 'strongest in market')"),
)
def test_rs_rank_omitted_for_single_symbol():
    from aletheia.trading.market_context import _render_relative_strength

    assert _render_relative_strength({"BTC/USDT": 5.0}) == ""
    multi = _render_relative_strength({"BTC/USDT": 5.0, "ETH/USDT": 2.0})
    assert "Rank" in multi


@spec_claim(
    SpecClaim("UP-12", "universe_pipeline", "§2 MO-8 crowding n/a", "L2b",
              "crowding renders 'n/a' when no funding/OI/LSR input exists, not "
              "'0/100' (which the LLM reads as 'not crowded')"),
)
def test_crowding_na_without_inputs():
    from aletheia.data.normalizer import compute_leverage_state
    from aletheia.data.context_builder import build_market_context_snapshot

    lv = compute_leverage_state("BTC/USDT")  # no funding/oi/lsr → crowding 0, no input
    out = build_market_context_snapshot(["BTC/USDT"], {}, {"BTC/USDT": lv}, macro=None)
    assert "0/100" not in out


@spec_claim(
    SpecClaim("UP-13", "universe_pipeline", "§2 MO-7 degraded fallback marked", "L2a",
              "the market_context hard-fallback labels itself DEGRADED so the LLM "
              "doesn't treat last-known figures as live source-of-truth"),
)
def test_fallback_context_marks_degraded():
    from aletheia.trading import market_context

    assert "DEGRADED" in inspect.getsource(market_context)
