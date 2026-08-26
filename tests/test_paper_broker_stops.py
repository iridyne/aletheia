"""Tests that paper_broker carries stop_price / take_profit into the position.

Change-controlled (execution/paper_broker.py): the position must record its
protective stop/TP so the position_manager can act on it. Causal chain unchanged.
"""

import sqlite3
from types import SimpleNamespace

import pytest

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage import repositories as repo


def setup_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()


def test_paper_order_stores_stop_and_tp(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    import pytest
    from aletheia.execution.paper_broker import execute_paper_order
    from tests.execution_authority import seed_order_authority
    seed_order_authority(
        proposal_id="p1", risk_decision_id="r1", symbol="BTC/USDT",
        market_type="spot", direction="long", order_type="market",
        quantity=1.0, price=100.0, execution_mode="paper_internal",
        stop_price=98.0,
        take_profit=104.0,
    )
    # The broker may only materialize the immutable Proposal value; it must not
    # derive a new TP from fill price or mutable config after Risk approval.
    result = execute_paper_order(
        proposal_id="p1", risk_decision_id="r1",
        symbol="BTC/USDT", market_type="spot", direction="long",
        order_type="market", quantity=1.0, price=100.0,
        execution_mode="paper_internal", stop_price=98.0,
        take_profit=104.0,
    )
    pos = repo._select_one("positions", result["position_id"])
    assert pos["stop_price"] == 98.0
    assert pos["take_profit_price"] == 104.0


def test_different_proposals_keep_entry_costs_separate(tmp_path, monkeypatch):
    """A same-symbol fill cannot merge fees into another Proposal's Position."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.trading.slippage_bps = 5
    cfg.trading.execution_fee_pct = 0.1
    save_config(cfg)

    from aletheia.execution.paper_broker import execute_paper_order
    from tests.execution_authority import seed_order_authority
    seed_order_authority(
        proposal_id="p1", risk_decision_id="r1", symbol="BTC/USDT",
        market_type="spot", direction="long", order_type="market",
        quantity=1.0, price=100.0, execution_mode="paper_internal",
    )
    r1 = execute_paper_order(
        proposal_id="p1", risk_decision_id="r1",
        symbol="BTC/USDT", market_type="spot", direction="long",
        order_type="market", quantity=1.0, price=100.0,
        execution_mode="paper_internal",
    )
    pid = r1["position_id"]
    seed_order_authority(
        proposal_id="p2", risk_decision_id="r2", symbol="BTC/USDT",
        market_type="spot", direction="long", order_type="market",
        quantity=2.0, price=110.0, execution_mode="paper_internal",
    )
    r2 = execute_paper_order(
        proposal_id="p2", risk_decision_id="r2",
        symbol="BTC/USDT", market_type="spot", direction="long",
        order_type="market", quantity=2.0, price=110.0,
        execution_mode="paper_internal",
    )
    pos1 = repo._select_one("positions", pid)
    pos2 = repo._select_one("positions", r2["position_id"])
    assert r2["position_id"] != pid
    assert pos1["quantity"] == 1.0
    assert pos2["quantity"] == 2.0
    assert pos1["proposal_id"] == "p1"
    assert pos2["proposal_id"] == "p2"

    slip = 5 / 10_000.0
    fee_rate = 0.001
    fill1 = 100.0 * (1 + slip)
    fill2 = 110.0 * (1 + slip)
    assert pos1["entry_fee"] == pytest.approx(round(fill1 * fee_rate, 8))
    assert pos2["entry_fee"] == pytest.approx(round(fill2 * 2.0 * fee_rate, 8))
    assert pos1["slippage_cost"] == pytest.approx(
        round(abs(fill1 - 100.0), 8)
    )
    assert pos2["slippage_cost"] == pytest.approx(
        round(abs(fill2 - 110.0) * 2.0, 8)
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("execution_fee_pct", -0.01),
        ("execution_fee_pct", float("nan")),
        ("slippage_bps", -0.01),
        ("slippage_bps", float("inf")),
    ],
)
def test_invalid_cost_config_fails_before_paper_writes(
    tmp_path, monkeypatch, field, value,
):
    setup_env(tmp_path, monkeypatch)
    import aletheia.config.loader as loader
    from aletheia.execution.paper_broker import execute_paper_order
    from aletheia.storage.sqlite import get_db
    from tests.execution_authority import seed_order_authority

    trading = SimpleNamespace(slippage_bps=5.0, execution_fee_pct=0.06)
    setattr(trading, field, value)
    monkeypatch.setattr(
        loader,
        "load_config",
        lambda: SimpleNamespace(trading=trading),
    )
    seed_order_authority(
        proposal_id="invalid-cost-p1",
        risk_decision_id="invalid-cost-r1",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=1.0,
        price=100.0,
        execution_mode="paper_internal",
    )

    with pytest.raises(ValueError, match=field):
        execute_paper_order(
            proposal_id="invalid-cost-p1",
            risk_decision_id="invalid-cost-r1",
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            order_type="market",
            quantity=1.0,
            price=100.0,
            execution_mode="paper_internal",
        )

    counts = get_db().execute(
        """SELECT
            (SELECT COUNT(*) FROM orders),
            (SELECT COUNT(*) FROM fills),
            (SELECT COUNT(*) FROM positions),
            (SELECT COUNT(*) FROM paper_account_events),
            (SELECT COUNT(*) FROM position_exit_plan_versions)
        """
    ).fetchone()
    assert tuple(counts) == (0, 0, 0, 0, 0)


@pytest.mark.parametrize(
    ("direction", "expected_fill"),
    [("long", 100.05), ("short", 99.95)],
)
def test_valid_paper_costs_are_adverse_and_nonnegative(
    tmp_path, monkeypatch, direction, expected_fill,
):
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    from aletheia.execution.paper_broker import execute_paper_order
    from aletheia.storage import repositories as repo
    from tests.execution_authority import seed_order_authority

    cfg = load_config()
    cfg.trading.slippage_bps = 5.0
    cfg.trading.execution_fee_pct = 0.1
    save_config(cfg)
    seed_order_authority(
        proposal_id=f"valid-cost-{direction}",
        risk_decision_id=f"valid-cost-risk-{direction}",
        symbol="BTC/USDT",
        market_type="spot",
        direction=direction,
        order_type="market",
        quantity=1.0,
        price=100.0,
        execution_mode="paper_internal",
    )

    result = execute_paper_order(
        proposal_id=f"valid-cost-{direction}",
        risk_decision_id=f"valid-cost-risk-{direction}",
        symbol="BTC/USDT",
        market_type="spot",
        direction=direction,
        order_type="market",
        quantity=1.0,
        price=100.0,
        execution_mode="paper_internal",
    )

    fill = repo._select_one("fills", result["fill_id"])
    assert result["fill_price"] == expected_fill
    assert fill["fee"] >= 0.0


def test_different_proposal_keeps_its_own_protective_stop(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.execution.paper_broker import execute_paper_order
    from tests.execution_authority import seed_order_authority
    seed_order_authority(
        proposal_id="p1", risk_decision_id="r1", symbol="ETH/USDT",
        market_type="spot", direction="long", order_type="market",
        quantity=1.0, price=200.0, execution_mode="paper_internal",
        stop_price=196.0,
    )
    r1 = execute_paper_order(
        proposal_id="p1", risk_decision_id="r1",
        symbol="ETH/USDT", market_type="spot", direction="long",
        order_type="market", quantity=1.0, price=200.0,
        execution_mode="paper_internal", stop_price=196.0,
    )
    # A second Proposal on the same instrument is a separate causal position;
    # neither Proposal may rewrite the other's approved protection.
    seed_order_authority(
        proposal_id="p2", risk_decision_id="r2", symbol="ETH/USDT",
        market_type="spot", direction="long", order_type="market",
        quantity=1.0, price=210.0, execution_mode="paper_internal",
        stop_price=205.0,
    )
    r2 = execute_paper_order(
        proposal_id="p2", risk_decision_id="r2",
        symbol="ETH/USDT", market_type="spot", direction="long",
        order_type="market", quantity=1.0, price=210.0,
        execution_mode="paper_internal", stop_price=205.0,
    )
    pos1 = repo._select_one("positions", r1["position_id"])
    pos2 = repo._select_one("positions", r2["position_id"])
    assert r2["position_id"] != r1["position_id"]
    assert pos1["quantity"] == 1.0
    assert pos2["quantity"] == 1.0
    assert pos1["stop_price"] == 196.0
    assert pos2["stop_price"] == 205.0
