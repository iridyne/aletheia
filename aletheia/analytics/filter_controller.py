"""Filter Feedback Controller — Phase 1 (shadow / observe-only).

Reports whether each tunable filter has enough filter-specific evidence for a
negative-feedback verdict. Aggregate scanner-family outcomes are useful context,
but cannot prove that any individual filter is too tight or too loose, so they
must never become loosen/tighten advice on their own.

The controller now uses filter-specific, exact-version pass/reject cohorts with
cost-aware net outcomes. It remains a shadow audit: observational differences
never mutate a threshold or trading configuration.

OBSERVE-ONLY invariant: this module reads the DB and writes append-only rows to
`controller_recommendations`. It NEVER calls save_config, mutates trading state,
or invokes the ranker/debate/risk/execution paths. That invariant is asserted by
tests/filter_eval/.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from aletheia.analytics.filter_registry import (
    FilterSpec,
    FILTER_REGISTRY,
    tunable_filters,
    report_only_filters,
)

logger = logging.getLogger("aletheia.analytics.filter_controller")

Verdict = Literal["overrestrictive_suspect", "hold", "insufficient_data"]

# ─── Controller constants (Phase 1 defaults; overridable via ControllerConfig) ──
# Minimum settled samples for a (filter, family) cell before we recommend anything.
_DEFAULT_MIN_SAMPLES = 20
_MIN_OUTCOME_COVERAGE = 0.70
_MIN_NET_DIFFERENCE_PCT = 0.05


@dataclass
class FilterRecommendation:
    """One (filter × scanner_family) shadow verdict."""

    filter_name: str
    scanner_family: str
    stage: str
    current_value: str
    verdict: Verdict
    # "loosen" | "hold" | "tighten" | "n/a" — the config move implied by the verdict,
    # respecting the filter's monotonic loosen_direction. "n/a" for insufficient_data.
    suggested_direction: str
    # Signed error in [-1, 1]: >0 → drops look profitable (loosen), <0 → passes lost
    # (tighten). None when insufficient_data.
    error_signal: float | None
    n_samples: int
    reasoning: str
    direction: str | None = None
    market_type: str | None = None
    horizon: str | None = None
    filter_version: str | None = None
    passed_n: int = 0
    rejected_n: int = 0
    passed_shrunk_avg_net: float | None = None
    rejected_shrunk_avg_net: float | None = None
    outcome_coverage: float | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def compute_filter_feedback(
    min_samples: int = _DEFAULT_MIN_SAMPLES,
    *,
    evaluated_since: str | None = None,
) -> list[FilterRecommendation]:
    """Compare exact pass/reject cohorts without mutating any filter parameter."""
    try:
        from aletheia.analytics.filter_evidence import filter_feedback_stats

        stats = (
            filter_feedback_stats(evaluated_since=evaluated_since)
            if evaluated_since
            else filter_feedback_stats()
        )
    except Exception as exc:
        logger.warning("filter_controller: filter evidence unavailable: %s", exc)
        stats = []

    grouped: dict[tuple, dict[str, dict[str, Any]]] = {}
    for row in stats:
        if not row.get("horizon"):
            continue
        key = (
            row.get("filter_name"),
            row.get("stage"),
            row.get("filter_version"),
            row.get("scanner_family") or "unknown",
            row.get("direction"),
            row.get("market_type"),
            row.get("horizon"),
        )
        grouped.setdefault(key, {})[str(row.get("decision"))] = row

    recs: list[FilterRecommendation] = []
    for spec in tunable_filters():
        if not spec.feedback_ready:
            recs.append(FilterRecommendation(
                filter_name=spec.name, scanner_family="(unverified)", stage=spec.stage,
                current_value=spec.current_value, verdict="insufficient_data",
                suggested_direction="n/a", error_signal=None, n_samples=0,
                reasoning=(
                    "No per-filter passed/dropped cohort is persisted. Aggregate "
                    "scanner-family returns are not a filter-calibration signal."
                ),
            ))
            continue
        matching = [
            (key, cells) for key, cells in grouped.items() if key[0] == spec.name
        ]
        if not matching:
            recs.append(FilterRecommendation(
                filter_name=spec.name, scanner_family="(all)", stage=spec.stage,
                current_value=spec.current_value, verdict="insufficient_data",
                suggested_direction="n/a", error_signal=None, n_samples=0,
                reasoning="No settled filter-specific pass/reject net outcomes yet.",
            ))
            continue
        for key, cells in sorted(matching, key=lambda item: str(item[0])):
            _, evidence_stage, version, family, direction, market_type, horizon = key
            passed = cells.get("passed")
            rejected = cells.get("rejected")
            evidence = passed or rejected or {}
            current_value = str(
                evidence.get("parameter_snapshot") or spec.current_value
            )
            pass_n = int((passed or {}).get("n_net") or 0)
            reject_n = int((rejected or {}).get("n_net") or 0)
            pass_coverage = float((passed or {}).get("outcome_coverage") or 0.0)
            reject_coverage = float((rejected or {}).get("outcome_coverage") or 0.0)
            coverage = min(pass_coverage, reject_coverage)
            pass_mean = (passed or {}).get("shrunk_avg_net")
            reject_mean = (rejected or {}).get("shrunk_avg_net")
            enough = (
                passed is not None
                and rejected is not None
                and pass_n >= min_samples
                and reject_n >= min_samples
                and coverage >= _MIN_OUTCOME_COVERAGE
                and pass_mean is not None
                and reject_mean is not None
            )
            difference = (
                float(reject_mean) - float(pass_mean)
                if enough else None
            )
            suspect = bool(
                enough
                and difference is not None
                and difference >= _MIN_NET_DIFFERENCE_PCT
                and float(reject_mean) > 0
            )
            if suspect:
                verdict: Verdict = "overrestrictive_suspect"
                suggested = "review_loosen"
                error = round(max(-1.0, min(1.0, float(difference))), 4)
                reasoning = (
                    f"{family}:{direction}:{market_type}:{horizon} rejected net mean "
                    f"{float(reject_mean):+.3f}% exceeds passed {float(pass_mean):+.3f}% "
                    f"after shrinkage (pass n={pass_n}, reject n={reject_n}, "
                    f"coverage={coverage:.0%}); observational review only."
                )
            elif enough:
                verdict = "hold"
                suggested = "hold"
                error = None
                reasoning = (
                    f"{family}:{direction}:{market_type}:{horizon} has complete paired "
                    f"evidence but no >= {_MIN_NET_DIFFERENCE_PCT:.2f}% overrestriction "
                    f"signal; keep unchanged."
                )
            else:
                verdict = "insufficient_data"
                suggested = "n/a"
                error = None
                reasoning = (
                    f"{family}:{direction}:{market_type}:{horizon} needs pass/reject "
                    f"n>={min_samples} and coverage>={_MIN_OUTCOME_COVERAGE:.0%}; "
                    f"currently pass={pass_n}, reject={reject_n}, coverage={coverage:.0%}."
                )
            recs.append(FilterRecommendation(
                filter_name=spec.name,
                scanner_family=family,
                stage=str(evidence_stage or spec.stage),
                current_value=current_value,
                verdict=verdict,
                suggested_direction=suggested,
                error_signal=error,
                n_samples=min(pass_n, reject_n),
                reasoning=reasoning,
                direction=direction,
                market_type=market_type,
                horizon=horizon,
                filter_version=version,
                passed_n=pass_n,
                rejected_n=reject_n,
                passed_shrunk_avg_net=pass_mean,
                rejected_shrunk_avg_net=reject_mean,
                outcome_coverage=coverage,
            ))
    return recs


def run_controller_observe(min_samples: int = _DEFAULT_MIN_SAMPLES) -> dict[str, Any]:
    """Compute recommendations and PERSIST them as append-only shadow rows.

    Returns a summary dict for the scheduler log. Writes only to
    controller_recommendations with status='shadow_only'. No config is touched.
    """
    run_id = uuid.uuid4().hex
    generated_at = _now_iso()
    recs = compute_filter_feedback(min_samples=min_samples)

    written = 0
    try:
        from aletheia.storage import repositories as repo
        from aletheia.storage.repositories import transaction
        with transaction():
            for r in recs:
                legacy_verdict = (
                    r.verdict
                    if r.verdict in {"hold", "insufficient_data"}
                    else "insufficient_data"
                )
                legacy_direction = (
                    r.suggested_direction
                    if r.suggested_direction in {"loosen", "hold", "tighten", "n/a"}
                    else "n/a"
                )
                repo.insert_controller_recommendation({
                    "id": uuid.uuid4().hex,
                    "created_at": generated_at,
                    "filter_name": r.filter_name,
                    "scanner_family": r.scanner_family,
                    "stage": r.stage,
                    "current_value": r.current_value,
                    # Existing databases constrain these two legacy columns to
                    # the old action vocabulary. Keep old readers conservative;
                    # the evidence_* columns carry the new observational result.
                    "suggested_direction": legacy_direction,
                    "verdict": legacy_verdict,
                    "error_signal": r.error_signal,
                    "n_samples": r.n_samples,
                    "reasoning": r.reasoning,
                    "status": "shadow_only",
                    "run_id": run_id,
                    "evidence_verdict": r.verdict,
                    "evidence_direction": r.suggested_direction,
                    "filter_version": r.filter_version,
                    "direction": r.direction,
                    "market_type": r.market_type,
                    "horizon": r.horizon,
                    "passed_n": r.passed_n,
                    "rejected_n": r.rejected_n,
                    "passed_shrunk_avg_net": r.passed_shrunk_avg_net,
                    "rejected_shrunk_avg_net": r.rejected_shrunk_avg_net,
                    "outcome_coverage": r.outcome_coverage,
                })
                written += 1
    except Exception as e:  # pragma: no cover - defensive
        written = 0
        logger.warning("filter_controller: persist failed: %s", e)

    counts: dict[str, int] = {}
    for r in recs:
        counts[r.verdict] = counts.get(r.verdict, 0) + 1

    logger.info(
        "filter_controller observe: run=%s written=%d verdicts=%s",
        run_id, written, counts,
    )
    return {
        "run_id": run_id,
        "generated_at": generated_at,
        "recommendations_written": written,
        "verdict_counts": counts,
        "min_samples": min_samples,
        "shadow_only": True,
    }


# ─── Report builder + renderer (for `ato opportunity audit --view controller`) ──

def build_filter_controller_report(
    min_samples: int = _DEFAULT_MIN_SAMPLES,
    *,
    hours: int | None = None,
) -> dict[str, Any]:
    """Read-only report of current recommendations (does NOT persist)."""
    hours_i = max(1, int(hours)) if hours is not None else None
    evaluated_since = (
        (datetime.now(timezone.utc) - timedelta(hours=hours_i)).isoformat()
        if hours_i is not None
        else None
    )
    recs = compute_filter_feedback(
        min_samples=min_samples,
        evaluated_since=evaluated_since,
    )
    counts: dict[str, int] = {}
    for r in recs:
        counts[r.verdict] = counts.get(r.verdict, 0) + 1
    return {
        "generated_at": _now_iso(),
        "window": {"hours": hours_i, "evaluated_since": evaluated_since},
        "min_samples": min_samples,
        "shadow_only": True,
        "signal_basis": "filter_specific_shrunk_net_expectancy",
        "signal_note": (
            "Only exact filter-version pass/reject cohorts with cost-aware net outcomes "
            "are compared. Findings are observational and never mutate configuration."
        ),
        "verdict_counts": counts,
        "recommendations": [asdict(r) for r in recs],
        "report_only_filters": [
            {"name": s.name, "stage": s.stage, "reason": s.note}
            for s in report_only_filters()
        ],
        "tunable_filter_count": len(tunable_filters()),
        "total_filter_count": len(FILTER_REGISTRY),
    }


_ARROW = {"review_loosen": "review loosen", "hold": "= hold", "n/a": "n/a"}


def render_filter_controller_report(report: dict[str, Any]) -> str:
    """Human-readable rendering, mirroring the other --view renderers."""
    lines: list[str] = []
    lines.append("Filter Feedback Controller (SHADOW — observe only)")
    lines.append("")
    lines.append(f"Generated: {report.get('generated_at')}")
    window = report.get("window") or {}
    lines.append(
        f"Window: {window.get('hours')}h"
        if window.get("hours") is not None
        else "Window: all persisted evidence"
    )
    lines.append(f"Mode: shadow_only (no config changes) | "
                 f"min_samples gate: {report.get('min_samples')}")
    if report.get("signal_note"):
        lines.append(f"Signal: {report['signal_note']}")
    vc = report.get("verdict_counts") or {}
    lines.append(
        f"Verdicts: overrestrictive_suspect={vc.get('overrestrictive_suspect',0)} "
        f"hold={vc.get('hold',0)} insufficient_data={vc.get('insufficient_data',0)}"
    )
    lines.append("")

    recs = report.get("recommendations") or []
    actionable = [r for r in recs if r["verdict"] == "overrestrictive_suspect"]
    if actionable:
        lines.append("Overrestrictive suspects (human review only, NOT applied):")
        for r in sorted(actionable, key=lambda x: abs(x.get("error_signal") or 0), reverse=True):
            lines.append(
                f"  {r['filter_name']} · {r['scanner_family']} "
                f"[{_ARROW.get(r['suggested_direction'], r['suggested_direction'])}] "
                f"err={r.get('error_signal')} n={r['n_samples']}"
            )
            lines.append(f"      {r['reasoning']}")
    else:
        lines.append("No overrestrictive suspects — all cells hold or insufficient_data.")
    lines.append("")

    insufficient = [r for r in recs if r["verdict"] == "insufficient_data"]
    if insufficient:
        lines.append(f"Insufficient data ({len(insufficient)} cells): "
                     "collecting more settled outcomes before recommending.")
    ro = report.get("report_only_filters") or []
    if ro:
        lines.append("")
        lines.append("Report-only filters (never auto-tuned — hard rules / change-controlled):")
        for f in ro:
            lines.append(f"  {f['name']} ({f['stage']}) — {f['reason']}")

    lines.append("")
    lines.append("Next action:")
    lines.append("  Keep filter parameters unchanged; collect independent evidence or "
                 "run a bounded replay before changing a threshold.")
    return "\n".join(lines)
