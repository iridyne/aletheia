"""Tests for the deterministic post-debate leverage resolver (#835).

Covers the completion-gate cases from the issue:
- post-debate conviction change → leverage recalculated (not stale pre-debate value)
- high volatility → capped leverage
- tight/wide stops → stop-distance safety constraint
- low account headroom → headroom constraint
- tier cap clamping
- missing entry / stop / exhausted headroom → DecisionUnavailable (proposal must not be created)
- spot → None (unleveraged)
- AI advisory stored separately, never drives final value
"""
from __future__ import annotations

import pytest

from aletheia.agent_shell.trading_decision import (
    TradingDecision,
    _resolve_post_debate_leverage,
    _LIQ_SAFETY_MARGIN,
    _CONVICTION_MAX_LEV,
)


# ── minimal helpers ────────────────────────────────────────────────────────────

class _FakeCfg:
    class market_data:
        ccxt_exchange = "bitget"


def _decision(
    *,
    leverage: int | None = 3,
    conviction: int = 70,
    symbol: str = "BTC/USDT",
    direction: str = "long",
    market_type: str = "usdt_futures",
    stop_price: float | None = 95_000.0,
) -> TradingDecision:
    d = TradingDecision(
        action="trade",
        symbol=symbol,
        direction=direction,
        conviction=conviction,
        reasoning="test",
        market_type=market_type,
        leverage=leverage,
        stop_price=stop_price,
    )
    return d


def _ctx(
    *,
    entry_price: float | None = 100_000.0,
    atr_pct: float | None = 2.0,
    account_lev_ratio: float | None = None,
    account_lev_cap: float | None = None,
    execution_mode: str = "paper_internal",
    symbol: str = "BTC/USDT",
    market_type: str = "usdt_futures",
) -> dict:
    pending = [{"symbol": symbol, "direction": "long", "market_type": market_type}]
    feature = {"symbol": symbol, "market_type": "perp", "atr_pct": atr_pct}
    entry_mark: dict = (
        {"status": "available", "price": entry_price, "symbol": symbol,
         "market_type": market_type}
        if entry_price is not None
        else {"status": "unavailable", "reason": "missing"}
    )
    futures: dict = {}
    if account_lev_ratio is not None:
        futures["account_leverage_ratio"] = account_lev_ratio
    if account_lev_cap is not None:
        futures["account_leverage_cap"] = account_lev_cap
    return {
        "execution_mode": execution_mode,
        "layer1": {
            "feature_index": {"k0": feature},
            "futures": futures,
        },
        "layer2": {
            "entry_mark": entry_mark,
            "pending_signals": pending,
        },
    }


def _resolve(d: TradingDecision, ctx: dict) -> int | None:
    """Invoke the resolver with a fake cfg that returns tier_cap=10."""
    from unittest.mock import patch

    with patch(
        "aletheia.trading.instruments.get_max_leverage",
        return_value=10,
    ):
        return _resolve_post_debate_leverage(d, d.market_type or "usdt_futures", ctx, _FakeCfg())


# ── tests ──────────────────────────────────────────────────────────────────────

def test_spot_returns_none():
    """Spot positions must never have leverage."""
    d = _decision(market_type="spot", leverage=None, stop_price=95_000.0)
    result = _resolve(d, _ctx(market_type="spot"))
    assert result is None
    assert d.leverage is None
    assert d.leverage_advisory is None


def test_advisory_stored_separately():
    """Original AI value must be in leverage_advisory; final is deterministic."""
    d = _decision(leverage=8, conviction=75)
    result = _resolve(d, _ctx())
    assert d.leverage_advisory == 8
    assert d.leverage == result
    # Deterministic final should NOT simply equal the advisory (it's constrained)
    assert isinstance(result, int)
    assert 1 <= result <= 10


def test_ai_advisory_does_not_drive_final():
    """Changing AI advisory (8x vs 1x) with identical context must give same final."""
    d1 = _decision(leverage=8)
    d2 = _decision(leverage=1)
    ctx = _ctx()
    r1 = _resolve(d1, ctx)
    # Reset state for d2 (stop_price same, conviction same)
    d2.sizing_trace = {}
    r2 = _resolve(d2, ctx)
    assert r1 == r2, "advisory should not influence deterministic output"


def test_missing_entry_price_raises():
    """No entry price → DecisionUnavailable; a futures proposal cannot be created."""
    from aletheia.agent_shell.trading_decision import DecisionUnavailable
    d = _decision()
    with pytest.raises(DecisionUnavailable, match="incomplete_entry_plan:entry_price"):
        _resolve(d, _ctx(entry_price=None))


def test_missing_stop_price_raises():
    """No stop price → DecisionUnavailable; all four plan fields are mandatory."""
    from aletheia.agent_shell.trading_decision import DecisionUnavailable
    d = _decision(stop_price=None)
    with pytest.raises(DecisionUnavailable, match="incomplete_entry_plan:stop_price"):
        _resolve(d, _ctx())


def test_wide_stop_constrains_to_low_leverage():
    """A very wide stop (20% away) must produce low leverage (≤4x at 80% safety)."""
    # stop 20% below entry → stop_pct=0.20 → max = floor(0.80/0.20) = 4
    d = _decision(stop_price=80_000.0)  # 20% below 100k
    result = _resolve(d, _ctx())
    assert result <= 4, f"expected ≤4x for 20% stop, got {result}"


def test_tight_stop_allows_higher_leverage():
    """A tight stop (1% away) permits higher leverage from the stop constraint."""
    # stop 1% below entry → stop_pct=0.01 → stop_max = floor(0.80/0.01) = 80, capped by conviction
    # At conviction=70: fraction=(70-55)/45=0.333, conviction_based=round(1+0.333*4)=round(2.33)=2
    d = _decision(stop_price=99_000.0, conviction=70)  # 1% below 100k
    result = _resolve(d, _ctx(atr_pct=None))  # no ATR constraint
    # The bottleneck is conviction_based=2, not stop constraint
    assert result >= 2, f"expected ≥2x for tight stop + conviction 70, got {result}"
    # With high conviction the stop constraint should allow much more
    d_high = _decision(stop_price=99_000.0, conviction=95)
    d_high.sizing_trace = {}
    result_high = _resolve(d_high, _ctx(atr_pct=None))
    assert result_high > result, "high conviction should yield more leverage than low"


def test_high_volatility_caps_leverage():
    """ATR 10% → atr_based = round(10/10) = 1 → at most 1x."""
    d = _decision(conviction=95)  # high conviction to isolate ATR effect
    result = _resolve(d, _ctx(atr_pct=10.0, entry_price=100_000.0))
    assert result == 1, f"expected 1x at ATR=10%, got {result}"


def test_low_account_headroom():
    """When account leverage is nearly at cap, new positions get 1x headroom cap."""
    # ratio=4.5, cap=5 → remaining=0.5 → int(0.5)=0 → but max(1,...) ensures 1x
    d = _decision()
    result = _resolve(d, _ctx(account_lev_ratio=4.5, account_lev_cap=5.0))
    assert result == 1, f"expected 1x at near-exhausted headroom, got {result}"


def test_account_headroom_exhausted_raises():
    """account_leverage_ratio ≥ cap → DecisionUnavailable; no room for a new position."""
    from aletheia.agent_shell.trading_decision import DecisionUnavailable
    d = _decision()
    with pytest.raises(DecisionUnavailable, match="account_headroom_exhausted"):
        _resolve(d, _ctx(account_lev_ratio=6.0, account_lev_cap=5.0))


def test_tier_cap_is_hard_ceiling():
    """Final leverage must never exceed the tier cap."""
    # Tight stop + high conviction + no volatility → stop_max and conviction high,
    # but tier_cap=10 is the ceiling.
    d = _decision(stop_price=99_900.0, conviction=100)  # 0.1% stop
    result = _resolve(d, _ctx(atr_pct=0.1))
    assert result <= 10


def test_post_debate_conviction_used():
    """Final leverage must reflect post-debate conviction, not pre-debate value."""
    # Simulate debate raising conviction from 50 to 90
    d_low = _decision(conviction=55)   # just above minimum
    d_high = _decision(conviction=90)  # high conviction

    # Both with the same stop/ATR context
    ctx = _ctx(atr_pct=None)  # remove ATR constraint to isolate conviction effect
    r_low = _resolve(d_low, ctx)
    d_high.sizing_trace = {}
    r_high = _resolve(d_high, ctx)
    assert r_high >= r_low, "higher post-debate conviction must yield ≥ leverage"


def test_resolver_inputs_in_sizing_trace():
    """Resolver must store all inputs for auditing."""
    d = _decision(leverage=5, conviction=72)
    _resolve(d, _ctx(atr_pct=2.5, account_lev_ratio=1.0, account_lev_cap=10.0))
    r = d.sizing_trace["leverage_resolver"]
    assert r["ai_advisory"] == 5
    assert r["conviction"] == 72
    assert r["stop_pct"] is not None
    assert r["atr_pct"] == 2.5
    assert r["reason"] == "deterministic_post_debate"
    assert r["final"] == d.leverage


def test_leverage_advisory_field_preserved_on_decision():
    """decision.leverage_advisory must survive the call."""
    d = _decision(leverage=7)
    _resolve(d, _ctx())
    assert d.leverage_advisory == 7
