"""Persisted non-authoritative Candidate Selection Utility comparison."""

from __future__ import annotations

import json
from typing import Mapping, Sequence

from aletheia.analytics.temporal import parse_utc_instant
from aletheia.config.schema import UniverseConfig
from aletheia.storage.models import UniverseTwinShadowBatch, UniverseTwinShadowEvidence
from aletheia.universe.candidate_eligibility import canonical_fingerprint, evaluate_hard_gate
from aletheia.universe.candidate_selection_utility import (
    CANDIDATE_SELECTION_UTILITY_VERSION,
    build_candidate_selection_utility,
    select_admitted_candidates,
)
from aletheia.universe.candidate_selection_authority import candidate_selection_policy_identity
from aletheia.universe.economic_utility import estimate_economic_utility
from aletheia.universe.opportunity_evidence import (
    annotate_within_run_percentiles,
    build_opportunity_evidence,
)
from aletheia.universe.scanners import UNIVERSE_SCORER_BEHAVIOR_VERSION
from aletheia.universe.twin_persistence import TwinShadowBundle


CANDIDATE_SELECTION_RECORD_VERSION = "candidate-selection-utility-record/v1"
CANDIDATE_SELECTION_METRIC_VERSION = "candidate-selection-utility-net-return-4h/v1"
CANDIDATE_SELECTION_OUTCOME_VERSION = "candidate-selection-utility-outcome/v1"
_MAX_OUTPUT_BYTES = 32 * 1024


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _identity(feature: Mapping[str, object]) -> tuple[str, str, str, str]:
    return (
        str(feature.get("feature_snapshot_id") or ""),
        str(feature.get("venue") or ""),
        str(feature.get("market_type") or "spot"),
        str(feature.get("symbol") or ""),
    )


def _candidate_key(scanner_run_id: str, feature: Mapping[str, object]) -> str:
    feature_id, venue, market_type, symbol = _identity(feature)
    return f"candidate-selection-utility:v1:{scanner_run_id}:{feature_id}:{venue}:{market_type}:{symbol}"


def _outcome_key(candidate_key: str, direction: str) -> str:
    return f"{candidate_key}:{direction}"


def _safe_feature(feature: Mapping[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in feature.items()
        if not str(key).startswith("_") and isinstance(value, (str, int, float, bool, type(None)))
    }


def _training_outcome_fingerprint(
    outcomes: Sequence[Mapping[str, object]],
    *,
    cutoff_at: str,
) -> str:
    cutoff = parse_utc_instant(cutoff_at)
    if cutoff is None:
        raise ValueError("candidate_selection_training_cutoff_invalid")
    fields = (
        "outcome_candidate_key", "scanner_run_id", "mechanism_id", "direction",
        "venue", "market_type", "strength_quintile", "horizon", "status", "entry_ts",
        "settled_at", "symbol", "net_return_pct", "mae_pct", "cost_model_version",
        "min_tradable_edge_pct",
    )
    material = []
    for row in outcomes:
        entry = parse_utc_instant(row.get("entry_ts"))
        settled = parse_utc_instant(row.get("settled_at"))
        if entry is None or entry > cutoff or (settled is not None and settled > cutoff):
            continue
        material.append({field: row.get(field) for field in fields})
    material.sort(key=lambda row: (
        str(row["outcome_candidate_key"]),
        str(row["mechanism_id"]),
        str(row["strength_quintile"]),
    ))
    return canonical_fingerprint(material)


def build_candidate_selection_shadow_bundle(
    population: Sequence[Mapping[str, object]],
    *,
    scanner_run_id: str,
    captured_at: str,
    universe_config: UniverseConfig,
    legacy_scored: Sequence[Mapping[str, object]],
    legacy_allocated: Sequence[Mapping[str, object]],
    action_route_reasons: Mapping[tuple[str, str, str, str], str | None],
    historical_outcomes: Sequence[Mapping[str, object]],
    estimator_training_cutoff: str | None = None,
    selection_policy: Mapping[str, object] | None = None,
    authority_applied: bool = False,
    qualification_evidence: Mapping[str, object] | None = None,
) -> TwinShadowBundle:
    """Build one same-T0 full-denominator comparison and optional authority record."""

    scored = {_identity(item): item for item in legacy_scored}
    selected = {_identity(item) for item in legacy_allocated}
    gates = []
    bundles = []
    for feature in population:
        key = _candidate_key(scanner_run_id, feature)
        gates.append(evaluate_hard_gate(
            feature,
            universe_config,
            candidate_key=key,
            evaluated_at=captured_at,
            action_route_reason=action_route_reasons.get(_identity(feature)),
        ))
        bundles.append(build_opportunity_evidence(feature, candidate_key=key, source_cutoff_at=captured_at))
    eligible_indices = [index for index, gate in enumerate(gates) if gate.status == "eligible"]
    ranked = annotate_within_run_percentiles([bundles[index] for index in eligible_indices])
    for index, bundle in zip(eligible_indices, ranked, strict=True):
        bundles[index] = bundle

    from aletheia.analytics.filter_evidence import build_candidate_outcomes

    decisions = []
    outcomes_by_key = {}
    for feature, gate, evidence in zip(population, gates, bundles, strict=True):
        identity = _identity(feature)
        legacy = scored.get(identity, {})
        estimates = []
        for mechanism in evidence.mechanisms:
            if (
                mechanism.applicability == "applicable"
                and mechanism.direction in {"long", "short"}
                and mechanism.within_run_percentile is not None
            ):
                estimates.append(estimate_economic_utility(
                    candidate_key=gate.candidate_key,
                    mechanism_id=mechanism.mechanism_id,
                    direction=mechanism.direction,
                    venue=str(evidence.instrument["venue"]),
                    market_type=str(evidence.instrument["market_type"]),
                    strength_percentile=mechanism.within_run_percentile,
                    outcomes=historical_outcomes,
                    source_cutoff_at=captured_at,
                ))
        decision = build_candidate_selection_utility(
            hard_gate=gate,
            opportunity_evidence=evidence,
            economic_estimates=estimates,
            legacy_control={
                "primary_family": legacy.get("scanner_family"),
                "primary_direction": legacy.get("scanner_direction") or legacy.get("direction"),
                "total_score": legacy.get("total_score"),
                "authority_selected": identity in selected,
            },
        )
        decisions.append(decision)
    target_selected_keys = {
        item.candidate_key
        for item in select_admitted_candidates(
            decisions,
            max_candidates=universe_config.max_candidates,
        )
    }
    for feature, gate, evidence, decision in zip(population, gates, bundles, decisions, strict=True):
        legacy_direction = decision.legacy_control.get("primary_direction")
        legacy_selected = decision.legacy_control.get("authority_selected") is True
        if gate.status != "eligible" and not (legacy_selected and legacy_direction in {"long", "short"}):
            continue
        directions = {
            item.direction for item in evidence.mechanisms
            if gate.status == "eligible"
            and item.applicability == "applicable" and item.direction in {"long", "short"}
        }
        if legacy_selected and legacy_direction in {"long", "short"}:
            directions.add(str(legacy_direction))
        for direction in directions:
            price = feature.get("last_price") or feature.get("price")
            venue = str(feature.get("venue") or "")
            symbol = str(feature.get("symbol") or "")
            if not venue or not symbol or not isinstance(price, (int, float)) or float(price) <= 0:
                continue
            key = _outcome_key(gate.candidate_key, direction)
            built = build_candidate_outcomes(
                outcome_candidate_key=key,
                venue=venue,
                symbol=symbol,
                market_type=str(feature.get("market_type") or "spot"),
                direction=direction,
                scanner_family="candidate_selection_utility",
                regime_tag=str(feature.get("regime_tag")) if feature.get("regime_tag") else None,
                entry_price_usd=float(price),
                entry_spread_bps=feature.get("spread_bps"),
                funding_rate_at_entry=feature.get("funding_rate"),
                entry_ts=captured_at,
                allocation_selected=(
                    decision.candidate_key in target_selected_keys
                    and decision.primary_direction == direction
                ),
                horizons=("4h",),
            )
            if len(built) == 1:
                outcomes_by_key[key] = built[0]

    available_source_fingerprints = sorted({
        estimate.source_outcome_fingerprint
        for decision in decisions
        for estimate in decision.economic_estimates
        if estimate.estimate_status == "available" and estimate.source_outcome_fingerprint
    })
    effective_training_cutoff = (
        estimator_training_cutoff
        if available_source_fingerprints and estimator_training_cutoff
        else captured_at if available_source_fingerprints else None
    )
    training_outcome_fingerprint = (
        _training_outcome_fingerprint(
            historical_outcomes,
            cutoff_at=effective_training_cutoff,
        )
        if effective_training_cutoff is not None else None
    )
    frozen_selection_policy = dict(
        selection_policy or candidate_selection_policy_identity(universe_config)
    )
    behavior_contract_hash = canonical_fingerprint({
        "version": CANDIDATE_SELECTION_UTILITY_VERSION,
        "record_version": CANDIDATE_SELECTION_RECORD_VERSION,
        "outcome_version": CANDIDATE_SELECTION_OUTCOME_VERSION,
        "training_cutoff": effective_training_cutoff,
        "training_outcome_fingerprint": training_outcome_fingerprint,
        "selection_policy": frozen_selection_policy,
    })
    policy_epoch_id = (
        f"candidate-selection-utility:{behavior_contract_hash[:24]}"
        if available_source_fingerprints else None
    )
    experiment_id = (
        f"candidate-selection-utility-shadow:{behavior_contract_hash[:24]}"
        if available_source_fingerprints else None
    )
    qualified = bool(
        authority_applied
        and qualification_evidence
        and qualification_evidence.get("verdict") == "positive"
        and qualification_evidence.get("promotion_eligible") is True
        and qualification_evidence.get("policy_epoch_id") == policy_epoch_id
        and qualification_evidence.get("selection_policy")
        == frozen_selection_policy
        and not qualification_evidence.get("blocking_reasons")
        and not qualification_evidence.get("invalid_reasons")
    )
    if authority_applied and not qualified:
        raise ValueError("candidate_selection_authority_qualification_invalid")
    blocking_reasons = [] if authority_applied else [
        "prospective_qualification_not_complete", "owner_admission_required",
    ]

    evidence_rows = []
    for index, (feature, decision) in enumerate(zip(population, decisions, strict=True)):
        outcome_keys = {
            direction: key
            for direction in ("long", "short")
            if (key := _outcome_key(decision.candidate_key, direction)) in outcomes_by_key
        }
        material = {
            "record_version": CANDIDATE_SELECTION_RECORD_VERSION,
            "candidate_selection": decision.as_dict(),
            "target_selected": decision.candidate_key in target_selected_keys,
            "outcome_candidate_keys": outcome_keys,
            "evidence_contract": {
                "shadow_authority_applied": authority_applied,
                "promotion_eligible": qualified,
                "blocking_reasons": blocking_reasons,
                "outcome_contract_version": CANDIDATE_SELECTION_OUTCOME_VERSION,
                "behavior_contract_hash": behavior_contract_hash,
                "policy_epoch_id": policy_epoch_id,
                "estimator_training_cutoff": effective_training_cutoff,
                "training_outcome_fingerprint": training_outcome_fingerprint,
                "source_outcome_fingerprints": available_source_fingerprints,
            },
        }
        compact = _json(material)
        if len(compact.encode("utf-8")) > _MAX_OUTPUT_BYTES:
            raise ValueError("candidate_selection_shadow_output_too_large")
        feature_id, _venue, market_type, symbol = _identity(feature)
        input_json = _json(_safe_feature(feature))
        evidence_rows.append(UniverseTwinShadowEvidence(
            id=canonical_fingerprint({"record_version": CANDIDATE_SELECTION_RECORD_VERSION, "candidate_key": decision.candidate_key})[:24],
            scanner_run_id=scanner_run_id,
            candidate_key=decision.candidate_key,
            candidate_index=index,
            feature_snapshot_id=feature_id or None,
            asset_id=str(feature.get("asset_id")) if feature.get("asset_id") else None,
            symbol=symbol,
            market_type=market_type,
            experiment_id=experiment_id,
            policy_epoch_id=policy_epoch_id,
            behavior_contract_hash=behavior_contract_hash,
            primary_horizon="4h",
            outcome_contract_version=CANDIDATE_SELECTION_OUTCOME_VERSION,
            metric_version=CANDIDATE_SELECTION_METRIC_VERSION,
            outcome_candidate_key=outcome_keys.get(decision.primary_direction or ""),
            authority_total_score=float(decision.legacy_control.get("total_score") or 0.0),
            fine_filter_passed=bool(scored.get(_identity(feature))),
            authority_selected=bool(decision.legacy_control.get("authority_selected")),
            assigned_policy_selected=(decision.candidate_key in target_selected_keys) if authority_applied else None,
            shadow_authority_applied=authority_applied,
            promotion_eligible=qualified,
            shadow_valid=decision.hard_gate.status == "eligible",
            shadow_pessimistic_selected=decision.candidate_key in target_selected_keys,
            compact_output_json=compact,
            input_json=input_json,
            record_hash=canonical_fingerprint({"output": material, "input": json.loads(input_json)}),
            captured_at=captured_at,
        ))
    batch_material = {
        "scanner_run_id": scanner_run_id,
        "record_version": CANDIDATE_SELECTION_RECORD_VERSION,
        "candidate_keys": [item.candidate_key for item in evidence_rows],
        "record_hashes": [item.record_hash for item in evidence_rows],
        "outcome_keys": sorted(outcomes_by_key),
    }
    batch = UniverseTwinShadowBatch(
        scanner_run_id=scanner_run_id,
        record_version=CANDIDATE_SELECTION_RECORD_VERSION,
        authority_behavior_version=UNIVERSE_SCORER_BEHAVIOR_VERSION,
        shadow_behavior_version=CANDIDATE_SELECTION_UTILITY_VERSION,
        allocation_replay_contract="candidate-selection-utility-allocation/v1",
        experiment_id=experiment_id,
        policy_epoch_id=policy_epoch_id,
        behavior_contract_hash=behavior_contract_hash,
        primary_horizon="4h",
        outcome_contract_version=CANDIDATE_SELECTION_OUTCOME_VERSION,
        feature_source_fingerprint=canonical_fingerprint({
            "source": "universe_feature_snapshot/v1", "hard_gate": "universe-hard-gate/v1"
        }),
        metric_version=CANDIDATE_SELECTION_METRIC_VERSION,
        allocator_random_state_json="null",
        policy_json=_json({
            "universe": universe_config.model_dump(mode="json"),
            "selection_policy": frozen_selection_policy,
            "estimator_training_cutoff": effective_training_cutoff,
            "training_outcome_fingerprint": training_outcome_fingerprint,
            "source_outcome_fingerprints": available_source_fingerprints,
        }),
        allocation_context_json=_json({
            "same_t0_population": True,
            "legacy_rollback": True,
            "authority_policy": (
                "candidate_selection_utility_v1" if authority_applied else "legacy_total_score_v1"
            ),
        }),
        candidate_count=len(evidence_rows),
        promotion_eligible=qualified,
        blocking_reasons_json=_json(blocking_reasons),
        batch_hash=canonical_fingerprint(batch_material),
        captured_at=captured_at,
    )
    return TwinShadowBundle(batch, tuple(evidence_rows), tuple(outcomes_by_key[key] for key in sorted(outcomes_by_key)))


def load_candidate_selection_outcomes(
    *,
    limit_batches: int = 500,
    settled_through: str | None = None,
) -> list[dict[str, object]]:
    """Join settled canonical outcomes to their persisted mechanism labels."""

    from aletheia.storage import repositories as repo

    db = repo.get_db()
    records = db.execute(
        "SELECT r.scanner_run_id, r.compact_output_json FROM universe_twin_shadow_records r "
        "JOIN universe_twin_shadow_batches b ON b.scanner_run_id=r.scanner_run_id "
        "WHERE b.record_version=? ORDER BY b.captured_at DESC, r.candidate_index LIMIT ?",
        (CANDIDATE_SELECTION_RECORD_VERSION, int(limit_batches) * 1000),
    ).fetchall()
    labels_by_key: dict[str, list[dict[str, object]]] = {}
    for row in records:
        try:
            payload = json.loads(row["compact_output_json"])
            decision = payload["candidate_selection"]
            mechanisms = decision["opportunity_evidence"]["mechanisms"]
            keys = payload["outcome_candidate_keys"]
        except (KeyError, TypeError, ValueError):
            continue
        for mechanism in mechanisms:
            direction = mechanism.get("direction")
            percentile = mechanism.get("within_run_percentile")
            key = keys.get(direction) if direction else None
            if key and percentile is not None and mechanism.get("applicability") == "applicable":
                labels_by_key.setdefault(str(key), []).append({
                    "scanner_run_id": row["scanner_run_id"],
                    "mechanism_id": mechanism["mechanism_id"],
                    "strength_quintile": min(5, int(float(percentile) * 5) + 1),
                })
    if not labels_by_key:
        return []
    keys = sorted(labels_by_key)
    result = []
    for offset in range(0, len(keys), 500):
        chunk = keys[offset:offset + 500]
        placeholders = ",".join("?" for _ in chunk)
        rows = db.execute(
            f"SELECT * FROM filter_candidate_outcomes WHERE horizon='4h' AND outcome_candidate_key IN ({placeholders})",
            chunk,
        ).fetchall()
        for row in rows:
            base = dict(row)
            if settled_through:
                settled_at = parse_utc_instant(base.get("settled_at"))
                cutoff = parse_utc_instant(settled_through)
                if settled_at is None or cutoff is None or settled_at > cutoff:
                    continue
            for labels in labels_by_key.get(str(row["outcome_candidate_key"]), ()):
                result.append({**base, **labels})
    return result


def candidate_selection_training_cutoff(*, policy_epoch_id: str | None = None) -> str | None:
    """Return the first frozen estimator cutoff, optionally for one policy epoch."""

    from aletheia.storage import repositories as repo

    epoch_clause = " AND b.policy_epoch_id=?" if policy_epoch_id is not None else ""
    params: tuple[object, ...] = (
        (CANDIDATE_SELECTION_RECORD_VERSION, policy_epoch_id)
        if policy_epoch_id is not None else (CANDIDATE_SELECTION_RECORD_VERSION,)
    )
    rows = repo.get_db().execute(
        "SELECT b.captured_at, r.compact_output_json FROM universe_twin_shadow_batches b "
        "JOIN universe_twin_shadow_records r ON r.scanner_run_id=b.scanner_run_id "
        "WHERE b.record_version=? AND b.policy_epoch_id IS NOT NULL "
        f"{epoch_clause} "
        "ORDER BY b.captured_at, r.candidate_index",
        params,
    ).fetchall()
    for row in rows:
        try:
            contract = json.loads(row["compact_output_json"])["evidence_contract"]
        except (KeyError, TypeError, ValueError):
            continue
        cutoff = contract.get("estimator_training_cutoff")
        if cutoff:
            return str(cutoff)
    return None


def target_candidates_from_bundle(
    bundle: TwinShadowBundle,
    scored_population: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Project admitted target rows onto existing Candidate carrier fields."""

    by_identity = {_identity(item): item for item in scored_population}
    selected: list[dict[str, object]] = []
    for row in bundle.evidence:
        try:
            payload = json.loads(row.compact_output_json)
            if payload.get("target_selected") is not True:
                continue
            decision = payload["candidate_selection"]
            gate = decision["hard_gate"]
            instrument = gate["instrument"]
            identity = (
                str(row.feature_snapshot_id or ""),
                str(instrument["venue"]),
                str(instrument["market_type"]),
                str(instrument["symbol"]),
            )
            source = by_identity[identity]
            mechanism = next(
                item for item in decision["opportunity_evidence"]["mechanisms"]
                if item["mechanism_id"] == decision["primary_mechanism"]
            )
            family_reason = next(
                reason for reason in mechanism["reason_codes"]
                if str(reason).startswith("projected_from:")
            )
            family = str(family_reason).split(":", 1)[1]
            utility = float(decision["economic_utility_pct"])
        except (KeyError, StopIteration, TypeError, ValueError) as exc:
            raise ValueError("candidate_selection_target_projection_invalid") from exc
        raw_reasons = source.get("reasons")
        source_reasons = list(raw_reasons) if isinstance(raw_reasons, (list, tuple)) else []
        projected = dict(source)
        projected.update({
            "scanner_family": family,
            "scanner_direction": decision["primary_direction"],
            "candidate_selection_utility_pct": utility,
            "candidate_selection_policy": "candidate_selection_utility_v1",
            "candidate_selection_policy_epoch": bundle.batch.policy_epoch_id,
            "reasons": source_reasons + [
                f"candidate_selection_utility:{utility:.6f}",
                f"primary_mechanism:{decision['primary_mechanism']}",
            ],
        })
        selected.append(projected)
    selected.sort(key=lambda item: (
        -float(item["candidate_selection_utility_pct"]),
        str(item.get("symbol") or ""),
        str(item.get("venue") or ""),
        str(item.get("market_type") or ""),
    ))
    return selected


def target_admitted_identity_keys(
    bundle: TwinShadowBundle,
) -> set[tuple[str, str, str, str, str]]:
    """Return every above-floor target identity before the capacity ceiling."""

    identities = set()
    for row in bundle.evidence:
        try:
            decision = json.loads(row.compact_output_json)["candidate_selection"]
            if decision["admission_status"] != "admitted":
                continue
            instrument = decision["hard_gate"]["instrument"]
            mechanism = next(
                item for item in decision["opportunity_evidence"]["mechanisms"]
                if item["mechanism_id"] == decision["primary_mechanism"]
            )
            family = next(
                str(reason).split(":", 1)[1] for reason in mechanism["reason_codes"]
                if str(reason).startswith("projected_from:")
            )
            identities.add((
                str(instrument["venue"]),
                str(instrument["symbol"]),
                str(instrument["market_type"]),
                family,
                str(decision["primary_direction"]),
            ))
        except (KeyError, StopIteration, TypeError, ValueError) as exc:
            raise ValueError("candidate_selection_admitted_identity_invalid") from exc
    return identities


__all__ = [
    "CANDIDATE_SELECTION_RECORD_VERSION", "build_candidate_selection_shadow_bundle",
    "candidate_selection_training_cutoff", "load_candidate_selection_outcomes",
    "target_candidates_from_bundle",
    "target_admitted_identity_keys",
]
