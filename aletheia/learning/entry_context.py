"""Read-only, cutoff-safe Entry Learning evidence for Grounded planning."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ENTRY_LEARNING_CONTEXT_VERSION = "entry-learning-context/v1"
ENTRY_LEARNING_OUTCOME_VERSION = "entry-learning-outcome/v1"
ENTRY_LEARNING_METRIC_VERSION = "entry-learning-metrics/v1"
ENTRY_LEARNING_ADAPTER_ID = "mechanism-aware-entry-learning"
ENTRY_LEARNING_ADAPTER_VERSION = "v1"
ENTRY_LEARNING_INTERFERENCE_KEY = "mechanism-aware-grounded-entry"


def _instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("entry_learning_timezone_required")
    return parsed.astimezone(timezone.utc)


def _fingerprint(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def _planning_inference_evidence(
    db, *, generation_id: str, cutoff: datetime,
) -> tuple[set[tuple[str, ...]], float | None, list[str], set[str]]:
    """Read exact logical/physical inference identities already tied to one generation."""

    tables = {
        str(row[0]) for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
            "('llm_context_artifacts','llm_inference_calls','runtime_cost_events')"
        )
    }
    required = {"llm_context_artifacts", "llm_inference_calls", "runtime_cost_events"}
    if tables != required:
        return set(), None, [], {"entry_learning_inference_ledger_unavailable"}
    calls = [dict(row) for row in db.execute(
        "SELECT DISTINCT c.* FROM llm_context_artifacts a "
        "JOIN llm_inference_calls c ON c.logical_call_id=a.logical_call_id "
        "WHERE a.stable_id=? AND a.caller='agent_shell.entry_planner' "
        "AND c.finished_at IS NOT NULL AND julianday(c.finished_at)<=julianday(?) "
        "ORDER BY c.started_at,c.logical_call_id",
        (f"planning-generation:{generation_id}", cutoff.isoformat()),
    ).fetchall()]
    if not calls or not any(row.get("status") == "success" for row in calls):
        return set(), None, [], {"entry_learning_inference_identity_unavailable"}
    identities = {
        (
            str(row.get("final_provider") or ""),
            str(row.get("final_model") or ""),
            str(row.get("prompt_version") or ""),
            str(row.get("prompt_static_sha256") or ""),
            str(row.get("output_schema_sha256") or ""),
        )
        for row in calls
    }
    reasons: set[str] = set()
    if len(identities) != 1 or not all(all(value for value in item) for item in identities):
        reasons.add("entry_learning_mixed_model_identity")
    call_ids = [str(row["logical_call_id"]) for row in calls]
    placeholders = ",".join("?" for _ in call_ids)
    costs = [dict(row) for row in db.execute(
        f"SELECT * FROM runtime_cost_events WHERE call_id IN ({placeholders}) "
        "AND julianday(COALESCE(finished_at,started_at))<=julianday(?) "
        "ORDER BY started_at,id",
        (*call_ids, cutoff.isoformat()),
    ).fetchall()]
    if not costs or any(
        row.get("estimated_cost_usd") is None
        or row.get("cost_status") == "unknown"
        or str(row.get("currency") or "") != "USD"
        for row in costs
    ):
        reasons.add("entry_learning_model_data_cost_unavailable")
        cost = None
    else:
        cost = sum(float(row["estimated_cost_usd"]) for row in costs)
        if not math.isfinite(cost) or cost < 0:
            reasons.add("entry_learning_model_data_cost_invalid")
            cost = None
    refs = [f"llm-inference:{call_id}" for call_id in call_ids]
    refs.extend(f"runtime-cost:{row['id']}" for row in costs)
    return identities, cost, refs, reasons


class EntryLearningContextV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal["entry-learning-context/v1"] = ENTRY_LEARNING_CONTEXT_VERSION
    as_of_at: str
    status: Literal["unavailable", "observational", "experimental"]
    scope: dict[str, Any]
    support: dict[str, Any]
    metrics: dict[str, Any]
    evidence_refs: tuple[str, ...] = Field(default_factory=tuple, max_length=64)
    source_fingerprint: str | None
    experiment_identity: dict[str, Any] | None = None
    outcome_contract_version: str = ENTRY_LEARNING_OUTCOME_VERSION
    metric_version: str = ENTRY_LEARNING_METRIC_VERSION
    blocking_reasons: tuple[str, ...] = ()

    @field_validator("as_of_at")
    @classmethod
    def _utc_cutoff(cls, value: str) -> str:
        return _instant(value).isoformat()

    @model_validator(mode="after")
    def _bounded(self) -> "EntryLearningContextV1":
        for name in ("complete_outcome_coverage",):
            value = self.support.get(name)
            if value is not None and (
                not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1
            ):
                raise ValueError(f"entry_learning_{name}_invalid")
        for name in ("fill_rate", "no_plan_rate", "no_trigger_rate", "no_fill_rate"):
            value = self.metrics.get(name)
            if value is not None and (
                not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1
            ):
                raise ValueError(f"entry_learning_{name}_invalid")
        if self.status == "unavailable" and not self.blocking_reasons:
            raise ValueError("entry_learning_unavailable_reason_required")
        if self.status != "unavailable" and (not self.source_fingerprint or self.blocking_reasons):
            raise ValueError("entry_learning_available_identity_invalid")
        if self.status == "experimental":
            identity = self.experiment_identity or {}
            if (
                identity.get("assignment") != "treatment"
                or not identity.get("observation_id")
                or not identity.get("manifest_hash")
                or not identity.get("receipt_id")
            ):
                raise ValueError("entry_learning_experiment_identity_invalid")
        elif self.experiment_identity is not None:
            raise ValueError("entry_learning_experiment_identity_forbidden")
        return self


@dataclass(frozen=True)
class EntryLearningTreatmentAdapter:
    """Lab adapter whose only behavior delta is bounded prompt evidence."""

    adapter_id: str = ENTRY_LEARNING_ADAPTER_ID
    adapter_version: str = ENTRY_LEARNING_ADAPTER_VERSION
    behavior_dimension: str = "entry_learning_context"
    requires_outcome_seed: bool = False

    def assignment_unit_key(self, context: dict[str, Any]) -> str:
        return str(context["planning_generation_id"])

    def eligible(self, manifest, context: dict[str, Any]) -> bool:
        population = manifest.eligible_population or {}
        for key in (
            "mechanism", "direction", "market_type", "candidate_selection_policy",
            "candidate_selection_policy_epoch",
        ):
            if population.get(key) != context.get(key):
                return False
        return (
            manifest.interference_key == ENTRY_LEARNING_INTERFERENCE_KEY
            and manifest.behavior_policy_epoch_ref
            == context.get("candidate_selection_policy_epoch")
            and manifest.deterministic_baseline == {"include_learning_context": False}
            and manifest.control_payload == {"include_learning_context": False}
            and manifest.treatment_payload == {"include_learning_context": True}
        )

    def deterministic_baseline(self, context: dict[str, Any]) -> dict[str, Any]:
        return {"include_learning_context": False}

    def apply_payload(
        self,
        baseline: dict[str, Any],
        payload: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        include = payload.get("include_learning_context")
        if not isinstance(include, bool):
            raise ValueError("entry_learning_treatment_payload_invalid")
        return {"include_learning_context": include}

    def build_outcome_seed(self, context: dict[str, Any]) -> None:
        return None


def resolve_entry_learning_treatment(
    *, planning_generation_id: str, mechanism: str, direction: str,
    market_type: str, policy: str, policy_epoch: str | None,
    as_of_at: str,
):
    """Resolve the canonical Lab treatment; unavailable paths stay empty."""

    from aletheia.observation_lab.canonical import (
        register_treatment_adapter,
        registered_treatment_adapters,
        resolve_observation_treatment,
    )

    adapter = EntryLearningTreatmentAdapter()
    key = (adapter.adapter_id, adapter.adapter_version)
    if key not in registered_treatment_adapters():
        register_treatment_adapter(adapter)
    context = {
        "planning_generation_id": planning_generation_id,
        "assignment_key": planning_generation_id,
        "mechanism": mechanism,
        "direction": direction,
        "market_type": market_type,
        "candidate_selection_policy": policy,
        "candidate_selection_policy_epoch": policy_epoch,
        "interference_key": ENTRY_LEARNING_INTERFERENCE_KEY,
    }
    return resolve_observation_treatment(
        adapter_id=adapter.adapter_id,
        adapter_version=adapter.adapter_version,
        candidate_key=planning_generation_id,
        context=context,
        baseline=adapter.deterministic_baseline(context),
        now=as_of_at,
        consumer="entry_planner",
        consumer_stage="entry_analysis_context",
    )


def empty_entry_learning_context(
    *, as_of_at: str, mechanism: str, direction: str, market_type: str,
    policy: str, policy_epoch: str | None,
    reasons: tuple[str, ...] = ("entry_learning_evidence_unavailable",),
) -> EntryLearningContextV1:
    return EntryLearningContextV1(
        as_of_at=as_of_at,
        status="unavailable",
        scope={
            "mechanism": mechanism, "direction": direction, "market_type": market_type,
            "entry_profile_id": "mechanism-aware-grounded/v1", "horizon": "4h",
            "candidate_selection_policy": policy, "policy_epoch": policy_epoch,
        },
        support={
            "independent_n": 0, "distinct_symbols": 0, "distinct_dates": 0,
            "complete_outcome_coverage": 0.0,
        },
        metrics={
            "intent_to_treat_net_utility_pct": None, "fill_rate": None,
            "no_plan_rate": None, "no_trigger_rate": None, "no_fill_rate": None,
            "mae_p95_pct": None, "mfe_pct": None, "mfe_to_realized_capture": None,
            "model_and_data_cost": None,
        },
        evidence_refs=(), source_fingerprint=None,
        blocking_reasons=tuple(sorted(set(reasons))),
    )


def build_entry_learning_context(
    *, as_of_at: str, mechanism: str, direction: str, market_type: str,
    policy: str, policy_epoch: str | None, exclude_generation_id: str = "",
) -> EntryLearningContextV1:
    """Project existing planning/execution owners; never creates or updates evidence."""

    from aletheia.storage import repositories as repo

    cutoff = _instant(as_of_at)
    db = repo.get_db()
    rows = db.execute(
        "SELECT pg.*,um.symbol AS candidate_symbol,ep.id AS plan_id,"
        "ep.status AS plan_status,ep.execution_mode,ep.plan_json,"
        "ep.terminal_at AS plan_terminal_at "
        "FROM candidate_planning_generations pg "
        "JOIN universe_members um ON um.id=pg.candidate_id "
        "LEFT JOIN entry_plans ep ON ep.planning_generation_id=pg.id "
        "WHERE pg.identity_version='candidate-planning/v2' "
        "AND pg.opportunity_mechanism=? AND pg.direction=? AND pg.market_type=? "
        "AND pg.candidate_selection_policy=? "
        "AND COALESCE(pg.candidate_selection_policy_epoch,'')=COALESCE(?, '') "
        "AND pg.id<>? "
        "AND julianday(pg.created_at)<=julianday(?) ORDER BY pg.created_at,pg.id",
        (
            mechanism, direction, market_type, policy, policy_epoch,
            exclude_generation_id, cutoff.isoformat(),
        ),
    ).fetchall()
    if not rows:
        return empty_entry_learning_context(
            as_of_at=cutoff.isoformat(), mechanism=mechanism, direction=direction,
            market_type=market_type, policy=policy, policy_epoch=policy_epoch,
            reasons=("entry_learning_no_eligible_units",),
        )

    evidence_refs: list[str] = []
    utilities: list[float] = []
    symbols: set[str] = set()
    dates: set[str] = set()
    no_plan = no_trigger = no_fill = filled = complete = 0
    identity_values: set[tuple[str, ...]] = set()
    inference_identities: set[tuple[str, ...]] = set()
    profile_identities: set[tuple[str, str]] = set()
    inference_costs: list[float] = []
    invalid_reasons: set[str] = set()
    for raw in rows:
        row = dict(raw)
        try:
            created = _instant(row["created_at"])
        except (TypeError, ValueError):
            invalid_reasons.add("entry_learning_unit_time_invalid")
            continue
        symbols.add(str(row["candidate_symbol"]))
        dates.add(created.date().isoformat())
        evidence_refs.append(f"planning-generation:{row['id']}")
        planner_input = str(row.get("planner_input_json") or "")
        try:
            frozen = json.loads(planner_input)
            inference = frozen["inference_identity"]
            if (
                hashlib.sha256(planner_input.encode()).hexdigest()
                != row.get("planner_input_hash")
                or inference.get("caller") != "agent_shell.entry_planner"
                or inference.get("prompt_version") != "v2"
                or not inference.get("model_provider")
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            invalid_reasons.add("entry_learning_planner_identity_unavailable")
            inference = {}
        identity_values.add((
            str(row.get("producer_commit") or "unavailable"),
            str(row.get("behavior_fingerprint") or "unavailable"),
            str(row.get("planner_contract_version") or "unavailable"),
            str(row.get("mechanism_version") or "unavailable"),
            str(inference.get("model_provider") or "unavailable"),
            str(inference.get("prompt_version") or "unavailable"),
        ))
        call_identities, inference_cost, inference_refs, inference_reasons = (
            _planning_inference_evidence(
                db, generation_id=str(row["id"]), cutoff=cutoff,
            )
        )
        inference_identities.update(call_identities)
        evidence_refs.extend(inference_refs)
        invalid_reasons.update(inference_reasons)
        if inference_cost is not None:
            inference_costs.append(inference_cost)
        if row["status"] == "no_plan":
            try:
                completed_at = _instant(row["completed_at"])
            except (TypeError, ValueError):
                invalid_reasons.add("entry_learning_planning_outcome_incomplete")
                continue
            if completed_at > cutoff:
                invalid_reasons.add("entry_learning_planning_outcome_after_cutoff")
                continue
            no_plan += 1
            complete += 1
            utilities.append(0.0)
            continue
        plan_id = row.get("plan_id")
        if not plan_id:
            invalid_reasons.add("entry_learning_planning_outcome_incomplete")
            continue
        try:
            plan_payload = json.loads(str(row.get("plan_json") or ""))
            profile_identity = (
                str(plan_payload["entry_profile_id"]),
                str(plan_payload["entry_profile_version"]),
            )
            if not all(profile_identity):
                raise ValueError
            profile_identities.add(profile_identity)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            invalid_reasons.add("entry_learning_profile_identity_unavailable")
        evidence_refs.append(f"entry-plan:{plan_id}")
        orders = [dict(item) for item in db.execute(
            "SELECT * FROM orders WHERE entry_plan_id=? AND julianday(created_at)<=julianday(?) "
            "ORDER BY created_at,id", (plan_id, cutoff.isoformat()),
        ).fetchall()]
        if not orders:
            try:
                plan_terminal_at = _instant(row["plan_terminal_at"])
            except (TypeError, ValueError):
                plan_terminal_at = None
            if (
                row.get("plan_status") in {"expired", "invalidated", "cancelled", "failed"}
                and plan_terminal_at is not None
                and plan_terminal_at <= cutoff
            ):
                no_trigger += int(row.get("execution_mode") == "TRIGGERED")
                no_fill += 1
                complete += 1
                utilities.append(0.0)
            else:
                invalid_reasons.add("entry_learning_plan_open_at_cutoff")
            continue
        order = orders[-1]
        evidence_refs.append(f"order:{order['id']}")
        fills = [dict(item) for item in db.execute(
            "SELECT * FROM fills WHERE order_id=? AND julianday(filled_at)<=julianday(?)",
            (order["id"], cutoff.isoformat()),
        ).fetchall()]
        if not fills:
            try:
                plan_terminal_at = _instant(row["plan_terminal_at"])
            except (TypeError, ValueError):
                plan_terminal_at = None
            if (
                order.get("status") in {"cancelled", "rejected", "failed", "expired"}
                and plan_terminal_at is not None
                and plan_terminal_at <= cutoff
            ):
                no_fill += 1
                complete += 1
                utilities.append(0.0)
            else:
                invalid_reasons.add("entry_learning_order_open_at_cutoff")
            continue
        filled += 1
        evidence_refs.extend(f"fill:{item['id']}" for item in fills)
        position = db.execute(
            "SELECT * FROM positions WHERE order_id=? AND julianday(opened_at)<=julianday(?) "
            "ORDER BY opened_at DESC,id DESC LIMIT 1",
            (order["id"], cutoff.isoformat()),
        ).fetchone()
        if position is None:
            invalid_reasons.add("entry_learning_position_missing")
            continue
        position = dict(position)
        closed_at = position.get("closed_at")
        if position.get("status") != "closed" or not closed_at:
            invalid_reasons.add("entry_learning_position_open_at_cutoff")
            continue
        try:
            if _instant(closed_at) > cutoff:
                invalid_reasons.add("entry_learning_outcome_after_cutoff")
                continue
            notional = abs(float(position["quantity"]) * float(position["entry_price"]))
            net = float(position["realized_pnl"])
        except (TypeError, ValueError):
            invalid_reasons.add("entry_learning_position_outcome_invalid")
            continue
        if (
            not math.isfinite(notional) or notional <= 0 or not math.isfinite(net)
            or not bool(position.get("outcome_eligible"))
        ):
            invalid_reasons.add("entry_learning_position_outcome_incomplete")
            continue
        evidence_refs.append(f"position:{position['id']}")
        complete += 1
        utilities.append(net / notional * 100.0)

    total = len(rows)
    coverage = complete / total
    if len(identity_values) != 1:
        invalid_reasons.add("entry_learning_mixed_identity")
    if len(inference_identities) != 1:
        invalid_reasons.add("entry_learning_mixed_model_identity")
    if len(inference_costs) != total:
        invalid_reasons.add("entry_learning_model_data_cost_unavailable")
    if len(profile_identities) > 1:
        invalid_reasons.add("entry_learning_mixed_profile_identity")
    if complete != total:
        invalid_reasons.add("entry_learning_incomplete_outcomes")
    scope = {
        "mechanism": mechanism, "direction": direction, "market_type": market_type,
        "entry_profile_id": "mechanism-aware-grounded/v1", "horizon": "4h",
        "candidate_selection_policy": policy, "policy_epoch": policy_epoch,
    }
    support = {
        "independent_n": total, "distinct_symbols": len(symbols),
        "distinct_dates": len(dates), "complete_outcome_coverage": coverage,
    }
    rates = lambda value: value / total
    metrics = {
        "intent_to_treat_net_utility_pct": (
            sum(utilities) / total if complete == total else None
        ),
        "fill_rate": rates(filled), "no_plan_rate": rates(no_plan),
        "no_trigger_rate": rates(no_trigger), "no_fill_rate": rates(no_fill),
        "mae_p95_pct": None, "mfe_pct": None, "mfe_to_realized_capture": None,
        "model_and_data_cost": (
            sum(inference_costs) / total if len(inference_costs) == total else None
        ),
    }
    material = {
        "as_of_at": cutoff.isoformat(), "scope": scope, "support": support,
        "metrics": metrics, "evidence_refs": sorted(set(evidence_refs)),
        "identity": sorted(identity_values),
        "inference_identity": sorted(inference_identities),
        "profile_identity": sorted(profile_identities),
    }
    if invalid_reasons:
        return EntryLearningContextV1(
            as_of_at=cutoff.isoformat(), status="unavailable", scope=scope,
            support=support, metrics=metrics, evidence_refs=tuple(sorted(set(evidence_refs))),
            source_fingerprint=None, blocking_reasons=tuple(sorted(invalid_reasons)),
        )
    return EntryLearningContextV1(
        as_of_at=cutoff.isoformat(), status="observational", scope=scope,
        support=support, metrics=metrics, evidence_refs=tuple(sorted(set(evidence_refs))),
        source_fingerprint=_fingerprint(material), blocking_reasons=(),
    )


__all__ = [
    "ENTRY_LEARNING_ADAPTER_ID", "ENTRY_LEARNING_ADAPTER_VERSION",
    "ENTRY_LEARNING_CONTEXT_VERSION", "ENTRY_LEARNING_INTERFERENCE_KEY",
    "EntryLearningContextV1", "EntryLearningTreatmentAdapter",
    "build_entry_learning_context", "empty_entry_learning_context",
    "resolve_entry_learning_treatment",
]
