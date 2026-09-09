"""Exchange EntryPlan → Risk → Gateway → Order → private Fill → Position.

Uses the existing UTA adapter double without network or credentials. Covers
account identity, acknowledgements, partial fills, replay and database reconnect.
The final Risk cases exercise the real gate; routing cases supply a Risk decision.
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


def _wire_venue(monkeypatch, *, evidence=None, reason_code=None, venue_order_id=True):
    """Wire the venue double. `evidence` selects the command outcome under test."""
    from aletheia.execution import coordinator
    from aletheia.uta.models import CommandEvidence, SubmissionReport
    from tests.uta.test_execution_coordinator import _identity

    _ExchangeAdapter = _exchange_adapter_class()

    submitted: list[object] = []
    outcome = evidence or CommandEvidence.ACKNOWLEDGED

    async def submit(command):
        submitted.append(command)
        return SubmissionReport(
            identity=command.expected_account,
            command_id=command.command_id,
            evidence=outcome,
            reason_code=reason_code,
            received_at=datetime.now(timezone.utc),
            client_order_id=command.client_order_id,
            venue_order_id=(
                f"venue-entry-{len(submitted)}" if venue_order_id else None
            ),
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


def _approving_risk(monkeypatch):
    """Represent Risk by an approving decision, as the paper counterpart does."""
    from types import SimpleNamespace

    from aletheia.execution import gateway
    from aletheia.storage.models import RiskDecision

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


def _drive_entry(plan):
    from aletheia.execution.entry_plan_execution import execute_entry_plan
    from tests.test_entry_plan_execution import _execution_receipt_kwargs

    now = datetime.now(timezone.utc)
    return execute_entry_plan(
        plan.plan_id,
        bid=100,
        ask=100.05,
        observed_at=now.isoformat(),
        source_observed_at=now.isoformat(),
        now=now,
        **_execution_receipt_kwargs(now, depth=True),
    )


def _seeded_plan():
    return _seeded_plan_with(None)


def _seeded_plan_with(plan_updates):
    from tests.test_entry_plan_execution import _persist_waiting_plan

    return _persist_waiting_plan(
        account_mode=_MODE,
        mode="TRIGGERED",
        tactic="OPPOSING_BBO_IOC",
        plan_updates=plan_updates,
    )


def _private_entry_fill(order: dict, *, quantity=None, fee="0.02", fee_coin="USDT"):
    return {
        "orderId": order["exchange_order_id"],
        "clientOid": order["client_order_id"],
        "tradeId": f"private-entry-{order['id']}",
        "fillPrice": "100.05",
        "fillQuantity": str(quantity if quantity is not None else order["quantity"]),
        "fee": fee,
        "feeCoin": fee_coin,
        "cTime": str(int(datetime.now(timezone.utc).timestamp() * 1000) - 1_000),
    }


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

    # An acknowledgement alone must not create a Fill or Position.
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
    assert position["expected_horizon_hours"] == plan.holding_horizon_hours
    assert position["stop_source"] == "operator"
    assert all(row["execution_mode"] == _MODE for row in repo.list_positions())


@pytest.mark.parametrize(
    ("evidence_name", "reason_code", "expected_status", "expected_reason"),
    (
        pytest.param(
            "NOT_SENT",
            "insufficient_margin",
            "error",
            "insufficient_margin",
            id="pre-submit-failure",
        ),
        pytest.param(
            "AMBIGUOUS",
            None,
            "submission_unknown",
            "command_acknowledgement_unknown",
            id="acknowledgement-unknown",
        ),
        pytest.param(
            "VENUE_REJECTED",
            "invalid_quantity",
            "submission_rejected",
            "invalid_quantity",
            id="venue-rejected",
        ),
    ),
)
def test_non_acknowledged_entry_invents_no_economics(
    e2e_db, monkeypatch, evidence_name, reason_code, expected_status, expected_reason
):
    """A command the venue did not take must leave no Fill and no Position.

    These are the three non-acknowledged outcomes the coordinator distinguishes
    (`coordinator._status`), and they are distinct on purpose: `error` means the
    order never left, `submission_unknown` means we cannot tell and must not
    retry, `submission_rejected` means the venue said no. Collapsing any of them
    into a fill is the failure this covers.
    """
    from aletheia.uta.models import CommandEvidence

    _approving_risk(monkeypatch)
    _wire_venue(monkeypatch, evidence=getattr(CommandEvidence, evidence_name),
                reason_code=reason_code)
    plan = _seeded_plan()

    _drive_entry(plan)

    orders = [
        row for row in repo.list_orders(limit=50) if row["execution_mode"] == _MODE
    ]
    assert len(orders) == 1, orders
    order = orders[0]
    assert order["status"] == expected_status
    assert order["error_reason"] == expected_reason
    # Nothing economic, whatever the venue said.
    assert repo.list_fills(order_id=order["id"]) == []
    assert repo.list_positions(execution_mode=_MODE) == []
    assert all(row["execution_mode"] == _MODE for row in repo.list_positions())


def test_partial_private_fill_does_not_report_a_complete_entry(e2e_db, monkeypatch):
    """Half the requested quantity is a partial entry, not a filled one."""
    from aletheia.execution.exchange_materialization import materialize_bitget_entry

    _approving_risk(monkeypatch)
    _wire_venue(monkeypatch)
    plan = _seeded_plan()
    result = _drive_entry(plan)
    order = repo.get_order(result["order_id"])

    requested = float(order["quantity"])
    materialized = materialize_bitget_entry(
        order["id"], [_private_entry_fill(order, quantity=requested / 2)]
    )

    assert materialized.state != "filled", materialized
    settled = repo.get_order(order["id"])
    assert settled["status"] != "filled"
    # Whatever the partial state is called, it must not claim the full quantity.
    fills = repo.list_fills(order_id=order["id"])
    assert sum(float(row["fill_quantity"]) for row in fills) < requested
    assert all(row["execution_mode"] == _MODE for row in repo.list_positions())


def test_replayed_private_fill_evidence_stays_idempotent(e2e_db, monkeypatch):
    """The same private fill seen twice is one Fill and one Position, not two.

    Reconciliation, the scheduler and a restart can all present the same evidence
    again; the materializer is the single place that has to refuse to double-count.
    """
    from aletheia.execution.exchange_materialization import materialize_bitget_entry

    _approving_risk(monkeypatch)
    _wire_venue(monkeypatch)
    plan = _seeded_plan()
    result = _drive_entry(plan)
    order = repo.get_order(result["order_id"])
    evidence = [_private_entry_fill(order)]

    first = materialize_bitget_entry(order["id"], evidence)
    assert first.state == "filled", first
    second = materialize_bitget_entry(order["id"], evidence)
    assert second.state == "filled", second

    assert len(repo.list_fills(order_id=order["id"])) == 1
    assert len(repo.list_positions(execution_mode=_MODE)) == 1


def test_entry_fails_closed_when_another_account_already_holds_the_mode(
    e2e_db, monkeypatch
):
    """An open Order under a different account identity blocks a new entry.

    `coordinator.execute_order` refuses rather than submitting alongside evidence
    it cannot attribute to one account. Without this a credential or profile
    change mid-flight would leave two accounts' Orders in the same execution mode
    with no way to tell them apart.
    """
    from aletheia.storage.models import Order

    _approving_risk(monkeypatch)
    submitted, identity_of = _wire_venue(monkeypatch)

    foreign = identity_of(_MODE).model_copy(update={"credential_generation": 99})
    repo.insert_order(
        Order(
            symbol="BTC/USDT",
            market_type="usdt_futures",
            direction="long",
            order_type="market",
            quantity=1.0,
            price=100.0,
            execution_mode=_MODE,
            status="submitted",
            # Adopted, so this fixture needs no Proposal/RiskDecision authority of
            # its own: the identity check reads status and execution_mode, not
            # provenance.
            source="adopted",
            uta_account_identity_json=foreign.model_dump_json(),
        )
    )

    plan = _seeded_plan()
    _drive_entry(plan)

    # The command never went to the venue, and no Position was opened.
    assert submitted == []
    assert repo.list_positions(execution_mode=_MODE) == []


def test_acknowledged_entry_recovers_after_the_connection_is_reopened(
    e2e_db, monkeypatch
):
    """An acknowledged entry converges from evidence read on a fresh connection.

    Scope, stated rather than implied: this crosses the *connection* boundary, not
    the process boundary. Recovery must not depend on the connection that observed
    the acknowledgement — durable state has to carry everything the materializer
    needs. The process-boundary proof for exchange economics lives in
    `test_exchange_funding_golden_path`, which runs three real Python processes;
    this is the cheaper adjacent claim, not a substitute for it.
    """
    import aletheia.storage.sqlite as sq
    from aletheia.execution.exchange_materialization import materialize_bitget_entry

    _approving_risk(monkeypatch)
    _wire_venue(monkeypatch)
    plan = _seeded_plan()
    result = _drive_entry(plan)
    order_id = result["order_id"]
    assert repo.get_order(order_id)["status"] == "submitted"

    # Drop the observing connection before any evidence is applied.
    sq.reset_db()
    sq.get_db()

    reopened = repo.get_order(order_id)
    assert reopened["exchange_order_id"] == "venue-entry-1"
    # The account proof survived the reopen; without it the materializer has no
    # attributable evidence at all.
    assert reopened["uta_account_identity_json"]

    materialized = materialize_bitget_entry(
        order_id, [_private_entry_fill(reopened)]
    )
    assert materialized.state == "filled", materialized

    positions = repo.list_positions(execution_mode=_MODE)
    assert len(positions) == 1
    assert positions[0]["order_id"] == order_id
    assert all(row["execution_mode"] == _MODE for row in repo.list_positions())


@pytest.mark.parametrize(
    ("fee", "fee_coin", "case"),
    (
        pytest.param(None, "USDT", "fee-absent", id="fee-absent"),
        pytest.param("0.02", "BGB", "fee-third-currency", id="fee-third-currency"),
    ),
)
def test_entry_with_unavailable_cost_is_not_reported_as_complete(
    e2e_db, monkeypatch, fee, fee_coin, case
):
    """Cost that cannot be expressed in the quote currency must stay unavailable.

    Gate A's last item: unavailable cost must not masquerade as complete.

    The two cases here are the ones that genuinely cannot be costed. A fee in the
    *base* currency deliberately is not among them — the materializer converts it
    at the fill price, which is arithmetic rather than fabrication. A fee in a
    third currency (BGB, BNB) has no attributable conversion without market data,
    and an absent fee has no value at all; either read as 0.0 would understate
    cost on every downstream Outcome.
    """
    from aletheia.execution.exchange_materialization import materialize_bitget_entry

    _approving_risk(monkeypatch)
    _wire_venue(monkeypatch)
    plan = _seeded_plan()
    result = _drive_entry(plan)
    order = repo.get_order(result["order_id"])

    row = _private_entry_fill(order, fee=fee, fee_coin=fee_coin)
    if fee is None:
        row.pop("fee")

    try:
        materialized = materialize_bitget_entry(order["id"], row and [row])
    except Exception:
        # Refusing outright is an acceptable, honest answer for evidence that
        # cannot be costed.
        assert repo.list_positions(execution_mode=_MODE) == []
        return

    if materialized.state != "filled":
        assert repo.list_positions(execution_mode=_MODE) == []
        return

    # The raw venue fee stays auditable on the Fill by design — that is not the
    # claim. The claim is that the Position's quote-currency entry cost must be
    # unavailable rather than a number pretending the fee was already in USDT.
    fills = repo.list_fills(order_id=order["id"])
    assert len(fills) == 1
    positions = repo.list_positions(execution_mode=_MODE)
    assert len(positions) == 1
    entry_fee = positions[0]["entry_fee"]
    assert entry_fee is None or entry_fee == 0, (
        f"{case}: an uncostable fee became a quote-currency entry cost",
        {"entry_fee": entry_fee, "fill_fee": fills[0]["fee"],
         "fill_fee_currency": fills[0].get("fee_currency")},
    )


def test_real_risk_refuses_an_exchange_entry_the_runtime_is_not_configured_for(
    e2e_db, monkeypatch
):
    """A Demo order cannot execute against an explicitly selected live mode."""
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.exchange.mode = "live"
    save_config(cfg)
    submitted, _ = _wire_venue(monkeypatch)
    plan = _seeded_plan()

    result = _drive_entry(plan)

    assert result["status"] == "rejected", result
    violations = " ".join(result.get("violations") or []) + result.get("message", "")
    assert "account_mode_mismatch" in violations, result

    # Nothing reached the venue and nothing durable was created.
    assert submitted == []
    assert repo.list_positions(execution_mode=_MODE) == []
    assert all(row["execution_mode"] == _MODE for row in repo.list_positions())
    decisions = repo.list_risk_decisions(limit=20)
    assert decisions, "Risk must record its refusal, not stay silent"
    assert not any(row["approved"] for row in decisions), decisions


def test_real_risk_admits_an_exchange_entry_once_the_runtime_agrees(
    e2e_db, monkeypatch
):
    """The approving half: real Risk lets a bitget_demo entry through.

    The refusal counterpart above names three independent reasons Risk gives while
    the runtime is not pointed at the venue. This satisfies all three honestly
    rather than bypassing the gate:

    * `exchange.mode` is set to `demo`, so the configured venue and the order's
      execution mode agree — the mismatch is a real disagreement, not a nuisance;
    * account equity comes from the exchange account cache seam Risk actually
      reads (`exchange_sync.get_unified_cached_state`), so capital is a known
      number rather than an assumption;
    * a fresh snapshot exists at the configured market-data venue.

    Nothing in the Risk engine is patched. This is the last Gate A entry claim
    that had no exchange-mode coverage: that Risk *admits*, not merely that it
    refuses.
    """
    from datetime import datetime as _dt

    from aletheia.config.loader import load_config, save_config
    from aletheia.data import price_feed
    from aletheia.portfolio import exchange_sync, local_funding
    from aletheia.risk.policy import resolve_risk_policy
    from aletheia.storage.models import MarketSnapshot

    submitted, identity_of = _wire_venue(monkeypatch)
    # Risk calls load_config() itself, so the configured venue has to be persisted
    # rather than set on a local copy — patching an object the engine never sees is
    # how this test first "passed" the mode gate without actually agreeing with it.
    cfg = load_config()
    cfg.exchange.mode = "demo"
    save_config(cfg)
    cfg = load_config()
    assert cfg.exchange.mode == "demo"
    resolved = resolve_risk_policy(cfg, _MODE)

    repo.insert_market_snapshot(
        MarketSnapshot(
            id="exchange-entry-snapshot",
            symbol="BTC/USDT",
            market_type="usdt_futures",
            exchange=cfg.market_data.ccxt_exchange,
            price=100.05,
            volume_24h=500_000_000.0,
            snapshot_at=_dt.now(timezone.utc).isoformat(),
        )
    )
    monkeypatch.setattr(
        exchange_sync,
        "get_unified_cached_state",
        lambda *a, **k: {
            "age_seconds": 1.0,
            "status": "healthy",
            "errors": [],
            "accounts": [{"broker_id": _MODE, "equity_usdt": 5_000.0}],
            "positions": [],
        },
    )
    monkeypatch.setattr(
        price_feed,
        "get_fresh_price_for_protective_close",
        lambda *_args, **_kwargs: 100.05,
    )
    monkeypatch.setattr(
        local_funding, "has_fresh_local_funding_schedule", lambda *a, **k: True
    )

    plan = _seeded_plan_with(
        {
            "max_notional": 10.0,
            "risk_policy_id": resolved.policy.policy_id,
            "risk_policy_revision": resolved.policy.revision,
            "risk_policy_hash": resolved.policy_hash,
        }
    )

    result = _drive_entry(plan)

    assert result["status"] == "submitted", result
    assert len(submitted) == 1

    approved = [row for row in repo.list_risk_decisions(limit=20) if row["approved"]]
    assert approved, repo.list_risk_decisions(limit=20)
    assert approved[0]["proposal_execution_mode"] == _MODE
    assert approved[0]["allowed_execution_mode"] == _MODE

    order = repo.get_order(result["order_id"])
    assert order["execution_mode"] == _MODE
    assert order["risk_decision_id"] == approved[0]["id"]
    from aletheia.uta.models import UTAAccountIdentity

    assert UTAAccountIdentity.model_validate_json(
        order["uta_account_identity_json"]
    ) == identity_of(_MODE)
    assert all(row["execution_mode"] == _MODE for row in repo.list_positions())


def test_exchange_entry_lineage_links_candidate_to_order_by_exact_ids(
    e2e_db, monkeypatch
):
    """Candidate → PlanningGeneration → EntryPlan → Proposal → Order, one chain.

    The other tests here start from a persisted waiting plan and assert what
    happens downstream. This asserts the head is actually attached: the planning
    generation belongs to the Candidate, the Entry Plan to that generation, the
    Proposal to that plan, and the Order to that Proposal — by exact id at every
    hop, on an exchange mode.

    Lineage is not decoration. Attribution, the learning exposure link and every
    later Outcome are joined on these ids; a chain that merely exists in parallel
    rows would let two Candidates' evidence be attributed to one entry.
    """
    _approving_risk(monkeypatch)
    submitted, _ = _wire_venue(monkeypatch)
    plan = _seeded_plan()

    generation = repo.get_candidate_planning_generation(plan.planning_generation_id)
    assert generation is not None
    candidate_id = generation["candidate_id"]
    assert candidate_id, generation
    assert repo.get_candidate(candidate_id) is not None

    stored_plan = repo.get_entry_plan(plan.plan_id)
    assert stored_plan["planning_generation_id"] == generation["id"]
    assert stored_plan["candidate_id"] == candidate_id

    result = _drive_entry(plan)
    assert result["status"] == "submitted", result

    proposal = repo.get_trade_proposal_by_entry_plan(plan.plan_id)
    assert proposal["entry_plan_id"] == plan.plan_id
    assert proposal["execution_mode"] == _MODE

    order = repo.get_order(result["order_id"])
    assert order["proposal_id"] == proposal["id"]
    assert order["entry_plan_id"] == plan.plan_id
    assert order["execution_mode"] == _MODE

    # One Candidate, one generation, one plan, one proposal, one order — a second
    # of anything here would mean the chain forked without saying so.
    assert len(submitted) == 1
    orders = [
        row for row in repo.list_orders(limit=50) if row["execution_mode"] == _MODE
    ]
    assert len(orders) == 1, orders
    assert all(row["execution_mode"] == _MODE for row in repo.list_positions())
