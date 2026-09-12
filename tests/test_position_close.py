"""Tests for closing positions, realized PnL, and today's realized-PnL aggregate.

These calculation tests supply fees explicitly. Without private fill evidence,
closes remain unverified; verified economics are covered in test_outcome_closure.
"""

import sqlite3

import pytest

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import Position
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


def test_close_with_entry_fee_zero_keeps_zero_no_fallback(tmp_path, monkeypatch):
    """Sprint B (QR-2026-07-01-02): close_position must NOT fall back to re-deriving
    entry_fee from the fee schedule when entry_fee=0.0 is the stored value — truthiness
    fallback treated 0.0 as missing and inflated realized PnL by the re-derived fee."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.portfolio.pnl import close_position
    p = Position(symbol="SOLUSDT", market_type="spot", direction="long",
                 quantity=2.0, entry_price=100.0, execution_mode="bitget_demo",
                 entry_fee=0.0)
    repo.insert_position(p)

    result = close_position(p.id, exit_price=110.0, actual_exit_fee=0.132)
    expected = 20.0 - 0.132
    assert result["realized_pnl"] == pytest.approx(expected)

    closed = repo._select_one("positions", p.id)
    assert closed["entry_fee"] == 0.0  # not overwritten by re-derived value


@pytest.mark.parametrize("mode", [
    "bitget_demo", "bitget_live_limited", "okx_demo", "okx_live_limited",
    "binance_testnet", "binance_live_limited",
])
@pytest.mark.parametrize("entry_fee,exit_fee", [(None, 0.0), (0.0, None), (None, None)])
def test_missing_close_fees_are_not_estimated(tmp_path, monkeypatch, mode, entry_fee, exit_fee):
    setup_env(tmp_path, monkeypatch)
    from aletheia.portfolio.pnl import close_position

    position = Position(
        symbol="BTC/USDT", market_type="spot", direction="long",
        quantity=1.0, entry_price=100.0, execution_mode=mode, entry_fee=entry_fee,
    )
    repo.insert_position(position)
    result = close_position(position.id, exit_price=110.0, actual_exit_fee=exit_fee)
    closed = repo._select_one("positions", position.id)
    event, = repo.list_position_close_events(position_id=position.id)
    assert result["realized_pnl"] is None
    assert closed["status"] == "closed"
    assert closed["outcome_eligible"] == 0
    for row in (closed, event):
        assert row["entry_fee"] == entry_fee
        assert row["exit_fee"] == exit_fee
        assert row["realized_pnl"] is None


def test_close_long_position_realizes_profit(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.portfolio.pnl import close_position
    p = Position(symbol="BTCUSDT", market_type="spot", direction="long",
                 quantity=2.0, entry_price=100.0, execution_mode="bitget_demo", entry_fee=0.12)
    repo.insert_position(p)

    result = close_position(p.id, exit_price=110.0, actual_exit_fee=0.132)
    # gross (110-100)*2 = 20, minus round-trip fees
    expected = 20.0 - 0.12 - 0.132
    assert result["realized_pnl"] == pytest.approx(expected)

    closed = repo._select_one("positions", p.id)
    assert closed["status"] == "closed"
    assert closed["realized_pnl"] == pytest.approx(expected)
    assert closed["closed_at"] is not None
    # no longer counted as open
    assert len(repo.list_positions(status="open")) == 0


def test_close_short_position_realizes_loss(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.portfolio.pnl import close_position
    p = Position(symbol="ETHUSDT", market_type="usdt_futures", direction="short",
                 quantity=1.0, entry_price=200.0, execution_mode="bitget_demo",
                 entry_fee=0.12, funding_cost=0.0, funding_source="broker_ledger")
    repo.insert_position(p)
    # price rose against the short → gross loss = (200-220)*1 = -20, minus fees
    result = close_position(p.id, exit_price=220.0, actual_exit_fee=0.132)
    expected = -20.0 - 0.12 - 0.132
    assert result["realized_pnl"] == pytest.approx(expected)


def test_today_realized_pnl_sums_closed_positions(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.portfolio.pnl import close_position, today_realized_pnl

    p1 = Position(symbol="BTCUSDT", market_type="spot", direction="long",
                  quantity=1.0, entry_price=100.0, execution_mode="bitget_demo", entry_fee=0.06)
    p2 = Position(symbol="SOLUSDT", market_type="spot", direction="long",
                  quantity=1.0, entry_price=50.0, execution_mode="bitget_demo", entry_fee=0.03)
    repo.insert_position(p1)
    repo.insert_position(p2)
    close_position(p1.id, exit_price=90.0, actual_exit_fee=0.054)   # gross -10
    close_position(p2.id, exit_price=60.0, actual_exit_fee=0.036)   # gross +10
    # net of fees: gross sum 0, minus both round-trip fees
    sum_12 = (-10.0 - 0.06 - 0.054) + (10.0 - 0.03 - 0.036)
    assert today_realized_pnl() == pytest.approx(sum_12)

    p3 = Position(symbol="ETHUSDT", market_type="spot", direction="long",
                  quantity=2.0, entry_price=100.0, execution_mode="bitget_demo", entry_fee=0.12)
    repo.insert_position(p3)
    close_position(p3.id, exit_price=80.0, actual_exit_fee=0.096)   # gross -40
    sum_123 = sum_12 + (-40.0 - 0.12 - 0.096)
    assert today_realized_pnl() == pytest.approx(sum_123)


def test_close_demo_mode_sets_strategy_category_not_eligible(tmp_path, monkeypatch):
    """close_position() on demo/live modes: close_category='strategy' but
    outcome_eligible=False because exchange fill verification is not available."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.portfolio.pnl import close_position
    p = Position(symbol="ETH/USDT", market_type="spot", direction="long",
                 quantity=1.0, entry_price=200.0, execution_mode="okx_demo")
    repo.insert_position(p)
    close_position(p.id, exit_price=210.0, reason="stop_loss")

    row = repo._select_one("positions", p.id)
    assert row["close_category"] == "strategy"
    assert row["outcome_eligible"] == 0
    assert row["exit_fill_verified"] == 0
    assert row["exit_price_source"] == "unavailable"
    assert row["outcome_exclusion_reason"] in ("unverified_exit_fill", "missing_attribution")
