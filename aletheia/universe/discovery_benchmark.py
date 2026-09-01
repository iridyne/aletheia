"""Frozen prospective measurement of Universe candidate-selection quality."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import math
import os
import random
import statistics
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from aletheia.analytics.filter_evidence import (
    _encode_candidate_facts,
    build_candidate_outcomes,
    decode_filter_decision_batch,
)
from aletheia.config.loader import load_config
from aletheia.storage import repositories as repo
from aletheia.storage.models import FilterDecisionBatch
from aletheia.universe.baseline_integrity import (
    BASELINE_INTEGRITY_CONTRACT_VERSION,
    audit_universe_baseline,
    family_semantics,
)
from aletheia.universe.outcome_economics import COST_MODEL_VERSION, estimate_outcome_economics
from aletheia.universe.scanners import UNIVERSE_SCORER_BEHAVIOR_VERSION

BENCHMARK_CONTRACT_VERSION = "universe-discovery-benchmark/v1"
ALLOCATOR_CONTRACT_VERSION = "production-universe-allocator-at-frozen-commit/v1"
OUTCOME_CONTRACT_VERSION = "filter-candidate-four-horizon/v1"
FAMILIES = ("momentum", "mean_reversion", "volatility", "derivatives", "event", "short_bias")
HORIZONS = ("15m", "1h", "4h", "24h")
CAPTURE_DAYS = 14
CADENCE_SEC = 4 * 60 * 60
MAX_BATCHES = CAPTURE_DAYS * 24 * 60 * 60 // CADENCE_SEC
MAX_CONTINUITY_GAP_SEC = 2 * CADENCE_SEC
FINALIZE_DELAY = timedelta(days=CAPTURE_DAYS + 1, hours=6)
MIN_BATCHES = 60
MIN_DATES = 10
MIN_INFERENCE_BATCHES = 40
MIN_OUTCOME_COVERAGE = 0.80
MIN_MATCH_COVERAGE = 0.70
BOOTSTRAP_DRAWS = 10_000

_HANDLER_VERSION = "universe-discovery-handler/v1"
_SLOT_KEY = "universe-discovery"
_VALID_DIRECTIONS = frozenset({"long", "short"})
_PERP_MARKETS = frozenset({"perp", "swap", "futures", "usdt_futures"})

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: str | datetime | None = None) -> datetime:
    if value is None:
        return _now()
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(
        str(value).replace("Z", "+00:00")
    )
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _market(value: Any) -> str:
    return "perp" if str(value or "spot").lower() in _PERP_MARKETS else "spot"


def _identity(item: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(item.get("venue") or "").strip().lower(),
        _market(item.get("market_type")),
        str(item.get("symbol") or "").strip().upper(),
        str(item.get("asset_id") or ""),
    )


def _direction_tradable(item: Mapping[str, Any], direction: Any) -> bool:
    side = str(direction or "").lower()
    return side in _VALID_DIRECTIONS and not (_market(item.get("market_type")) == "spot" and side == "short")


def _current_commit() -> str | None:
    from aletheia.agent_runtime.build_info import get_boot_commit
    from aletheia.agent_runtime.runtime_session import (
        current_external_gateway_session,
    )

    commit = get_boot_commit()
    if commit:
        return commit
    session = current_external_gateway_session()
    external_commit = str((session or {}).get("boot_commit") or "").strip()
    return external_commit or None


def _frozen_config(cfg) -> dict[str, Any]:
    return {
        "universe": cfg.universe.model_dump(mode="json"),
        "market_data": {
            "ccxt_exchange": cfg.market_data.ccxt_exchange,
            "ccxt_fallback": cfg.market_data.ccxt_fallback,
        },
        "exchange": {
            "enabled_markets": cfg.exchange.enabled_markets,
            "preferred_market_type": cfg.exchange.preferred_market_type,
        },
        "costs": {
            "execution_fee_pct": float(cfg.trading.execution_fee_pct),
            "slippage_bps": float(cfg.trading.slippage_bps),
            "fallback_spread_bps": float(cfg.universe.max_spread_bps),
        },
    }


def build_contract(*, code_commit: str | None = None, started_at: str | None = None, cfg=None) -> dict[str, Any]:
    """Build causal benchmark identity; runtime commit and interval are provenance."""
    del code_commit, started_at
    cfg = cfg or load_config()
    semantics = family_semantics()
    return {
        "contract_version": BENCHMARK_CONTRACT_VERSION,
        "versions": {
            "baseline_integrity": BASELINE_INTEGRITY_CONTRACT_VERSION,
            "scanner_behavior": UNIVERSE_SCORER_BEHAVIOR_VERSION,
            "allocator": ALLOCATOR_CONTRACT_VERSION,
            "outcome": OUTCOME_CONTRACT_VERSION,
            "cost_model": COST_MODEL_VERSION,
        },
        "population": "post-filter-post-spot-perp-dedup allocator input",
        "families": {family: semantics[family] for family in FAMILIES},
        "cohorts": {
            "production_selected": "allocator selected and execution eligible",
            "matched_non_selected": "exact venue/market/direction, tertile-stratified nearest neighbor without replacement",
            "family_top_k": list(FAMILIES),
            "simple_momentum": "abs(return_4h), spot positive-long only",
        },
        "horizons": list(HORIZONS),
        "primary_horizon": "4h",
        "capture": {
            "days": CAPTURE_DAYS,
            "cadence_sec": CADENCE_SEC,
            "max_batches": MAX_BATCHES,
            "minimum_batches": MIN_BATCHES,
            "minimum_utc_dates": MIN_DATES,
            "finalize_delay_sec": int(FINALIZE_DELAY.total_seconds()),
            "maximum_continuity_gap_sec": MAX_CONTINUITY_GAP_SEC,
            "early_stopping": False,
        },
        "statistics": {
            "bootstrap_draws": BOOTSTRAP_DRAWS,
            "minimum_complete_batches": MIN_INFERENCE_BATCHES,
            "minimum_outcome_coverage": MIN_OUTCOME_COVERAGE,
            "minimum_matching_coverage": MIN_MATCH_COVERAGE,
            "family_holm_comparisons": 21,
        },
        "conclusions": ["useful", "not_useful", "neutral", "unavailable"],
        "promotion_eligible": False,
        "config": _frozen_config(cfg),
    }


def _as_benchmark_window(window: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **dict(window),
        "id": str(window["observation_id"]),
        "contract_json": str(window["spec_json"]),
        "contract_hash": str(window["spec_hash"]),
        "code_commit": window.get("producer_commit"),
        "capture_ends_at": str(window["collect_until"]),
    }


def start_benchmark(*, now: str | datetime | None = None, code_commit: str | None = None, cfg=None) -> dict[str, Any]:
    from aletheia.observation_lab.kernel import invalidate_window, start_window

    cfg = cfg or load_config()
    if not cfg.universe.enabled:
        raise ValueError("universe_not_configured")
    commit = str(code_commit or _current_commit() or "").strip()
    if not commit:
        raise ValueError("runtime_boot_commit_unavailable")
    started = _utc(now)
    legacy = repo.get_universe_discovery_benchmark()
    window_id = hashlib.sha256(
        f"universe-discovery|{started.isoformat()}".encode("utf-8")
    ).hexdigest()[:24]
    window = start_window(
        observation_id=window_id,
        kind="universe_discovery",
        handler_version=_HANDLER_VERSION,
        slot_key=_SLOT_KEY,
        authority_mode="observe_only",
        spec=build_contract(cfg=cfg),
        started_at=started,
        collect_until=started + timedelta(days=CAPTURE_DAYS),
        finalize_not_before=started + FINALIZE_DELAY,
        producer_commit=commit,
    )
    if legacy is not None:
        try:
            invalidated = repo.invalidate_universe_discovery_benchmark(
                str(legacy["id"]),
                reason="superseded_by_observation_window_kernel",
                at=started.isoformat(),
            )
        except Exception:
            invalidate_window(window_id, int(window["generation"]), reason="legacy_cutover_failed")
            raise
        if not invalidated:
            invalidate_window(window_id, int(window["generation"]), reason="legacy_cutover_failed")
            raise RuntimeError("legacy_benchmark_cutover_failed")
    return _as_benchmark_window(window)


def abort_benchmark(window_id: str, *, reason: str, now: str | datetime | None = None) -> dict[str, Any]:
    from aletheia.observation_lab.kernel import abort_window

    del now
    status = status_benchmark(window_id)
    abort_window(window_id, int(status["generation"]), reason=reason)
    return status_benchmark(window_id)


def status_benchmark(window_id: str | None = None, *, now: str | datetime | None = None) -> dict[str, Any]:
    from aletheia.observation_lab.kernel import status_window

    raw = status_window(window_id, slot_key=_SLOT_KEY if window_id is None else None, now=now)
    window = _as_benchmark_window(raw)
    outcomes = repo.list_universe_discovery_outcomes(str(window["id"]))
    batches = repo.list_universe_discovery_batches(str(window["id"]))
    counts = Counter(str(row.get("status") or "unknown") for row in outcomes)
    dates = {str(row.get("evaluated_at") or "")[:10] for row in batches if row.get("evaluated_at")}
    return {
        **window,
        "batch_count": len(batches),
        "utc_date_count": len(dates),
        "outcome_counts": dict(sorted(counts.items())),
        "promotion_eligible": False,
    }


def _rank_values(
    rows: Sequence[Mapping[str, Any]], field: str, *, absolute: bool = False
) -> dict[tuple[str, str, str, str], float]:
    values = [
        (row, abs(value) if absolute else value)
        for row in rows
        if (value := _finite(row.get(field))) is not None
    ]
    present = sorted((value, _identity(row)) for row, value in values)
    denominator = max(1, len(present) - 1)
    ranks: dict[tuple[str, str, str, str], float] = {}
    start = 0
    while start < len(present):
        end = start + 1
        while end < len(present) and present[end][0] == present[start][0]:
            end += 1
        percentile = ((start + end - 1) / 2) / denominator
        for _, identity in present[start:end]:
            ranks[identity] = percentile
        start = end
    return ranks


def _tertile(value: float | None) -> int | None:
    if value is None:
        return None
    return min(2, int(value * 3.0))


def _cohort_memberships(
    population: list[dict[str, Any]],
    allocated: Sequence[Mapping[str, Any]],
    audits: dict[tuple[str, str, str, str], dict[str, Any]],
    *,
    max_candidates: int,
) -> tuple[
    dict[tuple[tuple[str, str, str, str], str], set[str]],
    dict[tuple[tuple[str, str, str, str], str], dict[str, Any]],
]:
    memberships: dict[tuple[tuple[str, str, str, str], str], set[str]] = defaultdict(set)
    annotations: dict[tuple[tuple[str, str, str, str], str], dict[str, Any]] = defaultdict(dict)
    by_identity = {_identity(item): item for item in population}
    allocated_identities = {_identity(item) for item in allocated}

    selected: list[tuple[tuple[str, str, str, str], str]] = []
    for rank, raw in enumerate(allocated, 1):
        identity = _identity(raw)
        item = by_identity.get(identity, dict(raw))
        direction = str(raw.get("scanner_direction") or raw.get("direction") or "").lower()
        note = annotations[(identity, direction)]
        note["production_rank"] = rank
        if (
            raw.get("execution_eligible", True)
            and (price := _finite(raw.get("last_price"))) is not None
            and price > 0
            and _direction_tradable(item, direction)
        ):
            selected.append((identity, direction))
            memberships[(identity, direction)].add("production_selected")
        else:
            note["production_unavailable_reason"] = "identity_price_direction_or_execution_unavailable"

    for family in FAMILIES:
        eligible: list[tuple[float, tuple[str, str, str, str], str]] = []
        for item in population:
            identity = _identity(item)
            evidence = audits[identity]["family_evidence"][family]
            direction = str(evidence.get("direction") or "").lower()
            price = _finite(item.get("last_price"))
            if evidence.get("applicability") == "applicable" and price is not None and price > 0 and _direction_tradable(item, direction):
                eligible.append((float(evidence["score"]), identity, direction))
        eligible.sort(key=lambda row: (-row[0], row[1]))
        for rank, (_, identity, direction) in enumerate(eligible[: min(max_candidates, len(eligible))], 1):
            label = f"family:{family}"
            memberships[(identity, direction)].add(label)
            annotations[(identity, direction)].setdefault("family_ranks", {})[family] = rank

    momentum: list[tuple[float, tuple[str, str, str, str], str]] = []
    for item in population:
        identity = _identity(item)
        ret = _finite(item.get("return_4h"))
        price = _finite(item.get("last_price"))
        if ret is None or ret == 0 or price is None or price <= 0:
            continue
        direction = "long" if ret > 0 else "short"
        if _direction_tradable(item, direction):
            momentum.append((abs(ret), identity, direction))
    momentum.sort(key=lambda row: (-row[0], row[1]))
    for rank, (_, identity, direction) in enumerate(momentum[: min(max_candidates, len(momentum))], 1):
        memberships[(identity, direction)].add("simple_momentum")
        annotations[(identity, direction)]["simple_momentum_rank"] = rank

    non_selected = [item for item in population if _identity(item) not in allocated_identities]
    rank_fields = ("quote_volume_24h", "spread_bps", "atr_pct", "return_4h")
    ranks = {
        field: _rank_values(population, field, absolute=field == "return_4h")
        for field in rank_fields
    }
    available = {_identity(item): item for item in non_selected}
    used: set[tuple[str, str, str, str]] = set()
    for selected_identity, direction in sorted(selected):
        selected_item = by_identity.get(selected_identity)
        if selected_item is None:
            continue
        selected_vector = [ranks[field].get(selected_identity) for field in rank_fields]
        selected_strata = tuple(_tertile(value) for value in selected_vector[:3])
        candidates: list[tuple[float, tuple[str, str, str, str]]] = []
        for identity, item in available.items():
            if identity in used or identity[:2] != selected_identity[:2]:
                continue
            candidate_direction = str(item.get("scanner_direction") or item.get("direction") or "").lower()
            candidate_price = _finite(item.get("last_price"))
            if (
                candidate_direction != direction
                or candidate_price is None
                or candidate_price <= 0
                or not _direction_tradable(item, candidate_direction)
            ):
                continue
            vector = [ranks[field].get(identity) for field in rank_fields]
            if tuple(_tertile(value) for value in vector[:3]) != selected_strata or any(value is None for value in selected_vector + vector):
                continue
            distance = sum(
                abs(a - b)
                for a, b in zip(selected_vector, vector)
                if a is not None and b is not None
            )
            candidates.append((distance, identity))
        if not candidates:
            annotations[(selected_identity, direction)]["match_unavailable"] = True
            continue
        _, control_identity = min(candidates, key=lambda row: (row[0], row[1]))
        used.add(control_identity)
        pair_id = hashlib.sha256(f"{selected_identity}|{control_identity}|{direction}".encode()).hexdigest()[:16]
        memberships[(control_identity, direction)].add("matched_non_selected")
        annotations[(selected_identity, direction)].update(
            matched_pair_id=pair_id, matched_pair_role="selected"
        )
        annotations[(control_identity, direction)].update(
            matched_pair_id=pair_id, matched_pair_role="control"
        )

    return memberships, annotations


def build_capture(
    *,
    window: Mapping[str, Any],
    scanner_run_id: str,
    population: Sequence[Mapping[str, Any]],
    allocated: Sequence[Mapping[str, Any]],
    entry_ts: str,
    regime_tag: str | None,
    producer_commit: str,
    cfg=None,
) -> tuple[FilterDecisionBatch, list[dict[str, Any]]]:
    """Build, but do not persist, one lossless benchmark batch."""
    cfg = cfg or load_config()
    normalized = [dict(item) for item in population]
    normalized.sort(key=_identity)
    audits = {_identity(item): audit_universe_baseline(item) for item in normalized}
    memberships, annotations = _cohort_memberships(
        normalized, allocated, audits, max_candidates=int(cfg.universe.max_candidates)
    )
    batch_id = hashlib.sha256(f"{window['id']}|{scanner_run_id}".encode()).hexdigest()[:12]
    facts: list[dict[str, Any]] = []
    for item in normalized:
        identity = _identity(item)
        item_regime_tag = regime_tag if (item.get("analysis_context") or {}).get("kind") == "crypto" else None
        facts.append(
            {
                "decision": "passed",
                "asset_id": item.get("asset_id"),
                "feature_snapshot_id": item.get("feature_snapshot_id"),
                "venue": item.get("venue"),
                "symbol": item.get("symbol"),
                "market_type": _market(item.get("market_type")),
                "entry_price_usd": item.get("last_price"),
                "quote_volume_24h": item.get("quote_volume_24h"),
                "spread_bps": item.get("spread_bps"),
                "depth_10bps_usd": item.get("depth_10bps_usd"),
                "atr_pct": item.get("atr_pct"),
                "return_4h": item.get("return_4h"),
                "return_24h": item.get("return_24h"),
                "funding_rate": item.get("funding_rate"),
                "data_freshness_sec": item.get("data_freshness_sec"),
                "regime_tag": item_regime_tag,
                "production_scanner_family": item.get("scanner_family"),
                "production_scanner_direction": item.get("scanner_direction") or item.get("direction"),
                "production_total_score": item.get("total_score"),
                "production_execution_eligible": item.get("execution_eligible", True),
                "production_execution_ineligible_reason": item.get("execution_ineligible_reason"),
                "authority": audits[identity]["authority"],
                "family_evidence": audits[identity]["family_evidence"],
                "cohorts": sorted(
                    {
                        label
                        for (member_identity, _), labels in memberships.items()
                        if member_identity == identity
                        for label in labels
                    }
                ),
                "direction_annotations": {
                    direction: note
                    for (member_identity, direction), note in sorted(annotations.items())
                    if member_identity == identity
                },
            }
        )
    contract_hash = str(window["contract_hash"])
    parameter_snapshot = _json({"contract_hash": contract_hash, "scanner_run_id": scanner_run_id})
    dedup_key = hashlib.sha256(f"{window['id']}|{scanner_run_id}|{contract_hash}".encode()).hexdigest()
    batch = FilterDecisionBatch(
        id=batch_id,
        dedup_key=dedup_key,
        run_id=scanner_run_id,
        candidate_kind="universe_discovery_population",
        filter_name="universe_discovery_benchmark",
        stage="post_dedup_pre_allocation",
        candidate_count=len(facts),
        passed_count=len(facts),
        candidate_facts=_encode_candidate_facts(facts),
        parameter_snapshot=parameter_snapshot,
        filter_version=BENCHMARK_CONTRACT_VERSION,
        code_commit=producer_commit,
        evaluated_at=_utc(entry_ts).isoformat(),
        benchmark_window_id=str(window["id"]),
        benchmark_batch_id=batch_id,
    )

    by_identity = {_identity(item): item for item in normalized}
    cost_snapshot = _json(_frozen_config(cfg)["costs"])
    try:
        from aletheia.agent_runtime.runtime_session import current_provenance

        provenance = current_provenance()
    except Exception:
        provenance = {}
    outcome_rows: list[dict[str, Any]] = []
    for (identity, direction), labels in sorted(memberships.items()):
        item = by_identity[identity]
        item_regime_tag = regime_tag if (item.get("analysis_context") or {}).get("kind") == "crypto" else None
        key = (
            "universe-discovery:"
            + hashlib.sha256(f"{window['id']}|{batch_id}|{identity}|{direction}".encode()).hexdigest()[:24]
        )
        rows = build_candidate_outcomes(
            outcome_candidate_key=key,
            venue=identity[0],
            symbol=str(item.get("symbol")),
            market_type=identity[1],
            direction=direction,
            scanner_family=None,
            regime_tag=item_regime_tag,
            entry_price_usd=float(item["last_price"]),
            entry_spread_bps=_finite(item.get("spread_bps")),
            funding_rate_at_entry=_finite(item.get("funding_rate")),
            entry_ts=_utc(entry_ts).isoformat(),
            allocation_selected="production_selected" in labels,
            horizons=HORIZONS,
            rsi_1h_at_entry=_finite(item.get("rsi_14")),
            rsi_15m_at_entry=_finite(item.get("rsi_15m")),
            return_24h_at_entry=_finite(item.get("return_24h")),
            direction_exhaustion_signal=item.get("direction_exhaustion_signal"),
        )
        path_facts = {
            "asset_id": item.get("asset_id"),
            "feature_snapshot_id": item.get("feature_snapshot_id"),
            "scanner_run_id": scanner_run_id,
            **annotations.get((identity, direction), {}),
        }
        for row in rows:
            data = row.model_dump()
            data.update(
                {
                    "benchmark_window_id": str(window["id"]),
                    "benchmark_batch_id": batch_id,
                    "benchmark_cohorts": _json(sorted(labels)),
                    "benchmark_cost_snapshot": cost_snapshot,
                    "benchmark_path_facts": _json(path_facts),
                    "producer_runtime_session_id": provenance.get("runtime_session_id"),
                    "producer_commit": producer_commit,
                    "producer_behavior_fingerprint": contract_hash,
                }
            )
            outcome_rows.append(data)
    return batch, outcome_rows


def capture_completed_run(
    *,
    scanner_run_id: str,
    population: Sequence[Mapping[str, Any]],
    allocated: Sequence[Mapping[str, Any]],
    entry_ts: str,
    regime_tag: str | None = None,
    cfg=None,
) -> dict[str, Any]:
    """Capture the published run, then append its immutable Lab unit identities."""
    from aletheia.observation_lab.kernel import capture_event, invalidate_window

    try:
        window = status_benchmark()
    except KeyError:
        return {"accepted": False, "reason": "no_active_window"}
    cfg = cfg or load_config()
    current = build_contract(cfg=cfg)
    if _hash(current) != str(window["contract_hash"]):
        invalidate_window(
            str(window["id"]), int(window["generation"]), reason="frozen_contract_changed"
        )
        return {"accepted": False, "reason": "frozen_contract_changed", "invalidated": True}
    batch_id = hashlib.sha256(f"{window['id']}|{scanner_run_id}".encode()).hexdigest()[:12]
    persisted_batch = next(
        (
            row for row in repo.list_universe_discovery_batches(str(window["id"]))
            if str(row["benchmark_batch_id"]) == batch_id
        ),
        None,
    )
    if persisted_batch is None:
        producer_commit = _current_commit()
        if producer_commit is None:
            return {"accepted": False, "reason": "producer_provenance_unavailable"}
        batch, outcomes = build_capture(
            window=window, scanner_run_id=scanner_run_id, population=population,
            allocated=allocated, entry_ts=entry_ts, regime_tag=regime_tag,
            producer_commit=producer_commit, cfg=cfg,
        )
    else:
        batch, outcomes = persisted_batch, []
    result = repo.capture_universe_discovery_batch(
        str(window["id"]), batch, outcomes, captured_at=entry_ts,
        cadence_sec=CADENCE_SEC, max_batches=MAX_BATCHES, window_snapshot=window,
    )
    if result.get("accepted"):
        persisted_commit = result.pop("_producer_commit", None)
        persisted_session = result.pop("_producer_runtime_session_id", None)
        persisted_batch = next(
            (
                row for row in repo.list_universe_discovery_batches(str(window["id"]))
                if str(row["benchmark_batch_id"]) == str(result["batch_id"])
            ),
            None,
        )
        if persisted_batch is None:
            raise RuntimeError("persisted_benchmark_batch_unavailable")
        persisted_population = decode_filter_decision_batch(str(persisted_batch["candidate_facts"]))
        if len(persisted_population) != int(persisted_batch["candidate_count"]):
            raise RuntimeError("persisted_benchmark_batch_population_mismatch")
        persisted_sessions = {
            str(row["producer_runtime_session_id"])
            for row in repo.list_universe_discovery_outcomes(str(window["id"]))
            if str(row["benchmark_batch_id"]) == str(result["batch_id"])
            and row.get("producer_runtime_session_id")
        }
        if result.get("idempotent"):
            from aletheia.observation_lab.db import get_lab_db

            counts = dict(get_lab_db().execute(
                "SELECT source_kind,COUNT(*) FROM observation_units "
                "WHERE observation_id=? AND generation=? AND spec_hash=? "
                "AND unit_key LIKE ? AND admission='admitted' GROUP BY source_kind",
                (str(window["id"]), int(window["generation"]), str(window["contract_hash"]),
                 f"{result['batch_id']}:%"),
            ).fetchall())
            candidate_count = int(counts.get("universe_candidate", 0))
            expected_count = int(persisted_batch["candidate_count"])
            has_marker = int(counts.get("universe_batch", 0)) == 1
            if candidate_count == expected_count and (expected_count > 0 or has_marker):
                return result
            if candidate_count:
                raise RuntimeError("partial_benchmark_batch_lab_admission")
        capture_event(
            str(window["id"]), int(window["generation"]), str(window["contract_hash"]),
            {"batch_id": result["batch_id"], "known_at": persisted_batch["evaluated_at"],
             "batch_source_hash": _hash(persisted_population),
             "population": persisted_population},
            producer_commit=str(persisted_commit or persisted_batch["code_commit"]),
            producer_runtime_session_id=(
                str(persisted_session) if persisted_session else (
                    next(iter(persisted_sessions)) if len(persisted_sessions) == 1 else None
                )
            ),
        )
    return result


def _percentile(values: Sequence[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * p
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def _summary(values: list[float], intended: int, statuses: Counter[str]) -> dict[str, Any]:
    losses = sorted(values)[: max(1, math.ceil(len(values) * 0.10))] if values else []
    return {
        "intended": intended,
        "settled": statuses.get("settled", 0),
        "failed": statuses.get("failed", 0),
        "pending": statuses.get("pending", 0),
        "coverage": len(values) / intended if intended else None,
        "mean_net_return_pct": statistics.fmean(values) if values else None,
        "median_net_return_pct": statistics.median(values) if values else None,
        "win_rate": sum(value > 0 for value in values) / len(values) if values else None,
        "p10_net_return_pct": _percentile(values, 0.10),
        "cvar10_net_return_pct": statistics.fmean(losses) if losses else None,
    }


def _seed(contract_hash: str, comparison: str) -> int:
    return int(hashlib.sha256(f"{contract_hash}|{comparison}".encode()).hexdigest()[:16], 16)


def _paired_inference(
    differences: Mapping[str, float | Sequence[float]],
    *,
    contract_hash: str,
    comparison: str,
) -> dict[str, Any]:
    clusters = [
        [float(value)] if isinstance(value, (int, float)) else [float(item) for item in value]
        for _, value in sorted(differences.items())
    ]
    clusters = [cluster for cluster in clusters if cluster]
    if len(clusters) < MIN_INFERENCE_BATCHES:
        return {"available": False, "reason": "insufficient_complete_batches", "complete_batches": len(clusters)}
    rng = random.Random(_seed(contract_hash, comparison))
    values = [value for cluster in clusters for value in cluster]
    draws = [
        statistics.fmean(value for cluster in rng.choices(clusters, k=len(clusters)) for value in cluster)
        for _ in range(BOOTSTRAP_DRAWS)
    ]
    observed = statistics.fmean(values)
    extreme = 0
    for _ in range(BOOTSTRAP_DRAWS):
        permuted = [
            value * sign
            for cluster in clusters
            for sign in (1 if rng.getrandbits(1) else -1,)
            for value in cluster
        ]
        if abs(statistics.fmean(permuted)) >= abs(observed):
            extreme += 1
    return {
        "available": True,
        "complete_batches": len(clusters),
        "complete_pairs": len(values),
        "mean_difference_pct": observed,
        "ci95": [_percentile(draws, 0.025), _percentile(draws, 0.975)],
        "p_value": (extreme + 1) / (BOOTSTRAP_DRAWS + 1),
    }


def _holm(results: dict[str, dict[str, Any]]) -> None:
    """Apply the preregistered Holm correction across all 21 comparisons."""
    available = sorted(
        ((float(value["p_value"]), key) for key, value in results.items() if value.get("available")),
        key=lambda row: (row[0], row[1]),
    )
    running = 0.0
    total = len(results)
    for value in results.values():
        value["holm_family_size"] = total
        value["holm_adjusted_p"] = None
    for index, (p_value, key) in enumerate(available):
        running = max(running, min(1.0, p_value * (total - index)))
        results[key]["holm_adjusted_p"] = running

def analyze_benchmark(
    window_id: str,
    *,
    snapshot: tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    if snapshot is None:
        window = status_benchmark(window_id)
        batches = repo.list_universe_discovery_batches(window_id)
        rows = repo.list_universe_discovery_outcomes(window_id)
    else:
        window, batches, rows = snapshot
    if window is None:
        raise KeyError(window_id)
    contract = json.loads(str(window["contract_json"]))
    costs = contract["config"]["costs"]
    measurements: list[dict[str, Any]] = []
    for row in rows:
        value = dict(row)
        net = None
        economics: dict[str, Any] | None = None
        if value.get("status") == "settled" and _finite(value.get("exit_price_usd")) not in (None, 0.0):
            economics = estimate_outcome_economics(
                entry_price=float(value["entry_price_usd"]),
                exit_price=float(value["exit_price_usd"]),
                direction=value.get("direction"),
                market_type=value.get("market_type"),
                hold_seconds=max(0.0, (_utc(value["due_at"]) - _utc(value["entry_ts"])).total_seconds()),
                entry_spread_bps=_finite(value.get("entry_spread_bps")),
                fallback_spread_bps=float(costs["fallback_spread_bps"]),
                funding_rate_at_entry=_finite(value.get("funding_rate_at_entry")),
                execution_fee_pct=float(costs["execution_fee_pct"]),
                slippage_bps=float(costs["slippage_bps"]),
                price_quality_ok=bool(value.get("settlement_price_source")),
            )
            net = economics["net_return_pct"]
        measurements.append({
            **value,
            "cohorts": json.loads(value.get("benchmark_cohorts") or "[]"),
            "path": json.loads(value.get("benchmark_path_facts") or "{}"),
            "frozen_net_return_pct": net,
            "frozen_cost_components": {
                field: _finite(economics.get(field)) if economics else None
                for field in ("estimated_fee_pct", "estimated_spread_slippage_pct", "estimated_funding_pct")
            },
        })

    cohort_rows: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in measurements:
        for cohort in row["cohorts"]:
            cohort_rows[(cohort, str(row["horizon"]))].append(row)
    summaries: dict[str, dict[str, Any]] = {}
    for (cohort, horizon), cohort_items in sorted(cohort_rows.items()):
        values = [float(row["frozen_net_return_pct"]) for row in cohort_items if row["frozen_net_return_pct"] is not None]
        summary = _summary(values, len(cohort_items), Counter(str(row["status"]) for row in cohort_items))
        summary["mean_mfe_pct"] = statistics.fmean(mfe) if (mfe := [float(row["mfe_pct"]) for row in cohort_items if _finite(row.get("mfe_pct")) is not None]) else None
        summary["mean_mae_pct"] = statistics.fmean(mae) if (mae := [float(row["mae_pct"]) for row in cohort_items if _finite(row.get("mae_pct")) is not None]) else None
        summaries.setdefault(cohort, {})[horizon] = summary

    paired_by_batch: dict[str, list[float]] = defaultdict(list)
    pairs: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)
    for row in measurements:
        if row["horizon"] != "4h" or row["frozen_net_return_pct"] is None:
            continue
        pair_id = row["path"].get("matched_pair_id")
        role = row["path"].get("matched_pair_role")
        if pair_id and role in {"selected", "control"}:
            pairs[(str(row["benchmark_batch_id"]), str(pair_id))][str(role)] = float(row["frozen_net_return_pct"])
    for (batch_id, _), pair in pairs.items():
        if pair.keys() >= {"selected", "control"}:
            paired_by_batch[batch_id].append(pair["selected"] - pair["control"])
    selected_control = _paired_inference(
        paired_by_batch,
        contract_hash=str(window["contract_hash"]), comparison="selected_vs_matched",
    )

    family_labels = [f"family:{family}" for family in FAMILIES] + ["simple_momentum"]
    batch_cohort_values: dict[tuple[str, str], list[float]] = defaultdict(list)
    batch_cohort_intended: Counter[tuple[str, str]] = Counter()
    for row in measurements:
        if row["horizon"] != "4h":
            continue
        for cohort in row["cohorts"]:
            if cohort in family_labels:
                key = (str(row["benchmark_batch_id"]), cohort)
                batch_cohort_intended[key] += 1
                if row["frozen_net_return_pct"] is not None:
                    batch_cohort_values[key].append(float(row["frozen_net_return_pct"]))
    comparisons: dict[str, dict[str, Any]] = {}
    for left_index, left in enumerate(family_labels):
        for right in family_labels[left_index + 1:]:
            differences = {
                batch_id: statistics.fmean(batch_cohort_values[(batch_id, left)])
                - statistics.fmean(batch_cohort_values[(batch_id, right)])
                for batch_id in {key[0] for key in batch_cohort_intended}
                if batch_cohort_intended[(batch_id, left)] > 0
                and batch_cohort_intended[(batch_id, right)] > 0
                and len(batch_cohort_values[(batch_id, left)]) == batch_cohort_intended[(batch_id, left)]
                and len(batch_cohort_values[(batch_id, right)]) == batch_cohort_intended[(batch_id, right)]
            }
            key = f"{left}_vs_{right}"
            comparisons[key] = _paired_inference(differences, contract_hash=str(window["contract_hash"]), comparison=key)
    _holm(comparisons)

    batch_times = sorted(
        _utc(str(batch["evaluated_at"]))
        for batch in batches
        if batch.get("evaluated_at")
    )
    continuity_points = [_utc(window["started_at"]), *batch_times, _utc(window["capture_ends_at"])]
    continuity_gaps = [
        max(0.0, (right - left).total_seconds())
        for left, right in zip(continuity_points, continuity_points[1:])
    ]
    max_continuity_gap_sec = max(continuity_gaps, default=0.0)
    batch_dates = {timestamp.date().isoformat() for timestamp in batch_times}
    selected_4h = summaries.get("production_selected", {}).get("4h", {})
    matched_4h = summaries.get("matched_non_selected", {}).get("4h", {})
    intended_selected = int(selected_4h.get("intended") or 0)
    matched_intended = int(matched_4h.get("intended") or 0)
    integrity_reasons: list[str] = []
    if len(batches) < MIN_BATCHES:
        integrity_reasons.append("insufficient_capture_batches")
    if len(batch_dates) < MIN_DATES:
        integrity_reasons.append("insufficient_utc_dates")
    if max_continuity_gap_sec > MAX_CONTINUITY_GAP_SEC:
        integrity_reasons.append("material_capture_continuity_gap")
    if (selected_4h.get("coverage") or 0.0) < MIN_OUTCOME_COVERAGE:
        integrity_reasons.append("insufficient_selected_outcome_coverage")
    if (matched_4h.get("coverage") or 0.0) < MIN_OUTCOME_COVERAGE:
        integrity_reasons.append("insufficient_matched_outcome_coverage")
    if intended_selected == 0 or matched_intended / intended_selected < MIN_MATCH_COVERAGE:
        integrity_reasons.append("insufficient_matching_coverage")
    if not selected_control.get("available"):
        integrity_reasons.append(str(selected_control.get("reason") or "selected_control_inference_unavailable"))

    selected_ci = None
    selected_batches = {
        batch_id: values
        for batch_id in {str(row["benchmark_batch_id"]) for row in measurements}
        if (
            values := [
                float(row["frozen_net_return_pct"])
                for row in measurements
                if row["horizon"] == "4h"
                and row["benchmark_batch_id"] == batch_id
                and "production_selected" in row["cohorts"]
                and row["frozen_net_return_pct"] is not None
            ]
        )
    }
    if len(selected_batches) >= MIN_INFERENCE_BATCHES:
        selected_inference = _paired_inference(selected_batches, contract_hash=str(window["contract_hash"]), comparison="selected_vs_zero")
        selected_ci = selected_inference.get("ci95")
    if integrity_reasons:
        conclusion = "unavailable"
    else:
        lift_ci = selected_control["ci95"]
        selected_mean = selected_4h.get("mean_net_return_pct")
        if lift_ci[0] > 0 and selected_control["p_value"] < 0.05 and selected_mean is not None and selected_mean > 0:
            conclusion = "useful"
        elif lift_ci[1] < 0 or (selected_ci and selected_ci[1] < 0):
            conclusion = "not_useful"
        else:
            conclusion = "neutral"

    family_means = {label: summaries.get(label, {}).get("4h", {}).get("mean_net_return_pct") for label in family_labels}
    supported = None
    if not integrity_reasons:
        for candidate, mean in family_means.items():
            if mean is None or mean <= 0:
                continue
            wins = []
            for other in family_labels:
                if other == candidate:
                    continue
                direct = comparisons.get(f"{candidate}_vs_{other}")
                inverse = comparisons.get(f"{other}_vs_{candidate}")
                if direct:
                    wins.append(bool(direct.get("available") and direct.get("ci95", [0])[0] > 0 and direct.get("holm_adjusted_p", 1) < 0.05))
                elif inverse:
                    wins.append(bool(inverse.get("available") and inverse.get("ci95", [0, 0])[1] < 0 and inverse.get("holm_adjusted_p", 1) < 0.05))
            if wins and all(wins):
                supported = candidate
                break

    symbols = Counter(str(row["symbol"]) for row in measurements if row["horizon"] == "4h")
    dates = Counter(str(row["entry_ts"])[:10] for row in measurements if row["horizon"] == "4h")
    def grouped_4h(field: str) -> dict[str, dict[str, dict[str, Any]]]:
        groups: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
        for row in measurements:
            if row["horizon"] == "4h":
                for cohort in row["cohorts"]:
                    groups[str(cohort)][str(row.get(field) or "unavailable")].append(row)
        return {
            cohort: {
                key: _summary(
                    [float(row["frozen_net_return_pct"]) for row in items if row["frozen_net_return_pct"] is not None],
                    len(items),
                    Counter(str(row["status"]) for row in items),
                )
                for key, items in sorted(strata.items())
            }
            for cohort, strata in sorted(groups.items())
        }

    selected_settled_4h = [
        row for row in measurements
        if row["horizon"] == "4h"
        and "production_selected" in row["cohorts"]
        and row["frozen_net_return_pct"] is not None
    ]
    cost_components = {
        field: (statistics.fmean(values) if values else None)
        for field in ("estimated_fee_pct", "estimated_spread_slippage_pct", "estimated_funding_pct")
        if (values := [
            float(value)
            for row in selected_settled_4h
            if (value := row["frozen_cost_components"].get(field)) is not None
        ]) is not None
    }
    source_counts = Counter(str(row.get("settlement_price_source") or "unavailable") for row in measurements)
    settlement_lags = [value for row in measurements if (value := _finite(row.get("settlement_lag_sec"))) is not None]
    settlement_report = {
        "status_counts": dict(sorted(Counter(str(row["status"]) for row in measurements).items())),
        "price_source_counts": dict(sorted(source_counts.items())),
        "lag_sec": {
            "count": len(settlement_lags),
            "p50": _percentile(settlement_lags, 0.50),
            "p95": _percentile(settlement_lags, 0.95),
            "max": max(settlement_lags) if settlement_lags else None,
        },
    }

    def concentration(counts: Counter[str]) -> dict[str, Any]:
        total = sum(counts.values())
        shares = [count / total for count in counts.values()] if total else []
        return {"unique": len(counts), "max_share": max(shares) if shares else None, "hhi": sum(share * share for share in shares)}

    return {
        "contract_hash": window["contract_hash"],
        "batch_count": len(batches),
        "utc_date_count": len(batch_dates),
        "capture_continuity": {
            "maximum_allowed_gap_sec": MAX_CONTINUITY_GAP_SEC,
            "maximum_observed_gap_sec": max_continuity_gap_sec,
            "material_gap_detected": max_continuity_gap_sec > MAX_CONTINUITY_GAP_SEC,
        },
        "cohort_horizon_summary": summaries,
        "stratified_4h_summary": {
            "direction": grouped_4h("direction"),
            "market_type": grouped_4h("market_type"),
            "regime_tag": grouped_4h("regime_tag"),
        },
        "settlement": settlement_report,
        "costs": {"assumptions": costs, "selected_4h_mean_components": cost_components},
        "selected_vs_matched": selected_control,
        "family_comparisons": comparisons,
        "family_4h_means": family_means,
        "family_winner": {"status": "supported_best", "cohort": supported} if supported else {"status": "no_supported_winner"},
        "concentration": {"symbol": concentration(symbols), "utc_date": concentration(dates)},
        "integrity": {"complete": not integrity_reasons, "reasons": sorted(set(integrity_reasons))},
        "conclusion": conclusion,
        "promotion_eligible": False,
    }


def _handler_capture(window: dict[str, Any], event: dict[str, Any]):
    from aletheia.observation_lab.kernel import ObservationProgress

    batch_id = str(event["batch_id"])
    units = [{
        "unit_key": f"{batch_id}:__batch__",
        "source_kind": "universe_batch",
        "source_ref": f"filter_decision_batch:{batch_id}",
        "source_hash": event["batch_source_hash"],
        "known_at": event["known_at"],
        "admission": "admitted",
        "reason": "complete_batch_capture",
    }]
    for item in event["population"]:
        identity = "|".join(_identity(item))
        units.append({
            "unit_key": f"{batch_id}:{identity}",
            "source_kind": "universe_candidate",
            "source_ref": f"filter_decision_batch:{batch_id}:{identity}",
            "source_hash": _hash(dict(item)),
            "known_at": event["known_at"],
            "admission": "admitted",
            "reason": "complete_post_dedup_pre_allocation_cohort",
        })
    return ObservationProgress(
        summary={"batch_id": batch_id, "population_size": len(event["population"])},
        units=tuple(units),
    )

def _admitted_snapshot(window: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Return only app evidence whose complete cohort was admitted by lab.db."""
    from aletheia.observation_lab.db import get_lab_db

    window_id = str(window["observation_id"] if "observation_id" in window else window["id"])
    generation = int(window["generation"])
    spec_hash = str(window["spec_hash"] if "spec_hash" in window else window["contract_hash"])
    rows = get_lab_db().execute(
        "SELECT unit_key,source_kind FROM observation_units "
        "WHERE observation_id=? AND generation=? AND spec_hash=? "
        "AND source_kind IN ('universe_candidate','universe_batch') AND admission='admitted'",
        (window_id, generation, spec_hash),
    ).fetchall()
    unit_counts = Counter(
        str(row["unit_key"]).split(":", 1)[0]
        for row in rows if row["source_kind"] == "universe_candidate"
    )
    batch_markers = {
        str(row["unit_key"]).split(":", 1)[0]
        for row in rows if row["source_kind"] == "universe_batch"
    }
    all_batches = repo.list_universe_discovery_batches(window_id)
    batches = [
        batch for batch in all_batches
        if unit_counts[str(batch["benchmark_batch_id"])] == int(batch["candidate_count"])
        and (
            str(batch["benchmark_batch_id"]) in batch_markers
            or int(batch["candidate_count"]) > 0
        )
    ]
    admitted_ids = {str(batch["benchmark_batch_id"]) for batch in batches}
    outcomes = [
        row for row in repo.list_universe_discovery_outcomes(window_id)
        if str(row["benchmark_batch_id"]) in admitted_ids
    ]
    return batches, outcomes, len(all_batches) - len(batches)



def _handler_advance(window: dict[str, Any]):
    from aletheia.observation_lab.kernel import ObservationProgress

    benchmark_window = _as_benchmark_window(window)
    batches, outcomes, orphan_batch_count = _admitted_snapshot(window)
    finalize_boundary = _utc(str(benchmark_window["finalize_not_before"]))
    outcome_contract_closed = all(
        row.get("due_at") and _utc(str(row["due_at"])) <= finalize_boundary
        for row in outcomes
    )
    return ObservationProgress(
        summary={
            "batch_count": len(batches),
            "orphan_batch_count": orphan_batch_count,
            "outcome_counts": dict(sorted(Counter(str(row["status"]) for row in outcomes).items())),
            "outcome_contract_closed": outcome_contract_closed,
        },
        can_finalize=outcome_contract_closed,
    )


def _handler_finalize(window: dict[str, Any]) -> dict[str, Any]:
    benchmark_window = _as_benchmark_window(window)
    window_id = str(benchmark_window["id"])
    batches, outcomes, orphan_batch_count = _admitted_snapshot(window)
    analysis = analyze_benchmark(window_id, snapshot=(benchmark_window, batches, outcomes))
    if orphan_batch_count:
        analysis["integrity"]["complete"] = False
        analysis["integrity"]["reasons"] = sorted(
            {*analysis["integrity"]["reasons"], "orphan_app_batch"}
        )
        analysis["conclusion"] = "unavailable"
        analysis["family_winner"] = {"status": "no_supported_winner"}
    runtime_session_ids = sorted({
        str(row[field])
        for row in outcomes
        for field in ("producer_runtime_session_id", "settlement_runtime_session_id")
        if row.get(field)
    })
    commits = sorted({
        str(value)
        for value in [
            benchmark_window.get("producer_commit"),
            *(row.get("producer_commit") for row in outcomes),
            *(row.get("settlement_producer_commit") for row in outcomes),
        ]
        if value
    })
    return {
        "manifest": {
            "format": BENCHMARK_CONTRACT_VERSION,
            "window_id": window_id,
            "contract_hash": benchmark_window["contract_hash"],
            "producer_commits": commits,
            "started_at": benchmark_window["started_at"],
            "capture_ends_at": benchmark_window["capture_ends_at"],
            "promotion_eligible": False,
            "orphan_batch_count": orphan_batch_count,
            "runtime_session_ids": runtime_session_ids,
        },
        "contract": json.loads(str(benchmark_window["contract_json"])),
        "population_batches": [
            {**batch, "candidate_facts": decode_filter_decision_batch(str(batch["candidate_facts"]))}
            for batch in batches
        ],
        "outcomes": outcomes,
        "analysis": analysis,
    }
def _retention_claims(window: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    from aletheia.observation_lab.db import get_lab_db

    row = get_lab_db().execute(
        "SELECT MIN(known_at) FROM observation_units WHERE observation_id=? AND generation=?",
        (window["observation_id"], window["generation"]),
    ).fetchone()
    earliest = str(row[0]) if row and row[0] else str(window["started_at"])
    return (
        {"domain": "filter_decision_batches", "earliest_at": earliest},
        {"domain": "filter_candidate_outcomes", "earliest_at": earliest},
    )



def register_observation_handler() -> None:
    from aletheia.observation_lab.kernel import (
        ObservationHandler,
        register_handler,
        registered_handlers,
    )

    key = ("universe_discovery", _HANDLER_VERSION)
    if key not in registered_handlers():
        register_handler(ObservationHandler(
            kind=key[0],
            version=key[1],
            normalize_spec=lambda spec: dict(spec),
            capture=_handler_capture,
            advance=_handler_advance,
            finalize=_handler_finalize,
            retention_claims=_retention_claims,
        ))


def finalize_benchmark(window_id: str, output: str | Path, *, now: str | datetime | None = None) -> dict[str, Any]:
    from aletheia.observation_lab.kernel import finalize_window, invalidate_window

    window = status_benchmark(window_id, now=now)
    if _hash(build_contract()) != str(window["contract_hash"]):
        invalidate_window(window_id, int(window["generation"]), reason="frozen_contract_drift_at_finalization")
        raise ValueError("benchmark_frozen_contract_drift")
    try:
        finalized = finalize_window(
            window_id, int(window["generation"]), now=now,
        )
    except ValueError as exc:
        if str(exc) == "window_finalize_not_due":
            raise ValueError("benchmark_finalize_not_due") from exc
        raise
    canonical = Path(str(finalized["artifact_path"])).expanduser().resolve()
    artifact = canonical.read_bytes()
    digest = hashlib.sha256(artifact).hexdigest()
    if digest != str(finalized["artifact_sha256"]):
        raise RuntimeError("benchmark_artifact_hash_mismatch")
    requested = Path(output).expanduser().resolve()
    if requested != canonical:
        requested.parent.mkdir(parents=True, exist_ok=True)
        requested_artifact = artifact if requested.suffix == ".gz" else gzip.decompress(artifact)
        requested_digest = hashlib.sha256(requested_artifact).hexdigest()
        staged = requested.with_name(f".{requested.name}.{uuid.uuid4().hex}.tmp")
        try:
            staged.write_bytes(requested_artifact)
            if hashlib.sha256(staged.read_bytes()).hexdigest() != requested_digest:
                raise RuntimeError("benchmark_artifact_copy_verification_failed")
            if requested.exists():
                if hashlib.sha256(requested.read_bytes()).hexdigest() != requested_digest:
                    raise FileExistsError(requested)
                staged.unlink(missing_ok=True)
            else:
                os.replace(staged, requested)
        except Exception:
            staged.unlink(missing_ok=True)
            raise
    package = json.loads(gzip.decompress(artifact))
    return {
        **finalized,
        "analysis": package["domain_result"]["analysis"],
    }


register_observation_handler()

__all__ = [
    "BENCHMARK_CONTRACT_VERSION",
    "abort_benchmark",
    "analyze_benchmark",
    "build_capture",
    "build_contract",
    "capture_completed_run",
    "finalize_benchmark",
    "start_benchmark",
    "register_observation_handler",
    "status_benchmark",
]
