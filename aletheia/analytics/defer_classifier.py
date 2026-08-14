"""Pure classifier: which reason class does a defer_reason string belong to?

Layer-1 observability helper. Zero DB access, zero schema knowledge — takes the
same `defer_reason` string that is already persisted in
``operator_evaluation_events.queue_snapshot_json.defer_reason`` (commands.py
callers) and returns one of three buckets:

* ``market_quiet``       — Operator saw the setup and hedged (regime signal).
* ``candidate_specific`` — data-plumbing / per-candidate state (NOT regime).
* ``unknown``            — reason string didn't match any known rule.

The classifier is used both by the immediate-layer log payload (commands.py)
and by the pipeline_progress aggregation. It is intentionally the ONLY place
this taxonomy lives so downstream surfaces (doctor attribution, analytics
route response, frontend tooltip) all agree.

Rule ordering matters: ``direct_skip_not_confirmed:candidate_changed`` must be
tested BEFORE the generic ``direct_skip_not_confirmed:*`` rule — the specific
suffix marks a candidate-state shift mid-eval and belongs in the candidate
bucket, whereas the generic form (unverified adverse reasons, invalid skip
payloads) is a regime hedge.
"""

from __future__ import annotations

from typing import Literal


ReasonClass = Literal["market_quiet", "candidate_specific", "unknown"]


_MARKET_QUIET_EXACT: frozenset[str] = frozenset(
    {
        "operator_low_conviction",
        "debate_not_confirmed",
    }
)

_CANDIDATE_SPECIFIC_EXACT: frozenset[str] = frozenset(
    {
        "candidate_technical_snapshot_unavailable",
        "candidate_technical_snapshot_incomplete",
        "candidate_technical_snapshot_stale",
        "candidate_entry_mark_unavailable",
        "candidate_entry_mark_identity_unavailable",
        "candidate_entry_mark_identity_mismatch",
    }
)

_DIRECT_SKIP_PREFIX = "direct_skip_not_confirmed"
_DIRECT_SKIP_CANDIDATE_SUFFIX = "candidate_changed"
_DIRECT_SKIP_MARKET_SUFFIX = "unverified_adverse_reason"


def classify_defer_reason(reason: str | None) -> ReasonClass:
    """Return the reason class for a persisted ``defer_reason`` string.

    Never raises. Falls back to ``unknown`` for any string that didn't match.
    """
    code = (reason or "").strip()
    if not code:
        return "unknown"

    if code in _MARKET_QUIET_EXACT:
        return "market_quiet"
    if code in _CANDIDATE_SPECIFIC_EXACT:
        return "candidate_specific"

    if code.startswith(_DIRECT_SKIP_PREFIX + ":"):
        suffix = code[len(_DIRECT_SKIP_PREFIX) + 1 :]
        # Order matters: candidate_changed is a state shift, not a regime hedge.
        if suffix == _DIRECT_SKIP_CANDIDATE_SUFFIX:
            return "candidate_specific"
        if suffix.startswith(_DIRECT_SKIP_MARKET_SUFFIX):
            return "market_quiet"
        return "unknown"

    return "unknown"


__all__ = ["classify_defer_reason", "ReasonClass"]
