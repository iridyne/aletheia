"""Immutable trigger facts bound to one Plan; quotes alone cannot activate it."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from aletheia.data.symbols import InstrumentId
from aletheia.trading.entry_plan import EntryPlan, entry_plan_payload
from aletheia.trading.trigger_specs import require_registered_trigger_spec
from aletheia.universe.fast_trigger import TriggerReady


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _instant(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("entry_trigger_activation_timezone_required")
    return result.astimezone(timezone.utc)


def _spec_identity(spec) -> str:
    from aletheia.trading.entry_contract import _implementation_semantics

    return _hash({"id": spec.spec_id, "channels": spec.data_requirements,
                  "freshness_ms": spec.freshness_ms,
                  "validation": _implementation_semantics(spec.validate_params)})


class EntryTriggerActivationV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal["entry-trigger-activation/v1"] = "entry-trigger-activation/v1"
    activation_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    instrument: InstrumentId
    direction: Literal["long", "short"]
    trigger_spec_id: str
    trigger_spec_version: str
    compiled_geometry_fingerprint: str
    trigger_params_hash: str
    snapshot: TriggerReady
    created_at: str
    expires_at: str
    activation_hash: str

    def unsigned_payload(self) -> dict:
        return self.model_dump(mode="json", exclude={"activation_hash"})

    def require_valid(self, plan: EntryPlan, *, now: datetime) -> None:
        if self.activation_hash != _hash(self.unsigned_payload()):
            raise ValueError("entry_trigger_activation_hash_mismatch")
        spec, params = require_registered_trigger_spec(plan.trigger_spec_id, plan.trigger_params)
        if (plan.execution_mode != "TRIGGERED" or self.plan_id != plan.plan_id
            or self.plan_hash != entry_plan_payload(plan)[1]
            or self.instrument != InstrumentId.from_parts(plan.venue, plan.symbol, plan.market_type)
            or self.direction != plan.direction or self.trigger_spec_id != spec.spec_id
            or self.trigger_spec_version != _spec_identity(spec)
            or self.compiled_geometry_fingerprint != plan.compiled_geometry_fingerprint
            or self.trigger_params_hash != _hash(params)):
            raise ValueError("entry_trigger_activation_identity_mismatch")
        created, expires = _instant(self.created_at), _instant(self.expires_at)
        if not created <= now < expires or expires > _instant(plan.expires_at):
            raise ValueError("entry_trigger_activation_expired")
        ready = self.snapshot
        if not ready.stream_generation_id or not ready.subscription_generation_id:
            raise ValueError("entry_trigger_activation_generation_required")
        trigger = ready.entry_trigger
        if (trigger.get("trigger_spec_id") != plan.trigger_spec_id
            or trigger.get("geometry_fingerprint") != params.get("geometry_fingerprint")):
            raise ValueError("entry_trigger_activation_identity_mismatch")
        # Current canonical compiler emits only this trigger. Historical specs
        # remain readable but cannot acquire a new production activation.
        if plan.trigger_spec_id != "anchor-retrace-reclaim":
            raise ValueError("entry_trigger_activation_spec_not_current")
        events = ready.activation_events
        if len(events) != 4:
            raise ValueError("entry_trigger_activation_state_evidence_required")
        pullback, sweep, reclaim, confirmation = events
        previous_sequence = -1
        previous_time = -1
        for event in events:
            if (event.event_type != "quote" or event.instrument != self.instrument
                or event.stream_generation_id != ready.stream_generation_id
                or event.subscription_generation_id != ready.subscription_generation_id
                or event.sequence <= previous_sequence
                or event.received_timestamp_ms < previous_time
                or event.received_timestamp_ms < int(_instant(plan.approved_at).timestamp() * 1000)
                or not 0 <= event.received_timestamp_ms - event.exchange_timestamp_ms <= spec.freshness_ms
                or event.bid is None or event.ask is None
                or not 0 < event.bid <= event.ask):
                raise ValueError("entry_trigger_activation_event_invalid")
            previous_sequence, previous_time = event.sequence, event.received_timestamp_ms
        mids = [(event.bid + event.ask) / 2 for event in events]
        long = plan.direction == "long"
        if (not params["pullback_low"] <= mids[0] <= params["pullback_high"]
            or not (mids[0] <= params["breakout_level"] if long else mids[0] >= params["breakout_level"])
            or not (params["sweep_low"] <= mids[1] < params["breakout_level"] if long
                    else params["breakout_level"] < mids[1] <= params["sweep_high"])
            or not all(mid > params["breakout_level"] if long else mid < params["breakout_level"] for mid in mids[2:])
            or confirmation.received_timestamp_ms - reclaim.received_timestamp_ms < 300):
            raise ValueError("entry_trigger_activation_state_order_invalid")
        path = trigger.get("state_path") or []
        allowed = {"armed": {"pullback"}, "pullback": {"sweep"}, "sweep": {"reclaim"},
                   "reclaim": {"sweep", "pullback", "triggered"}}
        if (not path or path[0] != "armed" or path[-1] != "triggered"
            or any(right not in allowed.get(left, set()) for left, right in zip(path, path[1:]))):
            raise ValueError("entry_trigger_activation_state_order_invalid")
        if (confirmation.sequence != ready.quote_sequence
            or confirmation.received_timestamp_ms != ready.quote_received_timestamp_ms
            or confirmation.exchange_timestamp_ms != ready.quote_exchange_timestamp_ms
            or (confirmation.bid, confirmation.ask) != (ready.bid, ready.ask)):
            raise ValueError("entry_trigger_activation_quote_mismatch")
        expected_trigger = {
            "pullback_seen_at_ms": pullback.received_timestamp_ms,
            "sweep_seen_at_ms": sweep.received_timestamp_ms,
            "reclaim_seen_at_ms": reclaim.received_timestamp_ms,
            "first_reclaim_quote_at_ms": reclaim.received_timestamp_ms,
            "first_reclaim_quote_sequence": reclaim.sequence,
            "confirming_reclaim_quote_at_ms": confirmation.received_timestamp_ms,
            "confirming_reclaim_quote_sequence": confirmation.sequence,
        }
        if any(trigger.get(key) != value for key, value in expected_trigger.items()):
            raise ValueError("entry_trigger_activation_state_evidence_mismatch")
        created_ms = int(created.timestamp() * 1000)
        for channel in ("quote", "trade", "depth"):
            received = getattr(ready, f"{channel}_received_timestamp_ms")
            source = getattr(ready, f"{channel}_exchange_timestamp_ms")
            if not 0 <= created_ms - received <= spec.freshness_ms or not 0 <= created_ms - source <= spec.freshness_ms:
                raise ValueError("entry_trigger_activation_market_stale")
        oldest_fact = min(getattr(ready, f"{channel}_{clock}_timestamp_ms")
                          for channel in ("quote", "trade", "depth") for clock in ("received", "exchange"))
        maximum_expiry = min(_instant(plan.expires_at),
                             datetime.fromtimestamp((oldest_fact + spec.freshness_ms) / 1000, timezone.utc))
        if expires > maximum_expiry:
            raise ValueError("entry_trigger_activation_expiry_invalid")


def build_entry_trigger_activation(plan: EntryPlan, ready: TriggerReady, *, now: datetime) -> EntryTriggerActivationV1:
    spec, params = require_registered_trigger_spec(plan.trigger_spec_id, plan.trigger_params)
    oldest_fact = min(getattr(ready, f"{channel}_{clock}_timestamp_ms")
                      for channel in ("quote", "trade", "depth") for clock in ("received", "exchange"))
    activation = EntryTriggerActivationV1(
        activation_id=uuid4().hex,
        plan_id=plan.plan_id, plan_hash=entry_plan_payload(plan)[1],
        instrument=InstrumentId.from_parts(plan.venue, plan.symbol, plan.market_type),
        direction=plan.direction, trigger_spec_id=spec.spec_id, trigger_spec_version=_spec_identity(spec),
        compiled_geometry_fingerprint=plan.compiled_geometry_fingerprint,
        trigger_params_hash=_hash(params), snapshot=ready,
        created_at=now.isoformat(),
        expires_at=min(_instant(plan.expires_at),
                       datetime.fromtimestamp((oldest_fact + spec.freshness_ms) / 1000, timezone.utc)).isoformat(),
        activation_hash="",
    )
    activation = activation.model_copy(update={"activation_hash": _hash(activation.unsigned_payload())})
    activation.require_valid(plan, now=now)
    return activation


def require_entry_trigger_activation(
    plan: EntryPlan, activation_id: str | None, activation_hash: str | None, *, now: datetime,
    stream_generation_id: str | None = None, subscription_generation_id: str | None = None,
    quote_sequence: int | None = None,
) -> EntryTriggerActivationV1 | None:
    from aletheia.storage import repositories as repo

    if plan.execution_mode != "TRIGGERED":
        return None
    if not activation_id or not activation_hash:
        raise ValueError("entry_trigger_activation_required")
    row = repo.get_entry_trigger_activation(activation_id)
    owner = repo.get_entry_plan(plan.plan_id)
    if row is None or owner is None or owner.get("trigger_activation_id") != activation_id:
        raise ValueError("entry_trigger_activation_required")
    if row["activation_hash"] != activation_hash:
        raise ValueError("entry_trigger_activation_hash_mismatch")
    activation = EntryTriggerActivationV1.model_validate_json(row["activation_json"])
    if (activation.activation_id != activation_id or activation.activation_hash != activation_hash
        or row["plan_id"] != plan.plan_id):
        raise ValueError("entry_trigger_activation_identity_mismatch")
    activation.require_valid(plan, now=now)
    if owner["plan_hash"] != activation.plan_hash:
        raise ValueError("entry_plan_hash_mismatch")
    if owner["status"] not in {"triggered", "risk_reserved"}:
        raise ValueError("entry_trigger_activation_plan_not_executable")
    monitoring = json.loads(owner.get("monitoring_evidence_json") or "{}")
    if (owner.get("monitoring_state") != "ready"
        or monitoring.get("stream_generation_id") != activation.snapshot.stream_generation_id
        or monitoring.get("subscription_generation_id") != activation.snapshot.subscription_generation_id):
        raise ValueError("entry_trigger_activation_monitoring_mismatch")
    if stream_generation_id is not None:
        snapshot = activation.snapshot
        if (snapshot.stream_generation_id != stream_generation_id
            or snapshot.subscription_generation_id != subscription_generation_id
            or quote_sequence is None or quote_sequence < snapshot.quote_sequence):
            raise ValueError("entry_trigger_activation_generation_mismatch")
    return activation
