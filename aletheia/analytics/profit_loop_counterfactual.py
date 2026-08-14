"""Finite exploratory counterfactual registry over frozen Position facts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from typing import Any, Callable, Mapping

from ..storage.proposal_hypothesis import evaluate_hypothesis_prefix


COUNTERFACTUAL_CONTRACT_VERSION = "profit_loop_counterfactual/v1"
COUNTERFACTUAL_SCOPE = "first_60_minutes"
COUNTERFACTUAL_HORIZON = "60_minutes_after_each_hypothetical_entry"
_FIXED_HORIZON_BARS = 60
_DURATION_DAYS = 30
_ASSIGNMENT_UNIT = "position_id"
_ELIGIBILITY = (
    "opened_on_or_after_registration_closed_by_cutoff_complete_position_fact_with_frozen_hypothesis_and_1m_prefix"
)
_STOPPING_RULE = "single_locked_retrospective_evaluation_after_fixed_cutoff_no_optional_stopping"
_BASELINE_POLICY_ID = "initial_stop_fixed_60m/v1"
_FROZEN_INPUTS = "exact_instrument_1m_ohlcv_position_hypothesis_initial_stop"
_FILL_COST_CONTRACT = "next_bar_open_pessimistic_stop_fee_funding_slippage/v1"
_TREATMENTS = (
    ("delay_1_bar/v1", "delay_1_bar_fixed_60m/v1"),
    ("delay_3_bars/v1", "delay_3_bars_fixed_60m/v1"),
    ("delay_5_bars/v1", "delay_5_bars_fixed_60m/v1"),
    ("breakeven_after_1r/v1", "breakeven_after_1r_fixed_60m/v1"),
    ("thesis_invalidation_exit/v1", "thesis_invalidation_exit_fixed_60m/v1"),
)


@dataclass(frozen=True, slots=True)
class CounterfactualSpec:
    hypothesis_id: str
    baseline_policy_id: str
    treatment_policy_id: str
    registration_at: str
    cutoff_at: str
    registration_commit: str
    eligible_producer_commits: tuple[str, ...]
    behavior_fingerprint: str
    assignment_unit: str
    eligibility: str
    fixed_horizon_bars: int
    sample_cap: int
    frozen_inputs: str
    fill_cost_contract: str
    stopping_rule: str
    evidence_class: str


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _registry_payload() -> list[dict[str, Any]]:
    return [
        {
            "hypothesis_id": hypothesis_id,
            "baseline_policy_id": _BASELINE_POLICY_ID,
            "treatment_policy_id": treatment_policy_id,
            "fixed_horizon_bars": _FIXED_HORIZON_BARS,
            "sample_cap": 500,
            "frozen_inputs": _FROZEN_INPUTS,
            "fill_cost_contract": _FILL_COST_CONTRACT,
            "evidence_class": "exploratory",
        }
        for hypothesis_id, treatment_policy_id in _TREATMENTS
    ]


def profit_loop_registry_hash() -> str:
    canonical = json.dumps(_registry_payload(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _registry(registration: Mapping[str, Any]) -> tuple[CounterfactualSpec, ...]:
    return tuple(
        CounterfactualSpec(
            hypothesis_id=hypothesis_id,
            baseline_policy_id=_BASELINE_POLICY_ID,
            treatment_policy_id=treatment_policy_id,
            registration_at=str(registration["registration_at"]),
            cutoff_at=str(registration["cutoff_at"]),
            registration_commit=str(registration["deploy_commit"]),
            eligible_producer_commits=(str(registration["deploy_commit"]),),
            behavior_fingerprint=str(registration["behavior_fingerprint"]),
            assignment_unit=_ASSIGNMENT_UNIT,
            eligibility=_ELIGIBILITY,
            fixed_horizon_bars=_FIXED_HORIZON_BARS,
            sample_cap=500,
            frozen_inputs=_FROZEN_INPUTS,
            fill_cost_contract=_FILL_COST_CONTRACT,
            stopping_rule=_STOPPING_RULE,
            evidence_class="exploratory",
        )
        for hypothesis_id, treatment_policy_id in _TREATMENTS
    )


@dataclass(frozen=True, slots=True)
class CostModel:
    model_id: str
    entry_fee_bps: float
    exit_fee_bps: float
    slippage_bps_per_side: float
    source_evidence_hash: str
    verified_at: str
    funding_bps_per_hour: float

    def valid(self) -> bool:
        values = (
            self.entry_fee_bps,
            self.exit_fee_bps,
            self.slippage_bps_per_side,
            self.funding_bps_per_hour,
        )
        verified = _parse_utc(self.verified_at) is not None
        hash_valid = len(self.source_evidence_hash) == 64 and all(
            char in "0123456789abcdef" for char in self.source_evidence_hash.lower()
        )
        return (
            bool(self.model_id)
            and verified
            and hash_valid
            and all(math.isfinite(value) and value >= 0 for value in values)
        )


def _parse_utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _fixed_cutoff(started_at: datetime) -> datetime | None:
    try:
        return started_at + timedelta(days=_DURATION_DAYS)
    except OverflowError:
        return None


def _nested(root: Mapping[str, Any], *keys: str) -> Any:
    value: Any = root
    for key in keys:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def resolve_profit_loop_registration(fact: Mapping[str, Any]) -> dict[str, Any] | None:
    """Resolve one immutable profit-loop epoch from complete producer identity."""
    producer = fact.get("producer")
    if not isinstance(producer, Mapping) or producer.get("status") != "available":
        return {"status": "not_evaluable", "reason": "complete_producer_identity_required"}
    commits = [str(value) for value in producer.get("producer_commits") or [] if value]
    fingerprints = [str(value) for value in producer.get("behavior_fingerprints") or [] if value]
    if len(commits) != 1 or len(fingerprints) != 1:
        return {"status": "not_evaluable", "reason": "complete_producer_identity_required"}
    commit = commits[0]
    fingerprint = fingerprints[0]

    try:
        from aletheia.storage.sqlite import get_db

        rows = (
            get_db()
            .execute(
                "SELECT epoch_id, effective_values_json, started_at, ended_at "
                "FROM learning_policy_epochs WHERE epoch_kind=? "
                "ORDER BY started_at, rowid",
                ("evidence_anchor:profit_loop_observability",),
            )
            .fetchall()
        )
    except Exception:
        return {"status": "not_evaluable", "reason": "verified_deployment_registration_required"}

    identity_matches: list[tuple[Any, Mapping[str, Any]]] = []
    for row in rows:
        try:
            values = json.loads(row["effective_values_json"])
        except (TypeError, ValueError, json.JSONDecodeError):
            return {"status": "not_evaluable", "reason": "registered_epoch_malformed"}
        if not isinstance(values, Mapping):
            return {"status": "not_evaluable", "reason": "registered_epoch_malformed"}
        if values.get("deploy_commit") == commit and values.get("behavior_fingerprint") == fingerprint:
            identity_matches.append((row, values))
    if not identity_matches:
        return {"status": "not_evaluable", "reason": "verified_deployment_registration_required"}
    if len(identity_matches) != 1:
        return {"status": "not_evaluable", "reason": "registered_epoch_conflict"}

    expected_hash = profit_loop_registry_hash()
    matches = [item for item in identity_matches if item[1].get("registry_hash") == expected_hash]
    if not matches:
        return {"status": "not_evaluable", "reason": "registered_registry_hash_mismatch"}
    row, values = matches[0]
    registration_at = _parse_utc(row["started_at"])
    if registration_at is None:
        return {"status": "not_evaluable", "reason": "verified_deployment_registration_required"}
    cutoff_at = _fixed_cutoff(registration_at)
    if cutoff_at is None:
        return {"status": "not_evaluable", "reason": "registered_epoch_malformed"}
    raw_ended_at = row["ended_at"]
    ended_at = _parse_utc(raw_ended_at)
    if raw_ended_at is not None and ended_at is None:
        return {"status": "not_evaluable", "reason": "registered_epoch_end_malformed"}
    if ended_at is not None and ended_at < cutoff_at:
        return {"status": "not_evaluable", "reason": "registered_epoch_ended_before_cutoff"}
    return {
        "status": "available",
        "epoch_id": str(row["epoch_id"]),
        "registration_at": registration_at.isoformat(),
        "cutoff_at": cutoff_at.isoformat(),
        "deploy_commit": commit,
        "behavior_fingerprint": fingerprint,
        "registry_hash": expected_hash,
        "duration_days": _DURATION_DAYS,
        "assignment_unit": _ASSIGNMENT_UNIT,
        "eligibility": _ELIGIBILITY,
        "stopping_rule": _STOPPING_RULE,
        "ended_at": ended_at.isoformat() if ended_at else None,
    }


def profit_loop_registration_cutoffs() -> list[str]:
    """Return persisted profit-loop cutoffs for cutoff-anchored CLI replay."""
    try:
        from aletheia.storage.sqlite import get_db

        rows = (
            get_db()
            .execute(
                "SELECT started_at FROM learning_policy_epochs WHERE epoch_kind=? ORDER BY started_at DESC, rowid DESC",
                ("evidence_anchor:profit_loop_observability",),
            )
            .fetchall()
        )
    except Exception:
        return []
    return [
        cutoff.isoformat()
        for row in rows
        if (started := _parse_utc(row["started_at"])) is not None
        if (cutoff := _fixed_cutoff(started)) is not None
    ]


def _rows(fact: Mapping[str, Any]) -> list[dict[str, float]]:
    raw = _nested(fact, "stages", "holding", "ohlcv", "rows")
    if not isinstance(raw, list):
        return []
    rows = []
    for item in raw:
        if not isinstance(item, list) or len(item) < 6:
            return []
        try:
            ts, open_, high, low, close, volume = (
                int(item[0]),
                float(item[1]),
                float(item[2]),
                float(item[3]),
                float(item[4]),
                float(item[5]),
            )
        except (TypeError, ValueError, OverflowError):
            return []
        if not all(math.isfinite(value) for value in (open_, high, low, close, volume)):
            return []
        rows.append(
            {
                "ts": float(ts),
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
        )
    return rows


def _return_pct(direction: str, entry: float, exit_price: float) -> float:
    return (exit_price - entry) / entry * 100.0 if direction == "long" else (entry - exit_price) / entry * 100.0


def _adverse_slippage(price: float, direction: str, bps: float, *, entry: bool) -> float:
    adverse_up = (direction == "long") == entry
    multiplier = 1.0 + bps / 10_000.0 if adverse_up else 1.0 - bps / 10_000.0
    return price * multiplier


def _simulate(
    *,
    rows: list[dict[str, float]],
    direction: str,
    initial_stop: float,
    delay_bars: int,
    breakeven_after_1r: bool,
    exit_on_invalidation: bool,
    hypothesis: Mapping[str, Any],
    cost_model: CostModel,
    evaluator: Callable[..., str],
) -> dict[str, Any]:
    if len(rows) < delay_bars + _FIXED_HORIZON_BARS + 1:
        return {"status": "not_evaluable", "reason": "fixed_horizon_coverage_unavailable"}
    delay_prefix = rows[:delay_bars]
    if delay_prefix:
        delay_state = evaluator(hypothesis, completed_bars=delay_prefix)
        if delay_state == "not_evaluable":
            return {"status": "not_evaluable", "reason": "delay_invalidation_not_evaluable"}
        if delay_state == "invalidated":
            return {"status": "no_trade", "reason": "hypothesis_invalidated_before_delayed_entry"}
    stop_crossed = any(
        bar["low"] <= initial_stop if direction == "long" else bar["high"] >= initial_stop for bar in delay_prefix
    )
    if stop_crossed:
        return {"status": "no_trade", "reason": "frozen_stop_crossed_before_delayed_entry"}
    raw_entry = rows[delay_bars]["open"]
    entry = _adverse_slippage(
        raw_entry,
        direction,
        cost_model.slippage_bps_per_side,
        entry=True,
    )
    if (direction == "long" and entry <= initial_stop) or (direction == "short" and entry >= initial_stop):
        return {"status": "no_trade", "reason": "frozen_stop_crossed_before_delayed_entry"}
    initial_r = abs(entry - initial_stop)
    if initial_r <= 0:
        return {"status": "not_evaluable", "reason": "initial_risk_unavailable"}
    active_stop = initial_stop
    exit_price = rows[delay_bars + _FIXED_HORIZON_BARS]["open"]
    exit_reason = "fixed_horizon"
    prefix = list(delay_prefix)
    for index in range(delay_bars, delay_bars + _FIXED_HORIZON_BARS):
        bar = rows[index]
        prefix.append(bar)
        stop_hit = bar["low"] <= active_stop if direction == "long" else bar["high"] >= active_stop
        if stop_hit:
            gap_through = bar["open"] < active_stop if direction == "long" else bar["open"] > active_stop
            exit_price = bar["open"] if gap_through else active_stop
            exit_reason = "stop"
            break
        favorable = bar["high"] >= entry + initial_r if direction == "long" else bar["low"] <= entry - initial_r
        breakeven_touched = bar["low"] <= entry if direction == "long" else bar["high"] >= entry
        if breakeven_after_1r and favorable and breakeven_touched:
            exit_price = entry
            exit_reason = "breakeven_same_bar_ambiguous"
            break
        state = evaluator(hypothesis, completed_bars=prefix)
        if state == "not_evaluable" and exit_on_invalidation:
            return {"status": "not_evaluable", "reason": "hypothesis_state_not_evaluable"}
        if state == "invalidated" and exit_on_invalidation:
            exit_price = rows[index + 1]["open"]
            exit_reason = "hypothesis_invalidation"
            break
        if breakeven_after_1r and favorable:
            active_stop = entry
    exit_price = _adverse_slippage(
        exit_price,
        direction,
        cost_model.slippage_bps_per_side,
        entry=False,
    )
    gross_pct = _return_pct(direction, entry, exit_price)
    held_bars = max(1, len(prefix) - delay_bars)
    explicit_cost_pct = (
        cost_model.entry_fee_bps + cost_model.exit_fee_bps + cost_model.funding_bps_per_hour * held_bars / 60.0
    ) / 100.0
    return {
        "status": "evaluated",
        "reason": exit_reason,
        "entry_price": entry,
        "exit_price": exit_price,
        "held_bars": held_bars,
        "gross_return_pct": gross_pct,
        "explicit_cost_pct": explicit_cost_pct,
        "net_return_pct": gross_pct - explicit_cost_pct,
        "slippage_embedded_in_gross": True,
        "pessimistic_same_bar_ordering": True,
    }


def evaluate_counterfactual_registry(
    fact: Mapping[str, Any],
    *,
    cost_model: CostModel | None,
    as_of_at: str | datetime | None = None,
    hypothesis_evaluator: Callable[..., str] = evaluate_hypothesis_prefix,
) -> dict[str, Any]:
    """Evaluate fixed registry; every state call receives only its completed prefix."""
    registration = resolve_profit_loop_registration(fact)
    registered = registration if registration and registration.get("status") == "available" else None
    registry = _registry(registered) if registered else ()
    base = {
        "contract_version": COUNTERFACTUAL_CONTRACT_VERSION,
        "counterfactual_scope": COUNTERFACTUAL_SCOPE,
        "counterfactual_horizon": COUNTERFACTUAL_HORIZON,
        "fact_id": fact.get("fact_id"),
        "assignment_unit": _ASSIGNMENT_UNIT,
        "evidence_class": "exploratory",
        "promotion_eligible": False,
        "registry_hash": profit_loop_registry_hash(),
        "registration": registered,
        "registry": [asdict(spec) for spec in registry] if registry else _registry_payload(),
    }
    if registered is None:
        return {
            **base,
            "status": "not_evaluable",
            "reason": (registration.get("reason") if registration else "verified_deployment_registration_required"),
            "results": [],
        }
    cutoff_at = _parse_utc(registered["cutoff_at"])
    if cutoff_at is None or _now_utc() < cutoff_at:
        return {**base, "status": "not_evaluable", "reason": "registered_cutoff_not_reached", "results": []}
    evaluation_at = _parse_utc(fact.get("as_of_at"))
    requested_as_of = _parse_utc(as_of_at)
    if evaluation_at is None:
        return {**base, "status": "not_evaluable", "reason": "evaluation_as_of_unavailable", "results": []}
    if requested_as_of is not None and requested_as_of != evaluation_at:
        return {
            **base,
            "status": "not_evaluable",
            "reason": "evaluation_as_of_conflicts_with_fact",
            "results": [],
        }
    if evaluation_at != cutoff_at:
        return {
            **base,
            "status": "not_evaluable",
            "reason": "registered_evidence_cutoff_mismatch",
            "results": [],
        }
    opened_at = _parse_utc(_nested(fact, "stages", "exit", "position", "opened_at"))
    closed_at = _parse_utc(_nested(fact, "stages", "exit", "position", "closed_at"))
    registration_at = _parse_utc(registered["registration_at"])
    if (
        opened_at is None
        or closed_at is None
        or registration_at is None
        or opened_at < registration_at
        or closed_at > cutoff_at
    ):
        return {
            **base,
            "status": "not_evaluable",
            "reason": "registered_cohort_eligibility_failed",
            "results": [],
        }
    if (
        _nested(fact, "causal_link", "status") != "available"
        or _nested(fact, "economics", "availability") != "available"
        or _nested(fact, "stages", "holding", "availability") != "available"
        or _nested(fact, "stages", "holding", "ohlcv", "availability") != "available"
    ):
        return {
            **base,
            "status": "not_evaluable",
            "reason": "complete_position_fact_required",
            "results": [],
        }
    if cost_model is None or not cost_model.valid():
        return {**base, "status": "not_evaluable", "reason": "verified_cost_model_required", "results": []}
    rows = _rows(fact)
    hypothesis = _nested(
        fact,
        "stages",
        "decision",
        "proposal",
        "attribution_snapshot",
        "position_hypothesis",
    )
    direction = str(_nested(fact, "stages", "exit", "position", "direction") or "")
    initial_stop = _nested(hypothesis or {}, "initial_risk", "stop_price")
    try:
        stop = float(initial_stop)
    except (TypeError, ValueError, OverflowError):
        stop = math.nan
    if not rows or not isinstance(hypothesis, Mapping) or direction not in {"long", "short"} or not math.isfinite(stop):
        return {**base, "status": "not_evaluable", "reason": "frozen_inputs_unavailable", "results": []}

    baseline = _simulate(
        rows=rows,
        direction=direction,
        initial_stop=stop,
        delay_bars=0,
        breakeven_after_1r=False,
        exit_on_invalidation=False,
        hypothesis=hypothesis,
        cost_model=cost_model,
        evaluator=hypothesis_evaluator,
    )
    results = []
    for spec in registry:
        delay = 0
        if spec.hypothesis_id.startswith("delay_"):
            delay = int(spec.hypothesis_id.split("_")[1])
        treatment = _simulate(
            rows=rows,
            direction=direction,
            initial_stop=stop,
            delay_bars=delay,
            breakeven_after_1r=spec.hypothesis_id.startswith("breakeven_"),
            exit_on_invalidation=spec.hypothesis_id.startswith("thesis_invalidation_"),
            hypothesis=hypothesis,
            cost_model=cost_model,
            evaluator=hypothesis_evaluator,
        )
        delta = None
        if baseline.get("status") == "evaluated" and treatment.get("status") == "evaluated":
            delta = treatment["net_return_pct"] - baseline["net_return_pct"]
        results.append(
            {
                "hypothesis_id": spec.hypothesis_id,
                "baseline": baseline,
                "treatment": treatment,
                "paired_net_delta_pct": delta,
                "promotion_eligible": False,
            }
        )
    return {
        **base,
        "status": "evaluated" if baseline.get("status") == "evaluated" else "not_evaluable",
        "reason": baseline.get("reason"),
        "cost_model": asdict(cost_model),
        "results": results,
    }
