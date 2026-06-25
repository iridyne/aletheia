"""Integration tests for the learning loop improvements (P0-P3).

Tests:
- P0: Ranker regime-aware strategy filtering
- P1: Lesson-aware scoring penalty
- P2: Executable RuleSet bonus
- P3: Post-trade → Strategy Designer event chain
"""
from unittest.mock import patch, MagicMock
import json

import pytest


# ---------------------------------------------------------------------------
# P0: Regime-aware strategy filtering
# ---------------------------------------------------------------------------

class TestRegimeAwareFiltering:
    """Test _check_strategy_regime_fit logic."""

    def test_bear_regime_blocks_long_only_strategy(self):
        from aletheia.agents.paper_trader_ranker import _check_strategy_regime_fit
        from aletheia.regime.detector import MarketRegime

        strategy = {"direction_affinity": "long", "applicable_regimes": "all", "parameters": "{}"}
        regime = MarketRegime(state="bear", confidence=0.7, long_bias=0.25, short_bias=0.75)

        result = _check_strategy_regime_fit(strategy, "long", regime)
        assert result is not None
        assert "regime_mismatch" in result
        assert "bear_vs_long_only" in result

    def test_bull_regime_blocks_short_only_strategy(self):
        from aletheia.agents.paper_trader_ranker import _check_strategy_regime_fit
        from aletheia.regime.detector import MarketRegime

        strategy = {"direction_affinity": "short", "applicable_regimes": "all", "parameters": "{}"}
        regime = MarketRegime(state="bull", confidence=0.8, long_bias=0.75, short_bias=0.25)

        result = _check_strategy_regime_fit(strategy, "short", regime)
        assert result is not None
        assert "bull_vs_short_only" in result

    def test_both_affinity_passes_any_regime(self):
        from aletheia.agents.paper_trader_ranker import _check_strategy_regime_fit
        from aletheia.regime.detector import MarketRegime

        strategy = {"direction_affinity": "both", "applicable_regimes": "all", "parameters": "{}"}
        regime = MarketRegime(state="bear", confidence=0.9)

        result = _check_strategy_regime_fit(strategy, "long", regime)
        assert result is None

    def test_low_confidence_regime_does_not_block(self):
        from aletheia.agents.paper_trader_ranker import _check_strategy_regime_fit
        from aletheia.regime.detector import MarketRegime

        strategy = {"direction_affinity": "long", "applicable_regimes": "all", "parameters": "{}"}
        regime = MarketRegime(state="bear", confidence=0.4)

        result = _check_strategy_regime_fit(strategy, "long", regime)
        assert result is None

    def test_applicable_regimes_filter(self):
        from aletheia.agents.paper_trader_ranker import _check_strategy_regime_fit
        from aletheia.regime.detector import MarketRegime

        strategy = {
            "direction_affinity": "both",
            "applicable_regimes": json.dumps(["bull", "sideways"]),
            "parameters": "{}",
        }
        regime = MarketRegime(state="bear", confidence=0.5)

        result = _check_strategy_regime_fit(strategy, "short", regime)
        assert result is not None
        assert "regime_not_applicable" in result

    def test_applicable_regimes_allows_matching(self):
        from aletheia.agents.paper_trader_ranker import _check_strategy_regime_fit
        from aletheia.regime.detector import MarketRegime

        strategy = {
            "direction_affinity": "both",
            "applicable_regimes": json.dumps(["bear", "volatile"]),
            "parameters": "{}",
        }
        regime = MarketRegime(state="bear", confidence=0.8)

        result = _check_strategy_regime_fit(strategy, "short", regime)
        assert result is None

    def test_affinity_from_parameters_json(self):
        from aletheia.agents.paper_trader_ranker import _check_strategy_regime_fit
        from aletheia.regime.detector import MarketRegime

        strategy = {
            "parameters": json.dumps({"direction_affinity": "long", "applicable_regimes": "all"}),
        }
        regime = MarketRegime(state="bear", confidence=0.7)

        result = _check_strategy_regime_fit(strategy, "long", regime)
        assert result is not None
        assert "bear_vs_long_only" in result


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
            ),
        ]
        with patch("aletheia.knowledge.retrieval.retrieve_lessons", return_value=mock_lessons):
            penalty = _lesson_penalty("BTC/USDT", "long", "momentum")
            assert penalty < 0
            assert penalty >= -25.0

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
                   confidence="high_confidence", created_at="2026-06-17T00:00:00Z", score=1.0)
            for i in range(5)
        ]
        with patch("aletheia.knowledge.retrieval.retrieve_lessons", return_value=mock_lessons):
            penalty = _lesson_penalty("BTC/USDT", "long", "momentum")
            assert penalty == -25.0

    def test_no_symbol_returns_zero(self):
        from aletheia.agents.paper_trader_ranker import _lesson_penalty
        assert _lesson_penalty(None, "long", "momentum") == 0.0


# ---------------------------------------------------------------------------
# P2: Executable RuleSet bonus (tested via _compute_trade_score)
# ---------------------------------------------------------------------------

class TestRuleSetBonus:
    """Test that strategy_has_ruleset adds bonus to trade score."""

    def _score(self, has_ruleset: bool) -> float:
        from aletheia.agents.paper_trader_ranker import _compute_trade_score
        from aletheia.regime.detector import MarketRegime
        opp = {"confidence_score": 60, "symbol": "BTC/USDT", "scanner_family": "momentum"}
        with patch("aletheia.regime.detector.get_current_regime",
                   return_value=MarketRegime(state="sideways", confidence=0.4)), \
             patch("aletheia.knowledge.retrieval.retrieve_lessons", return_value=[]):
            return _compute_trade_score(
                opp, None, {"status": "watch"}, "medium", None,
                strategy_has_ruleset=has_ruleset, direction="long",
            )

    def test_ruleset_bonus_applied(self):
        score_with = self._score(True)
        score_without = self._score(False)
        assert score_with > score_without
        assert score_with - score_without == 15.0


# ---------------------------------------------------------------------------
# P3: Post-trade → Strategy Designer event chain
# ---------------------------------------------------------------------------

class TestPostTradeEventChain:
    """Test _trigger_strategy_adaptation and event chain."""

    def test_trigger_fires_when_enough_lessons(self):
        from aletheia.agents.post_trade_reviewer import _trigger_strategy_adaptation
        with patch("aletheia.agent_runtime.scheduler.trigger_strategy_designer_on_demand", return_value=True) as mock_trigger:
            _trigger_strategy_adaptation(3, "run-1")
            mock_trigger.assert_called_once()

    def test_trigger_handles_import_failure(self):
        from aletheia.agents.post_trade_reviewer import _trigger_strategy_adaptation
        with patch("aletheia.agent_runtime.scheduler.trigger_strategy_designer_on_demand", side_effect=ImportError("test")):
            _trigger_strategy_adaptation(2, "run-1")

    def test_adaptation_drives_real_debounce_chain(self):
        """End-to-end through the REAL trigger: _trigger_strategy_adaptation →
        trigger_strategy_designer_on_demand → debounce. Mocks ONLY the heavy
        designer body (_run_strategy_designer), so the actual cooldown logic runs.

        Catches a regression where the review→design chain stops honoring the
        debounce (would re-run the designer on every post-trade review) — invisible
        to tests that mock trigger_strategy_designer_on_demand wholesale.
        """
        import aletheia.agent_runtime.scheduler as sched
        from aletheia.agents.post_trade_reviewer import _trigger_strategy_adaptation

        sched._last_event_design_time = 0.0  # reset cooldown
        with patch.object(sched, "_run_strategy_designer") as mock_run:
            # First adaptation after enough lessons → designer actually runs.
            _trigger_strategy_adaptation(3, "run-1")
            assert mock_run.call_count == 1
            ts_after_first = sched._last_event_design_time
            assert ts_after_first > 0  # debounce timestamp was set

            # A second adaptation immediately after → debounced, designer NOT re-run.
            _trigger_strategy_adaptation(4, "run-2")
            assert mock_run.call_count == 1  # still 1 — cooldown suppressed it

    def test_full_flow_triggers_on_case_studies(self):
        """End-to-end: reviewer produces cases → triggers strategy designer."""
        import asyncio
        from aletheia.agents.post_trade_reviewer import run_post_trade_reviewer

        mock_cases = [{"case_study_id": "cs1"}, {"case_study_id": "cs2"}]

        with patch("aletheia.agents.post_trade_reviewer.is_tool_permitted", return_value=True), \
             patch("aletheia.agents.post_trade_reviewer.list_fills", return_value=[]), \
             patch("aletheia.agents.post_trade_reviewer.list_orders", return_value=[]), \
             patch("aletheia.agents.post_trade_reviewer.log_tool"), \
             patch("aletheia.agents.post_trade_reviewer._run_case_builder_safe", return_value=(mock_cases, [])), \
             patch("aletheia.agents.post_trade_reviewer._trigger_strategy_adaptation") as mock_adapt:
            result = asyncio.run(run_post_trade_reviewer(permission_mode="Paper", run_id="test"))
            mock_adapt.assert_called_once_with(2, "test")
            assert "strategy_designer_triggered" in str(result["outputs"])

    def test_no_trigger_when_insufficient_lessons(self):
        """Only 1 case study → no trigger."""
        import asyncio
        from aletheia.agents.post_trade_reviewer import run_post_trade_reviewer

        mock_cases = [{"case_study_id": "cs1"}]

        with patch("aletheia.agents.post_trade_reviewer.is_tool_permitted", return_value=True), \
             patch("aletheia.agents.post_trade_reviewer.list_fills", return_value=[]), \
             patch("aletheia.agents.post_trade_reviewer.list_orders", return_value=[]), \
             patch("aletheia.agents.post_trade_reviewer.log_tool"), \
             patch("aletheia.agents.post_trade_reviewer._run_case_builder_safe", return_value=(mock_cases, [])), \
             patch("aletheia.agents.post_trade_reviewer._trigger_strategy_adaptation") as mock_adapt:
            result = asyncio.run(run_post_trade_reviewer(permission_mode="Paper", run_id="test"))
            mock_adapt.assert_not_called()
