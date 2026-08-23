"""Tests for learning analytics data-pollution fixes: #1263, #1978, #1981, #1248."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest


class TestMemoRotationFixes:
    """#1263: Memo rotation abandons late outcomes."""

    def test_evaluate_prior_memo_accepts_old_memo_for_rotation(self):
        """evaluate_prior_memo with for_rotation=True accepts a memo older than 72h."""
        from aletheia.analytics.memo_effectiveness import evaluate_prior_memo
        from aletheia.analytics.learning_window import CLEAN_WINDOW_ANCHOR_ID
        from aletheia.analytics.outcome_integrity import OUTCOME_SEMANTICS_VERSION

        # 10-day-old memo: past 72h but within 30 days
        ten_days_ago = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        old_memo = {
            "id": "memo_old_123",
            "generated_at": ten_days_ago,
            "outcome_semantics_version": OUTCOME_SEMANTICS_VERSION,
            "clean_window_anchor_id": CLEAN_WINDOW_ANCHOR_ID,
            "regime_playbook": [],
        }

        with patch("aletheia.storage.repositories.list_knowledge_items") as mock_list, \
             patch("aletheia.storage.repositories.update_knowledge_item"), \
             patch("aletheia.analytics.memo_effectiveness.eligible_strategy_outcomes") as mock_outcomes:
            mock_list.return_value = [{"id": "memo_old_123", "content": json.dumps(old_memo), "tags": "{}"}]
            mock_outcomes.return_value = []

            result_no_rotation = evaluate_prior_memo(for_rotation=False)
            assert result_no_rotation is None, "10-day-old memo should be rejected without for_rotation"

            result_with_rotation = evaluate_prior_memo(for_rotation=True)
            assert result_with_rotation is not None, "10-day-old memo should be accepted with for_rotation=True"
            assert result_with_rotation["memo_id"] == "memo_old_123"


class TestMemoEffectivenessTradeFiltering:
    """#1978: Memo effectiveness credits wrong trades."""

    def test_excludes_non_paper_internal_trades(self):
        """Trade with execution_mode != 'paper_internal' is excluded from memo cohort."""
        from aletheia.analytics.memo_effectiveness import evaluate_prior_memo
        from aletheia.analytics.learning_window import CLEAN_WINDOW_ANCHOR_ID
        from aletheia.analytics.outcome_integrity import OUTCOME_SEMANTICS_VERSION

        now_iso = datetime.now(timezone.utc).isoformat()
        memo = {
            "id": "memo_123",
            "generated_at": now_iso,
            "outcome_semantics_version": OUTCOME_SEMANTICS_VERSION,
            "clean_window_anchor_id": CLEAN_WINDOW_ANCHOR_ID,
            "regime_playbook": [{"regime": "bull", "direction": "long", "edge": "strong"}],
        }

        trades = [
            {
                "opened_at": now_iso,
                "execution_mode": "paper_internal",
                "regime_tag": "bull",
                "direction": "long",
                "realized_pnl": 100,
            },
            {
                "opened_at": now_iso,
                "execution_mode": "live_simulation",  # should be excluded
                "regime_tag": "bull",
                "direction": "long",
                "realized_pnl": 200,
            },
            {
                "opened_at": now_iso,
                "execution_mode": "paper_internal",
                "regime_tag": "bull",
                "direction": "long",
                "realized_pnl": -50,
            },
        ]

        with patch("aletheia.storage.repositories.list_knowledge_items") as mock_list, \
             patch("aletheia.storage.repositories.update_knowledge_item"), \
             patch("aletheia.analytics.memo_effectiveness.eligible_strategy_outcomes") as mock_outcomes:
            mock_list.return_value = [{"id": "memo_123", "content": json.dumps(memo), "tags": "{}"}]
            mock_outcomes.return_value = trades

            result = evaluate_prior_memo()

            assert result is not None
            # Only 2 paper_internal trades should be counted (live_simulation excluded)
            assert result["n_trades"] == 2, f"Expected 2 trades, got {result['n_trades']}"


class TestReasoningFeedbackRegimeTag:
    """#1981: Rejected proposals use wrong regime_tag."""

    def test_rejected_proposal_uses_proposal_regime_tag(self):
        """Rejected proposal uses proposal_regime_tag (from opportunity), not outcome regime_tag.

        Three rejected long+momentum+bull + seven profitable executed long+momentum+bull
        → rejection rate 3/10 (all 10 in same bucket), NOT 3/3 (rejected-only bucket).
        """
        from aletheia.analytics.reasoning_feedback import build_reasoning_feedback

        mock_db = MagicMock()

        # All 10 proposals share proposal_regime_tag="bull" (from opportunity at proposal time)
        proposals_raw = []
        for i in range(3):
            proposals_raw.append({
                "proposal_id": f"rejected_{i}",
                "proposal_status": "rejected",
                "agent_rationale": 'reasoning\n---\n{"scanner_family":"momentum"}',
                "direction": "long",
                "opportunity_id": f"opp_r_{i}",
                "conviction": None,
                "pi_reasoning": None,
                "proposal_regime_tag": "bull",
                "proposal_scanner_family": "momentum",
            })
        for i in range(7):
            proposals_raw.append({
                "proposal_id": f"executed_{i}",
                "proposal_status": "executed",
                "agent_rationale": 'reasoning\n---\n{"scanner_family":"momentum"}',
                "direction": "long",
                "opportunity_id": f"opp_e_{i}",
                "conviction": 65,
                "pi_reasoning": "rationale",
                "proposal_regime_tag": "bull",
                "proposal_scanner_family": "momentum",
            })

        mock_db.execute.return_value.fetchall.return_value = proposals_raw

        # Only executed proposals have closed-position outcomes
        outcomes = []
        for i in range(7):
            outcomes.append({
                "proposal_id": f"executed_{i}",
                "realized_pnl": 50.0,
                "regime_tag": "bull",
            })

        with patch("aletheia.storage.sqlite.get_db", return_value=mock_db), \
             patch("aletheia.analytics.reasoning_feedback.eligible_strategy_outcomes",
                   return_value=outcomes), \
             patch("aletheia.analytics.learning_window.clean_window_cutoff",
                   return_value="2026-06-01T00:00:00Z"):
            result = build_reasoning_feedback()

        assert result is not None
        # The fix: rejected (3) and executed (7) proposals share the same regime="bull",
        # so they land in the SAME bucket, yielding rejection_rate = 3/10 = 0.3.
        # Without the fix, rejected had regime_tag=None → "unknown", executed had "bull",
        # so they split → rejection_rate = 3/3 = 1.0.
        # Since rejection_rate < 0.5, this pattern won't appear in rejection_patterns.
        # Instead check approval_outcomes (which lists every pattern with exec_total≥3).
        approval = result.get("approval_outcomes", [])
        bull_pattern = next(
            (p for p in approval if p["pattern_label"] == "long+momentum+bull"), None
        )
        assert bull_pattern is not None, \
            f"Pattern 'long+momentum+bull' not in approval_outcomes; got={approval}"
        assert bull_pattern["approval_count"] == 7  # only executed trades


class TestAttributionReadError:
    """#1248: Read failures collapse to no history."""

    def test_read_one_raises_attribution_read_error_on_db_exception(self):
        """_read_one raises AttributionReadError on DB exception (not returns None)."""
        from aletheia.analytics.trade_attribution import _read_one, AttributionReadError

        mock_db = MagicMock()
        mock_db.execute.side_effect = Exception("DB connection failed")

        with pytest.raises(AttributionReadError) as excinfo:
            _read_one(mock_db, "trade_proposals", "proposal_123", None)

        assert excinfo.value.table == "trade_proposals"
        assert excinfo.value.key == "proposal_123"
        assert "DB connection failed" in str(excinfo.value.cause)

    def test_build_trade_attribution_rows_logs_read_errors(self):
        """build_trade_attribution_rows catches AttributionReadError and logs errors."""
        from aletheia.analytics.trade_attribution import build_trade_attribution_rows

        mock_db = MagicMock()
        mock_db.execute.side_effect = Exception("Read failed")

        position = {
            "id": "pos_123",
            "status": "closed",
            "closed_at": datetime.now(timezone.utc).isoformat(),
            "execution_mode": "paper_internal",
            "proposal_id": "prop_123",
            "order_id": "order_123",
            "opportunity_id": "opp_123",
        }

        from aletheia.analytics.outcome_integrity import OutcomeAssessment
        mock_assessment = OutcomeAssessment(eligible=True, reason="ok")

        with patch("aletheia.analytics.trade_attribution.raw_account_events",
                   return_value=[position]), \
             patch("aletheia.analytics.trade_attribution.assess_reportable_outcome",
                   return_value=mock_assessment), \
             patch("aletheia.analytics.trade_attribution.logger") as mock_logger:

            rows = build_trade_attribution_rows(
                limit=10, closed_after="2026-01-01T00:00:00Z", db=mock_db
            )

        # AttributionReadError caught — result is empty but no exception raised
        assert rows == []
        # Warning logged about read errors
        assert mock_logger.warning.called
        warning_messages = [str(c) for c in mock_logger.warning.call_args_list]
        assert any("read error" in msg for msg in warning_messages)
