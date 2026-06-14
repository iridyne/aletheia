"""Backtest runner: chronological simulation over a bar series.

Single-position model (flat or in one trade at a time). No look-ahead.
Stop-loss and take-profit are checked intra-bar using the bar's high/low
before the exit signal is evaluated.

Usage:
    from aletheia.strategy.backtest import run_backtest, BacktestConfig, load_bars_from_db
    from aletheia.strategy.cost_model import CostModel

    bars = load_bars_from_db("BTC/USDT", "1h")
    result = run_backtest(ruleset, bars, BacktestConfig(symbol="BTC/USDT", timeframe="1h"))
    print(result.metrics)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import mean, stdev

from aletheia.strategy.cost_model import CostModel
from aletheia.strategy.dsl import RuleSet
from aletheia.strategy.evaluator import BarData, compute_stop_price, evaluate_at_bar


# ── config & result types ─────────────────────────────────────────────────────

@dataclass
class BacktestConfig:
    symbol: str
    timeframe: str
    direction: str = "long"           # "long" | "short" | "both"
    initial_capital: float = 1_000.0  # USDT
    position_size_pct: float = 95.0   # % of equity per trade
    cost_model: CostModel = field(default_factory=CostModel)


@dataclass
class Trade:
    entry_bar_index: int
    exit_bar_index: int
    entry_ts: int
    exit_ts: int
    direction: str
    entry_price: float
    exit_price: float
    quantity: float
    entry_fee: float
    exit_fee: float
    entry_slippage: float
    exit_slippage: float
    pnl: float
    return_pct: float
    exit_reason: str   # "signal" | "stop_loss" | "take_profit" | "end_of_data"


@dataclass
class BacktestMetrics:
    trade_count: int
    win_count: int
    loss_count: int
    win_rate: float
    total_return_pct: float
    expectancy: float        # mean PnL per trade in USDT
    profit_factor: float     # gross_profit / gross_loss (inf if no losses)
    max_drawdown_pct: float  # peak-to-trough % of equity curve
    sharpe_approx: float     # annualised approximation from per-trade returns
    avg_trade_bars: float

    def __str__(self) -> str:
        return (
            f"trades={self.trade_count}  win_rate={self.win_rate:.1%}  "
            f"expectancy={self.expectancy:+.2f}  PF={self.profit_factor:.2f}  "
            f"max_dd={self.max_drawdown_pct:.1f}%  return={self.total_return_pct:+.1f}%  "
            f"sharpe~={self.sharpe_approx:.2f}"
        )


@dataclass
class BacktestResult:
    trades: list[Trade]
    metrics: BacktestMetrics
    equity_curve: list[float]  # equity after each bar (len == len(bars))


# ── main entry point ──────────────────────────────────────────────────────────

def run_backtest(
    ruleset: RuleSet,
    bars: list[BarData],
    config: BacktestConfig,
) -> BacktestResult:
    """Run a chronological event-driven backtest. No look-ahead bias."""
    if len(bars) < 2:
        empty_metrics = _compute_metrics([], config.initial_capital, len(bars), config.timeframe)
        return BacktestResult(trades=[], metrics=empty_metrics, equity_curve=[config.initial_capital] * len(bars))

    trades: list[Trade] = []
    equity = config.initial_capital
    equity_curve: list[float] = []

    in_trade = False
    entry_bar_idx: int = 0
    entry_ts: int = 0
    entry_direction: str = config.direction if config.direction != "both" else "long"
    entry_price_filled: float = 0.0
    entry_fee_paid: float = 0.0
    entry_slip_paid: float = 0.0
    quantity: float = 0.0
    stop_price: float | None = None
    tp_price: float | None = None

    for i, bar in enumerate(bars):
        if in_trade:
            # 1. Check stop-loss and take-profit intra-bar (use high/low)
            exit_reason: str | None = None
            exit_bar_price = bar.close  # default exit at close

            if stop_price is not None:
                if entry_direction == "long" and bar.low <= stop_price:
                    exit_reason = "stop_loss"
                    exit_bar_price = stop_price
                elif entry_direction == "short" and bar.high >= stop_price:
                    exit_reason = "stop_loss"
                    exit_bar_price = stop_price

            if exit_reason is None and tp_price is not None:
                if entry_direction == "long" and bar.high >= tp_price:
                    exit_reason = "take_profit"
                    exit_bar_price = tp_price
                elif entry_direction == "short" and bar.low <= tp_price:
                    exit_reason = "take_profit"
                    exit_bar_price = tp_price

            # 2. If not stopped/TPed, check exit signal
            if exit_reason is None:
                signals = evaluate_at_bar(ruleset, bars, i)
                if signals["exit"] is True:
                    exit_reason = "signal"
                    exit_bar_price = bar.close

            # 3. Last bar force-close
            if exit_reason is None and i == len(bars) - 1:
                exit_reason = "end_of_data"
                exit_bar_price = bar.close

            if exit_reason is not None:
                fill, fee, slip = config.cost_model.exit_cost(exit_bar_price, quantity, entry_direction)
                notional_entry = entry_price_filled * quantity
                notional_exit = fill * quantity
                if entry_direction == "long":
                    pnl = notional_exit - notional_entry - entry_fee_paid - fee - entry_slip_paid - slip
                else:
                    pnl = notional_entry - notional_exit - entry_fee_paid - fee - entry_slip_paid - slip
                ret_pct = pnl / notional_entry * 100 if notional_entry else 0.0

                trades.append(Trade(
                    entry_bar_index=entry_bar_idx,
                    exit_bar_index=i,
                    entry_ts=entry_ts,
                    exit_ts=bar.ts,
                    direction=entry_direction,
                    entry_price=entry_price_filled,
                    exit_price=fill,
                    quantity=quantity,
                    entry_fee=entry_fee_paid,
                    exit_fee=fee,
                    entry_slippage=entry_slip_paid,
                    exit_slippage=slip,
                    pnl=pnl,
                    return_pct=ret_pct,
                    exit_reason=exit_reason,
                ))
                equity += pnl
                in_trade = False

        else:
            # Not in trade — check entry signal
            signals = evaluate_at_bar(ruleset, bars, i)
            want_long = (config.direction in ("long", "both")) and signals["entry"] is True
            want_short = (config.direction in ("short", "both")) and signals["entry"] is True

            if want_long or want_short:
                direction = "long" if want_long else "short"
                notional = equity * config.position_size_pct / 100
                fill, fee, slip = config.cost_model.entry_cost(bar.close, 1.0, direction)
                qty = notional / fill if fill > 0 else 0.0

                in_trade = True
                entry_bar_idx = i
                entry_ts = bar.ts
                entry_direction = direction
                entry_price_filled = fill
                entry_fee_paid = fee * qty
                entry_slip_paid = slip * qty
                quantity = qty

                # Compute stop price if specified
                window = bars[: i + 1]
                highs = [b.high for b in window]
                lows = [b.low for b in window]
                closes = [b.close for b in window]

                stop_price = None
                if ruleset.stop_loss is not None:
                    stop_price = compute_stop_price(
                        ruleset.stop_loss, fill, direction, highs, lows, closes
                    )

                tp_price = None
                if ruleset.take_profit is not None:
                    tp = ruleset.take_profit
                    if tp.mode == "fixed_pct":
                        delta = fill * tp.value / 100
                        tp_price = fill + delta if direction == "long" else fill - delta

        equity_curve.append(equity)

    metrics = _compute_metrics(trades, config.initial_capital, len(bars), config.timeframe)
    return BacktestResult(trades=trades, metrics=metrics, equity_curve=equity_curve)


# ── metrics computation ───────────────────────────────────────────────────────

_BARS_PER_YEAR = {
    "1m": 525_600,
    "5m": 105_120,
    "15m": 35_040,
    "1h": 8_760,
    "4h": 2_190,
    "1d": 365,
}


def _compute_metrics(
    trades: list[Trade],
    initial_capital: float,
    bar_count: int,
    timeframe: str,
) -> BacktestMetrics:
    if not trades:
        return BacktestMetrics(
            trade_count=0, win_count=0, loss_count=0,
            win_rate=0.0, total_return_pct=0.0, expectancy=0.0,
            profit_factor=0.0, max_drawdown_pct=0.0,
            sharpe_approx=0.0, avg_trade_bars=0.0,
        )

    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    gross_profit = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))

    total_pnl = sum(t.pnl for t in trades)
    total_return_pct = total_pnl / initial_capital * 100

    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else math.inf

    # Max drawdown from equity curve reconstruction
    equity = initial_capital
    peak = equity
    max_dd = 0.0
    for t in trades:
        equity += t.pnl
        peak = max(peak, equity)
        dd = (peak - equity) / peak * 100 if peak > 0 else 0.0
        max_dd = max(max_dd, dd)

    # Sharpe approximation (annualised, per-trade returns)
    returns = [t.return_pct / 100 for t in trades]
    bpy = _BARS_PER_YEAR.get(timeframe, 8_760)
    trades_per_year = bpy / max(bar_count / len(trades), 1) if trades else 1
    if len(returns) >= 2:
        m = mean(returns)
        sd = stdev(returns)
        sharpe = (m / sd * math.sqrt(trades_per_year)) if sd > 0 else 0.0
    else:
        sharpe = 0.0

    avg_bars = mean(t.exit_bar_index - t.entry_bar_index for t in trades) if trades else 0.0

    return BacktestMetrics(
        trade_count=len(trades),
        win_count=len(wins),
        loss_count=len(losses),
        win_rate=len(wins) / len(trades),
        total_return_pct=total_return_pct,
        expectancy=total_pnl / len(trades),
        profit_factor=profit_factor,
        max_drawdown_pct=max_dd,
        sharpe_approx=sharpe,
        avg_trade_bars=avg_bars,
    )


# ── data loading helper ───────────────────────────────────────────────────────

def load_bars_from_db(symbol: str, timeframe: str, limit: int = 500) -> list[BarData]:
    """Load OHLCV bars from the existing market_ohlcv SQLite table."""
    from aletheia.storage.repositories import get_ohlcv_candles
    candles = get_ohlcv_candles(symbol, timeframe, limit=limit)
    return [
        BarData(
            ts=int(c["ts"]),
            open=float(c["open"]),
            high=float(c["high"]),
            low=float(c["low"]),
            close=float(c["close"]),
            volume=float(c["volume"]),
        )
        for c in candles
    ]
