"""Finite exploratory counterfactual registry over frozen Position facts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import math
from typing import Any, Callable, Mapping

from ..storage.proposal_hypothesis import evaluate_hypothesis_prefix


COUNTERFACTUAL_CONTRACT_VERSION = "profit_loop_counterfactual/v1"
_FIXED_HORIZON_BARS = 60


@dataclass(frozen=True, slots=True)
class CounterfactualSpec:
    hypothesis_id: str
    baseline_policy_id: str
    treatment_policy_id: str
    registration_at: str
    cutoff_at: str
    registration_commit: str
    assignment_unit: str
    eligibility: str
    fixed_horizon_bars: int
    sample_cap: int
    frozen_inputs: str
    fill_cost_contract: str
    stopping_rule: str
    evidence_class: str


_REGISTRATION = {
    "registration_at": "2026-08-13T06:12:47+00:00",
    "cutoff_at": "2026-09-12T06:12:47+00:00",
    "registration_commit": "683c8e27952bee7816931733bed0f64bfadae856",
    "assignment_unit": "position_id",
    "eligibility": "opened_on_or_after_registration_closed_by_cutoff_complete_position_fact_with_frozen_hypothesis_and_1m_prefix",
    "fixed_horizon_bars": _FIXED_HORIZON_BARS,
    "sample_cap": 500,
    "frozen_inputs": "exact_instrument_1m_ohlcv_position_hypothesis_initial_stop",
    "fill_cost_contract": "next_bar_open_pessimistic_stop_fee_funding_slippage/v1",
    "stopping_rule": "single_locked_retrospective_evaluation_after_fixed_cutoff_no_optional_stopping",
    "evidence_class": "exploratory",
}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _spec(hypothesis_id: str, treatment: str) -> CounterfactualSpec:
    return CounterfactualSpec(
        hypothesis_id=hypothesis_id,
        baseline_policy_id="initial_stop_fixed_60m/v1",
        treatment_policy_id=treatment,
        **_REGISTRATION,
    )


REGISTRY = (
    _spec("delay_1_bar/v1", "delay_1_bar_fixed_60m/v1"),
    _spec("delay_3_bars/v1", "delay_3_bars_fixed_60m/v1"),
    _spec("delay_5_bars/v1", "delay_5_bars_fixed_60m/v1"),
    _spec("breakeven_after_1r/v1", "breakeven_after_1r_fixed_60m/v1"),
    _spec("thesis_invalidation_exit/v1", "thesis_invalidation_exit_fixed_60m/v1"),
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


def _nested(root: Mapping[str, Any], *keys: str) -> Any:
    value: Any = root
    for key in keys:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


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
    base = {
        "contract_version": COUNTERFACTUAL_CONTRACT_VERSION,
        "fact_id": fact.get("fact_id"),
        "assignment_unit": "position_id",
        "evidence_class": "exploratory",
        "promotion_eligible": False,
        "registry": [asdict(spec) for spec in REGISTRY],
    }
    if not all(_REGISTRATION.get(field) for field in ("registration_at", "cutoff_at", "registration_commit")):
        return {
            **base,
            "status": "not_evaluable",
            "reason": "verified_deployment_registration_required",
            "results": [],
        }
    evaluation_at = _parse_utc(as_of_at) or _parse_utc(fact.get("as_of_at"))
    cutoff_at = _parse_utc(_REGISTRATION["cutoff_at"])
    if evaluation_at is None:
        return {**base, "status": "not_evaluable", "reason": "evaluation_as_of_unavailable", "results": []}
    if cutoff_at is None or _now_utc() < cutoff_at:
        return {**base, "status": "not_evaluable", "reason": "registered_cutoff_not_reached", "results": []}
    if evaluation_at != cutoff_at:
        return {
            **base,
            "status": "not_evaluable",
            "reason": "registered_evidence_cutoff_mismatch",
            "results": [],
        }
    registered_commit = str(_REGISTRATION["registration_commit"])
    producer_commits = {str(commit) for commit in (_nested(fact, "producer", "producer_commits") or [])}
    if producer_commits != {registered_commit}:
        return {
            **base,
            "status": "not_evaluable",
            "reason": "registered_producer_identity_mismatch",
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
    opened_at = _parse_utc(_nested(fact, "stages", "exit", "position", "opened_at"))
    closed_at = _parse_utc(_nested(fact, "stages", "exit", "position", "closed_at"))
    registration_at = _parse_utc(_REGISTRATION["registration_at"])
    cutoff_at = _parse_utc(_REGISTRATION["cutoff_at"])
    if (
        opened_at is None
        or closed_at is None
        or registration_at is None
        or cutoff_at is None
        or opened_at < registration_at
        or closed_at > cutoff_at
    ):
        return {
            **base,
            "status": "not_evaluable",
            "reason": "registered_cohort_eligibility_failed",
            "results": [],
        }
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
    for spec in REGISTRY:
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
