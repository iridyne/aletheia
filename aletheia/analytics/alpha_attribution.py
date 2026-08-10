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

from aletheia.analytics.outcome_integrity import eligible_strategy_outcomes
from aletheia.analytics.learning_window import outcome_window_metadata


VERDICT_NAMES = (
    "Universe Alpha",
    "Ranker Alpha",
    "System Operator Alpha",
    "Learning Alpha",
    "Serenity Alpha",
)

MIN_SAMPLE_SIZE = 5
ALPHA_THRESHOLD_PCT = 0.10
_RANKER_ENTRY_ALIGNMENT_MAX_SEC = 60

_MODULES_BY_LAYER = {
    "Universe Alpha": [
        "aletheia/universe/scanners.py",
        "aletheia/universe/allocator.py",
        "aletheia/universe/filters.py",
        "aletheia/universe/service.py",
    ],
    "Ranker Alpha": [
        "aletheia/agents/paper_trader_ranker.py",
        "aletheia/analytics/factor_feedback.py",
        "aletheia/knowledge/guards.py",
    ],
    "System Operator Alpha": [
        "aletheia/agent_shell/trading_decision.py",
        "aletheia/agent_shell/trading_context.py",
        "aletheia/agent_runtime/pi_daemon.py",
    ],
    "Learning Alpha": [
        "aletheia/learning/lessons.py",
        "aletheia/analytics/memo_effectiveness.py",
        "aletheia/knowledge/guards.py",
        "aletheia/analytics/guard_effectiveness.py",
    ],
    "Serenity Alpha": [
        "aletheia/agents/research_worker.py",
        "aletheia/trading/serenity_scoring.py",
        "aletheia/trading/serenity_evidence.py",
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
    generated_at = _utcnow().isoformat(timespec="seconds")
    cutoff = (_utcnow() - timedelta(days=days)).isoformat()

    if getattr(sqlite_store, "_db_conn", None) is None and not sqlite_store.db_path().exists():
        return _empty_report(generated_at, days, "Database is not initialized; no alpha evidence is available.")

    try:
        db = sqlite_store.get_db()
    except sqlite3.OperationalError:
        return _empty_report(generated_at, days, "Database could not be opened; no alpha evidence is available.")

    verdicts = {
        "universe": _safe_layer("Universe Alpha", lambda: _universe_alpha(db, cutoff, min_sample_size)),
        "ranker": _safe_layer("Ranker Alpha", lambda: _ranker_alpha(db, cutoff, min_sample_size)),
        "system_operator": _safe_layer("System Operator Alpha", lambda: _system_operator_alpha(db, cutoff, min_sample_size)),
        "learning": _safe_layer("Learning Alpha", lambda: _learning_alpha(db, cutoff, days, min_sample_size)),
        "serenity": _safe_layer("Serenity Alpha", lambda: _serenity_alpha(db, cutoff, min_sample_size)),
    }

    return {
        "generated_at": generated_at,
        "window_days": days,
        **outcome_window_metadata(),
        "verdicts": verdicts,
        "universe_twin_evidence": _universe_twin_evidence(db, cutoff),
        "universe_relative_rank_evidence": _universe_relative_rank_evidence(db, cutoff),
        "exit_paired_evidence": _exit_paired_evidence(cutoff),
        "risk_policy_evidence": _risk_policy_evidence(db, cutoff, min_sample_size),
        "execution_cost_evidence": _execution_cost_evidence(
            db, cutoff, min_sample_size
        ),
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
        "risk_policy_evidence": _empty_risk_policy_evidence(reason),
        "execution_cost_evidence": _empty_execution_cost_evidence(reason),
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
        "contract_version": "exit-paired-evidence/v1",
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
        "changed_count": 0,
        "incremental_net_pnl_total": None,
        "incremental_net_pnl_mean": None,
        "positive_increment_count": 0,
        "close_reason_breakdown": [],
        "close_reason_attribution_available": False,
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
    start_at = _parse_dt(window_start)
    end_at = _parse_dt(window_end)
    cutoff_at = _parse_dt(cutoff)
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
        from aletheia.universe.twin_evaluation import evaluate_twin_shadow_epoch

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
    start_at = _parse_dt(window_start)
    end_at = _parse_dt(window_end)
    cutoff_at = _parse_dt(cutoff)
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
        "contract_version": "exit-paired-evidence/v1",
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


def _empty_risk_policy_evidence(reason: str) -> dict:
    from aletheia.analytics.risk_contribution import empty_risk_policy_contribution

    return empty_risk_policy_contribution(reason)


def _risk_policy_evidence(db, cutoff: str, min_sample_size: int) -> dict:
    from aletheia.analytics.risk_contribution import (
        build_risk_policy_contribution,
    )

    try:
        return build_risk_policy_contribution(
            db,
            cutoff=cutoff,
            min_sample_size=min_sample_size,
            min_interventions=min_sample_size,
        )
    except sqlite3.OperationalError as exc:
        return _empty_risk_policy_evidence(
            f"Required Risk policy evidence table is unavailable: {exc}"
        )


def _empty_execution_cost_evidence(reason: str) -> dict:
    from aletheia.analytics.execution_contribution import (
        empty_execution_cost_contribution,
    )

    return empty_execution_cost_contribution(reason)


def _execution_cost_evidence(db, cutoff: str, min_sample_size: int) -> dict:
    from aletheia.analytics.execution_contribution import (
        build_execution_cost_contribution,
    )

    try:
        return build_execution_cost_contribution(
            db,
            cutoff=cutoff,
            min_sample_size=min_sample_size,
        )
    except sqlite3.OperationalError as exc:
        return _empty_execution_cost_evidence(
            f"Required Execution cost evidence is unavailable: {exc}"
        )


def render_alpha_audit(report: dict) -> str:
    """Render the audit in the owner-facing CLI format."""
    lines = ["Alpha Attribution Audit", "", f"Window: {report.get('window_days')}d", ""]
    ordered = [
        ("Universe", "universe"),
        ("Ranker", "ranker"),
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
        f"changed={paired.get('changed_count', 0)} "
        f"mean_delta={_fmt_metric(paired.get('incremental_net_pnl_mean'))}",
        f"  {paired.get('reason', 'Exit paired evidence is unavailable.')}",
    ])
    for item in paired.get("close_reason_breakdown") or []:
        lines.append(
            f"  {item['close_reason']}: available={item['available_count']} "
            f"changed={item['changed_count']} "
            f"total_delta={_fmt_metric(item['incremental_net_pnl_total'])} "
            f"mean_delta={_fmt_metric(item['incremental_net_pnl_mean'])}"
        )
    risk = report.get("risk_policy_evidence") or {}
    lines.extend([
        "",
        f"Risk policy evidence: {risk.get('measurement_status', 'not_measured')}",
        "  "
        f"eligible={risk.get('eligible_count', 0)} "
        f"interventions={risk.get('actual_intervention_count', 0)} "
        f"delta={_fmt_metric(risk.get('delta'))} "
        f"contribution={risk.get('contribution_verdict', 'insufficient_data')}",
        f"  {risk.get('reason', 'Risk policy evidence is unavailable.')}",
    ])
    execution = report.get("execution_cost_evidence") or {}
    lines.extend([
        "",
        "Execution cost evidence: "
        f"{execution.get('measurement_status', 'not_measured')}",
        "  "
        f"eligible={execution.get('eligible_count', 0)} "
        f"delta={_fmt_metric(execution.get('delta'))} "
        "quote_delta="
        f"{_fmt_metric(execution.get('incremental_net_pnl_total'))} "
        "contribution="
        f"{execution.get('contribution_verdict', 'insufficient_data')}",
        f"  {execution.get('reason', 'Execution cost evidence is unavailable.')}",
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


def _universe_alpha(db, cutoff: str, min_sample_size: int) -> dict:
    causal = _causal_control_alpha(db, "universe", "Universe Alpha", cutoff, min_sample_size)
    if causal is not None:
        return causal

    rows = db.execute(
        "SELECT symbol, direction, horizon, entry_ts, forward_return_pct "
        "FROM universe_forward_outcomes "
        "WHERE status = 'settled' "
        "AND horizon IN ('1h', '4h', '24h') "
        "AND forward_return_pct IS NOT NULL "
        "AND ABS(forward_return_pct) <= 100 "
        "AND datetime(entry_ts) >= datetime(?)",
        (cutoff,),
    ).fetchall()

    selected: list[float] = []
    baseline: list[float] = []
    by_horizon: dict[str, dict[str, Any]] = {}

    for row in rows:
        direction = row["direction"] or "long"
        selected_ret = _directional_return(row["forward_return_pct"], direction)
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


def _ranker_alpha(db, cutoff: str, min_sample_size: int) -> dict:
    contract = db.execute(
        "SELECT s.policy_epoch_id, fo.cost_model_version "
        "FROM shadow_trades s JOIN filter_candidate_outcomes fo "
        "ON fo.outcome_candidate_key=s.outcome_candidate_key "
        "WHERE s.batch_id IS NOT NULL AND s.policy_epoch_id IS NOT NULL "
        "AND fo.horizon='24h' AND fo.status='settled' "
        "AND fo.net_return_pct IS NOT NULL AND fo.cost_model_version IS NOT NULL "
        "AND s.cost_model_version=fo.cost_model_version "
        "AND s.venue=fo.venue AND s.symbol=fo.symbol "
        "AND lower(s.direction)=lower(fo.direction) "
        "AND (CASE WHEN lower(s.market_type) IN "
        "('perp','futures','swap','usdt_futures') THEN 'perp' ELSE 'spot' END)=fo.market_type "
        "AND julianday(s.created_at)>=julianday(fo.entry_ts) "
        "AND (julianday(s.created_at)-julianday(fo.entry_ts))*86400.0<=? "
        "AND datetime(fo.entry_ts) >= datetime(?) "
        "ORDER BY datetime(fo.entry_ts) DESC, datetime(s.created_at) DESC, s.id DESC LIMIT 1",
        (_RANKER_ENTRY_ALIGNMENT_MAX_SEC, cutoff),
    ).fetchone()
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
        " SELECT s.batch_id, s.outcome_candidate_key, s.rank, fo.net_return_pct,"
        " s.created_at AS ranked_at, fo.entry_ts,"
        " ROW_NUMBER() OVER (PARTITION BY s.outcome_candidate_key "
        " ORDER BY julianday(s.created_at) ASC, s.id ASC) AS exposure_rank"
        " FROM shadow_trades s JOIN filter_candidate_outcomes fo"
        " ON fo.outcome_candidate_key=s.outcome_candidate_key"
        " WHERE s.batch_id IS NOT NULL AND s.policy_epoch_id=?"
        " AND s.rank BETWEEN 1 AND 5"
        " AND fo.horizon='24h' AND fo.status='settled'"
        " AND fo.net_return_pct IS NOT NULL AND fo.cost_model_version=?"
        " AND s.cost_model_version=fo.cost_model_version"
        " AND s.venue=fo.venue AND s.symbol=fo.symbol"
        " AND lower(s.direction)=lower(fo.direction)"
        " AND (CASE WHEN lower(s.market_type) IN "
        " ('perp','futures','swap','usdt_futures') THEN 'perp' ELSE 'spot' END)=fo.market_type"
        " AND datetime(fo.entry_ts) >= datetime(?)"
        ") SELECT batch_id, outcome_candidate_key, rank, net_return_pct, ranked_at, entry_ts "
        "FROM candidates WHERE exposure_rank=1",
        (
            contract["policy_epoch_id"],
            contract["cost_model_version"],
            cutoff,
        ),
    ).fetchall()

    batches: dict[str, list[dict]] = defaultdict(list)
    temporally_misaligned_candidates = 0
    temporally_misaligned_batch_ids: set[str] = set()
    for row in rows:
        candidate = dict(row)
        ranked_at = _parse_dt(candidate.get("ranked_at"))
        entry_at = _parse_dt(candidate.get("entry_ts"))
        if ranked_at is None or entry_at is None:
            temporally_misaligned_candidates += 1
            temporally_misaligned_batch_ids.add(str(row["batch_id"]))
            continue
        lag_sec = (ranked_at - entry_at).total_seconds()
        if lag_sec < 0 or lag_sec > _RANKER_ENTRY_ALIGNMENT_MAX_SEC:
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
        entry_times = [_parse_dt(r.get("entry_ts")) for r in batch_rows]
        if any(ts is None for ts in entry_times):
            temporally_mixed_batches += 1
            continue
        valid_entry_times = [ts for ts in entry_times if ts is not None]
        if (
            max(valid_entry_times) - min(valid_entry_times)
        ).total_seconds() > _RANKER_ENTRY_ALIGNMENT_MAX_SEC:
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
        "first_exposure_alignment_max_sec": _RANKER_ENTRY_ALIGNMENT_MAX_SEC,
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


def _system_operator_alpha(db, cutoff: str, min_sample_size: int) -> dict:
    first_exposure = (
        "WITH operator_rows AS ("
        " SELECT fd.*, fd.rowid AS source_rowid, pd.action, pd.runtime_session_id,"
        " pd.producer_commit, pd.behavior_fingerprint,"
        " MIN(fd.evaluated_at) OVER "
        " (PARTITION BY fd.outcome_candidate_key) AS first_evaluated_at"
        " FROM filter_decisions fd JOIN pi_decisions pd"
        " ON fd.candidate_key=('pi_decision:' || pd.id)"
        " WHERE fd.candidate_kind='pi_decision' AND fd.stage='system_operator'"
        " AND fd.outcome_candidate_key IS NOT NULL"
        " AND pd.signal_source='market_observer' AND pd.opportunity_id IS NOT NULL"
        " AND pd.runtime_session_id IS NOT NULL AND pd.producer_commit IS NOT NULL"
        " AND pd.behavior_fingerprint IS NOT NULL AND pd.producer_commit=fd.code_commit"
        "), first_rows AS ("
        " SELECT * FROM operator_rows"
        " WHERE evaluated_at=first_evaluated_at"
        ") "
    )
    contract = db.execute(
        first_exposure + "SELECT fd.producer_commit, fd.behavior_fingerprint, fo.cost_model_version "
        "FROM first_rows fd JOIN filter_candidate_outcomes fo "
        "ON fo.outcome_candidate_key=fd.outcome_candidate_key "
        "WHERE fo.horizon='24h' "
        "AND fo.status='settled' AND fo.net_return_pct IS NOT NULL "
        "AND fo.cost_model_version IS NOT NULL "
        "AND fd.venue=fo.venue AND fd.symbol=fo.symbol "
        "AND lower(fd.direction)=lower(fo.direction) "
        "AND fd.market_type=fo.market_type "
        "AND datetime(fo.entry_ts) >= datetime(?) "
        "ORDER BY datetime(fo.entry_ts) DESC, fd.source_rowid DESC LIMIT 1",
        (cutoff,),
    ).fetchone()
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
        first_exposure + "SELECT fd.outcome_candidate_key, fd.action, fo.net_return_pct, "
        "MAX(CASE WHEN fd.decision='rejected' THEN 1 ELSE 0 END) AS rejected, "
        "MAX(CASE WHEN fd.decision IN ('passed','passed_with_penalty') THEN 1 ELSE 0 END) AS passed "
        "FROM first_rows fd JOIN filter_candidate_outcomes fo "
        "ON fo.outcome_candidate_key=fd.outcome_candidate_key "
        "WHERE fd.producer_commit=? AND fd.behavior_fingerprint=? AND fo.cost_model_version=? "
        "AND fo.horizon='24h' AND fo.status='settled' AND fo.net_return_pct IS NOT NULL "
        "AND fd.venue=fo.venue AND fd.symbol=fo.symbol "
        "AND lower(fd.direction)=lower(fo.direction) AND fd.market_type=fo.market_type "
        "AND datetime(fo.entry_ts) >= datetime(?) "
        "GROUP BY fd.outcome_candidate_key, fd.action, fo.net_return_pct",
        (
            contract["producer_commit"],
            contract["behavior_fingerprint"],
            contract["cost_model_version"],
            cutoff,
        ),
    ).fetchall()

    accepted = [
        float(row["net_return_pct"])
        for row in rows
        if row["action"] == "trade" and not int(row["rejected"] or 0) and int(row["passed"] or 0)
    ]
    rejected = [
        float(row["net_return_pct"])
        for row in rows
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


def _dedicated_learning_alpha(db, cutoff: str, min_sample_size: int) -> dict | None:
    from aletheia.learning.cohorts import SUPPORTED_PATH_IDS
    from aletheia.learning.fixed_look import (
        get_consumed_fixed_look_verdict,
        get_fixed_look_decision,
    )

    path_ids = sorted(SUPPORTED_PATH_IDS)
    if not path_ids:
        return None
    placeholders = ",".join("?" for _ in path_ids)
    rows = db.execute(
        "SELECT e.experiment_id, e.hypothesis_id, e.stage, e.min_samples_per_arm, "
        "(SELECT MIN(x.exposed_at) FROM learning_exposures x "
        " WHERE x.experiment_id=e.experiment_id) AS first_exposed_at, "
        "h.path_id, h.target_horizon FROM learning_experiments e "
        "JOIN learning_hypotheses h ON h.hypothesis_id=e.hypothesis_id "
        f"WHERE e.ended_at IS NULL AND h.path_id IN ({placeholders}) "
        "ORDER BY datetime(e.created_at) DESC, e.experiment_id DESC",
        tuple(path_ids),
    ).fetchall()
    valid: list[tuple[dict, dict]] = []
    invalid: list[dict] = []
    required_identity = (
        "policy_epoch",
        "stage",
        "target_horizon",
        "outcome_contract_version",
        "metric_version",
        "code_commit",
        "behavior_contract_hash",
        "inference_contract_version",
        "inference_plan_hash",
        "inference_cutoff_fingerprint",
    )
    for raw in rows:
        experiment = dict(raw)
        decision = get_fixed_look_decision(experiment["experiment_id"])
        if decision is None or decision.status != "consumed":
            continue
        verdict = get_consumed_fixed_look_verdict(experiment["experiment_id"])
        if verdict is None:
            invalid.append({"experiment_id": experiment["experiment_id"], "reason": "consumed_verdict_invalid"})
            continue
        mismatch = next(
            (
                reason
                for reason, matches in (
                    ("hypothesis_mismatch", verdict.get("hypothesis_id") == experiment["hypothesis_id"]),
                    ("stage_mismatch", verdict.get("stage") == experiment["stage"]),
                    ("horizon_mismatch", verdict.get("target_horizon") == experiment["target_horizon"]),
                    ("identity_incomplete", all(verdict.get(field) not in {None, ""} for field in required_identity)),
                    ("sample_contract_unmet", min(int(verdict.get("unique_treatment_n") or 0), int(verdict.get("unique_control_n") or 0)) >= int(experiment["min_samples_per_arm"] or min_sample_size)),
                    ("economic_result_incomplete", all(verdict.get(field) is not None for field in ("baseline", "observed", "delta"))),
                )
                if not matches
            ),
            None,
        )
        if mismatch:
            invalid.append({"experiment_id": experiment["experiment_id"], "reason": mismatch})
            continue
        window_start = _parse_dt(verdict.get("window_start"))
        window_end = _parse_dt(verdict.get("window_end"))
        cutoff_at = _parse_dt(cutoff)
        if (
            window_start is None
            or window_end is None
            or cutoff_at is None
            or window_start < cutoff_at
            or window_end < window_start
        ):
            continue
        valid.append((experiment, verdict))

    if len(valid) > 1 or invalid:
        reason = (
            "Several current Learning estimands have authority verdicts and cannot be aggregated."
            if len(valid) > 1
            else "A consumed Learning verdict failed its exact identity contract."
        )
        return _verdict(
            "Learning Alpha",
            "insufficient_data",
            0,
            None,
            None,
            None,
            "insufficient",
            reason,
            {
                "valid_verdict_count": len(valid),
                "invalid_verdicts": invalid,
                "path_ids": sorted({row[0]["path_id"] for row in valid}),
            },
            evidence_type="dedicated_learning_contribution_verdict",
            causal_grade="insufficient",
        )
    if not valid:
        cutoff_at = _parse_dt(cutoff)
        experiment_rows = {row["experiment_id"]: dict(row) for row in rows}
        paper_experiment_ids = {
            experiment_id
            for experiment_id, experiment in experiment_rows.items()
            if str(experiment.get("stage") or "").startswith("paper_")
            and cutoff_at is not None
            and (first_exposed_at := _parse_dt(experiment.get("first_exposed_at")))
            is not None
            and first_exposed_at >= cutoff_at
        }
        if not paper_experiment_ids:
            return None
        try:
            from aletheia.learning.read_models import build_learning_contribution_projection

            projection = build_learning_contribution_projection(limit=100)
        except Exception:
            return _verdict(
                "Learning Alpha",
                "insufficient_data",
                0,
                None,
                None,
                None,
                "insufficient",
                "A current Paper Learning experiment exists, but its contribution projection is unavailable.",
                {"experiment_ids": sorted(paper_experiment_ids)},
                evidence_type="dedicated_learning_contribution_monitoring",
                causal_grade="insufficient",
            )
        current_paper = [
            item
            for item in projection.get("items", [])
            if item.get("experiment_id") in paper_experiment_ids
        ]
        if not current_paper:
            return _verdict(
                "Learning Alpha",
                "insufficient_data",
                0,
                None,
                None,
                None,
                "insufficient",
                "A current Paper Learning experiment exists, but no current exact measurement projection is available.",
                {"experiment_ids": sorted(paper_experiment_ids)},
                evidence_type="dedicated_learning_contribution_monitoring",
                causal_grade="insufficient",
            )
        if len(current_paper) > 1:
            return _verdict(
                "Learning Alpha",
                "insufficient_data",
                0,
                None,
                None,
                None,
                "insufficient",
                "Several current Paper Learning estimands are collecting and cannot be aggregated.",
                {
                    "experiment_ids": [row["experiment_id"] for row in current_paper],
                    "path_ids": [row["path_id"] for row in current_paper],
                },
                evidence_type="dedicated_learning_contribution_monitoring",
                causal_grade="insufficient",
            )

        item = current_paper[0]
        measurement = item.get("measurement") or {}
        samples = item.get("samples") or {}
        integrity = item.get("integrity") or {}
        state = str(measurement.get("state") or "not_measured")
        state_has_delta = state in {"positive", "neutral", "negative"}
        status = state if state_has_delta else "insufficient_data"
        window_start = measurement.get("window_start")
        window_end = measurement.get("window_end")
        window_start_at = _parse_dt(window_start)
        window_end_at = _parse_dt(window_end)
        window_compatible = not (
            cutoff_at is None
            or window_start_at is None
            or window_end_at is None
            or window_start_at < cutoff_at
            or window_end_at < window_start_at
        )
        if state_has_delta and not window_compatible:
            status = "insufficient_data"
        sample_size = min(
            int(samples.get("treatment_assignment_units") or 0),
            int(samples.get("control_assignment_units") or 0),
        )
        return _verdict(
            "Learning Alpha",
            status,
            sample_size,
            measurement.get("baseline"),
            measurement.get("observed"),
            measurement.get("delta") if status != "insufficient_data" else None,
            _confidence_label(sample_size, min_sample_size),
            (
                "Current Paper Learning monitoring window is unavailable or outside the audit window."
                if state_has_delta and not window_compatible
                else f"Current Paper Learning monitoring verdict is {state}, but only a consumed "
                "confirmatory verdict can establish contribution."
            ),
            {
                "path_id": item.get("path_id"),
                "experiment_id": item.get("experiment_id"),
                "stage": item.get("stage"),
                "allocation_pct": item.get("allocation_pct"),
                "measurement_reason": measurement.get("reason"),
                "measurement_authority_eligible": bool(measurement.get("authority_eligible")),
                "window_start": window_start,
                "window_end": window_end,
                "audit_window_compatible": window_compatible,
                "raw_exposures": int(samples.get("raw_exposures") or 0),
                "unique_treatment_n": int(samples.get("treatment_assignment_units") or 0),
                "unique_control_n": int(samples.get("control_assignment_units") or 0),
                "treatment_members": samples.get("treatment_members"),
                "treatment_selected_members": samples.get(
                    "treatment_selected_members"
                ),
                "control_members": samples.get("control_members"),
                "control_selected_members": samples.get("control_selected_members"),
                "actual_intervention_treatment_units": int(
                    samples.get("actual_intervention_treatment_units") or 0
                ),
                "exposed_actual_intervention_treatment_units": int(
                    samples.get("exposed_actual_intervention_treatment_units") or 0
                ),
                "actual_intervention_state": integrity.get("actual_intervention_state"),
                "exclusion_reasons": integrity.get("exclusion_reasons") or {},
                "current_identity": item.get("current_identity") or {},
                "inference_status": (item.get("inference") or {}).get("status"),
            },
            evidence_type="dedicated_learning_contribution_monitoring",
            causal_grade=(
                "moderate_prospective_monitoring"
                if status != "insufficient_data"
                else "insufficient"
            ),
        )

    experiment, verdict = valid[0]
    status = str(verdict["verdict"])
    if status not in {"positive", "neutral", "negative"}:
        return _verdict(
            "Learning Alpha",
            "insufficient_data",
            min(int(verdict["unique_treatment_n"]), int(verdict["unique_control_n"])),
            verdict.get("baseline"),
            verdict.get("observed"),
            None,
            "insufficient",
            f"The exact confirmatory Learning verdict is {status}, so it cannot establish contribution.",
            {"verdict_id": verdict["verdict_id"], "path_id": experiment["path_id"]},
            evidence_type="dedicated_learning_contribution_verdict",
            causal_grade="insufficient",
        )
    sample_size = min(int(verdict["unique_treatment_n"]), int(verdict["unique_control_n"]))
    return _verdict(
        "Learning Alpha",
        status,
        sample_size,
        verdict["baseline"],
        verdict["observed"],
        verdict["delta"],
        _confidence_label(sample_size, min_sample_size),
        f"Learning path {experiment['path_id']} has a consumed exact confirmatory contribution verdict: {verdict['reason']}",
        {
            "path_id": experiment["path_id"],
            "experiment_id": experiment["experiment_id"],
            "hypothesis_id": experiment["hypothesis_id"],
            "verdict_id": verdict["verdict_id"],
            "unique_treatment_n": int(verdict["unique_treatment_n"]),
            "unique_control_n": int(verdict["unique_control_n"]),
            "window_start": verdict["window_start"],
            "window_end": verdict["window_end"],
            "policy_epoch": verdict["policy_epoch"],
            "target_horizon": verdict["target_horizon"],
            "outcome_contract_version": verdict["outcome_contract_version"],
            "metric_version": verdict["metric_version"],
            "code_commit": verdict["code_commit"],
            "behavior_contract_hash": verdict["behavior_contract_hash"],
            "inference_contract_version": verdict["inference_contract_version"],
            "inference_plan_hash": verdict["inference_plan_hash"],
            "inference_cutoff_fingerprint": verdict["inference_cutoff_fingerprint"],
            "harm_metrics": _parse_json(verdict.get("harm_metrics_json")),
        },
        evidence_type="dedicated_learning_contribution_verdict",
        causal_grade="strong_prospective_control",
    )


def _learning_alpha(db, cutoff: str, days: int, min_sample_size: int) -> dict:
    dedicated = _dedicated_learning_alpha(db, cutoff, min_sample_size)
    if dedicated is not None:
        return dedicated
    raw_events = _learning_events(db, cutoff)
    events = _dedupe_learning_events(raw_events)
    if not raw_events:
        return _verdict(
            "Learning Alpha",
            "insufficient_data",
            0,
            None,
            None,
            None,
            "insufficient",
            "No structured learning/memo/guard events in the audit window.",
            {"event_count": 0, "raw_event_count": 0, "unique_event_count": 0},
        )

    before_avgs: list[float] = []
    after_avgs: list[float] = []
    raw_after_count = 0
    unique_after: dict[str, float] = {}
    unique_before: dict[str, float] = {}
    unique_control_after: dict[str, float] = {}
    unique_control_before: dict[str, float] = {}
    after_membership: dict[str, int] = defaultdict(int)
    usable_events = 0
    did_events = 0
    total_events_with_group = 0

    for event in events:
        group = event["group"]
        if not group:
            continue
        total_events_with_group += 1
        before, after, control_before, control_after = _trade_pnl_before_after(db, event["event_at"], days, group)
        raw_after_count += len(after)
        for key, value in before.items():
            unique_before.setdefault(key, value)
        for key, value in after.items():
            unique_after.setdefault(key, value)
            after_membership[key] += 1
        for key, value in control_before.items():
            unique_control_before.setdefault(key, value)
        for key, value in control_after.items():
            unique_control_after.setdefault(key, value)
        if len(before) < min_sample_size or len(after) < min_sample_size:
            continue
        before_avgs.append(mean(before.values()))
        after_avgs.append(mean(after.values()))
        usable_events += 1
        if len(control_before) >= min_sample_size and len(control_after) >= min_sample_size:
            did_events += 1

    if usable_events == 0:
        return _verdict(
            "Learning Alpha",
            "insufficient_data",
            len(unique_after),
            _safe_mean(before_avgs),
            _safe_mean(after_avgs),
            None,
            "insufficient",
            "Learning events exist, but there are not enough before/after same-pattern closed trades to judge behavior change.",
            {
                "event_count": len(events),
                "raw_event_count": len(raw_events),
                "unique_event_count": len(events),
                "duplicate_event_count": len(raw_events) - len(events),
                "events_with_group": total_events_with_group,
                "usable_events": 0,
                "raw_after_sample_size": raw_after_count,
                "unique_after_sample_size": len(unique_after),
            },
            evidence_type="same_pattern_before_after",
            causal_grade="insufficient",
        )

    if (
        len(unique_control_before) >= min_sample_size
        and len(unique_control_after) >= min_sample_size
    ):
        observed = mean(unique_after.values()) - mean(unique_before.values())
        base = mean(unique_control_after.values()) - mean(unique_control_before.values())
        reason_basis = "difference-in-differences against contemporaneous non-matching trade controls"
        evidence_type = "difference_in_differences"
        causal_grade = "moderate_quasi_causal"
    else:
        observed = mean(unique_after.values())
        base = mean(unique_before.values())
        reason_basis = "same-pattern before/after comparison without enough controls"
        evidence_type = "same_pattern_before_after"
        causal_grade = "weak_before_after"
    delta = observed - base
    status = _status_from_delta(delta)
    return _verdict(
        "Learning Alpha",
        status,
        len(unique_after),
        base,
        observed,
        delta,
        _confidence_label(len(unique_after), min_sample_size),
        f"Learning-treated patterns {'improved' if delta > 0 else 'worsened' if delta < 0 else 'matched'} using {reason_basis}.",
        {
            "event_count": len(events),
            "raw_event_count": len(raw_events),
            "unique_event_count": len(events),
            "duplicate_event_count": len(raw_events) - len(events),
            "events_with_group": total_events_with_group,
            "usable_events": usable_events,
            "did_events": did_events,
            "raw_after_sample_size": raw_after_count,
            "unique_after_sample_size": len(unique_after),
            "unique_before_sample_size": len(unique_before),
            "unique_control_after_sample_size": len(unique_control_after),
            "unique_control_before_sample_size": len(unique_control_before),
            "overlapping_after_sample_size": sum(1 for count in after_membership.values() if count > 1),
        },
        evidence_type=evidence_type,
        causal_grade=causal_grade,
    )


def _serenity_alpha(db, cutoff: str, min_sample_size: int) -> dict:
    causal = _causal_control_alpha(db, "serenity", "Serenity Alpha", cutoff, min_sample_size)
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


def _learning_events(db, cutoff: str) -> list[dict]:
    events: list[dict] = []
    lesson_rows = db.execute(
        "SELECT lesson_id AS id, source_type, source_id, evidence_json, recommendation_json, "
        "COALESCE(activated_at, created_at) AS event_at "
        "FROM ps_lessons "
        "WHERE status IN ('accepted', 'applied') "
        "AND datetime(COALESCE(activated_at, created_at)) >= datetime(?)",
        (cutoff,),
    ).fetchall()
    for row in lesson_rows:
        evidence = _parse_json(row["evidence_json"])
        recommendation = _parse_json(row["recommendation_json"])
        events.append({
            "id": row["id"],
            "event_kind": "lesson",
            "source_type": row["source_type"],
            "source_id": row["source_id"],
            "event_at": row["event_at"],
            "group": _extract_learning_group(evidence, recommendation),
            "action": recommendation.get("action"),
        })

    knowledge_rows = db.execute(
        "SELECT id, item_type, tags, created_at AS event_at FROM knowledge_items "
        "WHERE item_type IN ('trading_memo', 'trading_guard') "
        "AND datetime(created_at) >= datetime(?)",
        (cutoff,),
    ).fetchall()
    for row in knowledge_rows:
        tags = _parse_json(row["tags"])
        events.append({
            "id": row["id"],
            "event_kind": row["item_type"],
            "source_type": row["item_type"],
            "source_id": row["id"],
            "event_at": row["event_at"],
            "group": _extract_learning_group(tags),
            "action": tags.get("action") if isinstance(tags, dict) else None,
        })
    return events


def _dedupe_learning_events(events: list[dict]) -> list[dict]:
    """Collapse repeated artifact rows into one scope/action cohort.

    Prose, artifact ids, and retry timestamps are intentionally excluded. The
    earliest activation anchors the weak before/after window; later duplicates
    cannot reset the clock or multiply the same closed positions.
    """
    by_fingerprint: dict[str, dict] = {}
    for event in events:
        payload = {
            "event_kind": event.get("event_kind"),
            "source_type": event.get("source_type"),
            "group": event.get("group") or {},
            "action": event.get("action"),
        }
        fingerprint = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        current = by_fingerprint.get(fingerprint)
        if current is None or str(event.get("event_at") or "") < str(current.get("event_at") or ""):
            by_fingerprint[fingerprint] = event
    return sorted(by_fingerprint.values(), key=lambda row: str(row.get("event_at") or ""))


def _trade_pnl_before_after(db, event_at: str, days: int, group: dict) -> tuple[dict[str, float], dict[str, float], dict[str, float], dict[str, float]]:
    event_dt = _parse_dt(event_at)
    if event_dt is None:
        return {}, {}, {}, {}
    before_start = (event_dt - timedelta(days=days)).isoformat()
    after_end = _utcnow().isoformat()
    rows = []
    for position in eligible_strategy_outcomes(
        closed_after=before_start, closed_before=after_end,
    ):
        scanner_family = None
        proposal_id = position.get("proposal_id")
        if proposal_id:
            metadata = db.execute(
                "SELECT o.scanner_family FROM trade_proposals tp "
                "LEFT JOIN opportunities o ON o.id = tp.opportunity_id "
                "WHERE tp.id = ?",
                (proposal_id,),
            ).fetchone()
            scanner_family = metadata["scanner_family"] if metadata else None
        rows.append({
            **position,
            "position_id": position["id"],
            "scanner_family": scanner_family,
        })

    before: dict[str, float] = {}
    after: dict[str, float] = {}
    control_before: dict[str, float] = {}
    control_after: dict[str, float] = {}
    for row in rows:
        notional = float(row.get("entry_price") or 0) * float(row.get("quantity") or 0)
        if notional <= 0:
            continue
        pnl_pct = float(row["realized_pnl"]) / notional * 100.0
        closed_at = _parse_dt(row.get("closed_at"))
        if closed_at is None:
            continue
        matches = _matches_learning_group(row, group)
        position_id = f"position:{row['position_id']}"
        if matches and closed_at < event_dt:
            before[position_id] = pnl_pct
        elif matches:
            after[position_id] = pnl_pct
        elif closed_at < event_dt:
            control_before[position_id] = pnl_pct
        else:
            control_after[position_id] = pnl_pct
    return before, after, control_before, control_after


def _matches_learning_group(row, group: dict) -> bool:
    for key in ("scanner_family", "direction", "symbol"):
        expected = group.get(key)
        if expected and str(row[key] or "").lower() != str(expected).lower():
            return False
    bucket = group.get("failure_bucket") or group.get("close_reason")
    if bucket and str(row["close_reason"] or "").lower() != str(bucket).lower():
        return False
    return True


def _extract_learning_group(*objects: Any) -> dict:
    group = {
        "scanner_family": None,
        "direction": None,
        "symbol": None,
        "failure_bucket": None,
        "close_reason": None,
    }
    for key in list(group):
        for obj in objects:
            value = _find_key(obj, key)
            if value is not None:
                group[key] = value
                break
    if group["close_reason"] and not group["failure_bucket"]:
        group["failure_bucket"] = group["close_reason"]
    return {k: v for k, v in group.items() if v not in (None, "")}


def _find_key(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for value in obj.values():
            found = _find_key(value, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_key(item, key)
            if found is not None:
                return found
    return None


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


def _causal_control_alpha(db, layer: str, name: str, cutoff: str, min_sample_size: int) -> dict | None:
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
        ret = _control_observation_return(db, row)
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


def _control_observation_return(db, row) -> float | None:
    outcome_key = row["outcome_candidate_key"]
    if not outcome_key:
        return None
    outcome = db.execute(
        "SELECT net_return_pct FROM filter_candidate_outcomes "
        "WHERE outcome_candidate_key=? AND horizon=? AND status='settled' "
        "AND net_return_pct IS NOT NULL LIMIT 1",
        (outcome_key, row["horizon"]),
    ).fetchone()
    return float(outcome["net_return_pct"]) if outcome else None


def _exit_price_from_ohlcv(db, symbol: str, due_at: str) -> float | None:
    due_dt = _parse_dt(due_at)
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
    entry_dt = _parse_dt(entry_at)
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
        "ranker": "Ranker top1 underperforms lower-ranked candidates. Inspect scoring weights, penalties, and eligibility filters.",
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


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_days(days: int) -> int:
    if days <= 0:
        return 30
    if days not in {7, 14, 30}:
        return days
    return days
