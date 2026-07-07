"""Tests for the knowledge reference router."""

from unittest.mock import patch, MagicMock

from aletheia.knowledge.router import (
    build_advisory_context,
    retrieve_for_role,
    _build_guard_advisory,
    _ITEM_TYPES_BY_ROLE,
)


class TestRetrieveForRole:
    @patch("aletheia.knowledge.router.retrieve_lessons")
    def test_uses_role_item_types(self, mock_retrieve):
        # #177 fix: dead role entries (strategy_designer/equity_observer/paper_trader)
        # were removed from _ITEM_TYPES_BY_ROLE. Exercise the sole live role.
        mock_retrieve.return_value = []
        retrieve_for_role("market_observer", "entry exit")
        mock_retrieve.assert_called_once_with(
            "entry exit", k=3,
            item_types=_ITEM_TYPES_BY_ROLE["market_observer"],
        )

    @patch("aletheia.knowledge.router.retrieve_lessons")
    def test_unknown_role_defaults(self, mock_retrieve):
        mock_retrieve.return_value = []
        retrieve_for_role("unknown_agent", "test query")
        call_args = mock_retrieve.call_args
        assert "trade_case_study" in call_args.kwargs["item_types"]

    @patch("aletheia.knowledge.router.retrieve_lessons")
    def test_returns_empty_on_error(self, mock_retrieve):
        mock_retrieve.side_effect = RuntimeError("db broken")
        result = retrieve_for_role("market_observer", "query")
        assert result == []

    def test_no_role_retrieves_post_trade_review(self):
        """Anti-hallucination lock (LEARNING_LOOP_AUDIT §B1 symmetry): post_trade_review
        is the hallucination-prone source. B1 removed it from the ranker; it must also
        never enter any role's advisory retrieval via build_advisory_context. Re-adding
        it to any role mapping is a regression."""
        for role, item_types in _ITEM_TYPES_BY_ROLE.items():
            assert "post_trade_review" not in item_types, (
                f"role {role!r} must not retrieve post_trade_review lessons — it floods "
                f"the advisory pool with hallucination-prone reviews (audit B1/C1)"
            )

    @patch("aletheia.knowledge.router.retrieve_lessons")
    def test_default_fallback_excludes_post_trade_review(self, mock_retrieve):
        mock_retrieve.return_value = []
        retrieve_for_role("unknown_agent", "q")
        assert "post_trade_review" not in mock_retrieve.call_args.kwargs["item_types"]


class TestBuildGuardAdvisory:
    @patch("aletheia.knowledge.router.evaluate_guards")
    def test_returns_empty_when_no_hits(self, mock_eval):
        from aletheia.knowledge.guards import GuardEvaluation
        mock_eval.return_value = GuardEvaluation(blocked=False, total_penalty=0, hits=[])
        assert _build_guard_advisory({"symbol": "BTC/USDT"}) == ""

    @patch("aletheia.knowledge.router.evaluate_guards")
    def test_returns_block_on_hits(self, mock_eval):
        from aletheia.knowledge.guards import GuardEvaluation, GuardHit
        mock_eval.return_value = GuardEvaluation(
            blocked=False, total_penalty=15.0,
            hits=[GuardHit(
                guard_id="test", title="Test Guard", enforcement="scoring",
                severity="warning", score_penalty=15.0, reason="matched",
            )],
        )
        result = _build_guard_advisory({"symbol": "BTC/USDT"})
        assert "ADVISORY" in result
        assert "test" in result

    @patch("aletheia.knowledge.router.evaluate_guards")
    def test_returns_empty_on_error(self, mock_eval):
        mock_eval.side_effect = RuntimeError("broken")
        assert _build_guard_advisory({"symbol": "BTC/USDT"}) == ""


class TestBuildAdvisoryContext:
    @patch("aletheia.knowledge.router._build_guard_advisory")
    @patch("aletheia.knowledge.router._retrieve_lessons_safe")
    def test_combines_lessons_and_guards(self, mock_lessons, mock_guards):
        mock_lessons.return_value = "## Lessons block"
        mock_guards.return_value = "## Guard block"
        result = build_advisory_context(
            "market_observer", "query",
            guard_context={"symbol": "BTC/USDT"},
        )
        assert "Lessons block" in result
        assert "Guard block" in result

    @patch("aletheia.knowledge.router._build_guard_advisory")
    @patch("aletheia.knowledge.router._retrieve_lessons_safe")
    def test_empty_when_nothing(self, mock_lessons, mock_guards):
        mock_lessons.return_value = ""
        mock_guards.return_value = ""
        result = build_advisory_context("market_observer", "query", guard_context={})
        assert result == ""

    @patch("aletheia.knowledge.router._retrieve_lessons_safe")
    def test_no_guard_context_skips_guards(self, mock_lessons):
        mock_lessons.return_value = "## Lessons"
        result = build_advisory_context("market_observer", "query")
        assert "Lessons" in result

    @patch("aletheia.knowledge.router._retrieve_lessons_safe")
    def test_truncates_long_output(self, mock_lessons):
        mock_lessons.return_value = "x" * 3000
        result = build_advisory_context("market_observer", "query", max_chars=100)
        assert len(result) <= 120  # 100 + truncation notice
        assert "[...truncated]" in result
