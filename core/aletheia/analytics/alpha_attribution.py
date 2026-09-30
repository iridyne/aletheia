"""Read-only Alpha Attribution Audit.

This module answers one question: which layer appears to add or destroy alpha?
It does not write to the database, call agents, submit orders, change risk, or
modify ranker/System Operator behavior.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Any

from aletheia.utils.dt import parse_utc
from aletheia.analytics.learning_window import outcome_window_metadata
from aletheia.analytics.outcome_semantics import assess_candidate_outcome
from aletheia.storage.repositories import (
    SHADOW_OUTCOME_ENTRY_ALIGNMENT_MAX_SEC,
    complete_outcome_economics_sql,
)
from aletheia.storage.time_utils import utcnow as _utcnow

VERDICT_NAMES = (
    "Universe Alpha",
    "Ranker Alpha",
    "System Operator Alpha",
    "Learning Alpha",
    "Serenity Alpha",
)

MIN_SAMPLE_SIZE = 5
ALPHA_THRESHOLD_PCT = 0.10

_MODULES_BY_LAYER = {
    "Universe Alpha": [
        "core/aletheia/universe/scanners.py",
        "core/aletheia/universe/allocator.py",
        "core/aletheia/universe/filters.py",
        "core/aletheia/universe/service.py",
    ],
    "System Operator Alpha": [
        "core/aletheia/agent_shell/trading_context.py",
        "core/aletheia/agent_runtime/pi_daemon.py",
    ],
    "Learning Alpha": [
        "extensions/learning/lessons.py",
        "extensions/analytics/memo_effectiveness.py",
        "extensions/knowledge/guards.py",
        "extensions/analytics/guard_effectiveness.py",
    ],
    "Serenity Alpha": [
        "extensions/research/research_worker.py",
        "core/aletheia/trading/serenity_scoring.py",
        "core/aletheia/trading/serenity_evidence.py",
    ],
}

_HORIZON_SECONDS = {
    "1h": 3600,
    "4h": 4 * 3600,
    "24h": 24 * 3600,
    "1d": 24 * 3600,
    "3d": 3 * 24 * 3600,
    "7d": 7 * 24 * 3600,
}


def build_alpha_attribution_audit(days: int = 30, min_sample_size: int = MIN_SAMPLE_SIZE) -> dict:
    """Build the five-layer alpha attribution report.

    Returns a JSON-serializable dict. Every layer has a verdict, baseline,
    sample_size, delta, confidence_label, and reason. ``insufficient_data`` is a
    first-class verdict because forcing positive/neutral/negative from thin data
    would create false precision.
    """
    import aletheia.storage.sqlite as sqlite_store

    days = _normalize_days(days)
    now = _utcnow()
    generated_at = now.isoformat(timespec="seconds")
    cutoff = (now - timedelta(days=days)).isoformat()

    if getattr(sqlite_store, "_db_conn", None) is None and not sqlite_store.db_path().exists():
        return _empty_report(generated_at, days, "Database is not initialized; no alpha evidence is available.")

    try:
        db = sqlite_store.get_db()
    except sqlite3.OperationalError:
        return _empty_report(generated_at, days, "Database could not be opened; no alpha evidence is available.")

    verdicts = {
        "universe": _safe_layer(
            "Universe Alpha",
            lambda: _universe_alpha(db, cutoff, generated_at, min_sample_size),
        ),
        "ranker": _safe_layer(
            "Ranker Alpha",
            lambda: _ranker_alpha(db, cutoff, generated_at, min_sample_size),
        ),
        "system_operator": _safe_layer(
            "System Operator Alpha",
            lambda: _system_operator_alpha(db, cutoff, generated_at, min_sample_size),
        ),
        "learning": _safe_layer("Learning Alpha", lambda: _learning_alpha(cutoff)),
        "serenity": _safe_layer(
            "Serenity Alpha",
            lambda: _serenity_alpha(db, cutoff, generated_at, min_sample_size),
        ),
    }

    return {
        "generated_at": generated_at,
        "window_days": days,
        **outcome_window_metadata(),
        "verdicts": verdicts,
        "universe_twin_evidence": _universe_twin_evidence(db, cutoff),
        "universe_relative_rank_evidence": _universe_relative_rank_evidence(db, cutoff),
        "exit_paired_evidence": _exit_paired_evidence(cutoff),
        "exit_trailing_qualification": _exit_trailing_qualification(cutoff),
        "main_bottleneck": _main_bottleneck(verdicts),
    }


def _empty_report(generated_at: str, days: int, reason: str) -> dict:
    verdicts = {
        "universe": _empty_layer("Universe Alpha", reason),
        "ranker": _empty_layer("Ranker Alpha", reason),
        "system_operator": _empty_layer("System Operator Alpha", reason),
        "learning": _empty_layer("Learning Alpha", reason),
        "serenity": _empty_layer("Serenity Alpha", reason),
    }
    return {
        "generated_at": generated_at,
        "window_days": days,
        **outcome_window_metadata(),
        "verdicts": verdicts,
        "universe_twin_evidence": _empty_universe_twin_evidence(reason),
        "universe_relative_rank_evidence": _empty_relative_rank_evidence(reason),
        "exit_paired_evidence": _empty_exit_paired_evidence(reason),
        "exit_trailing_qualification": _empty_exit_trailing_qualification(reason),
        "main_bottleneck": _main_bottleneck(verdicts),
    }


def _safe_layer(name: str, fn) -> dict:
    try:
        return fn()
    except sqlite3.OperationalError as exc:
        return _empty_layer(name, f"Required alpha evidence table is unavailable: {exc}")


def _empty_layer(name: str, reason: str) -> dict:
    return _verdict(
        name,
        "insufficient_data",
        0,
        None,
        None,
        None,
        "insufficient",
        reason,
        evidence_type="none",
        causal_grade="insufficient",
    )


def _empty_exit_paired_evidence(reason: str) -> dict:
    return {
        "contract_version": "exit-paired-evidence/v2",
        "experiment_version": "exit-breakeven-shadow/v1",
        "treatment_id": "entry-lock-after-1.5pct/v1",
        "cost_model_version": "paper-fill-close-bar-v1",
        "behavior_authority": False,
        "promotion_eligible": False,
        "contribution_verdict": "insufficient_data",
        "measurement_status": "not_measured",
        "evidence_type": "prospective_paired_shadow",
        "causal_grade": "insufficient",
        "statistical_unit": "position_id",
        "economic_unit": "net_pnl_quote_currency",
        "outcome_count": 0,
        "available_count": 0,
        "unavailable_count": 0,
        "unsettled_count": 0,
        "outcome_coverage": None,
        "changed_count": 0,
        "incremental_net_pnl_total": None,
        "incremental_net_pnl_mean": None,
        "incremental_net_pnl_mean_ci_95": None,
        "bootstrap_method": "position_percentile_bootstrap/v1",
        "bootstrap_seed": 20260810,
        "bootstrap_replicates": 0,
        "bootstrap_max_draws": 2_000_000,
        "positive_increment_count": 0,
        "close_reason_breakdown": [],
        "close_reason_attribution_available": False,
        "reason": reason,
    }


def _empty_exit_trailing_qualification(reason: str) -> dict:
    from aletheia.analytics.exit_trailing_qualification import (
        _TRAILING_QUALIFICATION_VERSION,
        _TRAILING_RECOVERY_THRESHOLD_PCT,
        _TRAILING_RECOVERY_WINDOW_BARS,
    )

    return {
        "contract_version": "exit-trailing-qualification/v2",
        "policy_version": _TRAILING_QUALIFICATION_VERSION,
        "enrollment_enabled": False,
        "enrollment_block_reason": "archived",
        "measurement_status": "not_measured",
        "causal_grade": "insufficient",
        "assignment_count": 0,
        "treatment_assignment_count": 0,
        "control_assignment_count": 0,
        "closed_count": 0,
        "available_count": 0,
        "unavailable_count": 0,
        "changed_count": 0,
        "cohort_count": 0,
        "cohorts": [],
        "cross_cohort_pooling": False,
        "recovery_contract": {
            "contract_version": "post-exit-recovery/v1",
            "observation": "complete_post_exit_1m_bars",
            "window_bars": _TRAILING_RECOVERY_WINDOW_BARS,
            "threshold_pct": _TRAILING_RECOVERY_THRESHOLD_PCT,
            "authority": "diagnostic_only",
        },
        "recovery_available_count": 0,
        "recovery_pending_count": 0,
        "recovery_unavailable_count": 0,
        "recovery_truncation_count": 0,
        "recovery_truncation_rate": None,
        "recovery_truncation_reason": "no_mature_treatment_changing_close",
        "promotion_eligible": False,
        "reason": reason,
    }


def _empty_universe_twin_evidence(reason: str) -> dict:
    return {
        "contract_version": "universe-twin-evidence/v1",
        "authority": "shadow_only",
        "behavior_authority": False,
        "promotion_eligible": False,
        "contribution_verdict": "insufficient_data",
        "measurement_status": "not_measured",
        "evidence_type": "prospective_randomized_cluster_shadow",
        "causal_grade": "insufficient",
        "statistical_unit": "scanner_run_id",
        "economic_unit": "cluster_mean_assigned_net_return_pct_unselected_zero",
        "policy_epoch_id": None,
        "expected_cluster_n": 0,
        "complete_cluster_n": 0,
        "treatment_cluster_n": 0,
        "control_cluster_n": 0,
        "observed_cluster_utility_delta": None,
        "window_scope": "exact_policy_epoch",
        "window_start": None,
        "window_end": None,
        "audit_window_compatible": None,
        "verdict": "insufficient_data",
        "blocking_reasons": [],
        "invalid_reasons": [],
        "reason": reason,
    }


def _universe_twin_evidence(db, cutoff: str) -> dict:
    try:
        latest = db.execute(
            "SELECT policy_epoch_id FROM universe_twin_shadow_batches "
            "WHERE experiment_id IS NOT NULL AND policy_epoch_id IS NOT NULL "
            "AND datetime(captured_at) IS NOT NULL "
            "AND datetime(captured_at) >= datetime(?) "
            "ORDER BY datetime(captured_at) DESC, scanner_run_id DESC LIMIT 1",
            (cutoff,),
        ).fetchone()
    except sqlite3.DatabaseError as exc:
        return _empty_universe_twin_evidence(
            f"Universe Twin evidence storage is unavailable: {type(exc).__name__}."
        )
    if latest is None:
        return _empty_universe_twin_evidence(
            "No prospective Universe Twin policy epoch is available in the audit window."
        )

    policy_epoch_id = str(latest["policy_epoch_id"])
    try:
        bounds = db.execute(
            "SELECT MIN(captured_at) AS window_start, MAX(captured_at) AS window_end "
            "FROM universe_twin_shadow_batches WHERE policy_epoch_id=?",
            (policy_epoch_id,),
        ).fetchone()
    except sqlite3.DatabaseError as exc:
        return {
            **_empty_universe_twin_evidence(
                f"Universe Twin epoch bounds are unavailable: {type(exc).__name__}."
            ),
            "policy_epoch_id": policy_epoch_id,
        }
    window_start = bounds["window_start"] if bounds else None
    window_end = bounds["window_end"] if bounds else None
    start_at = parse_utc(window_start)
    end_at = parse_utc(window_end)
    cutoff_at = parse_utc(cutoff)
    if (
        start_at is None
        or end_at is None
        or cutoff_at is None
        or start_at < cutoff_at
        or end_at < start_at
    ):
        return {
            **_empty_universe_twin_evidence(
                "The exact Universe Twin epoch falls outside or violates the requested "
                "audit window and is not truncated or pooled."
            ),
            "policy_epoch_id": policy_epoch_id,
            "window_start": window_start,
            "window_end": window_end,
            "audit_window_compatible": False,
        }
    try:
        from extensions.analytics.twin_evaluation import evaluate_twin_shadow_epoch

        report = evaluate_twin_shadow_epoch(policy_epoch_id)
        complete_cluster_n = int(report.get("complete_cluster_n") or 0)
    except Exception as exc:
        return {
            **_empty_universe_twin_evidence(
                f"Universe Twin evaluation is unavailable: {type(exc).__name__}."
            ),
            "policy_epoch_id": policy_epoch_id,
            "window_start": window_start,
            "window_end": window_end,
            "audit_window_compatible": True,
        }

    if report.get("verdict") == "invalid":
        measurement_status = "invalid_evidence"
        causal_grade = "insufficient"
        reason = (
            "The exact Universe Twin epoch failed its integrity contract; it cannot "
            "grant contribution authority."
        )
    elif complete_cluster_n > 0:
        measurement_status = "prospective_shadow_diagnostic"
        causal_grade = "prospective_cluster_shadow_only"
        reason = (
            "Universe Twin scanner-cluster outcomes are projected into Alpha Audit, "
            "but the frozen experiment is Shadow-only and cannot grant contribution "
            "authority or promotion."
        )
    else:
        measurement_status = "not_measured"
        causal_grade = "prospective_cluster_shadow_only"
        reason = (
            "Prospective Universe Twin clusters exist, but no complete fixed-horizon "
            "scanner cluster is available yet."
        )
    return {
        **report,
        "contract_version": "universe-twin-evidence/v1",
        "behavior_authority": False,
        "promotion_eligible": False,
        "contribution_verdict": "insufficient_data",
        "measurement_status": measurement_status,
        "evidence_type": "prospective_randomized_cluster_shadow",
        "causal_grade": causal_grade,
        "statistical_unit": "scanner_run_id",
        "economic_unit": "cluster_mean_assigned_net_return_pct_unselected_zero",
        "window_scope": "exact_policy_epoch",
        "window_start": window_start,
        "window_end": window_end,
        "audit_window_compatible": True,
        "reason": reason,
    }


def _empty_relative_rank_evidence(reason: str) -> dict:
    return {
        **_empty_universe_twin_evidence(reason),
        "contract_version": "universe-relative-rank-evidence/v1",
    }


def _universe_relative_rank_evidence(db, cutoff: str) -> dict:
    try:
        latest = db.execute(
            "SELECT policy_epoch_id FROM universe_relative_rank_shadow_batches "
            "WHERE datetime(captured_at) >= datetime(?) "
            "ORDER BY datetime(captured_at) DESC LIMIT 1",
            (cutoff,),
        ).fetchone()
    except sqlite3.DatabaseError as exc:
        return _empty_relative_rank_evidence(
            f"Relative-rank evidence storage is unavailable: {type(exc).__name__}."
        )
    if latest is None:
        return _empty_relative_rank_evidence(
            "No prospective relative-rank policy epoch is available in the audit window."
        )
    policy_epoch_id = str(latest["policy_epoch_id"])
    bounds = db.execute(
        "SELECT MIN(captured_at) AS window_start, MAX(captured_at) AS window_end "
        "FROM universe_relative_rank_shadow_batches WHERE policy_epoch_id=?",
        (policy_epoch_id,),
    ).fetchone()
    window_start = bounds["window_start"] if bounds else None
    window_end = bounds["window_end"] if bounds else None
    start_at = parse_utc(window_start)
    end_at = parse_utc(window_end)
    cutoff_at = parse_utc(cutoff)
    if (
        start_at is None
        or end_at is None
        or cutoff_at is None
        or start_at < cutoff_at
        or end_at < start_at
    ):
        return {
            **_empty_relative_rank_evidence(
                "The exact relative-rank epoch falls outside the requested audit window."
            ),
            "policy_epoch_id": policy_epoch_id,
            "window_start": window_start,
            "window_end": window_end,
            "audit_window_compatible": False,
        }
    try:
        from aletheia.universe.relative_rank_evaluation import (
            evaluate_relative_rank_shadow_epoch,
        )

        report = evaluate_relative_rank_shadow_epoch(policy_epoch_id)
    except Exception as exc:
        return {
            **_empty_relative_rank_evidence(
                f"Relative-rank evaluation is unavailable: {type(exc).__name__}."
            ),
            "policy_epoch_id": policy_epoch_id,
            "window_start": window_start,
            "window_end": window_end,
            "audit_window_compatible": True,
        }
    complete = int(report.get("complete_cluster_n") or 0)
    return {
        **report,
        "contract_version": "universe-relative-rank-evidence/v1",
        "behavior_authority": False,
        "contribution_verdict": "insufficient_data",
        "measurement_status": (
            "invalid_evidence"
            if report.get("verdict") == "invalid"
            else "prospective_shadow_diagnostic" if complete else "not_measured"
        ),
        "evidence_type": "prospective_randomized_cluster_shadow",
        "causal_grade": "prospective_cluster_shadow_only",
        "statistical_unit": "scanner_run_id",
        "economic_unit": "cluster_mean_assigned_net_return_pct_unselected_zero",
        "window_scope": "exact_policy_epoch",
        "window_start": window_start,
        "window_end": window_end,
        "audit_window_compatible": True,
        "reason": (
            "Relative-rank evidence is diagnostic only; Step-v1 remains authoritative "
            "and promotion is not authorized."
        ),
    }


def _exit_paired_evidence(cutoff: str) -> dict:
    from aletheia.analytics.exit_breakeven_shadow import (
        build_exit_breakeven_shadow_report,
    )

    try:
        report = build_exit_breakeven_shadow_report(closed_after=cutoff)
    except sqlite3.OperationalError as exc:
        return _empty_exit_paired_evidence(
            f"Required Exit paired evidence table is unavailable: {exc}"
        )
    if not report["available_count"]:
        return {
            **_empty_exit_paired_evidence(
                "No settled, identity-complete Exit paired outcomes are available in the audit window."
            ),
            **report,
        }
    return {
        "contract_version": "exit-paired-evidence/v2",
        **report,
        "contribution_verdict": "insufficient_data",
        "measurement_status": "treatment_specific_paired_evidence",
        "evidence_type": "prospective_paired_shadow",
        "causal_grade": "strong_prospective_paired_shadow",
        "statistical_unit": "position_id",
        "economic_unit": "net_pnl_quote_currency",
        "reason": (
            "This estimates one frozen entry-lock treatment for eligible Paper positions; "
            "it does not establish generic Exit-module contribution or authorize behavior changes."
        ),
    }


def _exit_trailing_qualification(cutoff: str) -> dict:
    from aletheia.analytics.exit_trailing_qualification import (
        build_exit_trailing_qualification_report,
    )

    try:
        report = build_exit_trailing_qualification_report(opened_after=cutoff)
    except sqlite3.OperationalError as exc:
        return _empty_exit_trailing_qualification(
            f"Required ExitPlan evidence table is unavailable: {exc}"
        )
    if not report["assignment_count"]:
        return {
            **_empty_exit_trailing_qualification(
                "No prospective dynamic-trailing treatment/control assignments are available in the audit window."
            ),
            **report,
        }
    return {
        **report,
        "measurement_status": (
            "prospective_randomized_holdout"
            if report["available_count"] else "prospective_assignments_only"
        ),
        "causal_grade": "prospective_position_randomized_holdout",
        "reason": (
            "Exact-build treatment/control outcomes are descriptive until a separate "
            "fixed qualification gate is registered and satisfied."
        ),
    }


def render_alpha_audit(report: dict) -> str:
    """Render the audit in the owner-facing CLI format."""
    lines = ["Alpha Attribution Audit", "", f"Window: {report.get('window_days')}d", ""]
    ordered = [
        ("Universe", "universe"),
        ("Ranker (historical)", "ranker"),
        ("System Operator", "system_operator"),
        ("Learning", "learning"),
        ("Serenity", "serenity"),
    ]
    for label, key in ordered:
        item = report["verdicts"][key]
        lines.append(f"{label}: {item['verdict']}")
        lines.append(
            "  "
            f"sample_size={item['sample_size']} "
            f"baseline={_fmt_metric(item.get('baseline'))} "
            f"observed={_fmt_metric(item.get('observed'))} "
            f"delta={_fmt_metric(item.get('delta'))} "
            f"confidence={item['confidence_label']} "
            f"evidence={item.get('causal_grade', 'unknown')} "
            f"contribution={item.get('contribution_verdict', 'insufficient_data')}"
        )
        lines.append(f"  {item['reason']}")
        if item.get("evidence_type") == "dedicated_learning_contribution_monitoring":
            details = item.get("details") or {}
            if details.get("experiment_id"):
                lines.append(
                    "  Learning experiment: "
                    f"id={details['experiment_id']} stage={details.get('stage')} "
                    f"raw_exposures={details.get('raw_exposures', 0)} "
                    "exposed_interventions="
                    f"{details.get('exposed_actual_intervention_treatment_units', 0)} "
                    "settled_interventions="
                    f"{details.get('actual_intervention_treatment_units', 0)}"
                )
                lines.append(
                    "  Learning window: "
                    f"{details.get('window_start')} -> {details.get('window_end')} "
                    "exclusions="
                    f"{json.dumps(details.get('exclusion_reasons') or {}, sort_keys=True)}"
                )
                lines.append(
                    "  Learning selection: "
                    f"treatment={_fmt_metric(details.get('treatment_selected_members'))}/"
                    f"{_fmt_metric(details.get('treatment_members'))} "
                    f"control={_fmt_metric(details.get('control_selected_members'))}/"
                    f"{_fmt_metric(details.get('control_members'))}"
                )
        modules = item.get("suspected_modules") or []
        if (
            item.get("contribution_verdict") == "negative"
            and item.get("measurement_status") == "measured_contribution"
            and modules
        ):
            lines.append(f"  Measured suspect modules: {', '.join(modules[:3])}")
    twin = report.get("universe_twin_evidence") or {}
    lines.extend([
        "",
        "Universe Twin evidence: "
        f"{twin.get('measurement_status', 'not_measured')}",
        "  "
        f"complete_clusters={twin.get('complete_cluster_n', 0)}/"
        f"{twin.get('expected_cluster_n', 0)} "
        f"treatment={twin.get('treatment_cluster_n', 0)} "
        f"control={twin.get('control_cluster_n', 0)} "
        f"delta={_fmt_metric(twin.get('observed_cluster_utility_delta'))} "
        f"contribution={twin.get('contribution_verdict', 'insufficient_data')}",
        f"  {twin.get('reason', 'Universe Twin evidence is unavailable.')}",
    ])
    relative = report.get("universe_relative_rank_evidence") or {}
    lines.extend([
        "",
        "Universe relative-rank evidence: "
        f"{relative.get('measurement_status', 'not_measured')}",
        "  "
        f"complete_clusters={relative.get('complete_cluster_n', 0)}/"
        f"{relative.get('expected_cluster_n', 0)} "
        f"treatment={relative.get('treatment_cluster_n', 0)} "
        f"control={relative.get('control_cluster_n', 0)} "
        f"delta={_fmt_metric(relative.get('observed_cluster_utility_delta'))}",
        f"  {relative.get('reason', 'Relative-rank evidence is unavailable.')}",
    ])
    paired = report.get("exit_paired_evidence") or {}
    lines.extend([
        "",
        f"Exit paired evidence: {paired.get('measurement_status', 'not_measured')}",
        "  "
        f"available={paired.get('available_count', 0)} "
        f"unsettled={paired.get('unsettled_count', 0)} "
        f"coverage={_fmt_metric(paired.get('outcome_coverage'))} "
        f"changed={paired.get('changed_count', 0)} "
        f"mean_delta={_fmt_metric(paired.get('incremental_net_pnl_mean'))}",
        f"  mean_delta_ci95={paired.get('incremental_net_pnl_mean_ci_95')}",
        f"  {paired.get('reason', 'Exit paired evidence is unavailable.')}",
    ])
    for item in paired.get("close_reason_breakdown") or []:
        lines.append(
            f"  {item['close_reason']}: available={item['available_count']} "
            f"changed={item['changed_count']} "
            f"total_delta={_fmt_metric(item['incremental_net_pnl_total'])} "
            f"mean_delta={_fmt_metric(item['incremental_net_pnl_mean'])}"
        )
    trailing = report.get("exit_trailing_qualification") or {}
    lines.extend([
        "",
        "Exit trailing qualification: "
        f"{trailing.get('measurement_status', 'not_measured')}",
        "  "
        f"enrollment={trailing.get('enrollment_enabled')} "
        f"block={trailing.get('enrollment_block_reason')} "
        f"assignments={trailing.get('assignment_count', 0)} "
        f"treatment={trailing.get('treatment_assignment_count', 0)} "
        f"control={trailing.get('control_assignment_count', 0)} "
        f"available={trailing.get('available_count', 0)} "
        f"changed={trailing.get('changed_count', 0)}",
        f"  {trailing.get('reason', 'Exit trailing qualification is unavailable.')}",
    ])
    bottleneck = report["main_bottleneck"]
    lines.extend([
        "",
        "Main bottleneck:",
        bottleneck["summary"],
        "",
        "Next action:",
        bottleneck["next_action"],
    ])
    return "\n".join(lines)


def _universe_alpha(db, cutoff: str, as_of_at: str, min_sample_size: int) -> dict:
    causal = _causal_control_alpha(
        db,
        "universe",
        "Universe Alpha",
        cutoff,
        as_of_at,
        min_sample_size,
    )
    if causal is not None:
        return causal

    rows = db.execute(
        "SELECT symbol, direction, horizon, entry_ts, directional_return_pct "
        "FROM filter_candidate_outcomes "
        "WHERE status = 'settled' "
        "AND allocation_selected = 1 "
        f"AND {complete_outcome_economics_sql()} "
        "AND horizon IN ('1h', '4h', '24h') "
        "AND ABS(directional_return_pct) <= 100 "
        "AND datetime(entry_ts) >= datetime(?)",
        (cutoff,),
    ).fetchall()

    selected: list[float] = []
    baseline: list[float] = []
    by_horizon: dict[str, dict[str, Any]] = {}

    for row in rows:
        direction = row["direction"] or "long"
        selected_ret = float(row["directional_return_pct"])
        selected.append(selected_ret)
        base_returns = _deterministic_baseline_returns(
            db,
            entry_at=row["entry_ts"],
            horizon=row["horizon"],
            direction=direction,
            exclude_symbol=row["symbol"],
            seed=f"universe:{row['entry_ts']}:{row['horizon']}:{row['symbol']}",
            equity_like=False,
        )
        baseline.extend(base_returns)

        h = by_horizon.setdefault(row["horizon"], {"selected": [], "baseline": []})
        h["selected"].append(selected_ret)
        h["baseline"].extend(base_returns)

    if len(selected) < min_sample_size or len(baseline) < min_sample_size:
        return _verdict(
            "Universe Alpha",
            "insufficient_data",
            len(selected),
            _safe_mean(baseline),
            _safe_mean(selected),
            None,
            "insufficient",
            "Not enough settled universe forward outcomes with a same-period tradable baseline.",
            {"baseline_sample_size": len(baseline), "horizons": _horizon_details(by_horizon)},
        )

    observed = mean(selected)
    base = mean(baseline)
    delta = observed - base
    status = _status_from_delta(delta)
    return _verdict(
        "Universe Alpha",
        status,
        len(selected),
        base,
        observed,
        delta,
        _confidence_label(len(selected), min_sample_size),
        f"Selected universe members {'outperformed' if delta > 0 else 'underperformed' if delta < 0 else 'matched'} deterministic same-period tradable samples.",
        {"baseline_sample_size": len(baseline), "horizons": _horizon_details(by_horizon)},
        evidence_type="reconstructed_ohlcv_baseline",
        causal_grade="weak_reconstructed",
    )


def _ranker_alpha(db, cutoff: str, as_of_at: str, min_sample_size: int) -> dict:
    contract_rows = db.execute(
        "SELECT s.policy_epoch_id, fo.* "
        "FROM shadow_trades s JOIN filter_candidate_outcomes fo "
        "ON fo.outcome_candidate_key=s.outcome_candidate_key "
        "WHERE s.batch_id IS NOT NULL AND s.policy_epoch_id IS NOT NULL "
        "AND s.invalidated_at IS NULL "
        "AND fo.horizon='24h' AND fo.status='settled' "
        f"AND {complete_outcome_economics_sql('fo')} "
        "AND s.cost_model_version=fo.cost_model_version "
        "AND s.venue=fo.venue AND s.symbol=fo.symbol "
        "AND lower(s.direction)=lower(fo.direction) "
        "AND (CASE WHEN lower(s.market_type) IN "
        "('perp','futures','swap','usdt_futures') THEN 'perp' ELSE 'spot' END)=fo.market_type "
        "AND julianday(s.created_at)>=julianday(fo.entry_ts) "
        f"AND julianday(s.created_at)<=julianday(fo.entry_ts, "
        f"'+{SHADOW_OUTCOME_ENTRY_ALIGNMENT_MAX_SEC} seconds') "
        "AND datetime(fo.entry_ts) >= datetime(?) "
        "ORDER BY datetime(fo.entry_ts) DESC, datetime(s.created_at) DESC, s.id DESC",
        (cutoff,),
    ).fetchall()
    contract = next(
        (
            dict(row)
            for row in contract_rows
            if assess_candidate_outcome(dict(row), as_of_at=as_of_at).eligible
        ),
        None,
    )
    if not contract:
        return _verdict(
            "Ranker Alpha",
            "insufficient_data",
            0,
            None,
            None,
            None,
            "insufficient",
            "No identity-complete 24h cost-aware Ranker candidate cohort is available.",
            {
                "horizon": "24h",
                "statistical_unit": "outcome_candidate_key",
                "assignment_unit": "batch_id",
                "economic_unit": "net_return_pct",
                "source_table": "filter_candidate_outcomes",
            },
            evidence_type="fixed_horizon_candidate_cohort",
            causal_grade="insufficient",
        )

    rows = db.execute(
        "WITH candidates AS ("
        " SELECT s.batch_id, s.rank, s.created_at AS ranked_at, fo.*, "
        " ROW_NUMBER() OVER (PARTITION BY s.outcome_candidate_key "
        " ORDER BY julianday(s.created_at) ASC, s.id ASC) AS exposure_rank"
        " FROM shadow_trades s JOIN filter_candidate_outcomes fo"
        " ON fo.outcome_candidate_key=s.outcome_candidate_key"
        " WHERE s.batch_id IS NOT NULL AND s.policy_epoch_id=?"
        " AND s.invalidated_at IS NULL"
        " AND s.rank BETWEEN 1 AND 5"
        " AND fo.horizon='24h' AND fo.status='settled'"
        f" AND {complete_outcome_economics_sql('fo')} AND fo.cost_model_version=?"
        " AND s.cost_model_version=fo.cost_model_version"
        " AND s.venue=fo.venue AND s.symbol=fo.symbol"
        " AND lower(s.direction)=lower(fo.direction)"
        " AND (CASE WHEN lower(s.market_type) IN "
        " ('perp','futures','swap','usdt_futures') THEN 'perp' ELSE 'spot' END)=fo.market_type"
        " AND datetime(fo.entry_ts) >= datetime(?)"
        ") SELECT * FROM candidates WHERE exposure_rank=1",
        (
            contract["policy_epoch_id"],
            contract["cost_model_version"],
            cutoff,
        ),
    ).fetchall()

    batches: dict[str, list[dict]] = defaultdict(list)
    temporally_misaligned_candidates = 0
    temporally_misaligned_batch_ids: set[str] = set()
    outcome_exclusions: dict[str, int] = defaultdict(int)
    for row in rows:
        candidate = dict(row)
        assessment = assess_candidate_outcome(candidate, as_of_at=as_of_at)
        if not assessment.eligible:
            outcome_exclusions[assessment.reason] += 1
            continue
        candidate["net_return_pct"] = assessment.net_return_pct
        ranked_at = parse_utc(candidate.get("ranked_at"))
        entry_at = parse_utc(candidate.get("entry_ts"))
        if ranked_at is None or entry_at is None:
            temporally_misaligned_candidates += 1
            temporally_misaligned_batch_ids.add(str(row["batch_id"]))
            continue
        lag_sec = (ranked_at - entry_at).total_seconds()
        if lag_sec < 0 or lag_sec > SHADOW_OUTCOME_ENTRY_ALIGNMENT_MAX_SEC:
            temporally_misaligned_candidates += 1
            temporally_misaligned_batch_ids.add(str(row["batch_id"]))
            continue
        batches[row["batch_id"]].append(candidate)
    for batch_id in temporally_misaligned_batch_ids:
        batches.pop(batch_id, None)

    top1: list[float] = []
    top2_5: list[float] = []
    batch_baselines: list[float] = []
    eligible: list[float] = []
    usable_batches = 0
    temporally_mixed_batches = 0
    for batch_rows in batches.values():
        entry_times = [parse_utc(r.get("entry_ts")) for r in batch_rows]
        if any(ts is None for ts in entry_times):
            temporally_mixed_batches += 1
            continue
        valid_entry_times = [ts for ts in entry_times if ts is not None]
        if (
            max(valid_entry_times) - min(valid_entry_times)
        ).total_seconds() > SHADOW_OUTCOME_ENTRY_ALIGNMENT_MAX_SEC:
            temporally_mixed_batches += 1
            continue
        rank1 = [float(r["net_return_pct"]) for r in batch_rows if int(r["rank"] or 0) == 1]
        rest = [float(r["net_return_pct"]) for r in batch_rows if 2 <= int(r["rank"] or 0) <= 5]
        if len(rank1) != 1 or not rest:
            continue
        usable_batches += 1
        top1.append(rank1[0])
        top2_5.extend(rest)
        batch_baselines.append(mean(rest))
        eligible.extend(float(r["net_return_pct"]) for r in batch_rows if int(r["rank"] or 0) >= 1)

    details = {
        "top2_5_sample_size": len(top2_5),
        "baseline_batch_count": len(batch_baselines),
        "eligible_average": _safe_mean(eligible),
        "batch_count": usable_batches,
        "temporally_misaligned_candidate_count": temporally_misaligned_candidates,
        "temporally_misaligned_batch_count": len(temporally_misaligned_batch_ids),
        "temporally_mixed_batch_count": temporally_mixed_batches,
        "candidate_outcome_exclusion_counts": dict(sorted(outcome_exclusions.items())),
        "first_exposure_alignment_max_sec": SHADOW_OUTCOME_ENTRY_ALIGNMENT_MAX_SEC,
        "horizon": "24h",
        "statistical_unit": "outcome_candidate_key",
        "assignment_unit": "batch_id",
        "economic_unit": "net_return_pct",
        "estimand": "mean_batch_rank1_minus_mean_rank2_5",
        "source_table": "filter_candidate_outcomes",
        "policy_epoch_id": contract["policy_epoch_id"],
        "cost_model_version": contract["cost_model_version"],
        "authority_limitations": [
            "rank_assignment_not_randomized",
            "batch_cluster_dependence_not_inferred",
            "rank1_is_ordering_not_execution_selection",
        ],
    }
    if len(top1) < min_sample_size or len(batch_baselines) < min_sample_size:
        return _verdict(
            "Ranker Alpha",
            "insufficient_data",
            len(top1),
            _safe_mean(batch_baselines),
            _safe_mean(top1),
            None,
            "insufficient",
            "Not enough temporally aligned first-exposure 24h Ranker batches.",
            details,
            evidence_type="fixed_horizon_candidate_cohort",
            causal_grade="insufficient",
        )

    observed = mean(top1)
    base = mean(batch_baselines)
    delta = observed - base
    status = _status_from_delta(delta)
    return _verdict(
        "Ranker Alpha",
        status,
        len(top1),
        base,
        observed,
        delta,
        _confidence_label(len(top1), min_sample_size),
        f"Ranker first-exposure top1 {'beat' if delta > 0 else 'trailed' if delta < 0 else 'matched'} the per-batch rank2-5 baseline on the same 24h cost-aware outcome contract.",
        details,
        evidence_type="fixed_horizon_candidate_cohort",
        causal_grade="moderate_prospective_cohort",
    )


def _system_operator_alpha(db, cutoff: str, as_of_at: str, min_sample_size: int) -> dict:
    first_exposure = (
        "WITH operator_rows AS ("
        " SELECT fd.*, fd.rowid AS source_rowid, pd.action, pd.runtime_session_id,"
        " pd.producer_commit, pd.behavior_fingerprint,"
        " ROW_NUMBER() OVER (PARTITION BY fd.outcome_candidate_key "
        " ORDER BY datetime(fd.evaluated_at), fd.rowid) AS exposure_rank"
        " FROM filter_decisions fd JOIN pi_decisions pd"
        " ON fd.candidate_key=('pi_decision:' || pd.id)"
        " WHERE fd.candidate_kind='pi_decision' AND fd.stage='system_operator'"
        " AND fd.outcome_candidate_key IS NOT NULL"
        " AND pd.signal_source='market_observer' AND pd.opportunity_id IS NOT NULL"
        " AND pd.runtime_session_id IS NOT NULL AND pd.producer_commit IS NOT NULL"
        " AND pd.behavior_fingerprint IS NOT NULL AND pd.producer_commit=fd.code_commit"
        "), first_rows AS ("
        " SELECT * FROM operator_rows"
        " WHERE exposure_rank=1"
        ") "
    )
    contract = db.execute(
        first_exposure + "SELECT fd.producer_commit, fd.behavior_fingerprint, fo.* "
        "FROM first_rows fd JOIN filter_candidate_outcomes fo "
        "ON fo.outcome_candidate_key=fd.outcome_candidate_key "
        "WHERE fo.horizon='24h' "
        "AND fo.status='settled' "
        f"AND {complete_outcome_economics_sql('fo')} "
        "AND fd.venue=fo.venue AND fd.symbol=fo.symbol "
        "AND lower(fd.direction)=lower(fo.direction) "
        "AND fd.market_type=fo.market_type "
        "AND datetime(fo.entry_ts) >= datetime(?) "
        "ORDER BY datetime(fo.entry_ts) DESC, fd.source_rowid DESC",
        (cutoff,),
    ).fetchall()
    contract = next(
        (
            dict(row)
            for row in contract
            if assess_candidate_outcome(dict(row), as_of_at=as_of_at).eligible
        ),
        None,
    )
    if not contract:
        return _verdict(
            "System Operator Alpha",
            "insufficient_data",
            0,
            None,
            None,
            None,
            "insufficient",
            "No identity-complete 24h cost-aware System Operator candidate cohort is available.",
            {
                "horizon": "24h",
                "statistical_unit": "outcome_candidate_key",
                "economic_unit": "net_return_pct",
                "source_table": "filter_candidate_outcomes",
            },
            evidence_type="fixed_horizon_candidate_cohort",
            causal_grade="insufficient",
        )

    rows = db.execute(
        first_exposure + "SELECT fd.outcome_candidate_key, fd.action, fo.*, "
        "MAX(CASE WHEN fd.decision='rejected' THEN 1 ELSE 0 END) AS rejected, "
        "MAX(CASE WHEN fd.decision IN ('passed','passed_with_penalty') THEN 1 ELSE 0 END) AS passed "
        "FROM first_rows fd JOIN filter_candidate_outcomes fo "
        "ON fo.outcome_candidate_key=fd.outcome_candidate_key "
        "WHERE fd.producer_commit=? AND fd.behavior_fingerprint=? AND fo.cost_model_version=? "
        "AND fo.horizon='24h' AND fo.status='settled' "
        f"AND {complete_outcome_economics_sql('fo')} "
        "AND fd.venue=fo.venue AND fd.symbol=fo.symbol "
        "AND lower(fd.direction)=lower(fo.direction) AND fd.market_type=fo.market_type "
        "AND datetime(fo.entry_ts) >= datetime(?) "
        "GROUP BY fd.outcome_candidate_key, fd.action, fo.id",
        (
            contract["producer_commit"],
            contract["behavior_fingerprint"],
            contract["cost_model_version"],
            cutoff,
        ),
    ).fetchall()

    outcome_exclusions: dict[str, int] = defaultdict(int)
    eligible_rows = []
    for row in rows:
        candidate = dict(row)
        assessment = assess_candidate_outcome(candidate, as_of_at=as_of_at)
        if not assessment.eligible:
            outcome_exclusions[assessment.reason] += 1
            continue
        candidate["net_return_pct"] = assessment.net_return_pct
        eligible_rows.append(candidate)
    accepted = [
        float(row["net_return_pct"])
        for row in eligible_rows
        if row["action"] == "trade" and not int(row["rejected"] or 0) and int(row["passed"] or 0)
    ]
    rejected = [
        float(row["net_return_pct"])
        for row in eligible_rows
        if row["action"] in {"skip", "portfolio_guard_skip"} and int(row["rejected"] or 0)
    ]
    details = {
        "rejected_sample_size": len(rejected),
        "horizon": "24h",
        "statistical_unit": "outcome_candidate_key",
        "economic_unit": "net_return_pct",
        "source_table": "filter_candidate_outcomes",
        "producer_commit": contract["producer_commit"],
        "behavior_fingerprint": contract["behavior_fingerprint"],
        "cost_model_version": contract["cost_model_version"],
        "candidate_outcome_exclusion_counts": dict(sorted(outcome_exclusions.items())),
        "authority_limitations": [
            "operator_assignment_not_randomized",
            "passed_and_rejected_candidate_selection_confounded",
        ],
    }

    if len(accepted) < min_sample_size or len(rejected) < min_sample_size:
        return _verdict(
            "System Operator Alpha",
            "insufficient_data",
            len(accepted),
            _safe_mean(rejected),
            _safe_mean(accepted),
            None,
            "insufficient",
            "Not enough identity-complete 24h candidates in both passed and rejected System Operator cohorts.",
            details,
            evidence_type="fixed_horizon_candidate_cohort",
            causal_grade="insufficient",
        )

    observed = mean(accepted)
    base = mean(rejected)
    delta = observed - base
    status = _status_from_delta(delta)
    return _verdict(
        "System Operator Alpha",
        status,
        len(accepted),
        base,
        observed,
        delta,
        _confidence_label(len(accepted), min_sample_size),
        f"Passed System Operator candidates {'beat' if delta > 0 else 'trailed' if delta < 0 else 'matched'} rejected candidates on the same 24h cost-aware outcome contract.",
        details,
        evidence_type="fixed_horizon_candidate_cohort",
        causal_grade="moderate_prospective_cohort",
    )


def _learning_alpha(cutoff: str) -> dict:
    """Expose persisted Lab evidence without promoting it to current qualification."""
    from extensions.learning.read_models import build_learning_contribution_projection

    projection = build_learning_contribution_projection(limit=100)
    cutoff_at = parse_utc(cutoff)
    measurements = []
    for item in projection["items"]:
        measurement = item["measurement"]
        window_start = parse_utc(measurement["window_start"])
        window_end = parse_utc(measurement["window_end"])
        measurements.append({
            "path_id": item["path_id"],
            "experiment_id": item["experiment_id"],
            "stage": item["stage"],
            "experiment_status": item["experiment_status"],
            "availability": item["availability"],
            "data_freshness": item["data_freshness"],
            "measurement": measurement,
            "samples": item["samples"],
            "current_identity": item["current_identity"],
            "inference": item["inference"],
            "integrity": item["integrity"],
            "audit_window_compatible": (
                cutoff_at is not None and window_start is not None
                and window_end is not None and cutoff_at <= window_start <= window_end
            ),
        })
    reason = projection["unavailable_reason"] or (
        "current_consumed_settled_proof_unavailable"
        if measurements else "no_persisted_lab_measurement"
    )
    return _verdict(
        "Learning Alpha", "insufficient_data", 0, None, None, None, "insufficient",
        reason,
        {
            "availability": projection["availability"],
            "unavailable_reason": reason,
            "measurements": measurements,
        },
        evidence_type="canonical_lab_measurements",
        causal_grade="insufficient",
    )


def _serenity_alpha(db, cutoff: str, as_of_at: str, min_sample_size: int) -> dict:
    causal = _causal_control_alpha(
        db,
        "serenity",
        "Serenity Alpha",
        cutoff,
        as_of_at,
        min_sample_size,
    )
    if causal is not None:
        return causal

    rows = db.execute(
        "SELECT symbol, direction, created_at, asset_class FROM serenity_opportunities "
        "WHERE datetime(created_at) >= datetime(?)",
        (cutoff,),
    ).fetchall()

    observed_returns: list[float] = []
    baseline_returns: list[float] = []
    by_horizon: dict[str, dict[str, Any]] = {}

    for row in rows:
        equity_like = (row["asset_class"] or "").lower() == "equity" or "/" not in (row["symbol"] or "")
        for horizon in ("1d", "3d", "7d"):
            ret = _forward_return_from_ohlcv(db, row["symbol"], row["created_at"], horizon, row["direction"] or "long")
            if ret is None:
                continue
            observed_returns.append(ret)
            base = _deterministic_baseline_returns(
                db,
                entry_at=row["created_at"],
                horizon=horizon,
                direction=row["direction"] or "long",
                exclude_symbol=row["symbol"],
                seed=f"serenity:{row['created_at']}:{horizon}:{row['symbol']}",
                equity_like=equity_like,
            )
            baseline_returns.extend(base)
            h = by_horizon.setdefault(horizon, {"selected": [], "baseline": []})
            h["selected"].append(ret)
            h["baseline"].extend(base)

    if len(observed_returns) < min_sample_size or len(baseline_returns) < min_sample_size:
        return _verdict(
            "Serenity Alpha",
            "insufficient_data",
            len(observed_returns),
            _safe_mean(baseline_returns),
            _safe_mean(observed_returns),
            None,
            "insufficient",
            "Not enough Serenity opportunities with 1d/3d/7d forward returns and an observed baseline.",
            {"baseline_sample_size": len(baseline_returns), "horizons": _horizon_details(by_horizon)},
        )

    observed = mean(observed_returns)
    base = mean(baseline_returns)
    delta = observed - base
    status = _status_from_delta(delta)
    return _verdict(
        "Serenity Alpha",
        status,
        len(observed_returns),
        base,
        observed,
        delta,
        _confidence_label(len(observed_returns), min_sample_size),
        f"Serenity opportunities {'beat' if delta > 0 else 'trailed' if delta < 0 else 'matched'} observed same-period baselines.",
        {"baseline_sample_size": len(baseline_returns), "horizons": _horizon_details(by_horizon)},
        evidence_type="reconstructed_ohlcv_baseline",
        causal_grade="weak_reconstructed",
    )


def _deterministic_baseline_returns(
    db,
    *,
    entry_at: str,
    horizon: str,
    direction: str,
    exclude_symbol: str,
    seed: str,
    equity_like: bool,
    sample_size: int = 5,
) -> list[float]:
    symbols = _baseline_symbols(db, exclude_symbol=exclude_symbol, equity_like=equity_like)
    ranked = sorted(symbols, key=lambda s: hashlib.sha256(f"{seed}:{s}".encode("utf-8")).hexdigest())
    returns: list[float] = []
    for symbol in ranked:
        ret = _forward_return_from_ohlcv(db, symbol, entry_at, horizon, direction)
        if ret is None:
            continue
        returns.append(ret)
        if len(returns) >= sample_size:
            break
    return returns


def _causal_control_alpha(
    db,
    layer: str,
    name: str,
    cutoff: str,
    as_of_at: str,
    min_sample_size: int,
) -> dict | None:
    rows = db.execute(
        "SELECT * FROM alpha_control_observations "
        "WHERE layer = ? AND datetime(entry_ts) >= datetime(?) "
        "ORDER BY entry_ts ASC",
        (layer, cutoff),
    ).fetchall()
    if not rows:
        return None

    selected: list[float] = []
    controls: list[float] = []
    by_horizon: dict[str, dict[str, Any]] = {}

    for row in rows:
        ret = _control_observation_return(db, row, as_of_at)
        if ret is None:
            continue
        horizon = row["horizon"]
        h = by_horizon.setdefault(horizon, {"selected": [], "baseline": []})
        if row["role"] == "selected":
            selected.append(ret)
            h["selected"].append(ret)
        elif row["role"] == "control":
            controls.append(ret)
            h["baseline"].append(ret)

    if len(selected) < min_sample_size or len(controls) < min_sample_size:
        return _verdict(
            name,
            "insufficient_data",
            len(selected),
            _safe_mean(controls),
            _safe_mean(selected),
            None,
            "insufficient",
            "Prospective control cohorts exist, but not enough selected/control outcomes have matured.",
            {"baseline_sample_size": len(controls), "horizons": _horizon_details(by_horizon)},
            evidence_type="prospective_control_cohort",
            causal_grade="insufficient",
        )

    observed = mean(selected)
    base = mean(controls)
    delta = observed - base
    status = _status_from_delta(delta)
    return _verdict(
        name,
        status,
        len(selected),
        base,
        observed,
        delta,
        _confidence_label(len(selected), min_sample_size),
        f"{name} selected observations {'beat' if delta > 0 else 'trailed' if delta < 0 else 'matched'} prospectively logged same-time controls; the cohort lacks a contribution-authority contract.",
        {
            "baseline_sample_size": len(controls),
            "horizons": _horizon_details(by_horizon),
            "authority_limitations": [
                "no_policy_epoch_identity",
                "no_behavior_contract_identity",
                "no_declared_primary_horizon",
                "no_cohort_level_inference_contract",
            ],
        },
        evidence_type="prospective_control_cohort",
        causal_grade="weak_observational",
    )


def _control_observation_return(db, row, as_of_at: str) -> float | None:
    outcome_key = row["outcome_candidate_key"]
    if not outcome_key:
        return None
    outcomes = db.execute(
        "SELECT * FROM filter_candidate_outcomes "
        "WHERE outcome_candidate_key=? AND horizon=? AND status='settled' "
        f"AND {complete_outcome_economics_sql()} "
        "ORDER BY datetime(settled_at), rowid",
        (outcome_key, row["horizon"]),
    ).fetchall()
    for outcome in outcomes:
        assessment = assess_candidate_outcome(dict(outcome), as_of_at=as_of_at)
        if assessment.eligible:
            return assessment.net_return_pct
    return None


def _exit_price_from_ohlcv(db, symbol: str, due_at: str) -> float | None:
    due_dt = parse_utc(due_at)
    if due_dt is None:
        return None
    target_ms = int(due_dt.timestamp() * 1000)
    row = db.execute(
        "SELECT close FROM market_ohlcv "
        "WHERE symbol = ? AND ts >= ? ORDER BY ts ASC LIMIT 1",
        (symbol, target_ms),
    ).fetchone()
    return float(row["close"]) if row and row["close"] is not None else None


def _baseline_symbols(db, *, exclude_symbol: str, equity_like: bool) -> list[str]:
    registry = db.execute(
        "SELECT DISTINCT symbol FROM universe_registry "
        "WHERE status = 'active' AND COALESCE(is_blacklisted, 0) = 0"
    ).fetchall()
    symbols = {r["symbol"] for r in registry if r["symbol"]}
    ohlcv = db.execute("SELECT DISTINCT symbol FROM market_ohlcv").fetchall()
    symbols.update(r["symbol"] for r in ohlcv if r["symbol"])
    symbols.discard(exclude_symbol)
    if equity_like:
        filtered = {s for s in symbols if "/" not in s}
        if filtered:
            symbols = filtered
    return sorted(symbols)


def _forward_return_from_ohlcv(db, symbol: str, entry_at: str, horizon: str, direction: str) -> float | None:
    entry_dt = parse_utc(entry_at)
    seconds = _HORIZON_SECONDS.get(horizon)
    if entry_dt is None or seconds is None:
        return None
    entry_ms = int(entry_dt.timestamp() * 1000)
    exit_ms = int((entry_dt.timestamp() + seconds) * 1000)
    timeframes = ("1h", "1d") if horizon in {"1h", "4h", "24h"} else ("1d", "1h")
    for timeframe in timeframes:
        entry = db.execute(
            "SELECT close FROM market_ohlcv "
            "WHERE symbol = ? AND timeframe = ? AND ts >= ? "
            "ORDER BY ts ASC LIMIT 1",
            (symbol, timeframe, entry_ms),
        ).fetchone()
        exit_row = db.execute(
            "SELECT close FROM market_ohlcv "
            "WHERE symbol = ? AND timeframe = ? AND ts >= ? "
            "ORDER BY ts ASC LIMIT 1",
            (symbol, timeframe, exit_ms),
        ).fetchone()
        if not entry or not exit_row:
            continue
        entry_price = float(entry["close"] or 0)
        exit_price = float(exit_row["close"] or 0)
        if entry_price <= 0 or exit_price <= 0:
            continue
        raw = (exit_price - entry_price) / entry_price * 100.0
        return _directional_return(raw, direction)
    return None


def _directional_return(raw_return_pct: float, direction: str | None) -> float:
    value = float(raw_return_pct)
    return -value if (direction or "").lower() == "short" else value


def _main_bottleneck(verdicts: dict) -> dict:
    negative = [
        (key, item)
        for key, item in verdicts.items()
        if item.get("contribution_verdict") == "negative"
        and item.get("measurement_status") == "measured_contribution"
        and item.get("delta") is not None
    ]
    if len(negative) == 1:
        key, item = negative[0]
        return {
            "layer": item["name"],
            "summary": f"{item['name']} has measured negative contribution: {item['reason']}",
            "next_action": _next_action(key),
            "suspected_modules": item.get("suspected_modules", []),
        }
    if len(negative) > 1:
        return {
            "layer": "not_comparable",
            "summary": "No single actionable gradient is established: several layers have measured negative contribution but use non-comparable estimands.",
            "next_action": "Put the measured effects on one net economic basis before selecting a single bottleneck.",
            "suspected_modules": [],
            "candidate_layers": [item["name"] for _, item in negative],
        }
    return {
        "layer": "insufficient_data",
        "summary": "No actionable gradient is currently established: no measured negative contribution is available.",
        "next_action": "Continue collecting contribution-grade evidence; do not change trading behavior from directional results.",
        "suspected_modules": [],
    }


def _next_action(key: str) -> str:
    return {
        "universe": "Do not tune the decision prompt first. Inspect universe scanner selection and its tradable-symbol baseline.",
        "system_operator": "Do not change scanner. Improve System Operator decision prompt, model, and context selection.",
        "learning": "Learning artifacts are not reducing repeated losses. Inspect memo/guard consumption before adding more memories.",
        "serenity": "Serenity opportunities underperform baseline. Inspect the lens, evidence gating, and equity opportunity criteria.",
    }.get(key, "Collect more data before changing trading behavior.")


def _verdict(
    name: str,
    status: str,
    sample_size: int,
    baseline: float | None,
    observed: float | None,
    delta: float | None,
    confidence_label: str,
    reason: str,
    details: dict | None = None,
    evidence_type: str = "observational",
    causal_grade: str = "weak_observational",
) -> dict:
    contribution_verdict = (
        status
        if causal_grade == "strong_prospective_control" and status != "insufficient_data"
        else "insufficient_data"
    )
    return {
        "name": name,
        **({"scope": "historical_ranker"} if name == "Ranker Alpha" else {}),
        "verdict": status,
        "sample_size": int(sample_size or 0),
        "baseline": _round_or_none(baseline),
        "observed": _round_or_none(observed),
        "delta": _round_or_none(delta),
        "confidence_label": confidence_label,
        "evidence_type": evidence_type,
        "causal_grade": causal_grade,
        "contribution_verdict": contribution_verdict,
        "measurement_status": (
            "measured_contribution"
            if contribution_verdict != "insufficient_data"
            else "directional_evidence_only"
            if status != "insufficient_data"
            else "not_measured"
        ),
        "suspected_modules": _MODULES_BY_LAYER.get(name, []),
        "reason": reason,
        "details": details or {},
    }


def _status_from_delta(delta: float) -> str:
    if delta > ALPHA_THRESHOLD_PCT:
        return "positive"
    if delta < -ALPHA_THRESHOLD_PCT:
        return "negative"
    return "neutral"


def _confidence_label(sample_size: int, min_sample_size: int) -> str:
    if sample_size < min_sample_size:
        return "insufficient"
    if sample_size >= 30:
        return "high"
    if sample_size >= 10:
        return "medium"
    return "low"


def _horizon_details(groups: dict[str, dict[str, list[float]]]) -> dict:
    result = {}
    for horizon, values in groups.items():
        selected = values.get("selected", [])
        baseline = values.get("baseline", [])
        result[horizon] = {
            "sample_size": len(selected),
            "baseline_sample_size": len(baseline),
            "observed": _round_or_none(_safe_mean(selected)),
            "baseline": _round_or_none(_safe_mean(baseline)),
            "delta": _round_or_none(
                (_safe_mean(selected) - _safe_mean(baseline))
                if selected and baseline else None
            ),
        }
    return result


def _safe_mean(values: list[float]) -> float | None:
    return mean(values) if values else None


def _round_or_none(value: float | None) -> float | None:
    return round(float(value), 4) if value is not None else None


def _fmt_metric(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4f}%"
    return str(value)


def _parse_json(raw: Any) -> Any:
    if raw is None:
        return {}
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return {}
    return {}





def _normalize_days(days: int) -> int:
    if days <= 0:
        return 30
    if days not in {7, 14, 30}:
        return days
    return days
