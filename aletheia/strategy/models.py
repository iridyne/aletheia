"""Pydantic persistence models for backtest results."""
from __future__ import annotations

from pydantic import BaseModel, Field

from aletheia.storage.models import _utcnow, new_id


class BacktestRun(BaseModel):
    """One backtest execution — strategy + config + summary metrics."""
    id: str = Field(default_factory=new_id)
    strategy_id: str | None = None   # FK to strategy_records.id (optional)
    symbol: str
    timeframe: str
    direction: str
    bar_count: int
    initial_capital: float = 1_000.0
    cost_model_json: str = "{}"      # serialized CostModel dataclass
    ruleset_json: str = "{}"         # serialized RuleSet

    # Denormalised metrics for quick queries
    trade_count: int = 0
    win_rate: float = 0.0
    total_return_pct: float = 0.0
    expectancy: float = 0.0
    profit_factor: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_approx: float = 0.0
    avg_trade_bars: float = 0.0

    status: str = "completed"        # "completed" | "failed" | "running"
    error_message: str | None = None
    created_at: str = Field(default_factory=_utcnow)


class BacktestTrade(BaseModel):
    """One round-trip trade within a backtest run."""
    id: str = Field(default_factory=new_id)
    run_id: str
    trade_index: int
    direction: str
    entry_ts: int
    exit_ts: int
    entry_bar_index: int
    exit_bar_index: int
    entry_price: float
    exit_price: float
    quantity: float
    entry_fee: float = 0.0
    exit_fee: float = 0.0
    entry_slippage: float = 0.0
    exit_slippage: float = 0.0
    pnl: float = 0.0
    return_pct: float = 0.0
    exit_reason: str = "signal"
