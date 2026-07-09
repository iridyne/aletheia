"""Observation-only Vergex perp structure collector.

This module intentionally does not call a real Vergex endpoint yet. It provides
the safe first write path for partner/fixture payloads: accept only aggregate
whitelisted fields, drop address/account/prompt/credential-shaped data, and
return PerpStructureSnapshot models for repository persistence by the caller.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from aletheia.data.models import PerpStructureSnapshot
from aletheia.data.position_x import extract_position_x_snapshot


_ALLOWED_FIELDS = {
    "symbol",
    "venue",
    "source",
    "as_of_time",
    "observed_at",
    "data_quality",
    "coverage_note",
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
