"""
Filter evaluation: _proposal_direction_overheated (direction diversity brake).

Run with:  uv run pytest tests/filter_eval/test_direction_brake_eval.py -v

Note: the mock_proposals_db fixture is defined in tests/filter_eval/conftest.py
and is discovered automatically by pytest via standard fixture discovery.
"""
from __future__ import annotations

import pytest

from aletheia.agent_shell.trading_decision import _proposal_direction_overheated
from .scenarios.direction_brake import ALL_SCENARIOS


@pytest.mark.parametrize("scenario", ALL_SCENARIOS, ids=lambda s: s.name)
def test_direction_brake_scenario(scenario, mock_proposals_db):
    """Each synthetic scenario asserts the brake blocks or passes as expected."""
    mock_proposals_db(scenario)

    for record, expected_blocked in zip(scenario.incoming, scenario.expected_blocked):
        result = _proposal_direction_overheated(record.direction, record.conviction)
        actual_blocked = result is not None

        assert actual_blocked == expected_blocked, (
            f"Scenario '{scenario.name}': "
            f"expected blocked={expected_blocked}, got blocked={actual_blocked}\n"
            f"  direction={record.direction!r}, conviction={record.conviction}\n"
            f"  history_count={len(scenario.history)}, "
            f"history_dirs={[p.direction for p in scenario.history]}\n"
            f"  brake_reason={result!r}"
        )


def test_direction_brake_params():
    """Verify the current default params are within the intended relaxed range."""
    import inspect
    sig = inspect.signature(_proposal_direction_overheated)
    params = sig.parameters

    assert params["min_sample"].default >= 10, (
        f"min_sample={params['min_sample'].default} is too low — should be >= 10 "
        "to avoid false positives in trending markets"
    )
    assert params["max_share"].default >= 0.90, (
        f"max_share={params['max_share'].default} is too strict — should be >= 0.90 "
        "to allow directional regimes"
    )
    assert params["override_conviction"].default <= 80, (
        f"override_conviction={params['override_conviction'].default} is too high — "
        "should be <= 80 so normal high-conviction trades can bypass the brake"
    )
