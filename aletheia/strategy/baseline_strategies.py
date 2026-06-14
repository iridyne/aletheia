"""Three baseline strategies expressed in the Rule DSL.

Strategy 0 — Pipeline test: always-true entry/exit (validates the machinery).
Strategy 1 — BTC trend following (SMA50 + RSI).
Strategy 2 — ETH mean reversion (RSI oversold + SMA200 trend filter).
"""
from __future__ import annotations

from aletheia.strategy.dsl import (
    Comparator, Condition, IndicatorRef, RuleExpr, RuleSet, StopLoss,
)


def strategy_0_pipeline_test() -> tuple[str, str, str, str, RuleSet]:
    """Strategy 0: entry on every bar, exit on every bar.

    Close > 0 is always true. This produces a new trade every two bars and
    exists only to validate the full pipeline end-to-end. Not meant to be
    profitable.

    Returns: (name, symbol, timeframe, direction, ruleset)
    """
    always_true = RuleExpr(op="and", conditions=[
        Condition(
            left=IndicatorRef(name="close"),
            comparator=Comparator.GT,
            right=0.0,
        )
    ])
    return (
        "Pipeline Test — buy/sell every bar",
        "BTC/USDT",
        "1h",
        "long",
        RuleSet(entry=always_true, exit=always_true),
    )


def strategy_1_btc_trend_following() -> tuple[str, str, str, str, RuleSet]:
    """Strategy 1: BTC 1h trend following.

    Entry:  close > SMA(50)  AND  RSI(14) < 70
    Exit:   close < SMA(50)  OR   RSI(14) > 80
    Stop:   1.5 × ATR(14) below entry
    """
    entry = RuleExpr(op="and", conditions=[
        Condition(
            left=IndicatorRef(name="close"),
            comparator=Comparator.GT,
            right=IndicatorRef(name="sma", params={"period": 50}),
        ),
        Condition(
            left=IndicatorRef(name="rsi", params={"period": 14}),
            comparator=Comparator.LT,
            right=70.0,
        ),
    ])
    exit_rule = RuleExpr(op="or", conditions=[
        Condition(
            left=IndicatorRef(name="close"),
            comparator=Comparator.LT,
            right=IndicatorRef(name="sma", params={"period": 50}),
        ),
        Condition(
            left=IndicatorRef(name="rsi", params={"period": 14}),
            comparator=Comparator.GT,
            right=80.0,
        ),
    ])
    stop = StopLoss(mode="atr_multiple", value=1.5, atr_period=14)
    return (
        "BTC Trend Following — SMA50 + RSI",
        "BTC/USDT",
        "1h",
        "long",
        RuleSet(entry=entry, exit=exit_rule, stop_loss=stop),
    )


def strategy_2_eth_mean_reversion() -> tuple[str, str, str, str, RuleSet]:
    """Strategy 2: ETH 1h mean reversion.

    Entry:  RSI(14) < 30  AND  close > SMA(200)
    Exit:   RSI(14) > 50
    Stop:   3% fixed below entry

    SMA(200) requires ≥200 bars warmup; the evaluator returns None (no signal)
    until enough data is available — no look-ahead, no special-casing.
    """
    entry = RuleExpr(op="and", conditions=[
        Condition(
            left=IndicatorRef(name="rsi", params={"period": 14}),
            comparator=Comparator.LT,
            right=30.0,
        ),
        Condition(
            left=IndicatorRef(name="close"),
            comparator=Comparator.GT,
            right=IndicatorRef(name="sma", params={"period": 200}),
        ),
    ])
    exit_rule = RuleExpr(op="and", conditions=[
        Condition(
            left=IndicatorRef(name="rsi", params={"period": 14}),
            comparator=Comparator.GT,
            right=50.0,
        ),
    ])
    stop = StopLoss(mode="fixed_pct", value=3.0)
    return (
        "ETH Mean Reversion — RSI oversold + SMA200",
        "ETH/USDT",
        "1h",
        "long",
        RuleSet(entry=entry, exit=exit_rule, stop_loss=stop),
    )


ALL_BASELINES = [
    strategy_0_pipeline_test,
    strategy_1_btc_trend_following,
    strategy_2_eth_mean_reversion,
]
