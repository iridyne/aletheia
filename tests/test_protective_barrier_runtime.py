from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from aletheia.data.providers import Candle
from aletheia.portfolio.paper_account import record_position_opened
from aletheia.portfolio.protective_barrier import (
    _trailing_qualification_assignment,
    activate_new_paper_position,
    adopt_existing_paper_positions,
    evaluate_due_position_protection,
    persist_position_exit_plan,
    run_due_position_protection_cycle,
)
from aletheia.storage import repositories as repo
from aletheia.storage.models import PositionExitPlanVersion
from tests.test_position_manager import _position_with_entry_chain, _snap, setup_env


def _protected_position(tmp_path, monkeypatch, *, direction="long"):
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.trading.trail_enabled = False
    save_config(cfg)
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
    assert repo.get_exit_breakeven_shadow_assignment(position.id) is None
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


def _set_trailing_test_defaults(*, trail_pct: float = 10.0) -> None:
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.trading.trail_enabled = True
    cfg.trading.trail_pct = trail_pct
    save_config(cfg)


def _treatment_position(
    tmp_path,
    monkeypatch,
    *,
    direction: str = "long",
    stop: float = 98.0,
    target: float = 110.0,
):
    import aletheia.portfolio.protective_barrier as barrier

    assign = barrier._trailing_qualification_assignment
    monkeypatch.setattr(
        barrier,
        "_trailing_qualification_assignment",
        lambda position_id: {**assign(position_id), "arm": "treatment"},
    )
    setup_env(tmp_path, monkeypatch)
    _set_trailing_test_defaults()
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    cursor = ((now_ms // 60_000) - 1) * 60_000
    position = _position_with_entry_chain(
        symbol="BTC/USDT",
        market_type="spot",
        direction=direction,
        quantity=1.0,
        entry_price=100.0,
        entry_fee=0.06,
        stop_price=stop,
        take_profit_price=target,
        opened_at=datetime.fromtimestamp(
            (cursor - 30_000) / 1000, timezone.utc
        ).isoformat(),
    )
    record_position_opened(position.id)
    activate_new_paper_position(position.id)
    return position, cursor


def test_new_short_term_paper_position_authors_default_trailing_plan(
    tmp_path, monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    _set_trailing_test_defaults()
    position = _position_with_entry_chain(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        entry_fee=0.06,
        stop_price=98.0,
        take_profit_price=110.0,
    )
    record_position_opened(position.id)

    activation = activate_new_paper_position(position.id)

    stored = repo.get_position(position.id)
    trailing = json.loads(stored["trailing_json"])
    immutable = json.loads(repo.get_current_position_exit_plan(position.id)["plan_json"])
    assert activation["activated"] is True
    qualification = _trailing_qualification_assignment(position.id)
    treatment = qualification["arm"] == "treatment"
    assert trailing == {
        "enabled": treatment,
        "mode": "atr" if treatment else "off",
        "distance": 1.5,
        "fallback_pct": 10.0,
        "activation_profit_pct": 1.5,
        "floor_at_breakeven": True,
        "floor_mode": "net_balance",
        "observation": "completed_1m",
        "cost_model_version": "paper-net-balance-v1",
        "exit_fee_rate": 0.0006,
        "slippage_bps": 5.0,
        "qualification": qualification,
    }
    assert immutable["trailing"] == trailing
    assert repo.get_exit_breakeven_shadow_assignment(position.id) is None


@pytest.mark.parametrize(
    ("direction", "stop", "target", "high", "low"),
    [
        ("long", 98.0, 110.0, 102.0, 99.0),
        ("short", 102.0, 90.0, 101.0, 98.0),
    ],
)
def test_completed_bar_trailing_uses_net_balance_not_entry(
    tmp_path, monkeypatch, direction, stop, target, high, low,
):
    position, cursor = _treatment_position(
        tmp_path,
        monkeypatch,
        direction=direction,
        stop=stop,
        target=target,
    )
    _bar(cursor, high=high, low=low)

    summary = evaluate_due_position_protection()

    stored = repo.get_position(position.id)
    if direction == "long":
        raw_balance = (100.0 + 0.06) / ((1 - 0.0005) * (1 - 0.0006))
        expected_balance = math.ceil(raw_balance * 100_000_000) / 100_000_000
    else:
        raw_balance = (100.0 - 0.06) / ((1 + 0.0005) * (1 + 0.0006))
        expected_balance = math.floor(raw_balance * 100_000_000) / 100_000_000
    assert summary["errors"] == []
    assert stored["stop_price"] == expected_balance
    assert (stored["stop_price"] > stored["entry_price"]) is (direction == "long")
    assert stored["trail_high_water"] == (high if direction == "long" else low)
    current_plan = repo.get_current_position_exit_plan(position.id)
    trailing = json.loads(current_plan["plan_json"])["trailing"]
    assert trailing["last_runtime_observation"]["distance_source"] == "fallback_pct"
    assert stored["exit_plan_version"] == 2
    assert stored["status"] == "open"
    assert repo.get_position_protection_trigger(position.id) is None


def test_completed_bar_trailing_uses_activation_venue_after_config_change(
    tmp_path, monkeypatch,
):
    import aletheia.portfolio.position_manager as position_manager
    from aletheia.config.loader import load_config, save_config

    position, cursor = _treatment_position(tmp_path, monkeypatch)
    frozen_venue = repo.get_position_protection_state(position.id)["venue"]
    cfg = load_config()
    cfg.market_data.ccxt_exchange = "okx"
    save_config(cfg)
    seen_venues = []
    monkeypatch.setattr(
        position_manager,
        "_get_atr_observation",
        lambda *_args, **kwargs: seen_venues.append(kwargs["protection_venue"]),
    )
    _bar(cursor, high=102.0, low=99.0, exchange=frozen_venue)

    summary = evaluate_due_position_protection()

    assert summary["errors"] == []
    assert seen_venues == [frozen_venue]


def test_trailing_cas_conflict_does_not_consume_completed_bar(tmp_path, monkeypatch):
    import aletheia.portfolio.protective_barrier as barrier

    position, cursor = _treatment_position(tmp_path, monkeypatch)
    monkeypatch.setattr(
        barrier,
        "persist_position_exit_plan",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            repo.PositionProtectionConflict("stale plan")
        ),
    )
    _bar(cursor, high=102.0, low=99.0)

    summary = evaluate_due_position_protection()

    state = repo.get_position_protection_state(position.id)
    assert summary["bars_evaluated"] == 0
    assert summary["errors"][0]["error_type"] == "PositionProtectionConflict"
    assert state["next_bar_open_ms"] == cursor
    assert state["last_evaluated_bar_open_ms"] is None


def test_net_balance_stop_includes_complete_futures_funding(monkeypatch):
    from aletheia.portfolio.position_manager import _net_balance_stop

    monkeypatch.setattr(
        "aletheia.portfolio.local_funding.compute_local_funding",
        lambda *_args, **_kwargs: SimpleNamespace(complete=True, funding_cost=0.05),
    )
    trailing = {"exit_fee_rate": 0.0006, "slippage_bps": 5.0}
    position = {
        "entry_price": 100.0,
        "quantity": 1.0,
        "entry_fee": 0.06,
        "market_type": "usdt_futures",
        "direction": "long",
    }

    balance = _net_balance_stop(
        position,
        trailing,
        observed_at="2026-08-11T00:01:00+00:00",
    )

    raw_balance = (100.0 + 0.06 + 0.05) / ((1 - 0.0005) * (1 - 0.0006))
    assert balance == math.ceil(raw_balance * 100_000_000) / 100_000_000

    monkeypatch.setattr(
        "aletheia.portfolio.local_funding.compute_local_funding",
        lambda *_args, **_kwargs: SimpleNamespace(complete=False, funding_cost=None),
    )
    assert (
        _net_balance_stop(
            position,
            trailing,
            observed_at="2026-08-11T00:01:00+00:00",
        )
        is None
    )


def test_net_balance_trailing_fails_closed_when_entry_cost_is_unavailable(
    tmp_path, monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    _set_trailing_test_defaults()
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    cursor = ((now_ms // 60_000) - 1) * 60_000
    opened_at = datetime.fromtimestamp((cursor - 30_000) / 1000, timezone.utc).isoformat()
    position = _position_with_entry_chain(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        entry_fee=0.06,
        stop_price=98.0,
        take_profit_price=110.0,
        opened_at=opened_at,
    )
    record_position_opened(position.id)
    activate_new_paper_position(position.id)
    repo.update_position(position.id, entry_fee=None)
    _bar(cursor, high=102.0, low=99.0)

    summary = evaluate_due_position_protection()

    stored = repo.get_position(position.id)
    assert summary["errors"] == []
    assert stored["stop_price"] == 98.0
    assert stored["trail_high_water"] is None
    assert stored["exit_plan_version"] == 1


def test_forward_adoption_starts_after_current_bar_without_historical_replay(
    tmp_path, monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    from aletheia.portfolio import protective_barrier

    actual_now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    adoption_ms = ((actual_now_ms // 60_000) - 3) * 60_000 + 30_000
    adoption_at = datetime.fromtimestamp(adoption_ms / 1000, timezone.utc)
    clock = [adoption_at]
    monkeypatch.setattr(protective_barrier, "_now", lambda: clock[0])
    position = _position_with_entry_chain(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        entry_fee=0.06,
        stop_price=98.0,
        take_profit_price=110.0,
        opened_at=datetime.fromtimestamp(
            (adoption_ms - 3_600_000) / 1000, timezone.utc
        ).isoformat(),
    )
    record_position_opened(position.id)
    historical_cursor = ((adoption_ms - 3_000_000) // 60_000) * 60_000
    _bar(historical_cursor, high=101.0, low=97.0)

    first = adopt_existing_paper_positions()
    second = adopt_existing_paper_positions()

    state = repo.get_position_protection_state(position.id)
    plan = repo.get_current_position_exit_plan(position.id)
    first_forward_bar = ((adoption_ms + 59_999) // 60_000) * 60_000
    assert first["adopted"] == 1
    assert second["adopted"] == 0
    assert second["already_protected"] == 1
    assert state["activated_at"] == adoption_at.isoformat()
    assert state["next_bar_open_ms"] == first_forward_bar
    assert plan["effective_at"] == adoption_at.isoformat()
    assert plan["provenance"] == "paper_forward_adoption_v1"
    assert repo.get_position(position.id)["trailing_json"] is None
    assert repo.get_exit_breakeven_shadow_assignment(position.id) is None

    evaluate_due_position_protection()
    assert repo.get_position(position.id)["status"] == "open"
    assert repo.get_position_protection_trigger(position.id) is None

    _bar(first_forward_bar, high=101.0, low=97.0)
    _snap("BTC/USDT", 97.5)
    clock[0] = datetime.fromtimestamp(
        (first_forward_bar + 65_000) / 1000, timezone.utc
    )

    summary = run_due_position_protection_cycle()

    assert summary["closed"] == 1
    assert repo.get_position(position.id)["close_reason"] == "stop_loss"


def test_non_barrier_close_supersedes_untriggered_protection(tmp_path, monkeypatch):
    position, _cursor = _protected_position(tmp_path, monkeypatch)
    closed_at = datetime.now(timezone.utc).isoformat()
    repo.update_position(
        position.id,
        status="closed",
        closed_at=closed_at,
        close_reason="max_hold",
    )

    summary = run_due_position_protection_cycle()

    state = repo.get_position_protection_state(position.id)
    assert summary["errors"] == []
    assert state["status"] == "superseded"
    assert state["updated_at"] == closed_at
    assert state["trigger_id"] is None
    assert repo.get_position_protection_trigger(position.id) is None
    assert repo.supersede_position_protection_after_close(position.id) is False


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
    from aletheia.portfolio import protective_barrier

    monkeypatch.setattr(
        protective_barrier,
        "_now",
        lambda: datetime.fromtimestamp(
            (cursor + 60_000 + 30_000) / 1000, timezone.utc
        ),
    )

    evaluate_due_position_protection()

    state = repo.get_position_protection_state(position.id)
    assert state["status"] == "active"
    assert state["last_evaluated_bar_open_ms"] == cursor
    assert state["next_bar_open_ms"] == cursor + 60_000
    assert repo.get_position(position.id)["status"] == "open"


def test_available_backlog_is_consumed_in_order_in_one_cycle(tmp_path, monkeypatch):
    position, cursor = _protected_position(tmp_path, monkeypatch)
    for offset in range(3):
        repo.upsert_ohlcv_candles(
            "BTC/USDT",
            "1m",
            [
                Candle(
                    timestamp=cursor + offset * 60_000,
                    open=100.0,
                    high=105.0,
                    low=99.0,
                    close=100.0,
                    volume=5.0,
                )
            ],
            provider="ccxt:binance",
            exchange="binance",
            market_type="spot",
        )
    from aletheia.portfolio import protective_barrier

    monkeypatch.setattr(
        protective_barrier,
        "_now",
        lambda: datetime.fromtimestamp(
            (cursor + 3 * 60_000 + 30_000) / 1000, timezone.utc
        ),
    )

    summary = evaluate_due_position_protection()

    state = repo.get_position_protection_state(position.id)
    assert summary["bars_evaluated"] == 3
    assert state["status"] == "active"
    assert state["last_evaluated_bar_open_ms"] == cursor + 2 * 60_000
    assert state["next_bar_open_ms"] == cursor + 3 * 60_000


def test_cycle_fetches_missing_exact_bar_before_evaluation(tmp_path, monkeypatch):
    position, cursor = _protected_position(tmp_path, monkeypatch)
    calls = []

    class Provider:
        async def get_ohlcv(self, instrument, *, timeframe, limit, since):
            calls.append((instrument, timeframe, limit, since))
            _bar(cursor, high=105.0, low=99.0, exchange=instrument.exchange)
            return []

        async def close(self):
            return None

    from aletheia.data import ccxt_provider

    monkeypatch.setattr(
        ccxt_provider,
        "get_market_data_provider",
        lambda *, exchange, native_ccxt, fallback_exchange: Provider(),
    )
    from aletheia.portfolio import protective_barrier

    # Keep the due window on the bar created above. Around a wall-clock minute
    # rollover the cycle can otherwise correctly advance to the next missing
    # bar and mark a gap, making this exact-bar refresh contract nondeterministic.
    monkeypatch.setattr(
        protective_barrier,
        "_now",
        lambda: datetime.fromtimestamp(
            (cursor + 60_000 + 30_000) / 1000, timezone.utc
        ),
    )

    summary = run_due_position_protection_cycle()

    assert len(calls) == 1
    instrument, timeframe, fetch_limit, since = calls[0]
    assert str(instrument) == "binance:BTC/USDT:spot"
    assert timeframe == "1m"
    assert fetch_limit >= 2
    assert since == cursor
    assert summary["refresh_requested"] == 1
    assert summary["refresh_available"] == 1
    assert summary["bars_evaluated"] == 1
    assert summary["errors"] == []
    state = repo.get_position_protection_state(position.id)
    assert state["status"] == "active"
    assert state["next_bar_open_ms"] == cursor + 60_000


def test_cycle_does_not_fetch_when_exact_bar_is_already_present(tmp_path, monkeypatch):
    _position, cursor = _protected_position(tmp_path, monkeypatch)
    _bar(cursor, high=105.0, low=99.0)

    from aletheia.data import ccxt_provider

    monkeypatch.setattr(
        ccxt_provider,
        "get_market_data_provider",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected fetch")),
    )

    summary = run_due_position_protection_cycle()

    assert summary["refresh_requested"] == 0
    assert summary["bars_evaluated"] == 1
    assert summary["errors"] == []


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


def test_dynamic_stop_rejects_stale_caller_plan(tmp_path, monkeypatch):
    position, _cursor = _protected_position(tmp_path, monkeypatch)
    from aletheia.portfolio.exit_plan import load_exit_plan

    stale = load_exit_plan(repo.get_position(position.id))
    current = load_exit_plan(repo.get_position(position.id))
    current.stop_price = 99.0
    persist_position_exit_plan(
        position.id,
        current,
        provenance="test_winning_revision",
    )

    stale.stop_price = 97.0
    with pytest.raises(
        repo.PositionProtectionConflict,
        match="caller version compare-and-set failed",
    ):
        persist_position_exit_plan(
            position.id,
            stale,
            provenance="test_stale_revision",
        )

    projected = repo.get_position(position.id)
    assert projected["exit_plan_version"] == 2
    assert projected["stop_price"] == 99.0
