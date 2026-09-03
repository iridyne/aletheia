"""Whole-policy profitability qualification for a frozen policy cohort.

Read-only. Never writes to DB, modifies config, or grants behavior authority.
Resolves Issue #988: prove or reject the minimum net-profitable frozen policy cohort.

The registration dict must be written BEFORE the first eligible prospective
Position and must not be changed after outcome inspection begins.  All required
keys — including cutoff_at — must be present in the registration; supplying
cutoff_at only at evaluation time is rejected to prevent optional stopping.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from aletheia.analytics.profit_gradient_evidence import _parse_utc, _iso
from typing import Any

from aletheia.analytics.outcome_integrity import (
    OUTCOME_SEMANTICS_VERSION,
    read_complete_outcome_cohort,
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


def _resolve_registration_identity(
    registration: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    """Resolve preregistered labels to existing durable runtime/policy owners."""

    from aletheia.learning.policy_epochs import build_policy_epoch
    from aletheia.storage.sqlite import get_db

    db = get_db()
    session = db.execute(
        "SELECT session_id, boot_commit, config_hash, behavior_fingerprint "
        "FROM runtime_sessions WHERE session_id=?",
        (str(registration["runtime_session_id"]),),
    ).fetchone()
    if session is None:
        return None, "runtime_session_not_recorded"
    required_session = ("boot_commit", "config_hash", "behavior_fingerprint")
    if any(not str(session[key] or "").strip() for key in required_session):
        return None, "runtime_session_identity_incomplete"
    if str(session["boot_commit"]) != str(registration["deploy_sha"]):
        return None, "deploy_sha_mismatch"

    epoch = db.execute(
        "SELECT * FROM learning_policy_epochs WHERE epoch_id=?",
        (str(registration["policy_epoch_id"]),),
    ).fetchone()
    if epoch is None:
        return None, "policy_epoch_not_recorded"
    try:
        values = json.loads(epoch["effective_values_json"])
        raw_affected_paths = json.loads(epoch["affected_path_ids_json"])
        if not isinstance(values, dict) or not isinstance(raw_affected_paths, list):
            raise ValueError
        affected_paths = tuple(str(path) for path in raw_affected_paths)
        rebuilt = build_policy_epoch(
            str(epoch["epoch_kind"]),
            values,
            affected_path_ids=affected_paths,
            safety_required=bool(epoch["safety_required"]),
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return None, "policy_epoch_malformed"
    if rebuilt.epoch_hash != epoch["effective_values_hash"]:
        return None, "policy_epoch_hash_mismatch"
    if "profit_loop_qualification" not in affected_paths:
        return None, "policy_epoch_scope_mismatch"
    expected_values = {
        "deploy_sha": str(session["boot_commit"]),
        "runtime_session_id": str(session["session_id"]),
        "behavior_fingerprint": str(session["behavior_fingerprint"]),
        "config_hash": str(session["config_hash"]),
        "improvement_id": str(registration["improvement_id"]),
        "cost_model_id": str(registration["cost_model_id"]),
    }
    if any(values.get(key) != value for key, value in expected_values.items()):
        return None, "policy_epoch_identity_mismatch"
    epoch_started = _parse_utc(epoch["started_at"], field="policy_epoch.started_at")
    registration_started = _parse_utc(
        registration["registration_at"], field="registration_at"
    )
    cutoff = _parse_utc(registration["cutoff_at"], field="cutoff_at")
    if epoch_started > registration_started:
        return None, "policy_epoch_started_after_registration"
    if epoch["ended_at"] and _parse_utc(
        epoch["ended_at"], field="policy_epoch.ended_at"
    ) < cutoff:
        return None, "policy_epoch_ended_before_cutoff"

    equivalent_sessions = {
        str(row["session_id"])
        for row in db.execute(
            "SELECT session_id FROM runtime_sessions WHERE boot_commit=? "
            "AND config_hash=? AND behavior_fingerprint=?",
            (
                session["boot_commit"],
                session["config_hash"],
                session["behavior_fingerprint"],
            ),
        ).fetchall()
    }
    return {
        **expected_values,
        "policy_epoch_id": str(epoch["epoch_id"]),
        "equivalent_runtime_session_ids": equivalent_sessions,
    }, None


def _outcome_identity_mismatch_reason(
    row: dict[str, Any], identity: dict[str, Any]
) -> str | None:
    if row.get("runtime_session_id") != identity["runtime_session_id"]:
        return "opening_runtime_session_mismatch"
    if row.get("producer_commit") != identity["deploy_sha"]:
        return "opening_deploy_sha_mismatch"
    if row.get("behavior_fingerprint") != identity["behavior_fingerprint"]:
        return "opening_behavior_fingerprint_mismatch"
    closed_session = row.get("closed_runtime_session_id")
    if not closed_session:
        return "closing_runtime_session_missing"
    if closed_session not in identity["equivalent_runtime_session_ids"]:
        return "closing_runtime_session_mismatch"
    if row.get("closed_producer_commit") != identity["deploy_sha"]:
        return "closing_deploy_sha_mismatch"
    if row.get("closed_behavior_fingerprint") != identity["behavior_fingerprint"]:
        return "closing_behavior_fingerprint_mismatch"
    return None


def _finalize_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    artifact["content_hash"] = sha256_json(artifact)
    artifact["generated_at"] = datetime.now(timezone.utc).isoformat()
    return artifact


def _to_float(v: Any) -> float | None:
    try:
        f = float(v)  # type: ignore[arg-type]
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _registration_cap(registration: dict[str, Any], key: str, default: float) -> float:
    if key not in registration:
        return default
    value = _to_float(registration[key])
    if value is None or not 0.0 <= value <= 1.0:
        raise ValueError(f"{key} must be a finite number between 0 and 1")
    return value


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


def _max_drawdown_from_peak(ordered_pnls: list[float], starting_equity: float = 0.0) -> float | None:
    """Max drawdown as fraction of peak cumulative equity.

    starting_equity defaults to 0 so loss-first cohorts are captured correctly.
    When peak is exactly 0 and equity goes negative, returns 1.0 (total loss from flat).
    """
    if not ordered_pnls:
        return None
    peak = starting_equity
    equity = starting_equity
    max_dd = 0.0
    for pnl in ordered_pnls:
        equity += pnl
        if equity > peak:
            peak = equity
        if peak > 0:
            dd = (peak - equity) / peak
            if dd > max_dd:
                max_dd = dd
        elif equity < peak:
            # peak <= 0 and equity fell further: treat as full drawdown
            max_dd = 1.0
    return round(max_dd, 6)


def compile_qualification_verdict(
    *,
    registration: dict[str, Any],
    starting_equity: float | None = None,
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
    reg_dt = _parse_utc(registration_at, field="registration_at")
    cut_dt = _parse_utc(cutoff_at, field="cutoff_at")
    if reg_dt >= cut_dt:
        raise ValueError(
            f"registration_at ({registration_at!r}) must be strictly before "
            f"cutoff_at ({cutoff_at!r}); preregistration cannot postdate the stopping rule"
        )
    # If starting_equity not provided, try pulling from registration; default 0 for loss-first support.
    if starting_equity is None:
        starting_equity = _to_float(registration.get("starting_equity"))
    if starting_equity is None or not math.isfinite(starting_equity):
        starting_equity = 0.0
    top1_cap = _registration_cap(
        registration, "concentration_top1_cap", _DEFAULT_CONCENTRATION_TOP1_CAP
    )
    top5_cap = _registration_cap(
        registration, "concentration_top5_cap", _DEFAULT_CONCENTRATION_TOP5_CAP
    )
    max_drawdown_cap = _to_float(registration.get("max_drawdown_cap"))

    registration_hash = sha256_json(registration)
    try:
        identity, identity_reason = _resolve_registration_identity(registration)
    except Exception:
        identity, identity_reason = None, "registration_identity_unavailable"
    if identity is None:
        return _finalize_artifact({
            "contract_version": QUALIFICATION_CONTRACT_VERSION,
            "verdict": "insufficient_data",
            "registration": registration,
            "registration_hash": registration_hash,
            "metrics": {"reason": identity_reason},
            "gates": {},
            "funnel": {
                "cohort_complete": False,
                "cohort_state": "unavailable",
                "identity_mismatch_reasons": {str(identity_reason): 1},
            },
            "outcome_semantics_version": OUTCOME_SEMANTICS_VERSION,
        })

    # One complete window read supplies identity, exclusions, and economics.
    cohort = read_complete_outcome_cohort(
        execution_mode=execution_mode,
        closed_after=registration_at,
        closed_before=cutoff_at,
    )
    if not cohort.cohort_complete:
        return _finalize_artifact({
            "contract_version": QUALIFICATION_CONTRACT_VERSION,
            "verdict": "insufficient_data",
            "registration": registration,
            "registration_hash": registration_hash,
            "metrics": {"reason": "outcome_cohort_unavailable"},
            "gates": {},
            "funnel": {
                "source_raw_event_count": cohort.source_raw_event_count,
                "loaded_raw_event_count": cohort.loaded_raw_event_count,
                "cohort_complete": False,
                "cohort_state": cohort.cohort_state,
                "ordered_source_digest": cohort.ordered_source_digest,
                "cohort_identity": cohort.identity,
            },
            "outcome_semantics_version": OUTCOME_SEMANTICS_VERSION,
        })

    target_pairs: list[tuple[dict[str, Any], Any]] = []
    identity_mismatch_reasons: dict[str, int] = {}
    for row, assessment in zip(cohort.rows, cohort.assessments, strict=True):
        reason = _outcome_identity_mismatch_reason(row, identity)
        if reason is None:
            target_pairs.append((row, assessment))
        else:
            identity_mismatch_reasons[reason] = (
                identity_mismatch_reasons.get(reason, 0) + 1
            )
    registered_pairs: list[tuple[dict[str, Any], Any]] = []
    try:
        for row, assessment in target_pairs:
            if _parse_utc(row.get("opened_at") or "", field="opened_at") >= reg_dt:
                registered_pairs.append((row, assessment))
    except ValueError:
        return _finalize_artifact({
            "contract_version": QUALIFICATION_CONTRACT_VERSION,
            "verdict": "insufficient_data",
            "registration": registration,
            "registration_hash": registration_hash,
            "metrics": {"reason": "invalid_opened_at"},
            "gates": {},
            "funnel": {
                "source_raw_event_count": None,
                "loaded_raw_event_count": 0,
                "cohort_complete": False,
                "cohort_state": "unavailable",
                "ordered_source_digest": None,
                "cohort_identity": {
                    **cohort.identity,
                    "runtime_session_id": runtime_session_id,
                    "opened_after": registration_at,
                },
            },
            "outcome_semantics_version": OUTCOME_SEMANTICS_VERSION,
        })

    registered_rows = [row for row, _assessment in registered_pairs]
    cohort_outcomes = [
        row for row, assessment in registered_pairs if assessment.eligible
    ]
    exclusion_reasons: dict[str, int] = {}
    for _row, assessment in registered_pairs:
        if not assessment.eligible:
            exclusion_reasons[assessment.reason] = (
                exclusion_reasons.get(assessment.reason, 0) + 1
            )
    source_raw_count = len(registered_rows)
    ordered_source_digest = sha256_json(registered_rows)

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------
    pnl_with_time: list[tuple[datetime, float]] = []
    for row in cohort_outcomes:
        pnl = _to_float(row.get("realized_pnl"))
        if pnl is None:
            continue
        closed_dt = _parse_utc(row.get("closed_at") or "", field="closed_at")
        pnl_with_time.append((closed_dt, pnl))

    pnl_with_time.sort(key=lambda x: x[0])
    ordered_pnls = [p for _, p in pnl_with_time]
    dates = {dt.date() for dt, _ in pnl_with_time}

    net_pnl = round(sum(ordered_pnls), 8) if ordered_pnls else 0.0
    profit_factor = _profit_factor(ordered_pnls)
    win_count = sum(1 for p in ordered_pnls if p > 0)
    loss_count = sum(1 for p in ordered_pnls if p < 0)
    flat_count = sum(1 for p in ordered_pnls if p == 0)
    max_dd = _max_drawdown_from_peak(ordered_pnls, starting_equity)
    top1_conc, top5_conc = _concentration_values(ordered_pnls)
    eligible_days = len(dates)

    # JSON-safe profit factor: inf (zero-loss) → None with explicit state field.
    pf_no_losses = profit_factor is not None and math.isinf(float(profit_factor))
    pf_serializable = None if pf_no_losses else profit_factor
    pf_state: str | None = (
        "no_losses" if pf_no_losses else
        ("no_trades" if profit_factor is None else None)
    )

    metrics: dict[str, Any] = {
        "net_realized_pnl": net_pnl,
        "profit_factor": pf_serializable,
        "profit_factor_state": pf_state,
        "win_count": win_count,
        "loss_count": loss_count,
        "flat_count": flat_count,
        "total_eligible_closes": len(ordered_pnls),
        "eligible_calendar_days": eligible_days,
        "max_drawdown_from_peak": max_dd,
        "top1_abs_concentration": top1_conc,
        "top5_abs_concentration": top5_conc,
    }

    funnel: dict[str, Any] = {
        "raw_event_count": source_raw_count,
        "eligible_outcome_count": len(cohort_outcomes),
        "excluded_outcome_count": source_raw_count - len(cohort_outcomes),
        "cohort_filtered_count": len(ordered_pnls),
        "pre_policy_excluded_count": len(target_pairs) - len(registered_pairs),
        "identity_mismatched_count": len(cohort.rows) - len(target_pairs),
        "identity_mismatch_reasons": dict(sorted(identity_mismatch_reasons.items())),
        "outcome_exclusion_reasons": dict(sorted(exclusion_reasons.items())),
        "outcome_semantics_version": OUTCOME_SEMANTICS_VERSION,
        "source_raw_event_count": cohort.source_raw_event_count,
        "loaded_raw_event_count": cohort.loaded_raw_event_count,
        "cohort_complete": True,
        "cohort_state": cohort.cohort_state,
        "ordered_source_digest": ordered_source_digest,
        "cohort_identity": {
            **cohort.identity,
            "runtime_session_id": runtime_session_id,
            "deploy_sha": identity["deploy_sha"],
            "behavior_fingerprint": identity["behavior_fingerprint"],
            "config_hash": identity["config_hash"],
            "policy_epoch_id": identity["policy_epoch_id"],
            "improvement_id": identity["improvement_id"],
            "cost_model_id": identity["cost_model_id"],
            "opened_after": registration_at,
        },
    }
    # Decision gates
    # ------------------------------------------------------------------
    def _gate(pass_: bool | None, value: Any, threshold: str) -> dict[str, Any]:
        return {"pass": pass_, "value": value, "threshold": threshold}

    gates: dict[str, Any] = {
        "has_eligible_outcomes": _gate(
            len(ordered_pnls) > 0, len(ordered_pnls), "> 0"
        ),
        "net_pnl_positive": _gate(
            net_pnl > 0 if ordered_pnls else False, net_pnl, "> 0"
        ),
        "profit_factor_above_1": _gate(
            bool(profit_factor is not None and float(profit_factor) > 1.0),
            pf_serializable,
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
            _gate(max_dd is not None and max_dd <= max_drawdown_cap, max_dd, f"<= {max_drawdown_cap}")
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
    artifact: dict[str, Any] = {
        "contract_version": QUALIFICATION_CONTRACT_VERSION,
        "verdict": verdict,
        "registration": registration,
        "registration_hash": registration_hash,
        "metrics": metrics,
        "gates": gates,
        "funnel": funnel,
        "outcome_semantics_version": OUTCOME_SEMANTICS_VERSION,
    }
    return _finalize_artifact(artifact)
