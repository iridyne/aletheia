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
from collections import defaultdict
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


@dataclass
class PersistentRecommendation:
    """A (filter × cell) key whose overrestrictive_suspect verdict has held across
    multiple consecutive controller runs.

    Purpose: single-run suspects are noise-prone; repeated flags on the SAME cell
    over N+ runs are what warrants operator attention. This dataclass surfaces
    that stability signal for human review — it is NOT a Learning hypothesis
    proposal. The ranker_factor_filter path cannot mutate filter parameters
    (allowed_effects=('candidate_filter','score_adjustment') per registry), and
    filter_ttl_shadow is declared read_only (is_learning_treatment=False), so any
    config change here is a change-controlled operator decision, not an A/B experiment.

    See docs/design/subsystems/causal_counterfactual_research_guidance.md L2 for
    the observational-diagnostic vs prospective-experiment boundary this respects.
    """

    filter_name: str
    scanner_family: str
    direction: str | None
    market_type: str | None
    horizon: str | None
    filter_version: str | None
    consecutive_suspect_runs: int
    runs_analyzed: int
    first_suspect_at: str  # ISO — the OLDEST end of the current streak
    last_suspect_at: str   # ISO — the NEWEST (most recent) suspect
    latest_error_signal: float | None
    latest_reasoning: str
    latest_run_id: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _batch_scanner_weights_quota_stats(
    *, evaluated_since: str | None = None,
    shrinkage_k: float = 20.0,
) -> list[dict[str, Any]]:
    """Aggregate scanner_weights_quota rows out of the compressed batch stream.

    Mirrors `filter_feedback_stats`' output shape so `compute_filter_feedback`
    can consume batches side-by-side with legacy per-row filter_decisions rows.
    Legacy rows in filter_decisions produced before the writer flip continue to
    surface via `filter_feedback_stats`; this helper covers the batch format so
    the observational signal survives the migration (dual-decode).
    """
    try:
        from aletheia.analytics.filter_evidence import decode_filter_decision_batch
        from aletheia.storage.sqlite import get_db
    except Exception:
        return []
    time_clause = " AND datetime(evaluated_at) >= datetime(?)" if evaluated_since else ""
    params: tuple[Any, ...] = (evaluated_since,) if evaluated_since else ()
    try:
        rows = get_db().execute(
            "SELECT id, filter_name, stage, filter_version, parameter_snapshot, "
            "candidate_facts, evaluated_at "
            "FROM filter_decision_batches "
            "WHERE filter_name = 'scanner_weights_quota'"
            f"{time_clause} "
            "ORDER BY datetime(evaluated_at) ASC, id ASC",
            params,
        ).fetchall()
    except Exception:
        return []

    # {(cohort_tuple, decision): {"outcome_keys": set(), "first_ts": str}}
    first_exposure: dict[str, str] = {}
    facts_by_cohort: dict[tuple, list[dict[str, Any]]] = {}
    for raw in rows:
        row = dict(raw)
        payload = row.get("candidate_facts") or "[]"
        try:
            facts = decode_filter_decision_batch(str(payload))
        except Exception:
            facts = []
        stage = row.get("stage") or "universe_allocation"
        version = row.get("filter_version")
        param_snap = row.get("parameter_snapshot") or "{}"
        evaluated_at = row.get("evaluated_at")
        for fact in facts:
            outcome_key = fact.get("outcome_candidate_key")
            if not outcome_key:
                continue
            decision = str(fact.get("decision") or "")
            if decision not in {"passed", "rejected"}:
                continue
            key = str(outcome_key)
            existing = first_exposure.get(key)
            if existing is not None and str(existing) <= str(evaluated_at or ""):
                # Not the first exposure — skip to keep pass/reject cohorts disjoint.
                continue
            first_exposure[key] = str(evaluated_at or "")
            cohort_key = (
                "scanner_weights_quota",
                stage,
                version,
                param_snap,
                fact.get("scanner_family") or "unknown",
                fact.get("direction"),
                fact.get("market_type") or "spot",
                decision,
            )
            facts_by_cohort.setdefault(cohort_key, []).append({
                "outcome_candidate_key": key,
                "decision": decision,
            })

    if not facts_by_cohort:
        return []

    # Join with filter_candidate_outcomes to attach per-horizon net returns.
    try:
        from aletheia.storage.sqlite import get_db

        outcome_rows = get_db().execute(
            "SELECT outcome_candidate_key, horizon, status, net_return_pct "
            "FROM filter_candidate_outcomes"
        ).fetchall()
    except Exception:
        outcome_rows = []
    outcomes: dict[str, list[dict[str, Any]]] = {}
    for raw in outcome_rows:
        row = dict(raw)
        key = str(row.get("outcome_candidate_key") or "")
        if not key:
            continue
        outcomes.setdefault(key, []).append(row)

    aggregated: list[dict[str, Any]] = []
    priors: dict[tuple, tuple[int, float]] = {}
    for cohort_key, facts in facts_by_cohort.items():
        (filter_name, stage, version, param_snap, family, direction,
         market_type, decision) = cohort_key
        per_horizon: dict[str, dict[str, Any]] = {}
        for fact in facts:
            key = fact["outcome_candidate_key"]
            for outcome in outcomes.get(key, []):
                horizon = outcome.get("horizon")
                if not horizon:
                    continue
                bucket = per_horizon.setdefault(str(horizon), {
                    "candidates": set(), "settled": [], "gains": 0.0, "losses": 0.0,
                })
                bucket["candidates"].add(key)
                if outcome.get("status") == "settled" and outcome.get("net_return_pct") is not None:
                    net = float(outcome["net_return_pct"])
                    bucket["settled"].append(net)
                    if net > 0:
                        bucket["gains"] += net
                    elif net < 0:
                        bucket["losses"] += -net
        for horizon, bucket in per_horizon.items():
            n_total = len(bucket["candidates"])
            settled = bucket["settled"]
            n_net = len(settled)
            avg_net = (sum(settled) / n_net) if n_net else 0.0
            row_key = (filter_name, stage, version, param_snap, family, direction, market_type, horizon)
            total, weighted = priors.get(row_key, (0, 0.0))
            priors[row_key] = (total + n_net, weighted + n_net * avg_net)
            aggregated.append({
                "filter_name": filter_name,
                "stage": stage,
                "filter_version": version,
                "parameter_snapshot": param_snap,
                "decision": decision,
                "scanner_family": family,
                "direction": direction,
                "market_type": market_type,
                "horizon": horizon,
                "n_total": n_total,
                "n_net": n_net,
                "n": n_net,
                "avg_net": round(avg_net, 6),
                "gains": bucket["gains"],
                "losses": bucket["losses"],
            })

    output: list[dict[str, Any]] = []
    for row in aggregated:
        prior_key = (
            row["filter_name"], row["stage"], row["filter_version"],
            row["parameter_snapshot"], row["scanner_family"], row["direction"],
            row["market_type"], row["horizon"],
        )
        prior_n, prior_sum = priors.get(prior_key, (0, 0.0))
        prior = prior_sum / prior_n if prior_n else 0.0
        n = row["n_net"]
        sample = row["avg_net"]
        gains = row.pop("gains", 0.0)
        losses = row.pop("losses", 0.0)
        row["outcome_coverage"] = round(n / row["n_total"], 4) if row["n_total"] else 0.0
        row["shrunk_avg_net"] = round(
            (n * sample + float(shrinkage_k) * prior) / (n + float(shrinkage_k)),
            6,
        )
        row["profit_factor"] = round(gains / losses, 4) if losses > 0 else None
        row["assignment_policy"] = "first_exposure"
        output.append(row)
    return output


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

    # Dual-decode: also pull scanner_weights_quota rows from the compressed
    # batch stream so the writer flip does not silently drop that filter's
    # observational signal. Legacy filter_decisions rows and new batch rows
    # co-exist during migration; both feed the same cohort grouping below.
    try:
        stats = list(stats) + _batch_scanner_weights_quota_stats(evaluated_since=evaluated_since)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("filter_controller: batch dual-decode failed: %s", exc)

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


def compute_persistent_recommendations(
    *,
    min_consecutive_runs: int = 3,
    max_runs_lookback: int = 10,
) -> list[PersistentRecommendation]:
    """Detect (filter × cell) keys stably flagged overrestrictive_suspect across runs.

    Reads the persisted controller_recommendations table and, for each
    (filter_name, scanner_family, direction, market_type, horizon, filter_version)
    cell, counts consecutive most-recent runs whose row for that cell has
    evidence_verdict='overrestrictive_suspect'. Cells with a streak of at least
    ``min_consecutive_runs`` are returned, sorted by streak length descending.

    A cell that does NOT appear in a run (implicit absence — e.g. dropped back to
    insufficient_data or the batch never emitted a row for that cell) breaks the
    streak. This keeps false-positives lower: the underlying pass/reject cohort
    must sustain enough evidence to trigger the suspect verdict every single run.

    Observe-only: never mutates any table. The result is bubbled through
    ``build_filter_controller_report`` to HTTP / CLI for operator review only.
    Not consumed by any A/B path — see PersistentRecommendation docstring.
    """
    from aletheia.storage.sqlite import get_db

    min_streak = max(1, int(min_consecutive_runs or 1))
    max_lookback = max(min_streak, int(max_runs_lookback or min_streak))

    db = get_db()

    # 1) The last N distinct run_ids, newest-first.
    run_rows = db.execute(
        "SELECT run_id, MAX(created_at) AS run_ts "
        "FROM controller_recommendations "
        "WHERE run_id IS NOT NULL "
        "GROUP BY run_id "
        "ORDER BY datetime(run_ts) DESC "
        "LIMIT ?",
        (max_lookback,),
    ).fetchall()
    if len(run_rows) < min_streak:
        return []
    ordered_run_ids: list[str] = [str(r["run_id"]) for r in run_rows]

    # 2) All rows for these runs, excluding the summary marker (which is not a cell).
    placeholders = ",".join("?" for _ in ordered_run_ids)
    rows = db.execute(
        f"SELECT id, run_id, created_at, filter_name, scanner_family, "
        f"direction, market_type, horizon, filter_version, "
        f"COALESCE(evidence_verdict, verdict) AS effective_verdict, "
        f"error_signal, reasoning "
        f"FROM controller_recommendations "
        f"WHERE run_id IN ({placeholders}) "
        f"AND filter_name <> '(insufficient_summary)'",
        tuple(ordered_run_ids),
    ).fetchall()

    # 3) Group by cell key; dedup within run defensively (keep newest by created_at).
    by_cell: dict[tuple, dict[str, dict]] = defaultdict(dict)
    for row in rows:
        cell_key = (
            str(row["filter_name"]),
            str(row["scanner_family"] or ""),
            row["direction"],
            row["market_type"],
            row["horizon"],
            row["filter_version"],
        )
        run_id = str(row["run_id"])
        existing = by_cell[cell_key].get(run_id)
        if existing is None or str(row["created_at"]) > str(existing["created_at"]):
            by_cell[cell_key][run_id] = dict(row)

    # 4) For each cell, walk newest-run → oldest and count consecutive suspects.
    persistent: list[PersistentRecommendation] = []
    for cell_key, rows_by_run in by_cell.items():
        consecutive = 0
        oldest_in_streak_run: str | None = None
        for run_id in ordered_run_ids:
            row = rows_by_run.get(run_id)
            if row is None:
                break
            if str(row.get("effective_verdict") or "") != "overrestrictive_suspect":
                break
            consecutive += 1
            oldest_in_streak_run = run_id
        if consecutive < min_streak:
            continue
        newest = rows_by_run[ordered_run_ids[0]]
        oldest = (
            rows_by_run[oldest_in_streak_run]
            if oldest_in_streak_run is not None
            else newest
        )
        error_signal = newest.get("error_signal")
        try:
            latest_error_signal: float | None = (
                float(error_signal) if error_signal is not None else None
            )
        except (TypeError, ValueError):
            latest_error_signal = None
        persistent.append(PersistentRecommendation(
            filter_name=cell_key[0],
            scanner_family=cell_key[1],
            direction=cell_key[2],
            market_type=cell_key[3],
            horizon=cell_key[4],
            filter_version=cell_key[5],
            consecutive_suspect_runs=consecutive,
            runs_analyzed=len(ordered_run_ids),
            first_suspect_at=str(oldest["created_at"]),
            last_suspect_at=str(newest["created_at"]),
            latest_error_signal=latest_error_signal,
            latest_reasoning=str(newest.get("reasoning") or ""),
            latest_run_id=str(newest["run_id"]),
        ))

    # Deterministic ordering: longest streak first, then largest |signal|, then key.
    persistent.sort(
        key=lambda p: (
            -p.consecutive_suspect_runs,
            -(abs(p.latest_error_signal) if p.latest_error_signal is not None else 0.0),
            p.filter_name,
            p.scanner_family,
        )
    )
    return persistent


def _is_fresh_prior_run(interval_hours: int) -> bool:
    """Return True when the most recent observe row is younger than half the cadence.

    The observe cycle is idempotent within a window: verdicts derive from
    multi-hour forward returns, so a manual bounce or misfire replay firing the
    job twice inside one interval only rewrites identical rows and inflates
    `controller_recommendations`. Half the cadence is the widest guard that still
    tolerates a legitimate late fire caused by scheduler drift.
    """
    if interval_hours <= 0:
        return False
    try:
        from aletheia.storage.sqlite import get_db

        row = get_db().execute(
            "SELECT created_at FROM controller_recommendations "
            "WHERE run_id IS NOT NULL ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    except Exception:  # pragma: no cover - defensive
        return False
    created_at = row["created_at"] if row else None
    if not created_at:
        return False
    try:
        latest_dt = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    except ValueError:
        return False
    if latest_dt.tzinfo is None:
        latest_dt = latest_dt.replace(tzinfo=timezone.utc)
    floor_dt = datetime.now(timezone.utc) - timedelta(hours=interval_hours * 0.5)
    return latest_dt >= floor_dt


def _empty_run_summary(
    min_samples: int,
    *,
    generated_at: str,
    skipped_reason: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "run_id": None,
        "generated_at": generated_at,
        "recommendations_written": 0,
        "verdict_counts": {},
        "min_samples": min_samples,
        "shadow_only": True,
        "experiments_registered": 0,
        "experiments_evaluated": 0,
        "experiment_registration": {
            "registered": 0, "already_registered": 0, "skipped": 0,
        },
        "experiment_evaluation": {
            "evaluated": 0, "ready_for_review": 0,
            "completed_no_support": 0, "invalidated": 0,
        },
    }
    if skipped_reason:
        payload["skipped_reason"] = skipped_reason
    return payload


def run_controller_observe(min_samples: int = _DEFAULT_MIN_SAMPLES) -> dict[str, Any]:
    """Compute recommendations and PERSIST them as append-only shadow rows.

    Returns a summary dict for the scheduler log. Writes only to
    controller_recommendations with status='shadow_only'. No config is touched.
    """
    generated_at = _now_iso()

    # Freshness gate — skip if the latest observe row is younger than half the
    # configured cadence. Prevents scheduler misfires + manual re-triggers from
    # doubling the append-only recommendation stream during one window.
    interval_hours = 0
    try:
        from aletheia.config.loader import load_config

        interval_hours = int(load_config().controller.observe_interval_hours or 0)
    except Exception as exc:  # pragma: no cover - defensive config path
        logger.warning("filter_controller: observe_interval_hours unavailable: %s", exc)
    if _is_fresh_prior_run(interval_hours):
        logger.info(
            "filter_controller observe: skipped — prior run inside %.1fh floor",
            interval_hours * 0.5,
        )
        return _empty_run_summary(
            min_samples,
            generated_at=generated_at,
            skipped_reason="fresh_prior_run",
        )

    run_id = uuid.uuid4().hex
    recs = compute_filter_feedback(min_samples=min_samples)

    written = 0
    skipped_insufficient = 0
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
                # Skip both-insufficient rows: when the evidence verdict itself is
                # insufficient_data, the legacy verdict is also insufficient_data
                # (no other legacy_verdict maps to it), so the row carries zero
                # observational signal. `overrestrictive_suspect` and `hold` still
                # persist unchanged — the coalesced summary row below records the
                # count for cadence tracking without inflating the append-only stream.
                if r.verdict == "insufficient_data":
                    skipped_insufficient += 1
                    continue
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
            if skipped_insufficient:
                # One summary row per run so operators still see the cell count
                # without recording N near-empty near-duplicate rows.
                repo.insert_controller_recommendation({
                    "id": uuid.uuid4().hex,
                    "created_at": generated_at,
                    "filter_name": "(insufficient_summary)",
                    "scanner_family": "(all)",
                    "stage": "(summary)",
                    "current_value": None,
                    "suggested_direction": "n/a",
                    "verdict": "insufficient_data",
                    "error_signal": None,
                    "n_samples": skipped_insufficient,
                    "reasoning": (
                        f"{skipped_insufficient} cells reported "
                        "insufficient_data; coalesced to a single row per run."
                    ),
                    "status": "shadow_only",
                    "run_id": run_id,
                    "evidence_verdict": "insufficient_data",
                    "evidence_direction": "n/a",
                    "filter_version": None,
                    "direction": None,
                    "market_type": None,
                    "horizon": None,
                    "passed_n": None,
                    "rejected_n": None,
                    "passed_shrunk_avg_net": None,
                    "rejected_shrunk_avg_net": None,
                    "outcome_coverage": None,
                })
                written += 1
    except Exception as e:  # pragma: no cover - defensive
        written = 0
        logger.warning("filter_controller: persist failed: %s", e)

    counts: dict[str, int] = {}
    for r in recs:
        counts[r.verdict] = counts.get(r.verdict, 0) + 1

    experiment_registration = {
        "registered": 0, "already_registered": 0, "skipped": 0,
    }
    experiment_evaluation = {
        "evaluated": 0, "ready_for_review": 0,
        "completed_no_support": 0, "invalidated": 0,
    }
    try:
        from aletheia.analytics.filter_replay import build_bounded_filter_replays
        from aletheia.analytics.filter_shadow_experiments import (
            evaluate_active_shadow_experiments,
            register_replay_ready_experiments,
        )

        replays = build_bounded_filter_replays(recs, min_samples=min_samples)
        experiment_registration = register_replay_ready_experiments(replays)
        experiment_evaluation = evaluate_active_shadow_experiments()
    except Exception as exc:  # pragma: no cover - defensive scheduler boundary
        logger.warning("filter_controller: shadow experiment cycle failed: %s", exc)

    logger.info(
        "filter_controller observe: run=%s written=%d verdicts=%s experiments=%s/%s",
        run_id, written, counts, experiment_registration, experiment_evaluation,
    )
    return {
        "run_id": run_id,
        "generated_at": generated_at,
        "recommendations_written": written,
        "verdict_counts": counts,
        "min_samples": min_samples,
        "shadow_only": True,
        "experiments_registered": experiment_registration["registered"],
        "experiments_evaluated": experiment_evaluation["evaluated"],
        "experiment_registration": experiment_registration,
        "experiment_evaluation": experiment_evaluation,
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
    try:
        from aletheia.analytics.filter_evidence import (
            filter_feedback_trajectory_summary,
            filter_version_status,
            observer_pre_filter_diagnostics,
        )

        trajectory_summary = filter_feedback_trajectory_summary(
            evaluated_since=evaluated_since
        )
        observer_diagnostics = observer_pre_filter_diagnostics(
            evaluated_since=evaluated_since
        )
        observer_version_status = filter_version_status("observer_pre_filter")
    except Exception as exc:
        logger.warning("filter_controller: trajectory summary unavailable: %s", exc)
        trajectory_summary = {
            "assignment_policy": "first_exposure",
            "candidate_trajectories": 0,
            "excluded_later_rows": 0,
            "decision_transition_candidates": 0,
            "error": type(exc).__name__,
        }
        observer_diagnostics = {
            "filter_name": "observer_pre_filter",
            "assignment_policy": "first_exposure",
            "read_only": True,
            "evaluated_since": evaluated_since,
            "row_count": 0,
            "cohort_count": 0,
            "cohorts": [],
            "error": type(exc).__name__,
        }
        observer_version_status = {
            "filter_name": "observer_pre_filter",
            "current_filter_version": None,
            "versions": [],
            "error": type(exc).__name__,
        }
    try:
        from aletheia.analytics.filter_replay import build_bounded_filter_replays

        bounded_replays = build_bounded_filter_replays(
            recs,
            evaluated_since=evaluated_since,
            min_samples=min_samples,
        )
    except Exception as exc:
        logger.warning("filter_controller: bounded replay unavailable: %s", exc)
        bounded_replays = []
    try:
        from aletheia.analytics.filter_shadow_experiments import (
            build_filter_shadow_experiment_report,
        )

        shadow_experiments = build_filter_shadow_experiment_report()
    except Exception as exc:
        logger.warning("filter_controller: shadow experiment report unavailable: %s", exc)
        shadow_experiments = []
    counts: dict[str, int] = {}
    for r in recs:
        counts[r.verdict] = counts.get(r.verdict, 0) + 1
    # Cross-run stability signal (observe-only). Read config knobs from
    # ControllerConfig with defensive defaults for offline / test environments.
    try:
        from aletheia.config.loader import load_config
        _cfg = load_config().controller
        _min_streak = int(getattr(_cfg, "persistent_min_consecutive_runs", 3))
        _max_lookback = int(getattr(_cfg, "persistent_max_runs_lookback", 10))
    except Exception:
        _min_streak = 3
        _max_lookback = 10
    try:
        persistent = compute_persistent_recommendations(
            min_consecutive_runs=_min_streak,
            max_runs_lookback=_max_lookback,
        )
    except Exception as exc:
        logger.warning("filter_controller: persistent stability signal unavailable: %s", exc)
        persistent = []
    # A bounded current-window report may be immature at 24h even when the same
    # exact-version cell has been persistently suspect across scheduled runs.
    # Surface its read-only replay without turning the persistent signal into an
    # experiment or production mutation.
    stale_persistent: list[dict[str, Any]] = []
    try:
        from aletheia.analytics.filter_replay import (
            build_observer_pre_filter_replay,
        )

        current_observer_version = observer_version_status.get(
            "current_filter_version"
        )
        replayed_scopes = {
            (
                replay.get("filter_name"),
                replay.get("filter_version"),
                replay.get("scanner_family"),
                replay.get("direction"),
                replay.get("market_type"),
            )
            for replay in bounded_replays
        }
        observer_replay_count = sum(
            replay.get("filter_name") == "observer_pre_filter"
            for replay in bounded_replays
        )
        for item in persistent:
            scope = (
                item.filter_name,
                item.filter_version,
                item.scanner_family,
                item.direction,
                item.market_type,
            )
            if (
                item.filter_name != "observer_pre_filter"
                or item.horizon != "24h"
                or not item.filter_version
                or not item.direction
                or not item.market_type
                or scope in replayed_scopes
                or len(bounded_replays) >= 8
                or observer_replay_count >= 3
            ):
                continue
            if item.filter_version != current_observer_version:
                stale_persistent.append({
                    **asdict(item),
                    "current_filter_version": current_observer_version,
                    "replay_blocked_reason": "production_filter_version_changed",
                })
                continue
            bounded_replays.append(build_observer_pre_filter_replay(
                filter_version=item.filter_version,
                scanner_family=item.scanner_family,
                direction=item.direction,
                market_type=item.market_type,
                evaluated_since=evaluated_since,
            ))
            replayed_scopes.add(scope)
            observer_replay_count += 1
    except Exception as exc:
        logger.warning("filter_controller: persistent Observer replay unavailable: %s", exc)
    replay_summary = _summarize_replays(bounded_replays)
    return {
        "generated_at": _now_iso(),
        "window": {"hours": hours_i, "evaluated_since": evaluated_since},
        "min_samples": min_samples,
        "shadow_only": True,
        "signal_basis": "filter_specific_shrunk_net_expectancy",
        "signal_note": (
            "Only exact filter-version pass/reject cohorts with cost-aware net outcomes "
            "are compared. Each candidate is assigned by its first persisted exposure; "
            "later retries are excluded. Findings are observational and never mutate configuration."
        ),
        "trajectory_summary": trajectory_summary,
        "observer_pre_filter_diagnostics": observer_diagnostics,
        "observer_pre_filter_version_status": observer_version_status,
        "stale_persistent_recommendations": stale_persistent,
        "bounded_replays": bounded_replays,
        "replay_summary": replay_summary,
        "shadow_experiments": shadow_experiments,
        "verdict_counts": counts,
        "recommendations": [asdict(r) for r in recs],
        "persistent_recommendations": [asdict(p) for p in persistent],
        "persistent_config": {
            "min_consecutive_runs": _min_streak,
            "max_runs_lookback": _max_lookback,
        },
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
    trajectory = report.get("trajectory_summary") or {}
    lines.append(
        "Cohort assignment: first exposure; "
        f"later retries excluded={trajectory.get('excluded_later_rows', 0)}; "
        f"decision transitions={trajectory.get('decision_transition_candidates', 0)}"
    )
    vc = report.get("verdict_counts") or {}
    lines.append(
        f"Verdicts: overrestrictive_suspect={vc.get('overrestrictive_suspect',0)} "
        f"hold={vc.get('hold',0)} insufficient_data={vc.get('insufficient_data',0)}"
    )
    observer_diagnostics = report.get("observer_pre_filter_diagnostics") or {}
    observer_version = report.get("observer_pre_filter_version_status") or {}
    observer_rows = [
        row
        for row in (observer_diagnostics.get("cohorts") or [])
        if row.get("horizon") == "24h"
        and int(row.get("n_net") or 0) >= int(report.get("min_samples") or 0)
    ]
    lines.append(
        "Observer rule diagnostics: "
        f"rows={observer_diagnostics.get('row_count', 0)} "
        f"cohorts={observer_diagnostics.get('cohort_count', 0)} "
        "(first exposure; read-only)"
    )
    lines.append(
        "Observer production filter version: "
        f"{observer_version.get('current_filter_version') or 'not available'}; "
        "older-version suspects cannot authorize replay or experiment"
    )
    stale_persistent = report.get("stale_persistent_recommendations") or []
    if stale_persistent:
        lines.append(
            f"Observer stale persistent suspects blocked: {len(stale_persistent)} "
            "(production_filter_version_changed)"
        )
    persistent = report.get("persistent_recommendations") or []
    persistent_cfg = report.get("persistent_config") or {}
    _min_streak = int(persistent_cfg.get("min_consecutive_runs", 3) or 3)
    _max_lookback = int(persistent_cfg.get("max_runs_lookback", 10) or 10)
    lines.append(
        f"Persistent (>= {_min_streak} of last {_max_lookback} consecutive runs): "
        f"{len(persistent)} cell(s) — human review"
    )
    replay_summary = report.get("replay_summary") or {}
    lines.append(
        f"Replay disposition: experiment_ready={replay_summary.get('experiment_ready', 0)} "
        f"no_support={replay_summary.get('no_support', 0)} "
        f"insufficient_data={replay_summary.get('insufficient_data', 0)}"
    )
    lines.append("")

    if observer_rows:
        lines.append("Observer 24h matched-rule cohorts (diagnostic; NOT applied):")
        for row in sorted(
            observer_rows,
            key=lambda item: float(item.get("shrunk_avg_net_pct") or 0.0),
        )[:12]:
            lines.append(
                f"  {row.get('scanner_family')} {row.get('direction')} "
                f"{row.get('market_type')} · {row.get('decision')} "
                f"{row.get('rule_cohort')} "
                f"RSI={row.get('rsi_deviation_bucket')} "
                f"ATR={row.get('atr_pct_bucket')} "
                f"n={row.get('n_net')}/{row.get('n_total')} "
                f"coverage={float(row.get('outcome_coverage') or 0):.0%} "
                f"shrunk_net={row.get('shrunk_avg_net_pct')}%"
            )
        lines.append("")

    recs = report.get("recommendations") or []
    actionable = [r for r in recs if r["verdict"] == "overrestrictive_suspect"]
    if actionable:
        lines.append("Observational screening suspects (replay required; NOT applied):")
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

    if persistent:
        lines.append(
            f"Persistent overrestrictive suspects "
            f"(>= {_min_streak} consecutive runs — priority for human review):"
        )
        for p in persistent:
            cell_parts = [
                str(p.get("direction") or "-"),
                str(p.get("market_type") or "-"),
                str(p.get("horizon") or "-"),
            ]
            signal_text = (
                f" signal={p['latest_error_signal']:+.4f}"
                if p.get("latest_error_signal") is not None
                else ""
            )
            version_text = (
                f" v={p['filter_version']}"
                if p.get("filter_version") else ""
            )
            lines.append(
                f"  {p['filter_name']} · {p['scanner_family']} "
                f"[{', '.join(cell_parts)}]{version_text} "
                f"streak={p['consecutive_suspect_runs']}/{p['runs_analyzed']}"
                f"{signal_text}"
            )
            reasoning = str(p.get("latest_reasoning") or "").strip()
            if reasoning:
                lines.append(f"      {reasoning}")
        lines.append("")

    replays = report.get("bounded_replays") or []
    if replays:
        lines.append("Bounded gate-only replays (read-only; downstream gates not replayed):")
        for replay in replays:
            if replay.get("filter_name") == "observer_pre_filter":
                lines.append(
                    f"  observer_pre_filter · {replay.get('scanner_family')} "
                    f"{replay.get('direction')} {replay.get('market_type')} "
                    f"version={replay.get('filter_version')}"
                )
                for alternative in replay.get("alternatives") or []:
                    primary = (
                        (alternative.get("horizon_metrics") or {})
                        .get("24h", {})
                        .get("treatment_union", {})
                    )
                    admitted = (
                        (alternative.get("horizon_metrics") or {})
                        .get("24h", {})
                        .get("newly_admitted", {})
                    )
                    lines.append(
                        "    "
                        f"RSI>{alternative.get('rsi_deviation')} OR "
                        f"ATR>{alternative.get('atr_pct')}: "
                        f"new={alternative.get('newly_admitted_candidates')} "
                        f"settled={admitted.get('settled_n')} "
                        f"coverage={float(admitted.get('outcome_coverage') or 0):.0%} "
                        f"union_delta={primary.get('delta_vs_control_pct')}pp "
                        f"lcb={alternative.get('bootstrap_delta_lcb_pct')}pp "
                        f"readiness={alternative.get('readiness')}"
                    )
                continue
            lines.append(
                f"  {replay.get('filter_name')} · {replay.get('scanner_family')} "
                f"{replay.get('direction')} {replay.get('market_type')} "
                f"{replay.get('horizon')} version={replay.get('filter_version')}"
            )
            for alternative in replay.get("alternatives") or []:
                lines.append(
                    f"    {replay.get('current_ttl_sec')}s -> {alternative.get('ttl_sec')}s: "
                    f"new={alternative.get('newly_admitted_candidates')} "
                    f"settled={alternative.get('settled_n')} "
                    f"coverage={float(alternative.get('outcome_coverage') or 0):.0%} "
                    f"raw_net={alternative.get('raw_avg_net_pct')}% "
                    f"shrunk_net={alternative.get('shrunk_avg_net_pct')}% "
                    f"readiness={alternative.get('readiness')}"
                )
            if replay.get("candidate_cap_reached"):
                lines.append("    candidate cap reached; widen only in an explicit offline analysis run")
        lines.append("")

    experiments = report.get("shadow_experiments") or []
    if experiments:
        lines.append("Prospective shadow experiments (production unchanged):")
        for experiment in experiments:
            control = experiment.get("control_parameter") or {}
            treatment = experiment.get("treatment_parameter") or {}
            latest = experiment.get("latest_evaluation") or {}
            lines.append(
                f"  {experiment.get('filter_name')} · {experiment.get('scanner_family')} "
                f"{experiment.get('direction')} {experiment.get('market_type')} "
                f"{experiment.get('horizon')} "
                f"{control.get('ttl_sec')}s -> {treatment.get('ttl_sec')}s "
                f"status={experiment.get('status')}"
            )
            lines.append(
                "    prospective first exposures only; "
                f"control settled={latest.get('control_settled_n', 0)}; "
                f"treatment settled={latest.get('treatment_settled_n', 0)}; "
                f"disposition={latest.get('disposition', 'not_evaluated')}"
            )
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
    active_experiments = [
        row for row in experiments if row.get("status") == "collecting"
    ]
    review_experiments = [
        row for row in experiments if row.get("status") == "ready_for_review"
    ]
    if review_experiments:
        lines.append(
            "  Review prospective evidence manually; production parameters remain unchanged."
        )
    elif active_experiments:
        lines.append(
            "  Keep production parameters unchanged; collect future first-exposure "
            "control and treatment outcomes."
        )
    elif replays and replay_summary.get("experiment_ready", 0) == 0:
        lines.append(
            "  Keep filter parameters unchanged; bounded replays found no cohort "
            "ready for a controlled experiment."
        )
    elif replay_summary.get("experiment_ready", 0) > 0:
        lines.append(
            "  Keep production parameters unchanged; design a separate shadow experiment "
            "only for replay-ready cohorts."
        )
    else:
        lines.append("  Keep filter parameters unchanged; collect independent evidence or "
                     "run a bounded replay before changing a threshold.")
    return "\n".join(lines)


def _summarize_replays(replays: list[dict[str, Any]]) -> dict[str, int]:
    summary = {
        "experiment_ready": 0,
        "no_support": 0,
        "insufficient_data": 0,
    }
    for replay in replays:
        readiness = {
            str(row.get("readiness") or "insufficient_data")
            for row in replay.get("alternatives") or []
        }
        if (
            "ready_for_controlled_experiment" in readiness
            or "ready_for_prospective_shadow" in readiness
        ):
            summary["experiment_ready"] += 1
        elif "no_support" in readiness:
            summary["no_support"] += 1
        else:
            summary["insufficient_data"] += 1
    return summary
