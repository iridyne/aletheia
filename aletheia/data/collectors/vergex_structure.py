"""Observation-only Vergex perp structure collector.

This module intentionally does not call a real Vergex endpoint yet. It provides
the safe first write path for partner/fixture payloads: accept only aggregate
whitelisted fields, drop address/account/prompt/credential-shaped data, and
return PerpStructureSnapshot models for repository persistence by the caller.
"""

from __future__ import annotations

import json
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from aletheia.data.models import PerpStructureSnapshot
from aletheia.data.position_x import compute_position_x_batch, extract_position_x_snapshot


_ALLOWED_FIELDS = {
    "symbol",
    "venue",
    "source",
    "as_of_time",
    "observed_at",
    "data_quality",
    "coverage_note",
    "reference_price",
    "poc",
    "value_area_low",
    "value_area_high",
    "support",
    "resistance",
    "magnet",
    "short_liq_above_usd",
    "long_liq_below_usd",
    "long_unrealized_pnl_usd",
    "short_unrealized_pnl_usd",
    "top10_concentration",
    "top50_concentration",
    "holder_count",
    "long_holder_count",
    "short_holder_count",
    "cgo_direction",
    "vap_direction",
    "lfa_direction",
    "cascade_risk",
    "convexity",
    "vendor_bias",
    "vendor_rank",
    "vendor_universe_size",
    "structure_bias",
    "composite_z",
    "structure_rank",
    "universe_size",
    "dimension_scores_json",
    "risk_context_json",
}

_DIRECTION_VALUES = {"bullish", "bearish", "neutral", "conflict"}
_QUALITY_VALUES = {"complete", "partial", "degraded", "unknown"}
_FORBIDDEN_KEY_FRAGMENTS = (
    "api_key", "token", "credential", "balance", "order", "wallet",
    "address", "proxy", "secret", "private_key",
)


class VergexStructureCollector:
    """Collect whitelisted aggregate perp structure rows from injected fixtures."""

    domain = "perp_structure"

    def __init__(self, fixture_payloads: list[dict[str, Any]] | None = None):
        self._fixture_payloads = fixture_payloads or []

    async def collect(self, symbols: list[str]) -> dict[str, Any]:
        """Return whitelisted perp structure snapshots for requested symbols.

        The real partner endpoint is deliberately not implemented in this step.
        An empty fixture set is an honest no-source skip, not an error.
        """
        if not self._fixture_payloads:
            return {"perp_structure": [], "errors": ["no_source"]}

        wanted = set(symbols)
        rows: list[PerpStructureSnapshot] = []
        errors: list[str] = []
        for payload in self._fixture_payloads:
            if payload.get("symbol") not in wanted:
                continue
            try:
                rows.append(_snapshot_from_payload(payload))
            except Exception as e:
                errors.append(f"{payload.get('symbol') or 'unknown'}: {e}")
        return {"perp_structure": rows, "errors": errors}

    @staticmethod
    def sample_fixture_payloads(as_of_time: str | None = None) -> list[dict[str, Any]]:
        """Deterministic BTC/ETH/SOL aggregate samples for offline smoke tests."""
        ts = as_of_time or datetime.now(timezone.utc).isoformat()
        return [
            {
                "symbol": "BTC/USDT",
                "venue": "hyperliquid",
                "source": "vergex",
                "as_of_time": ts,
                "data_quality": "complete",
                "poc": 62267.64,
                "value_area_low": 60000.0,
                "value_area_high": 65000.0,
                "short_liq_above_usd": 335_141_283.44,
                "long_liq_below_usd": 220_161_274.01,
                "top10_concentration": 0.21,
                "top50_concentration": 0.44,
                "holder_count": 30843,
                "cgo_direction": "neutral",
                "vap_direction": "bullish",
                "lfa_direction": "bullish",
                "cascade_risk": 0.35,
                "convexity": 0.42,
            },
            {
                "symbol": "ETH/USDT",
                "venue": "hyperliquid",
                "source": "vergex",
                "as_of_time": ts,
                "data_quality": "partial",
                "poc": 3420.0,
                "value_area_low": 3300.0,
                "value_area_high": 3600.0,
                "short_liq_above_usd": 94_000_000.0,
                "long_liq_below_usd": 88_000_000.0,
                "top10_concentration": 0.18,
                "top50_concentration": 0.39,
                "cgo_direction": "conflict",
                "vap_direction": "neutral",
                "lfa_direction": "neutral",
                "cascade_risk": 0.22,
                "convexity": 0.27,
            },
            {
                "symbol": "SOL/USDT",
                "venue": "hyperliquid",
                "source": "vergex",
                "as_of_time": ts,
                "data_quality": "degraded",
                "poc": 148.5,
                "value_area_low": 140.0,
                "value_area_high": 158.0,
                "short_liq_above_usd": 41_000_000.0,
                "long_liq_below_usd": 56_000_000.0,
                "top10_concentration": 0.26,
                "top50_concentration": 0.51,
                "coverage_note": "fixture degraded sample",
                "cgo_direction": "bearish",
                "vap_direction": "neutral",
                "lfa_direction": "bearish",
                "cascade_risk": 0.48,
                "convexity": 0.31,
            },
        ]


def _snapshot_from_payload(payload: dict[str, Any]) -> PerpStructureSnapshot:
    if isinstance(payload.get("data"), dict) and (
        payload["data"].get("dimensions") or payload["data"].get("metrics")
    ):
        return extract_position_x_snapshot(payload)
    cleaned = _clean_payload(payload)
    now = datetime.now(timezone.utc).isoformat()
    cleaned.setdefault("venue", "hyperliquid")
    cleaned.setdefault("source", "vergex")
    cleaned.setdefault("observed_at", now)
    cleaned.setdefault("data_quality", "unknown")
    cleaned["raw_json"] = json.dumps(cleaned, sort_keys=True, separators=(",", ":"))
    return PerpStructureSnapshot(**cleaned)


def _clean_payload(payload: dict[str, Any]) -> dict[str, Any]:
    cleaned = {k: payload[k] for k in _ALLOWED_FIELDS if k in payload}
    if cleaned.get("data_quality") not in _QUALITY_VALUES:
        cleaned["data_quality"] = "unknown"
    for key in ("cgo_direction", "vap_direction", "lfa_direction", "vendor_bias"):
        if cleaned.get(key) is not None and cleaned[key] not in _DIRECTION_VALUES:
            cleaned[key] = "conflict"
    return cleaned


def structure_source_status() -> dict[str, Any]:
    """Describe the real authority and automation state without implying a feed."""
    return {
        "mode": "owner_aggregate_import",
        "automatic_source_configured": False,
        "decision_authority": "observation_only",
        "accepted_inputs": ["aggregate_rows", "sanitized_holder_batch"],
        "minimum_shadow_window_days": 14,
        "note": "No trusted automatic Position X endpoint is configured.",
    }


def ingest_owner_aggregate(payload: dict[str, Any]) -> dict[str, Any]:
    """Persist an owner-authorized aggregate export or sanitized holder batch.

    Holder batches are reduced to the aggregate whitelist before persistence.
    The import adds immutable source provenance and never changes execution.
    """
    from aletheia.agent_shell.vergex_policy import ensure_vergex_utilization_policy_epoch
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import AuditEvent, new_id

    source_ref = str(payload.get("source_ref") or "").strip()
    if not source_ref:
        raise ValueError("source_ref is required")
    forbidden = _find_forbidden_key(payload)
    if forbidden:
        raise ValueError(f"forbidden sensitive field in import payload: {forbidden}")
    batch: dict[str, Any] | None = None
    if payload.get("markets") is not None:
        parameters = payload.get("parameters") or {}
        if not isinstance(parameters, dict):
            raise ValueError("parameters must be an object")
        batch = compute_position_x_batch(payload["markets"], **parameters)
        rows = batch["rows"]
        input_mode = "sanitized_holder_batch"
    else:
        rows = payload.get("rows")
        if not isinstance(rows, list) or not rows:
            raise ValueError("rows must be a non-empty list")
        input_mode = "aggregate_rows"
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    source_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    epoch_id = ensure_vergex_utilization_policy_epoch()
    inserted = 0
    seeded = 0

    with repo.transaction(immediate=True, savepoint=True):
        for raw in rows:
            if not isinstance(raw, dict):
                raise ValueError("each row must be an object")
            snap = _snapshot_from_payload({
                **raw,
                "source": "owner_aggregate",
                "observed_at": datetime.now(timezone.utc).isoformat(),
            })
            data = snap.model_dump()
            snapshot_id = new_id()
            data.update({
                "id": snapshot_id,
                "source": "owner_aggregate",
                "source_ref": source_ref,
                "source_hash": source_hash,
            })
            repo.insert_perp_structure_snapshot(data)
            inserted += 1
            seeded += _seed_shadow_outcomes(
                snapshot_id=snapshot_id,
                snapshot=data,
                policy_epoch_id=epoch_id,
                source_hash=source_hash,
            )
        repo.insert_audit_event(AuditEvent(
            actor="owner",
            source="aletheia.data.ingest-perp-structure",
            event_type="perp_structure_aggregate_imported",
            entity_type="perp_structure_source",
            entity_id=source_hash,
            after_state=json.dumps({
                "source_ref": source_ref,
                "source_hash": source_hash,
                "snapshots_inserted": inserted,
                "shadow_outcomes_seeded": seeded,
                "input_mode": input_mode,
                "batch_id": batch["batch_id"] if batch else None,
                "universe_size": batch["universe_size"] if batch else inserted,
                "mean_coverage_ratio": batch["mean_coverage_ratio"] if batch else None,
                "decision_authority": "observation_only",
            }, sort_keys=True),
            message="Owner-authorized aggregate structure evidence imported for shadow evaluation.",
        ))

    return {
        "status": "ingested",
        "snapshots_inserted": inserted,
        "shadow_outcomes_seeded": seeded,
        "source_ref": source_ref,
        "source_hash": source_hash,
        "policy_epoch_id": epoch_id,
        "input_mode": input_mode,
        "batch_id": batch["batch_id"] if batch else None,
        "universe_size": batch["universe_size"] if batch else inserted,
        "mean_coverage_ratio": batch["mean_coverage_ratio"] if batch else None,
        "decision_authority": "observation_only",
    }


def _seed_shadow_outcomes(
    *, snapshot_id: str, snapshot: dict[str, Any], policy_epoch_id: str,
    source_hash: str,
) -> int:
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import new_id

    bias = snapshot.get("structure_bias")
    direction = "long" if bias == "bullish" else "short" if bias == "bearish" else None
    entry_price = snapshot.get("reference_price")
    if direction is None or entry_price is None or float(entry_price) <= 0:
        return 0
    entry_ts = str(snapshot["as_of_time"])
    parsed = datetime.fromisoformat(entry_ts.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    horizons = {
        "1h": timedelta(hours=1),
        "4h": timedelta(hours=4),
        "24h": timedelta(hours=24),
    }
    for horizon, delta in horizons.items():
        repo.insert_perp_structure_shadow_outcome({
            "id": new_id(),
            "snapshot_id": snapshot_id,
            "symbol": snapshot["symbol"],
            "venue": snapshot.get("venue") or "hyperliquid",
            "market_type": "perp",
            "direction": direction,
            "horizon": horizon,
            "entry_price_usd": float(entry_price),
            "entry_ts": parsed.isoformat(),
            "due_at": (parsed + delta).isoformat(),
            "policy_epoch_id": policy_epoch_id,
            "source_hash": source_hash,
        })
    return len(horizons)


def _find_forbidden_key(value: Any, path: str = "payload") -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            current = f"{path}.{key}"
            if any(fragment in lowered for fragment in _FORBIDDEN_KEY_FRAGMENTS):
                return current
            found = _find_forbidden_key(item, current)
            if found:
                return found
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found = _find_forbidden_key(item, f"{path}[{index}]")
            if found:
                return found
    return None
