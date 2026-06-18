"""Declarative Rule DSL for executable trading strategies.

A RuleSet replaces the advisory natural-language entry_rule/exit_rule strings
in StrategySpec. It is machine-evaluable and serializes to JSON for storage
in strategy_records.parameters.

Design: declarative condition tree, NOT a Turing-complete language.
Every leaf is a comparison between two values (indicator vs literal, or
indicator vs indicator). Conditions compose with AND/OR. No loops, no state.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Literal


class Comparator(str, Enum):
    LT = "lt"
    GT = "gt"
    LTE = "lte"
    GTE = "gte"
    CROSS_ABOVE = "cross_above"  # prev bar below, current bar above
    CROSS_BELOW = "cross_below"  # prev bar above, current bar below


@dataclass
class IndicatorRef:
    """Reference to an indicator or price field.

    name options:
        price fields:  "close", "open", "high", "low", "volume"
        indicators:    "sma", "ema", "rsi", "atr",
                       "bollinger_upper", "bollinger_lower", "bollinger_mid",
                       "momentum_pct", "zscore"
    params: indicator parameters, e.g. {"period": 14} or {"period": 20, "k": 2.0}
    """
    name: str
    params: dict[str, int | float] = field(default_factory=dict)


@dataclass
class Condition:
    """Single predicate: left COMPARATOR right.

    right may be a float literal or another IndicatorRef
    (e.g., close > sma(50)).

    timeframe: optional override (e.g., "4h") for multi-timeframe conditions.
    When set, this condition evaluates against bars from that timeframe
    instead of the strategy's primary timeframe.
    """
    left: IndicatorRef
    comparator: Comparator
    right: float | IndicatorRef
    timeframe: str | None = None


@dataclass
class RuleExpr:
    """Boolean expression: AND/OR tree of Condition or nested RuleExpr."""
    op: Literal["and", "or"] = "and"
    conditions: list[Condition | RuleExpr] = field(default_factory=list)


@dataclass
class StopLoss:
    """Stop-loss specification."""
    mode: Literal["fixed_pct", "atr_multiple"]
    value: float           # pct (e.g. 2.0 = 2%) or ATR multiplier (e.g. 1.5)
    atr_period: int = 14


@dataclass
class TakeProfit:
    """Take-profit specification."""
    mode: Literal["fixed_pct", "atr_multiple"]
    value: float
    atr_period: int = 14


@dataclass
class RuleSet:
    """Complete executable strategy rules.

    Serializable to/from JSON. Intended to be stored in
    strategy_records.parameters as {"ruleset": <json>}.
    """
    entry: RuleExpr
    exit: RuleExpr
    stop_loss: StopLoss | None = None
    take_profit: TakeProfit | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, s: str) -> RuleSet:
        return _ruleset_from_dict(json.loads(s))


# ── deserialization helpers ──────────────────────────────────────────────────

def _indicator_ref_from_dict(d: dict) -> IndicatorRef:
    return IndicatorRef(name=d["name"], params=d.get("params") or {})


def _condition_from_dict(d: dict) -> Condition:
    left = _indicator_ref_from_dict(d["left"])
    raw_right = d["right"]
    right: float | IndicatorRef = (
        _indicator_ref_from_dict(raw_right)
        if isinstance(raw_right, dict)
        else float(raw_right)
    )
    return Condition(left=left, comparator=Comparator(d["comparator"]), right=right,
                     timeframe=d.get("timeframe"))


def _rule_expr_from_dict(d: dict) -> RuleExpr:
    children: list[Condition | RuleExpr] = []
    for c in d.get("conditions", []):
        if "op" in c:
            children.append(_rule_expr_from_dict(c))
        else:
            children.append(_condition_from_dict(c))
    return RuleExpr(op=d.get("op", "and"), conditions=children)


def _stop_from_dict(d: dict | None) -> StopLoss | None:
    if not d:
        return None
    return StopLoss(mode=d["mode"], value=d["value"], atr_period=d.get("atr_period", 14))


def _tp_from_dict(d: dict | None) -> TakeProfit | None:
    if not d:
        return None
    return TakeProfit(mode=d["mode"], value=d["value"], atr_period=d.get("atr_period", 14))


def _ruleset_from_dict(d: dict) -> RuleSet:
    return RuleSet(
        entry=_rule_expr_from_dict(d["entry"]),
        exit=_rule_expr_from_dict(d["exit"]),
        stop_loss=_stop_from_dict(d.get("stop_loss")),
        take_profit=_tp_from_dict(d.get("take_profit")),
    )
