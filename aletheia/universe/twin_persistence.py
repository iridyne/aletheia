"""Durable, replayable evidence for the non-authoritative Universe twin scorer.

This module has no runtime authority.  It serializes an already-computed twin
batch, registers direction-specific fixed-horizon outcomes, and can replay the
pure scoring/allocation path for integrity checks.  Missing experiment identity
is recorded as missing; no assignment or propensity is inferred.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import random
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

from aletheia.analytics.filter_evidence import build_candidate_outcomes
from aletheia.config.schema import UniverseConfig
from aletheia.storage.models import (
    FilterCandidateOutcome,
    UniverseTwinShadowBatch,
    UniverseTwinShadowEvidence,
)
from aletheia.universe.continuous_scoring import (
    ContinuousScoringPolicy,
    raw_feature_declarations,
)
from aletheia.universe.outcome_economics import COST_MODEL_VERSION
from aletheia.universe.twin_allocation import (
    TWIN_ALLOCATION_REPLAY_CONTRACT,
    replay_twin_shadow_allocations,
)
from aletheia.universe.twin_scoring import (
    TwinShadowInput,
    TwinShadowRecord,
    annotate_authority_selection,
    build_twin_shadow_batch,
)


TWIN_FEATURE_SOURCE_CONTRACT = "universe-twin-feature-source-v1"
TWIN_OUTCOME_CONTRACT_VERSION = "universe-twin-direction-outcomes-v1"
TWIN_METRIC_VERSION = "universe-continuous-shadow-cluster-allocation-utility-v1"

_IDENTITY_AND_MARKET_FIELDS = frozenset(
    {
        "feature_snapshot_id",
        "asset_id",
        "symbol",
        "market_type",
        "venue",
        "last_price",
        "price",
        "close",
    }
)
_SCANNER_FIELDS = frozenset(
    {"scanner_family", "scanner_direction", "direction", "opportunity_score", "reasons"}
)
_AUDIT_FIELDS = frozenset(
    {"contract", "effective_opportunity_multiplier", "calibration_authority_applied"}
)
_TASK5_BLOCKERS = (
    "assignment_not_available",
    "availability_profile_not_available",
    "feature_observation_lineage_not_available",
    "policy_epoch_not_available",
    "propensity_not_available",
    "task5_diagnostic_only",
)
_TASK5_UNAVAILABLE_IDENTITIES = (
    "policy_epoch",
    "availability_profile",
    "assignment",
    "propensity",
)


@dataclass(frozen=True)
class TwinShadowBundle:
    batch: UniverseTwinShadowBatch
    evidence: tuple[UniverseTwinShadowEvidence, ...]
    outcomes: tuple[FilterCandidateOutcome, ...]


@dataclass(frozen=True)
class TwinReplayReport:
    scanner_run_id: str
    candidate_count: int
    matched_count: int
    mismatched_candidate_keys: tuple[str, ...]
    batch_hash_matches: bool

    @property
    def matches(self) -> bool:
        return self.batch_hash_matches and not self.mismatched_candidate_keys


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _safe_value(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _safe_value(item) for key, item in value.items()}
    return None


def _selected(source: Mapping[str, object], allowed: frozenset[str]) -> dict[str, object]:
    return {
        key: _safe_value(source[key])
        for key in sorted(allowed)
        if key in source
    }


def _safe_input(item: TwinShadowInput) -> dict[str, object]:
    feature_fields = frozenset(raw_feature_declarations()) | _IDENTITY_AND_MARKET_FIELDS
    return {
        "feature": _selected(item.feature, feature_fields),
        "authority_scanner_result": _selected(item.authority_scanner_result, _SCANNER_FIELDS),
        "authority_total_score": float(item.authority_total_score),
        "authority_score_audit": _selected(item.authority_score_audit, _AUDIT_FIELDS),
    }


def feature_source_fingerprint(*, provider_id: str) -> str:
    manifest = {
        feature_id: asdict(declaration)
        for feature_id, declaration in sorted(raw_feature_declarations().items())
    }
    return _sha256(
        {
            "contract": TWIN_FEATURE_SOURCE_CONTRACT,
            "provider_id": provider_id or "not_available",
            "availability_profile_id": "not_available",
            "raw_feature_manifest": manifest,
        }
    )


def encode_random_state(state: object) -> str:
    return _canonical_json(state)


def _tuples(value: object) -> object:
    if isinstance(value, list):
        return tuple(_tuples(item) for item in value)
    return value


def decode_random_state(payload: str) -> object:
    return _tuples(json.loads(payload))


def _candidate_key(scanner_run_id: str, feature: Mapping[str, object]) -> str:
    feature_id = feature.get("feature_snapshot_id")
    asset_id = feature.get("asset_id")
    symbol = str(feature.get("symbol") or "")
    market_type = str(feature.get("market_type") or "spot")
    source_id = feature_id or asset_id or "not_available"
    return (
        f"universe-twin:{scanner_run_id or 'not_available'}:"
        f"{source_id}:{market_type}:{symbol or 'not_available'}"
    )


def _positive_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def _optional_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _direction_outcome_key(candidate_key: str, direction: str | None) -> str | None:
    if direction not in {"long", "short"}:
        return None
    return f"{candidate_key}:direction:{direction}"


def _record_material(
    record: TwinShadowRecord,
    *,
    candidate_index: int,
    outcome_keys: Mapping[str, str | None],
) -> dict[str, object]:
    record_payload = asdict(record)
    record_payload["unavailable_promotion_identities"] = list(
        _TASK5_UNAVAILABLE_IDENTITIES
    )
    record_payload["blocking_reasons"] = sorted(
        {
            *(reason for reason in record.blocking_reasons if reason != "task4_diagnostic_only"),
            "feature_observation_lineage_not_available",
            "task5_diagnostic_only",
        }
    )
    return {
        "record": record_payload,
        "evidence_contract": {
            "candidate_index": candidate_index,
            "assignment_unit_id": record.scanner_run_id,
            "assignment_arm": None,
            "propensity": None,
            "treatment_coefficient": None,
            "shadow_authority_applied": False,
            "promotion_eligible": False,
            "unavailable_identities": list(_TASK5_BLOCKERS),
        },
        "outcome_candidate_keys": dict(outcome_keys),
    }


def _allocation_context(
    *,
    universe_config: UniverseConfig,
    enabled_markets: Mapping[str, bool],
    preferred_market_type: str,
    family_tolerances: Mapping[str, float] | None,
    regime_state: str | None,
) -> dict[str, object]:
    return {
        "universe_config": universe_config.model_dump(mode="json"),
        "exchange": {
            "enabled_markets": dict(sorted(enabled_markets.items())),
            "preferred_market_type": preferred_market_type,
        },
        "family_tolerances": dict(sorted((family_tolerances or {}).items())),
        "regime_state": regime_state,
    }


def build_twin_shadow_bundle(
    records: Sequence[TwinShadowRecord],
    inputs: Sequence[TwinShadowInput],
    *,
    scanner_run_id: str,
    authority_snapshot_id: str | None,
    policy: ContinuousScoringPolicy,
    allocator_random_state: object,
    universe_config: UniverseConfig,
    enabled_markets: Mapping[str, bool],
    preferred_market_type: str,
    family_tolerances: Mapping[str, float] | None,
    regime_state: str | None,
    provider_id: str,
    captured_at: str | None = None,
) -> TwinShadowBundle:
    """Build complete Task-5 evidence in memory; performs no database writes."""

    captured = captured_at or datetime.now(timezone.utc).isoformat()
    safe_inputs = {_candidate_key(scanner_run_id, item.feature): _safe_input(item) for item in inputs}
    evidence: list[UniverseTwinShadowEvidence] = []
    outcomes_by_id: dict[str, FilterCandidateOutcome] = {}

    for candidate_index, record in enumerate(records):
        safe_input = safe_inputs.get(record.candidate_key)
        if safe_input is None:
            raise ValueError(f"twin_input_not_available:{record.candidate_key}")
        directions = {
            "authority": record.authority_direction,
            "shadow_point": record.shadow.point_direction if record.shadow.valid else None,
            "shadow_pessimistic": record.shadow.pessimistic_direction if record.shadow.valid else None,
        }
        outcome_keys = {
            arm: _direction_outcome_key(record.candidate_key, direction)
            for arm, direction in directions.items()
        }
        feature = safe_input["feature"]
        assert isinstance(feature, dict)
        entry_price = _positive_float(
            feature.get("last_price") or feature.get("price") or feature.get("close")
        )
        for arm, outcome_key in outcome_keys.items():
            direction = directions[arm]
            if outcome_key is None or direction is None or entry_price is None:
                continue
            for outcome in build_candidate_outcomes(
                outcome_candidate_key=outcome_key,
                venue=str(feature.get("venue") or provider_id or "not_available"),
                symbol=record.symbol,
                market_type=record.market_type,
                direction=direction,
                scanner_family=None,
                regime_tag=regime_state,
                entry_price_usd=entry_price,
                entry_spread_bps=_optional_float(feature.get("spread_bps")),
                funding_rate_at_entry=_optional_float(feature.get("funding_rate")),
                entry_ts=captured,
                allocation_selected=None,
            ):
                outcomes_by_id[outcome.id] = outcome

        material = _record_material(
            record,
            candidate_index=candidate_index,
            outcome_keys=outcome_keys,
        )
        record_json = _canonical_json(material)
        input_json = _canonical_json(safe_input)
        evidence.append(
            UniverseTwinShadowEvidence(
                id=_sha256({"scanner_run_id": scanner_run_id, "candidate_key": record.candidate_key})[:24],
                scanner_run_id=scanner_run_id,
                candidate_key=record.candidate_key,
                candidate_index=candidate_index,
                feature_snapshot_id=record.feature_snapshot_id,
                asset_id=record.asset_id,
                symbol=record.symbol,
                market_type=record.market_type,
                assignment_unit_id=scanner_run_id,
                authority_outcome_candidate_key=outcome_keys["authority"],
                shadow_point_outcome_candidate_key=outcome_keys["shadow_point"],
                shadow_pessimistic_outcome_candidate_key=outcome_keys["shadow_pessimistic"],
                authority_total_score=record.authority_total_score,
                shadow_point_score=record.shadow.point_score if record.shadow.valid else None,
                shadow_pessimistic_score=record.shadow.pessimistic_score if record.shadow.valid else None,
                authority_rank=record.authority_rank,
                shadow_point_rank=record.shadow_point_rank,
                shadow_pessimistic_rank=record.shadow_pessimistic_rank,
                fine_filter_passed=record.fine_filter_passed,
                authority_selected=record.authority_selected,
                shadow_point_selected=record.shadow_point_selected,
                shadow_pessimistic_selected=record.shadow_pessimistic_selected,
                shadow_authority_applied=False,
                promotion_eligible=False,
                shadow_valid=record.shadow.valid,
                record_json=record_json,
                input_json=input_json,
                record_hash=_sha256({"record": material, "input": safe_input}),
                captured_at=captured,
            )
        )

    context = _allocation_context(
        universe_config=universe_config,
        enabled_markets=enabled_markets,
        preferred_market_type=preferred_market_type,
        family_tolerances=family_tolerances,
        regime_state=regime_state,
    )
    policy_json = _canonical_json(asdict(policy))
    random_state_json = encode_random_state(allocator_random_state)
    source_fingerprint = feature_source_fingerprint(provider_id=provider_id)
    first = records[0] if records else None
    record_version = first.record_version if first else "universe-twin-shadow-record-v1"
    authority_behavior_version = first.authority_behavior_version if first else "not_available"
    shadow_behavior_version = first.shadow_behavior_version if first else "not_available"
    batch_material = {
        "scanner_run_id": scanner_run_id,
        "authority_snapshot_id": authority_snapshot_id,
        "record_version": record_version,
        "authority_behavior_version": authority_behavior_version,
        "shadow_behavior_version": shadow_behavior_version,
        "allocation_replay_contract": TWIN_ALLOCATION_REPLAY_CONTRACT,
        "policy_epoch_id": None,
        "feature_source_fingerprint": source_fingerprint,
        "availability_profile_id": None,
        "outcome_contract_version": TWIN_OUTCOME_CONTRACT_VERSION,
        "cost_model_version": COST_MODEL_VERSION,
        "metric_version": TWIN_METRIC_VERSION,
        "policy": json.loads(policy_json),
        "allocation_context": context,
        "allocator_random_state": json.loads(random_state_json),
        "candidate_count": len(evidence),
        "promotion_eligible": False,
        "blocking_reasons": list(_TASK5_BLOCKERS),
        "captured_at": captured,
        "record_hashes": [
            item.record_hash for item in sorted(evidence, key=lambda value: value.candidate_key)
        ],
    }
    batch = UniverseTwinShadowBatch(
        scanner_run_id=scanner_run_id,
        authority_snapshot_id=authority_snapshot_id,
        record_version=record_version,
        authority_behavior_version=authority_behavior_version,
        shadow_behavior_version=shadow_behavior_version,
        allocation_replay_contract=TWIN_ALLOCATION_REPLAY_CONTRACT,
        policy_epoch_id=None,
        feature_source_fingerprint=source_fingerprint,
        availability_profile_id=None,
        outcome_contract_version=TWIN_OUTCOME_CONTRACT_VERSION,
        cost_model_version=COST_MODEL_VERSION,
        metric_version=TWIN_METRIC_VERSION,
        allocator_random_state_json=random_state_json,
        policy_json=policy_json,
        allocation_context_json=_canonical_json(context),
        candidate_count=len(evidence),
        promotion_eligible=False,
        blocking_reasons_json=_canonical_json(_TASK5_BLOCKERS),
        batch_hash=_sha256(batch_material),
        captured_at=captured,
    )
    return TwinShadowBundle(batch, tuple(evidence), tuple(outcomes_by_id.values()))


def _dedupe_for_replay(values: list[dict], app_config: Any) -> list[dict]:
    preferred = getattr(app_config.exchange, "preferred_market_type", "auto")
    enabled = app_config.exchange.enabled_markets
    spot_enabled = enabled.get("spot", True)
    perp_enabled = enabled.get("usdt_futures", False)
    preferred_market = "spot" if preferred == "spot" else "perp"

    def market_rank(item: dict) -> int:
        if spot_enabled and perp_enabled:
            return 0 if item.get("market_type", "spot") == preferred_market else 1
        return 0

    selected: dict[str, dict] = {}
    for item in values:
        symbol = str(item["symbol"])
        current = selected.get(symbol)
        if current is None or (
            market_rank(item), -float(item.get("total_score", 0.0))
        ) < (
            market_rank(current), -float(current.get("total_score", 0.0))
        ):
            selected[symbol] = item
    return sorted(selected.values(), key=lambda item: item["total_score"], reverse=True)


def replay_twin_shadow_bundle(
    batch: Mapping[str, object],
    evidence_rows: Sequence[Mapping[str, object]],
) -> TwinReplayReport:
    """Recompute one persisted batch and compare its canonical record payloads."""

    scanner_run_id = str(batch["scanner_run_id"])
    ordered_rows = sorted(evidence_rows, key=lambda row: int(row["candidate_index"]))
    if [int(row["candidate_index"]) for row in ordered_rows] != list(range(len(ordered_rows))):
        raise ValueError("twin_candidate_index_not_contiguous")
    inputs = []
    for row in ordered_rows:
        payload = json.loads(str(row["input_json"]))
        inputs.append(
            TwinShadowInput(
                feature=payload["feature"],
                authority_scanner_result=payload["authority_scanner_result"],
                authority_total_score=payload["authority_total_score"],
                authority_score_audit=payload["authority_score_audit"],
            )
        )
    policy = ContinuousScoringPolicy(**json.loads(str(batch["policy_json"])))
    replayed = build_twin_shadow_batch(inputs, scanner_run_id=scanner_run_id, base_policy=policy)
    by_key = {str(row["candidate_key"]): row for row in ordered_rows}
    fine = [item.feature for item in inputs if bool(by_key[_candidate_key(scanner_run_id, item.feature)]["fine_filter_passed"])]
    allocated = [item.feature for item in inputs if bool(by_key[_candidate_key(scanner_run_id, item.feature)]["authority_selected"])]
    replayed = annotate_authority_selection(replayed, fine_filtered=fine, allocated=allocated)

    context = json.loads(str(batch["allocation_context_json"]))
    exchange = context["exchange"]
    app_config = SimpleNamespace(
        exchange=SimpleNamespace(
            enabled_markets=exchange["enabled_markets"],
            preferred_market_type=exchange["preferred_market_type"],
        )
    )
    replayed = replay_twin_shadow_allocations(
        replayed,
        inputs,
        scanner_run_id=scanner_run_id,
        universe_config=UniverseConfig.model_validate(context["universe_config"]),
        app_config=app_config,
        family_tolerances=context["family_tolerances"],
        regime_state=context["regime_state"],
        allocator_random_state=decode_random_state(str(batch["allocator_random_state_json"])),
        dedupe=_dedupe_for_replay,
    )

    mismatches: list[str] = []
    record_hashes_by_key: dict[str, str] = {}
    for record in replayed:
        row = by_key[record.candidate_key]
        stored_material = json.loads(str(row["record_json"]))
        replay_material = _record_material(
            record,
            candidate_index=int(row["candidate_index"]),
            outcome_keys=stored_material["outcome_candidate_keys"],
        )
        input_payload = json.loads(str(row["input_json"]))
        record_hash = _sha256({"record": replay_material, "input": input_payload})
        record_hashes_by_key[record.candidate_key] = record_hash
        if record_hash != str(row["record_hash"]):
            mismatches.append(record.candidate_key)

    batch_material = {
        "scanner_run_id": scanner_run_id,
        "authority_snapshot_id": batch.get("authority_snapshot_id"),
        "record_version": batch["record_version"],
        "authority_behavior_version": batch["authority_behavior_version"],
        "shadow_behavior_version": batch["shadow_behavior_version"],
        "allocation_replay_contract": batch["allocation_replay_contract"],
        "policy_epoch_id": batch.get("policy_epoch_id"),
        "feature_source_fingerprint": batch["feature_source_fingerprint"],
        "availability_profile_id": batch.get("availability_profile_id"),
        "outcome_contract_version": batch["outcome_contract_version"],
        "cost_model_version": batch["cost_model_version"],
        "metric_version": batch["metric_version"],
        "policy": json.loads(str(batch["policy_json"])),
        "allocation_context": context,
        "allocator_random_state": json.loads(str(batch["allocator_random_state_json"])),
        "candidate_count": int(batch["candidate_count"]),
        "promotion_eligible": bool(batch["promotion_eligible"]),
        "blocking_reasons": json.loads(str(batch["blocking_reasons_json"])),
        "captured_at": batch["captured_at"],
        "record_hashes": [record_hashes_by_key[key] for key in sorted(record_hashes_by_key)],
    }
    return TwinReplayReport(
        scanner_run_id=scanner_run_id,
        candidate_count=len(evidence_rows),
        matched_count=len(evidence_rows) - len(mismatches),
        mismatched_candidate_keys=tuple(sorted(mismatches)),
        batch_hash_matches=_sha256(batch_material) == str(batch["batch_hash"]),
    )


__all__ = [
    "TWIN_FEATURE_SOURCE_CONTRACT",
    "TWIN_METRIC_VERSION",
    "TWIN_OUTCOME_CONTRACT_VERSION",
    "TwinReplayReport",
    "TwinShadowBundle",
    "build_twin_shadow_bundle",
    "decode_random_state",
    "encode_random_state",
    "feature_source_fingerprint",
    "replay_twin_shadow_bundle",
]
