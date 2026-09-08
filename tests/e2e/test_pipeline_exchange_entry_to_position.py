"""Pipeline: exchange-mode EntryPlan → Risk → Gateway → Order → Fill → Position.

The entry counterpart of `test_pipeline_exchange_position_to_close.py`. Every
existing test that drives an entry as far as an Order runs on `paper_internal`
(#2993), so the segment that *opens* a position has no coverage at all on the
modes it will actually run on. `_persist_waiting_plan` already defaults to
`bitget_demo`, but every caller that reaches the order path overrides it back to
paper — including `test_controlled_grounded_candidate_through_real_paper_close`,
which asserts `list_positions(execution_mode="paper_internal")`.

Offline by construction: the venue is the same UTA adapter double the coordinator
tests use. No network, no credentials, no recorded fixtures.

Routing under test — `bitget_demo`, NOT paper:

    execute_entry_plan()              # real Entry Plan owner
      → gateway.submit_proposal()     # real Risk gate, claim, dispatch
      → coordinator.execute_order()   # exchange modes only
      → adapter submit → Order acknowledgement → private Fill → Position

What this asserts beyond "it worked": the Order carries the exact
`uta_account_identity_json` of the account that acknowledged it, which is the
identity link the whole close/reconciliation chain later depends on
(execution_contract.md:235 — 提交前持久化 exact `uta_account_identity_json`).

Scope boundary, stated so this is not mistaken for more than it proves: Risk is
represented by an approving double, as in the paper counterpart. This file proves
that *given* an approved RiskDecision the exchange path persists a truthful
identity chain. Whether the real Risk gate approves an exchange-mode Entry Plan
is a separate claim, and its paper counterpart
(`test_paper_internal_entry_plan_passes_real_risk_gate`) has no exchange-mode
equivalent yet either.
"""

from datetime import datetime, timezone

import pytest

from aletheia.storage import repositories as repo

_MODE = "bitget_demo"


@pytest.fixture(autouse=True)
def _isolate_entry_contract(monkeypatch):
    """Neutralise the entry-contract fingerprint match, exactly as the owning
    entry-plan test module does.

    `_require_ai_entry_authority` compares the plan's behaviour/schema
    fingerprints against `entry_contract_fingerprints()`. `_plan()` seeds
    deliberately synthetic fingerprints, so a fixture plan can never match the
    live contract. That gate is a contract-*version* match, orthogonal to what
    this file claims (exchange-mode routing and account-identity persistence);
    it is not a Risk, identity, freshness or accounting gate, and nothing here
    relaxes those.
    """
    monkeypatch.setattr(
        "aletheia.execution.entry_plan_execution._require_ai_entry_authority",
        lambda _plan: None,
    )


def _demo_capabilities():
    """The real Bitget demo profile's capability surface, read not invented.

    `coordinator.execute_order` consults `adapter.capabilities.native_tpsl` to
    decide whether protection rides along with the entry command. Deriving the
    surface from `_BitgetDemoProfile`'s class attributes keeps the double honest
    and stops it drifting if the profile's support changes. The class is read
    without instantiating it, so no credentials are involved.
    """
    from aletheia.uta.models import CapabilitySupport, UTACapabilities
    from aletheia.uta.profiles.bitget import _BitgetDemoProfile as profile

    def support(flag: bool) -> CapabilitySupport:
        return (
            CapabilitySupport.SUPPORTED if flag else CapabilitySupport.UNSUPPORTED
        )

    return UTACapabilities(
        account_read=CapabilitySupport.SUPPORTED,
        position_read=CapabilitySupport.SUPPORTED,
        order_read=CapabilitySupport.SUPPORTED,
        fill_read=CapabilitySupport.SUPPORTED,
        funding_ledger=CapabilitySupport.SUPPORTED,
        verified_exit=CapabilitySupport.SUPPORTED,
        market_order=support(profile.mutation_market_order),
        limit_order=support(profile.mutation_limit_order),
        modify_order=support(profile.mutation_modify_order),
        cancel_order=support(profile.mutation_cancel_order),
        stop_order=support(profile.mutation_trigger_order),
        trigger_order=support(profile.mutation_trigger_order),
        reduce_only=support(profile.mutation_reduce_only),
        leverage=support(profile.mutation_leverage),
        post_only=support(profile.mutation_post_only),
        client_order_id=support(profile.mutation_client_order_id),
        native_tpsl=support(profile.native_tpsl),
        protection_replace=support(profile.protection_replace),
        protection_cancel=support(profile.protection_cancel),
        trading_permission=CapabilitySupport.UNKNOWN,
        supported_market_types=profile.supported_market_types,
        private_order_history_bounds="90_days",
        private_fill_history_bounds="90_days",
        environment_available=True,
        # `source` is required and names where the surface came from. Saying
        # "double" rather than copying the real profile's string keeps the
        # provenance honest if this ever shows up in an assertion or a log.
        source="bitget_classic_v2_profile_v1_double",
    )


def _exchange_adapter_class():
    from tests.uta.test_execution_coordinator import _Adapter

    class _ExchangeAdapter(_Adapter):
        """The coordinator double plus the capability surface entry needs.

        Subclassed rather than extending the shared `_Adapter`: the close-side
        tests do not consult capabilities, and widening a shared double to suit
        one caller is how doubles stop resembling the thing they stand in for.
        """

        @property
        def capabilities(self):
            return _demo_capabilities()

    return _ExchangeAdapter


def _wire_venue(monkeypatch):
    """Accept one entry command and report it acknowledged with a venue identity."""
    from aletheia.execution import coordinator
    from aletheia.uta.models import CommandEvidence, SubmissionReport
    from tests.uta.test_execution_coordinator import _identity

    _ExchangeAdapter = _exchange_adapter_class()

    submitted: list[object] = []

    async def submit(command):
        submitted.append(command)
        return SubmissionReport(
            identity=command.expected_account,
            command_id=command.command_id,
            evidence=CommandEvidence.ACKNOWLEDGED,
            received_at=datetime.now(timezone.utc),
            client_order_id=command.client_order_id,
            venue_order_id=f"venue-entry-{len(submitted)}",
            profile_version="1",
        )

    adapter = _ExchangeAdapter(submit, _MODE)
    monkeypatch.setattr(coordinator, "create_uta_adapter", lambda _mode: adapter)
    monkeypatch.setattr(
        coordinator, "require_uta_mutation_qualification", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "aletheia.execution.order_constraints.prepare_exchange_quantity",
        lambda mode, symbol, market, qty, price: qty,
    )
    return submitted, _identity


def test_exchange_entry_plan_opens_a_position_through_risk_and_the_broker(
    e2e_db, monkeypatch
):
    from aletheia.execution import gateway
    from aletheia.execution.entry_plan_execution import execute_entry_plan
    from aletheia.storage.models import RiskDecision
    from types import SimpleNamespace
    from tests.test_entry_plan_execution import (
        _execution_receipt_kwargs,
        _persist_waiting_plan,
    )

    submitted, identity_of = _wire_venue(monkeypatch)
    plan = _persist_waiting_plan(account_mode=_MODE, mode="TRIGGERED",
                                 tactic="OPPOSING_BBO_IOC")

    def approve(proposal, *, decision_role, execution_receipt=None):
        decision = RiskDecision(
            proposal_id=proposal.id,
            approved=True,
            allowed_execution_mode=_MODE,
            decision_role=decision_role,
            evaluated_quantity=proposal.quantity,
            proposal_commit_hash=proposal.commit_hash,
            proposal_execution_mode=proposal.execution_mode,
        )
        repo.insert_risk_decision(decision)
        return SimpleNamespace(
            approved=True,
            allowed_execution_mode=_MODE,
            decision_id=decision.id,
            decision_role=decision_role,
        )

    monkeypatch.setattr(gateway, "evaluate_proposal", approve)

    now = datetime.now(timezone.utc)
    result = execute_entry_plan(
        plan.plan_id,
        bid=100,
        ask=100.05,
        observed_at=now.isoformat(),
        source_observed_at=now.isoformat(),
        now=now,
        **_execution_receipt_kwargs(now, depth=True),
    )

    # Acknowledgement is not a fill. An exchange entry that the venue merely
    # accepted must not invent economics: this is where paper and exchange modes
    # genuinely diverge, and asserting it is the point of covering this segment.
    assert result["status"] == "submitted", result

    proposal = repo.get_trade_proposal_by_entry_plan(plan.plan_id)
    assert proposal["execution_mode"] == _MODE

    order = repo.get_order(result["order_id"])
    assert order["execution_mode"] == _MODE
    assert order["entry_plan_id"] == plan.plan_id
    assert order["status"] == "submitted"
    # The venue's identity for the order, not one we minted locally.
    assert order["exchange_order_id"] == "venue-entry-1"
    assert len(submitted) == 1

    # Nothing economic yet, and no Position.
    assert repo.list_fills(order_id=order["id"]) == []
    assert repo.list_positions(execution_mode=_MODE) == []

    # The account link the close and reconciliation chain later depends on
    # (execution_contract.md:235 — 提交前持久化 exact uta_account_identity_json).
    from aletheia.uta.models import UTAAccountIdentity

    stored = UTAAccountIdentity.model_validate_json(order["uta_account_identity_json"])
    assert stored == identity_of(_MODE)
    assert stored == submitted[0].expected_account

    # Only exact private fill evidence turns the acknowledgement into a Position.
    from aletheia.execution.exchange_materialization import materialize_bitget_entry

    materialized = materialize_bitget_entry(
        order["id"],
        [
            {
                "orderId": order["exchange_order_id"],
                "clientOid": order["client_order_id"],
                "tradeId": f"private-entry-{order['id']}",
                "fillPrice": "100.05",
                "fillQuantity": str(order["quantity"]),
                "fee": "0.02",
                "feeCoin": "USDT",
                "cTime": str(
                    int(datetime.now(timezone.utc).timestamp() * 1000) - 1_000
                ),
            }
        ],
    )
    assert materialized.state == "filled", materialized

    settled = repo.get_order(order["id"])
    assert settled["status"] == "filled"
    fills = repo.list_fills(order_id=order["id"])
    assert len(fills) == 1
    assert fills[0]["fill_price"] == pytest.approx(100.05)
    assert fills[0]["fee"] == pytest.approx(0.02)

    positions = repo.list_positions(execution_mode=_MODE)
    assert len(positions) == 1
    position = positions[0]
    assert position["status"] == "open"
    assert position["order_id"] == order["id"]
    assert position["entry_price"] == pytest.approx(100.05)
    # Paper never appears anywhere in this lineage.
    assert repo.list_positions(execution_mode="paper_internal") == []
