"""
Scenarios for the direction-aware debate advantage formula.

The formula post-fix:
  - SHORT: advantage = bear_confidence - bull_confidence
  - LONG:  advantage = bull_confidence - bear_confidence
  adjusted = initial_conviction + advantage * 0.3

_MIN_CONVICTION_TO_TRADE = 55
_DEBATE_RANGE = (40, 80)
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class DebateInput:
    direction: Literal["long", "short"]
    initial_conviction: int
    bull_confidence: int
    bear_confidence: int


@dataclass
class DebateScenario:
    name: str
    description: str
    input: DebateInput
    expected_skip: bool          # True  → conviction should fall below 55 → skip
    expected_conviction: int     # exact adjusted conviction after formula


def _compute_conviction(inp: DebateInput) -> int:
    """Mirror the fixed formula from trading_decision.py."""
    if inp.direction == "short":
        advantage = inp.bear_confidence - inp.bull_confidence
    else:
        advantage = inp.bull_confidence - inp.bear_confidence
    adjusted = int(round(inp.initial_conviction + advantage * 0.3))
    return max(0, min(100, adjusted))


ALL_SCENARIOS: list[DebateScenario] = [
    DebateScenario(
        name="short_bearish_debate_should_not_skip",
        description=(
            "SHORT with bear=75, bull=25.  "
            "Bearish debate strongly supports the short thesis. "
            "advantage = 75-25=50, adjusted = 58+15=73 → must NOT skip."
        ),
        input=DebateInput(direction="short", initial_conviction=58, bull_confidence=25, bear_confidence=75),
        expected_skip=False,
        expected_conviction=73,
    ),
    DebateScenario(
        name="long_bearish_debate_should_skip",
        description=(
            "LONG with bear=75, bull=25.  "
            "Bearish debate opposes the long thesis. "
            "advantage = 25-75=-50, adjusted = 58-15=43 → must skip."
        ),
        input=DebateInput(direction="long", initial_conviction=58, bull_confidence=25, bear_confidence=75),
        expected_skip=True,
        expected_conviction=43,
    ),
    DebateScenario(
        name="short_bullish_debate_should_skip",
        description=(
            "SHORT with bear=25, bull=75.  "
            "Bullish debate opposes the short thesis. "
            "advantage = 25-75=-50, adjusted = 58-15=43 → must skip."
        ),
        input=DebateInput(direction="short", initial_conviction=58, bull_confidence=75, bear_confidence=25),
        expected_skip=True,
        expected_conviction=43,
    ),
    DebateScenario(
        name="long_bullish_debate_should_not_skip",
        description=(
            "LONG with bear=25, bull=75.  "
            "Bullish debate supports the long thesis. "
            "advantage = 75-25=50, adjusted = 58+15=73 → must NOT skip."
        ),
        input=DebateInput(direction="long", initial_conviction=58, bull_confidence=75, bear_confidence=25),
        expected_skip=False,
        expected_conviction=73,
    ),
    DebateScenario(
        name="short_neutral_debate_unchanged",
        description=(
            "SHORT with bear=50, bull=50.  "
            "Neutral debate: advantage=0, conviction stays at 60 → must NOT skip."
        ),
        input=DebateInput(direction="short", initial_conviction=60, bull_confidence=50, bear_confidence=50),
        expected_skip=False,
        expected_conviction=60,
    ),
]
