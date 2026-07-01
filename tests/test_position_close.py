"""Tests for closing positions, realized PnL, and today's realized-PnL aggregate.

Supports the §21.3 `max daily loss` risk check, which needs a realized-PnL source.
"""

import sqlite3

import pytest

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import Position
from aletheia.storage import repositories as repo

# Paper round-trip fee model (see portfolio/pnl.close_position):
# realized = gross_pnl - (entry_price + exit_price) * qty * PAPER_FEE_RATE
# 0.0006 = Bitget-taker 0.06% both sides — matches execution_fee_pct default and
# _EXIT_FEE_BY_MODE["paper_internal"] after the 2026-07-01 demo/live fee-parity fix
# (PR#35 P1/P3: exit fee by mode + execution_fee_pct 0.1%→0.06%).
PAPER_FEE_RATE = 0.0006


def _round_trip_fee(entry: float, exit_price: float, qty: float) -> float:
    return (entry + exit_price) * qty * PAPER_FEE_RATE


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


def test_close_long_position_realizes_profit(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.portfolio.pnl import close_position
    p = Position(symbol="BTCUSDT", market_type="spot", direction="long",
                 quantity=2.0, entry_price=100.0, execution_mode="paper_internal")
    repo.insert_position(p)

    result = close_position(p.id, exit_price=110.0)
    # gross (110-100)*2 = 20, minus round-trip fees
    expected = 20.0 - _round_trip_fee(100.0, 110.0, 2.0)
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
                 quantity=1.0, entry_price=200.0, execution_mode="paper_internal")
    repo.insert_position(p)
    # price rose against the short → gross loss = (200-220)*1 = -20, minus fees
    result = close_position(p.id, exit_price=220.0)
    expected = -20.0 - _round_trip_fee(200.0, 220.0, 1.0)
    assert result["realized_pnl"] == pytest.approx(expected)


def test_today_realized_pnl_sums_closed_positions(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.portfolio.pnl import close_position, today_realized_pnl

    p1 = Position(symbol="BTCUSDT", market_type="spot", direction="long",
                  quantity=1.0, entry_price=100.0, execution_mode="paper_internal")
    p2 = Position(symbol="SOLUSDT", market_type="spot", direction="long",
                  quantity=1.0, entry_price=50.0, execution_mode="paper_internal")
    repo.insert_position(p1)
    repo.insert_position(p2)
    close_position(p1.id, exit_price=90.0)   # gross -10
    close_position(p2.id, exit_price=60.0)   # gross +10
    # net of fees: gross sum 0, minus both round-trip fees
    sum_12 = (-10.0 - _round_trip_fee(100.0, 90.0, 1.0)) + (10.0 - _round_trip_fee(50.0, 60.0, 1.0))
    assert today_realized_pnl() == pytest.approx(sum_12)

    p3 = Position(symbol="ETHUSDT", market_type="spot", direction="long",
                  quantity=2.0, entry_price=100.0, execution_mode="paper_internal")
    repo.insert_position(p3)
    close_position(p3.id, exit_price=80.0)   # gross -40
    sum_123 = sum_12 + (-40.0 - _round_trip_fee(100.0, 80.0, 2.0))
    assert today_realized_pnl() == pytest.approx(sum_123)
