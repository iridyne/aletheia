"""SQLite CRUD for backtest_runs and backtest_trades.

Isolated from the main repositories.py (which is already 1200+ lines).
Uses the same get_db() / thread-local connection pattern.
"""
from __future__ import annotations

from aletheia.storage.sqlite import get_db
from aletheia.strategy.models import BacktestRun, BacktestTrade


def insert_backtest_run(run: BacktestRun) -> None:
    db = get_db()
    d = run.model_dump()
    db.execute(
        """
        INSERT INTO backtest_runs (
            id, strategy_id, symbol, timeframe, direction, bar_count,
            initial_capital, cost_model_json, ruleset_json,
            trade_count, win_rate, total_return_pct, expectancy,
            profit_factor, max_drawdown_pct, sharpe_approx, avg_trade_bars,
            status, error_message, created_at
        ) VALUES (
            :id, :strategy_id, :symbol, :timeframe, :direction, :bar_count,
            :initial_capital, :cost_model_json, :ruleset_json,
            :trade_count, :win_rate, :total_return_pct, :expectancy,
            :profit_factor, :max_drawdown_pct, :sharpe_approx, :avg_trade_bars,
            :status, :error_message, :created_at
        )
        """,
        d,
    )
    db.commit()


def get_backtest_run(run_id: str) -> dict | None:
    db = get_db()
    row = db.execute(
        "SELECT * FROM backtest_runs WHERE id = ?", (run_id,)
    ).fetchone()
    return dict(row) if row else None


def list_backtest_runs(
    strategy_id: str | None = None, limit: int = 50
) -> list[dict]:
    db = get_db()
    if strategy_id:
        rows = db.execute(
            "SELECT * FROM backtest_runs WHERE strategy_id = ? ORDER BY created_at DESC LIMIT ?",
            (strategy_id, limit),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM backtest_runs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def insert_backtest_trades(trades: list[BacktestTrade]) -> None:
    if not trades:
        return
    db = get_db()
    rows = [t.model_dump() for t in trades]
    db.executemany(
        """
        INSERT INTO backtest_trades (
            id, run_id, trade_index, direction,
            entry_ts, exit_ts, entry_bar_index, exit_bar_index,
            entry_price, exit_price, quantity,
            entry_fee, exit_fee, entry_slippage, exit_slippage,
            pnl, return_pct, exit_reason
        ) VALUES (
            :id, :run_id, :trade_index, :direction,
            :entry_ts, :exit_ts, :entry_bar_index, :exit_bar_index,
            :entry_price, :exit_price, :quantity,
            :entry_fee, :exit_fee, :entry_slippage, :exit_slippage,
            :pnl, :return_pct, :exit_reason
        )
        """,
        rows,
    )
    db.commit()


def list_backtest_trades(run_id: str, limit: int = 500) -> list[dict]:
    db = get_db()
    rows = db.execute(
        "SELECT * FROM backtest_trades WHERE run_id = ? ORDER BY trade_index ASC LIMIT ?",
        (run_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]
