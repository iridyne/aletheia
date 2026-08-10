"""Durable, non-authoritative evidence for the relative-rank challenger."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from collections.abc import Mapping, Sequence

from aletheia.analytics.filter_evidence import build_candidate_outcomes
from aletheia.storage.models import (
    FilterCandidateOutcome,
    UniverseRelativeRankShadowBatch,
)
from aletheia.universe.relative_rank_shadow import RelativeRankBatch
from aletheia.universe.twin_persistence import TwinExperimentContext
from aletheia.universe.twin_scoring import TwinShadowInput


RELATIVE_RANK_CAPTURE_INTERVAL_SEC = 55 * 60
RELATIVE_RANK_FEATURE_SOURCE_CONTRACT = "universe-relative-rank-feature-source/v1"
_INPUT_FIELDS = (
    "feature_snapshot_id",
    "asset_id",
    "symbol",
    "venue",
    "market_type",
    "last_price",
    "quote_volume_24h",
    "spread_bps",
    "depth_10bps_usd",
    "data_freshness_sec",
    "funding_rate",
    "open_interest",
    "oi_delta_1h",
    "long_short_ratio",
    "top_trader_position_ratio",
)


@dataclass(frozen=True)
class RelativeRankShadowBundle:
    batch: UniverseRelativeRankShadowBatch
    outcomes: tuple[FilterCandidateOutcome, ...] = ()


def _safe(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return None


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def relative_rank_feature_source_fingerprint(*, provider_id: str) -> str:
    return _sha256(
        {
            "contract": RELATIVE_RANK_FEATURE_SOURCE_CONTRACT,
            "provider_id": provider_id or "not_available",
            "fields": _INPUT_FIELDS,
        }
    )


def build_relative_rank_shadow_bundle(
    relative_batch: RelativeRankBatch,
    inputs: Sequence[TwinShadowInput],
    *,
    authority_snapshot_id: str | None,
    experiment: TwinExperimentContext,
    provider_id: str,
    outcome_venue: str,
    regime_state: str | None,
    captured_at: str | None = None,
) -> RelativeRankShadowBundle:
    """Attach assigned-arm outcomes to one already-frozen relative-rank batch."""

    captured = captured_at or datetime.now(timezone.utc).isoformat()
    input_by_identity = {
        (
            str(item.feature.get("asset_id") or ""),
            str(item.feature.get("market_type") or "spot").lower(),
            str(item.feature.get("symbol") or ""),
        ): item
        for item in inputs
    }
    records: list[dict[str, object]] = []
    outcomes: list[FilterCandidateOutcome] = []
    for record in relative_batch.records:
        item = input_by_identity.get((record.asset_id or "", record.market_type, record.symbol))
        if item is None:
            raise ValueError(f"relative_rank_input_not_available:{record.candidate_key}")
        if experiment.assignment_arm == "treatment":
            assigned_selected = record.relative_selected
            family = record.family
            direction = record.direction
        elif experiment.assignment_arm == "control":
            assigned_selected = record.authority_selected
            family = item.authority_scanner_result.get("scanner_family")
            direction = item.authority_scanner_result.get("scanner_direction") or item.authority_scanner_result.get(
                "direction"
            )
        else:
            raise ValueError("relative_rank_assignment_arm_invalid")

        outcome_key = None
        if relative_batch.available and assigned_selected:
            feature = item.feature
            price = feature.get("last_price") or feature.get("price") or feature.get("close")
            try:
                entry_price = float(price)
            except (TypeError, ValueError):
                entry_price = 0.0
            normalized_direction = str(direction or "").lower()
            normalized_family = str(family or "")
            venue = str(feature.get("venue") or outcome_venue or "")
            if normalized_direction not in {"long", "short"} or not normalized_family or not venue or entry_price <= 0:
                raise ValueError(f"relative_rank_outcome_identity_not_available:{record.candidate_key}")
            outcome_key = f"relative-rank:{experiment.experiment_id}:{record.candidate_key}:prospective"
            built = build_candidate_outcomes(
                outcome_candidate_key=outcome_key,
                venue=venue,
                symbol=record.symbol,
                market_type=record.market_type,
                direction=normalized_direction,
                scanner_family=normalized_family,
                regime_tag=regime_state,
                entry_price_usd=entry_price,
                entry_spread_bps=feature.get("spread_bps"),
                funding_rate_at_entry=feature.get("funding_rate"),
                entry_ts=captured,
                allocation_selected=True,
                horizons=(experiment.primary_horizon,),
            )
            if len(built) != 1:
                raise ValueError("relative_rank_primary_outcome_not_available")
            outcomes.extend(built)
        records.append(
            {
                **asdict(record),
                "assigned_policy_selected": assigned_selected,
                "assigned_policy_family": family,
                "assigned_policy_direction": direction,
                "outcome_candidate_key": outcome_key,
                "input": {field: _safe(item.feature.get(field)) for field in _INPUT_FIELDS},
            }
        )

    blockers = {
        "shadow_authority_not_applied",
        "promotion_not_authorized",
        "availability_profile_not_available",
        "no_actual_authority_intervention",
    }
    if not relative_batch.available and relative_batch.unavailable_reason:
        blockers.add(relative_batch.unavailable_reason)
    records_json = _canonical_json(records)
    material = {
        "scanner_run_id": relative_batch.scanner_run_id,
        "experiment_id": experiment.experiment_id,
        "policy_epoch_id": experiment.policy_epoch_id,
        "behavior_contract_hash": experiment.behavior_contract_hash,
        "assignment_arm": experiment.assignment_arm,
        "assignment_propensity": experiment.assignment_propensity,
        "record_version": relative_batch.record_version,
        "behavior_version": relative_batch.behavior_version,
        "min_candidate_count": relative_batch.min_candidate_count,
        "candidate_count": relative_batch.candidate_count,
        "eligible_count": relative_batch.eligible_count,
        "available": relative_batch.available,
        "records": records,
        "captured_at": captured,
    }
    return RelativeRankShadowBundle(
        batch=UniverseRelativeRankShadowBatch(
            scanner_run_id=relative_batch.scanner_run_id,
            authority_snapshot_id=authority_snapshot_id,
            experiment_id=experiment.experiment_id,
            policy_epoch_id=experiment.policy_epoch_id,
            behavior_contract_hash=experiment.behavior_contract_hash,
            assignment_unit_key=experiment.assignment_unit_key,
            assignment_contract_version=experiment.assignment_contract_version,
            assignment_arm=experiment.assignment_arm,
            assignment_propensity=experiment.assignment_propensity,
            treatment_coefficient=experiment.treatment_coefficient,
            treatment_policy=experiment.treatment_policy,
            control_policy=experiment.control_policy,
            primary_horizon=experiment.primary_horizon,
            outcome_contract_version=experiment.outcome_contract_version,
            metric_version=experiment.metric_version,
            feature_source_fingerprint=relative_rank_feature_source_fingerprint(provider_id=provider_id),
            record_version=relative_batch.record_version,
            behavior_version=relative_batch.behavior_version,
            min_candidate_count=relative_batch.min_candidate_count,
            candidate_count=relative_batch.candidate_count,
            eligible_count=relative_batch.eligible_count,
            available=relative_batch.available,
            promotion_eligible=False,
            blocking_reasons_json=_canonical_json(sorted(blockers)),
            records_json=records_json,
            batch_hash=_sha256(material),
            captured_at=captured,
        ),
        outcomes=tuple(outcomes),
    )


__all__ = [
    "RELATIVE_RANK_CAPTURE_INTERVAL_SEC",
    "RELATIVE_RANK_FEATURE_SOURCE_CONTRACT",
    "RelativeRankShadowBundle",
    "build_relative_rank_shadow_bundle",
    "relative_rank_feature_source_fingerprint",
]
