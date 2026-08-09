"""Tests for DecisionUnavailable retry limit.

A candidate with conviction below the trade threshold should not be retried
indefinitely. After _MAX_DEFERRALS_BEFORE_TERMINAL defers, the candidate
must be converted to a terminal skip (opportunity marked 'reviewed') and
its valid_until must NOT be extended further.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

import aletheia.agent_shell.commands as cmd


def _make_ctx(opportunity_id="opp_1", symbol="BTC/USDT", direction="long",
              market_type="usdt_futures"):
    """Build a minimal context for defer recording tests."""
    now = datetime.now(timezone.utc)
    return {
        "layer2": {
            "pending_queue": {
                "unresolved_total": 1,
                "visible_count": 1,
                "overflow_count": 0,
                "oldest_at": now.isoformat(),
            },
            "pending_signals": [
                {
                    "opportunity_id": opportunity_id,
                    "symbol": symbol,
                    "direction": direction,
                    "market_type": market_type,
                    "direction_bias": direction,
                    "market": market_type,
                }
            ],
        },
        "skip_policy": {
            "unchanged_context_defer_window_minutes": 120,
        },
        "policy_epoch_id": "epoch_test_1",
    }


_REPO = "aletheia.storage.repositories"


def test_defer_count_increments():
    """Defer count for a symbol+direction must accumulate across calls."""
    ctx = _make_ctx()

    with patch(f"{_REPO}.extend_opportunity_valid_until_forward_only", return_value=1), \
         patch(f"{_REPO}.record_operator_evaluation_event") as mock_event, \
         patch(f"{_REPO}.transaction"):
        mock_event.return_value = MagicMock(id=1)

        # Record 3 defers (below threshold — should not trigger terminal skip)
        for i in range(3):
            result = cmd._record_deferred_context_observation(
                ctx,
                context_fingerprint=f"fp_test_{i}",
                opportunity_id="opp_1",
                policy_epoch_id="epoch_test_1",
                trigger="test",
                reason="operator_low_conviction",
                window_minutes=120,
                event_kind="operator_decision_deferred_unchanged_context",
            )
            # Below threshold: should return a defer record, not terminal skip
            assert result.get("status") != "terminal_skip"

        # 3 events recorded
        assert mock_event.call_count == 3


def test_terminal_skip_after_max_deferrals():
    """After _MAX_DEFERRALS_BEFORE_TERMINAL defers, opportunity must be marked reviewed."""
    ctx = _make_ctx()

    with patch.object(cmd, "_count_recent_defers_for_candidate", return_value=cmd._MAX_DEFERRALS_BEFORE_TERMINAL), \
         patch(f"{_REPO}.extend_opportunity_valid_until_forward_only", return_value=1), \
         patch(f"{_REPO}.record_operator_evaluation_event") as mock_event, \
         patch(f"{_REPO}.transition_opportunity_to_reviewed") as mock_reviewed, \
         patch(f"{_REPO}.transaction"):
        mock_event.return_value = MagicMock(id=1)

        result = cmd._record_deferred_context_observation(
            ctx,
            context_fingerprint="fp_exceed",
            opportunity_id="opp_1",
            policy_epoch_id="epoch_test_1",
            trigger="test",
            reason="operator_low_conviction",
            window_minutes=120,
            event_kind="operator_decision_deferred_unchanged_context",
        )

        # Should trigger terminal skip
        assert result["status"] == "terminal_skip"
        assert result["reason"] == "retry_exhausted"
        # Opportunity should be marked reviewed
        mock_reviewed.assert_called_once_with("opp_1")


def test_valid_until_not_extended_after_exhaustion():
    """After defer exhaustion, valid_until must NOT be extended."""
    ctx = _make_ctx()

    with patch.object(cmd, "_count_recent_defers_for_candidate", return_value=cmd._MAX_DEFERRALS_BEFORE_TERMINAL + 1), \
         patch(f"{_REPO}.extend_opportunity_valid_until_forward_only") as mock_extend, \
         patch(f"{_REPO}.record_operator_evaluation_event") as mock_event, \
         patch(f"{_REPO}.transition_opportunity_to_reviewed"), \
         patch(f"{_REPO}.transaction"):
        mock_event.return_value = MagicMock(id=1)

        result = cmd._record_deferred_context_observation(
            ctx,
            context_fingerprint="fp_exhausted",
            opportunity_id="opp_1",
            policy_epoch_id="epoch_test_1",
            trigger="test",
            reason="operator_low_conviction",
            window_minutes=120,
            event_kind="operator_decision_deferred_unchanged_context",
        )

        # valid_until should NOT be extended after exhaustion
        mock_extend.assert_not_called()
        assert result["status"] == "terminal_skip"


def test_non_counting_defer_reason_unaffected():
    """Defer reasons not in _RETRY_LIMITED_DEFER_REASONS must not trigger limit."""
    ctx = _make_ctx()

    with patch.object(cmd, "_count_recent_defers_for_candidate", return_value=999), \
         patch(f"{_REPO}.extend_opportunity_valid_until_forward_only", return_value=1), \
         patch(f"{_REPO}.record_operator_evaluation_event") as mock_event, \
         patch(f"{_REPO}.transition_opportunity_to_reviewed") as mock_reviewed, \
         patch(f"{_REPO}.transaction"):
        mock_event.return_value = MagicMock(id=1)

        result = cmd._record_deferred_context_observation(
            ctx,
            context_fingerprint="fp_data_missing",
            opportunity_id="opp_1",
            policy_epoch_id="epoch_test_1",
            trigger="test",
            reason="missing_data",  # not in _RETRY_LIMITED_DEFER_REASONS
            window_minutes=120,
            event_kind="operator_decision_deferred_unchanged_context",
        )

        # Should NOT trigger terminal skip for non-counting reasons
        assert result.get("status") != "terminal_skip"
        mock_reviewed.assert_not_called()


def test_max_deferrals_constant_exists():
    """The max deferrals constant must be defined and reasonable."""
    assert hasattr(cmd, "_MAX_DEFERRALS_BEFORE_TERMINAL")
    assert 3 <= cmd._MAX_DEFERRALS_BEFORE_TERMINAL <= 10
