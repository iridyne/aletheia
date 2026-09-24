"""Tests for guard effectiveness analytics and promotion pipeline."""

from datetime import datetime, timedelta
from unittest.mock import patch

from aletheia.analytics.learning_window import CLEAN_WINDOW_ANCHOR_ISO
from extensions.analytics.guard_effectiveness import (
    compute_guard_stats,
    _row_to_guard_context,
)

_ANCHOR = datetime.fromisoformat(CLEAN_WINDOW_ANCHOR_ISO)


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
    @patch("extensions.analytics.guard_effectiveness.build_trade_attribution_rows")
    @patch("extensions.analytics.guard_effectiveness.load_trading_guards")
    def test_no_guards_empty(self, mock_guards, mock_rows):
        mock_guards.return_value = []
        result = compute_guard_stats()
        assert result == []

    @patch("extensions.analytics.guard_effectiveness.build_trade_attribution_rows")
    @patch("extensions.analytics.guard_effectiveness.load_trading_guards")
    def test_no_rows_zero_counts(self, mock_guards, mock_rows):
        from extensions.knowledge.guards import TradingGuard, GuardPredicate, GuardAppliesTo
        mock_guards.return_value = [TradingGuard(
            guard_id="test", title="Test", status="active",
            predicates=[GuardPredicate(field="late_entry_penalty", op="gte", value=20)],
            applies_to=GuardAppliesTo(asset_class=["crypto"]),
        )]
        mock_rows.return_value = []
        result = compute_guard_stats()
        assert result == []

    @patch("extensions.analytics.guard_effectiveness.build_trade_attribution_rows")
    @patch("extensions.analytics.guard_effectiveness.load_trading_guards")
    def test_counts_matching_rows(self, mock_guards, mock_rows):
        from extensions.knowledge.guards import TradingGuard, GuardPredicate, GuardAppliesTo
        mock_guards.return_value = [TradingGuard(
            guard_id="late_guard", title="Late Guard", status="active",
            predicates=[GuardPredicate(field="late_entry_penalty", op="gte", value=20)],
            applies_to=GuardAppliesTo(asset_class=["crypto"]),
        )]
        # Rows must close after the clean-window anchor (compute_guard_stats uses
        # _clean_rows, which drops rows with closed_at < LEARNING_DATA_FLOOR_ISO).
        post_anchor_close = (_ANCHOR + timedelta(hours=2)).isoformat()
        mock_rows.return_value = [
            {"symbol": "BTC/USDT", "direction": "long", "late_entry_penalty": 25,
             "realized_pnl": -5, "realized_pnl_pct": -2.5, "risk_flags": [],
             "scanner_family": "momentum", "confidence": "high", "confidence_score": 70,
             "tradability_score": 70, "universe_total_score": 80, "crowding_risk": 3,
             "closed_at": post_anchor_close},
            {"symbol": "ETH/USDT", "direction": "long", "late_entry_penalty": 30,
             "realized_pnl": 3, "realized_pnl_pct": 1.5, "risk_flags": [],
             "scanner_family": "momentum", "confidence": "high", "confidence_score": 70,
             "tradability_score": 70, "universe_total_score": 80, "crowding_risk": 3,
             "closed_at": post_anchor_close},
        ]
        result = compute_guard_stats()
        assert len(result) == 1
        assert result[0]["fire_count"] == 2
        assert result[0]["loss_rate_when_fired"] == 0.5
