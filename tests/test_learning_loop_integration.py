"""Integration tests for the learning loop — lesson-aware scoring (P1).

P0 (ranker regime-aware strategy filtering), P2 (executable RuleSet bonus), and
P3 (post-trade → strategy_designer event chain) were removed with the rules
engine (Position C, 2026-07-02): regime scoring moved to the universe
scanner-family layer (see universe pipeline UP-* specs), and the
strategy-designer trigger chain no longer exists. Lesson-aware scoring (P1)
survives and is the sole learning-loop hook exercised here.
"""
from unittest.mock import patch


# ---------------------------------------------------------------------------
# P1: Lesson-aware scoring
# ---------------------------------------------------------------------------

class TestLessonAwareScoring:
    """Test _lesson_penalty logic."""

    def test_no_lessons_no_penalty(self):
        from aletheia.agents.paper_trader_ranker import _lesson_penalty

        with patch("aletheia.knowledge.retrieval.retrieve_lessons", return_value=[]):
            penalty = _lesson_penalty("BTC/USDT", "long", "momentum")
            assert penalty == 0.0

    def test_negative_lessons_apply_penalty(self):
        from aletheia.agents.paper_trader_ranker import _lesson_penalty
        from aletheia.knowledge.retrieval import Lesson

        mock_lessons = [
            Lesson(
                item_id="l1", item_type="trade_case_study",
                title="Loss on BTC long", snippet="This trade resulted in a loss of -3.5%",
                confidence="high_confidence", created_at="2026-06-17T00:00:00Z", score=0.8,
                outcome="loss",
            ),
        ]
        with patch("aletheia.knowledge.retrieval.retrieve_lessons", return_value=mock_lessons) as retrieve:
            penalty = _lesson_penalty("BTC/USDT", "long", "momentum")
            assert penalty < 0
            assert penalty >= -25.0
            retrieve.assert_called_once_with(
                "BTC/USDT long momentum",
                k=3,
                item_types=["trade_case_study"],
                symbol="BTC/USDT",
                direction="long",
                scanner_family="momentum",
            )

    def test_falls_back_to_symbol_direction_when_scanner_history_missing(self):
        from aletheia.agents.paper_trader_ranker import _lesson_penalty
        from aletheia.knowledge.retrieval import Lesson

        fallback_lessons = [
            Lesson(
                item_id="l-old", item_type="trade_case_study",
                title="Old BTC long loss", snippet="BTC long failed before scanner tracking",
                confidence="high_confidence", created_at="2026-06-17T00:00:00Z", score=0.5,
                outcome="loss",
            ),
        ]
        with patch(
            "aletheia.knowledge.retrieval.retrieve_lessons",
            side_effect=[[], fallback_lessons],
        ) as retrieve:
            penalty = _lesson_penalty("BTC/USDT", "long", "momentum")
            assert penalty < 0
            assert retrieve.call_count == 2
            retrieve.assert_any_call(
                "BTC/USDT long momentum",
                k=3,
                item_types=["trade_case_study"],
                symbol="BTC/USDT",
                direction="long",
                scanner_family="momentum",
            )
            retrieve.assert_any_call(
                "BTC/USDT long",
                k=3,
                item_types=["trade_case_study"],
                symbol="BTC/USDT",
                direction="long",
            )

    def test_positive_lessons_no_penalty(self):
        from aletheia.agents.paper_trader_ranker import _lesson_penalty
        from aletheia.knowledge.retrieval import Lesson

        mock_lessons = [
            Lesson(
                item_id="l2", item_type="trade_case_study",
                title="Win on ETH short", snippet="This trade was profitable with +2.1%",
                confidence="high_confidence", created_at="2026-06-17T00:00:00Z", score=0.9,
            ),
        ]
        with patch("aletheia.knowledge.retrieval.retrieve_lessons", return_value=mock_lessons):
            penalty = _lesson_penalty("ETH/USDT", "short", "mean_reversion")
            assert penalty == 0.0

    def test_penalty_capped_at_minus_25(self):
        from aletheia.agents.paper_trader_ranker import _lesson_penalty
        from aletheia.knowledge.retrieval import Lesson

        mock_lessons = [
            Lesson(item_id=f"l{i}", item_type="trade_case_study",
                   title=f"Loss {i}", snippet="massive loss and drawdown failure",
                   confidence="high_confidence", created_at="2026-06-17T00:00:00Z", score=1.0,
                   outcome="loss")
            for i in range(5)
        ]
        with patch("aletheia.knowledge.retrieval.retrieve_lessons", return_value=mock_lessons):
            penalty = _lesson_penalty("BTC/USDT", "long", "momentum")
            assert penalty == -25.0

    def test_no_symbol_returns_zero(self):
        from aletheia.agents.paper_trader_ranker import _lesson_penalty
        assert _lesson_penalty(None, "long", "momentum") == 0.0
