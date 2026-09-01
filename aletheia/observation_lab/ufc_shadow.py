"""UFC shadow-equivalence observation for Issue #1072.

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

The current UFC window's identity is untouched. This module has no write path
into ``learning_*`` tables and no path to Risk, Execution, credentials, or
promotion. Historical implementation sequencing is not part of this module's
runtime contract.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("aletheia.observation_lab.ufc_shadow")

UFC_PATH_ID = "universe_family_calibration"
_SKIPPED_EQUIVALENCE_NOTES = frozenset({
    "skipped",
    "no_stored_verdict_yet",
    "no_cohorts_yet",
    "current_policy_epoch_unavailable",
    "no_exact_current_cohort",
    "no_exact_current_verdict",
})


def decode_equivalence_result(row: dict[str, Any]) -> str:
    """Recover the three-state result from the backward-compatible row shape."""
    if row.get("notes") in _SKIPPED_EQUIVALENCE_NOTES:
        return "skipped"
    return "matched" if int(row.get("matched") or 0) == 1 else "mismatched"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _resolve_policy_epoch(experiment_row: dict[str, Any]) -> str:
    """Resolve the current UFC policy epoch without creating state.

    ``LearningExperiment`` does NOT carry ``policy_epoch`` as a column;
    derive it from the running policy definition. Historical verdicts are
    deliberately not a fallback: they describe a prior UFC generation.
    """
    try:
        from aletheia.learning.experiments import current_policy_epoch as _current_epoch

        computed = _current_epoch(UFC_PATH_ID, persist_config_migrations=False)
        if computed:
            return str(computed)
    except Exception:  # pragma: no cover - config load fallback
        logger.debug("ufc_shadow: current_policy_epoch unavailable", exc_info=True)
    return "unknown"


def _observation_id(
    experiment_row: dict[str, Any], *, policy_epoch: str | None = None
) -> str:
    """Stable Lab identity for one UFC generation.

    Current identity is
    ``ufc:<experiment_id>:<policy_epoch>:<fixed_look_generation>``.
    ``fixed_look_generation`` comes from the experiment row's
    ``stage_generation`` field; ``policy_epoch`` is resolved via
    ``_resolve_policy_epoch`` because it is not a column on
    ``LearningExperiment`` itself.
    """
    exp_id = str(experiment_row["experiment_id"])
    policy_epoch = policy_epoch or _resolve_policy_epoch(experiment_row)
    fixed_look_gen = str(
        experiment_row.get("fixed_look_generation")
        or experiment_row.get("stage_generation")
        or 0
    )
    return f"ufc:{exp_id}:{policy_epoch}:{fixed_look_gen}"


def _cohort_key(
    experiment_row: dict[str, Any], *, policy_epoch: str | None = None
) -> str:
    policy_epoch = policy_epoch or _resolve_policy_epoch(experiment_row)
    return (
        f"{experiment_row['hypothesis_id']}:{policy_epoch}:"
        f"{experiment_row.get('stage', '')}"
    )


def _extract_verdict_fields(verdict_row: dict[str, Any]) -> dict[str, Any]:
    """Pull the comparison-relevant fields from a stored verdict row.

    ``verdict`` is the load-bearing field — the actual causal conclusion
    (``positive`` / ``negative`` / ``insufficient_data`` / …). ``treatment_n``
    and ``control_n`` are recorded for **diagnostic audit only** and are NOT
    part of the equivalence signature: they legitimately drift between the
    stored verdict's snapshot time and Lab's fresh recompute because
    :func:`_market_day_coverage` in ``learning.verdicts`` re-qualifies the
    treatment set every pass, and newly-arrived exposures on market days
    with no matching control can collapse the qualified prefix. Both sides
    still say ``insufficient_data``; the counts just walk. If Lab actually
    disagrees on the **conclusion**, that's the signal we want to catch.
    """
    return {
        "verdict": str(verdict_row.get("verdict") or ""),
        "treatment_n": int(verdict_row.get("unique_treatment_n") or 0),
        "control_n": int(verdict_row.get("unique_control_n") or 0),
    }


def _first_mismatch(
    stored: dict[str, Any], recomputed: dict[str, Any]
) -> str | None:
    """Return the mismatch field name, or None when the causal conclusion matches.

    Only compares ``verdict`` — the sole field that expresses a causal
    conclusion. ``treatment_n`` / ``control_n`` differences do not
    invalidate the pass; the stored verdict and the fresh recompute are
    taken at different times against a coverage gate that legitimately
    shifts. The diff is still recorded in the equivalence row for audit
    (see :func:`_record_equivalence` caller sites).
    """
    if stored.get("verdict") != recomputed.get("verdict"):
        return "verdict"
    return None


def run_ufc_shadow_equivalence_pass() -> dict[str, int]:
    """Recompute + compare + record one equivalence pass over active UFC generations.

    Returns a summary suitable for scheduler logs:
    ``{"checked": N, "matched": M, "mismatched": K, "skipped": S}``.

    Skipped rows are those with no stored measurement verdict yet (a freshly
    admitted trial that has not accumulated enough exposures). They are not
    mismatches — the equivalence gate simply has nothing to compare against.
    """
    from aletheia.learning.authority import measurement_identity_reason
    from aletheia.learning.behavior_contracts import current_behavior_contract_hash
    from aletheia.learning.cohorts import build_experiment_cohorts
    from aletheia.learning.ledger import get_latest_measurement_verdict, list_open_experiments
    from aletheia.learning.registry import get_learning_path
    from aletheia.learning.verdicts import evaluate_cohort
    from aletheia.observation_lab.db import get_lab_db

    summary = {"checked": 0, "matched": 0, "mismatched": 0, "skipped": 0}
    open_experiments = list_open_experiments(path_id=UFC_PATH_ID)
    if not open_experiments:
        return summary
    path = get_learning_path(UFC_PATH_ID)

    for exp_row in open_experiments:
        exp_id = str(exp_row["experiment_id"])
        policy_epoch = _resolve_policy_epoch(exp_row)
        observation_id = _observation_id(exp_row, policy_epoch=policy_epoch)
        cohort_key = _cohort_key(exp_row, policy_epoch=policy_epoch)

        if policy_epoch == "unknown":
            summary["skipped"] += 1
            _record_unchecked(
                get_lab_db(),
                observation_id=observation_id,
                experiment_id=exp_id,
                cohort_key=cohort_key,
                matched=None,
                mismatch_field=None,
                notes="current_policy_epoch_unavailable",
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
            _record_unchecked(
                get_lab_db(),
                observation_id=observation_id,
                experiment_id=exp_id,
                cohort_key=cohort_key,
                matched=False,
                mismatch_field="cohort_build_error",
                notes=f"cohort_build_error: {exc!r}"[:400],
            )
            continue

        if not cohorts:
            summary["skipped"] += 1
            _record_unchecked(
                get_lab_db(),
                observation_id=observation_id,
                experiment_id=exp_id,
                cohort_key=cohort_key,
                matched=None,
                mismatch_field=None,
                notes="no_cohorts_yet",
            )
            continue

        target_horizon = str(cohorts[0].hypothesis.target_horizon or "")
        try:
            expected_behavior_contract_hash = current_behavior_contract_hash(
                path_id=UFC_PATH_ID,
                policy_epoch=policy_epoch,
                target_horizon=target_horizon,
                outcome_contract_version=path.outcome_contract_version,
                metric_version=path.metric_version,
            )
        except (KeyError, ValueError) as exc:
            summary["mismatched"] += 1
            _record_unchecked(
                get_lab_db(),
                observation_id=observation_id,
                experiment_id=exp_id,
                cohort_key=cohort_key,
                matched=False,
                mismatch_field="current_behavior_contract_unavailable",
                notes=f"current_behavior_contract_unavailable: {exc!r}"[:400],
            )
            continue

        primary = next((
            cohort
            for cohort in cohorts
            if (
                cohort.key.experiment_id == exp_id
                and cohort.key.hypothesis_id == str(exp_row["hypothesis_id"])
                and cohort.key.policy_epoch == policy_epoch
                and cohort.key.horizon == target_horizon
                and cohort.key.outcome_contract_version
                == path.outcome_contract_version
                and cohort.key.behavior_contract_hash
                == expected_behavior_contract_hash
                and str(cohort.experiment.stage)
                == str(exp_row.get("stage") or "")
            )
        ), None)
        if primary is None:
            summary["skipped"] += 1
            _record_unchecked(
                get_lab_db(),
                observation_id=observation_id,
                experiment_id=exp_id,
                cohort_key=cohort_key,
                matched=None,
                mismatch_field=None,
                notes="no_exact_current_cohort",
            )
            continue

        stored_verdict = get_latest_measurement_verdict(
            exp_id,
            policy_epoch=policy_epoch,
            stage=str(exp_row.get("stage") or ""),
            hypothesis_id=str(exp_row["hypothesis_id"]),
            target_horizon=target_horizon,
            outcome_contract_version=path.outcome_contract_version,
            metric_version=path.metric_version,
            behavior_contract_hash=expected_behavior_contract_hash,
            inference_role="monitoring",
        )
        if stored_verdict is None:
            summary["skipped"] += 1
            _record_unchecked(
                get_lab_db(),
                observation_id=observation_id,
                experiment_id=exp_id,
                cohort_key=cohort_key,
                matched=None,
                mismatch_field=None,
                notes="no_exact_current_verdict",
            )
            continue

        identity_mismatch = measurement_identity_reason(
            stored_verdict,
            str(exp_row.get("stage") or ""),
            policy_epoch,
            expected_horizon=target_horizon,
            expected_behavior_contract_hash=expected_behavior_contract_hash,
            expected_hypothesis_id=str(exp_row["hypothesis_id"]),
            expected_outcome_contract_version=path.outcome_contract_version,
            expected_metric_version=path.metric_version,
        )
        if identity_mismatch is not None:
            summary["mismatched"] += 1
            _record_unchecked(
                get_lab_db(),
                observation_id=observation_id,
                experiment_id=exp_id,
                cohort_key=cohort_key,
                matched=False,
                mismatch_field=identity_mismatch,
                notes="stored_verdict_identity_mismatch",
                stored_verdict=stored_verdict,
            )
            continue

        try:
            recomputed = evaluate_cohort(primary)
        except Exception as exc:
            logger.warning(
                "ufc_shadow_equivalence: evaluate_cohort(%s) raised: %s",
                exp_id, exc,
            )
            summary["mismatched"] += 1
            _record_unchecked(
                get_lab_db(),
                observation_id=observation_id,
                experiment_id=exp_id,
                cohort_key=cohort_key,
                matched=False,
                mismatch_field="evaluator_error",
                notes=f"evaluator_error: {exc!r}"[:400],
                stored_verdict=stored_verdict,
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


def _record_unchecked(
    lab_db,
    *,
    observation_id: str,
    experiment_id: str,
    cohort_key: str,
    matched: bool | None,
    mismatch_field: str | None,
    notes: str,
    stored_verdict: dict[str, Any] | None = None,
) -> None:
    stored = _extract_verdict_fields(stored_verdict) if stored_verdict else {}
    _record_equivalence(
        lab_db,
        observation_id=observation_id,
        experiment_id=experiment_id,
        cohort_key=cohort_key,
        matched=matched,
        mismatch_field=mismatch_field,
        learning_verdict=stored.get("verdict"),
        recomputed_verdict=None,
        learning_treatment_n=stored.get("treatment_n"),
        recomputed_treatment_n=None,
        learning_control_n=stored.get("control_n"),
        recomputed_control_n=None,
        notes=notes,
    )


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
