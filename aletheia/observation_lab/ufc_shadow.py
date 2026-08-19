"""UFC shadow-equivalence adoption (Issue #1072 Packet 1).

Registers the CURRENT active UFC generation in Observation Lab **without**
resetting anything: no reassignment, no sample copying, no repooled cutoff,
no new fixed-look, no ledger writes to ``learning_*``. The existing
Learning-side evaluator remains authoritative.

What this module does:
1. Discover the active UFC experiment via ``list_open_experiments("universe_family_calibration")``.
2. Rebuild cohorts and re-evaluate with the exact same pure functions
   Learning already uses (``build_experiment_cohorts`` + ``evaluate_cohort``).
3. Compare against the latest measurement verdict Learning already wrote.
4. Record the equivalence result to ``lab.db::ufc_shadow_equivalence``.
5. On mismatch: append-only record with ``matched=0`` and the specific
   mismatch field. Never mutates the Learning verdict; never grants any
   authority.

Owner review §1 guarded: current #447 window's identity is untouched. This
module has no write path into ``learning_*`` tables and no path to Risk,
Execution, credentials, or promotion.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("aletheia.observation_lab.ufc_shadow")

UFC_PATH_ID = "universe_family_calibration"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _resolve_policy_epoch(experiment_row: dict[str, Any]) -> str:
    """Best-effort policy epoch for one UFC experiment (v1 design G1 fix).

    ``LearningExperiment`` does NOT carry ``policy_epoch`` as a column;
    the epoch lives on ``LearningExposure.concurrent_policy_epoch`` and
    ``LearningContributionVerdict.policy_epoch``. Sourcing order:

    1. Latest measurement verdict's ``policy_epoch`` for this experiment
       (the ledger stamp for the cohort the evaluator actually used).
    2. ``aletheia.learning.experiments.current_policy_epoch(UFC_PATH_ID)``
       (the epoch the running Learning code would compute right now).
    3. Sentinel ``"unknown"`` — kept because the audit trail is more
       useful with a stable placeholder than an unhandled None.
    """
    from aletheia.learning.ledger import get_latest_measurement_verdict

    verdict = get_latest_measurement_verdict(str(experiment_row["experiment_id"]))
    if verdict and verdict.get("policy_epoch"):
        return str(verdict["policy_epoch"])
    try:
        from aletheia.learning.experiments import current_policy_epoch as _current_epoch

        computed = _current_epoch(UFC_PATH_ID)
        if computed:
            return str(computed)
    except Exception:  # pragma: no cover - config load fallback
        logger.debug("ufc_shadow: current_policy_epoch unavailable", exc_info=True)
    return "unknown"


def _observation_id(experiment_row: dict[str, Any]) -> str:
    """Stable Lab identity for one UFC generation.

    Per new #1072 Packet 1: ``ufc:<experiment_id>:<policy_epoch>:<fixed_look_generation>``.
    ``fixed_look_generation`` comes from the experiment row's
    ``stage_generation`` field; ``policy_epoch`` is resolved via
    ``_resolve_policy_epoch`` because it is not a column on
    ``LearningExperiment`` itself.
    """
    exp_id = str(experiment_row["experiment_id"])
    policy_epoch = _resolve_policy_epoch(experiment_row)
    fixed_look_gen = str(
        experiment_row.get("fixed_look_generation")
        or experiment_row.get("stage_generation")
        or 0
    )
    return f"ufc:{exp_id}:{policy_epoch}:{fixed_look_gen}"


def _cohort_key(experiment_row: dict[str, Any]) -> str:
    return (
        f"{experiment_row['hypothesis_id']}:{_resolve_policy_epoch(experiment_row)}:"
        f"{experiment_row.get('stage', '')}"
    )


def _extract_verdict_fields(verdict_row: dict[str, Any]) -> dict[str, Any]:
    """Pull the comparison-relevant fields from a stored verdict row."""
    return {
        "verdict": str(verdict_row.get("verdict") or ""),
        "treatment_n": int(verdict_row.get("unique_treatment_n") or 0),
        "control_n": int(verdict_row.get("unique_control_n") or 0),
    }


def _first_mismatch(
    stored: dict[str, Any], recomputed: dict[str, Any]
) -> str | None:
    """Return the first field name that differs, or None on full match."""
    for key in ("verdict", "treatment_n", "control_n"):
        if stored.get(key) != recomputed.get(key):
            return key
    return None


def run_ufc_shadow_equivalence_pass() -> dict[str, int]:
    """Recompute + compare + record one equivalence pass over active UFC generations.

    Returns a summary suitable for scheduler logs:
    ``{"checked": N, "matched": M, "mismatched": K, "skipped": S}``.

    Skipped rows are those with no stored measurement verdict yet (a freshly
    admitted trial that has not accumulated enough exposures). They are not
    mismatches — the equivalence gate simply has nothing to compare against.
    """
    from aletheia.learning.cohorts import build_experiment_cohorts
    from aletheia.learning.ledger import get_latest_measurement_verdict, list_open_experiments
    from aletheia.learning.verdicts import evaluate_cohort
    from aletheia.observation_lab.db import get_lab_db

    summary = {"checked": 0, "matched": 0, "mismatched": 0, "skipped": 0}
    open_experiments = list_open_experiments(path_id=UFC_PATH_ID)
    if not open_experiments:
        return summary

    for exp_row in open_experiments:
        exp_id = str(exp_row["experiment_id"])
        observation_id = _observation_id(exp_row)
        cohort_key = _cohort_key(exp_row)

        stored_verdict = get_latest_measurement_verdict(exp_id)
        if stored_verdict is None:
            summary["skipped"] += 1
            _record_equivalence(
                get_lab_db(),
                observation_id=observation_id,
                experiment_id=exp_id,
                cohort_key=cohort_key,
                matched=None,
                mismatch_field=None,
                learning_verdict=None,
                recomputed_verdict=None,
                learning_treatment_n=None,
                recomputed_treatment_n=None,
                learning_control_n=None,
                recomputed_control_n=None,
                notes="no_stored_verdict_yet",
            )
            continue

        try:
            cohorts = build_experiment_cohorts(exp_id)
        except Exception as exc:
            logger.warning(
                "ufc_shadow_equivalence: build_experiment_cohorts(%s) raised: %s",
                exp_id, exc,
            )
            summary["mismatched"] += 1
            _record_equivalence(
                get_lab_db(),
                observation_id=observation_id,
                experiment_id=exp_id,
                cohort_key=cohort_key,
                matched=False,
                mismatch_field="cohort_build_error",
                learning_verdict=str(stored_verdict.get("verdict") or ""),
                recomputed_verdict=None,
                learning_treatment_n=int(stored_verdict.get("unique_treatment_n") or 0),
                recomputed_treatment_n=None,
                learning_control_n=int(stored_verdict.get("unique_control_n") or 0),
                recomputed_control_n=None,
                notes=f"cohort_build_error: {exc!r}"[:400],
            )
            continue

        if not cohorts:
            summary["skipped"] += 1
            _record_equivalence(
                get_lab_db(),
                observation_id=observation_id,
                experiment_id=exp_id,
                cohort_key=cohort_key,
                matched=None,
                mismatch_field=None,
                learning_verdict=str(stored_verdict.get("verdict") or ""),
                recomputed_verdict=None,
                learning_treatment_n=int(stored_verdict.get("unique_treatment_n") or 0),
                recomputed_treatment_n=None,
                learning_control_n=int(stored_verdict.get("unique_control_n") or 0),
                recomputed_control_n=None,
                notes="no_cohorts_yet",
            )
            continue

        # A UFC experiment produces one primary cohort in shadow / paper stages;
        # take the first as canonical for equivalence. This mirrors how
        # contribution_runner iterates and stores the primary verdict.
        primary = cohorts[0]
        try:
            recomputed = evaluate_cohort(primary)
        except Exception as exc:
            logger.warning(
                "ufc_shadow_equivalence: evaluate_cohort(%s) raised: %s",
                exp_id, exc,
            )
            summary["mismatched"] += 1
            _record_equivalence(
                get_lab_db(),
                observation_id=observation_id,
                experiment_id=exp_id,
                cohort_key=cohort_key,
                matched=False,
                mismatch_field="evaluator_error",
                learning_verdict=str(stored_verdict.get("verdict") or ""),
                recomputed_verdict=None,
                learning_treatment_n=int(stored_verdict.get("unique_treatment_n") or 0),
                recomputed_treatment_n=None,
                learning_control_n=int(stored_verdict.get("unique_control_n") or 0),
                recomputed_control_n=None,
                notes=f"evaluator_error: {exc!r}"[:400],
            )
            continue

        stored_fields = _extract_verdict_fields(stored_verdict)
        recomputed_fields = {
            "verdict": recomputed.verdict,
            "treatment_n": recomputed.unique_treatment_n,
            "control_n": recomputed.unique_control_n,
        }
        mismatch = _first_mismatch(stored_fields, recomputed_fields)

        summary["checked"] += 1
        if mismatch is None:
            summary["matched"] += 1
        else:
            summary["mismatched"] += 1
            logger.warning(
                "ufc_shadow_equivalence: mismatch on %s field=%s "
                "learning=%r recomputed=%r",
                observation_id, mismatch,
                stored_fields.get(mismatch),
                recomputed_fields.get(mismatch),
            )
        _record_equivalence(
            get_lab_db(),
            observation_id=observation_id,
            experiment_id=exp_id,
            cohort_key=cohort_key,
            matched=(mismatch is None),
            mismatch_field=mismatch,
            learning_verdict=stored_fields["verdict"],
            recomputed_verdict=recomputed_fields["verdict"],
            learning_treatment_n=stored_fields["treatment_n"],
            recomputed_treatment_n=recomputed_fields["treatment_n"],
            learning_control_n=stored_fields["control_n"],
            recomputed_control_n=recomputed_fields["control_n"],
            notes=None,
        )
    return summary


def _record_equivalence(
    lab_db,
    *,
    observation_id: str,
    experiment_id: str,
    cohort_key: str,
    matched: bool | None,
    mismatch_field: str | None,
    learning_verdict: str | None,
    recomputed_verdict: str | None,
    learning_treatment_n: int | None,
    recomputed_treatment_n: int | None,
    learning_control_n: int | None,
    recomputed_control_n: int | None,
    notes: str | None,
) -> None:
    """Append one equivalence-pass row. ``matched=None`` denotes a skip."""
    # SQL CHECK constraint requires matched in (0, 1); map None → skip by
    # writing 0 and setting a distinct notes marker. This keeps the schema
    # strict while preserving the "not enough data yet" distinction.
    matched_flag = 1 if matched is True else 0
    if matched is None:
        if not notes:
            notes = "skipped"
    lab_db.execute(
        "INSERT INTO ufc_shadow_equivalence "
        "(id, observation_id, experiment_id, cohort_key, checked_at, matched, "
        "mismatch_field, learning_verdict, recomputed_verdict, "
        "learning_treatment_n, recomputed_treatment_n, "
        "learning_control_n, recomputed_control_n, notes) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            uuid.uuid4().hex,
            observation_id,
            experiment_id,
            cohort_key,
            _now(),
            matched_flag,
            mismatch_field,
            learning_verdict,
            recomputed_verdict,
            learning_treatment_n,
            recomputed_treatment_n,
            learning_control_n,
            recomputed_control_n,
            notes,
        ),
    )


def latest_equivalence_record(observation_id: str) -> dict | None:
    """Read-only: most recent equivalence pass for one Lab observation id."""
    from aletheia.observation_lab.db import get_lab_db

    row = get_lab_db().execute(
        "SELECT * FROM ufc_shadow_equivalence WHERE observation_id=? "
        "ORDER BY checked_at DESC LIMIT 1",
        (observation_id,),
    ).fetchone()
    return dict(row) if row else None
