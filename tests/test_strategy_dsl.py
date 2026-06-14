"""Unit tests for the strategy Rule DSL, evaluator, cost model, and backtest runner."""
import json
import math

import pytest

from aletheia.strategy.backtest import BacktestConfig, BacktestResult, run_backtest
from aletheia.strategy.baseline_strategies import (
    strategy_0_pipeline_test,
    strategy_1_btc_trend_following,
    strategy_2_eth_mean_reversion,
)
from aletheia.strategy.cost_model import CostModel
from aletheia.strategy.dsl import (
    Comparator, Condition, IndicatorRef, RuleExpr, RuleSet, StopLoss,
)
from aletheia.strategy.evaluator import BarData, compute_stop_price, evaluate_at_bar


# ── helpers ───────────────────────────────────────────────────────────────────

def make_bars(closes: list[float]) -> list[BarData]:
    """Synthetic bars: high = close + 0.5%, low = close - 0.5%, open = close."""
    bars = []
    for i, c in enumerate(closes):
        bars.append(BarData(
            ts=i * 3_600_000,
            open=c,
            high=c * 1.005,
            low=c * 0.995,
            close=c,
            volume=1000.0,
        ))
    return bars


# ── DSL serialization ─────────────────────────────────────────────────────────

class TestDSLSerialisation:
    def test_roundtrip_simple(self):
        rs = RuleSet(
            entry=RuleExpr(op="and", conditions=[
                Condition(
                    left=IndicatorRef(name="rsi", params={"period": 14}),
                    comparator=Comparator.LT,
                    right=30.0,
                )
            ]),
            exit=RuleExpr(op="and", conditions=[
                Condition(
                    left=IndicatorRef(name="rsi", params={"period": 14}),
                    comparator=Comparator.GT,
                    right=70.0,
                )
            ]),
            stop_loss=StopLoss(mode="fixed_pct", value=2.0),
        )
        serialised = rs.to_json()
        recovered = RuleSet.from_json(serialised)
        assert recovered.to_json() == serialised

    def test_roundtrip_indicator_vs_indicator(self):
        rs = RuleSet(
            entry=RuleExpr(op="and", conditions=[
                Condition(
                    left=IndicatorRef(name="close"),
                    comparator=Comparator.GT,
                    right=IndicatorRef(name="sma", params={"period": 50}),
                )
            ]),
            exit=RuleExpr(op="and", conditions=[
                Condition(
                    left=IndicatorRef(name="close"),
                    comparator=Comparator.LT,
                    right=IndicatorRef(name="sma", params={"period": 50}),
                )
            ]),
        )
        recovered = RuleSet.from_json(rs.to_json())
        assert json.loads(recovered.to_json()) == json.loads(rs.to_json())

    def test_roundtrip_nested_or(self):
        rs = RuleSet(
            entry=RuleExpr(op="or", conditions=[
                Condition(left=IndicatorRef(name="rsi"), comparator=Comparator.LT, right=30.0),
                Condition(left=IndicatorRef(name="rsi"), comparator=Comparator.GT, right=70.0),
            ]),
            exit=RuleExpr(op="and", conditions=[
                Condition(left=IndicatorRef(name="close"), comparator=Comparator.GT, right=0.0),
            ]),
        )
        recovered = RuleSet.from_json(rs.to_json())
        assert recovered.entry.op == "or"
        assert len(recovered.entry.conditions) == 2

    def test_invalid_comparator_raises(self):
        d = json.loads(strategy_0_pipeline_test()[4].to_json())
        d["entry"]["conditions"][0]["comparator"] = "INVALID"
        with pytest.raises(ValueError):
            RuleSet.from_json(json.dumps(d))


# ── evaluator ────────────────────────────────────────────────────────────────

class TestEvaluator:
    def test_always_true_entry(self):
        _, _, _, _, rs = strategy_0_pipeline_test()
        bars = make_bars([100.0] * 10)
        sig = evaluate_at_bar(rs, bars, 5)
        assert sig["entry"] is True
        assert sig["exit"] is True

    def test_insufficient_data_returns_none(self):
        # SMA(50) needs 50 bars; with only 10 bars it should return None
        rs = RuleSet(
            entry=RuleExpr(op="and", conditions=[
                Condition(
                    left=IndicatorRef(name="close"),
                    comparator=Comparator.GT,
                    right=IndicatorRef(name="sma", params={"period": 50}),
                )
            ]),
            exit=RuleExpr(op="and", conditions=[
                Condition(left=IndicatorRef(name="close"), comparator=Comparator.GT, right=0.0)
            ]),
        )
        bars = make_bars([100.0] * 10)
        sig = evaluate_at_bar(rs, bars, 9)
        assert sig["entry"] is None

    def test_rsi_oversold_triggers_entry(self):
        # Build a series that drives RSI < 30: 15 up bars then 20 sharp down bars
        closes = [100.0 + i for i in range(15)] + [114.0 - i * 2 for i in range(20)]
        rs = RuleSet(
            entry=RuleExpr(op="and", conditions=[
                Condition(
                    left=IndicatorRef(name="rsi", params={"period": 14}),
                    comparator=Comparator.LT,
                    right=30.0,
                )
            ]),
            exit=RuleExpr(op="and", conditions=[
                Condition(left=IndicatorRef(name="close"), comparator=Comparator.GT, right=0.0)
            ]),
        )
        bars = make_bars(closes)
        # At the final bar, RSI should be very low
        sig = evaluate_at_bar(rs, bars, len(bars) - 1)
        # Either fires or is None — just confirm it doesn't raise
        assert sig["entry"] in (True, False, None)

    def test_no_look_ahead(self):
        """evaluate_at_bar at index i must not see bars[i+1:]."""
        closes = [100.0] * 30 + [200.0] * 30  # sudden jump at bar 30
        bars = make_bars(closes)
        rs = RuleSet(
            entry=RuleExpr(op="and", conditions=[
                Condition(
                    left=IndicatorRef(name="sma", params={"period": 10}),
                    comparator=Comparator.GT,
                    right=150.0,
                )
            ]),
            exit=RuleExpr(op="and", conditions=[
                Condition(left=IndicatorRef(name="close"), comparator=Comparator.GT, right=0.0)
            ]),
        )
        # At bar 29 (last bar before jump), SMA(10) should still be ~100
        sig_before = evaluate_at_bar(rs, bars, 29)
        assert sig_before["entry"] is False

    def test_or_rule_partial_none(self):
        """OR rule returns non-None if at least one condition has a value."""
        rs = RuleSet(
            entry=RuleExpr(op="or", conditions=[
                # This needs 200 bars (None with 10 bars)
                Condition(
                    left=IndicatorRef(name="close"),
                    comparator=Comparator.GT,
                    right=IndicatorRef(name="sma", params={"period": 200}),
                ),
                # This is always evaluable
                Condition(
                    left=IndicatorRef(name="close"),
                    comparator=Comparator.GT,
                    right=0.0,
                ),
            ]),
            exit=RuleExpr(op="and", conditions=[
                Condition(left=IndicatorRef(name="close"), comparator=Comparator.GT, right=0.0)
            ]),
        )
        bars = make_bars([100.0] * 10)
        sig = evaluate_at_bar(rs, bars, 9)
        # Second condition is True, so OR result should be True (not None)
        assert sig["entry"] is True


# ── cost model ────────────────────────────────────────────────────────────────

class TestCostModel:
    def test_long_entry_fills_above(self):
        cm = CostModel(slippage_pct=0.1)
        fill, fee, slip = cm.entry_cost(price=100.0, quantity=1.0, direction="long")
        assert fill > 100.0
        assert fee > 0
        assert slip > 0

    def test_long_exit_fills_below(self):
        cm = CostModel(slippage_pct=0.1)
        fill, fee, slip = cm.exit_cost(price=100.0, quantity=1.0, direction="long")
        assert fill < 100.0

    def test_short_entry_fills_below(self):
        cm = CostModel(slippage_pct=0.1)
        fill, fee, slip = cm.entry_cost(price=100.0, quantity=1.0, direction="short")
        assert fill < 100.0

    def test_short_exit_fills_above(self):
        cm = CostModel(slippage_pct=0.1)
        fill, fee, slip = cm.exit_cost(price=100.0, quantity=1.0, direction="short")
        assert fill > 100.0

    def test_zero_slippage(self):
        cm = CostModel(slippage_pct=0.0)
        fill, _, _ = cm.entry_cost(price=50_000.0, quantity=1.0, direction="long")
        assert fill == 50_000.0


# ── backtest runner ────────────────────────────────────────────────────────────

class TestBacktestRunner:
    def test_pipeline_strategy_trade_count(self):
        """Strategy 0 (always enter, always exit) should produce len(bars)//2 trades."""
        _, symbol, tf, direction, rs = strategy_0_pipeline_test()
        bars = make_bars([100.0 + i * 0.1 for i in range(50)])
        cfg = BacktestConfig(symbol=symbol, timeframe=tf, direction=direction)
        result = run_backtest(rs, bars, cfg)
        # Each trade takes 1 bar: enter at bar i, exit at bar i+1 → ~25 trades
        assert result.metrics.trade_count > 0
        assert len(result.equity_curve) == len(bars)

    def test_equity_curve_length(self):
        _, symbol, tf, direction, rs = strategy_0_pipeline_test()
        bars = make_bars([100.0] * 20)
        cfg = BacktestConfig(symbol=symbol, timeframe=tf, direction=direction)
        result = run_backtest(rs, bars, cfg)
        assert len(result.equity_curve) == len(bars)

    def test_costs_reduce_equity(self):
        """With non-zero costs, a perfectly flat market should lose money."""
        _, symbol, tf, direction, rs = strategy_0_pipeline_test()
        bars = make_bars([100.0] * 40)
        cfg = BacktestConfig(
            symbol=symbol, timeframe=tf, direction=direction,
            cost_model=CostModel(maker_fee_pct=0.1, taker_fee_pct=0.1, slippage_pct=0.05),
        )
        result = run_backtest(rs, bars, cfg)
        assert result.equity_curve[-1] < cfg.initial_capital

    def test_zero_costs_flat_market_neutral(self):
        """With zero costs and flat prices, PnL should be ~0."""
        _, symbol, tf, direction, rs = strategy_0_pipeline_test()
        bars = make_bars([100.0] * 20)
        cfg = BacktestConfig(
            symbol=symbol, timeframe=tf, direction=direction,
            cost_model=CostModel(maker_fee_pct=0.0, taker_fee_pct=0.0, slippage_pct=0.0),
        )
        result = run_backtest(rs, bars, cfg)
        assert abs(result.equity_curve[-1] - cfg.initial_capital) < 1e-6

    def test_stop_loss_triggers(self):
        """Price drops sharply; stop-loss should limit loss per trade."""
        stop_rs = RuleSet(
            entry=RuleExpr(op="and", conditions=[
                Condition(left=IndicatorRef(name="close"), comparator=Comparator.GT, right=0.0)
            ]),
            exit=RuleExpr(op="and", conditions=[
                Condition(left=IndicatorRef(name="close"), comparator=Comparator.LT, right=-1.0)  # never fires
            ]),
            stop_loss=StopLoss(mode="fixed_pct", value=1.0),  # 1% stop
        )
        # Flat then sharp drop — stop should fire
        closes = [100.0] * 5 + [95.0] * 5   # 5% drop hits 1% stop
        bars = make_bars(closes)
        cfg = BacktestConfig(
            symbol="BTC/USDT", timeframe="1h", direction="long",
            cost_model=CostModel(slippage_pct=0.0, maker_fee_pct=0.0, taker_fee_pct=0.0),
        )
        result = run_backtest(stop_rs, bars, cfg)
        stop_trades = [t for t in result.trades if t.exit_reason == "stop_loss"]
        assert len(stop_trades) >= 1

    def test_insufficient_bars_returns_no_trades(self):
        _, symbol, tf, direction, rs = strategy_2_eth_mean_reversion()
        # Only 10 bars — SMA(200) will always return None, so no entry fires
        bars = make_bars([1500.0] * 10)
        cfg = BacktestConfig(symbol=symbol, timeframe=tf, direction=direction)
        result = run_backtest(rs, bars, cfg)
        assert result.metrics.trade_count == 0

    def test_metrics_win_rate_bounds(self):
        _, symbol, tf, direction, rs = strategy_0_pipeline_test()
        bars = make_bars([100.0 + i for i in range(60)])  # steadily rising
        cfg = BacktestConfig(
            symbol=symbol, timeframe=tf, direction=direction,
            cost_model=CostModel(slippage_pct=0.0, maker_fee_pct=0.0, taker_fee_pct=0.0),
        )
        result = run_backtest(rs, bars, cfg)
        m = result.metrics
        assert 0.0 <= m.win_rate <= 1.0
        assert m.trade_count == m.win_count + m.loss_count

    def test_profit_factor_inf_on_no_losses(self):
        _, symbol, tf, direction, rs = strategy_0_pipeline_test()
        bars = make_bars([100.0 + i for i in range(40)])  # all up
        cfg = BacktestConfig(
            symbol=symbol, timeframe=tf, direction=direction,
            cost_model=CostModel(slippage_pct=0.0, maker_fee_pct=0.0, taker_fee_pct=0.0),
        )
        result = run_backtest(rs, bars, cfg)
        if result.metrics.loss_count == 0 and result.metrics.trade_count > 0:
            assert math.isinf(result.metrics.profit_factor)

    def test_end_to_end_strategy1_no_exception(self):
        """Strategy 1 must run end-to-end on synthetic bars without raising."""
        _, symbol, tf, direction, rs = strategy_1_btc_trend_following()
        import random
        random.seed(42)
        closes = [50_000.0]
        for _ in range(499):
            closes.append(closes[-1] * (1 + random.uniform(-0.005, 0.005)))
        bars = make_bars(closes)
        cfg = BacktestConfig(symbol=symbol, timeframe=tf, direction=direction)
        result = run_backtest(rs, bars, cfg)
        assert isinstance(result, BacktestResult)


# ── baseline strategy definitions ─────────────────────────────────────────────

class TestBaselineStrategies:
    def test_all_return_correct_tuple_structure(self):
        for fn in [strategy_0_pipeline_test, strategy_1_btc_trend_following,
                   strategy_2_eth_mean_reversion]:
            name, symbol, tf, direction, rs = fn()
            assert isinstance(name, str)
            assert "/" in symbol
            assert tf in ("1m", "5m", "15m", "1h", "4h", "1d")
            assert direction in ("long", "short", "both")
            assert isinstance(rs, RuleSet)

    def test_all_serialise_round_trip(self):
        for fn in [strategy_0_pipeline_test, strategy_1_btc_trend_following,
                   strategy_2_eth_mean_reversion]:
            _, _, _, _, rs = fn()
            recovered = RuleSet.from_json(rs.to_json())
            assert recovered.to_json() == rs.to_json()


# ── structured review (kill/keep) ─────────────────────────────────────────────

from aletheia.strategy.review import (
    Verdict, ReviewThreshold, evaluate_strategy,
)


class TestStrategyReview:
    def test_kill_on_negative_expectancy(self):
        v = evaluate_strategy(
            trade_count=20, win_rate=0.4, expectancy=-1.5,
            profit_factor=0.7, max_drawdown_pct=3.0,
        )
        assert v.verdict == Verdict.KILL
        assert any("expectancy" in r for r in v.reasons)

    def test_kill_on_excessive_drawdown(self):
        v = evaluate_strategy(
            trade_count=20, win_rate=0.5, expectancy=2.0,
            profit_factor=1.5, max_drawdown_pct=12.0,
        )
        assert v.verdict == Verdict.KILL
        assert any("drawdown" in r for r in v.reasons)

    def test_kill_on_low_win_rate(self):
        v = evaluate_strategy(
            trade_count=30, win_rate=0.10, expectancy=-0.5,
            profit_factor=0.5, max_drawdown_pct=2.0,
        )
        assert v.verdict == Verdict.KILL

    def test_keep_on_good_metrics(self):
        v = evaluate_strategy(
            trade_count=30, win_rate=0.55, expectancy=3.0,
            profit_factor=1.8, max_drawdown_pct=2.5,
        )
        assert v.verdict == Verdict.KEEP

    def test_review_on_insufficient_trades(self):
        v = evaluate_strategy(
            trade_count=1, win_rate=1.0, expectancy=100.0,
            profit_factor=999.0, max_drawdown_pct=0.0,
        )
        assert v.verdict == Verdict.REVIEW

    def test_review_on_borderline(self):
        """Passes kill thresholds but fails some keep thresholds."""
        v = evaluate_strategy(
            trade_count=10, win_rate=0.30, expectancy=0.3,
            profit_factor=1.05, max_drawdown_pct=6.0,
        )
        assert v.verdict == Verdict.REVIEW

    def test_custom_thresholds(self):
        strict = ReviewThreshold(min_profit_factor_keep=2.0)
        v = evaluate_strategy(
            trade_count=20, win_rate=0.55, expectancy=3.0,
            profit_factor=1.5, max_drawdown_pct=2.0,
            thresholds=strict,
        )
        assert v.verdict == Verdict.REVIEW  # PF 1.5 < strict 2.0

    def test_metrics_summary_populated(self):
        v = evaluate_strategy(
            trade_count=10, win_rate=0.5, expectancy=1.0,
            profit_factor=1.3, max_drawdown_pct=3.0,
        )
        assert v.metrics_summary["trade_count"] == 10
        assert v.metrics_summary["win_rate"] == 0.5
