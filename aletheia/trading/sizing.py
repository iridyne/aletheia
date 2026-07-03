"""Conviction-based position sizing.

Fractional Kelly formula adapted for conviction scores:
  size = base_notional * kelly_fraction * conviction_ratio * drawdown_scale

Used by PI agent (evaluate_trading_opportunity) and can be called from
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
    drawdown_pct: float = 0.0,
    atr_pct_override: float | None = None,
    paper_trade_count: int = 0,
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
    _eff = effective_sizing_params(cfg, symbol=symbol)
    base = _eff["notional_usdt"]
    base_kelly = _eff["kelly_fraction"]

    # P1-5: sample-aware Kelly fraction
    if paper_trade_count < 10:
        effective_kelly = 0.1
    elif paper_trade_count < 30:
        t = (paper_trade_count - 10) / 20.0
        effective_kelly = 0.1 + t * (base_kelly - 0.1)
    else:
        effective_kelly = base_kelly

    if not cfg.trading.vol_sizing_enabled:
        return compute_position_size(conviction, base, drawdown_pct=drawdown_pct,
                                     kelly_fraction=effective_kelly)

    # Get ATR%
    atr_pct = atr_pct_override
    if atr_pct is None:
        atr_pct = _lookup_atr_pct(symbol)

    if not atr_pct or atr_pct <= 0:
        return compute_position_size(conviction, base, drawdown_pct=drawdown_pct,
                                     kelly_fraction=effective_kelly)

    # Core formula: size such that 1 ATR move = risk_budget
    risk_budget = cfg.trading.vol_target_risk_usdt
    raw_size = risk_budget / (atr_pct / 100.0)

    # Kelly cap scaled by effective_kelly
    kelly_cap = base * (effective_kelly * 4) * _MAX_MULTIPLIER
    sized = min(raw_size, kelly_cap)

    # Apply conviction multiplier and drawdown scale
    return compute_position_size(conviction, sized, drawdown_pct=drawdown_pct,
                                 kelly_fraction=effective_kelly)


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
