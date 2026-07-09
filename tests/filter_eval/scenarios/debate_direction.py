"""
Scenarios for the debate advantage formula.

The formula (both LONG and SHORT use the same sign — prompts are direction-aware):
  advantage = bull_confidence - bear_confidence
  adjusted  = initial_conviction + advantage * 0.3

Where:
  bull_confidence = P(this trade succeeds)  [prompt: "make the case FOR {symbol} {direction}"]
  bear_confidence = P(this trade fails)     [prompt: "why {direction} is a BAD idea"]

So bull - bear > 0 means the debate thinks the trade will likely succeed, regardless of
whether the direction is long or short. The prompts carry the direction context.

_MIN_CONVICTION_TO_TRADE = 55
_DEBATE_RANGE = (40, 80)
"""
from __future__ import annotations
from dataclasses import dataclass
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
    expected_skip: bool      # True → conviction should fall below 55 → skip
    expected_conviction: int  # exact adjusted conviction after formula


def _compute_conviction(inp: DebateInput) -> int:
    """Mirror the formula from trading_decision.py (direction-agnostic sign)."""
    advantage = inp.bull_confidence - inp.bear_confidence
    adjusted = int(round(inp.initial_conviction + advantage * 0.3))
    return max(0, min(100, adjusted))


ALL_SCENARIOS: list[DebateScenario] = [
    DebateScenario(
        name="short_bearish_debate_skips",
        description=(
            "SHORT with bear=75 (trade will fail), bull=25 (trade succeeds unlikely). "
            "advantage = 25-75 = -50, adjusted = 58-15 = 43 → SKIP. "
            "The LLM debates the SHORT and concludes it has low chance of success."
        ),
        input=DebateInput(direction="short", initial_conviction=58, bull_confidence=25, bear_confidence=75),
        expected_skip=True,
        expected_conviction=43,
    ),
    DebateScenario(
        name="short_bullish_debate_passes",
        description=(
            "SHORT with bear=25 (trade will NOT fail), bull=75 (trade likely succeeds). "
            "advantage = 75-25 = 50, adjusted = 58+15 = 73 → PASS. "
            "The LLM debates the SHORT and is confident it will succeed."
        ),
        input=DebateInput(direction="short", initial_conviction=58, bull_confidence=75, bear_confidence=25),
        expected_skip=False,
        expected_conviction=73,
    ),
    DebateScenario(
        name="long_bearish_debate_skips",
        description=(
            "LONG with bear=75 (trade will fail), bull=25. "
            "advantage = -50, adjusted = 43 → SKIP."
        ),
        input=DebateInput(direction="long", initial_conviction=58, bull_confidence=25, bear_confidence=75),
        expected_skip=True,
        expected_conviction=43,
    ),
    DebateScenario(
        name="long_bullish_debate_passes",
        description=(
            "LONG with bull=75, bear=25. "
            "advantage = 50, adjusted = 73 → PASS."
        ),
        input=DebateInput(direction="long", initial_conviction=58, bull_confidence=75, bear_confidence=25),
        expected_skip=False,
        expected_conviction=73,
    ),
    DebateScenario(
        name="neutral_debate_unchanged",
        description=(
            "bull=50, bear=50 → advantage=0, conviction stays at 60 → PASS."
        ),
        input=DebateInput(direction="short", initial_conviction=60, bull_confidence=50, bear_confidence=50),
        expected_skip=False,
        expected_conviction=60,
    ),
]
