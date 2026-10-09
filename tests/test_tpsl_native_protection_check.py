"""Entry-attached venue protection: read back, verify, or close (#3848, contract §4)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from aletheia.uta.models import NativeProtectionLeg
from tests.specs.spec_pipeline_tpsl_private_materialization import (
    _patch_configured_reader,
    _seed_position,
    _setup_db,
)
from tests.specs.uta_private_history import _identity
from tests.test_bitget_uta_v3_switch import _FakeOpenStrategyExchange, _install_ccxt, _strategy_row

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)


def _leg(order_id, kind, side="long", price="59000.25", qty=None):
    return NativeProtectionLeg.model_validate({
        "plan_order_id": order_id, "kind": kind, "position_side": side, "trigger_price": price, "quantity": qty,
    })


def _position(**overrides):
    position = {
        "direction": "long", "quantity": 0.01,
        "opened_at": (NOW - timedelta(hours=1)).isoformat(),
    }
    position.update(overrides)
    return position


@pytest.mark.parametrize(
    ("position", "legs", "expected"),
    [
        (_position(), [_leg("P", "sl"), _leg("P", "tp", price="62000.75")], ("verified", {"sl": "P", "tp": "P"})),
        (_position(), [_leg("S", "sl", qty="0.01"), _leg("T", "tp")], ("verified", {"sl": "S", "tp": "T"})),
        (_position(), [_leg("S", "sl"), _leg("T1", "tp"), _leg("T2", "tp")], ("verified", {"sl": "S"})),
        (_position(), [], ("absent", "native_protection_absent")),
        (_position(), [_leg("T", "tp")], ("absent", "native_protection_absent")),
        (_position(), [_leg("S", "sl", side="short")], ("absent", "native_protection_absent")),
        (_position(opened_at=(NOW - timedelta(seconds=60)).isoformat()), [], ("unverified", "native_protection_pending")),
        (_position(), [_leg("S1", "sl"), _leg("S2", "sl")], ("unverified", "native_protection_ambiguous")),
        (_position(), [_leg("S", "sl", qty="0.005")], ("unverified", "native_protection_partial")),
        (_position(direction="short"), [_leg("S", "sl", side="short", price="61000")], ("verified", {"sl": "S"})),
    ],
    ids=[
        "shared", "separate", "ambiguous_tp_dropped", "absent", "tp_only_is_absent", "other_side_is_absent",
        "pending", "ambiguous_sl", "partial_qty", "short",
    ],
)
def test_classify_native_protection(position, legs, expected):
    from aletheia.execution.stops import classify_native_protection

    assert classify_native_protection(position, legs, now=NOW) == expected


def seed_unverified_position(tmp_path, monkeypatch, *, opened_at="2026-07-13T00:00:00+00:00"):
    """A bitget_demo futures Position whose entry preset carried SL/TP but no id."""
    _setup_db(tmp_path, monkeypatch)
    from aletheia.storage import repositories as repo

    position = _seed_position("bitget_demo", suffix="native", plan_ids={}, opened_at=opened_at)
    repo.update_open_position_exit_plan(position.id, stop_price=59000.25, take_profit_price=62000.75)
    _patch_configured_reader(monkeypatch, _identity("bitget_demo"))
    return position.id


def install_venue(monkeypatch, response):
    """The real Bitget reader over a fake CCXT transport (venue response shape)."""
    exchange = _FakeOpenStrategyExchange(response)
    _install_ccxt(monkeypatch, exchange)
    return exchange


def record_closes(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "aletheia.portfolio.position_manager.close_position_routed",
        lambda position_id, reason, **kwargs: calls.append((position_id, reason, kwargs)) or {"closed": False},
    )
    return calls


def stored_ids(position_id):
    from aletheia.storage import repositories as repo

    position = repo.get_position(position_id)
    assert position is not None
    return json.loads(position["exchange_tpsl_order_id"])


def audit_types(position_id):
    from aletheia.storage.sqlite import get_db

    return [
        row[0] for row in get_db().execute(
            "SELECT event_type FROM audit_events WHERE entity_id=? AND event_type LIKE 'native_protection_%'",
            (position_id,),
        )
    ]


def test_separate_venue_ids_are_persisted(tmp_path, monkeypatch):
    from aletheia.portfolio.native_protection import verify_native_protection

    position_id = seed_unverified_position(tmp_path, monkeypatch)
    install_venue(monkeypatch, {"code": "00000", "data": [
        _strategy_row(orderId="SL-1", takeProfit=""),
        _strategy_row(orderId="TP-1", stopLoss=""),
    ]})
    closes = record_closes(monkeypatch)

    assert verify_native_protection()["verified"] == [position_id]
    assert stored_ids(position_id) == {"origin": "entry_preset", "sl": "SL-1", "tp": "TP-1"}
    assert closes == []


def test_fresh_position_with_no_venue_stop_waits_out_the_grace(tmp_path, monkeypatch):
    from aletheia.portfolio.native_protection import verify_native_protection

    position_id = seed_unverified_position(
        tmp_path, monkeypatch, opened_at=datetime.now(timezone.utc).isoformat(),
    )
    install_venue(monkeypatch, {"code": "00000", "data": []})
    closes = record_closes(monkeypatch)

    result = verify_native_protection()

    assert closes == []
    assert [e["error_type"] for e in result["errors"]] == ["native_protection_pending"]
    assert stored_ids(position_id) == {}


@pytest.mark.parametrize(
    ("explicit", "mode", "expected"),
    [
        (None, "bitget_demo", True),
        (None, "bitget_live_limited", False),
        (None, "okx_demo", False),
        (None, "okx_live_limited", False),
        (False, "bitget_demo", False),
        (True, "okx_demo", True),
    ],
)
def test_unset_flag_enables_native_protection_on_bitget_demo_only(explicit, mode, expected):
    """#3848 PR-A item 4: the default is on for bitget_demo only; an explicit value is global."""
    from aletheia.config.schema import TradingConfig
    from aletheia.execution.stops import native_tpsl_enabled

    assert TradingConfig().exchange_tpsl_enabled is None
    assert native_tpsl_enabled(SimpleNamespace(exchange_tpsl_enabled=explicit), mode) is expected


def test_flag_off_touches_no_venue(tmp_path, monkeypatch):
    from aletheia.portfolio import native_protection

    seed_unverified_position(tmp_path, monkeypatch)
    exchange = install_venue(monkeypatch, {"code": "00000", "data": []})
    closes = record_closes(monkeypatch)
    monkeypatch.setattr(
        "aletheia.config.loader.load_config",
        lambda: SimpleNamespace(trading=SimpleNamespace(exchange_tpsl_enabled=False)),
    )

    assert native_protection.verify_native_protection() == {"verified": [], "closed": [], "errors": []}
    assert exchange.calls == []
    assert closes == []


def test_different_account_writes_and_closes_nothing(tmp_path, monkeypatch):
    from aletheia.portfolio.native_protection import verify_native_protection

    position_id = seed_unverified_position(tmp_path, monkeypatch)
    # Current configuration authenticates as another account than the entry's.
    _patch_configured_reader(monkeypatch, _identity("bitget_demo").model_copy(update={"account_id": "other"}))
    install_venue(monkeypatch, {"code": "00000", "data": []})
    closes = record_closes(monkeypatch)

    result = verify_native_protection()

    assert closes == []
    assert [e["error_type"] for e in result["errors"]] == ["uta_account_identity_mismatch"]
    assert stored_ids(position_id) == {}
    assert audit_types(position_id) == []


def test_scheduler_surfaces_a_failed_protection_read(tmp_path, monkeypatch):
    from aletheia.agent_runtime import scheduler

    seed_unverified_position(tmp_path, monkeypatch)
    install_venue(monkeypatch, {"code": "40001", "data": []})
    record_closes(monkeypatch)

    result = scheduler._tpsl_fill_check_body()

    # The early "no plan candidates" return must not swallow the protection failure.
    assert result["failed_count"] == 1
    assert result["errors"][0]["error_type"] == "RuntimeError"
