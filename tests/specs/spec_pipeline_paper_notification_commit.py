"""Paper fill notifications must describe only committed entry chains."""

from __future__ import annotations

from contextlib import contextmanager
import json
from types import SimpleNamespace

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
