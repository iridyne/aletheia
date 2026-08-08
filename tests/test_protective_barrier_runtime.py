from __future__ import annotations

import json
from datetime import datetime, timezone

from aletheia.data.providers import Candle
from aletheia.portfolio.paper_account import record_position_opened
from aletheia.portfolio.protective_barrier import (
    activate_new_paper_position,
    evaluate_due_position_protection,
    persist_position_exit_plan,
)
from aletheia.storage import repositories as repo
from aletheia.storage.models import PositionExitPlanVersion
from tests.test_position_manager import _position_with_entry_chain, _snap, setup_env


def _protected_position(tmp_path, monkeypatch, *, direction="long"):
    setup_env(tmp_path, monkeypatch)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    cursor = ((now_ms // 60_000) - 1) * 60_000
    opened_at = datetime.fromtimestamp((cursor - 30_000) / 1000, timezone.utc).isoformat()
    position = _position_with_entry_chain(
        symbol="BTC/USDT",
        market_type="spot",
        direction=direction,
        quantity=1.0,
        entry_price=100.0,
        entry_fee=0.06,
        stop_price=98.0 if direction == "long" else 102.0,
        take_profit_price=110.0 if direction == "long" else 90.0,
        opened_at=opened_at,
    )
    record_position_opened(position.id)
    activation = activate_new_paper_position(position.id)
    assert activation["next_bar_open_ms"] == cursor
    return position, cursor


def _bar(cursor: int, *, high: float, low: float, exchange: str = "binance"):
    repo.upsert_ohlcv_candles(
        "BTC/USDT",
        "1m",
        [Candle(timestamp=cursor, open=100.0, high=high, low=low, close=100.0, volume=5.0)],
        provider=f"ccxt:{exchange}",
        exchange=exchange,
        market_type="spot",
    )


def test_long_wick_stop_records_trigger_and_closes_at_fresh_mark(tmp_path, monkeypatch):
    position, cursor = _protected_position(tmp_path, monkeypatch)
    _bar(cursor, high=101.0, low=97.0)
    _snap("BTC/USDT", 99.5)

    summary = evaluate_due_position_protection()

    assert summary["closed"] == 1
    trigger = repo.get_position_protection_trigger(position.id)
    assert trigger["reason"] == "stop_loss"
    assert trigger["bar_low"] == 97.0
    state = repo.get_position_protection_state(position.id)
    assert state["status"] == "materialized"
    assert state["close_fill_price"] != 98.0
    assert repo.get_position(position.id)["close_reason"] == "stop_loss"


def test_same_bar_stop_and_target_is_stop_first(tmp_path, monkeypatch):
    position, cursor = _protected_position(tmp_path, monkeypatch)
    _bar(cursor, high=111.0, low=97.0)
    _snap("BTC/USDT", 100.0)

    evaluate_due_position_protection()

    trigger = repo.get_position_protection_trigger(position.id)
    assert trigger["stop_hit"] == 1
    assert trigger["target_hit"] == 1
    assert trigger["reason"] == "stop_loss"


def test_exact_instrument_mismatch_records_gap_without_close(tmp_path, monkeypatch):
    position, cursor = _protected_position(tmp_path, monkeypatch)
    _bar(cursor, high=111.0, low=97.0, exchange="okx")

    summary = evaluate_due_position_protection()

    state = repo.get_position_protection_state(position.id)
    assert summary["closed"] == 0
    assert state["status"] == "gap"
    assert state["gap_reason"] == "exact_1m_bar_unavailable"
    assert state["next_bar_open_ms"] == cursor
    assert repo.get_position(position.id)["status"] == "open"


def test_non_crossing_bar_advances_exactly_one_minute(tmp_path, monkeypatch):
    position, cursor = _protected_position(tmp_path, monkeypatch)
    _bar(cursor, high=105.0, low=99.0)

    evaluate_due_position_protection()

    state = repo.get_position_protection_state(position.id)
    assert state["status"] == "active"
    assert state["last_evaluated_bar_open_ms"] == cursor
    assert state["next_bar_open_ms"] == cursor + 60_000
    assert repo.get_position(position.id)["status"] == "open"


def test_exit_plan_boundary_bar_is_skipped_as_explicit_gap(tmp_path, monkeypatch):
    position, cursor = _protected_position(tmp_path, monkeypatch)
    _bar(cursor, high=111.0, low=97.0)
    effective_at = datetime.fromtimestamp((cursor + 30_000) / 1000, timezone.utc).isoformat()
    payload = json.dumps(
        {
            "stop_price": 99.0,
            "take_profit_price": 110.0,
            "exit_limit_price": None,
            "exit_limit_source": None,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    current = repo.get_position(position.id)
    repo.append_position_exit_plan_version(
        PositionExitPlanVersion(
            position_id=position.id,
            version=2,
            effective_at=effective_at,
            stop_price=99.0,
            take_profit_price=110.0,
            plan_json=payload,
            plan_hash=repo.hash_position_exit_plan_json(payload),
            provenance="test_boundary",
            created_at=effective_at,
        ),
        expected_effective_at=current["exit_plan_effective_at"],
    )

    summary = evaluate_due_position_protection()

    state = repo.get_position_protection_state(position.id)
    assert summary["closed"] == 0
    assert repo.get_position_protection_trigger(position.id) is None
    assert state["status"] == "gap"
    assert state["gap_reason"] == "exit_plan_boundary_unordered"
    assert state["next_bar_open_ms"] == cursor + 60_000


def test_dynamic_stop_update_appends_plan_and_flat_projection_atomically(tmp_path, monkeypatch):
    position, _cursor = _protected_position(tmp_path, monkeypatch)
    from aletheia.portfolio.exit_plan import load_exit_plan

    plan = load_exit_plan(repo.get_position(position.id))
    plan.stop_price = 99.0
    stored = persist_position_exit_plan(
        position.id,
        plan,
        provenance="test_dynamic_stop",
    )

    current = repo.get_current_position_exit_plan(position.id)
    projected = repo.get_position(position.id)
    assert stored.version == 2
    assert current["version"] == 2
    assert current["stop_price"] == 99.0
    assert projected["stop_price"] == 99.0
    assert projected["exit_plan_version"] == 2
    assert projected["exit_plan_effective_at"] == current["effective_at"]
