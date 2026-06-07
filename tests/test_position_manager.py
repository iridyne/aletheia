"""Tests for automatic position management / closing (Component 1 — the keystone).

Closing a paper position activates realized PnL, the daily-loss breaker, and the
strategy promotion gates. Governance: paper-only (live positions never auto-closed),
kill switch must NOT block closing (closing reduces risk), every close writes an
audit event, no honest exit price → skip (never fabricate an exit).
"""

import sqlite3
from datetime import datetime, timezone, timedelta

import pytest

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import Position, MarketSnapshot
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


def _snap(symbol, price):
    repo.insert_market_snapshot(MarketSnapshot(symbol=symbol, market_type="spot", price=price))


def test_migration_adds_position_columns(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    import aletheia.storage.sqlite as sq
    cols = {r["name"] for r in sq.get_db().execute("PRAGMA table_info(positions)").fetchall()}
    assert {"stop_price", "take_profit_price", "close_reason"} <= cols


def test_stop_loss_closes_long(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    p = Position(symbol="BTCUSDT", market_type="spot", direction="long",
                 quantity=1.0, entry_price=100.0, current_price=100.0,
                 execution_mode="paper_internal", stop_price=98.0, take_profit_price=110.0)
    repo.insert_position(p)
    _snap("BTCUSDT", 97.0)  # below stop

    from aletheia.portfolio.position_manager import evaluate_open_positions
    result = evaluate_open_positions()
    assert result["closed"] == 1

    row = repo._select_one("positions", p.id)
    assert row["status"] == "closed"
    assert row["close_reason"] == "stop_loss"
    # closed at stop 98 -> gross (98-100)*1 = -2, minus round-trip fees
    expected = -2.0 - (100.0 + 98.0) * 1.0 * 0.001
    assert row["realized_pnl"] == pytest.approx(expected)
    assert len(repo.list_positions(status="open")) == 0


def test_take_profit_closes_short(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    p = Position(symbol="ETHUSDT", market_type="usdt_futures", direction="short",
                 quantity=2.0, entry_price=200.0, current_price=200.0,
                 execution_mode="paper_internal", stop_price=210.0, take_profit_price=180.0)
    repo.insert_position(p)
    _snap("ETHUSDT", 179.0)  # below TP for a short -> take profit

    from aletheia.portfolio.position_manager import evaluate_open_positions
    result = evaluate_open_positions()
    assert result["closed"] == 1
    row = repo._select_one("positions", p.id)
    assert row["close_reason"] == "take_profit"
    # closed at TP 180 -> gross (200-180)*2 = 40, minus round-trip fees
    expected = 40.0 - (200.0 + 180.0) * 2.0 * 0.001
    assert row["realized_pnl"] == pytest.approx(expected)


def test_max_hold_closes(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    old = (datetime.now(timezone.utc) - timedelta(hours=100)).isoformat()
    p = Position(symbol="SOLUSDT", market_type="spot", direction="long",
                 quantity=1.0, entry_price=150.0, current_price=150.0,
                 execution_mode="paper_internal", stop_price=140.0, take_profit_price=170.0)
    p.opened_at = old
    repo.insert_position(p)
    _snap("SOLUSDT", 150.0)  # no stop/TP trigger, but past max hold

    from aletheia.portfolio.position_manager import evaluate_open_positions
    result = evaluate_open_positions()
    assert result["closed"] == 1
    row = repo._select_one("positions", p.id)
    assert row["close_reason"] == "max_hold"


def test_no_trigger_leaves_open(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    p = Position(symbol="BTCUSDT", market_type="spot", direction="long",
                 quantity=1.0, entry_price=100.0, current_price=100.0,
                 execution_mode="paper_internal", stop_price=95.0, take_profit_price=110.0)
    repo.insert_position(p)
    _snap("BTCUSDT", 100.0)

    from aletheia.portfolio.position_manager import evaluate_open_positions
    result = evaluate_open_positions()
    assert result["closed"] == 0
    assert len(repo.list_positions(status="open")) == 1


def test_close_writes_audit(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    p = Position(symbol="BTCUSDT", market_type="spot", direction="long",
                 quantity=1.0, entry_price=100.0, current_price=100.0,
                 execution_mode="paper_internal", stop_price=98.0, take_profit_price=110.0)
    repo.insert_position(p)
    _snap("BTCUSDT", 97.0)

    from aletheia.portfolio.position_manager import evaluate_open_positions
    evaluate_open_positions()

    from aletheia.files.jsonl import read_jsonl
    events = read_jsonl("audit.jsonl")
    closed = [e for e in events if e.get("event_type") == "position_closed"]
    assert closed and closed[-1]["entity_id"] == p.id
    assert closed[-1]["entity_type"] == "position"


def test_live_position_never_auto_closed(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    p = Position(symbol="BTCUSDT", market_type="spot", direction="long",
                 quantity=1.0, entry_price=100.0, current_price=100.0,
                 execution_mode="bitget_live_limited", stop_price=98.0, take_profit_price=110.0)
    repo.insert_position(p)
    _snap("BTCUSDT", 90.0)  # well below stop

    from aletheia.portfolio.position_manager import evaluate_open_positions
    result = evaluate_open_positions()
    assert result["closed"] == 0
    assert repo._select_one("positions", p.id)["status"] == "open"  # live left alone


def test_kill_switch_does_not_block_close(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config(); cfg.runtime.kill_switch_active = True; save_config(cfg)

    p = Position(symbol="BTCUSDT", market_type="spot", direction="long",
                 quantity=1.0, entry_price=100.0, current_price=100.0,
                 execution_mode="paper_internal", stop_price=98.0, take_profit_price=110.0)
    repo.insert_position(p)
    _snap("BTCUSDT", 97.0)

    from aletheia.portfolio.position_manager import evaluate_open_positions
    result = evaluate_open_positions()
    assert result["closed"] == 1  # closing reduces risk; kill switch must not block it


def test_no_price_skips(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    p = Position(symbol="BTCUSDT", market_type="spot", direction="long",
                 quantity=1.0, entry_price=100.0, current_price=100.0,
                 execution_mode="paper_internal", stop_price=98.0, take_profit_price=110.0)
    repo.insert_position(p)
    # no snapshot inserted

    from aletheia.portfolio.position_manager import evaluate_open_positions
    result = evaluate_open_positions()
    assert result["closed"] == 0  # no honest exit price -> skip, never fabricate
    assert repo._select_one("positions", p.id)["status"] == "open"


def test_config_default_stop_used_when_no_explicit_stop(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    # No explicit stop_price -> manager uses cfg.trading.default_stop_loss_pct (2%).
    p = Position(symbol="BTCUSDT", market_type="spot", direction="long",
                 quantity=1.0, entry_price=100.0, current_price=100.0,
                 execution_mode="paper_internal")
    repo.insert_position(p)
    _snap("BTCUSDT", 97.0)  # below 2% default stop (98)

    from aletheia.portfolio.position_manager import evaluate_open_positions
    result = evaluate_open_positions()
    assert result["closed"] == 1
    assert repo._select_one("positions", p.id)["close_reason"] == "stop_loss"
