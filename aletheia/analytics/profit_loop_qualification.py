"""Whole-policy profitability qualification for a frozen policy cohort.

Read-only. Never writes to DB, modifies config, or grants behavior authority.
Resolves Issue #988: prove or reject the minimum net-profitable frozen policy cohort.

The registration dict must be written BEFORE the first eligible prospective
Position and must not be changed after outcome inspection begins.  All required
keys — including cutoff_at — must be present in the registration; supplying
cutoff_at only at evaluation time is rejected to prevent optional stopping.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from aletheia.analytics.outcome_integrity import (
    OUTCOME_SEMANTICS_VERSION,
    eligible_strategy_outcomes,
    build_outcome_cohort_summary,
)
from aletheia.storage.hashing import sha256_json


QUALIFICATION_CONTRACT_VERSION = "profit_loop_qualification/v1"

# All keys that must be present and non-empty in the preregistered dict.
# cutoff_at is included here so the stopping rule cannot be changed at runtime.
REQUIRED_REGISTRATION_KEYS: tuple[str, ...] = (
    "deploy_sha",
    "runtime_session_id",
    "execution_mode",
    "policy_epoch_id",
    "improvement_id",
    "registration_at",
    "stopping_rule",
    "cost_model_id",
    "cutoff_at",
)

# Preregistered decision thresholds — must not be adjusted after cohort start.
_DEFAULT_CONCENTRATION_TOP1_CAP = 0.25
_DEFAULT_CONCENTRATION_TOP5_CAP = 0.50
_MIN_ELIGIBLE_DAYS = 2


def _to_float(v: Any) -> float | None:
    try:
        f = float(v)  # type: ignore[arg-type]
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _concentration_values(pnls: list[float]) -> tuple[float | None, float | None]:
    """Top-1 and Top-5 absolute-contribution share of the total absolute PnL."""
    contributions = sorted((abs(p) for p in pnls), reverse=True)
    total = sum(contributions)
    if not math.isfinite(total):
        return math.nan, math.nan
    if total <= 0:
        return None, None
    return contributions[0] / total, sum(contributions[:5]) / total


def _profit_factor(pnls: list[float]) -> float | None:
    wins = sum(p for p in pnls if p > 0)
    losses = abs(sum(p for p in pnls if p < 0))
    if losses == 0:
        return None if wins == 0 else math.inf
    return round(wins / losses, 6)


def _max_drawdown_from_peak(ordered_pnls: list[float]) -> float | None:
    """Max drawdown as fraction of peak cumulative equity."""
    if not ordered_pnls:
        return None
    peak = 0.0
    equity = 0.0
    max_dd = 0.0
    for pnl in ordered_pnls:
        equity += pnl
        if equity > peak:
            peak = equity
        if peak > 0:
            dd = (peak - equity) / peak
            if dd > max_dd:
                max_dd = dd
    return round(max_dd, 6)


def compile_qualification_verdict(
    *,
    registration: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate a frozen policy cohort and return an immutable hashed verdict.

    Parameters
    ----------
    registration:
        Pre-registered cohort identity.  Required keys (see REQUIRED_REGISTRATION_KEYS;
        all others are preserved verbatim in the artifact):
          - deploy_sha          exact deployed commit hash
          - runtime_session_id  immutable Runtime Session identifier
          - execution_mode      "paper_internal" (only supported mode)
          - policy_epoch_id     frozen policy epoch tied to this cohort
          - improvement_id      the one selected single-variable improvement
          - registration_at     ISO-8601 start time (before first eligible Position)
          - stopping_rule       human-readable description of the stopping rule
          - cost_model_id       declared cost model for this cohort
          - cutoff_at           frozen UTC evaluation cutoff (ISO-8601 with timezone);
                                must be preregistered — supplying it only at CLI time
                                is rejected to prevent optional stopping
        Optional keys:
          - max_drawdown_cap    float 0–1; required for the drawdown gate to be
                                enforced rather than recorded-only
          - concentration_top1_cap   float (default 0.25)
          - concentration_top5_cap   float (default 0.50)
          - known_exclusions    list[str] of pre-declared integrity incidents
    """
    # ------------------------------------------------------------------
    # Validate required registration fields
    # ------------------------------------------------------------------
    missing = [k for k in REQUIRED_REGISTRATION_KEYS if not registration.get(k)]
    if missing:
        raise ValueError(
            f"registration missing required keys: {missing!r}; "
            "all fields including cutoff_at must be preregistered before outcome inspection"
        )

    cutoff_at = str(registration["cutoff_at"])
    registration_at = str(registration["registration_at"])
    runtime_session_id = str(registration["runtime_session_id"])
    execution_mode = str(registration["execution_mode"])

    # Temporal consistency: registration must precede the declared stopping rule.
    try:
        reg_dt = datetime.fromisoformat(registration_at.replace("Z", "+00:00"))
        cut_dt = datetime.fromisoformat(cutoff_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"registration_at or cutoff_at is not a valid ISO-8601 timestamp: {exc}"
        ) from exc
    if reg_dt >= cut_dt:
        raise ValueError(
            f"registration_at ({registration_at!r}) must be strictly before "
            f"cutoff_at ({cutoff_at!r}); preregistration cannot postdate the stopping rule"
        )
    max_drawdown_cap = _to_float(registration.get("max_drawdown_cap"))
    top1_cap = _to_float(registration.get("concentration_top1_cap")) or _DEFAULT_CONCENTRATION_TOP1_CAP
    top5_cap = _to_float(registration.get("concentration_top5_cap")) or _DEFAULT_CONCENTRATION_TOP5_CAP

    # ------------------------------------------------------------------
    # Fetch eligible genuine outcomes for this cohort window
    # ------------------------------------------------------------------
    # Window filters (time + mode only) — used for mismatch counting.
    window_kwargs: dict[str, Any] = {
        "execution_mode": execution_mode,
        "closed_after": registration_at,
        "closed_before": cutoff_at,
    }

    # Identity-bound fetch: only rows that prove registration membership.
    fetch_kwargs: dict[str, Any] = {**window_kwargs, "runtime_session_id": runtime_session_id}

    all_outcomes = eligible_strategy_outcomes(**fetch_kwargs)

    # Post-filter: only positions *opened* on or after the registration date
    # to exclude pre-policy positions that may close inside the window.
    cohort_outcomes = [
        row for row in all_outcomes
        if str(row.get("opened_at") or "") >= registration_at
    ]

    exclusion_summary = build_outcome_cohort_summary(**fetch_kwargs)
    window_summary = build_outcome_cohort_summary(**window_kwargs)

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------
    pnl_with_time: list[tuple[str, float]] = []
    for row in cohort_outcomes:
        pnl = _to_float(row.get("realized_pnl"))
        if pnl is None:
            continue
        pnl_with_time.append((str(row.get("closed_at") or ""), pnl))

    pnl_with_time.sort(key=lambda x: x[0])
    ordered_pnls = [p for _, p in pnl_with_time]
    dates = {ts[:10] for ts, _ in pnl_with_time if ts}

    net_pnl = round(sum(ordered_pnls), 8) if ordered_pnls else 0.0
    profit_factor = _profit_factor(ordered_pnls)
    win_count = sum(1 for p in ordered_pnls if p > 0)
    loss_count = sum(1 for p in ordered_pnls if p < 0)
    flat_count = sum(1 for p in ordered_pnls if p == 0)
    max_dd = _max_drawdown_from_peak(ordered_pnls)
    top1_conc, top5_conc = _concentration_values(ordered_pnls)
    eligible_days = len(dates)

    metrics: dict[str, Any] = {
        "net_realized_pnl": net_pnl,
        "profit_factor": profit_factor,
        "win_count": win_count,
        "loss_count": loss_count,
        "flat_count": flat_count,
        "total_eligible_closes": len(ordered_pnls),
        "eligible_calendar_days": eligible_days,
        "max_drawdown_from_peak": max_dd,
        "top1_abs_concentration": top1_conc,
        "top5_abs_concentration": top5_conc,
    }

    window_raw = window_summary.get("raw_event_count") or 0
    identity_raw = exclusion_summary.get("raw_event_count") or 0
    funnel: dict[str, Any] = {
        "raw_event_count": exclusion_summary.get("raw_event_count"),
        "eligible_outcome_count": exclusion_summary.get("eligible_outcome_count"),
        "excluded_outcome_count": exclusion_summary.get("excluded_outcome_count"),
        "cohort_filtered_count": len(ordered_pnls),
        "pre_policy_excluded_count": len(all_outcomes) - len(cohort_outcomes),
        "identity_mismatched_count": window_raw - identity_raw,
        "outcome_exclusion_reasons": exclusion_summary.get("outcome_exclusion_reasons", {}),
        "outcome_semantics_version": OUTCOME_SEMANTICS_VERSION,
    }

    # ------------------------------------------------------------------
    # Decision gates
    # ------------------------------------------------------------------
    def _gate(pass_: bool | None, value: Any, threshold: str) -> dict[str, Any]:
        return {"pass": pass_, "value": value, "threshold": threshold}

    pf_finite = profit_factor is not None and math.isfinite(float(profit_factor))

    gates: dict[str, Any] = {
        "has_eligible_outcomes": _gate(
            len(ordered_pnls) > 0, len(ordered_pnls), "> 0"
        ),
        "net_pnl_positive": _gate(
            net_pnl > 0 if ordered_pnls else False, net_pnl, "> 0"
        ),
        "profit_factor_above_1": _gate(
            bool(profit_factor is not None and pf_finite and float(profit_factor) > 1.0),
            profit_factor,
            "> 1.0",
        ),
        "multiple_eligible_days": _gate(
            eligible_days >= _MIN_ELIGIBLE_DAYS, eligible_days, f">= {_MIN_ELIGIBLE_DAYS}"
        ),
        "concentration_top1": _gate(
            (top1_conc is None) or (
                math.isfinite(top1_conc) and top1_conc <= top1_cap
            ),
            top1_conc,
            f"<= {top1_cap}",
        ),
        "concentration_top5": _gate(
            (top5_conc is None) or (
                math.isfinite(top5_conc) and top5_conc <= top5_cap
            ),
            top5_conc,
            f"<= {top5_cap}",
        ),
        "max_drawdown_bounded": (
            _gate(max_dd is None or max_dd <= max_drawdown_cap, max_dd, f"<= {max_drawdown_cap}")
            if max_drawdown_cap is not None
            else _gate(None, max_dd, "cap not preregistered — recorded only")
        ),
    }

    hard_gate_keys = [
        "has_eligible_outcomes",
        "net_pnl_positive",
        "profit_factor_above_1",
        "multiple_eligible_days",
        "concentration_top1",
        "concentration_top5",
    ]
    if max_drawdown_cap is not None:
        hard_gate_keys.append("max_drawdown_bounded")

    failed_gates = [k for k in hard_gate_keys if gates[k]["pass"] is False]

    if not gates["has_eligible_outcomes"]["pass"]:
        verdict = "insufficient_data"
    elif failed_gates:
        verdict = "minimum_profitable_loop_rejected"
        gates["failed_gates"] = failed_gates
    else:
        verdict = "minimum_profitable_loop_proven"

    # ------------------------------------------------------------------
    # Immutable artifact
    # ------------------------------------------------------------------
    # registration_hash lets callers detect if the same session is re-evaluated
    # with a different registration blob (different hash = different preregistration).
    registration_hash = sha256_json(registration)

    artifact: dict[str, Any] = {
        "contract_version": QUALIFICATION_CONTRACT_VERSION,
        "verdict": verdict,
        "registration": registration,
        "registration_hash": registration_hash,
        "metrics": metrics,
        "gates": gates,
        "funnel": funnel,
        "outcome_semantics_version": OUTCOME_SEMANTICS_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    artifact["content_hash"] = sha256_json(
        {k: v for k, v in artifact.items() if k != "content_hash"}
    )
    return artifact
