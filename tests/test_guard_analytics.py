"""Tests for guard effectiveness analytics and promotion pipeline."""

from unittest.mock import patch, MagicMock

from aletheia.analytics.guard_effectiveness import (
    compute_guard_stats,
    _row_to_guard_context,
)
from aletheia.analytics.guard_promotion import run_promotion_check


class TestRowToGuardContext:
    def test_maps_fields_correctly(self):
        row = {
            "symbol": "BTC/USDT",
            "direction": "long",
            "scanner_family": "momentum",
            "confidence": "high",
            "confidence_score": 75,
            "late_entry_penalty": 15,
            "tradability_score": 70,
            "universe_total_score": 80,
            "crowding_risk": 5,
            "risk_flags": ["wide_spread"],
        }
        ctx = _row_to_guard_context(row)
        assert ctx["symbol"] == "BTC/USDT"
        assert ctx["asset_class"] == "crypto"
        assert ctx["risk_flags"] == ["wide_spread"]
        assert ctx["late_entry_penalty"] == 15


class TestComputeGuardStats:
    @patch("aletheia.analytics.guard_effectiveness.build_trade_attribution_rows")
    @patch("aletheia.analytics.guard_effectiveness.load_trading_guards")
    def test_no_guards_empty(self, mock_guards, mock_rows):
        mock_guards.return_value = []
        result = compute_guard_stats()
        assert result == []

    @patch("aletheia.analytics.guard_effectiveness.build_trade_attribution_rows")
    @patch("aletheia.analytics.guard_effectiveness.load_trading_guards")
    def test_no_rows_zero_counts(self, mock_guards, mock_rows):
        from aletheia.knowledge.guards import TradingGuard, GuardPredicate, GuardAppliesTo
        mock_guards.return_value = [TradingGuard(
            guard_id="test", title="Test", status="active",
            predicates=[GuardPredicate(field="late_entry_penalty", op="gte", value=20)],
            applies_to=GuardAppliesTo(asset_class=["crypto"]),
        )]
        mock_rows.return_value = []
        result = compute_guard_stats()
        assert result == []

    @patch("aletheia.analytics.guard_effectiveness.build_trade_attribution_rows")
    @patch("aletheia.analytics.guard_effectiveness.load_trading_guards")
    def test_counts_matching_rows(self, mock_guards, mock_rows):
        from aletheia.knowledge.guards import TradingGuard, GuardPredicate, GuardAppliesTo
        mock_guards.return_value = [TradingGuard(
            guard_id="late_guard", title="Late Guard", status="active",
            predicates=[GuardPredicate(field="late_entry_penalty", op="gte", value=20)],
            applies_to=GuardAppliesTo(asset_class=["crypto"]),
        )]
        mock_rows.return_value = [
            {"symbol": "BTC/USDT", "direction": "long", "late_entry_penalty": 25,
             "realized_pnl": -5, "realized_pnl_pct": -2.5, "risk_flags": [],
             "scanner_family": "momentum", "confidence": "high", "confidence_score": 70,
             "tradability_score": 70, "universe_total_score": 80, "crowding_risk": 3},
            {"symbol": "ETH/USDT", "direction": "long", "late_entry_penalty": 30,
             "realized_pnl": 3, "realized_pnl_pct": 1.5, "risk_flags": [],
             "scanner_family": "momentum", "confidence": "high", "confidence_score": 70,
             "tradability_score": 70, "universe_total_score": 80, "crowding_risk": 3},
        ]
        result = compute_guard_stats()
        assert len(result) == 1
        assert result[0]["fire_count"] == 2
        assert result[0]["loss_rate_when_fired"] == 0.5


class TestRunPromotionCheck:
    @patch("aletheia.analytics.guard_promotion.load_config")
    def test_skips_when_disabled(self, mock_cfg):
        cfg = MagicMock()
        cfg.guard.auto_promote_enabled = False
        mock_cfg.return_value = cfg
        result = run_promotion_check()
        assert result["skipped"] is True

    @patch("aletheia.analytics.guard_promotion.load_trading_guards")
    @patch("aletheia.analytics.guard_promotion.load_config")
    def test_no_candidates_zero_promotions(self, mock_cfg, mock_guards):
        cfg = MagicMock()
        cfg.guard.auto_promote_enabled = True
        cfg.guard.min_matching_trades = 3
        mock_cfg.return_value = cfg
        mock_guards.return_value = []
        result = run_promotion_check()
        assert result["promotions"] == 0
        assert result["candidates_checked"] == 0
