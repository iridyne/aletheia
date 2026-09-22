"""T0 Funding proof for the canonical Candidate Outcome cohort (#3086)."""

from __future__ import annotations

import json
from collections.abc import Mapping

from aletheia.utils.dt import parse_utc_instant
from aletheia.data.derivatives_facts import finite, project_snapshot
from aletheia.data.freshness import assess_freshness

PERP_FUNDING_EVIDENCE_VERSION = "candidate-perp-funding/v1"


def funding_proof(row: Mapping) -> dict:
    try:
        value = json.loads(row.get("funding_evidence_json") or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def proved_funding_interval(row: Mapping) -> float | None:
    """Validate frozen proof at T0, never against settlement wall-clock time."""
    proof = funding_proof(row)
    at = parse_utc_instant(row.get("entry_ts"))
    source = parse_utc_instant(proof.get("source_observed_at"))
    acquired = parse_utc_instant(proof.get("acquired_at"))
    interval_at = parse_utc_instant(proof.get("interval_acquired_at"))
    interval = finite(proof.get("interval_seconds"))
    instrument = proof.get("instrument") or {}
    if (
        proof.get("version") != PERP_FUNDING_EVIDENCE_VERSION
        or proof.get("availability") != "available"
        or proof.get("venue") != row.get("venue")
        or instrument.get("canonical_symbol") != row.get("symbol")
        or instrument.get("market_type") != "perp"
        or not instrument.get("source_instrument_id")
        or proof.get("value_kind") != "observed"
        or proof.get("source_scope") != "venue_native"
        or finite(proof.get("value")) is None
        or proof.get("value") != row.get("funding_rate_at_entry")
        or interval is None
        or interval <= 0
        or not proof.get("interval_source")
        or at is None
        or source is None
        or acquired is None
        or source > at
        or acquired > at
        or interval_at is None
        or interval_at > at
    ):
        return None
    if not assess_freshness(proof | {"observed_at": proof.get("source_observed_at")}, "funding", now=at).usable:
        return None
    return interval


def stored_funding_proof(row: dict | None, *, now) -> dict:
    """Use the existing exact primary snapshot as a fallback, without new I/O."""
    if not row:
        return {}
    fact = project_snapshot(row, "funding", now=now)[0]
    return {
        **fact,
        "version": PERP_FUNDING_EVIDENCE_VERSION,
        "interval_acquired_at": fact.get("acquired_at"),
        "interval_source": "funding_snapshot:" + str(row["id"]) if row.get("id") else None,
    }


def freeze_funding_proof(feature: Mapping) -> str | None:
    if feature.get("market_type") != "perp":
        return None
    proof = funding_proof(feature)
    proof["version"] = PERP_FUNDING_EVIDENCE_VERSION
    return json.dumps(proof, sort_keys=True, separators=(",", ":"))
