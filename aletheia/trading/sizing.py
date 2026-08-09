"""Conviction-based position sizing.

Fractional Kelly formula adapted for conviction scores:
  size = base_notional * kelly_fraction * conviction_ratio * drawdown_scale

Used by System Operator (evaluate_trading_opportunity) and can be called from
paper_trader for RuleSet-driven entries. Does NOT touch risk engine limits
— all proposals still pass through Risk Engine gates after sizing.
"""
from __future__ import annotations


_DEFAULT_KELLY_FRACTION = 0.25   # quarter-Kelly: conservative, reduces variance
_MIN_MULTIPLIER = 0.3            # floor: $25 base → $7.50 minimum at lowest conviction
_MAX_MULTIPLIER = 2.5            # cap: $25 base → $62.50 maximum at peak conviction
_CONVICTION_MIDPOINT = 70.0      # conviction at which multiplier = 1.0 (base notional)


def compute_position_size(
    conviction: int,
    base_notional: float,
    *,
    drawdown_pct: float = 0.0,
    kelly_fraction: float = _DEFAULT_KELLY_FRACTION,
    min_multiplier: float = _MIN_MULTIPLIER,
    max_multiplier: float = _MAX_MULTIPLIER,
) -> float:
    """Compute notional position size from conviction score.

    Args:
        conviction: 0-100 int representing trade confidence
        base_notional: base USDT amount (e.g. 25.0 from config)
        drawdown_pct: current portfolio drawdown % (0-100); scales size down
        kelly_fraction: fraction of full Kelly to use (default 0.25 = quarter-Kelly)
        min_multiplier: floor on the size multiplier
        max_multiplier: cap on the size multiplier

    Returns:
        Target notional size in USDT, rounded to 2 decimal places
    """
    if base_notional <= 0:
        return 0.0
    conviction_clamped = max(0, min(100, conviction))

    # Linear scaling: 0% conviction → min_multiplier, midpoint → 1.0, 100% → max_multiplier
    if conviction_clamped <= _CONVICTION_MIDPOINT:
        t = conviction_clamped / _CONVICTION_MIDPOINT
        multiplier = min_multiplier + t * (1.0 - min_multiplier)
    else:
        t = (conviction_clamped - _CONVICTION_MIDPOINT) / (100.0 - _CONVICTION_MIDPOINT)
        multiplier = 1.0 + t * (max_multiplier - 1.0)

    # Apply Kelly fraction
    multiplier *= kelly_fraction * 4  # 4× so that at kelly_fraction=0.25 the midpoint stays at 1.0

    # Drawdown scale: linear from 1.0 at 0% DD to 0.5 at 10%+ DD
    if drawdown_pct > 0:
        dd_scale = max(0.5, 1.0 - (drawdown_pct / 20.0))
        multiplier *= dd_scale

    # Clamp to configured bounds
    multiplier = max(min_multiplier, min(max_multiplier, multiplier))

    return round(base_notional * multiplier, 2)


def conviction_to_multiplier(conviction: int) -> float:
    """Quick multiplier lookup without Kelly/drawdown adjustments. Used for display."""
    result = compute_position_size(conviction, 1.0)
    return round(result, 3)


def vol_target_size(
    symbol: str,
    *,
    conviction: int = 70,
    direction: str | None = None,
    drawdown_pct: float = 0.0,
    atr_pct_override: float | None = None,
    paper_trade_count: int = 0,
    candidate_key: str | None = None,
    outcome_candidate_key: str | None = None,
    opportunity_id: str | None = None,
    sizing_trace: dict | None = None,
) -> float:
    """Unified volatility-target sizing with sample-aware Kelly fraction.

    P1-5: paper_trade_count adjusts the effective Kelly fraction:
      < 10 trades  → effective_kelly = 0.10 (one-tenth Kelly — new strategies start small)
      10-30 trades → linear ramp from 0.10 to base_kelly
      >= 30 trades → base_kelly unchanged (backward compatible)

    Reads config for risk budget and caps. Falls back to flat base_notional
    when vol_sizing_enabled=False or no ATR data is available.
    """
    from aletheia.config.loader import load_config
    from aletheia.trading.persona import effective_sizing_params

    cfg = load_config()
    # Persona seam (P1/P2): base notional + kelly come from the active persona
    # (P2: context-decided within range for operator_decidable params; else init);
    # persona OFF → legacy flat config (default_notional_usdt / vol_kelly_cap_fraction),
    # zero behavior change. `symbol` feeds the P2 sizing context (regime/F&G/vol).
    static_params = effective_sizing_params(
        cfg, symbol=symbol, direction=direction, apply_learning=False,
    )
    learned_params = effective_sizing_params(
        cfg, symbol=symbol, direction=direction, apply_learning=True,
    )

    # Resolve market volatility once so baseline and treatment differ only in the
    # persona Learning proposal, never because they observed different inputs.
    atr_pct = atr_pct_override
    if cfg.trading.vol_sizing_enabled and atr_pct is None:
        atr_pct = _lookup_atr_pct(symbol)

    def _from_params(params: dict) -> float:
        base = float(params["notional_usdt"])
        base_kelly = float(params["kelly_fraction"])
        if paper_trade_count < 10:
            effective_kelly = 0.1
        elif paper_trade_count < 30:
            t = (paper_trade_count - 10) / 20.0
            effective_kelly = 0.1 + t * (base_kelly - 0.1)
        else:
            effective_kelly = base_kelly
        sizing_base = base
        if cfg.trading.vol_sizing_enabled and atr_pct and atr_pct > 0:
            risk_budget = cfg.trading.vol_target_risk_usdt
            raw_size = risk_budget / (atr_pct / 100.0)
            kelly_cap = base * (effective_kelly * 4) * _MAX_MULTIPLIER
            sizing_base = min(raw_size, kelly_cap)
        return compute_position_size(
            conviction, sizing_base, drawdown_pct=drawdown_pct,
            kelly_fraction=effective_kelly,
        )

    baseline_notional = _from_params(static_params)
    # Phase 1 exposes one learned parameter only: base notional. Kelly, stops,
    # leverage and pyramiding remain at their static/persona values.
    learned_notional_params = dict(static_params)
    learned_notional_params["notional_usdt"] = learned_params["notional_usdt"]
    learned_notional = _from_params(learned_notional_params)
    from aletheia.trading.sizing_policy import resolve_learned_notional
    trace = resolve_learned_notional(
        baseline_notional=baseline_notional,
        learned_notional=learned_notional,
        symbol=symbol,
        direction=direction,
        regime_bucket=learned_params.get("_regime"),
        candidate_key=candidate_key,
        outcome_candidate_key=outcome_candidate_key,
        opportunity_id=opportunity_id,
    )
    if sizing_trace is not None:
        sizing_trace.clear()
        sizing_trace.update(trace)
    return round(float(trace["actual_notional"]), 2)


def _lookup_atr_pct(symbol: str) -> float | None:
    """Fetch ATR% from universe_feature_snapshots (most recent row).

    universe_feature_snapshots uses asset_id (FK → universe_registry) + ts,
    not symbol + snapshot_at. We join through universe_registry to filter by symbol.
    """
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        row = db.execute(
            "SELECT ufs.atr_pct FROM universe_feature_snapshots ufs "
            "JOIN universe_registry ur ON ufs.asset_id = ur.id "
            "WHERE ur.symbol = ? "
            "ORDER BY ufs.ts DESC LIMIT 1",
            (symbol,),
        ).fetchone()
        if row and row[0]:
            return float(row[0])
    except Exception:
        pass
    return None


# --- Dynamic position sizing (QuantMuse inspired) ---

def dynamic_size_multiplier(
    *,
    trend_strength: float | None = None,
    confidence: float | None = None,
    macro_alignment: float | None = None,
    portfolio_heat: float | None = None,
    historical_edge: float | None = None,
    volatility_regime: str | None = None,
) -> tuple[float, dict[str, float]]:
    """Compute a dynamic multiplier that adjusts position size based on
    multiple market context factors.

    Returns (multiplier, breakdown) where breakdown maps factor names
    to their contribution.  The multiplier is centered at 1.0 (no change)
    and typically ranges from 0.3 (very unfavorable) to 1.5 (very favorable).

    Factors:
        trend_strength: 0-1, strength of the trend (stronger → larger)
        confidence: 0-1, committee/agent confidence (higher → larger)
        macro_alignment: 0-1, macro environment alignment (aligned → larger)
        portfolio_heat: 0-1, current portfolio risk (hotter → smaller)
        historical_edge: 0-1, historical win rate for this setup (higher → larger)
        volatility_regime: "low" | "normal" | "high" (affects size)

    Returns:
        (multiplier, breakdown) where breakdown explains each factor's contribution.
    """
    breakdown: dict[str, float] = {}
    multiplier = 1.0

    # Trend strength: 0.7 at trend=0 → 1.0 at trend=0.5 → 1.3 at trend=1.0
    if trend_strength is not None:
        tf = 0.7 + 0.6 * max(0.0, min(1.0, trend_strength))
        breakdown["trend_strength"] = tf
        multiplier *= tf

    # Confidence: 0.8 at conf=0 → 1.0 at conf=0.5 → 1.2 at conf=1.0
    if confidence is not None:
        cf = 0.8 + 0.4 * max(0.0, min(1.0, confidence))
        breakdown["confidence"] = cf
        multiplier *= cf

    # Macro alignment: 0.85 at align=0 → 1.0 at align=0.5 → 1.15 at align=1.0
    if macro_alignment is not None:
        mf = 0.85 + 0.3 * max(0.0, min(1.0, macro_alignment))
        breakdown["macro_alignment"] = mf
        multiplier *= mf

    # Portfolio heat: 1.2 at heat=0 → 1.0 at heat=0.5 → 0.6 at heat=1.0
    if portfolio_heat is not None:
        hf = 1.2 - 0.6 * max(0.0, min(1.0, portfolio_heat))
        breakdown["portfolio_heat"] = hf
        multiplier *= hf

    # Historical edge: 0.9 at edge=0 → 1.0 at edge=0.5 → 1.1 at edge=1.0
    if historical_edge is not None:
        hf = 0.9 + 0.2 * max(0.0, min(1.0, historical_edge))
        breakdown["historical_edge"] = hf
        multiplier *= hf

    # Volatility regime: high vol reduces size, low vol increases slightly
    if volatility_regime:
        vol_factors = {"low": 1.1, "normal": 1.0, "high": 0.7, "extreme": 0.4}
        vf = vol_factors.get(volatility_regime, 1.0)
        breakdown["volatility_regime"] = vf
        multiplier *= vf

    # Clamp to safe range
    multiplier = max(0.3, min(1.5, multiplier))

    return round(multiplier, 3), breakdown
