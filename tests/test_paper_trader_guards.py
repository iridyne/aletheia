"""Tests for guard integration in paper_trader_ranker."""

from unittest.mock import patch, MagicMock

from aletheia.agents.paper_trader_ranker import _evaluate_guards_safe, _make_candidate
from aletheia.knowledge.guards import GuardEvaluation, GuardHit, GuardPredicate, TradingGuard, GuardAppliesTo


def _mock_config(enabled=True, enforce=True, max_penalty=40.0):
    cfg = MagicMock()
    cfg.guard.enabled = enabled
    cfg.guard.enforce_in_paper_ranker = enforce
    cfg.guard.max_score_penalty = max_penalty
    return cfg


def _mock_opp(symbol="BTC/USDT", confidence="high", scanner_family="momentum"):
    return {
        "id": "opp-1",
        "symbol": symbol,
        "confidence": confidence,
        "confidence_score": 75,
        "scanner_family": scanner_family,
        "direction_bias": "long",
        "source_agent": "market_observer",
        "universe_member_id": "um-1",
        "linked_strategy_id": "strat-1",
    }


def _mock_universe_member(late_entry_penalty=10, risk_flags=None):
    return {
        "rank": 1,
        "total_score": 80,
        "tradability_score": 70,
        "late_entry_penalty": late_entry_penalty,
        "crowding_risk": 5,
        "risk_flags": risk_flags or [],
        "late_entry_bucket": "early",
    }


class TestEvaluateGuardsSafe:
    @patch("aletheia.agents.paper_trader_ranker.load_config")
    @patch("aletheia.agents.paper_trader_ranker.evaluate_guards")
    def test_returns_evaluation_on_match(self, mock_eval, mock_cfg):
        mock_cfg.return_value = _mock_config()
        expected = GuardEvaluation(
            blocked=False, total_penalty=20.0,
            hits=[GuardHit(
                guard_id="test_g", title="Test", enforcement="scoring",
                severity="warning", score_penalty=20.0, reason="matched",
            )],
        )
        mock_eval.return_value = expected

        result = _evaluate_guards_safe(
            _mock_opp(), _mock_universe_member(), {"status": "active"},
            "long", "high", True, True, None,
        )
        assert result is not None
        assert result.total_penalty == 20.0
        assert len(result.hits) == 1

    @patch("aletheia.agents.paper_trader_ranker.load_config")
    def test_returns_none_when_disabled(self, mock_cfg):
        mock_cfg.return_value = _mock_config(enabled=False)

        result = _evaluate_guards_safe(
            _mock_opp(), _mock_universe_member(), {"status": "active"},
            "long", "high", True, True, None,
        )
        assert result is None

    @patch("aletheia.agents.paper_trader_ranker.load_config")
    def test_returns_none_when_enforce_off(self, mock_cfg):
        mock_cfg.return_value = _mock_config(enforce=False)

        result = _evaluate_guards_safe(
            _mock_opp(), _mock_universe_member(), {"status": "active"},
            "long", "high", True, True, None,
        )
        assert result is None

    @patch("aletheia.agents.paper_trader_ranker.load_config")
    @patch("aletheia.agents.paper_trader_ranker.evaluate_guards")
    def test_caps_penalty_at_max(self, mock_eval, mock_cfg):
        mock_cfg.return_value = _mock_config(max_penalty=30.0)
        mock_eval.return_value = GuardEvaluation(blocked=False, total_penalty=50.0, hits=[])

        result = _evaluate_guards_safe(
            _mock_opp(), _mock_universe_member(), {"status": "active"},
            "long", "high", True, True, None,
        )
        assert result.total_penalty == 30.0

    @patch("aletheia.agents.paper_trader_ranker.load_config")
    def test_never_crashes_on_exception(self, mock_cfg):
        mock_cfg.side_effect = RuntimeError("config broken")

        result = _evaluate_guards_safe(
            _mock_opp(), _mock_universe_member(), {"status": "active"},
            "long", "high", True, True, None,
        )
        assert result is None

    @patch("aletheia.agents.paper_trader_ranker.load_config")
    @patch("aletheia.agents.paper_trader_ranker.evaluate_guards")
    def test_builds_context_with_risk_flags(self, mock_eval, mock_cfg):
        mock_cfg.return_value = _mock_config()
        mock_eval.return_value = GuardEvaluation(blocked=False, total_penalty=0, hits=[])

        _evaluate_guards_safe(
            _mock_opp(), _mock_universe_member(risk_flags=["wide_spread", "crowded"]),
            {"status": "watch", "expectancy": -0.5, "trade_count": 5},
            "long", "high", False, None, {"drift_status": "paper_confirming"},
        )

        ctx = mock_eval.call_args[0][0]
        assert ctx["risk_flags"] == ["wide_spread", "crowded"]
        assert ctx["factor_status"] == "watch"
        assert ctx["strategy_has_ruleset"] is False
        assert ctx["strategy_drift_status"] == "paper_confirming"
        assert ctx["asset_class"] == "crypto"


class TestMakeCandidateGuardFields:
    def test_guard_metadata_when_eval_present(self):
        guard_eval = GuardEvaluation(
            blocked=True, total_penalty=20.0,
            hits=[
                GuardHit(
                    guard_id="g1", title="Guard 1", enforcement="blocking",
                    severity="critical", score_penalty=0, block_candidate=True,
                    reason="Guard 1: predicates matched",
                ),
            ],
        )
        c = _make_candidate({"id": "o1", "symbol": "BTC/USDT"}, guard_eval=guard_eval)
        assert c["guard_blocked"] is True
        assert c["guard_penalty"] == 20.0
        assert c["matched_guard_ids"] == ["g1"]
        assert c["guard_reasons"] == ["Guard 1: predicates matched"]

    def test_guard_metadata_when_no_eval(self):
        c = _make_candidate({"id": "o1", "symbol": "BTC/USDT"})
        assert c["guard_blocked"] is False
        assert c["guard_penalty"] == 0.0
        assert c["matched_guard_ids"] == []
        assert c["guard_reasons"] == []

    def test_guard_metadata_empty_eval(self):
        guard_eval = GuardEvaluation(blocked=False, total_penalty=0, hits=[])
        c = _make_candidate({"id": "o1", "symbol": "BTC/USDT"}, guard_eval=guard_eval)
        assert c["guard_blocked"] is False
        assert c["guard_penalty"] == 0.0
        assert c["matched_guard_ids"] == []
