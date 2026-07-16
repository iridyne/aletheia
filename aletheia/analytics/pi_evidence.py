"""Candidate-conditioned evidence for System Operator B2 debate inputs.

This deliberately learns only from terminal PI decisions made in the same B2
policy epoch.  It is not a replacement for Risk's independent historical edge
checks; it prevents an all-universe aggregate from acting as a hidden veto in
the operator's LLM debate.
"""

from __future__ import annotations

from typing import Any


_MIN_NET_SAMPLES = 20
_MIN_OUTCOME_COVERAGE = 0.70
_SHRINKAGE_K = 20.0


def score_bucket(trade_score: Any) -> str:
    """Return the stable B2 quality bucket for a Ranker-selected candidate."""
    try:
        score = float(trade_score)
    except (TypeError, ValueError):
        return "unknown_score"
    if score < 40:
        return "low_score"
    if score < 50:
        return "medium_score"
    return "high_score"


def _market_type(value: Any) -> str:
    return "perp" if str(value or "spot").lower() in {
        "perp", "futures", "swap", "usdt_futures",
    } else str(value or "spot").lower()


def build_b2_evidence_summary(candidate: dict[str, Any], *, policy_epoch_id: str) -> dict[str, Any]:
    """Summarize settled same-epoch PI outcomes for this candidate cell.

    The cohort is deliberately narrow: a candidate must have passed
    ``signal_enqueue``, have the same family/direction/market/score bucket, and
    have been terminally assessed under this exact B2 policy epoch.  A small or
    partially settled cohort is explicit neutral evidence, never a negative
    directional claim.
    """
    from aletheia.storage.sqlite import get_db

    family = str(candidate.get("scanner_family") or "unknown")
    direction = str(candidate.get("direction") or "unknown").lower()
    market = _market_type(candidate.get("market_type"))
    bucket = score_bucket(candidate.get("trade_score"))
    base = {
        "version": "b2",
        "policy_epoch_id": policy_epoch_id,
        "selection_tier": "ranker_enqueued",
        "scanner_family": family,
        "direction": direction,
        "market_type": market,
        "score_bucket": bucket,
        "horizon": "24h",
        "min_net_samples": _MIN_NET_SAMPLES,
        "min_outcome_coverage": _MIN_OUTCOME_COVERAGE,
        "shrinkage_k": _SHRINKAGE_K,
    }
    if family == "unknown" or direction not in {"long", "short"} or bucket == "unknown_score":
        return {
            **base, "n_total": 0, "n_net": 0, "outcome_coverage": 0.0,
            "avg_net_pct": None, "shrunk_avg_net_pct": None,
            "cost_model_version": None, "status": "insufficient_evidence",
            "directional_verdict": None,
        }

    row = get_db().execute(
        "WITH cohort AS ("
        " SELECT fd.opportunity_id, fd.outcome_candidate_key, fd.input_snapshot "
        " FROM filter_decisions fd "
        " WHERE fd.filter_name='operator_action_assessment' "
        "   AND fd.stage='system_operator' "
        "   AND COALESCE(fd.scanner_family, 'unknown')=? "
        "   AND LOWER(COALESCE(fd.direction, ''))=? "
        "   AND CASE WHEN fd.market_type IN ('perp','futures','swap','usdt_futures') "
        "            THEN 'perp' ELSE COALESCE(fd.market_type, 'spot') END=? "
        "   AND json_extract(fd.input_snapshot, '$.b2_evidence.version')='b2' "
        "   AND json_extract(fd.input_snapshot, '$.b2_evidence.policy_epoch_id')=? "
        "   AND json_extract(fd.input_snapshot, '$.b2_evidence.selection_tier')='ranker_enqueued' "
        "   AND json_extract(fd.input_snapshot, '$.b2_evidence.score_bucket')=? "
        "   AND EXISTS (SELECT 1 FROM filter_decisions en "
        "       WHERE en.filter_name='signal_enqueue' AND en.decision='passed' "
        "         AND en.opportunity_id=fd.opportunity_id)"
        "), outcomes AS ("
        " SELECT c.opportunity_id, fo.net_return_pct, fo.cost_model_version, fo.status "
        " FROM cohort c LEFT JOIN filter_candidate_outcomes fo "
        "   ON fo.outcome_candidate_key=c.outcome_candidate_key AND fo.horizon='24h'"
        ") "
        "SELECT COUNT(DISTINCT opportunity_id) AS n_total, "
        " COUNT(DISTINCT CASE WHEN status='settled' AND net_return_pct IS NOT NULL "
        "  THEN opportunity_id END) AS n_net, "
        " AVG(CASE WHEN status='settled' THEN net_return_pct END) AS avg_net, "
        " COUNT(DISTINCT CASE WHEN status='settled' THEN cost_model_version END) AS cost_versions, "
        " MIN(CASE WHEN status='settled' THEN cost_model_version END) AS cost_model_version "
        "FROM outcomes",
        (family, direction, market, policy_epoch_id, bucket),
    ).fetchone()
    n_total = int(row["n_total"] or 0)
    n_net = int(row["n_net"] or 0)
    coverage = round(n_net / n_total, 4) if n_total else 0.0
    avg = float(row["avg_net"]) if row["avg_net"] is not None else None
    cost_incident = n_net > 0 and int(row["cost_versions"] or 0) > 1
    qualified = n_net >= _MIN_NET_SAMPLES and coverage >= _MIN_OUTCOME_COVERAGE and not cost_incident
    shrunk = round((n_net * avg) / (n_net + _SHRINKAGE_K), 6) if avg is not None else None
    verdict = ("positive" if shrunk and shrunk > 0 else "negative") if qualified else None
    status = f"qualified_{verdict}" if verdict else (
        "data_quality_incident" if cost_incident else "insufficient_evidence"
    )
    return {
        **base, "n_total": n_total, "n_net": n_net, "outcome_coverage": coverage,
        "avg_net_pct": round(avg, 6) if avg is not None else None,
        "shrunk_avg_net_pct": shrunk, "cost_model_version": row["cost_model_version"],
        "status": status, "directional_verdict": verdict,
    }


def render_b2_evidence_summary(summary: dict[str, Any] | None) -> str:
    """Render the exact, role-neutral B2 evidence block shared by all LLM roles."""
    if not summary:
        return "B2 candidate evidence: unavailable; neutral, not a directional veto."
    if summary.get("status") in {"insufficient_evidence", "data_quality_incident"}:
        reason = "data-quality incident" if summary.get("status") == "data_quality_incident" else "insufficient same-epoch settled evidence"
        return (
            f"B2 candidate evidence: {reason} "
            f"(n_net={summary.get('n_net', 0)}/{summary.get('n_total', 0)}, "
            f"coverage={float(summary.get('outcome_coverage') or 0):.0%}); "
            "neutral, not a directional veto."
        )
    return (
        "B2 candidate evidence: "
        f"{summary.get('directional_verdict')} conditioned evidence "
        f"(n_net={summary.get('n_net')}/{summary.get('n_total')}, "
        f"coverage={float(summary.get('outcome_coverage') or 0):.0%}, "
        f"avg_net={float(summary.get('avg_net_pct') or 0):+.3f}%, "
        f"shrunk={float(summary.get('shrunk_avg_net_pct') or 0):+.3f}%); "
        "one input, not a directional veto."
    )
