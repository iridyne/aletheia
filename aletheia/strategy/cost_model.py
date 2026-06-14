"""Transaction cost model for backtest simulation.

Defaults approximate realistic crypto CEX costs for a retail taker account
on major pairs (BTC/USDT, ETH/USDT) at 1h resolution.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CostModel:
    maker_fee_pct: float = 0.10   # 10 bps
    taker_fee_pct: float = 0.10   # 10 bps (assume market orders)
    slippage_pct: float = 0.05    # 5 bps conservative for liquid pairs

    def entry_cost(
        self, price: float, quantity: float, direction: str
    ) -> tuple[float, float, float]:
        """Return (fill_price, fee, slippage_amount).

        Slippage goes against you: longs fill above, shorts fill below.
        """
        slip = price * self.slippage_pct / 100
        if direction == "long":
            fill = price + slip
        else:
            fill = price - slip
        fee = fill * quantity * self.taker_fee_pct / 100
        return fill, fee, abs(slip) * quantity

    def exit_cost(
        self, price: float, quantity: float, direction: str
    ) -> tuple[float, float, float]:
        """Return (fill_price, fee, slippage_amount).

        Slippage goes against you: long exits fill below, short exits above.
        """
        slip = price * self.slippage_pct / 100
        if direction == "long":
            fill = price - slip
        else:
            fill = price + slip
        fee = fill * quantity * self.taker_fee_pct / 100
        return fill, fee, abs(slip) * quantity
