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


def _percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _bounded_evidence(refs: list[str]) -> tuple[tuple[str, ...], int, bool]:
    complete = sorted(set(refs))
    return tuple(complete[:64]), len(complete), len(complete) > 64


def _filled_unit_outcome(
    db, *, row: dict[str, Any], position: dict[str, Any], cutoff: datetime,
) -> tuple[float, float, float, tuple[str, ...]] | tuple[None, None, None, str]:
    """Return the notional-weighted fixed-4h outcome of exact entry Fills."""

    if not bool(position.get("outcome_eligible")):
        return None, None, None, "entry_learning_position_outcome_incomplete"
    fills = [dict(item) for item in db.execute(
        "SELECT * FROM fills WHERE order_id=? AND julianday(filled_at)<=julianday(?) "
        "ORDER BY filled_at,id",
        (position["order_id"], cutoff.isoformat()),
    ).fetchall()]
    if not fills:
        return None, None, None, "entry_learning_fixed_horizon_outcome_unavailable"
    from aletheia.analytics.outcome_semantics import validated_outcome_economics

    weighted: list[tuple[float, float, float, float, str]] = []
    for fill in fills:
        outcomes = [dict(item) for item in db.execute(
            "SELECT * FROM filter_candidate_outcomes WHERE outcome_candidate_key=? "
            "AND horizon='4h' AND venue=? AND symbol=? AND market_type=? "
            "AND LOWER(direction)=LOWER(?) ORDER BY id",
            (
                f"entry-fill:{fill['id']}", row["venue"], row["symbol"],
                row["market_type"], row["direction"],
            ),
        ).fetchall()]
        if len(outcomes) != 1:
            return None, None, None, "entry_learning_fixed_horizon_outcome_unavailable"
        outcome = outcomes[0]
        try:
            due_at = _instant(outcome["due_at"])
            settled_at = _instant(outcome["settled_at"])
            filled_at = _instant(fill["filled_at"])
            entry_at = _instant(outcome["entry_ts"])
            notional = abs(float(fill["fill_price"]) * float(fill["fill_quantity"]))
        except (TypeError, ValueError):
            return None, None, None, "entry_learning_fixed_horizon_outcome_invalid"
        if outcome.get("status") != "settled" or due_at > cutoff or settled_at > cutoff:
            return None, None, None, "entry_learning_fixed_horizon_outcome_unavailable"
        economics = validated_outcome_economics(outcome)
        if (
            economics is None or entry_at != filled_at
            or not math.isfinite(notional) or notional <= 0
        ):
            return None, None, None, "entry_learning_fixed_horizon_outcome_invalid"
        try:
            mfe = float(outcome["mfe_pct"])
            mae = float(outcome["mae_pct"])
        except (TypeError, ValueError):
            return None, None, None, "entry_learning_fixed_horizon_path_invalid"
        utility = economics.get("net_return_pct")
        if (
            utility is None or not math.isfinite(mfe) or not math.isfinite(mae)
            or mfe < 0 or mae > 0
        ):
            return None, None, None, "entry_learning_fixed_horizon_path_invalid"
        weighted.append((notional, utility, mfe, mae, str(outcome["id"])))
    total_notional = sum(item[0] for item in weighted)
    return (
        sum(item[0] * item[1] for item in weighted) / total_notional,
        sum(item[0] * item[2] for item in weighted) / total_notional,
        sum(item[0] * item[3] for item in weighted) / total_notional,
        tuple(item[4] for item in weighted),
    )


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
        outcome_candidate_key=planning_generation_id,
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
            "cycle_n": 0, "planning_thread_n": 0, "max_cycles_per_thread": 0,
            "complete_outcome_coverage": 0.0,
            "evidence_ref_total": 0, "evidence_refs_truncated": False,
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
    current_behavior_identity: dict[str, str] | None = None,
) -> EntryLearningContextV1:
    """Project existing planning/execution owners; never creates or updates evidence."""

    from aletheia.storage import repositories as repo

    cutoff = _instant(as_of_at)
    db = repo.get_db()
    rows = db.execute(
        "SELECT pg.*,um.symbol AS candidate_symbol,ep.id AS plan_id,"
        "ep.candidate_id AS plan_candidate_id,"
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
    from aletheia.trading.entry_compat import classify_legacy_planning_outcome

    eligible_rows = []
    for raw in rows:
        row = dict(raw)
        outcome = (
            str(row.get("outcome_class"))
            if row.get("outcome_class")
            else classify_legacy_planning_outcome(row).outcome_class
        )
        if outcome in {"planning_blocked", "planning_failed", "planning_unavailable"}:
            continue
        eligible_rows.append(row)
    rows = eligible_rows
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
    maes: list[float] = []
    mfes: list[float] = []
    captures: list[float] = []
    planning_thread_counts: dict[str, int] = {}
    invalid_reasons: set[str] = set()
    current_identity = current_behavior_identity or {}
    required_current_identity = (
        "producer_commit", "behavior_fingerprint", "entry_behavior_fingerprint",
        "schema_fingerprint", "mechanism_version", "model_provider", "model",
        "prompt_fingerprint",
        "entry_profile_registry_hash",
    )
    if not all(current_identity.get(key) for key in required_current_identity):
        invalid_reasons.add("entry_learning_current_behavior_identity_unavailable")
    for raw in rows:
        row = dict(raw)
        try:
            created = _instant(row["created_at"])
        except (TypeError, ValueError):
            invalid_reasons.add("entry_learning_unit_time_invalid")
            continue
        symbols.add(str(row["candidate_symbol"]))
        dates.add(created.date().isoformat())
        planning_key = str(row.get("planning_key") or row["id"])
        planning_thread_counts[planning_key] = planning_thread_counts.get(planning_key, 0) + 1
        evidence_refs.append(f"planning-generation:{row['id']}")
        planner_input = str(row.get("planner_input_json") or "")
        frozen: dict[str, Any] = {}
        try:
            frozen = json.loads(planner_input)
            inference = frozen["inference_identity"]
            if (
                hashlib.sha256(planner_input.encode()).hexdigest()
                != row.get("planner_input_hash")
                or inference.get("caller") != "agent_shell.entry_planner"
                or len(str(inference.get("prompt_fingerprint") or "")) != 64
                or not inference.get("model_provider")
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            invalid_reasons.add("entry_learning_planner_identity_unavailable")
            inference = {}
        identity_values.add((
            str(row.get("producer_commit") or "unavailable"),
            str(row.get("behavior_fingerprint") or "unavailable"),
            str(row.get("entry_behavior_fingerprint") or "unavailable"),
            str(row.get("schema_fingerprint") or "unavailable"),
            str(row.get("mechanism_version") or "unavailable"),
            str(inference.get("model_provider") or "unavailable"),
            str(inference.get("prompt_fingerprint") or "unavailable"),
            _fingerprint(frozen.get("entry_profiles") or []),
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
                proposal = db.execute(
                    "SELECT id FROM trade_proposals WHERE entry_plan_id=? "
                    "AND julianday(created_at)<=julianday(?) LIMIT 1",
                    (plan_id, cutoff.isoformat()),
                ).fetchone()
                if proposal is not None:
                    evidence_refs.append(f"trade-proposal:{proposal['id']}")
                no_trigger += int(
                    row.get("execution_mode") == "TRIGGERED" and proposal is None
                )
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
        utility, mfe, mae, outcome_ids = _filled_unit_outcome(
            db, row=row, position=position, cutoff=cutoff,
        )
        if utility is None:
            invalid_reasons.add(str(outcome_ids))
            continue
        evidence_refs.append(f"position:{position['id']}")
        evidence_refs.extend(
            f"filter-candidate-outcome:{outcome_id}" for outcome_id in outcome_ids
        )
        complete += 1
        utilities.append(utility)
        if mfe is not None and mae is not None:
            mfes.append(mfe)
            maes.append(-mae)
            if mfe > 0:
                captures.append(utility / mfe)

    total = len(rows)
    coverage = complete / total
    expected_identity = {
        (
            str(current_identity.get("producer_commit") or "unavailable"),
            str(current_identity.get("behavior_fingerprint") or "unavailable"),
            str(current_identity.get("entry_behavior_fingerprint") or "unavailable"),
            str(current_identity.get("schema_fingerprint") or "unavailable"),
            str(current_identity.get("mechanism_version") or "unavailable"),
            str(current_identity.get("model_provider") or "unavailable"),
            str(current_identity.get("prompt_fingerprint") or "unavailable"),
            str(current_identity.get("entry_profile_registry_hash") or "unavailable"),
        )
    }
    if identity_values != expected_identity:
        invalid_reasons.add("entry_learning_mixed_identity")
    if (
        len(inference_identities) != 1
        or any(
            identity[:3] != (
                str(current_identity.get("model_provider") or "unavailable"),
                str(current_identity.get("model") or "unavailable"),
                str(current_identity.get("prompt_fingerprint") or "unavailable"),
            )
            for identity in inference_identities
        )
    ):
        invalid_reasons.add("entry_learning_mixed_model_identity")
    if len(inference_costs) != total:
        invalid_reasons.add("entry_learning_model_data_cost_unavailable")
    if complete != total:
        invalid_reasons.add("entry_learning_incomplete_outcomes")
    scope = {
        "mechanism": mechanism, "direction": direction, "market_type": market_type,
        "entry_profile_id": "mechanism-aware-grounded/v1", "horizon": "4h",
        "candidate_selection_policy": policy, "policy_epoch": policy_epoch,
    }
    support = {
        "independent_n": len(planning_thread_counts), "distinct_symbols": len(symbols),
        "distinct_dates": len(dates), "complete_outcome_coverage": coverage,
        "cycle_n": total, "planning_thread_n": len(planning_thread_counts),
        "max_cycles_per_thread": max(planning_thread_counts.values(), default=0),
        "entry_profile_scope": "parent_summary",
        "entry_profile_identities": [list(item) for item in sorted(profile_identities)],
        "current_behavior_identity": dict(sorted(current_identity.items())),
    }
    bounded_refs, evidence_ref_total, evidence_refs_truncated = _bounded_evidence(
        evidence_refs
    )
    support.update(
        evidence_ref_total=evidence_ref_total,
        evidence_refs_truncated=evidence_refs_truncated,
    )
    rates = lambda value: value / total
    metrics = {
        "intent_to_treat_net_utility_pct": (
            sum(utilities) / total if complete == total else None
        ),
        "fill_rate": rates(filled), "no_plan_rate": rates(no_plan),
        "no_trigger_rate": rates(no_trigger), "no_fill_rate": rates(no_fill),
        "mae_p95_pct": _percentile(maes, 0.95) if len(maes) == filled else None,
        "mfe_pct": sum(mfes) / filled if len(mfes) == filled and filled else None,
        "mfe_to_realized_capture": (
            sum(captures) / filled if len(captures) == filled and filled else None
        ),
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
            support=support, metrics=metrics, evidence_refs=bounded_refs,
            source_fingerprint=None, blocking_reasons=tuple(sorted(invalid_reasons)),
        )
    return EntryLearningContextV1(
        as_of_at=cutoff.isoformat(), status="observational", scope=scope,
        support=support, metrics=metrics, evidence_refs=bounded_refs,
        source_fingerprint=_fingerprint(material), blocking_reasons=(),
    )


__all__ = [
    "ENTRY_LEARNING_ADAPTER_ID", "ENTRY_LEARNING_ADAPTER_VERSION",
    "ENTRY_LEARNING_CONTEXT_VERSION", "ENTRY_LEARNING_INTERFERENCE_KEY",
    "EntryLearningContextV1", "EntryLearningTreatmentAdapter",
    "build_entry_learning_context", "empty_entry_learning_context",
    "resolve_entry_learning_treatment",
]
