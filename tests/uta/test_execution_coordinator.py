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
    return conn


def _authority(proposal_id: str, risk_id: str, *, order_type: str = "market", stop_price=None, take_profit=None):
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
        stop_price=stop_price, take_profit=take_profit,
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


def _identity(mode="binance_testnet"):
    venue = mode.split("_")[0]
    return UTAAccountIdentity(
        account_id=mode, execution_mode=mode, venue=venue,
        environment="live" if mode.endswith("live_limited") else "testnet" if venue == "binance" else "demo",
        profile=mode, credential_generation=1, adapter_implementation="CCXTUTAAdapter", adapter_version="1",
    )


def _insert_bound_position(position):
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Order

    entry = Order(symbol=position.symbol, market_type=position.market_type, direction=position.direction,
                  order_type="market", quantity=position.quantity, price=position.entry_price,
                  execution_mode=position.execution_mode, status="filled", source="adopted",
                  uta_account_identity_json=_identity(position.execution_mode).model_dump_json())
    repo.insert_order(entry)
    position.order_id = entry.id
    repo.insert_position(position)


class _Adapter:
    def __init__(self, submit, mode="binance_testnet"):
        self.identity = _identity(mode)
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


def _fill(exchange_order_id: str, client_id: str, *, identity, market_type, side):
    from aletheia.uta.compat import instrument_from_local
    from aletheia.uta.models import FillReport

    timestamp = datetime.fromtimestamp(1784050000, tz=timezone.utc)
    return FillReport(
        identity=identity,
        instrument=instrument_from_local("BTC/USDT", market_type),
        venue_order_id=exchange_order_id,
        client_order_id=client_id,
        venue_fill_id="private-fill-1",
        side=side,
        price="101.5", quantity="0.01", fee="0.001", fee_currency="USDT",
        executed_at=timestamp, received_at=timestamp,
    )


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
    proposal_id, risk_id = "proposal-protected", "risk-protected"
    _authority(proposal_id, risk_id, order_type="limit", stop_price=95.0, take_profit=110.0)
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


def test_coordinator_rolls_back_order_when_insert_fails(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.coordinator import execute_order
    from aletheia.storage import repositories as repo

    _authority("proposal-conflict", "risk-conflict", order_type="limit")
    monkeypatch.setattr(repo, "validate_native_order_authority", lambda _order: None)
    from aletheia.execution import coordinator
    insert = coordinator.insert_order
    def interrupted_insert(order):
        insert(order)
        raise RuntimeError("insert_interrupted")
    monkeypatch.setattr(coordinator, "insert_order", interrupted_insert)

    with pytest.raises(RuntimeError, match="insert_interrupted"):
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


@pytest.mark.parametrize("sha", ["old-head", "new-unrelated-head"])
def test_demo_mutation_needs_no_certificate_or_repository_sha(tmp_path, monkeypatch, sha):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.storage import repositories as repo
    monkeypatch.setattr("aletheia.update.git_ops.current_commit", lambda: sha)
    monkeypatch.setattr("aletheia.update.git_ops.is_worktree_dirty", lambda: True)
    assert not (tmp_path / "qualifications").exists()
    _authority("proposal-no-certificate", "risk-no-certificate")
    sent = []
    async def submit(command):
        sent.append(command.expected_account)
        return _ack(command)
    adapter = _Adapter(submit)
    monkeypatch.setattr("aletheia.execution.coordinator.create_uta_adapter", lambda _mode: adapter)
    result = _execute("proposal-no-certificate", "risk-no-certificate")
    assert sent == [adapter.identity]
    assert repo.get_order(result["order_id"])["uta_account_identity_json"] == adapter.identity.model_dump_json()


def test_missing_configured_adapter_is_recorded_before_send(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.storage import repositories as repo
    _authority("proposal-unconfigured", "risk-unconfigured")
    def unavailable(_mode):
        raise ValueError("credentials_not_configured")
    monkeypatch.setattr("aletheia.execution.coordinator.create_uta_adapter", unavailable)
    with pytest.raises(ValueError, match="credentials_not_configured"):
        _execute("proposal-unconfigured", "risk-unconfigured")
    [order] = repo.list_orders(limit=10)
    assert order["status"] == "error"
    assert order["error_reason"] == "uta_adapter_unavailable_before_submission"
    assert order["exchange_order_id"] is None


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
            pending["id"], [_fill("venue-late", proposal_id,
                                 identity=adapter.identity, market_type="spot", side="buy")]
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
    _insert_bound_position(position)
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
        [pending] = [row for row in repo.list_orders(limit=10) if row["position_id"] == position.id]
        winner = materialize_bitget_close(
            pending["id"], [_fill("venue-close", pending["client_order_id"],
                                 identity=adapter.identity, market_type="spot", side="sell")]
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
    _insert_bound_position(position)

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

    [stored_order] = [row for row in repo.list_orders(limit=10) if row["position_id"] == position.id]
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
    _insert_bound_position(position)

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

    [stored_order] = [row for row in repo.list_orders(limit=10) if row["position_id"] == position.id]
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


@pytest.mark.parametrize("mode", ["binance_testnet", "okx_demo", "bitget_demo", "binance_live_limited", "okx_live_limited", "bitget_live_limited"])
def test_external_gateway_close_uses_bound_account(tmp_path, monkeypatch, mode):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution import gateway
    from aletheia.storage.models import Position
    from aletheia.storage import repositories as repo

    position = Position(symbol="BTC/USDT", market_type="spot", direction="long", quantity=0.01,
                        entry_price=100.0, execution_mode=mode, status="open")
    _insert_bound_position(position)
    async def submit(command):
        assert command.expected_account == _identity(mode)
        return _ack(command)
    adapter = _Adapter(submit)
    adapter.identity = _identity(mode)
    monkeypatch.setattr("aletheia.execution.coordinator.create_uta_adapter", lambda _mode: adapter)
    result = gateway.close_exchange_position(position.id, "operator_close")
    assert result["closed"] is False
    assert adapter.calls == 1
    assert gateway.close_exchange_position(position.id, "operator_close")["already_pending"] is True
    assert adapter.calls == 1



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
    _insert_bound_position(position)
    calls = []

    async def submit(command):
        stored = repo.list_orders(limit=10)
        assert any(row["status"] == "pending" for row in stored)
        assert repo.get_position(position.id)["status"] == "open"
        calls.append(command.purpose)
        return _ack(command, f"venue-{command.purpose}")

    adapter = _Adapter(submit, "okx_demo")
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
    assert {row["status"] for row in repo.list_orders(limit=10) if row["position_id"] == position.id} == {"submitted"}


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
    _insert_bound_position(position)
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
        lambda _mode: Adapter(submit, "okx_demo"),
    )

    assert replace_protective_stop(position.id, 95.0) is True
    assert events == [("cancel", "old-sl"), ("submit", "95.0")]
    assert repo.get_position(position.id)["exchange_tpsl_order_id"] == '{"sl": "new-sl", "tp": "old-tp"}'


@pytest.mark.parametrize("mode", ["binance_testnet", "okx_demo", "bitget_demo", "binance_live_limited", "okx_live_limited", "bitget_live_limited"])
def test_six_mode_commands_use_real_uta_profiles_and_durable_identity(tmp_path, monkeypatch, mode):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution import coordinator, gateway
    from aletheia.execution.exchange_materialization import materialize_bitget_entry
    from aletheia.storage import repositories as repo
    from aletheia.uta.ccxt_adapter import CCXTUTAAdapter
    from aletheia.uta.compat import instrument_from_local
    from aletheia.uta.models import CancelOrderCommand
    from aletheia.uta.profiles.binance import BinanceTestnetProfile, BinanceLiveProfile
    from aletheia.uta.profiles.okx import OKXDemoProfile, OKXLiveProfile
    from aletheia.uta.profiles.bitget import BitgetUTAV3Profile, BitgetUTAV3LiveProfile
    from tests.uta.test_ccxt_adapter import FakeExchange, _identity as profile_identity, _install
    from tests.execution_authority import seed_order_authority

    profile_type = dict(binance_testnet=BinanceTestnetProfile, binance_live_limited=BinanceLiveProfile,
                        okx_demo=OKXDemoProfile, okx_live_limited=OKXLiveProfile,
                        bitget_demo=BitgetUTAV3Profile, bitget_live_limited=BitgetUTAV3LiveProfile)[mode]
    credentials = dict(api_key="key", secret="secret")
    if not mode.startswith("binance"):
        credentials["passphrase"] = "passphrase"
    profile = profile_type(**credentials)
    identity = profile_identity(profile).model_copy(update={"execution_mode": mode})
    exchange = FakeExchange()
    async def create_order(symbol, order_type, side, amount, price, params):
        exchange.events.append(("create_order", side, params))
        return dict(id=f"venue-{len(exchange.events)}", symbol=symbol, side=side, type=order_type,
                    amount=amount, filled="0", remaining=amount, status="open")
    exchange.create_order = create_order
    exchange.cancel_response = {"id": "entry-venue", "status": "canceled"}
    _install(monkeypatch, exchange)
    monkeypatch.setattr(coordinator, "create_uta_adapter", lambda _mode: CCXTUTAAdapter(identity, profile))
    proposal, decision = seed_order_authority(
        proposal_id="six-entry", risk_decision_id="six-risk", symbol="BTC/USDT",
        market_type="usdt_futures", direction="long", order_type="market", quantity=0.01,
        price=100.0, execution_mode=mode,
    )
    result = coordinator.execute_order(proposal_id=proposal.id, risk_decision_id=decision.id,
                                      symbol="BTC/USDT", market_type="usdt_futures", direction="long",
                                      order_type="market", quantity=0.01, price=100.0, execution_mode=mode,
                                      leverage=2)
    order = repo.get_order(result["order_id"])
    assert order["status"] == "submitted"
    assert order["proposal_id"] == proposal.id and order["risk_decision_id"] == decision.id
    assert UTAAccountIdentity.model_validate_json(order["uta_account_identity_json"]) == identity
    cancel = CancelOrderCommand(command_id="cancel-six", expected_account=identity,
                                instrument=instrument_from_local("BTC/USDT", "usdt_futures"),
                                purpose="entry", target_venue_order_id=order["exchange_order_id"], reason="test")
    assert coordinator.cancel_order(cancel).evidence is CommandEvidence.ACKNOWLEDGED
    materialized = materialize_bitget_entry(order["id"], [_fill(
        order["exchange_order_id"], proposal.id,
        identity=identity, market_type="usdt_futures", side="buy",
    )])
    assert materialized.state == "filled"
    [position] = repo.list_positions(status="open")
    repo.update_open_position_exit_plan(position["id"], stop_price=90.0, take_profit_price=120.0)
    protection = gateway.place_exchange_protection(position["id"])
    if mode.startswith("binance"):
        assert protection == {}  # existing profile explicitly lacks trigger orders
    else:
        assert set(protection) == {"sl", "tp"}
        assert gateway.replace_exchange_protective_stop(position["id"], 95.0) is True
    closed = gateway.close_exchange_position(position["id"], "operator_close")
    assert closed["closed"] is False
    assert repo.get_position(position["id"])["status"] == "open"
    assert repo.get_order(closed["order_id"])["uta_account_identity_json"] == order["uta_account_identity_json"]
    previous_events = list(exchange.events)
    drifted = identity.model_copy(update={"credential_generation": identity.credential_generation + 1})
    monkeypatch.setattr(coordinator, "create_uta_adapter", lambda _mode: CCXTUTAAdapter(drifted, profile))
    with pytest.raises(ValueError, match="uta_account_identity_mismatch"):
        coordinator.cancel_order(cancel)
    assert exchange.events == previous_events

@pytest.mark.parametrize("exposure", ["unresolved_order", "open_position"])
def test_unbound_historical_exposure_blocks_new_entry_without_rebinding(tmp_path, monkeypatch, exposure):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Order, Position
    if exposure == "unresolved_order":
        historical = Order(symbol="ETH/USDT", market_type="spot", direction="long", order_type="market", quantity=1, execution_mode="binance_testnet", source="adopted", status="submission_unknown")
        repo.insert_order(historical)
        before = repo.get_order(historical.id)
    else:
        historical = Position(symbol="ETH/USDT", market_type="spot", direction="long", quantity=1, entry_price=100, execution_mode="binance_testnet", source="adopted")
        repo.insert_position(historical)
        before = repo.get_position(historical.id)
    _authority("new-proposal", "new-risk")
    async def submit(command):
        pytest.fail("unbound historical exposure permitted external submission")
    adapter = _Adapter(submit)
    monkeypatch.setattr("aletheia.execution.coordinator.create_uta_adapter", lambda _mode: adapter)
    with pytest.raises(ValueError):
        _execute("new-proposal", "new-risk")
    assert adapter.calls == 0
    assert (repo.get_order(historical.id) if exposure == "unresolved_order" else repo.get_position(historical.id)) == before
