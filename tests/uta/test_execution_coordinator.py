from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from aletheia.uta.models import (
    CommandReport,
    CommandEvidence,
    SubmissionReport,
    UTAAccountIdentity,
)


def _setup_db(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    sq._db_conn = None
    conn = sqlite3.connect(
        str(tmp_path / "app.db"), check_same_thread=False, isolation_level=None
    )
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    monkeypatch.setattr(
        "aletheia.execution.coordinator.require_uta_mutation_qualification",
        lambda _mode, **_kwargs: None,
    )
    return conn


def _authority(proposal_id: str, risk_id: str, *, order_type: str = "market"):
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import RiskDecision, TradeProposal

    proposal = TradeProposal(
        id=proposal_id,
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type=order_type,
        quantity=0.01,
        price=100.0,
        execution_mode="binance_testnet",
        status="approved",
    )
    repo.insert_trade_proposal(proposal)
    repo.insert_risk_decision(RiskDecision(
        id=risk_id,
        proposal_id=proposal_id,
        approved=True,
        allowed_execution_mode="binance_testnet",
        proposal_execution_mode="binance_testnet",
        decision_role="authoritative",
        evaluated_quantity=0.01,
        proposal_commit_hash=proposal.commit_hash,
    ))


def _identity():
    return UTAAccountIdentity(
        account_id="binance_testnet",
        execution_mode="binance_testnet",
        venue="binance",
        environment="testnet",
        profile="binance_testnet",
        credential_generation=1,
        adapter_implementation="CCXTUTAAdapter",
        adapter_version="1",
    )


class _Adapter:
    def __init__(self, submit):
        self.identity = _identity()
        self._submit = submit
        self.calls = 0

    async def submit_order(self, command):
        self.calls += 1
        return await self._submit(command)

    async def close(self):
        pass


def _ack(command, venue_order_id="venue-1"):
    return SubmissionReport(
        identity=command.expected_account,
        command_id=command.command_id,
        evidence=CommandEvidence.ACKNOWLEDGED,
        received_at=datetime.now(timezone.utc),
        client_order_id=command.client_order_id,
        venue_order_id=venue_order_id,
        profile_version="1",
    )


def _execute(proposal_id: str, risk_id: str):
    from aletheia.execution.coordinator import execute_order

    return execute_order(
        proposal_id=proposal_id,
        risk_decision_id=risk_id,
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=0.01,
        price=100.0,
        execution_mode="binance_testnet",
    )


def _fill(exchange_order_id: str, client_id: str):
    return {
        "orderId": exchange_order_id,
        "clientOid": client_id,
        "tradeId": "private-fill-1",
        "fillPrice": "101.5",
        "fillQuantity": "0.01",
        "fee": "0.001",
        "feeCoin": "USDT",
        "cTime": "1784050000000",
    }


@pytest.mark.parametrize(
    ("evidence", "reason_code", "expected"),
    (
        (CommandEvidence.NOT_SENT, "command_not_sent", "error"),
        (CommandEvidence.VENUE_REJECTED, "venue_rejected", "submission_rejected"),
        (CommandEvidence.AMBIGUOUS, None, "submission_unknown"),
        (CommandEvidence.ACKNOWLEDGED, None, "submitted"),
    ),
)
def test_command_evidence_keeps_distinct_business_state(
    evidence, reason_code, expected
):
    from aletheia.execution.coordinator import _status

    status, _reason = _status(SimpleNamespace(
        evidence=evidence,
        reason_code=reason_code,
        order=None,
    ))

    assert status == expected


def test_coordinator_commits_pending_before_uta_and_calls_once(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    proposal_id, risk_id = "proposal-1", "risk-1"
    _authority(proposal_id, risk_id)

    async def submit(command):
        from aletheia.storage import repositories as repo
        from aletheia.storage.sqlite import get_db

        [pending] = repo.list_orders(limit=10)
        assert pending["status"] == "pending"
        assert get_db().in_transaction is False
        return _ack(command)

    adapter = _Adapter(submit)
    monkeypatch.setattr(
        "aletheia.execution.coordinator.create_uta_adapter", lambda _mode: adapter
    )

    result = _execute(proposal_id, risk_id)

    assert adapter.calls == 1
    assert result["order_status"] == "submitted"
    assert result["exchange_order_id"] == "venue-1"


def test_coordinator_preserves_approved_native_protection_and_tif(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.storage import repositories as repo

    monkeypatch.setattr(repo, "validate_native_order_authority", lambda _order: None)
    transitions = []
    monkeypatch.setattr(
        "aletheia.execution.coordinator.transition_entry_plan",
        lambda plan_id, **kwargs: transitions.append((plan_id, kwargs)) or True,
    )
    proposal_id, risk_id = "proposal-protected", "risk-protected"
    _authority(proposal_id, risk_id, order_type="limit")
    captured = []

    async def submit(command):
        captured.append(command)
        return _ack(command)

    adapter = _Adapter(submit)
    from aletheia.uta.models import CapabilitySupport

    adapter.capabilities = SimpleNamespace(native_tpsl=CapabilitySupport.SUPPORTED)
    monkeypatch.setattr(
        "aletheia.execution.coordinator.create_uta_adapter", lambda _mode: adapter
    )
    from aletheia.execution.coordinator import execute_order

    execute_order(
        proposal_id=proposal_id,
        risk_decision_id=risk_id,
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="limit",
        quantity=0.01,
        price=100.0,
        execution_mode="binance_testnet",
        stop_price=95.0,
        take_profit_price=110.0,
        time_in_force="GTC",
        post_only=True,
        entry_plan_id="entry-plan-1",
        order_tactic="POST_ONLY_LIMIT",
    )

    [command] = captured
    assert command.stop_loss_price == 95
    assert command.take_profit_price == 110
    assert command.time_in_force == "GTC"
    assert command.post_only is True
    [stored] = repo.list_orders(limit=10)
    assert stored["entry_plan_id"] == "entry-plan-1"
    assert stored["order_tactic"] == "POST_ONLY_LIMIT"
    assert stored["time_in_force"] == "GTC"
    assert stored["post_only"] == 1
    assert stored["requested_liquidity"] == "maker"
    assert transitions == [
        (
            "entry-plan-1",
            {
                "expected_status": "risk_reserved",
                "status": "ordered",
                "order_id": stored["id"],
            },
        )
    ]


def test_coordinator_rolls_back_order_when_plan_link_conflicts(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.coordinator import execute_order
    from aletheia.storage import repositories as repo

    _authority("proposal-conflict", "risk-conflict", order_type="limit")
    monkeypatch.setattr(repo, "validate_native_order_authority", lambda _order: None)
    monkeypatch.setattr(
        "aletheia.execution.coordinator.transition_entry_plan",
        lambda *_args, **_kwargs: False,
    )

    with pytest.raises(RuntimeError, match="entry_plan_order_transition_conflict"):
        execute_order(
            proposal_id="proposal-conflict",
            risk_decision_id="risk-conflict",
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            order_type="limit",
            quantity=0.01,
            price=100.0,
            execution_mode="binance_testnet",
            entry_plan_id="entry-plan-conflict",
            order_tactic="POST_ONLY_LIMIT",
            time_in_force="GTC",
            post_only=True,
        )

    assert repo.list_orders(limit=10) == []


def test_stale_entry_qualification_creates_no_order_or_adapter(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.storage import repositories as repo
    from aletheia.trust.uta_qualification import UTAQualificationError

    proposal_id, risk_id = "proposal-stale", "risk-stale"
    _authority(proposal_id, risk_id)
    monkeypatch.setattr(
        "aletheia.execution.coordinator.require_uta_mutation_qualification",
        lambda _mode: (_ for _ in ()).throw(
            UTAQualificationError("qualification_code_mismatch")
        ),
    )
    monkeypatch.setattr(
        "aletheia.execution.coordinator.create_uta_adapter",
        lambda _mode: (_ for _ in ()).throw(
            AssertionError("UTA adapter must not be created")
        ),
    )

    with pytest.raises(UTAQualificationError) as caught:
        _execute(proposal_id, risk_id)

    assert caught.value.reason_code == "qualification_code_mismatch"
    assert repo.list_orders(limit=10) == []


def test_entry_sends_through_the_adapter_that_was_qualified(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    proposal_id, risk_id = "proposal-qualified-adapter", "risk-qualified-adapter"
    _authority(proposal_id, risk_id)
    qualified = []
    sent = []

    async def submit(command):
        sent.append(command.expected_account)
        return _ack(command)

    qualified_adapter = _Adapter(submit)
    sending_adapter = _Adapter(submit)
    sending_adapter.identity = UTAAccountIdentity(
        **{
            **sending_adapter.identity.__dict__,
            "credential_generation": 2,
        }
    )

    def require_qualification(_mode, adapter=None):
        qualified.append((adapter or qualified_adapter).identity)

    monkeypatch.setattr(
        "aletheia.execution.coordinator.require_uta_mutation_qualification",
        require_qualification,
    )
    monkeypatch.setattr(
        "aletheia.execution.coordinator.create_uta_adapter",
        lambda _mode: sending_adapter,
    )

    _execute(proposal_id, risk_id)

    assert sent == qualified[-1:]


def test_late_private_fill_wins_over_coordinator_ack_cas(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.exchange_materialization import materialize_bitget_entry
    from aletheia.storage import repositories as repo

    proposal_id, risk_id = "proposal-late", "risk-late"
    _authority(proposal_id, risk_id)
    entered = threading.Event()
    release = threading.Event()

    async def submit(command):
        entered.set()
        assert release.wait(timeout=5)
        return _ack(command, "venue-late")

    adapter = _Adapter(submit)
    monkeypatch.setattr(
        "aletheia.execution.coordinator.create_uta_adapter", lambda _mode: adapter
    )

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_execute, proposal_id, risk_id)
        assert entered.wait(timeout=5)
        [pending] = repo.list_orders(limit=10)
        winner = materialize_bitget_entry(
            pending["id"], [_fill("venue-late", proposal_id)]
        )
        assert winner.state == "filled"
        release.set()
        result = future.result(timeout=10)

    stored = repo.get_order(pending["id"])
    assert adapter.calls == 1
    assert stored["status"] == "filled"
    assert stored["exchange_order_id"] == "venue-late"
    assert result["order_status"] == "filled"
    assert result["fill_id"] is not None


def test_late_private_close_wins_over_ambiguous_report(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.exchange_materialization import materialize_bitget_close
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position
    from aletheia.uta.errors import UTAErrorCode

    position = Position(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        current_price=101.0,
        execution_mode="binance_testnet",
        status="open",
    )
    repo.insert_position(position)
    entered = threading.Event()
    release = threading.Event()

    async def submit(command):
        entered.set()
        assert release.wait(timeout=5)
        return SubmissionReport(
            identity=command.expected_account,
            command_id=command.command_id,
            evidence=CommandEvidence.AMBIGUOUS,
            received_at=datetime.now(timezone.utc),
            client_order_id=command.client_order_id,
            error_code=UTAErrorCode.AMBIGUOUS_COMMAND.value,
            reason_code="command_acknowledgement_unknown",
            profile_version="1",
        )

    adapter = _Adapter(submit)
    monkeypatch.setattr(
        "aletheia.execution.coordinator.create_uta_adapter", lambda _mode: adapter
    )
    from aletheia.execution.coordinator import close_position

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(close_position, position.id, "stop_loss")
        assert entered.wait(timeout=5)
        [pending] = repo.list_orders(limit=10)
        winner = materialize_bitget_close(
            pending["id"], [_fill("venue-close", pending["client_order_id"])]
        )
        assert winner.state == "filled"
        release.set()
        result = future.result(timeout=10)

    stored_order = repo.get_order(pending["id"])
    stored_position = repo.get_position(position.id)
    assert adapter.calls == 1
    assert stored_order["status"] == "filled"
    assert stored_position["status"] == "closed"
    assert result["closed"] is True


def test_venue_rejected_close_stays_claimed_and_is_not_resubmitted(
    tmp_path, monkeypatch
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.coordinator import close_position
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position
    from aletheia.uta.errors import UTAErrorCode

    position = Position(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        execution_mode="binance_testnet",
        status="open",
    )
    repo.insert_position(position)

    async def reject(command):
        return SubmissionReport(
            identity=command.expected_account,
            command_id=command.command_id,
            evidence=CommandEvidence.VENUE_REJECTED,
            received_at=datetime.now(timezone.utc),
            client_order_id=command.client_order_id,
            venue_order_id="venue-rejected-1",
            error_code=UTAErrorCode.VENUE_REJECTED.value,
            reason_code="invalid_position_mode",
            profile_version="1",
        )

    adapter = _Adapter(reject)
    monkeypatch.setattr(
        "aletheia.execution.coordinator.create_uta_adapter", lambda _mode: adapter
    )

    with pytest.raises(RuntimeError, match="invalid_position_mode"):
        close_position(position.id, "stop_loss")

    [stored_order] = repo.list_orders(limit=10)
    stored_position = repo.get_position(position.id)
    assert stored_order["status"] == "submission_rejected"
    assert stored_order["error_reason"] == "invalid_position_mode"
    assert stored_order["exchange_order_id"] == "venue-rejected-1"
    assert stored_position["pending_close_status"] == "submission_rejected"

    second = close_position(position.id, "stop_loss")

    assert second["already_pending"] is True
    assert second["status"] == "submission_rejected"
    assert adapter.calls == 1


def test_unreached_venue_rejected_close_is_owner_releasable(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.coordinator import close_position
    from aletheia.execution.pending_close_recovery import release_rate_limited_close_claim
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position
    from aletheia.uta.errors import UTAErrorCode

    position = Position(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        execution_mode="binance_testnet",
        status="open",
    )
    repo.insert_position(position)

    async def reject(command):
        return SubmissionReport(
            identity=command.expected_account,
            command_id=command.command_id,
            evidence=CommandEvidence.VENUE_REJECTED,
            received_at=datetime.now(timezone.utc),
            client_order_id=command.client_order_id,
            error_code=UTAErrorCode.VENUE_REJECTED.value,
            reason_code="invalid_position_mode",
            profile_version="1",
        )

    adapter = _Adapter(reject)
    monkeypatch.setattr(
        "aletheia.execution.coordinator.create_uta_adapter", lambda _mode: adapter
    )

    with pytest.raises(RuntimeError, match="invalid_position_mode"):
        close_position(position.id, "stop_loss")

    [stored_order] = repo.list_orders(limit=10)
    stored_position = repo.get_position(position.id)
    assert stored_order["status"] == "error"
    assert stored_order["exchange_order_id"] is None
    assert stored_position["pending_close_status"] == "submission_rejected"

    released = release_rate_limited_close_claim(
        position.id, actor="human", reason="owner fixed rejected close cause"
    )
    assert released["released"] is True
    assert repo.get_position(position.id)["pending_close_order_id"] is None
    assert adapter.calls == 1


def test_gateway_close_selector_requires_current_qualification(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    position = Position(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        execution_mode="binance_testnet",
        status="open",
    )
    repo.insert_position(position)
    selected = []
    monkeypatch.setattr(
        "aletheia.execution.coordinator.close_position",
        lambda position_id, **_kwargs: selected.append(("uta", position_id))
        or {"status": "submitted"},
    )
    monkeypatch.setattr(
        "aletheia.execution.binance_broker.close_testnet_position",
        lambda position_id, **_kwargs: selected.append(("legacy", position_id))
        or {"status": "submitted"},
    )
    from aletheia.execution import gateway

    qualification_checks = []
    monkeypatch.setattr(
        gateway,
        "_qualified_uta_mutation_selected",
        lambda _cfg, mode: qualification_checks.append(mode) or True,
    )

    monkeypatch.setattr(
        gateway,
        "load_config",
        lambda: SimpleNamespace(
            exchange=SimpleNamespace(
                uta_read_boundary_for=lambda _mode: "uta_v1"
            )
        ),
    )
    gateway.close_exchange_position(position.id, "operator_close")
    monkeypatch.setattr(
        gateway, "_qualified_uta_mutation_selected", lambda _cfg, _mode: False,
    )
    gateway.close_exchange_position(position.id, "operator_close")

    assert selected == [("uta", position.id), ("legacy", position.id)]
    assert qualification_checks == ["binance_testnet"]


def test_gateway_okx_close_selector_uses_same_qualified_coordinator(
    tmp_path, monkeypatch
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    position = Position(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        execution_mode="okx_demo",
        status="open",
    )
    repo.insert_position(position)
    selected = []
    monkeypatch.setattr(
        "aletheia.execution.coordinator.close_position",
        lambda position_id, **_kwargs: selected.append(("uta", position_id))
        or {"status": "submitted"},
    )
    monkeypatch.setattr(
        "aletheia.execution.okx_broker.close_okx_position",
        lambda position_id, **_kwargs: selected.append(("legacy", position_id))
        or {"status": "submitted"},
    )
    from aletheia.execution import gateway

    monkeypatch.setattr(
        gateway, "_qualified_uta_mutation_selected", lambda _cfg, _mode: True
    )
    gateway.close_exchange_position(position.id, "operator_close")
    monkeypatch.setattr(
        gateway, "_qualified_uta_mutation_selected", lambda _cfg, _mode: False
    )
    gateway.close_exchange_position(position.id, "operator_close")

    assert selected == [("uta", position.id), ("legacy", position.id)]


def test_gateway_bitget_close_selector_uses_same_qualified_coordinator(
    tmp_path, monkeypatch
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    position = Position(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        execution_mode="bitget_demo",
        status="open",
    )
    repo.insert_position(position)
    selected = []
    monkeypatch.setattr(
        "aletheia.execution.coordinator.close_position",
        lambda position_id, **_kwargs: selected.append(("uta", position_id))
        or {"status": "submitted"},
    )
    monkeypatch.setattr(
        "aletheia.execution.bitget_broker.close_demo_position",
        lambda position_id, **_kwargs: selected.append(("legacy", position_id))
        or {"status": "submitted"},
    )
    from aletheia.execution import gateway

    monkeypatch.setattr(
        gateway, "_qualified_uta_mutation_selected", lambda _cfg, _mode: True
    )
    gateway.close_exchange_position(position.id, "operator_close")
    monkeypatch.setattr(
        gateway, "_qualified_uta_mutation_selected", lambda _cfg, _mode: False
    )
    gateway.close_exchange_position(position.id, "operator_close")

    assert selected == [("uta", position.id), ("legacy", position.id)]


def test_gateway_live_close_selector_uses_same_qualified_coordinator(
    tmp_path, monkeypatch
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    position = Position(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        execution_mode="okx_live_limited",
        status="open",
    )
    repo.insert_position(position)
    selected = []
    monkeypatch.setattr(
        "aletheia.execution.coordinator.close_position",
        lambda position_id, **_kwargs: selected.append(("uta", position_id))
        or {"status": "submitted"},
    )
    monkeypatch.setattr(
        "aletheia.execution.okx_broker.close_okx_position",
        lambda position_id, **_kwargs: selected.append(("legacy", position_id))
        or {"status": "submitted"},
    )
    from aletheia.execution import gateway

    monkeypatch.setattr(
        gateway, "_qualified_uta_mutation_selected", lambda _cfg, _mode: True
    )
    gateway.close_exchange_position(position.id, "operator_close")
    monkeypatch.setattr(
        gateway, "_qualified_uta_mutation_selected", lambda _cfg, _mode: False
    )
    gateway.close_exchange_position(position.id, "operator_close")

    assert selected == [("uta", position.id), ("legacy", position.id)]


def test_okx_protection_commits_children_before_each_uta_send(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.coordinator import _submit_protection_leg
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    position = Position(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        stop_price=90.0,
        take_profit_price=120.0,
        execution_mode="okx_demo",
        status="open",
    )
    repo.insert_position(position)
    calls = []

    async def submit(command):
        stored = repo.list_orders(limit=10)
        assert any(row["status"] == "pending" for row in stored)
        assert repo.get_position(position.id)["status"] == "open"
        calls.append(command.purpose)
        return _ack(command, f"venue-{command.purpose}")

    adapter = _Adapter(submit)
    monkeypatch.setattr(
        "aletheia.execution.coordinator.create_uta_adapter", lambda _mode: adapter
    )

    stored_position = repo.get_position(position.id)
    result = {
        "sl": _submit_protection_leg(stored_position, "protective_stop", 90.0),
        "tp": _submit_protection_leg(stored_position, "take_profit", 120.0),
    }

    assert calls == ["protective_stop", "take_profit"]
    assert result == {
        "sl": "venue-protective_stop",
        "tp": "venue-take_profit",
    }
    assert {row["status"] for row in repo.list_orders(limit=10)} == {"submitted"}


def test_okx_stop_replacement_cancels_old_before_persisting_new(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.coordinator import replace_protective_stop
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    position = Position(
        symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        quantity=0.01, entry_price=100.0, stop_price=90.0,
        execution_mode="okx_demo", status="open",
        exchange_tpsl_order_id='{"sl":"old-sl","tp":"old-tp"}',
    )
    repo.insert_position(position)
    events = []

    class Adapter(_Adapter):
        async def cancel_order(self, command):
            events.append(("cancel", command.target_venue_order_id))
            return CommandReport(
                identity=command.expected_account, command_id=command.command_id,
                evidence=CommandEvidence.ACKNOWLEDGED,
                received_at=datetime.now(timezone.utc),
                venue_order_id=command.target_venue_order_id, profile_version="1",
            )

    async def submit(command):
        events.append(("submit", str(command.trigger_price)))
        return _ack(command, "new-sl")

    monkeypatch.setattr(
        "aletheia.execution.coordinator.create_uta_adapter",
        lambda _mode: Adapter(submit),
    )

    assert replace_protective_stop(position.id, 95.0) is True
    assert events == [("cancel", "old-sl"), ("submit", "95.0")]
    assert repo.get_position(position.id)["exchange_tpsl_order_id"] == '{"sl": "new-sl", "tp": "old-tp"}'


def test_gateway_stale_close_qualification_calls_no_backend(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position
    from aletheia.trust.uta_qualification import UTAQualificationError

    position = Position(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        execution_mode="binance_testnet",
        status="open",
    )
    repo.insert_position(position)
    from aletheia.execution import gateway

    monkeypatch.setattr(
        gateway,
        "_qualified_uta_mutation_selected",
        lambda _cfg, _mode: (_ for _ in ()).throw(
            UTAQualificationError("qualification_identity_mismatch")
        ),
    )
    monkeypatch.setattr(
        "aletheia.execution.coordinator.close_position",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("UTA backend must not be called")
        ),
    )
    monkeypatch.setattr(
        "aletheia.execution.binance_broker.close_testnet_position",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("legacy backend must not be called")
        ),
    )

    with pytest.raises(UTAQualificationError) as caught:
        gateway.close_exchange_position(position.id, "operator_close")

    assert caught.value.reason_code == "qualification_identity_mismatch"
