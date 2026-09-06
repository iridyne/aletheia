"""Single selector for Observation experiment lifecycle mutations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


class ExperimentMutationAuthorityError(RuntimeError):
    pass


@dataclass(frozen=True)
class SelectedExperimentMutationOwner:
    source: Literal["legacy", "lab"]
    epoch: str


def select_experiment_mutation_owner() -> SelectedExperimentMutationOwner:
    from aletheia.observation_lab.canonical import get_authority_source

    try:
        authority = get_authority_source()
    except Exception as exc:
        raise ExperimentMutationAuthorityError(
            "experiment_mutation_authority_unavailable"
        ) from exc
    if authority.source not in {"legacy", "lab"} or not authority.epoch:
        raise ExperimentMutationAuthorityError(
            "experiment_mutation_authority_invalid"
        )
    return SelectedExperimentMutationOwner(authority.source, authority.epoch)


def require_experiment_mutation_owner(
    source: Literal["legacy", "lab"],
    epoch: str,
    *,
    db=None,
) -> None:
    from aletheia.observation_lab.canonical import get_authority_source

    try:
        current = get_authority_source(db=db)
    except Exception as exc:
        raise ExperimentMutationAuthorityError(
            "experiment_mutation_authority_unavailable"
        ) from exc
    if current.source != source or current.epoch != epoch:
        raise ExperimentMutationAuthorityError(
            f"experiment_mutation_authority_changed:{source}:{epoch}"
        )


def run_selected_lifecycle_cycle() -> dict[str, int | str]:
    owner = select_experiment_mutation_owner()
    if owner.source == "lab":
        return {
            "status": "canonical_lab_authority",
            "experiments": 0,
            "promoted": 0,
            "refrozen": 0,
            "admitted": 0,
            "retired_idle": 0,
            "failed_experiments": 0,
        }
    from aletheia.learning.lifecycle import run_lifecycle_controller

    return run_lifecycle_controller(authority_epoch=owner.epoch)


def run_selected_filter_experiment_cycle() -> dict:
    owner = select_experiment_mutation_owner()
    if owner.source == "lab":
        from aletheia.observation_lab.filter_shadow import (
            list_filter_shadow_observations,
        )

        roots = list_filter_shadow_observations(status="active", limit=1000) or []
        evaluation = {
            "evaluated": len(roots),
            "ready_for_review": 0,
            "completed_no_support": 0,
            "invalidated": 0,
            "canonical_handler": True,
        }
        for root in roots:
            disposition = str(
                ((root.get("latest_evaluation") or {}).get("disposition") or "")
            )
            if disposition in {
                "ready_for_review", "completed_no_support", "invalidated",
            }:
                evaluation[disposition] += 1
        return {
            "status": "canonical_lab_authority",
            "frozen_units_captured": 0,
            "evaluation": evaluation,
        }

    from aletheia.counterfactual.filter_experiments import (
        capture_frozen_units_for,
        evaluate_active_shadow_experiments,
    )
    from aletheia.storage import repositories as repo

    captured = sum(
        capture_frozen_units_for(experiment)
        for experiment in repo.list_filter_shadow_experiments(status="collecting")
    )
    return {
        "frozen_units_captured": captured,
        "evaluation": evaluate_active_shadow_experiments(),
    }


def run_selected_observation_cycle(
    *, min_samples: int, legacy_controller_enabled: bool
) -> dict:
    owner = select_experiment_mutation_owner()
    if owner.source == "legacy" and legacy_controller_enabled:
        from aletheia.analytics.filter_controller import run_controller_observe

        return run_controller_observe(min_samples=min_samples)
    from aletheia.observation_lab.runner import run_lab_windows

    return {
        "pass_status": "success",
        "recommendations_written": 0,
        "lab_windows": run_lab_windows(),
    }


def admit_selected_legacy_trial(
    experiment_id: str,
    *,
    verdict_id: str,
    expected_policy_epoch: str,
    reason: str,
):
    owner = select_experiment_mutation_owner()
    if owner.source != "legacy":
        raise ExperimentMutationAuthorityError("legacy_experiment_owner_not_selected")
    from aletheia.learning.lifecycle import _execute_trial_admission

    return _execute_trial_admission(
        experiment_id,
        verdict_id=verdict_id,
        expected_policy_epoch=expected_policy_epoch,
        reason=reason,
        authority_epoch=owner.epoch,
    )


def record_selected_lab_owner_action(**fields) -> str:
    owner = select_experiment_mutation_owner()
    if owner.source != "lab":
        raise ExperimentMutationAuthorityError("lab_experiment_owner_not_selected")
    from aletheia.observation_lab.canonical import _record_owner_action

    return _record_owner_action(expected_authority_epoch=owner.epoch, **fields)


def insert_selected_legacy_filter_experiment(experiment: dict) -> bool:
    owner = select_experiment_mutation_owner()
    if owner.source != "legacy":
        raise ExperimentMutationAuthorityError("legacy_experiment_owner_not_selected")
    from aletheia.storage import repositories as repo
    from aletheia.storage.sqlite import get_db

    with repo.transaction(immediate=True):
        require_experiment_mutation_owner("legacy", owner.epoch, db=get_db())
        return repo._insert_filter_shadow_experiment(experiment)


def update_selected_legacy_filter_experiment(
    experiment_id: str, **fields
) -> None:
    owner = select_experiment_mutation_owner()
    if owner.source != "legacy":
        raise ExperimentMutationAuthorityError("legacy_experiment_owner_not_selected")
    from aletheia.storage import repositories as repo
    from aletheia.storage.sqlite import get_db

    with repo.transaction(immediate=True):
        require_experiment_mutation_owner("legacy", owner.epoch, db=get_db())
        repo._update_filter_shadow_experiment(experiment_id, **fields)


__all__ = [
    "ExperimentMutationAuthorityError",
    "SelectedExperimentMutationOwner",
    "admit_selected_legacy_trial",
    "insert_selected_legacy_filter_experiment",
    "record_selected_lab_owner_action",
    "require_experiment_mutation_owner",
    "run_selected_lifecycle_cycle",
    "run_selected_filter_experiment_cycle",
    "run_selected_observation_cycle",
    "select_experiment_mutation_owner",
    "update_selected_legacy_filter_experiment",
]
