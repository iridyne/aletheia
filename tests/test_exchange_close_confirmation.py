"""Regression tests for exchange-close confirmation.

Real-exchange close paths must not fabricate a fill or mark a position closed
until the exchange reports a confirmed fill. An accepted-but-unfilled close order
means local books should keep the position open and wait for reconciliation or a
later human/operator action.
"""

import importlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage import repositories as repo
from aletheia.storage.models import MarketSnapshot, Position


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
    monkeypatch.setattr("aletheia.execution.coordinator.require_uta_mutation_qualification", lambda *_args, **_kwargs: None)

def _insert_position(execution_mode: str) -> Position:
    pos = Position(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        current_price=101.0,
        execution_mode=execution_mode,
    )
    from tests.uta.test_execution_coordinator import _insert_bound_position
    _insert_bound_position(pos)
    return pos


def _patch_unconfirmed_close(monkeypatch, execution_mode, exchange_status, captured=None):
    from tests.uta.test_execution_coordinator import _Adapter
    from aletheia.uta.models import SubmissionReport, CommandEvidence, OrderStatusReport, OrderLifecycle
    async def submit(command):
        if captured is not None:
            captured.append(command)
        now = datetime.now(timezone.utc)
        return SubmissionReport(identity=command.expected_account, command_id=command.command_id, evidence=CommandEvidence.ACKNOWLEDGED, received_at=now, venue_order_id="EX-CLOSE-1", profile_version="1", order=OrderStatusReport(identity=command.expected_account, instrument=command.instrument, side=command.side, order_type=command.order_type, original_quantity=command.quantity, filled_quantity="0.005" if exchange_status == "partially_filled" else "0", state=OrderLifecycle.PARTIALLY_FILLED if exchange_status == "partially_filled" else OrderLifecycle.OPEN, received_at=now))
    monkeypatch.setattr("aletheia.execution.coordinator.create_uta_adapter", lambda mode: _Adapter(submit, mode))



@pytest.mark.parametrize(
    "exchange_status,expected_status",
    [
        ("submitted", "submitted"),
        ("partially_filled", "partially_filled"),
    ],
)
@pytest.mark.parametrize("execution_mode", ["bitget_demo", "okx_demo", "binance_testnet"])
def test_exchange_close_unconfirmed_does_not_fabricate_full_close(
    tmp_path,
    monkeypatch,
    execution_mode,
    exchange_status,
    expected_status,
):
    setup_env(tmp_path, monkeypatch)
    pos = _insert_position(execution_mode)
    captured = []
    _patch_unconfirmed_close(
        monkeypatch, execution_mode,
        exchange_status,
        captured,
    )

    from aletheia.portfolio.position_manager import close_position_routed

    result = close_position_routed(
        pos.id,
        reason="operator_close",
        decision_reasoning="Thesis changed while breadth weakened.",
        decision_actor="system_operator",
        decision_source="portfolio_review",
    )

    assert result["closed"] is False
    assert result["status"] == expected_status
    assert result["fill_quantity"] == 0

    still_open = repo._select_one("positions", pos.id)
    assert still_open["status"] == "open"
    assert still_open["pending_close_order_id"]
    assert still_open["pending_close_status"] == expected_status

    orders = [order for order in repo.list_orders() if order["position_id"] == pos.id]
    assert len(orders) == 1
    assert orders[0]["status"] == expected_status
    assert orders[0]["exchange_order_id"] == "EX-CLOSE-1"
    close_reasoning = json.loads(orders[0]["close_reasoning"])
    assert close_reasoning["actor"] == "system_operator"
    assert close_reasoning["source"] == "portfolio_review"
    assert close_reasoning["reason_code"] == "operator_close"
    assert close_reasoning["reason"] == "Thesis changed while breadth weakened."
    assert "exit_price" not in close_reasoning["trigger"]
    assert still_open["exit_reasoning"] is None
    assert repo.list_fills() == []
    assert len(captured) == 1
    assert captured[0].client_order_id == f"close-{pos.id}"

    second_result = close_position_routed(
        pos.id,
        reason="operator_close",
        decision_reasoning="A later retry must not replace the original rationale.",
        decision_actor="system_operator",
        decision_source="portfolio_review",
    )

    assert second_result["closed"] is False
    assert second_result["already_pending"] is True
    assert len(captured) == 1
    orders = [order for order in repo.list_orders() if order["position_id"] == pos.id]
    assert len(orders) == 1
    assert json.loads(orders[0]["close_reasoning"])["reason"] == (
        "Thesis changed while breadth weakened."
    )


def test_position_manager_counts_only_confirmed_exchange_closes(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    pos = Position(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        current_price=90.0,
        execution_mode="bitget_demo",
        stop_price=95.0,
    )
    repo.insert_position(pos)
    repo.insert_market_snapshot(MarketSnapshot(symbol="BTC/USDT", market_type="usdt_futures", price=90.0))

    import aletheia.portfolio.position_manager as pm

    calls = []

    def fake_close(position_id, reason, *, allow_live=False):
        calls.append((position_id, reason, allow_live))
        return {"closed": False, "status": "submitted"}

    monkeypatch.setattr(pm, "close_position_routed", fake_close)
    monkeypatch.setattr(
        pm,
        "get_fresh_price_for_protective_close",
        lambda _symbol, _market_type, *, venue: 90.0,
    )

    result = pm.evaluate_open_positions()

    assert calls
    assert result["checked"] == 1
    assert result["closed"] == 0
    assert repo._select_one("positions", pos.id)["status"] == "open"


def test_position_manager_skips_positions_with_pending_exchange_close(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    pos = Position(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        current_price=90.0,
        execution_mode="bitget_demo",
        stop_price=95.0,
        pending_close_order_id="close-order-1",
        pending_close_status="submitted",
    )
    repo.insert_position(pos)
    repo.insert_market_snapshot(MarketSnapshot(symbol="BTC/USDT", market_type="usdt_futures", price=90.0))

    import aletheia.portfolio.position_manager as pm

    calls = []

    def fake_close(position_id, reason, *, allow_live=False):
        calls.append((position_id, reason, allow_live))
        raise AssertionError("pending close should suppress repeat close routing")

    monkeypatch.setattr(pm, "close_position_routed", fake_close)

    result = pm.evaluate_open_positions()

    assert calls == []
    assert result["checked"] == 1
    assert result["closed"] == 0
    assert repo._select_one("positions", pos.id)["status"] == "open"


def test_position_manager_keeps_observing_pending_close_without_resubmitting(
    tmp_path, monkeypatch,
):
    """An unresolved exchange close must not disable protective observation."""
    setup_env(tmp_path, monkeypatch)
    pos = Position(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        current_price=90.0,
        execution_mode="bitget_demo",
        stop_price=95.0,
        pending_close_order_id="close-order-1",
        pending_close_status="submission_unknown",
        pending_close_requested_at=(
            datetime.now(timezone.utc) - timedelta(minutes=1)
        ).isoformat(),
    )
    repo.insert_position(pos)

    import aletheia.portfolio.position_manager as pm

    marks: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        pm,
        "get_fresh_price_for_protective_close",
        lambda symbol, market_type, *, venue: (
            marks.append((symbol, market_type, venue)) or 90.0
        ),
    )
    monkeypatch.setattr(pm, "_layer0_safety_exit", lambda *_args: None)
    trailing_calls: list[dict] = []
    monkeypatch.setattr(
        pm,
        "_maybe_update_trailing_stop",
        lambda *_args, **kwargs: trailing_calls.append(kwargs) or False,
    )
    monkeypatch.setattr(
        pm, "_decide_exit", lambda *_args: ("stop_loss", 90.0),
    )
    monkeypatch.setattr(
        pm,
        "close_position_routed",
        lambda *args, **kwargs: pytest.fail(
            "an unresolved close claim must suppress a second exchange submission"
        ),
    )

    result = pm.evaluate_open_positions()

    assert result["checked"] == 1
    assert marks == [("BTC/USDT", "usdt_futures", "bitget")]
    assert repo._select_one("positions", pos.id)["pending_close_status"] == (
        "submission_unknown"
    )
    assert trailing_calls and trailing_calls[0]["sync_exchange"] is False
    assert any(
        observation["code"] == "pending_close_protection_waiting"
        for observation in result["observations"]
    )
    assert all(
        error["code"] != "pending_close_protection_waiting"
        for error in result["errors"]
    )


@pytest.mark.parametrize(
    "pending_status,expected_state,expected_owner_status,requires_reconciliation",
    [
        (None, "still_open", "still open", False),
        ("error", "still_open", "still open", False),
        ("pending", "close_claimed", "close claimed; awaiting submission", False),
        ("submitted", "close_submitted", "close submitted", False),
        ("partially_filled", "partial", "partial", False),
        (
            "submission_unknown",
            "evidence_unavailable",
            "evidence unavailable",
            True,
        ),
        (
            "materialization_missing",
            "materialization_missing",
            "materialization missing",
            True,
        ),
        (
            "position_absence_unverified",
            "exchange_position_absent_verifying",
            "exchange position absent; verifying",
            True,
        ),
        (
            "submission_rejected",
            "manual_attention_required",
            "manual attention required",
            True,
        ),
    ],
)
def test_position_read_model_exposes_close_convergence_owner_truth(
    tmp_path,
    monkeypatch,
    pending_status,
    expected_state,
    expected_owner_status,
    requires_reconciliation,
):
    setup_env(tmp_path, monkeypatch)
    pos = Position(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        current_price=90.0,
        execution_mode="bitget_demo",
        pending_close_order_id="close-order-1" if pending_status else None,
        pending_close_status=pending_status,
        pending_close_requested_at=(
            (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
            if pending_status
            else None
        ),
    )
    repo.insert_position(pos)

    from aletheia.portfolio.pnl import position_realized_pnl_view

    view = position_realized_pnl_view(repo._select_one("positions", pos.id))

    assert view["close_convergence_state"] == expected_state
    assert view["close_convergence_owner_status"] == expected_owner_status
    assert view["close_convergence_requires_reconciliation"] is requires_reconciliation
    assert "close_convergence_age_hours" in view


def test_position_read_model_escalates_stale_close_claim_for_owner(
    tmp_path, monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    pos = Position(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        current_price=90.0,
        execution_mode="bitget_demo",
        pending_close_order_id="close-order-1",
        pending_close_status="submitted",
        pending_close_requested_at=(
            datetime.now(timezone.utc) - timedelta(hours=5)
        ).isoformat(),
    )
    repo.insert_position(pos)

    from aletheia.portfolio.pnl import position_realized_pnl_view

    view = position_realized_pnl_view(repo._select_one("positions", pos.id))

    assert view["close_convergence_state"] == "close_submitted"
    assert view["close_convergence_owner_status"] == "manual attention required"
    assert view["close_convergence_requires_reconciliation"] is True
    assert view["close_convergence_escalated"] is True


def test_bitget_unconfirmed_close_keeps_existing_tpsl_bracket(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.trading.exchange_tpsl_enabled = True
    save_config(cfg)

    pos = Position(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        current_price=101.0,
        execution_mode="bitget_demo",
        exchange_tpsl_order_id=json.dumps({"sl": "sl-1", "tp": "tp-1"}),
    )
    from tests.uta.test_execution_coordinator import _insert_bound_position
    _insert_bound_position(pos)
    captured = []
    _patch_unconfirmed_close(
        monkeypatch,
        "bitget_demo",
        "submitted",
        captured,
    )

    from aletheia.execution import coordinator
    result = coordinator.close_position(pos.id, reason="stop_loss")

    assert result["closed"] is False
    still_open = repo._select_one("positions", pos.id)
    assert still_open["status"] == "open"
    assert json.loads(still_open["exchange_tpsl_order_id"]) == {"sl": "sl-1", "tp": "tp-1"}
