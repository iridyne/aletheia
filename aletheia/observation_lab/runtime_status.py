"""Read-only runtime proof for Observation execution ownership.

Issue #1135 needs the updater to prove more than the configured owner: when a
Worker owns Observation, the running Main Gateway must not retain the
``filter_controller_observe`` APScheduler job.  This module exposes that narrow
in-process fact without adding another scheduler, management plane, or durable
state.
"""

from __future__ import annotations

from typing import Any

from aletheia.observation_lab.execution_owner import get_execution_owner

_OBSERVATION_CYCLE_JOB_ID = "filter_controller_observe"


def main_scheduler_ownership_snapshot() -> dict[str, Any]:
    """Return the configured owner and live Main scheduler registration state.

    This function is intended to run *inside the Gateway process*.  It reaches
    the existing in-memory APScheduler object read-only; it never starts,
    reconciles, pauses, removes, or adds jobs.

    ``exclusive`` is only false for the unsafe state that #1072/#1135 prohibit:
    Worker owns the canonical Observation cycle while Main still has the cycle
    job registered.  Main ownership with the job absent can be valid when the
    shadow controller is disabled, so absence is not treated as an error there.
    """

    owner = get_execution_owner()
    try:
        from aletheia.agent_runtime import scheduler as scheduler_mod

        scheduler = getattr(scheduler_mod, "_scheduler", None)
        if scheduler is None:
            return {
                "available": False,
                "unavailable_reason": "main_scheduler_not_started",
                "owner": owner,
                "job_id": _OBSERVATION_CYCLE_JOB_ID,
                "main_cycle_registered": None,
                "exclusive": None,
            }
        registered = scheduler.get_job(_OBSERVATION_CYCLE_JOB_ID) is not None
    except Exception as exc:  # status surfaces must remain observational
        return {
            "available": False,
            "unavailable_reason": "main_scheduler_probe_failed",
            "error": type(exc).__name__,
            "owner": owner,
            "job_id": _OBSERVATION_CYCLE_JOB_ID,
            "main_cycle_registered": None,
            "exclusive": None,
        }

    return {
        "available": True,
        "unavailable_reason": None,
        "owner": owner,
        "job_id": _OBSERVATION_CYCLE_JOB_ID,
        "main_cycle_registered": registered,
        "exclusive": not (owner == "worker" and registered),
    }


__all__ = ["main_scheduler_ownership_snapshot"]
