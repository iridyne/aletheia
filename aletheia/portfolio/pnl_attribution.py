"""PnL Attribution System — explains WHERE profit/loss comes from.

Maps each settled trade's PnL to:
  - Factor attribution (trend/momentum/volatility/etc contribution)
  - Agent attribution (Trend/Macro/News/Risk Agent votes vs actual)
  - Regime attribution (bull/bear/sideways performance)
  - Temporal attribution (1h/4h/1d/1w holding period)

This closes the loop: trade → outcome → attribution → learning → better trades.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("aletheia.attribution")


@dataclass
class PnLAttribution:
    """Complete attribution for one settled trade."""

    trade_id: str
    symbol: str
    direction: str
    entry_price: float
    exit_price: float
    quantity: float
    realized_pnl: float
    hold_time_hours: float

    # Factor attribution: which scanner family drove the signal
    scanner_family: str | None = None
    factor_contributions: dict[str, float] = field(default_factory=dict)

    # Agent attribution: which Agents voted correctly
    agent_votes: dict[str, dict[str, Any]] = field(default_factory=dict)
    agent_accuracy: dict[str, bool] = field(default_factory=dict)

    # Regime at entry
    regime: str | None = None
    regime_confidence: float = 0.0

    # Market context at entry
    fear_greed: int | None = None
    atr_pct: float | None = None

    @property
    def direction_correct(self) -> bool:
        """Was the trade direction correct?"""
        if self.realized_pnl > 0:
            return True
        elif self.realized_pnl < 0:
            return False
        return True  # breakeven

    @property
    def return_pct(self) -> float:
        if self.entry_price and self.quantity:
            notional = abs(self.entry_price * self.quantity)
            return (self.realized_pnl / notional) * 100 if notional > 0 else 0.0
        return 0.0

    def to_dict(self) -> dict:
        return {
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "realized_pnl": round(self.realized_pnl, 4),
            "return_pct": round(self.return_pct, 4),
            "hold_time_hours": round(self.hold_time_hours, 1),
            "direction_correct": self.direction_correct,
            "scanner_family": self.scanner_family,
            "factor_contributions": self.factor_contributions,
            "agent_votes": self.agent_votes,
            "agent_accuracy": self.agent_accuracy,
            "regime": self.regime,
        }


def attribute_trade(
    trade: dict[str, Any],
    committee_report: dict[str, Any] | None = None,
    market_context: dict[str, Any] | None = None,
) -> PnLAttribution:
    """Compute full PnL attribution for a settled trade.

    Args:
        trade: settled trade row from the database.
        committee_report: optional committee decision report (from metadata).
        market_context: optional market context at entry time.

    Returns:
        PnLAttribution with full breakdown.
    """
    # --- Basic fields ---
    direction = trade.get("direction") or trade.get("direction_bias", "neutral")
    entry_price = float(trade.get("entry_price") or 0)
    exit_price = float(trade.get("exit_price") or 0)
    quantity = float(trade.get("quantity") or 0)
    realized_pnl = float(trade.get("realized_pnl") or 0)

    # Hold time
    opened = trade.get("opened_at") or trade.get("entry_time")
    closed = trade.get("closed_at") or trade.get("exit_time")
    hold_hours = 0.0
    if opened and closed:
        try:
            ot = datetime.fromisoformat(str(opened).replace("Z", "+00:00"))
            ct = datetime.fromisoformat(str(closed).replace("Z", "+00:00"))
            hold_hours = (ct - ot).total_seconds() / 3600
        except (ValueError, TypeError):
            pass

    attr = PnLAttribution(
        trade_id=trade.get("id", ""),
        symbol=trade.get("symbol", ""),
        direction=direction,
        entry_price=entry_price,
        exit_price=exit_price,
        quantity=quantity,
        realized_pnl=realized_pnl,
        hold_time_hours=hold_hours,
        scanner_family=trade.get("scanner_family"),
    )

    # --- Agent attribution ---
    if committee_report:
        agent_opinions = committee_report.get("agent_opinions", [])
        for opinion in agent_opinions:
            agent_name = opinion.get("agent", "?")
            agent_dir = opinion.get("direction", "neutral")
            agent_conf = opinion.get("confidence", 0)

            # Was this agent correct?
            if agent_dir == "neutral":
                correct = None  # can't judge neutral
            elif direction == "long":
                correct = agent_dir == "long" and realized_pnl > 0
            else:
                correct = agent_dir == "short" and realized_pnl > 0

            attr.agent_votes[agent_name] = {
                "direction": agent_dir,
                "confidence": agent_conf,
                "weighted_contribution": opinion.get("weighted_contribution", 0),
            }
            if correct is not None:
                attr.agent_accuracy[agent_name] = correct

    # --- Factor attribution (heuristic based on scanner family + price action) ---
    family = trade.get("scanner_family", "")
    if family and realized_pnl != 0:
        # Simple heuristic: attribute PnL to the scanner family + sub-factors
        # This could be enhanced with actual factor return decomposition
        attr.factor_contributions = _estimate_factor_contribution(
            family, direction, entry_price, exit_price, quantity, hold_hours
        )

    # --- Regime ---
    if market_context:
        attr.regime = market_context.get("regime")
        attr.regime_confidence = market_context.get("regime_confidence", 0)
        attr.fear_greed = market_context.get("fear_greed")
        attr.atr_pct = market_context.get("atr_pct")

    return attr


def _estimate_factor_contribution(
    family: str,
    direction: str,
    entry_price: float,
    exit_price: float,
    quantity: float,
    hold_hours: float,
) -> dict[str, float]:
    """Estimate factor-level PnL contribution.

    In a full implementation, this would decompose returns into
    factor exposures.  Here we use a heuristic split based on the
    scanner family's typical factor composition.
    """
    total_pnl = (exit_price - entry_price) * quantity
    if direction == "short":
        total_pnl = -total_pnl

    if total_pnl == 0:
        return {}

    # Heuristic factor splits by family
    FAMILY_FACTOR_MAP = {
        "momentum": {"trend": 0.6, "volume": 0.25, "residual": 0.15},
        "mean_reversion": {"mean_rev": 0.7, "volatility": 0.15, "residual": 0.15},
        "volatility": {"volatility": 0.6, "momentum": 0.25, "residual": 0.15},
        "derivatives": {"funding": 0.4, "oi_delta": 0.35, "residual": 0.25},
        "event": {"sentiment": 0.5, "volume": 0.3, "residual": 0.2},
        "short_bias": {"funding": 0.4, "lsr": 0.35, "residual": 0.25},
        "breakout": {"trend": 0.5, "volume": 0.35, "residual": 0.15},
    }

    factors = FAMILY_FACTOR_MAP.get(family, {"unknown": 1.0})
    return {factor: total_pnl * weight for factor, weight in factors.items()}


# --- Factor effectiveness tracking ---

@dataclass
class FactorStats:
    """Running statistics for a factor's effectiveness."""

    factor_name: str
    total_trades: int = 0
    winning_trades: int = 0
    total_pnl: float = 0.0
    avg_hold_hours: float = 0.0

    @property
    def win_rate(self) -> float:
        return self.winning_trades / max(self.total_trades, 1)

    @property
    def avg_pnl(self) -> float:
        return self.total_pnl / max(self.total_trades, 1)

    def record(self, pnl: float, hold_hours: float) -> None:
        self.total_trades += 1
        if pnl > 0:
            self.winning_trades += 1
        self.total_pnl += pnl
        # Running average
        self.avg_hold_hours = (
            self.avg_hold_hours * (self.total_trades - 1) + hold_hours
        ) / self.total_trades


# Global factor stats registry (in-memory; could be persisted)
_FACTOR_STATS: dict[str, FactorStats] = {}


def record_attribution(attr: PnLAttribution) -> None:
    """Update factor stats registry with a new attribution."""
    for factor, contribution in attr.factor_contributions.items():
        if factor == "residual":
            continue
        if factor not in _FACTOR_STATS:
            _FACTOR_STATS[factor] = FactorStats(factor_name=factor)
        _FACTOR_STATS[factor].record(
            pnl=contribution,
            hold_hours=attr.hold_time_hours,
        )


def get_factor_ranking() -> list[dict]:
    """Return factors ranked by effectiveness (win rate × avg PnL)."""
    rankings = []
    for name, stats in _FACTOR_STATS.items():
        if stats.total_trades < 3:
            continue
        edge = stats.win_rate * stats.avg_pnl
        rankings.append({
            "factor": name,
            "trades": stats.total_trades,
            "win_rate": round(stats.win_rate, 3),
            "avg_pnl": round(stats.avg_pnl, 4),
            "total_pnl": round(stats.total_pnl, 2),
            "edge": round(edge, 4),
            "avg_hold_hours": round(stats.avg_hold_hours, 1),
        })
    rankings.sort(key=lambda x: x["edge"], reverse=True)
    return rankings


def get_agent_ranking() -> list[dict]:
    """Return agents ranked by historical accuracy."""
    agent_stats: dict[str, dict] = {}
    # This would need to be computed from persisted attributions
    # For now, return empty (would need DB query)
    return []
