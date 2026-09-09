"""Tests for M2 open-flow wiring: the Operator's exit intent (expected_horizon_hours +
stop provenance) travels decision → proposal → position.
"""

from aletheia.storage.models import TradeProposal
from aletheia.storage.repositories import _select_one, insert_trade_proposal


def test_trade_proposal_persists_exit_intent():
    """The proposal carries expected_horizon_hours + stop_source (migration + model)."""
    p = TradeProposal(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        expected_horizon_hours=8.0,
        stop_source="operator",
    )
    insert_trade_proposal(p)
    row = _select_one("trade_proposals", p.id)
    assert row["expected_horizon_hours"] == 8.0
    assert row["stop_source"] == "operator"
