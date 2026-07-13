"""Tests that paper_broker carries stop_price / take_profit into the position.

Change-controlled (execution/paper_broker.py): the position must record its
protective stop/TP so the position_manager can act on it. Causal chain unchanged.
"""

import sqlite3

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


def test_average_in_accumulates_entry_fee_and_slippage(tmp_path, monkeypatch):
    """Sprint B (QR-2026-07-01-02): a second fill must add its own entry_fees and
    slippage on top of the standing position's accumulated costs, not drop them."""
    setup_env(tmp_path, monkeypatch)
    import aletheia.config.loader as loader
    class _T:
        slippage_bps = 5
        execution_fee_pct = 0.1
        default_take_profit_pct = 4.0
    class _C:
        trading = _T()
    monkeypatch.setattr(loader, "load_config", lambda: _C())

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
    execute_paper_order(
        proposal_id="p2", risk_decision_id="r2",
        symbol="BTC/USDT", market_type="spot", direction="long",
        order_type="market", quantity=2.0, price=110.0,
        execution_mode="paper_internal",
    )
    pos = repo._select_one("positions", pid)
    assert pos["quantity"] == 3.0  # averaged fill

    slip = 5 / 10_000.0
    fee_rate = 0.001
    fill1 = 100.0 * (1 + slip)
    fill2 = 110.0 * (1 + slip)
    expected_entry_fee = round(fill1 * 1.0 * fee_rate + fill2 * 2.0 * fee_rate, 8)
    expected_slippage = round(abs(fill1 - 100.0) * 1.0 + abs(fill2 - 110.0) * 2.0, 8)

    assert pos["entry_fee"] == pytest.approx(expected_entry_fee)
    assert pos["slippage_cost"] == pytest.approx(expected_slippage)


def test_average_in_tightens_stop_to_more_protective(tmp_path, monkeypatch):
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
    # second fill into same position with a higher stop; for a long the tighter
    # (more protective) stop wins -> max(196, 205) = 205 (risk-reducing).
    seed_order_authority(
        proposal_id="p2", risk_decision_id="r2", symbol="ETH/USDT",
        market_type="spot", direction="long", order_type="market",
        quantity=1.0, price=210.0, execution_mode="paper_internal",
        stop_price=205.0,
    )
    execute_paper_order(
        proposal_id="p2", risk_decision_id="r2",
        symbol="ETH/USDT", market_type="spot", direction="long",
        order_type="market", quantity=1.0, price=210.0,
        execution_mode="paper_internal", stop_price=205.0,
    )
    pos = repo._select_one("positions", r1["position_id"])
    assert pos["quantity"] == 2.0  # averaged in
    assert pos["stop_price"] == 205.0  # tighter long stop kept
