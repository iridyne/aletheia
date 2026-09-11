from datetime import datetime, timedelta, timezone

import pytest

from aletheia.data.symbols import InstrumentId
from aletheia.trading.entry_activation import build_entry_trigger_activation
from aletheia.trading.entry_plan import EntryPlan
from aletheia.trading.momentum_trigger import MomentumTriggerEngine
from aletheia.universe.fast_stream import FastMarketEvent
from aletheia.universe.fast_trigger import TriggerPolicy
from tests.test_entry_plan_execution import _plan, _with_geometry_hash


def _observed_activation(tactic="OPPOSING_BBO_IOC"):
    now = datetime.now(timezone.utc)
    params = {**_plan().trigger_params, "geometry_fingerprint": "f" * 64,
              "geometry_version": "anchor-retrace-reclaim-geometry/v1",
              "sweep_low": 98.5, "sweep_high": 99.0, "atr_abs": 2.0}
    payload = _plan(tactic=tactic).model_dump(mode="json")
    payload.update(trigger_spec_id="anchor-retrace-reclaim", trigger_params=params,
                   approved_at=(now - timedelta(seconds=5)).isoformat())
    plan = EntryPlan.model_validate(_with_geometry_hash(payload))
    instrument = InstrumentId.from_parts(plan.venue, plan.symbol, plan.market_type)
    engine = MomentumTriggerEngine(instrument=instrument, direction="long", breakout_level=100,
        pullback_band=(99, 100), sweep_band=(98.5, 99), entry_zone=(100, 101),
        invalidation_price=98, atr_abs=2, geometry_version=params["geometry_version"],
        geometry_fingerprint=params["geometry_fingerprint"], trigger_spec_id=plan.trigger_spec_id,
        policy=TriggerPolicy(max_spread_bps=10, min_depth_10bps_usd=100))
    engine.confirm_subscription()
    start = int(now.timestamp() * 1000) - 1000

    def event(kind, sequence, offset, **values):
        return FastMarketEvent(event_type=kind, instrument=instrument, sequence=sequence,
            exchange_timestamp_ms=start + offset, received_timestamp_ms=start + offset,
            stream_generation_id="stream-1", subscription_generation_id="subscription-1", **values)

    for value in (
        event("quote", 1, 0, bid=99.49, ask=99.51),
        event("quote", 2, 100, bid=98.89, ask=98.91),
        event("quote", 3, 200, bid=100.09, ask=100.11),
        event("depth", 1, 400, bids=((100.09, 100),), asks=((100.11, 100),)),
        event("trade", 1, 400, price=100.1, size=100, side="buy"),
    ):
        assert engine.observe(value) is None
    ready = engine.observe(event("quote", 4, 600, bid=100.09, ask=100.11))
    assert ready is not None
    return plan, ready, now


def test_activation_binds_real_engine_events_and_rejects_mutation_expiry_and_wrong_plan():
    plan, ready, now = _observed_activation()
    activation = build_entry_trigger_activation(plan, ready, now=now)
    activation.require_valid(plan, now=now)
    with pytest.raises(ValueError, match="identity_mismatch"):
        activation.require_valid(plan.model_copy(update={"plan_id": "another-plan"}), now=now)
    with pytest.raises(ValueError, match="expired"):
        activation.require_valid(plan, now=now + timedelta(seconds=3))
    with pytest.raises(ValueError, match="hash_mismatch"):
        activation.model_copy(update={"direction": "short"}).require_valid(plan, now=now)


@pytest.mark.parametrize("defect", ["state_order", "stream", "sequence", "source_stale", "missing"])
def test_activation_rejects_invalid_trigger_facts(defect):
    plan, ready, now = _observed_activation()
    events = list(ready.activation_events)
    if defect == "state_order":
        events[1] = events[1].model_copy(update={"bid": 100.2, "ask": 100.3})
    elif defect == "stream":
        events[1] = events[1].model_copy(update={"stream_generation_id": "old-stream"})
    elif defect == "sequence":
        events[2] = events[2].model_copy(update={"sequence": events[1].sequence})
    elif defect == "source_stale":
        events[1] = events[1].model_copy(update={"exchange_timestamp_ms": 0})
    else:
        events = []
    with pytest.raises(ValueError, match="entry_trigger_activation"):
        build_entry_trigger_activation(plan, ready.model_copy(update={"activation_events": tuple(events)}), now=now)


def _persist_observed_plan(monkeypatch, tactic):
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import EntryPlanRecord
    from aletheia.trading.entry_plan import entry_plan_payload
    from tests.test_entry_planner import _typed_candidate

    plan, ready, now = _observed_activation(tactic)
    candidate = _typed_candidate(mechanism="continuation", family="momentum")
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    plan = plan.model_copy(update={"planning_generation_id": generation["id"], "candidate_id": candidate.id})
    payload, digest = entry_plan_payload(plan)
    # This test owns only activation persistence. Insert a typed Plan fixture;
    # full Planner/context authority has its separate end-to-end regressions.
    repo._insert("entry_plans", EntryPlanRecord(id=plan.plan_id,
        planning_generation_id=generation["id"], candidate_id=candidate.id,
        planning_key=generation["planning_key"], venue=plan.venue, symbol=plan.symbol,
        market_type=plan.market_type, direction=plan.direction, execution_mode=plan.execution_mode,
        plan_json=payload, plan_hash=digest).model_dump())
    monkeypatch.setattr("aletheia.execution.entry_plan_execution.require_entry_plan_runtime_authority", lambda _: None)
    return plan, ready, now, candidate


@pytest.mark.parametrize("tactic", ["POST_ONLY_LIMIT", "OPPOSING_BBO_IOC"])
def test_activation_persistence_is_atomic_immutable_and_retains_plan_bytes(monkeypatch, tactic):
    import sqlite3
    from aletheia.storage import repositories as repo
    from aletheia.trading.entry_plan import entry_plan_payload

    plan, ready, now, candidate = _persist_observed_plan(monkeypatch, tactic)
    payload, digest = entry_plan_payload(plan)
    activation = build_entry_trigger_activation(plan, ready, now=now)
    with pytest.raises(ValueError, match="monitoring_mismatch"):
        repo.bind_entry_trigger_activation(activation, expected_activation_id=None, now=now)
    assert repo.get_entry_trigger_activation(activation.activation_id) is None
    repo.record_entry_plan_monitoring(plan.plan_id, {"state": "ready", "stream_generation_id": "stream-1",
        "subscription_generation_id": "subscription-1"})
    assert repo.bind_entry_trigger_activation(activation, expected_activation_id=None, now=now)
    assert repo.bind_entry_trigger_activation(activation, expected_activation_id=None, now=now)
    competitor = build_entry_trigger_activation(plan, ready, now=now)
    assert not repo.bind_entry_trigger_activation(competitor, expected_activation_id=None, now=now)
    assert repo.get_entry_trigger_activation(competitor.activation_id) is None
    row = repo.get_entry_plan(plan.plan_id)
    assert row["status"] == "triggered" and row["trigger_activation_id"] == activation.activation_id
    assert row["plan_json"] == payload and row["plan_hash"] == digest
    lifecycle = repo.get_candidate_entry_lifecycle(candidate.id)
    assert lifecycle["lifecycle_state"] == "TRIGGERED"
    assert lifecycle["current_plan_id"] == plan.plan_id
    monitoring_bytes = row["monitoring_evidence_json"]
    assert not repo.record_entry_plan_monitoring(plan.plan_id, {"state": "ready",
        "stream_generation_id": "stream-1", "subscription_generation_id": "subscription-1"})
    assert repo.get_entry_plan(plan.plan_id)["monitoring_evidence_json"] == monitoring_bytes
    assert any(event["event"] == "trigger_activation_created" for event in lifecycle["cycles"][0]["events"])
    from aletheia.execution.entry_plan_execution import execute_entry_plan
    from aletheia.execution.gateway import execute_existing_proposal as actual_gateway

    submitted = []
    monkeypatch.setattr("aletheia.execution.gateway.execute_existing_proposal",
        lambda proposal_id, *args, **kwargs: submitted.append(proposal_id) or {"status": "submitted"})
    kwargs = dict(bid=ready.bid, ask=ready.ask, observed_at=now.isoformat(), source_observed_at=now.isoformat(),
        stream_generation_id=ready.stream_generation_id, subscription_generation_id=ready.subscription_generation_id,
        quote_sequence=ready.quote_sequence, required_channels=("quote", "trade", "depth", "candle"),
        depth_bids=ready.depth_bids, depth_asks=ready.depth_asks,
        depth_exchange_timestamp_ms=ready.depth_exchange_timestamp_ms,
        depth_received_timestamp_ms=ready.depth_received_timestamp_ms, depth_sequence=ready.depth_sequence,
        trigger_activation_id=activation.activation_id, trigger_activation_hash=activation.activation_hash, now=now)
    if tactic == "POST_ONLY_LIMIT":
        import asyncio
        from aletheia.agent_runtime.fast_trigger_watch import _submit_activated_entry_plan
        from aletheia.trading.entry_activation import EntryTriggerActivationV1

        restored = EntryTriggerActivationV1.model_validate_json(
            repo.get_entry_trigger_activation(activation.activation_id)["activation_json"])
        crossing = ready.activation_events[-1].model_copy(update={"bid": 99.9, "ask": 99.95})
        asyncio.run(_submit_activated_entry_plan(plan, restored, crossing))
        assert repo.get_trade_proposal_by_entry_plan(plan.plan_id) is None
        assert repo.get_entry_plan(plan.plan_id)["status"] == "triggered"
    with pytest.raises(ValueError, match="generation_mismatch"):
        execute_entry_plan(plan.plan_id, **{**kwargs, "stream_generation_id": "another-stream"})
    assert repo.get_trade_proposal_by_entry_plan(plan.plan_id) is None
    assert execute_entry_plan(plan.plan_id, **kwargs)["status"] == "submitted"
    assert execute_entry_plan(plan.plan_id, **kwargs)["status"] == "submitted"
    assert len(submitted) == 2 and len(set(submitted)) == 1
    from aletheia.execution.entry_plan_execution import build_entry_execution_receipt
    from aletheia.storage.models import TradeProposal
    import hashlib

    proposal = TradeProposal.model_validate(repo.get_trade_proposal_by_entry_plan(plan.plan_id))
    receipt = build_entry_execution_receipt(plan, proposal, **{key: value for key, value in kwargs.items() if key != "now"})
    if tactic == "OPPOSING_BBO_IOC":
        from aletheia.execution.entry_plan_execution import require_execution_receipt_activation

        require_execution_receipt_activation(proposal, receipt, now=now)
        for delta in (-0.01, 0.01):
            changed_quote = {**kwargs, "bid": ready.bid + delta, "ask": ready.ask + delta}
            changed_receipt = build_entry_execution_receipt(plan, proposal,
                **{key: value for key, value in changed_quote.items() if key != "now"})
            with pytest.raises(ValueError, match="entry_execution_receipt_intent_price_mismatch"):
                require_execution_receipt_activation(proposal, changed_receipt, now=now)
    # Even a self-consistent execution-receipt hash cannot replace the durable
    # activation. Verify the real Gateway refuses before acquiring ownership.
    forged = receipt.model_copy(update={"trigger_activation_id": "forged"})
    forged = forged.model_copy(update={"receipt_hash": hashlib.sha256(forged.canonical_json().encode()).hexdigest()})
    result = actual_gateway(proposal.id, proposal.execution_mode, execution_receipt=forged)
    assert result["reason_code"] == "entry_trigger_activation_required"
    assert repo.get_proposal_execution_claim(proposal.id) is None
    # A later fresh trigger may recover the same unsubmitted immutable intent.
    # Advance the fixture clock, keeping exchange/receive/state references exact.
    later = now + timedelta(seconds=3)
    data = ready.model_dump(mode="json")
    data["stream_generation_id"] = "stream-2"
    data["observed_at"] = later.isoformat()
    for key in tuple(data):
        if key.endswith("_timestamp_ms"):
            data[key] += 3000
    for event in data["activation_events"]:
        event["stream_generation_id"] = "stream-2"
        event["exchange_timestamp_ms"] += 3000
        event["received_timestamp_ms"] += 3000
    for key in tuple(data["entry_trigger"]):
        if key.endswith("_at_ms"):
            data["entry_trigger"][key] += 3000
    renewed = build_entry_trigger_activation(plan, type(ready).model_validate(data), now=later)
    repo.record_entry_plan_monitoring(plan.plan_id, {"state": "ready", "stream_generation_id": "stream-2",
        "subscription_generation_id": "subscription-1"})
    assert repo.get_candidate_entry_lifecycle(candidate.id)["lifecycle_state"] == "WAITING_TRIGGER"
    # A dead preparation owner must not prevent earning a new trigger proof.
    assert repo.insert_proposal_execution_claim(proposal.id, "dead-owner", now.isoformat(), later.isoformat())
    assert not repo.entry_plan_activation_can_refresh(plan.plan_id)
    assert not repo.bind_entry_trigger_activation(renewed, expected_activation_id=activation.activation_id, now=now)
    repo.execute_write("UPDATE proposal_execution_claims SET lease_expires_at=? WHERE proposal_id=?",
                       ((now - timedelta(seconds=1)).isoformat(), proposal.id))
    assert repo.entry_plan_activation_can_refresh(plan.plan_id)
    repo.update_trade_proposal(proposal.id, status="executing")
    assert not repo.entry_plan_activation_can_refresh(plan.plan_id)
    repo.update_trade_proposal(proposal.id, status=proposal.status)
    with monkeypatch.context() as order_guard:
        order_guard.setattr(repo, "has_order_for_proposal", lambda _: True)
        assert not repo.entry_plan_activation_can_refresh(plan.plan_id)
    assert repo.get_proposal_execution_claim(proposal.id)["owner_token"] == "dead-owner"
    original_proposal = repo.get_trade_proposal(proposal.id)
    assert repo.bind_entry_trigger_activation(renewed, expected_activation_id=activation.activation_id, now=later)
    assert repo.get_entry_trigger_activation(activation.activation_id)["activation_hash"] == activation.activation_hash
    assert repo.get_trade_proposal(proposal.id) == original_proposal
    timeline = repo.get_candidate_entry_lifecycle(candidate.id)["cycles"][0]["events"]
    assert [event["at"] for event in timeline if event["event"] == "trigger_activation_created"] == [
        activation.created_at, renewed.created_at]
    from aletheia.trading.entry_activation import require_entry_trigger_activation

    with pytest.raises(ValueError, match="activation_required"):
        require_entry_trigger_activation(plan, activation.activation_id, activation.activation_hash, now=later)
    require_entry_trigger_activation(plan, renewed.activation_id, renewed.activation_hash, now=later)
    for statement in ("UPDATE entry_trigger_activations SET activation_json='{}' WHERE id=?",
                      "DELETE FROM entry_trigger_activations WHERE id=?"):
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            repo.execute_write(statement, (activation.activation_id,))


@pytest.mark.asyncio
@pytest.mark.parametrize("tactic", ["POST_ONLY_LIMIT", "OPPOSING_BBO_IOC"])
@pytest.mark.parametrize("scenario", ["cold_start", "restored", "reconnect"])
async def test_watch_loop_restores_durable_activation_and_submits_same_plan(monkeypatch, tactic, scenario):
    """Restart must retain subscriptions even when a valid proof needs no engine."""
    import asyncio
    from types import SimpleNamespace
    from aletheia.agent_runtime import fast_trigger_watch as watch
    from aletheia.config.schema import AletheiaConfig
    from aletheia.storage import repositories as repo

    plan, ready, now, _ = _persist_observed_plan(monkeypatch, tactic)
    # This test owns reconnect/restoration, not elapsed-time expiry (covered above).
    # Keep immutable event time and authority time aligned across thread scheduling.
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now
    monkeypatch.setattr(watch, "datetime", Clock)
    monkeypatch.setattr("aletheia.execution.entry_plan_execution.datetime", Clock)
    activation = build_entry_trigger_activation(plan, ready, now=now)
    monitoring = {"state": "ready", "stream_generation_id": ready.stream_generation_id,
                  "subscription_generation_id": ready.subscription_generation_id}
    if scenario != "cold_start":
        repo.record_entry_plan_monitoring(plan.plan_id, monitoring)
        assert repo.bind_entry_trigger_activation(activation, expected_activation_id=None, now=now)
    if scenario == "reconnect":
        data = ready.model_dump(mode="json")
        data["stream_generation_id"] = "stream-2"
        for event in data["activation_events"]:
            event["stream_generation_id"] = "stream-2"
        ready = type(ready).model_validate(data)
        monitoring = {**monitoring, "stream_generation_id": "stream-2"}
        monkeypatch.setattr(watch, "_WATCHER_RELOAD_INTERVAL_SEC", 0.005)
    cfg = AletheiaConfig()
    cfg.agent.permission_mode = "Full"
    cfg.exchange.paper_execution_mode = "bitget_demo"
    cfg.market_data.ccxt_exchange = "bitget"
    cfg.universe.max_spread_bps = 10
    cfg.universe.min_depth_10bps_usd = 100
    monkeypatch.setattr("aletheia.config.loader.load_config", lambda **kwargs: cfg)
    monkeypatch.setattr("aletheia.risk.policy.resolve_risk_policy", lambda *args: SimpleNamespace(
        policy=SimpleNamespace(policy_id=plan.risk_policy_id, revision=plan.risk_policy_revision),
        policy_hash=plan.risk_policy_hash))
    stop = asyncio.Event()
    monkeypatch.setattr(watch, "_stop_event", stop)
    submitted = []

    def gateway(proposal_id, *args, **kwargs):
        row = repo.get_entry_plan(plan.plan_id)
        assert repo.get_entry_trigger_activation(row["trigger_activation_id"]) is not None
        if scenario == "restored":
            assert row["trigger_activation_id"] == activation.activation_id
        elif scenario == "reconnect":
            assert row["trigger_activation_id"] != activation.activation_id
        submitted.append(proposal_id)
        return {"status": "submitted"}

    monkeypatch.setattr("aletheia.execution.gateway.execute_existing_proposal", gateway)

    class Stream:
        async def connect(self, instruments):
            self.instruments = set(instruments)

        async def set_instruments(self, instruments):
            self.instruments = set(instruments)

        def monitoring_state(self, *_args):
            return monitoring

        async def close(self):
            pass

        async def events(self):
            quote = ready.activation_events[-1]
            if quote.instrument in self.instruments:
                if scenario == "reconnect":
                    # First event publishes the new generation; the next loader
                    # pass must create a fresh engine without replaying old proof.
                    yield quote
                    assert submitted == []
                    await asyncio.sleep(0.02)
                if scenario != "restored":
                    for event in ready.activation_events[:-1]:
                        yield event
                        if scenario == "reconnect":
                            await asyncio.sleep(0.02)
                    for kind, values in (
                        ("depth", {"bids": ready.depth_bids, "asks": ready.depth_asks}),
                        ("trade", {"price": 100.1, "size": 100, "side": "buy"}),
                    ):
                        yield FastMarketEvent(event_type=kind, instrument=quote.instrument, sequence=1,
                            exchange_timestamp_ms=ready.depth_exchange_timestamp_ms,
                            received_timestamp_ms=ready.depth_received_timestamp_ms,
                            stream_generation_id=ready.stream_generation_id,
                            subscription_generation_id=ready.subscription_generation_id, **values)
                yield quote
                # Execution no longer blocks the market consumer. A later natural
                # quote retries after the obsolete reconnect attempt releases ownership.
                for wake in range(20):
                    if submitted:
                        break
                    await asyncio.sleep(0.01)
                    yield quote.model_copy(update={"sequence": quote.sequence + wake + 1})
            stop.set()

    stream = Stream()
    monkeypatch.setattr(watch, "fast_market_stream_for_config", lambda _cfg: stream)
    await asyncio.wait_for(watch._watch_loop(cfg), timeout=2)
    assert ready.activation_events[-1].instrument in stream.instruments
    assert len(set(submitted)) == 1
    assert repo.get_trade_proposal_by_entry_plan(plan.plan_id)["id"] == submitted[0]
    if scenario != "cold_start":
        assert repo.get_entry_trigger_activation(activation.activation_id)["activation_hash"] == activation.activation_hash


@pytest.mark.parametrize("tactic", ["POST_ONLY_LIMIT", "OPPOSING_BBO_IOC"])
@pytest.mark.parametrize("expiry_stage", ["preflight", "authoritative", "broker"])
def test_expired_activation_before_broker_returns_to_waiting(monkeypatch, tactic, expiry_stage):
    from aletheia.storage import repositories as repo
    from aletheia.execution.entry_plan_execution import build_entry_plan_order_intent, build_entry_execution_receipt
    from aletheia.execution.gateway import execute_existing_proposal as actual_gateway

    plan, ready, now, _ = _persist_observed_plan(monkeypatch, tactic)
    activation = build_entry_trigger_activation(plan, ready, now=now)
    repo.record_entry_plan_monitoring(plan.plan_id, {"state": "ready", "stream_generation_id": "stream-1",
        "subscription_generation_id": "subscription-1"})
    assert repo.bind_entry_trigger_activation(activation, expected_activation_id=None, now=now)
    facts = dict(bid=ready.bid, ask=ready.ask, observed_at=now.isoformat(), source_observed_at=now.isoformat(),
        trigger_activation_id=activation.activation_id, trigger_activation_hash=activation.activation_hash)
    proposal = build_entry_plan_order_intent(plan, **facts, now=now)
    repo.insert_trade_proposal(proposal)
    receipt = build_entry_execution_receipt(plan, proposal, **facts,
        stream_generation_id=ready.stream_generation_id, subscription_generation_id=ready.subscription_generation_id,
        quote_sequence=ready.quote_sequence, required_channels=("quote", "trade", "depth", "candle"),
        depth_bids=ready.depth_bids, depth_asks=ready.depth_asks,
        depth_exchange_timestamp_ms=ready.depth_exchange_timestamp_ms,
        depth_received_timestamp_ms=ready.depth_received_timestamp_ms, depth_sequence=ready.depth_sequence)
    from types import SimpleNamespace
    from aletheia.execution import gateway

    audit = []
    monkeypatch.setattr(gateway, "write_audit", audit.append)
    broker_calls = []
    prepared = [False]

    class PreparationClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now + timedelta(seconds=3) if prepared[0] else now

    def risk_decision(*args, **kwargs):
        rejected = kwargs.get("decision_role") == expiry_stage
        return SimpleNamespace(approved=not rejected, allowed_execution_mode="bitget_demo",
            decision_id="fixture-risk", violations=["entry_execution_receipt_expired"] if rejected else [],
            message="Execution receipt expired")

    def finish_preparation(*_args):
        prepared[0] = True
        return None

    with monkeypatch.context() as gate:
        gate.setattr(gateway, "datetime", PreparationClock)
        gate.setattr(gateway, "evaluate_proposal", risk_decision)
        def prepare(*args, **kwargs):
            assert kwargs == {"allow_increase": False}
            return proposal.quantity, None
        gate.setattr(gateway, "_prepare_exchange_quantity", prepare)
        gate.setattr(gateway, "_check_symbol_availability", finish_preparation)
        def coordinator(**kwargs):
            kwargs["validate_submission"]()
            broker_calls.append(kwargs)
            return {"status": "failed"}
        gate.setattr("aletheia.execution.coordinator.execute_order", coordinator)
        result = actual_gateway(proposal.id, proposal.execution_mode, execution_receipt=receipt)
    assert result["status"] == ("waiting_market" if expiry_stage == "broker" else "error")
    assert result["reason_code"] == ("entry_trigger_activation_expired" if expiry_stage == "broker"
        else "entry_execution_receipt_rejected")
    assert broker_calls == []
    assert repo.get_trade_proposal(proposal.id)["status"] == ("pending" if expiry_stage == "broker" else "error")
    assert repo.get_entry_plan(plan.plan_id)["status"] == ("triggered" if expiry_stage == "broker" else "failed")
    assert any(event["event_type"] == ("entry_execution_stage" if expiry_stage == "broker" else "execution_error") for event in audit)
    assert repo.get_proposal_execution_claim(proposal.id) is None
