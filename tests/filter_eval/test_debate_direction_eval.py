"""
Filter evaluation: direction-aware debate advantage formula.

Tests that the conviction adjustment correctly supports short theses
when bear > bull (i.e., the debate is bearish = good for shorts).

Run with:  uv run pytest tests/filter_eval/test_debate_direction_eval.py -v
"""
from __future__ import annotations

import pytest

from .scenarios.debate_direction import ALL_SCENARIOS, _compute_conviction

_MIN_CONVICTION_TO_TRADE = 55


@pytest.mark.parametrize("scenario", ALL_SCENARIOS, ids=lambda s: s.name)
def test_debate_direction_conviction(scenario):
    """Verify the direction-aware advantage formula produces the expected conviction."""
    actual = _compute_conviction(scenario.input)
    assert actual == scenario.expected_conviction, (
        f"Scenario '{scenario.name}': "
        f"expected conviction={scenario.expected_conviction}, got {actual}\n"
        f"  direction={scenario.input.direction!r}, "
        f"initial={scenario.input.initial_conviction}, "
        f"bull={scenario.input.bull_confidence}, bear={scenario.input.bear_confidence}"
    )


@pytest.mark.parametrize("scenario", ALL_SCENARIOS, ids=lambda s: s.name)
def test_debate_direction_skip_decision(scenario):
    """Verify skip/no-skip outcomes match expectations."""
    actual_conviction = _compute_conviction(scenario.input)
    actual_skip = actual_conviction < _MIN_CONVICTION_TO_TRADE
    assert actual_skip == scenario.expected_skip, (
        f"Scenario '{scenario.name}': "
        f"expected skip={scenario.expected_skip}, got skip={actual_skip}\n"
        f"  adjusted conviction={actual_conviction}, threshold={_MIN_CONVICTION_TO_TRADE}\n"
        f"  direction={scenario.input.direction!r}, "
        f"initial={scenario.input.initial_conviction}, "
        f"bull={scenario.input.bull_confidence}, bear={scenario.input.bear_confidence}"
    )
