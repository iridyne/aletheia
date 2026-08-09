"""Backtest Engine — factor + agent weight backtesting.

Fills the biggest architectural gap: no way to offline-test hypotheses
before risking real capital.

Two backtest modes:
  1. Factor backtest: "How would a pure momentum/m/... strategy have performed?"
  2. Agent weight backtest: "Which agent weight configuration works best?"

Uses existing backtest_runs + backtest_trades tables.
XAlpha inspired: Hypothesis → Backtest → Reflection → Memory.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("aletheia.backtest")


@dataclass
class BacktestConfig:
    """Configuration for a backtest run."""

    symbols: list[str] | None = None  # None = all active universe
    start_date: str | None = None  # ISO date or None = all history
    end_date: str | None = None
    initial_capital: float = 10_000.0
    fee_pct: float = 0.001  # 0.1% per trade
    slippage_bps: float = 5.0  # 5 bps slippage
    max_positions: int = 5
    position_size_pct: float = 0.02  # 2% of capital per trade
    min_confidence: float = 0.5  # minimum confidence to take a trade


@dataclass
class BacktestResult:
    """Result of a backtest run."""

    run_id: str
    config: BacktestConfig
    trade_count: int = 0
    win_count: int = 0
    loss_count: int = 0
    total_pnl: float = 0.0
    total_return_pct: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_approx: float = 0.0
    avg_trade_return: float = 0.0
    trades: list[dict] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "trade_count": self.trade_count,
            "win_rate": round(self.win_rate, 4),
            "total_return_pct": round(self.total_return_pct, 4),
            "profit_factor": round(self.profit_factor, 4),
            "max_drawdown_pct": round(self.max_drawdown_pct, 4),
            "sharpe_approx": round(self.sharpe_approx, 4),
            "avg_trade_return": round(self.avg_trade_return, 4),
        }


def backtest_factor(
    scanner_family: str,
    direction: str | None = None,
    config: BacktestConfig | None = None,
) -> BacktestResult:
    """Backtest a single scanner family's historical performance.

    Replays historical opportunities that matched the scanner family and
    simulates taking trades based on the opportunity confidence score.

    Args:
        scanner_family: which family to backtest (momentum/mean_reversion/...)
        direction: filter by direction (long/short/None for both)
        config: backtest configuration

    Returns:
        BacktestResult with performance metrics.
    """
    if config is None:
        config = BacktestConfig()

    run_id = f"bt-factor-{uuid.uuid4().hex[:12]}"
    result = BacktestResult(run_id=run_id, config=config)

    try:
        from aletheia.storage.sqlite import get_db

        db = get_db()

        # Build query: find historical opportunities from this scanner family
        # Use a simpler query without the expensive JOIN
        query = """
            SELECT o.id, o.symbol, o.direction_bias, o.confidence_score,
                   o.created_at, o.scanner_family, o.market
            FROM opportunities o
            WHERE o.scanner_family = ?
              AND o.direction_bias IS NOT NULL
              AND o.confidence_score IS NOT NULL
        """
        params: list[Any] = [scanner_family]

        if direction:
            query += " AND o.direction_bias = ?"
            params.append(direction)

        if config.symbols:
            placeholders = ", ".join("?" for _ in config.symbols)
            query += f" AND o.symbol IN ({placeholders})"
            params.extend(config.symbols)

        if config.start_date:
            query += " AND o.created_at >= ?"
            params.append(config.start_date)

        if config.end_date:
            query += " AND o.created_at <= ?"
            params.append(config.end_date)

        query += " ORDER BY o.created_at ASC LIMIT 500"

        rows = db.execute(query, params).fetchall()

        if not rows:
            logger.info("Backtest %s: no opportunities found", scanner_family)
            return result

        # Pre-load outcomes into a dict for fast lookup
        # symbol → list of (entry_ts, return_pct, direction)
        outcomes_cache: dict[str, list[tuple[str, float, str]]] = {}
        for row in rows:
            symbol = row[1]
            if symbol not in outcomes_cache:
                outcome_rows = db.execute(
                    """
                    SELECT entry_ts, forward_return_pct, direction
                    FROM universe_forward_outcomes
                    WHERE symbol = ?
                    ORDER BY entry_ts ASC
                    """,
                    (symbol,),
                ).fetchall()
                outcomes_cache[symbol] = [
                    (r[0], r[1], r[2]) for r in outcome_rows
                ]

        # Simulate trades
        capital = config.initial_capital
        peak_capital = capital
        equity_curve = [capital]

        for row in rows:
            opp_id, symbol, dir_bias, conf_score, created_at, family, market = row
            conf = (conf_score or 50) / 100.0

            if conf < config.min_confidence:
                continue

            # Find matching outcome
            ret_pct = 0.0
            symbol_outcomes = outcomes_cache.get(symbol, [])
            for entry_ts, fwd_ret, out_dir in symbol_outcomes:
                if entry_ts >= created_at and out_dir == dir_bias:
                    ret_pct = (fwd_ret or 0) / 100.0
                    break

            # Apply direction
            if dir_bias == "short":
                ret_pct = -ret_pct

            # Position sizing
            position_size = capital * config.position_size_pct
            fees = position_size * config.fee_pct * 2  # entry + exit
            slippage = position_size * (config.slippage_bps / 10_000) * 2

            trade_pnl = position_size * ret_pct - fees - slippage
            capital += trade_pnl

            # Record trade
            result.trades.append({
                "symbol": symbol,
                "direction": dir_bias,
                "confidence": conf,
                "pnl": round(trade_pnl, 4),
                "return_pct": round(ret_pct * 100, 4),
            })

            result.total_pnl += trade_pnl
            if trade_pnl > 0:
                result.win_count += 1
            else:
                result.loss_count += 1

            equity_curve.append(capital)

            # Track peak for drawdown
            if capital > peak_capital:
                peak_capital = capital

        # Compute metrics
        result.equity_curve = [round(v, 2) for v in equity_curve]
        result.trade_count = len(result.trades)
        total_trades = result.win_count + result.loss_count
        if total_trades > 0:
            result.win_rate = result.win_count / total_trades
            result.avg_trade_return = result.total_pnl / total_trades
        if config.initial_capital > 0:
            result.total_return_pct = (result.total_pnl / config.initial_capital) * 100
        if peak_capital > 0:
            min_equity = min(equity_curve) if equity_curve else config.initial_capital
            result.max_drawdown_pct = ((peak_capital - min_equity) / peak_capital) * 100

        # Profit factor
        gross_profit = sum(t["pnl"] for t in result.trades if t["pnl"] > 0)
        gross_loss = abs(sum(t["pnl"] for t in result.trades if t["pnl"] < 0))
        if gross_loss > 0:
            result.profit_factor = gross_profit / gross_loss

        # Sharpe approximation (assuming independent trades)
        if total_trades > 1:
            returns = [t["return_pct"] for t in result.trades]
            mean_ret = sum(returns) / len(returns)
            variance = sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)
            import math
            std_ret = math.sqrt(variance) if variance > 0 else 1.0
            result.sharpe_approx = (mean_ret / std_ret) * math.sqrt(252) if std_ret > 0 else 0.0

        logger.info(
            "Backtest %s/%s: %d trades, WR=%.1f%%, Return=%.1f%%, PF=%.2f",
            scanner_family, direction or "both",
            result.trade_count, result.win_rate * 100,
            result.total_return_pct, result.profit_factor,
        )

    except Exception as exc:
        logger.error("Backtest %s failed: %s", scanner_family, exc, exc_info=True)

    return result


def backtest_all_factors(
    config: BacktestConfig | None = None,
) -> list[BacktestResult]:
    """Backtest all scanner families and return ranked results."""
    families = ["momentum", "mean_reversion", "volatility", "derivatives", "event", "short_bias"]
    results = []
    for family in families:
        result = backtest_factor(family, config=config)
        if result.trade_count > 0:
            results.append(result)
    results.sort(key=lambda r: r.total_return_pct, reverse=True)
    return results


def get_factor_ranking_from_backtest(
    config: BacktestConfig | None = None,
) -> list[dict]:
    """Rank scanner families by backtest performance.

    Returns list of dicts with family name + performance metrics,
    sorted by a composite score (return × win_rate × profit_factor).
    """
    results = backtest_all_factors(config)
    ranking = []
    for r in results:
        composite = r.total_return_pct * r.win_rate * max(r.profit_factor, 0.1)
        ranking.append({
            "scanner_family": r.run_id.split("-")[2] if "-" in r.run_id else "?",
            "trades": r.trade_count,
            "win_rate": round(r.win_rate, 3),
            "total_return_pct": round(r.total_return_pct, 2),
            "profit_factor": round(r.profit_factor, 3),
            "max_drawdown_pct": round(r.max_drawdown_pct, 2),
            "sharpe": round(r.sharpe_approx, 3),
            "composite_score": round(composite, 3),
        })
    ranking.sort(key=lambda x: x["composite_score"], reverse=True)
    return ranking
