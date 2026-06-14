"""Tests that paper_broker carries stop_price / take_profit into the position.

Change-controlled (execution/paper_broker.py): the position must record its
protective stop/TP so the position_manager can act on it. Causal chain unchanged.
"""

import sqlite3

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
    # default_take_profit_pct=4.0, slippage_bps=5 (default)
    # fill_price = 100 * (1 + 5/10000) = 100.05; TP = 100.05 * 1.04 ≈ 104.052
    result = execute_paper_order(
        proposal_id="p1", risk_decision_id="r1",
        symbol="BTCUSDT", market_type="spot", direction="long",
        order_type="market", quantity=1.0, price=100.0,
        execution_mode="paper_internal", stop_price=98.0,
    )
    pos = repo._select_one("positions", result["position_id"])
    assert pos["stop_price"] == 98.0
    # TP is derived from fill_price (after slippage), not raw price
    assert pos["take_profit_price"] == pytest.approx(104.0, rel=0.01)


def test_average_in_tightens_stop_to_more_protective(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.execution.paper_broker import execute_paper_order
    r1 = execute_paper_order(
        proposal_id="p1", risk_decision_id="r1",
        symbol="ETHUSDT", market_type="spot", direction="long",
        order_type="market", quantity=1.0, price=200.0,
        execution_mode="paper_internal", stop_price=196.0,
    )
    # second fill into same position with a higher stop; for a long the tighter
    # (more protective) stop wins -> max(196, 205) = 205 (risk-reducing).
    execute_paper_order(
        proposal_id="p2", risk_decision_id="r2",
        symbol="ETHUSDT", market_type="spot", direction="long",
        order_type="market", quantity=1.0, price=210.0,
        execution_mode="paper_internal", stop_price=205.0,
    )
    pos = repo._select_one("positions", r1["position_id"])
    assert pos["quantity"] == 2.0  # averaged in
    assert pos["stop_price"] == 205.0  # tighter long stop kept
