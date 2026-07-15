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


def test_stamp_exit_intent_updates_position():
    """The gateway post-open stamp writes horizon + provenance onto the position."""
    from aletheia.execution.gateway import _stamp_exit_intent
    from aletheia.storage.sqlite import get_db

    get_db().execute(
        "INSERT INTO positions (id,symbol,market_type,direction,quantity,entry_price,"
        "execution_mode,status) VALUES "
        "('p1','BTC/USDT','usdt_futures','long',1.0,100.0,'paper_internal','open')"
    )
    prop = TradeProposal(
        symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        expected_horizon_hours=12.0, stop_source="operator",
    )
    _stamp_exit_intent({"position_id": "p1"}, prop)

    row = _select_one("positions", "p1")
    assert row["expected_horizon_hours"] == 12.0
    assert row["stop_source"] == "operator"


def test_stamp_noop_without_position_id():
    """A rested (unfilled) order carries no position_id → stamp is a safe no-op."""
    from aletheia.execution.gateway import _stamp_exit_intent

    prop = TradeProposal(
        symbol="X/USDT", market_type="spot", direction="long", expected_horizon_hours=5.0
    )
    # neither of these should raise
    _stamp_exit_intent({}, prop)
    _stamp_exit_intent({"position_id": None}, prop)


def test_stamp_noop_without_intent():
    """A proposal with no exit intent leaves the position untouched."""
    from aletheia.execution.gateway import _stamp_exit_intent
    from aletheia.storage.sqlite import get_db

    get_db().execute(
        "INSERT INTO positions (id,symbol,market_type,direction,quantity,entry_price,"
        "execution_mode,status) VALUES "
        "('p2','ETH/USDT','usdt_futures','long',1.0,100.0,'paper_internal','open')"
    )
    prop = TradeProposal(symbol="ETH/USDT", market_type="usdt_futures", direction="long")
    _stamp_exit_intent({"position_id": "p2"}, prop)

    row = _select_one("positions", "p2")
    assert row["expected_horizon_hours"] is None
    assert row["stop_source"] is None


def test_context_surfaces_exit_provenance():
    """M5 reback: the Operator's context shows stop provenance + horizon per position,
    so a position still on config-default risk is visible for re-authoring."""
    from aletheia.agent_shell.trading_context import _build_layer1_snapshot
    from aletheia.storage.sqlite import get_db

    get_db().execute(
        "INSERT INTO positions (id,symbol,market_type,direction,quantity,entry_price,"
        "execution_mode,status,stop_source,expected_horizon_hours,stop_price) VALUES "
        "('p1','BTC/USDT','usdt_futures','long',1.0,100.0,'paper_internal','open',"
        "'operator',12.0,97.0)"
    )
    # legacy row: no stop_source → surfaced as 'default'
    get_db().execute(
        "INSERT INTO positions (id,symbol,market_type,direction,quantity,entry_price,"
        "execution_mode,status) VALUES "
        "('p2','ETH/USDT','usdt_futures','long',1.0,100.0,'paper_internal','open')"
    )

    positions = {p["id"]: p for p in _build_layer1_snapshot().get("positions", [])}
    assert positions["p1"]["stop_source"] == "operator"
    assert positions["p1"]["expected_horizon_hours"] == 12.0
    assert positions["p1"]["stop_price"] == 97.0
    assert positions["p2"]["stop_source"] == "default"  # NULL → default (needs review)
