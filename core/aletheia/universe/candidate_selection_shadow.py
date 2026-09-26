"""Current Candidate Selection Utility decisions and immutable population evidence."""

from __future__ import annotations

import json
import math
from typing import Mapping, Sequence

from aletheia.utils.dt import parse_utc_instant
from aletheia.config.schema import UniverseConfig
from aletheia.storage.models import UniverseTwinShadowBatch, UniverseTwinShadowEvidence
from aletheia.universe.candidate_eligibility import canonical_fingerprint, evaluate_hard_gate
from aletheia.universe.candidate_selection_utility import (
    CANDIDATE_SELECTION_UTILITY_VERSION,
    CandidateSelectionUtilityV1,
    build_candidate_selection_utility,
    select_admitted_candidates,
    candidate_selection_rank,
    decision_rank,
)
from aletheia.universe.candidate_selection_authority import candidate_selection_policy_identity
from aletheia.universe.economic_utility import (
    CANDIDATE_SELECTION_OUTCOME_VERSION,
    build_economic_utility_cohort_index,
    estimate_economic_utility,
    reason_proves_evidence_absent,
)
from aletheia.universe.opportunity_evidence import (
    annotate_within_run_percentiles,
    build_opportunity_evidence,
)
from aletheia.universe.twin_persistence import TwinShadowBundle
from aletheia.universe.features import CANDIDATE_FEATURE_ACQUISITION_FINGERPRINT

CANDIDATE_SELECTION_RECORD_VERSION = "candidate-selection-utility-record/v4"
CANDIDATE_SELECTION_METRIC_VERSION = "candidate-selection-utility-net-return-4h/v2"
CANDIDATE_SELECTION_EVIDENCE_CONTRACT_VERSION = "candidate-selection-evidence-cadence/v2"
CANDIDATE_SELECTION_EVIDENCE_INTERVAL_SEC = 4 * 60 * 60
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


def _compact_candidate_selection(
    decision: CandidateSelectionUtilityV1,
) -> dict[str, object]:
    """Keep immutable decision evidence while moving shared/raw detail out of every row."""

    value = decision.as_dict()
    value["hard_gate"] = {
        key: item for key, item in value["hard_gate"].items()
        if key not in {
            "candidate_key", "checks", "evaluated_at", "input_fingerprint",
            "policy_fingerprint",
        }
    }
    opportunity = {
        key: item for key, item in value["opportunity_evidence"].items()
        if key not in {"candidate_key", "feature_snapshot_id", "instrument"}
    }
    opportunity["mechanisms"] = [
        {
            key: item for key, item in mechanism.items()
            if key not in {"evidence_group_ids", "evidence_refs"}
            and item not in (None, [], ())
        }
        for mechanism in opportunity["mechanisms"]
    ]
    value["opportunity_evidence"] = opportunity
    value["economic_estimates"] = [
        {
            key: item for key, item in estimate.items()
            if key not in {
                "candidate_key", "horizon", "market_type", "uncertainty_penalty_pct",
                "venue", "version",
            }
        }
        for estimate in value["economic_estimates"]
    ]
    value.pop("candidate_key", None)
    value.pop("record_fingerprint", None)
    return value


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
        "min_tradable_edge_pct", "min_cost_covering_edge_pct", "estimated_fee_pct",
        "estimated_spread_slippage_pct", "estimated_funding_pct",
        "mechanism_behavior_version", "outcome_contract_version", "cost_assumptions",
    )
    material = []
    for row in outcomes:
        entry = parse_utc_instant(row.get("entry_ts"))
        settled = parse_utc_instant(row.get("settled_at"))
        if entry is not None and entry >= cutoff:
            continue
        material_row = {field: row.get(field) for field in fields}
        if settled is not None and settled > cutoff:
            for field in ("settled_at", "net_return_pct", "mae_pct", "cost_model_version",
                          "min_tradable_edge_pct", "min_cost_covering_edge_pct", "estimated_fee_pct",
                          "estimated_spread_slippage_pct", "estimated_funding_pct"):
                material_row[field] = None
            material_row["status"] = "pending"
        material.append(material_row)
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
    action_route_reasons: Mapping[tuple[str, str, str, str], str | None],
    historical_outcomes: Sequence[Mapping[str, object]],
    estimator_training_cutoff: str | None = None,
    selection_policy: Mapping[str, object] | None = None,
    authority_applied: bool = False,
    qualification_evidence: Mapping[str, object] | None = None,
) -> TwinShadowBundle:
    """Build one same-T0 full-denominator comparison and optional authority record."""

    frozen_selection_policy = dict(
        selection_policy or candidate_selection_policy_identity(universe_config)
    )
    cost_assumptions = {
        **dict(frozen_selection_policy.get("cost_assumptions") or {}),
        "fallback_spread_bps": universe_config.max_spread_bps,
    }
    cohort_index = build_economic_utility_cohort_index(
        historical_outcomes,
        source_cutoff_at=captured_at,
        cost_assumptions=cost_assumptions,
    )
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
        estimates = []
        for mechanism in evidence.mechanisms:
            if (
                mechanism.applicability == "applicable"
                and mechanism.direction in {"long", "short"}
            ):
                estimates.append(estimate_economic_utility(
                    candidate_key=gate.candidate_key,
                    mechanism_id=mechanism.mechanism_id,
                    direction=mechanism.direction,
                    venue=str(evidence.instrument["venue"]),
                    market_type=str(evidence.instrument["market_type"]),
                    strength_percentile=mechanism.within_run_percentile,
                    cohort_index=cohort_index,
                ))
        decision = build_candidate_selection_utility(
            hard_gate=gate,
            opportunity_evidence=evidence,
            economic_estimates=estimates,
            short_enabled=bool(frozen_selection_policy.get("short_enabled", True)),
        )
        decisions.append(decision)
    ranked_target = select_admitted_candidates(decisions, max_candidates=len(decisions))
    preferred_market = str(frozen_selection_policy.get("preferred_market_type") or "auto")
    market_rank = {"spot": 0, "perp": 1} if preferred_market == "spot" else {"perp": 0, "spot": 1}
    target_by_symbol = {}
    decision_features = {
        decision.candidate_key: feature
        for decision, feature in zip(decisions, population, strict=True)
    }
    for decision in ranked_target:
        feature = decision_features[decision.candidate_key]
        symbol = str(feature.get("symbol") or "")
        current = target_by_symbol.get(symbol)
        current_market = (
            str(decision_features[current.candidate_key].get("market_type") or "spot")
            if current is not None else None
        )
        if current is None or (decision_rank(decision)[:2], market_rank.get(
            str(feature.get("market_type") or "spot"), 2,
        ), decision.candidate_key) < (decision_rank(current)[:2], market_rank.get(current_market, 2), current.candidate_key):
            target_by_symbol[symbol] = decision
    target_selected_keys = {
        item.candidate_key
        for item in sorted(
            target_by_symbol.values(),
            key=decision_rank,
        )[:universe_config.max_candidates]
    }
    from aletheia.universe.candidate_funding import freeze_funding_proof
    for feature, gate, evidence, decision in zip(population, gates, bundles, decisions, strict=True):
        if gate.status != "eligible":
            continue
        directions = {
            item.direction for item in evidence.mechanisms
            if gate.status == "eligible"
            and item.applicability == "applicable" and item.direction in {"long", "short"}
        }
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
                funding_evidence_json=freeze_funding_proof(feature),
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
        estimate.source_outcome_fingerprint for decision in decisions for estimate in decision.economic_estimates
        if estimate.source_outcome_fingerprint
    })
    effective_training_cutoff = captured_at if authority_applied else estimator_training_cutoff or captured_at
    training_outcome_fingerprint = (
        _training_outcome_fingerprint(
            historical_outcomes,
            cutoff_at=effective_training_cutoff,
        )
        if effective_training_cutoff is not None else None
    )
    behavior_contract_hash = canonical_fingerprint({
        "version": CANDIDATE_SELECTION_UTILITY_VERSION,
        "record_version": CANDIDATE_SELECTION_RECORD_VERSION,
        "outcome_version": CANDIDATE_SELECTION_OUTCOME_VERSION,
        "training_cutoff": effective_training_cutoff,
        "training_outcome_fingerprint": training_outcome_fingerprint,
        "selection_policy": frozen_selection_policy,
    })
    policy_epoch_id = canonical_fingerprint(frozen_selection_policy)
    experiment_id = f"candidate-selection-utility-shadow:{behavior_contract_hash[:24]}"
    qualified = False  # Runtime authority is not an economic qualification verdict.
    blocking_reasons = [] if authority_applied else ["shadow_only"]

    evidence_rows = []
    for index, (feature, decision) in enumerate(zip(population, decisions, strict=True)):
        outcome_keys = {
            direction: key
            for direction in ("long", "short")
            if (key := _outcome_key(decision.candidate_key, direction)) in outcomes_by_key
        }
        material = {
            "record_version": CANDIDATE_SELECTION_RECORD_VERSION,
            "candidate_selection": _compact_candidate_selection(decision),
            "target_selected": decision.candidate_key in target_selected_keys,
            "outcome_candidate_keys": outcome_keys,
            "evidence_contract": {
                "feature_acquisition_version": CANDIDATE_FEATURE_ACQUISITION_FINGERPRINT,
                "shadow_authority_applied": authority_applied,
                "promotion_eligible": qualified,
                "blocking_reasons": blocking_reasons,
                "outcome_contract_version": CANDIDATE_SELECTION_OUTCOME_VERSION,
                "evidence_sampling_contract": CANDIDATE_SELECTION_EVIDENCE_CONTRACT_VERSION,
                "behavior_contract_hash": behavior_contract_hash,
                "policy_epoch_id": policy_epoch_id,
                "estimator_training_cutoff": effective_training_cutoff,
                "cost_assumptions": cost_assumptions,
                "training_outcome_fingerprint": training_outcome_fingerprint,
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
            authority_total_score=0.0,  # Historical NOT NULL storage field, not current evidence.
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
        "evidence_sampling_contract": CANDIDATE_SELECTION_EVIDENCE_CONTRACT_VERSION,
    }
    batch = UniverseTwinShadowBatch(
        scanner_run_id=scanner_run_id,
        record_version=CANDIDATE_SELECTION_RECORD_VERSION,
        authority_behavior_version=CANDIDATE_SELECTION_UTILITY_VERSION,
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
            "evidence_sampling_contract": CANDIDATE_SELECTION_EVIDENCE_CONTRACT_VERSION,
            "minimum_interval_sec": CANDIDATE_SELECTION_EVIDENCE_INTERVAL_SEC,
            "legacy_rollback": False,
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


def candidate_selection_storage_projection(
    evidence: Sequence[UniverseTwinShadowEvidence],
    *,
    min_interval_sec: int = CANDIDATE_SELECTION_EVIDENCE_INTERVAL_SEC,
) -> dict[str, int | float]:
    """Project the persisted Candidate row payload at the enforced Shadow cadence."""

    if min_interval_sec <= 0:
        raise ValueError("candidate_selection_storage_interval_invalid")
    bytes_per_run = sum(
        len(row.compact_output_json.encode("utf-8")) + len(row.input_json.encode("utf-8"))
        for row in evidence
    )
    runs_per_day = math.ceil(86_400 / min_interval_sec)
    mib_per_day = bytes_per_run * runs_per_day / (1024 * 1024)
    return {
        "rows_per_run": len(evidence),
        "bytes_per_run": bytes_per_run,
        "runs_per_day": runs_per_day,
        "mib_per_day": mib_per_day,
        "mib_per_30d": mib_per_day * 30,
    }


def load_candidate_selection_outcomes(
    *,
    selection_policy: Mapping[str, object],
    diagnostics_out: dict[str, object] | None = None,
    limit_batches: int = 500,
    settled_through: str | None = None,
) -> list[dict[str, object]]:
    """Join settled canonical outcomes to their persisted mechanism labels."""

    from aletheia.storage import repositories as repo

    db = repo.get_db()
    cutoff = parse_utc_instant(settled_through) if settled_through else None
    if settled_through and cutoff is None:
        raise ValueError("candidate_selection_training_cutoff_invalid")
    cutoff_clause = " AND b.captured_at < ?" if cutoff else ""
    limit_clause = "" if cutoff else " LIMIT ?"
    params: tuple[object, ...] = (
        (CANDIDATE_SELECTION_RECORD_VERSION, cutoff.isoformat())
        if cutoff else (CANDIDATE_SELECTION_RECORD_VERSION, int(limit_batches) * 1000)
    )
    records = db.execute(
        "SELECT r.scanner_run_id, r.compact_output_json FROM universe_twin_shadow_records r "
        "JOIN universe_twin_shadow_batches b ON b.scanner_run_id=r.scanner_run_id "
        f"WHERE b.record_version=?{cutoff_clause} "
        f"ORDER BY b.captured_at DESC, r.candidate_index{limit_clause}",
        params,
    ).fetchall()
    manifest = selection_policy.get("semantic_manifest") or {}
    expected_acquisition = manifest.get("feature_acquisition")
    excluded = 0
    labels_by_key: dict[str, list[dict[str, object]]] = {}
    for row in records:
        try:
            payload = json.loads(row["compact_output_json"])
            decision = payload["candidate_selection"]
            mechanisms = decision["opportunity_evidence"]["mechanisms"]
            keys = payload["outcome_candidate_keys"]
            contract = payload["evidence_contract"]
        except (KeyError, TypeError, ValueError):
            if diagnostics_out is not None:
                diagnostics_out["invalid_records"] = int(diagnostics_out.get("invalid_records", 0)) + 1
            raise ValueError("candidate_selection_history_record_invalid")
        if contract.get("feature_acquisition_version") != expected_acquisition:
            excluded += 1
            continue
        for mechanism in mechanisms:
            direction = mechanism.get("direction")
            percentile = mechanism.get("within_run_percentile")
            key = keys.get(direction) if direction else None
            if key and mechanism.get("applicability") == "applicable":
                labels_by_key.setdefault(str(key), []).append({
                    "scanner_run_id": row["scanner_run_id"],
                    "mechanism_id": mechanism["mechanism_id"],
                    "strength_quintile": min(5, int(float(percentile) * 5) + 1) if percentile is not None else None,
                    "mechanism_behavior_version": decision["opportunity_evidence"]["version"],
                    "outcome_contract_version": contract["outcome_contract_version"],
                    "cost_assumptions": contract["cost_assumptions"],
                })
    if diagnostics_out is not None:
        diagnostics_out["feature_acquisition_excluded"] = excluded
    if not labels_by_key:
        return []
    keys = sorted(labels_by_key)
    result = []
    from aletheia.universe.candidate_funding import funding_proof, proved_funding_interval
    expected_funding = manifest.get("perp_funding")
    funding_counts = {"perp_funding_historical_incompatible": 0,
                      "perp_funding_current_incomplete": 0, "perp_funding_current_complete": 0}
    for offset in range(0, len(keys), 500):
        chunk = keys[offset:offset + 500]
        placeholders = ",".join("?" for _ in chunk)
        rows = db.execute(
            f"SELECT * FROM filter_candidate_outcomes WHERE horizon='4h' AND outcome_candidate_key IN ({placeholders})",
            chunk,
        ).fetchall()
        if {str(row["outcome_candidate_key"]) for row in rows} != set(chunk):
            if diagnostics_out is not None:
                diagnostics_out["missing_outcomes"] = len(set(chunk) - {str(row["outcome_candidate_key"]) for row in rows})
            raise ValueError("candidate_selection_history_outcome_missing")
        for row in rows:
            base = dict(row)
            if base.get("market_type") == "perp" and expected_funding:
                if funding_proof(base).get("version") != expected_funding:
                    funding_counts["perp_funding_historical_incompatible"] += 1
                    continue
                complete = proved_funding_interval(base) is not None
                funding_counts["perp_funding_current_complete" if complete else
                               "perp_funding_current_incomplete"] += 1
            if cutoff:
                entry_at = parse_utc_instant(base.get("entry_ts"))
                settled_at = parse_utc_instant(base.get("settled_at"))
                if entry_at is not None and entry_at >= cutoff:
                    continue
                if settled_at is not None and settled_at > cutoff:
                    for field in (
                        "settled_at", "exit_price_usd", "gross_return_pct",
                        "directional_return_pct", "estimated_fee_pct",
                        "estimated_spread_slippage_pct", "estimated_funding_pct",
                        "net_return_pct", "min_tradable_edge_pct", "min_cost_covering_edge_pct", "mfe_pct",
                        "mae_pct", "settlement_price_source", "settlement_lag_sec",
                        "cost_model_version", "settlement_runtime_session_id",
                        "settlement_producer_commit", "settlement_behavior_fingerprint",
                    ):
                        base[field] = None
                    base["status"] = "pending"
            for labels in labels_by_key.get(str(row["outcome_candidate_key"]), ()):
                result.append({**base, **labels})
    if diagnostics_out is not None:
        diagnostics_out.update(funding_counts)
    return result


def candidate_selection_training_cutoff(
    *,
    policy_epoch_id: str | None = None,
    selection_policy: Mapping[str, object] | None = None,
) -> str | None:
    """Return the first frozen estimator cutoff, optionally for one policy epoch."""

    from aletheia.storage import repositories as repo

    epoch_clause = " AND b.policy_epoch_id=?" if policy_epoch_id is not None else ""
    params: tuple[object, ...] = (
        (CANDIDATE_SELECTION_RECORD_VERSION, "candidate-selection-utility-record/v3", policy_epoch_id)
        if policy_epoch_id is not None else (CANDIDATE_SELECTION_RECORD_VERSION, "candidate-selection-utility-record/v3")
    )
    rows = repo.get_db().execute(
        "SELECT b.captured_at, b.policy_json, r.compact_output_json "
        "FROM universe_twin_shadow_batches b "
        "JOIN universe_twin_shadow_records r ON r.scanner_run_id=b.scanner_run_id "
        "WHERE b.record_version IN (?,?) AND b.policy_epoch_id IS NOT NULL "
        f"{epoch_clause} "
        "ORDER BY b.captured_at, r.candidate_index",
        params,
    ).fetchall()
    for row in rows:
        try:
            frozen_policy = json.loads(row["policy_json"]).get("selection_policy")
            contract = json.loads(row["compact_output_json"])["evidence_contract"]
        except (KeyError, TypeError, ValueError):
            continue
        if selection_policy is not None and frozen_policy != dict(selection_policy):
            continue
        cutoff = contract.get("estimator_training_cutoff")
        if cutoff:
            return str(cutoff)
    return None


def candidate_selection_frozen_family_spread_control(
    selection_policy: Mapping[str, object],
) -> tuple[dict[str, float], dict[str, object]] | None:
    """Replay the first persisted adaptive filter state for this policy."""

    from aletheia.storage import repositories as repo

    expected = json.loads(_json(selection_policy))
    for row in repo.get_db().execute(
        "SELECT policy_json FROM universe_twin_shadow_batches "
        "WHERE record_version=? AND policy_epoch_id IS NOT NULL "
        "ORDER BY captured_at, scanner_run_id",
        (CANDIDATE_SELECTION_RECORD_VERSION,),
    ).fetchall():
        try:
            frozen = json.loads(row["policy_json"])["selection_policy"]
            control = frozen["legacy_control"]
            tolerances = control.pop("family_spread_tolerances")
            source = control.pop("family_spread_tolerance_source")
            normalized = {
                str(family): float(tolerance)
                for family, tolerance in tolerances.items()
            }
        except (KeyError, TypeError, ValueError):
            continue
        if frozen != expected or any(not math.isfinite(value) for value in normalized.values()):
            continue
        if not isinstance(source, dict) or not all(
            isinstance(source.get(field), str) and bool(source[field])
            for field in ("source_cutoff_at", "source_fingerprint")
        ):
            continue
        return dict(sorted(normalized.items())), source
    return None


def target_candidates_from_bundle(
    bundle: TwinShadowBundle,
    feature_population: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Build current Candidates from exact features and the selected decision."""

    by_identity = {_identity(item): item for item in feature_population}
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
            family = str(mechanism["source_component_id"])
            if not family:
                raise ValueError("candidate_selection_source_component_invalid")
            raw_utility = decision["economic_utility_pct"]
            utility = float(raw_utility) if raw_utility is not None else None
            if utility is None and not (
                decision.get("admission_basis") == "startup"
                and reason_proves_evidence_absent(decision.get("startup_reason"))
            ):
                raise ValueError("candidate_selection_startup_invalid")
        except (KeyError, StopIteration, TypeError, ValueError) as exc:
            raise ValueError("candidate_selection_target_projection_invalid") from exc
        projected = {key: source[key] for key in (
            "asset_id", "symbol", "venue", "market_type", "feature_snapshot_id",
            "last_price", "quote_volume_24h", "spread_bps", "funding_rate",
            "instrument_background", "analysis_context",
        ) if key in source}
        projected.update({
            "execution_eligible": gate["status"] == "eligible" and gate["actionability"] == "actionable",
            "scanner_family": family,
            "scanner_direction": decision["primary_direction"],
            "candidate_selection_utility_pct": utility,
            "admission_basis": decision.get("admission_basis"),
            "startup_reason": decision.get("startup_reason"),
            "pessimistic_strength": mechanism.get("pessimistic_strength"),
            "candidate_selection_key": row.candidate_key,
            "candidate_selection_policy": "candidate_selection_utility_v1",
            "candidate_selection_policy_epoch": bundle.batch.policy_epoch_id,
            "opportunity_mechanism": decision["primary_mechanism"],
            "opportunity_mechanism_version": mechanism["mechanism_version"],
            "opportunity_source_component": family,
            "opportunity_source_cutoff_at": decision["opportunity_evidence"]["source_cutoff_at"],
            "opportunity_evidence_hash": decision["opportunity_evidence"]["mechanism_set_fingerprint"],
            "_candidate_opportunity_record": {
                "decision": decision,
                "source_record_hash": row.record_hash,
            },
            "reasons": [
                f"candidate_selection_utility:{utility:.6f}" if utility is not None else f"candidate_selection_startup:{decision['startup_reason']}",
                f"primary_mechanism:{decision['primary_mechanism']}",
            ],
        })
        selected.append(projected)
    selected.sort(key=lambda item: candidate_selection_rank(
        item.get("admission_basis"), item.get("candidate_selection_utility_pct"),
        item.get("pessimistic_strength"), str(item["candidate_selection_key"]),
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
            family = str(mechanism["source_component_id"])
            if not family:
                raise ValueError("candidate_selection_source_component_invalid")
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
    "CANDIDATE_SELECTION_EVIDENCE_CONTRACT_VERSION",
    "CANDIDATE_SELECTION_EVIDENCE_INTERVAL_SEC",
    "CANDIDATE_SELECTION_RECORD_VERSION", "build_candidate_selection_shadow_bundle",
    "candidate_selection_training_cutoff",
    "candidate_selection_frozen_family_spread_control",
    "load_candidate_selection_outcomes",
    "target_candidates_from_bundle",
    "target_admitted_identity_keys",
    "candidate_selection_storage_projection",
]
