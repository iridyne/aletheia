"""Filter Feedback Controller — Phase 1 (shadow / observe-only).

Computes, for each tunable filter × scanner_family, a negative-feedback verdict:
whether the filter looks too tight (dropping opportunities that would have been
profitable → LOOSEN), too loose (passing opportunities that lost → TIGHTEN), or
in balance (HOLD). Below a sample-size gate it returns INSUFFICIENT_DATA and makes
no recommendation.

This is a direct generalization of `allocator.get_family_multipliers()`: same
per-scanner-family forward-outcome hit-rate → linear error map around a neutral
point → bounded/gated output. The difference is the *actuator*: get_family_multipliers
scales score multipliers; this maps the same error onto each filter's threshold
knob (in Phase 1 only as a recommendation, never applied).

OBSERVE-ONLY invariant: this module reads the DB and writes append-only rows to
`controller_recommendations`. It NEVER calls save_config, mutates trading state,
or invokes the ranker/debate/risk/execution paths. That invariant is asserted by
tests/filter_eval/.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Literal

from aletheia.analytics.filter_registry import (
    FilterSpec,
    FILTER_REGISTRY,
    tunable_filters,
    report_only_filters,
)

logger = logging.getLogger("aletheia.analytics.filter_controller")

Verdict = Literal["loosen", "hold", "tighten", "insufficient_data"]

# ─── Controller constants (Phase 1 defaults; overridable via ControllerConfig) ──
# Minimum settled samples for a (filter, family) cell before we recommend anything.
_DEFAULT_MIN_SAMPLES = 20
# Hit-rate band around 0.5 treated as "balanced" → HOLD. Outside the band we
# recommend a direction. Mirrors get_family_multipliers' linear map slope choice.
_HOLD_BAND = 0.10  # win_rate within 0.50 ± 0.10 → hold
# Per-family calibration horizon — the holding period each family is judged on.
# Copied from allocator._FAMILY_CALIBRATION_HORIZON so verdicts align with the
# score-multiplier loop already in production.
_FAMILY_HORIZON = {
    "momentum": "4h",
    "mean_reversion": "1h",
    "volatility": "1h",
    "derivatives": "1h",
}
_DEFAULT_HORIZON = "1h"


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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _family_horizon(family: str) -> str:
    return _FAMILY_HORIZON.get(family, _DEFAULT_HORIZON)


def _verdict_from_hitrate(
    hit_rate: float | None,
    n: int,
    min_samples: int,
) -> tuple[Verdict, float | None]:
    """Map a family's direction-adjusted hit_rate to a verdict + signed error.

    hit_rate is the fraction of settled outcomes where the family's directional
    call was correct (long wins on +return, short on -return) — the same win
    metric calibration_stats/family_direction_edge already compute.

    error = (hit_rate - 0.5) * 2  → range [-1, 1]. A high hit_rate on opportunities
    this filter DROPPED means the filter is discarding good setups → LOOSEN.
    A low hit_rate on setups it PASSED means it is letting losers through → TIGHTEN.
    """
    if hit_rate is None or n < min_samples:
        return "insufficient_data", None
    error = max(-1.0, min(1.0, (hit_rate - 0.5) * 2.0))
    if hit_rate > 0.5 + _HOLD_BAND:
        return "loosen", error
    if hit_rate < 0.5 - _HOLD_BAND:
        return "tighten", error
    return "hold", error


def _direction_label(spec: FilterSpec, verdict: Verdict) -> str:
    """The config move implied by a verdict, for display. Purely informational
    in Phase 1 (nothing is applied)."""
    if verdict in ("insufficient_data", "hold"):
        return "hold" if verdict == "hold" else "n/a"
    return verdict  # "loosen" / "tighten" — the abstract move, dir arrow shown in report


def _reason(spec: FilterSpec, family: str, verdict: Verdict,
            hit_rate: float | None, n: int, horizon: str) -> str:
    if verdict == "insufficient_data":
        return (f"{family}: only {n} settled {horizon} outcomes "
                f"(< gate); no recommendation.")
    wr = f"{hit_rate*100:.1f}%" if hit_rate is not None else "n/a"
    if verdict == "loosen":
        return (f"{family}: direction-adjusted {horizon} hit-rate {wr} on n={n} "
                f"outcomes — this family's setups are working; '{spec.name}' looks "
                f"too tight (move {spec.loosen_direction} to loosen).")
    if verdict == "tighten":
        return (f"{family}: direction-adjusted {horizon} hit-rate {wr} on n={n} "
                f"outcomes — setups underperforming; '{spec.name}' looks too loose.")
    return (f"{family}: hit-rate {wr} on n={n} outcomes — within balanced band, hold.")


def _family_hitrates(min_samples: int) -> dict[str, dict[str, Any]]:
    """Per-family direction-adjusted hit-rate at the family's calibration horizon.

    Reuses repositories.calibration_stats() (all settled universe_forward_outcomes,
    grouped by family+horizon, direction-aware win). Returns
    {family: {"hit_rate": float|None, "n": int, "horizon": str}}.
    Read-only; returns {} on any error so the controller degrades to
    insufficient_data rather than raising.
    """
    try:
        from aletheia.storage import repositories as repo
        stats = repo.calibration_stats()
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("filter_controller: calibration_stats failed: %s", e)
        return {}

    out: dict[str, dict[str, Any]] = {}
    for row in stats:
        family = row.get("scanner_family")
        if not family:
            continue
        if row.get("horizon") != _family_horizon(family):
            continue
        n = int(row.get("n") or 0)
        win = int(row.get("win_count") or 0)
        hit_rate = (win / n) if n else None
        out[family] = {"hit_rate": hit_rate, "n": n, "horizon": _family_horizon(family)}
    return out


def compute_filter_feedback(min_samples: int = _DEFAULT_MIN_SAMPLES) -> list[FilterRecommendation]:
    """Compute shadow recommendations for every tunable, family-keyed filter.

    Phase-1 signal source: per-family direction-adjusted forward-return hit-rate
    (calibration_stats). This is deliberately the SAME signal the production
    score-multiplier loop uses, so verdicts are consistent with existing behavior.

    Non-family-keyed filters (batch_limit, cooldown) and unobservable filters
    (observer_pre_filter) get a single 'insufficient_data' row noting why — they
    need dedicated signals added in a later phase.
    """
    hitrates = _family_hitrates(min_samples)
    recs: list[FilterRecommendation] = []

    for spec in tunable_filters():
        if not spec.family_keyed:
            recs.append(FilterRecommendation(
                filter_name=spec.name, scanner_family="(global)", stage=spec.stage,
                current_value=spec.current_value, verdict="insufficient_data",
                suggested_direction="n/a", error_signal=None, n_samples=0,
                reasoning=f"{spec.name} is not scanner_family-keyed and has no "
                          f"dedicated signal yet ({spec.feedback_source}); "
                          f"see roadmap.",
            ))
            continue
        if spec.feedback_source == "none":
            recs.append(FilterRecommendation(
                filter_name=spec.name, scanner_family="(all)", stage=spec.stage,
                current_value=spec.current_value, verdict="insufficient_data",
                suggested_direction="n/a", error_signal=None, n_samples=0,
                reasoning=f"{spec.name} drops before an opportunity row exists — "
                          f"not observable until a rejection log is added (roadmap).",
            ))
            continue

        if not hitrates:
            recs.append(FilterRecommendation(
                filter_name=spec.name, scanner_family="(all)", stage=spec.stage,
                current_value=spec.current_value, verdict="insufficient_data",
                suggested_direction="n/a", error_signal=None, n_samples=0,
                reasoning="No settled forward-outcome stats available yet.",
            ))
            continue

        for family, hr in sorted(hitrates.items()):
            verdict, error = _verdict_from_hitrate(hr["hit_rate"], hr["n"], min_samples)
            recs.append(FilterRecommendation(
                filter_name=spec.name,
                scanner_family=family,
                stage=spec.stage,
                current_value=spec.current_value,
                verdict=verdict,
                suggested_direction=_direction_label(spec, verdict),
                error_signal=round(error, 4) if error is not None else None,
                n_samples=hr["n"],
                reasoning=_reason(spec, family, verdict, hr["hit_rate"], hr["n"], hr["horizon"]),
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
                repo.insert_controller_recommendation({
                    "id": uuid.uuid4().hex,
                    "created_at": generated_at,
                    "filter_name": r.filter_name,
                    "scanner_family": r.scanner_family,
                    "stage": r.stage,
                    "current_value": r.current_value,
                    "suggested_direction": r.suggested_direction,
                    "verdict": r.verdict,
                    "error_signal": r.error_signal,
                    "n_samples": r.n_samples,
                    "reasoning": r.reasoning,
                    "status": "shadow_only",
                    "run_id": run_id,
                })
                written += 1
    except Exception as e:  # pragma: no cover - defensive
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

def build_filter_controller_report(min_samples: int = _DEFAULT_MIN_SAMPLES) -> dict[str, Any]:
    """Read-only report of current recommendations (does NOT persist)."""
    recs = compute_filter_feedback(min_samples=min_samples)
    counts: dict[str, int] = {}
    for r in recs:
        counts[r.verdict] = counts.get(r.verdict, 0) + 1
    return {
        "generated_at": _now_iso(),
        "min_samples": min_samples,
        "shadow_only": True,
        "signal_basis": "phase1_family_forward_return",
        "signal_note": (
            "Phase 1 uses a SHARED per-scanner_family direction-adjusted forward-return "
            "hit-rate (calibration_stats) as the error signal, so all filters keyed on "
            "the same family share a verdict — the per-filter loosen_direction still "
            "differs. A drop-specific counterfactual (per-stage) is a later-phase refinement."
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


_ARROW = {"loosen": "↑ loosen", "tighten": "↓ tighten", "hold": "= hold",
          "n/a": "n/a"}


def render_filter_controller_report(report: dict[str, Any]) -> str:
    """Human-readable rendering, mirroring the other --view renderers."""
    lines: list[str] = []
    lines.append("Filter Feedback Controller (SHADOW — observe only)")
    lines.append("")
    lines.append(f"Generated: {report.get('generated_at')}")
    lines.append(f"Mode: shadow_only (no config changes) | "
                 f"min_samples gate: {report.get('min_samples')}")
    if report.get("signal_note"):
        lines.append(f"Signal: {report['signal_note']}")
    vc = report.get("verdict_counts") or {}
    lines.append(f"Verdicts: loosen={vc.get('loosen',0)} tighten={vc.get('tighten',0)} "
                 f"hold={vc.get('hold',0)} insufficient_data={vc.get('insufficient_data',0)}")
    lines.append("")

    recs = report.get("recommendations") or []
    actionable = [r for r in recs if r["verdict"] in ("loosen", "tighten")]
    if actionable:
        lines.append("Actionable recommendations (would-be adjustments, NOT applied):")
        for r in sorted(actionable, key=lambda x: abs(x.get("error_signal") or 0), reverse=True):
            lines.append(
                f"  {r['filter_name']} · {r['scanner_family']} "
                f"[{_ARROW.get(r['suggested_direction'], r['suggested_direction'])}] "
                f"err={r.get('error_signal')} n={r['n_samples']}"
            )
            lines.append(f"      {r['reasoning']}")
    else:
        lines.append("No actionable recommendations — all cells hold or insufficient_data.")
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
    lines.append("  Shadow mode. Review verdicts over multiple cycles; do not tune "
                 "manually on a single cycle. Auto-tune is a future phase.")
    return "\n".join(lines)
