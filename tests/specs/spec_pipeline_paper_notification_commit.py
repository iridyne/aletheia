"""Paper fill notifications must describe only committed entry chains."""

from __future__ import annotations

from contextlib import contextmanager
import json
from types import SimpleNamespace

import pytest

from tests.execution_authority import seed_order_authority
from tests.specs.spec_meta import SpecClaim, spec_claim


def _seed_gateway_entry(monkeypatch):
    from aletheia.config import loader
    from aletheia.execution import gateway
    from aletheia.storage.sqlite import get_db

    cfg = SimpleNamespace(
        trading=SimpleNamespace(
            slippage_bps=0.0,
            execution_fee_pct=0.0,
            default_take_profit_pct=4.0,
        )
    )
    monkeypatch.setattr(loader, "load_config", lambda: cfg)
    monkeypatch.setattr(gateway, "load_config", lambda: cfg)
    proposal, decision = seed_order_authority(
        proposal_id="paper-notification-proposal",
        risk_decision_id="paper-notification-risk",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=1.0,
        price=100.0,
        execution_mode="paper_internal",
        attribution_snapshot=json.dumps({"regime_tag": "unknown"}),
    )
    # The helper inserts the authoritative decision directly. Production Risk
    # commits that decision before broker execution, so mirror that boundary.
    get_db().commit()
    monkeypatch.setattr(
        gateway,
        "evaluate_proposal",
        lambda _proposal, *, decision_role: SimpleNamespace(
            approved=True,
            allowed_execution_mode="paper_internal",
            decision_id=decision.id,
            violations=[],
            message="approved",
        ),
    )
    monkeypatch.setattr(gateway, "write_audit", lambda _event: None)
    return proposal


def _seed_exchange_gateway(
    monkeypatch,
    *,
    broker_result: dict | None = None,
    broker_error: BaseException | None = None,
    broker_error_order_status: str | None = None,
    persist_materialization: bool = True,
):
    from aletheia.config import loader
    from aletheia.execution import bitget_broker, gateway
    from aletheia.execution.errors import SubmissionUnknownError
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Fill, Order, Position
    from aletheia.storage.sqlite import get_db

    cfg = SimpleNamespace(
        trading=SimpleNamespace(
            slippage_bps=0.0,
            execution_fee_pct=0.0,
            default_take_profit_pct=4.0,
        )
    )
    monkeypatch.setattr(loader, "load_config", lambda: cfg)
    monkeypatch.setattr(gateway, "load_config", lambda: cfg)
    proposal, decision = seed_order_authority(
        proposal_id="exchange-post-commit-proposal",
        risk_decision_id="exchange-post-commit-risk",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        order_type="market",
        quantity=0.001,
        price=60_000.0,
        execution_mode="bitget_demo",
    )
    get_db().commit()
    monkeypatch.setattr(
        gateway,
        "evaluate_proposal",
        lambda _proposal, *, decision_role: SimpleNamespace(
            approved=True,
            allowed_execution_mode="bitget_demo",
            decision_id=decision.id,
            violations=[],
            message="approved",
        ),
    )
    monkeypatch.setattr(
        gateway,
        "_prepare_exchange_quantity",
        lambda _mode, _symbol, _market_type, quantity, _price: (quantity, None),
    )
    monkeypatch.setattr(
        gateway,
        "_check_symbol_availability",
        lambda _symbol, _mode, _market_type: None,
    )

    def execute_live_order(**_kwargs):
        if broker_error is not None:
            durable_error_status = broker_error_order_status
            if durable_error_status is None and isinstance(
                broker_error, SubmissionUnknownError
            ):
                durable_error_status = "submission_unknown"
            if durable_error_status is not None:
                repo.insert_order(
                    Order(
                        id=(
                            "exchange-order-unresolved"
                            if broker_error_order_status is None
                            else f"exchange-order-before-return-{durable_error_status}"
                        ),
                        proposal_id=proposal.id,
                        risk_decision_id=decision.id,
                        client_order_id=proposal.id,
                        symbol=proposal.symbol,
                        market_type=proposal.market_type,
                        direction=proposal.direction,
                        order_type=proposal.order_type,
                        quantity=proposal.quantity or 0.0,
                        price=proposal.price,
                        execution_mode=proposal.execution_mode,
                        exchange_order_id=(
                            getattr(broker_error, "exchange_order_id", None)
                            or "venue-order-before-return"
                        ),
                        status=durable_error_status,
                    )
                )
            raise broker_error
        assert broker_result is not None
        order = Order(
            id=broker_result["order_id"],
            proposal_id=proposal.id,
            risk_decision_id=decision.id,
            client_order_id=proposal.id,
            position_id=broker_result.get("position_id"),
            symbol=proposal.symbol,
            market_type=proposal.market_type,
            direction=proposal.direction,
            order_type=proposal.order_type,
            quantity=proposal.quantity or 0.0,
            price=proposal.price,
            execution_mode=proposal.execution_mode,
            exchange_order_id=broker_result.get("exchange_order_id"),
            status=broker_result["order_status"],
        )
        repo.insert_order(order)
        if (
            persist_materialization
            and broker_result.get("fill_id")
            and broker_result.get("position_id")
        ):
            repo.insert_fill(
                Fill(
                    id=broker_result["fill_id"],
                    order_id=order.id,
                    fill_price=proposal.price or 0.0,
                    fill_quantity=proposal.quantity or 0.0,
                )
            )
            repo.insert_position(
                Position(
                    id=broker_result["position_id"],
                    proposal_id=proposal.id,
                    order_id=order.id,
                    symbol=proposal.symbol,
                    market_type=proposal.market_type,
                    direction=proposal.direction,
                    quantity=proposal.quantity or 0.0,
                    entry_price=proposal.price or 0.0,
                    execution_mode=proposal.execution_mode,
                )
            )
        return dict(broker_result)

    monkeypatch.setattr(bitget_broker, "execute_live_order", execute_live_order)
    return proposal


@spec_claim(
    SpecClaim(
        "PL-PN-01",
        "pipeline_paper_notification_commit",
        "committed fill notification",
        "L2c",
        "A paper fill notification is sent only after its Order, Fill, Position, "
        "and executed Proposal are all committed.",
        status="implemented",
    )
)
def test_gateway_notifies_only_after_complete_paper_chain_commits(monkeypatch):
    from aletheia import notifications
    from aletheia.execution import gateway
    from aletheia.storage import repositories as repo
    from aletheia.storage.sqlite import get_db

    proposal = _seed_gateway_entry(monkeypatch)
    observed = []

    def _observe(
        symbol,
        direction,
        quantity,
        fill_price,
        position_id,
        execution_mode,
        **_kwargs,
    ):
        position = repo._select_one("positions", position_id)
        order = repo.get_order(position["order_id"]) if position else None
        fills = [
            fill for fill in repo.list_fills(limit=10)
            if order and fill["order_id"] == order["id"]
        ]
        stored_proposal = repo.get_trade_proposal(proposal.id)
        observed.append({
            "in_transaction": get_db().in_transaction,
            "proposal_status": stored_proposal["status"],
            "position_present": position is not None,
            "order_present": order is not None,
            "fill_present": bool(fills),
        })

    monkeypatch.setattr(notifications, "notify_trade_fill", _observe)

    result = gateway._execute_proposal(proposal)

    assert result["status"] == "executed"
    assert observed == [{
        "in_transaction": False,
        "proposal_status": "executed",
        "position_present": True,
        "order_present": True,
        "fill_present": True,
    }]
    assert "_notification_payload" not in result


@spec_claim(
    SpecClaim(
        "PL-PN-02",
        "pipeline_paper_notification_commit",
        "rollback suppression",
        "L2c",
        "A failed outer Gateway transaction rolls back the paper entry chain and "
        "must not emit a trade-fill notification.",
        status="implemented",
    )
)
def test_gateway_rollback_suppresses_paper_fill_notification(monkeypatch):
    from aletheia import notifications
    from aletheia.execution import gateway
    from aletheia.storage import repositories as repo

    proposal = _seed_gateway_entry(monkeypatch)
    notified = []
    monkeypatch.setattr(
        notifications,
        "notify_trade_fill",
        lambda *args, **kwargs: notified.append((args, kwargs)),
    )

    real_transaction = repo.transaction
    transaction_calls = 0

    @contextmanager
    def _fail_first_transaction(*, immediate=False):
        nonlocal transaction_calls
        transaction_calls += 1
        with real_transaction(immediate=immediate):
            yield
            if transaction_calls == 1:
                raise RuntimeError("forced outer transaction failure")

    monkeypatch.setattr(gateway, "transaction", _fail_first_transaction)

    result = gateway._execute_proposal(proposal)

    assert result["status"] == "error"
    assert notified == []
    assert repo.list_orders(limit=10) == []
    assert repo.list_fills(limit=10) == []
    assert repo.list_positions(limit=10) == []
    assert repo.get_trade_proposal(proposal.id)["status"] == "error"


@spec_claim(
    SpecClaim(
        "PL-PN-03",
        "pipeline_paper_notification_commit",
        "post-commit audit failure",
        "L2c",
        "After a paper Order, Fill, Position, and executed Proposal commit, failure of the "
        "secondary audit sink cannot rewrite the Proposal to error or change the executed response.",
        status="implemented",
    )
)
def test_post_commit_audit_failure_preserves_executed_paper_chain(
    monkeypatch, caplog
):
    from aletheia.execution import gateway
    from aletheia.storage import repositories as repo

    proposal = _seed_gateway_entry(monkeypatch)

    def fail_secondary_audit(_event):
        raise RuntimeError("token=must-not-leak-from-audit-sink")

    monkeypatch.setattr(gateway, "write_audit", fail_secondary_audit)

    result = gateway._execute_proposal(proposal)

    orders = [
        row for row in repo.list_orders(limit=10)
        if row.get("proposal_id") == proposal.id
    ]
    fills = repo.list_fills(limit=10)
    positions = [
        row for row in repo.list_positions(limit=10)
        if row.get("proposal_id") == proposal.id
    ]
    assert result["status"] == "executed"
    assert repo.get_trade_proposal(proposal.id)["status"] == "executed"
    assert len(orders) == len(fills) == len(positions) == 1
    assert "must-not-leak" not in str(result)
    assert "RuntimeError" in caplog.text
    assert "must-not-leak" not in caplog.text


@spec_claim(
    SpecClaim(
        "PL-PN-04",
        "pipeline_paper_notification_commit",
        "exchange post-commit audit failure",
        "L2c",
        "After an exchange broker result is durably represented as submitted or executed, "
        "failure of the secondary audit sink cannot rewrite that Proposal to error.",
        status="implemented",
    )
)
@pytest.mark.parametrize(
    ("broker_result", "expected_status"),
    [
        (
            {
                "order_id": "exchange-order-submitted",
                "order_status": "submitted",
                "exchange_order_id": "venue-order-submitted",
                "fill_id": None,
                "position_id": None,
            },
            "submitted",
        ),
        (
            {
                "order_id": "exchange-order-filled",
                "order_status": "filled",
                "exchange_order_id": "venue-order-filled",
                "fill_id": "exchange-fill",
                "position_id": "exchange-position",
            },
            "executed",
        ),
    ],
)
def test_post_commit_audit_failure_preserves_exchange_result(
    monkeypatch, caplog, broker_result, expected_status
):
    from aletheia.execution import gateway
    from aletheia.storage import repositories as repo

    proposal = _seed_exchange_gateway(
        monkeypatch,
        broker_result=broker_result,
    )

    def fail_secondary_audit(_event):
        raise RuntimeError("token=must-not-leak-from-exchange-audit-sink")

    monkeypatch.setattr(gateway, "write_audit", fail_secondary_audit)

    result = gateway._execute_proposal(proposal)

    assert result["status"] == expected_status
    assert repo.get_trade_proposal(proposal.id)["status"] == expected_status
    order = repo.get_order(broker_result["order_id"])
    assert order is not None
    assert order["status"] == broker_result["order_status"]
    if expected_status == "executed":
        assert any(row["id"] == broker_result["fill_id"] for row in repo.list_fills())
        assert any(
            row["id"] == broker_result["position_id"]
            for row in repo.list_positions()
        )
    assert "must-not-leak" not in str(result)
    assert "RuntimeError" in caplog.text
    assert "must-not-leak" not in caplog.text


@spec_claim(
    SpecClaim(
        "PL-PN-05",
        "pipeline_paper_notification_commit",
        "unresolved exchange post-commit audit failure",
        "L2c",
        "After an uncertain exchange handoff is durably marked submission_unknown, failure "
        "of the secondary audit sink cannot rewrite it to error or expose exception text.",
        status="implemented",
    )
)
def test_post_commit_audit_failure_preserves_submission_unknown(monkeypatch, caplog):
    from aletheia.execution import gateway
    from aletheia.execution.errors import SubmissionUnknownError
    from aletheia.storage import repositories as repo

    proposal = _seed_exchange_gateway(
        monkeypatch,
        broker_error=SubmissionUnknownError(
            "token=must-not-leak-from-transport",
            client_oid="exchange-post-commit-proposal",
        ),
    )

    def fail_secondary_audit(_event):
        raise RuntimeError("token=must-not-leak-from-unresolved-audit-sink")

    monkeypatch.setattr(gateway, "write_audit", fail_secondary_audit)

    result = gateway._execute_proposal(proposal)

    assert result["status"] == "submission_unknown"
    assert repo.get_trade_proposal(proposal.id)["status"] == "submission_unknown"
    assert repo.get_order("exchange-order-unresolved")["status"] == "submission_unknown"
    assert "must-not-leak" not in str(result)
    assert "RuntimeError" in caplog.text
    assert "must-not-leak" not in caplog.text


@spec_claim(
    SpecClaim(
        "PL-PN-06",
        "pipeline_paper_notification_commit",
        "accepted exchange result read recovery",
        "L2c",
        "After the broker has returned an accepted result and persisted its Order, a transient "
        "local evidence read failure recovers submitted/executed from durable identity-linked facts "
        "and can never rewrite the Proposal to error.",
        status="implemented",
    )
)
@pytest.mark.parametrize(
    ("broker_result", "expected_status"),
    [
        (
            {
                "order_id": "accepted-order",
                "order_status": "submitted",
                "exchange_order_id": "venue-accepted-order",
                "fill_id": None,
                "position_id": None,
            },
            "submitted",
        ),
        (
            {
                "order_id": "accepted-filled-order",
                "order_status": "filled",
                "exchange_order_id": "venue-accepted-filled-order",
                "fill_id": "accepted-fill",
                "position_id": "accepted-position",
            },
            "executed",
        ),
    ],
)
def test_transient_post_acceptance_read_fault_recovers_durable_exchange_state(
    monkeypatch, caplog, broker_result, expected_status
):
    from aletheia.execution import gateway
    from aletheia.storage import repositories as repo

    proposal = _seed_exchange_gateway(monkeypatch, broker_result=broker_result)
    monkeypatch.setattr(gateway, "write_audit", lambda _event: None)
    real_get_order = gateway.get_order
    reads = 0

    def transient_get_order(order_id):
        nonlocal reads
        reads += 1
        if reads == 1:
            raise RuntimeError("token=must-not-leak-from-post-acceptance-read")
        return real_get_order(order_id)

    monkeypatch.setattr(gateway, "get_order", transient_get_order)

    result = gateway._execute_proposal(proposal)

    assert reads >= 2
    assert result["status"] == expected_status
    assert repo.get_trade_proposal(proposal.id)["status"] == expected_status
    assert repo.get_order(broker_result["order_id"])["status"] == broker_result["order_status"]
    assert "automatic resubmission" not in result.get("message", "").lower()
    assert "must-not-leak" not in str(result)
    assert "must-not-leak" not in caplog.text


@spec_claim(
    SpecClaim(
        "PL-PN-07",
        "pipeline_paper_notification_commit",
        "accepted exchange result write recovery",
        "L2c",
        "After exchange acceptance, a transient Proposal status-write failure degrades to a "
        "retry-suppressing unresolved state instead of error, without exposing exception text.",
        status="implemented",
    )
)
@pytest.mark.parametrize(
    ("broker_result", "expected_status"),
    [
        (
            {
                "order_id": "accepted-write-fault-order",
                "order_status": "submitted",
                "exchange_order_id": "venue-accepted-write-fault-order",
                "fill_id": None,
                "position_id": None,
            },
            "submission_unknown",
        ),
        (
            {
                "order_id": "accepted-filled-write-fault-order",
                "order_status": "filled",
                "exchange_order_id": "venue-accepted-filled-write-fault-order",
                "fill_id": "accepted-write-fault-fill",
                "position_id": "accepted-write-fault-position",
            },
            "materialization_missing",
        ),
    ],
)
def test_transient_post_acceptance_proposal_write_fault_becomes_unresolved(
    monkeypatch, caplog, broker_result, expected_status
):
    from aletheia.execution import gateway
    from aletheia.storage import repositories as repo

    proposal = _seed_exchange_gateway(monkeypatch, broker_result=broker_result)
    monkeypatch.setattr(gateway, "write_audit", lambda _event: None)
    real_update = gateway.update_trade_proposal
    writes = []

    def transient_update(proposal_id, **changes):
        writes.append(changes.get("status"))
        if len(writes) == 1:
            raise RuntimeError("token=must-not-leak-from-post-acceptance-write")
        return real_update(proposal_id, **changes)

    monkeypatch.setattr(gateway, "update_trade_proposal", transient_update)

    result = gateway._execute_proposal(proposal)

    assert writes[:2] == [
        "executed" if broker_result["order_status"] == "filled" else "submitted",
        expected_status,
    ]
    assert result["status"] == expected_status
    assert repo.get_trade_proposal(proposal.id)["status"] == expected_status
    assert repo.get_order(broker_result["order_id"])["status"] == broker_result["order_status"]
    assert "automatic resubmission is blocked" in result["message"].lower()
    assert "must-not-leak" not in str(result)
    assert "RuntimeError" in caplog.text
    assert "must-not-leak" not in caplog.text


@spec_claim(
    SpecClaim(
        "PL-PN-08",
        "pipeline_paper_notification_commit",
        "exchange execution requires durable materialization",
        "L2c",
        "An exchange result can be marked executed only when its filled Order has one non-empty "
        "identity-linked Fill and Position; result ID strings alone degrade to materialization_missing.",
        status="implemented",
    )
)
def test_exchange_result_ids_without_durable_chain_are_not_executed(monkeypatch):
    from aletheia.execution import gateway
    from aletheia.storage import repositories as repo

    broker_result = {
        "order_id": "filled-without-chain-order",
        "order_status": "filled",
        "exchange_order_id": "venue-filled-without-chain-order",
        "fill_id": "missing-fill",
        "position_id": "missing-position",
    }
    proposal = _seed_exchange_gateway(
        monkeypatch,
        broker_result=broker_result,
        persist_materialization=False,
    )
    monkeypatch.setattr(gateway, "write_audit", lambda _event: None)

    result = gateway._execute_proposal(proposal)

    assert result["status"] == "materialization_missing"
    assert repo.get_trade_proposal(proposal.id)["status"] == "materialization_missing"
    assert repo.get_order(broker_result["order_id"])["status"] == "filled"
    assert repo.list_fills(limit=10) == []
    assert repo.list_positions(limit=10) == []
    assert "automatic resubmission is blocked" in result["message"].lower()


@spec_claim(
    SpecClaim(
        "PL-PN-09",
        "pipeline_paper_notification_commit",
        "broker before-return audit failure recovery",
        "L2c",
        "If a broker persists a retry-suppressing Order and then its secondary audit sink "
        "fails before return, the Gateway recovers that durable state and never resubmits.",
        status="implemented",
    )
)
@pytest.mark.parametrize(
    "durable_order_status",
    ["materialization_missing", "submission_unknown"],
)
def test_broker_audit_failure_before_return_recovers_durable_order_without_retry(
    monkeypatch, caplog, durable_order_status
):
    from aletheia.execution import bitget_broker, gateway
    from aletheia.storage import repositories as repo

    proposal = _seed_exchange_gateway(
        monkeypatch,
        broker_error=RuntimeError(
            "token=must-not-leak-from-broker-before-return-audit"
        ),
        broker_error_order_status=durable_order_status,
    )
    monkeypatch.setattr(gateway, "write_audit", lambda _event: None)

    result = gateway._execute_proposal(proposal)

    order_id = f"exchange-order-before-return-{durable_order_status}"
    assert repo.get_order(order_id)["status"] == durable_order_status
    assert repo.get_trade_proposal(proposal.id)["status"] == durable_order_status
    assert result["status"] == durable_order_status
    assert "automatic resubmission is blocked" in result["message"].lower()
    assert "must-not-leak" not in str(result)
    assert "RuntimeError" in caplog.text
    assert "must-not-leak" not in caplog.text

    monkeypatch.setattr(
        bitget_broker,
        "execute_live_order",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("retry reached broker after durable accepted Order")
        ),
    )
    replay = gateway.execute_existing_proposal(proposal.id, "bitget_demo")
    assert replay["status"] == durable_order_status


@spec_claim(
    SpecClaim(
        "PL-PN-10",
        "pipeline_paper_notification_commit",
        "broker evidence unavailable is not absent",
        "L2c",
        "If durable exchange evidence cannot be read after a broker failure, the Gateway "
        "fails closed as submission_unknown instead of treating unreadable evidence as absent/error.",
        status="implemented",
    )
)
def test_broker_failure_with_repeated_evidence_read_fault_is_not_error(
    monkeypatch, caplog
):
    from aletheia.execution import bitget_broker, gateway
    from aletheia.storage import repositories as repo

    proposal = _seed_exchange_gateway(
        monkeypatch,
        broker_error=RuntimeError(
            "token=must-not-leak-from-broker-with-unreadable-evidence"
        ),
        broker_error_order_status="submitted",
    )
    monkeypatch.setattr(gateway, "write_audit", lambda _event: None)
    reads = 0

    def unavailable_evidence(_proposal):
        nonlocal reads
        reads += 1
        raise RuntimeError("token=must-not-leak-from-evidence-read")

    monkeypatch.setattr(
        gateway,
        "_durable_retry_suppressing_exchange_order",
        unavailable_evidence,
    )

    result = gateway._execute_proposal(proposal)

    assert reads == 2
    assert repo.get_order("exchange-order-before-return-submitted")["status"] == "submitted"
    assert repo.get_trade_proposal(proposal.id)["status"] == "submission_unknown"
    assert result["status"] == "submission_unknown"
    assert "automatic resubmission is blocked" in result["message"].lower()
    assert "must-not-leak" not in str(result)
    assert "RuntimeError" in caplog.text
    assert "must-not-leak" not in caplog.text

    monkeypatch.setattr(
        bitget_broker,
        "execute_live_order",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("unreadable durable evidence permitted a retry")
        ),
    )
    replay = gateway.execute_existing_proposal(proposal.id, "bitget_demo")
    assert replay["status"] == "submission_unknown"
