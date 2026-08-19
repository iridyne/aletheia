"""Bounded, per-window-isolated Observation Lab runner (Issue #1072 Cluster 2 / §5).

One process, one bounded pass, deterministic fair ordering, per-window
exception isolation. This is the canonical entry point that new #1072
§"Runner semantics" mandates: multi-window operation without a queue, DAG,
worker fleet or per-window daemon.

## Design rules

- **Register, don't invoke ad hoc.** All Lab work goes through a window
  descriptor so a single runner pass can iterate everything. Filter-shadow
  and UFC shadow-equivalence are the two windows shipped in Cluster 2;
  future clusters plug in without touching the runner itself.
- **Idempotent windows do not need persistent cursors.** Every callable
  registered here must be safe to re-invoke on restart with no state loss.
  Frozen-unit capture, UFC shadow-equivalence, and filter-shadow evaluation
  all satisfy this today via ``INSERT OR IGNORE`` / append-only writes.
  When a future window is NOT idempotent, it will bring its own watermark
  and the runner protocol expands then — not now (ponytail: build the
  table when a real caller needs it).
- **Deterministic order.** Fair ordering is alphabetical by window name.
  This trivially prevents starvation because the order is the same every
  pass; a busy window cannot repeatedly cut in front of a quiet one.
- **Failure isolation.** Every window runs under its own try/except. A
  raise short-circuits that window's result to ``status="error"`` and the
  runner continues with the next. No exception propagates out of
  ``run_lab_windows``.
- **Bounded.** ``max_windows`` caps how many windows run per pass (default
  None = all). Callers doing tight-cycle probing can pass a small bound;
  the scheduled cycle passes the default.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Iterable

logger = logging.getLogger("aletheia.observation_lab.runner")

# One typed function signature keeps callers honest — a window MUST return a
# summary dict the runner can aggregate/log. Raising is allowed; the runner
# catches and converts to an ``error`` outcome.
WindowFn = Callable[[], dict[str, Any]]


@dataclass(frozen=True)
class LabWindow:
    """Descriptor for one Observation Window the Lab runner will advance.

    ``name`` is the deterministic sort key and the log/audit identifier —
    keep it stable. ``kind`` classifies the window for observability (e.g.
    ``filter_shadow`` / ``ufc_shadow_equivalence``); Cluster 3+ will add
    ``treatment_bearing``. ``fn`` is the bounded pass — it MUST be idempotent
    and MUST NOT raise past its own boundary (the runner catches, but a
    window that leaks state on error violates isolation regardless).
    """
    name: str
    kind: str
    fn: WindowFn


_WINDOWS: list[LabWindow] = []


def register_window(window: LabWindow) -> None:
    """Add a window. Duplicate names are rejected to keep ordering stable."""
    for existing in _WINDOWS:
        if existing.name == window.name:
            raise ValueError(f"lab window already registered: {window.name}")
    _WINDOWS.append(window)


def registered_windows() -> tuple[LabWindow, ...]:
    """Snapshot of registered windows in deterministic order — read-only."""
    return tuple(sorted(_WINDOWS, key=lambda w: w.name))


def clear_windows() -> None:
    """Test hook. Never call from production code paths."""
    _WINDOWS.clear()


def run_lab_windows(*, max_windows: int | None = None) -> dict[str, Any]:
    """Advance every registered window under one bounded, isolated pass.

    Returns a summary dict:

    ``{"windows_run": N, "windows_errored": K,
       "results": {name: <summary>}, "joint_checkpoint": {...}}``

    Every window either produced a summary (``results[name]`` = that dict)
    or errored (``results[name]`` = ``{"status": "error", "error": <repr>}``).
    Exceptions never propagate out; the runner is the top-level exception
    boundary for one pass.

    ## Checkpoint step (Cluster 5 / G3)

    Before any window runs, the runner:

    1. Verifies the joint app.db + lab.db + manifest fingerprint against
       the last stored checkpoint. Result surfaces as ``joint_checkpoint``
       in the return dict. A mismatch is logged as a warning but does NOT
       stop the pass — v1 records the drift; Packet 2+ can escalate to
       per-window invalidation when it has treatment-bearing windows to
       protect.
    2. Saves the current fingerprint (idempotent no-op when unchanged
       from the last stored row).

    Errors in the checkpoint step are contained the same way per-window
    errors are: logged, surfaced in the summary, execution continues.
    Cluster 5's contract requires the fingerprint chain to survive a
    misbehaving app.db read.
    """
    ordered = registered_windows()
    if max_windows is not None:
        ordered = ordered[: max(0, int(max_windows))]

    checkpoint_summary: dict[str, Any] = _pass_checkpoint()

    results: dict[str, Any] = {}
    errored = 0
    for window in ordered:
        try:
            summary = window.fn()
            if not isinstance(summary, dict):
                summary = {"status": "ok", "value": summary}
            results[window.name] = summary
        except Exception as exc:
            errored += 1
            logger.warning(
                "observation_lab.runner: window=%s kind=%s errored: %s",
                window.name, window.kind, exc, exc_info=True,
            )
            results[window.name] = {"status": "error", "error": repr(exc)[:400]}
    return {
        "windows_run": len(ordered),
        "windows_errored": errored,
        "results": results,
        "joint_checkpoint": checkpoint_summary,
    }


def _pass_checkpoint() -> dict[str, Any]:
    """Verify + save the joint checkpoint at the start of one pass.

    Failure-isolated: the whole checkpoint step is one try/except so a
    broken ``aletheia_schema_migrations`` reader (or any other input to
    the fingerprint) never prevents the window pass from running.
    """
    try:
        from aletheia.observation_lab.checkpoint import (
            compute_joint_checkpoint,
            save_joint_checkpoint,
            verify_joint_checkpoint,
        )

        verdict = verify_joint_checkpoint()
        if not verdict.ok:
            logger.warning(
                "observation_lab.runner: joint checkpoint mismatch: %s",
                verdict.reason,
            )
        current = compute_joint_checkpoint()
        save_joint_checkpoint(current)
        return {
            "ok": bool(verdict.ok),
            "reason": verdict.reason,
            "current_fingerprint": current.joint_fingerprint,
            "last_fingerprint": (
                verdict.last.joint_fingerprint if verdict.last is not None else None
            ),
            "active_manifest_count": current.active_manifest_count,
            "app_migration_count": current.app_migration_count,
        }
    except Exception as exc:
        logger.warning(
            "observation_lab.runner: joint checkpoint step errored: %s",
            exc, exc_info=True,
        )
        return {"ok": False, "reason": f"checkpoint_error:{exc!r}"[:400]}


def _register_default_windows() -> None:
    """Register the two windows shipped in Cluster 2.

    Deferred imports so a caller that only wants the runner primitive (e.g.
    a plugin test) can import this module without pulling the counterfactual
    subsystem.
    """
    def _filter_shadow_evaluate() -> dict[str, Any]:
        from aletheia.counterfactual.filter_experiments import (
            capture_frozen_units_for,
            evaluate_active_shadow_experiments,
        )
        from aletheia.storage import repositories as _repo

        captured = 0
        for experiment in _repo.list_filter_shadow_experiments(status="collecting"):
            captured += capture_frozen_units_for(experiment)
        evaluation = evaluate_active_shadow_experiments()
        return {"frozen_units_captured": captured, "evaluation": evaluation}

    def _ufc_shadow_equivalence() -> dict[str, Any]:
        from aletheia.observation_lab.ufc_shadow import run_ufc_shadow_equivalence_pass
        return run_ufc_shadow_equivalence_pass()

    # Guard against double-registration on module reimport (test harnesses).
    existing = {w.name for w in _WINDOWS}
    if "filter_shadow_evaluate" not in existing:
        register_window(LabWindow(
            name="filter_shadow_evaluate",
            kind="filter_shadow",
            fn=_filter_shadow_evaluate,
        ))
    if "ufc_shadow_equivalence" not in existing:
        register_window(LabWindow(
            name="ufc_shadow_equivalence",
            kind="ufc_shadow_equivalence",
            fn=_ufc_shadow_equivalence,
        ))


_register_default_windows()
