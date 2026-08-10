"""Read-only diagnosis for System Operator candidate-skip liveness.

This module deliberately has no dependency on PI prompts, configuration writers,
Proposal creation, Risk, or execution.  It turns already-durable candidate facts
and fixed-horizon outcomes into a bounded diagnosis report; it never treats a
positive missed counterfactual as authority to trade.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from aletheia.storage.sqlite import get_db


Finding = Literal[
    "not_applicable",
    "awaiting_outcomes",
    "recording_incident",
    "protective_skip_observed",
    "overrestriction_suspect",
    "operator_liveness_incident",
    "diagnosis_unavailable",
]

_THRESHOLD_VERSION = "skip-halt-v2"
_WINDOW_HOURS = 24
_OUTCOME_HORIZONS = frozenset({"15m", "1h", "4h", "24h"})
_PRIMARY_HORIZON = "24h"
_PRIMARY_HORIZON_HOURS = 24
_MIN_SETTLED_PER_COHORT = 5


@dataclass(frozen=True)
class SkipDiagnosisReport:
    """Serializable, non-authoritative diagnosis output."""

    finding: Finding
    recommended_action: str
    threshold_version: str
    policy_epoch_id: str | None
    window_start: str
    window_end: str
    counts: dict[str, int]
    metrics: dict[str, float | None]
    reason_codes: dict[str, int]
    side_effects: tuple[()] = ()
    error_code: str | None = None

    def as_dict(self) -> dict:
        return {
            "finding": self.finding,
            "recommended_action": self.recommended_action,
            "threshold_version": self.threshold_version,
            "policy_epoch_id": self.policy_epoch_id,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "counts": dict(self.counts),
            "metrics": dict(self.metrics),
            "reason_codes": dict(self.reason_codes),
            "side_effects": list(self.side_effects),
            "error_code": self.error_code,
        }


def diagnose_skip_halt(
    *,
    now: datetime | None = None,
    window_hours: int = _WINDOW_HOURS,
    policy_epoch_id: str | None = None,
) -> SkipDiagnosisReport:
    """Return the current candidate-skip state without writing any state.

    Results are intentionally conservative.  A full negative candidate skip
    cohort can be reported as protective, but a positive cohort becomes only an
    experiment/replay draft recommendation after a comparable pass cohort exists.
    """
    observed_end = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    window_delta = timedelta(hours=max(1, int(window_hours)))
    recent_start = observed_end - window_delta
    cohort_end = observed_end - timedelta(hours=_PRIMARY_HORIZON_HOURS)
    cohort_start = cohort_end - window_delta
    try:
        return _diagnose(
            start=cohort_start,
            end=cohort_end,
            recent_start=recent_start,
            observed_end=observed_end,
            policy_epoch_id=policy_epoch_id,
        )
    except Exception:
        return _report(
            "diagnosis_unavailable",
            "repair_diagnosis_read_path",
            cohort_start,
            cohort_end,
            policy_epoch_id=policy_epoch_id,
            counts={},
            metrics={},
            reason_codes={},
            error_code="skip_diagnosis_query_failed",
        )


def _diagnose(
    *,
    start: datetime,
    end: datetime,
    recent_start: datetime,
    observed_end: datetime,
    policy_epoch_id: str | None,
) -> SkipDiagnosisReport:
    db = get_db()
    if policy_epoch_id:
        epoch = db.execute(
            "SELECT started_at FROM learning_policy_epochs WHERE epoch_id=?",
            (policy_epoch_id,),
        ).fetchone()
        if epoch is None:
            raise ValueError("skip_diagnosis_policy_epoch_unknown")
        epoch_start = datetime.fromisoformat(str(epoch["started_at"]).replace("Z", "+00:00"))
        if epoch_start.tzinfo is None:
            epoch_start = epoch_start.replace(tzinfo=timezone.utc)
        epoch_start = epoch_start.astimezone(timezone.utc)
        start = max(start, epoch_start)
        recent_start = max(recent_start, epoch_start)
    start_iso, end_iso = start.isoformat(), end.isoformat()
    recent_start_iso, observed_end_iso = recent_start.isoformat(), observed_end.isoformat()
    mature_candidates = db.execute(
        """SELECT id, created_at FROM pi_decisions
           WHERE action='skip' AND opportunity_id IS NOT NULL
             AND COALESCE(signal_source, 'market_observer') != 'system_cascade'
             AND julianday(created_at) >= julianday(?)
             AND julianday(created_at) < julianday(?)
           ORDER BY created_at ASC, rowid ASC""",
        (start_iso, end_iso),
    ).fetchall()
    recent_candidates = db.execute(
        """SELECT id, created_at FROM pi_decisions
           WHERE action='skip' AND opportunity_id IS NOT NULL
             AND COALESCE(signal_source, 'market_observer') != 'system_cascade'
             AND julianday(created_at) >= julianday(?)
             AND julianday(created_at) <= julianday(?)
           ORDER BY created_at ASC, rowid ASC""",
        (recent_start_iso, observed_end_iso),
    ).fetchall()
    mature_candidate_ids = [str(row["id"]) for row in mature_candidates]
    recent_candidate_ids = [str(row["id"]) for row in recent_candidates]
    administrative_skips_excluded = int(db.execute(
        """SELECT COUNT(*) FROM pi_decisions
           WHERE action='skip' AND opportunity_id IS NOT NULL
             AND signal_source='system_cascade'
             AND julianday(created_at) >= julianday(?)
             AND julianday(created_at) <= julianday(?)""",
        (recent_start_iso, observed_end_iso),
    ).fetchone()[0] or 0)
    pending_count = int(db.execute(
        """SELECT COUNT(*) FROM pi_decisions pending
           JOIN opportunities source ON source.id=pending.opportunity_id
           WHERE pending.action='signal_pending'
             AND julianday(pending.created_at) >= julianday(?)
             AND julianday(pending.created_at) <= julianday(?)
             AND source.status='queued'
             AND pending.rowid=(
                 SELECT newer.rowid FROM pi_decisions newer
                 WHERE newer.opportunity_id=pending.opportunity_id
                   AND COALESCE(newer.signal_source, 'market_observer')=
                       COALESCE(pending.signal_source, 'market_observer')
                   AND newer.action='signal_pending'
                 ORDER BY julianday(newer.created_at) DESC, newer.rowid DESC
                 LIMIT 1
             )
             AND NOT EXISTS (
                 SELECT 1 FROM pi_decisions final
                 WHERE final.opportunity_id=pending.opportunity_id
                   AND final.action IN ('skip', 'watch', 'trade')
                   AND COALESCE(final.signal_source, 'market_observer')=
                       COALESCE(pending.signal_source, 'market_observer')
                   AND (
                       julianday(final.created_at)>julianday(pending.created_at)
                       OR (
                           julianday(final.created_at)=julianday(pending.created_at)
                           AND final.rowid>pending.rowid
                       )
                   )
             )""",
        (recent_start_iso, observed_end_iso),
    ).fetchone()[0] or 0)
    candidate_ids = list(dict.fromkeys([*mature_candidate_ids, *recent_candidate_ids]))
    keys = [f"pi_decision:{item}" for item in candidate_ids]
    mature_keys = [f"pi_decision:{item}" for item in mature_candidate_ids]
    counts = {
        "candidate_skips": len(recent_candidate_ids),
        "mature_candidate_skips": len(mature_candidate_ids),
        "immature_candidate_skips": len(recent_candidate_ids),
        "administrative_skips_excluded": administrative_skips_excluded,
        "pending_signals": pending_count,
    }
    if not candidate_ids:
        finding = "operator_liveness_incident" if pending_count >= _MIN_SETTLED_PER_COHORT else "not_applicable"
        action = "create_non_executable_replay_draft" if finding == "operator_liveness_incident" else "record_supply_state"
        return _report(
            finding, action, start, end,
            counts=counts, metrics={}, reason_codes={}, policy_epoch_id=policy_epoch_id,
        )

    placeholders = ",".join("?" for _ in candidate_ids)
    unreconstructable_adverse_evidence = int(db.execute(
        f"""SELECT COUNT(*) FROM pi_decisions decision
            WHERE decision.id IN ({placeholders})
              AND EXISTS (
                  SELECT 1
                  FROM json_each(
                      decision.evidence_manifest_json,
                      '$.operator_rationale.validated_adverse_claims'
                  ) claim,
                  json_each(claim.value, '$.fact_refs') ref,
                  json_each(
                      decision.evidence_manifest_json,
                      '$.operator_rationale.adverse_evidence'
                  ) evidence
                  WHERE ref.value LIKE 'decision.%'
                    AND instr(evidence.value, ref.value || '=null') > 0
              )""",
        tuple(candidate_ids),
    ).fetchone()[0] or 0)
    trace_rows = db.execute(
        f"""SELECT outcome_candidate_key, reason_code
            FROM filter_decisions
            WHERE candidate_key IN ({placeholders})
              AND filter_name='operator_action_assessment'""",
        tuple(keys),
    ).fetchall()
    traced_keys = {str(row["outcome_candidate_key"] or "") for row in trace_rows}
    reason_codes = Counter(str(row["reason_code"] or "unknown") for row in trace_rows)
    outcome_rows = db.execute(
        f"""SELECT outcome_candidate_key, horizon, status, net_return_pct
            FROM filter_candidate_outcomes
            WHERE outcome_candidate_key IN ({placeholders})""",
        tuple(keys),
    ).fetchall()
    outcomes: dict[str, list] = defaultdict(list)
    for row in outcome_rows:
        outcomes[str(row["outcome_candidate_key"])].append(row)

    missing_trace = sum(1 for key in keys if key not in traced_keys)
    missing_seed = sum(
        1 for key in keys
        if {str(row["horizon"]) for row in outcomes[key]} != _OUTCOME_HORIZONS
    )
    counts.update({
        "missing_trace": missing_trace,
        "missing_outcome_seed": missing_seed,
        "unreconstructable_adverse_evidence": unreconstructable_adverse_evidence,
    })
    if missing_trace or missing_seed or unreconstructable_adverse_evidence:
        return _report(
            "recording_incident", "create_recording_repair_task", start, end,
            counts=counts, metrics={}, reason_codes=dict(reason_codes), policy_epoch_id=policy_epoch_id,
        )

    if not mature_keys:
        counts["settled_candidate_skips"] = 0
        counts["unsettled_candidate_skips"] = len(recent_candidate_ids)
        return _report(
            "awaiting_outcomes", "await_fixed_horizon_settlement", start, end,
            counts=counts, metrics={}, reason_codes=dict(reason_codes), policy_epoch_id=policy_epoch_id,
        )

    settled_skip_nets: list[float] = []
    unsettled = 0
    for key in mature_keys:
        primary = next((row for row in outcomes[key] if row["horizon"] == _PRIMARY_HORIZON), None)
        if primary is None or primary["status"] != "settled" or primary["net_return_pct"] is None:
            unsettled += 1
        else:
            settled_skip_nets.append(float(primary["net_return_pct"]))
    counts["settled_candidate_skips"] = len(settled_skip_nets)
    counts["unsettled_candidate_skips"] = unsettled
    if unsettled:
        return _report(
            "awaiting_outcomes", "await_fixed_horizon_settlement", start, end,
            counts=counts, metrics={}, reason_codes=dict(reason_codes), policy_epoch_id=policy_epoch_id,
        )

    pass_rows = db.execute(
        """SELECT DISTINCT fo.outcome_candidate_key, fo.net_return_pct
           FROM filter_decisions fd
           JOIN filter_candidate_outcomes fo
             ON fo.outcome_candidate_key=fd.outcome_candidate_key
           WHERE fd.filter_name='operator_action_assessment'
             AND fd.decision='passed'
             AND julianday(fd.evaluated_at) >= julianday(?)
             AND julianday(fd.evaluated_at) < julianday(?)
             AND fo.horizon=? AND fo.status='settled' AND fo.net_return_pct IS NOT NULL""",
        (start_iso, end_iso, _PRIMARY_HORIZON),
    ).fetchall()
    pass_nets = [float(row["net_return_pct"]) for row in pass_rows]
    counts["comparable_passes"] = len(pass_nets)
    skip_avg = _average(settled_skip_nets)
    pass_avg = _average(pass_nets)
    metrics = {"skip_avg_net_24h": skip_avg, "pass_avg_net_24h": pass_avg}
    if len(settled_skip_nets) < _MIN_SETTLED_PER_COHORT:
        return _report(
            "awaiting_outcomes", "continue_observation_until_minimum_cohort", start, end,
            counts=counts, metrics=metrics, reason_codes=dict(reason_codes), policy_epoch_id=policy_epoch_id,
        )
    if skip_avg is not None and skip_avg <= 0:
        return _report(
            "protective_skip_observed", "retain_current_policy", start, end,
            counts=counts, metrics=metrics, reason_codes=dict(reason_codes), policy_epoch_id=policy_epoch_id,
        )
    if len(pass_nets) >= _MIN_SETTLED_PER_COHORT and skip_avg is not None and pass_avg is not None and skip_avg > 0 and skip_avg > pass_avg:
        return _report(
            "overrestriction_suspect", "create_non_executable_replay_draft", start, end,
            counts=counts, metrics=metrics, reason_codes=dict(reason_codes), policy_epoch_id=policy_epoch_id,
        )
    return _report(
        "operator_liveness_incident", "create_non_executable_replay_draft", start, end,
        counts=counts, metrics=metrics, reason_codes=dict(reason_codes), policy_epoch_id=policy_epoch_id,
    )


def _report(
    finding: Finding,
    recommended_action: str,
    start: datetime,
    end: datetime,
    *,
    counts: dict[str, int],
    metrics: dict[str, float | None],
    reason_codes: dict[str, int],
    policy_epoch_id: str | None = None,
    error_code: str | None = None,
) -> SkipDiagnosisReport:
    return SkipDiagnosisReport(
        finding=finding,
        recommended_action=recommended_action,
        threshold_version=_THRESHOLD_VERSION,
        policy_epoch_id=policy_epoch_id,
        window_start=start.isoformat(),
        window_end=end.isoformat(),
        counts=counts,
        metrics=metrics,
        reason_codes=reason_codes,
        error_code=error_code,
    )


def _average(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 8) if values else None
