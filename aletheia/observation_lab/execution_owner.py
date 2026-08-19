"""Observation cycle execution-ownership contract (Issue #1072 Worker split).

There is one canonical Observation cycle
(``filter_controller.run_controller_observe`` → ``run_lab_windows``).
Exactly ONE process owns and executes it at a time. This module is the
enforcement seam for that rule.

## Two owners, no ``both``

``config.observation.execution_owner`` accepts exactly two values:

- ``main`` (default) — the Main Runtime APScheduler owns
  ``filter_controller_observe``. Backward compatible with every deploy
  that predates this change.
- ``worker`` — a sibling ``aletheia-observer`` checkout owns the cycle
  via its own systemd timer. In this mode:
  1. Main Scheduler startup does NOT register ``filter_controller_observe``.
  2. Settings reconciliation removes any existing registration and refuses
     to re-add it, regardless of ``controller.shadow_enabled``.
  3. Any main-side attempt to execute the cycle (e.g. a stray manual
     ``_run_filter_controller_observe`` call) fails closed with a typed
     reason naming the Worker as owner.

## Why "no ``both``"

Two concurrent cycles against the same ``app.db`` + ``lab.db`` would
double-count first-exposure captures, race on checkpoint fingerprints,
and produce meaningless equivalence records. The contract is exclusive
because the underlying state is shared.

## Read-only

This module never writes config. Rollback = flip
``config.observation.execution_owner`` back to ``main`` and restart the
Gateway; the reconcile path re-adds the job at the next tick.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("aletheia.observation_lab.execution_owner")

# Sentinel used when config loading fails. ``main`` is the safe fallback —
# it preserves backward compatibility. A malformed config never silently
# hands ownership to a Worker that may not exist.
_DEFAULT_OWNER = "main"

_VALID_OWNERS = frozenset({"main", "worker"})


class ObservationOwnerError(RuntimeError):
    """Raised when an execution attempt violates the ownership contract."""


def get_execution_owner() -> str:
    """Return the configured owner, defaulting to ``main`` on any load error.

    Reads via ``aletheia.config.loader.load_config`` — same code path every
    other subsystem uses. Load failures are logged at DEBUG (not warning:
    this function is called from hot scheduler paths) and treated as
    ``main`` so a broken config never accidentally silences Main-side
    execution.
    """
    try:
        from aletheia.config.loader import load_config

        cfg = load_config()
    except Exception as exc:  # pragma: no cover - defensive config load
        logger.debug("execution_owner: load_config failed → default main: %s", exc)
        return _DEFAULT_OWNER
    observation = getattr(cfg, "observation", None)
    if observation is None:
        return _DEFAULT_OWNER
    owner = str(getattr(observation, "execution_owner", _DEFAULT_OWNER) or _DEFAULT_OWNER)
    return owner if owner in _VALID_OWNERS else _DEFAULT_OWNER


def is_main_owner() -> bool:
    """True iff Main Runtime owns the cycle. Backward-compatible default."""
    return get_execution_owner() == "main"


def is_worker_owner() -> bool:
    """True iff a sibling Worker owns the cycle."""
    return get_execution_owner() == "worker"


def require_worker_owner(*, action: str) -> None:
    """Fail closed unless Worker owns the cycle. Called by the Worker CLI.

    ``action`` is a short label (e.g. ``"observe run"``) used in the error
    message so a misconfigured Worker sees exactly what refused to run.
    """
    owner = get_execution_owner()
    if owner != "worker":
        raise ObservationOwnerError(
            f"{action}: refusing to run — observation.execution_owner={owner!r} "
            f"(worker CLI requires 'worker'). Set observation.execution_owner=worker "
            f"in the shared data-root config and retry."
        )


def require_main_or_absent(*, action: str) -> None:
    """Fail closed on the Main side when Worker owns the cycle.

    Used at every Main-side execution seam so a stray manual invocation
    can't produce a duplicate pass while the Worker also runs. The reason
    string names Worker as the current owner so operators fix config, not
    code.
    """
    owner = get_execution_owner()
    if owner == "worker":
        raise ObservationOwnerError(
            f"{action}: refusing to run — observation.execution_owner=worker. "
            f"The Observer Worker owns the cycle; run it via its systemd timer "
            f"or ``aletheia observe run`` from the observer checkout."
        )


__all__ = [
    "ObservationOwnerError",
    "get_execution_owner",
    "is_main_owner",
    "is_worker_owner",
    "require_worker_owner",
    "require_main_or_absent",
]
