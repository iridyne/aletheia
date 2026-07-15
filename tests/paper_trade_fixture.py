"""Explicit prerequisites for native ``paper_internal`` approval tests.

These helpers deliberately create the same durable identity, fresh mark, and
complete entry plan required in production.  They are test data builders, not
an override of Risk behaviour.
"""

from __future__ import annotations

from aletheia.storage import repositories as repo
from aletheia.storage.models import MarketSnapshot, Opportunity


def approvable_paper_trade(
    monkeypatch,
    *,
    symbol: str,
    market_type: str,
    direction: str,
    price: float,
    opportunity_id: str | None = None,
) -> dict[str, float | str]:
    """Seed the fail-closed prerequisites for one native local-paper entry."""
    if direction == "long":
        stop_price = price * 0.98
        take_profit = price * 1.04
    elif direction == "short":
        stop_price = price * 1.02
        take_profit = price * 0.96
    else:
        raise ValueError(f"unsupported test trade direction: {direction!r}")

    opportunity = Opportunity(
        id=opportunity_id or f"test-opportunity-{symbol.replace('/', '-').lower()}",
        symbol=symbol,
        market=market_type,
        direction_bias=direction,
        status="queued",
        source_agent="test-fixture",
    )
    repo.insert_opportunity(opportunity)
    repo.insert_market_snapshot(
        MarketSnapshot(symbol=symbol, market_type=market_type, price=price)
    )
    monkeypatch.setattr(
        "aletheia.data.price_feed.get_fresh_price_for_protective_close",
        lambda _symbol, _market_type, *, venue: price,
    )
    return {
        "opportunity_id": opportunity.id,
        "stop_price": stop_price,
        "take_profit": take_profit,
        "risk_reward": 2.0,
    }
