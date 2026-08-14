"""Tests for aletheia.analytics.defer_classifier.

Covers every defer_reason string that the current codebase can emit, plus the
ordering rule that ``direct_skip_not_confirmed:candidate_changed`` classifies
as ``candidate_specific`` and NOT as ``unknown`` or ``market_quiet``.
"""

from __future__ import annotations

import pytest

from aletheia.analytics.defer_classifier import classify_defer_reason


class TestMarketQuietReasons:
    """Reasons that indicate the Operator saw the setup and hedged."""

    def test_operator_low_conviction(self):
        assert classify_defer_reason("operator_low_conviction") == "market_quiet"

    def test_debate_not_confirmed(self):
        assert classify_defer_reason("debate_not_confirmed") == "market_quiet"

    @pytest.mark.parametrize(
        "adverse_code",
        [
            "signal_conflict",
            "regime_mismatch",
            "adverse_price_structure",
            "liquidity_or_execution_quality",
            "negative_same_cohort_outcomes",
            "invalid_risk_reward",
            "candidate_data_integrity",
        ],
    )
    def test_unverified_adverse_reason_variants(self, adverse_code):
        code = f"direct_skip_not_confirmed:unverified_adverse_reason:{adverse_code}"
        assert classify_defer_reason(code) == "market_quiet"


class TestCandidateSpecificReasons:
    """Reasons that indicate data-plumbing / per-candidate state."""

    @pytest.mark.parametrize(
        "code",
        [
            "candidate_technical_snapshot_unavailable",
            "candidate_technical_snapshot_incomplete",
            "candidate_technical_snapshot_stale",
            "candidate_entry_mark_unavailable",
            "candidate_entry_mark_identity_unavailable",
            "candidate_entry_mark_identity_mismatch",
        ],
    )
    def test_snapshot_variants(self, code):
        assert classify_defer_reason(code) == "candidate_specific"

    def test_candidate_changed_ordering(self):
        """candidate_changed MUST resolve to candidate_specific.

        The generic direct_skip_not_confirmed rule sorts it as market_quiet
        only when the suffix starts with unverified_adverse_reason. If rule
        ordering ever regresses (specific-suffix test moved below the generic
        prefix test), this test catches it.
        """
        assert (
            classify_defer_reason("direct_skip_not_confirmed:candidate_changed")
            == "candidate_specific"
        )


class TestUnknownFallback:
    """Anything unrecognised falls back to unknown, never raises."""

    @pytest.mark.parametrize("value", [None, "", "   ", "\t\n"])
    def test_empty_or_none(self, value):
        assert classify_defer_reason(value) == "unknown"

    def test_direct_skip_with_arbitrary_invalid_reason(self):
        # Untyped invalid_skip_reason surfaced by _direct_skip_validation_reason
        # goes into unknown, not market_quiet — we deliberately do not treat
        # a bare direct_skip_not_confirmed:* as a market hedge.
        assert (
            classify_defer_reason("direct_skip_not_confirmed:missing_opportunity_id")
            == "unknown"
        )

    def test_completely_novel_reason(self):
        assert classify_defer_reason("brand_new_reason_from_a_future_epoch") == "unknown"

    def test_bare_direct_skip_no_suffix(self):
        # No colon separator — treat as unknown (no suffix to route by).
        assert classify_defer_reason("direct_skip_not_confirmed") == "unknown"

    def test_direct_skip_empty_suffix(self):
        # Colon present but suffix empty.
        assert classify_defer_reason("direct_skip_not_confirmed:") == "unknown"


class TestPurity:
    """No I/O, no state, no crashes on weird input."""

    def test_never_raises_on_random_types_via_none(self):
        assert classify_defer_reason(None) == "unknown"

    def test_leading_and_trailing_whitespace_stripped(self):
        assert classify_defer_reason("  operator_low_conviction  ") == "market_quiet"
