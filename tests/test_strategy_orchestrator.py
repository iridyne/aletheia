"""Tests for strategy orchestrator and performance ledger."""
import json
from unittest.mock import patch, MagicMock

import pytest

from aletheia.strategy.backtest import BacktestConfig, BacktestResult, BacktestMetrics
from aletheia.strategy.cost_model import CostModel
from aletheia.strategy.baseline_strategies import strategy_1_btc_trend_following
from aletheia.strategy.orchestrator import run_all_backtests, generate_daily_report, OrchestratorResult
from aletheia.strategy.performance_ledger import LedgerSnapshot, format_ledger_report


class TestOrchestrator:
    def _make_strategy_row(self, sid="s1", name="test", state="paper_running", ruleset_json=None):
        params = {}
        if ruleset_json:
            params["ruleset"] = ruleset_json
            params["symbol"] = "BTC/USDT"
            params["timeframe"] = "1h"
            params["direction_bias"] = "long"
        return {
            "id": sid,
            "name": name,
            "state": state,
            "parameters": json.dumps(params),
        }

    @patch("aletheia.storage.repositories.list_strategies")
    @patch("aletheia.trading.strategy_lifecycle.promote_strategy")
    def test_skips_strategies_without_ruleset(self, mock_promote, mock_list):
        mock_list.return_value = [self._make_strategy_row(ruleset_json=None)]
        result = run_all_backtests()
        assert result.strategies_skipped == 1
        assert result.reports[0].action_taken == "skipped"
        assert "no_executable_ruleset" in result.reports[0].verdict_reasons

    @patch("aletheia.storage.repositories.list_strategies")
    @patch("aletheia.storage.repositories.update_strategy")
    @patch("aletheia.trading.strategy_lifecycle.promote_strategy")
    @patch("aletheia.strategy.orchestrator.load_bars_from_db")
    @patch("aletheia.strategy.orchestrator.insert_backtest_run")
    @patch("aletheia.strategy.orchestrator.insert_backtest_trades")
    @patch("aletheia.strategy.orchestrator._request_ohlcv_backfill")
    def test_skips_insufficient_bars(self, mock_backfill, mock_trades, mock_run, mock_bars, mock_promote, mock_update, mock_list):
        _, _, _, _, rs = strategy_1_btc_trend_following()
        mock_list.return_value = [self._make_strategy_row(ruleset_json=rs.to_json())]
        mock_bars.return_value = []  # no bars
        result = run_all_backtests()
        assert result.strategies_skipped == 1
        assert "insufficient_ohlcv_data" in result.reports[0].verdict_reasons
        mock_backfill.assert_called_once()

    @patch("aletheia.storage.repositories.list_strategies")
    @patch("aletheia.storage.repositories.update_strategy")
    @patch("aletheia.trading.strategy_lifecycle.promote_strategy")
    @patch("aletheia.strategy.orchestrator.load_bars_from_db")
    @patch("aletheia.strategy.orchestrator.insert_backtest_run")
    @patch("aletheia.strategy.orchestrator.insert_backtest_trades")
    @patch("aletheia.strategy.orchestrator._request_ohlcv_backfill")
    def test_auto_kills_after_3_consecutive_skips(self, mock_backfill, mock_trades, mock_run, mock_bars, mock_promote, mock_update, mock_list):
        _, _, _, _, rs = strategy_1_btc_trend_following()
        params = {"ruleset": rs.to_json(), "symbol": "BTC/USDT", "timeframe": "1h",
                  "direction_bias": "long", "_backtest_skip_count": 2}
        row = {"id": "s1", "name": "test", "state": "paper_running",
               "parameters": json.dumps(params)}
        mock_list.return_value = [row]
        mock_bars.return_value = []
        result = run_all_backtests()
        assert result.strategies_killed == 1
        assert result.reports[0].verdict == "kill"
        assert "consecutive_skips=3" in result.reports[0].verdict_reasons[1]
        mock_promote.assert_called_once_with("s1", "retired")

    @patch("aletheia.storage.repositories.list_strategies")
    @patch("aletheia.trading.strategy_lifecycle.promote_strategy")
    def test_handles_empty_active_list(self, mock_promote, mock_list):
        mock_list.return_value = [
            {"id": "x", "name": "inactive", "state": "retired", "parameters": "{}"}
        ]
        result = run_all_backtests()
        assert result.strategies_evaluated == 0
        assert result.reports == []

    def test_generate_daily_report_format(self):
        result = OrchestratorResult(
            timestamp="2026-06-14T04:00:00+00:00",
            strategies_evaluated=2, strategies_killed=1,
            strategies_kept=1, strategies_review=0, strategies_skipped=0,
            reports=[],
        )
        report = generate_daily_report(result)
        assert "2026-06-14" in report
        assert "Evaluated" in report
        assert "Killed" in report


class TestPerformanceLedger:
    def test_ledger_snapshot_format(self):
        snapshot = LedgerSnapshot(
            timestamp="2026-06-14T04:00:00+00:00", period_days=7,
            strategies_created=10, strategies_per_day=1.4,
            strategies_with_ruleset=7, compile_rate_pct=70.0,
            strategies_backtested=5, backtest_coverage_pct=50.0,
            strategies_surviving=3, survival_rate_pct=60.0,
            strategies_killed=2, kill_rate_pct=20.0,
            avg_time_to_verdict_hours=4.5, median_time_to_verdict_hours=3.2,
            active_strategies=8, total_backtest_trades=42,
        )
        report = format_ledger_report(snapshot)
        assert "Performance Ledger" in report
        assert "70%" in report
        assert "4.5h" in report
        assert "42" in report

    def test_ledger_snapshot_zero_division_safe(self):
        snapshot = LedgerSnapshot(
            timestamp="2026-06-14T04:00:00+00:00", period_days=7,
            strategies_created=0, strategies_per_day=0.0,
            strategies_with_ruleset=0, compile_rate_pct=0.0,
            strategies_backtested=0, backtest_coverage_pct=0.0,
            strategies_surviving=0, survival_rate_pct=0.0,
            strategies_killed=0, kill_rate_pct=0.0,
            avg_time_to_verdict_hours=0.0, median_time_to_verdict_hours=0.0,
            active_strategies=0, total_backtest_trades=0,
        )
        report = format_ledger_report(snapshot)
        assert "0%" in report
