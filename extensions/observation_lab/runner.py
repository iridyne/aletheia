"""Bounded, per-window-isolated Observation Lab runner (Issue #1072 Cluster 2 / §5).

One process, one bounded pass, deterministic fair ordering, per-window
exception isolation. This is the canonical entry point that new #1072
§"Runner semantics" mandates: multi-window operation without a queue, DAG,
worker fleet or per-window daemon.

## Design rules

- **Register, don't invoke ad hoc.** All Lab work goes through a window
  descriptor so one pass can iterate legacy Lab jobs and every durable
  Observation Window without a second scheduler.
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
import json
from contextlib import contextmanager
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
       stop the observe-only pass.
    2. Saves the post-window fingerprint only when the pre-pass proof was
       valid (idempotent no-op when unchanged). An unresolved mismatch is
       never promoted to a new trusted baseline.

    Errors in the checkpoint step are contained the same way per-window
    errors are: logged, surfaced in the summary, execution continues.
    Cluster 5's contract requires the fingerprint chain to survive a
    misbehaving app.db read.
    """
    from extensions.observation_lab.worker_lock import worker_cycle_exclusion

    with worker_cycle_exclusion():
        return _run_lab_windows_locked(max_windows=max_windows)


def _run_lab_windows_locked(*, max_windows: int | None = None) -> dict[str, Any]:
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
    if checkpoint_summary.get("ok") is True:
        _save_checkpoint(checkpoint_summary)
    else:
        # A divergent checkpoint is an unresolved restore/continuity boundary.
        # Never promote that state to the new trusted baseline merely because
        # the observe-only window pass completed.
        checkpoint_summary["save_skipped"] = "unresolved_checkpoint_mismatch"
    return {
        "windows_run": len(ordered),
        "windows_errored": errored,
        "results": results,
        "joint_checkpoint": checkpoint_summary,
    }


def _pass_checkpoint() -> dict[str, Any]:
    """Verify the joint checkpoint at the start of one pass.

    Saving happens after windows run, so expected append-only Lab evidence
    produced by this pass is part of the checkpoint for the next pass rather
    than being reported as restore drift. Failure-isolated: the whole
    checkpoint step is one try/except so a
    broken ``aletheia_schema_migrations`` reader (or any other input to
    the fingerprint) never prevents the window pass from running.
    """
    try:
        from extensions.observation_lab.checkpoint import verify_joint_checkpoint

        verdict = verify_joint_checkpoint(allow_main_outbox_progress=True)
        if not verdict.ok:
            logger.warning(
                "observation_lab.runner: joint checkpoint mismatch: %s",
                verdict.reason,
            )
        return {
            "ok": bool(verdict.ok),
            "reason": verdict.reason,
            "mismatch_components": list(verdict.mismatch_components),
            "current_fingerprint": verdict.current_fingerprint,
            "last_fingerprint": (
                verdict.last.joint_fingerprint if verdict.last is not None else None
            ),
            "active_manifest_count": (
                len(verdict.last.active_manifest_hashes)
                if verdict.last is not None and verdict.ok else None
            ),
            "app_migration_count": (
                verdict.last.app_migration_count if verdict.last is not None and verdict.ok else None
            ),
        }
    except Exception as exc:
        logger.warning(
            "observation_lab.runner: joint checkpoint step errored: %s",
            exc, exc_info=True,
        )
        return {"ok": False, "reason": f"checkpoint_error:{exc!r}"[:400]}


def _save_checkpoint(summary: dict[str, Any]) -> None:
    """Persist the post-window Lab state without widening runner failures."""
    try:
        from extensions.observation_lab.checkpoint import (
            compute_joint_checkpoint,
            save_joint_checkpoint,
        )

        current = compute_joint_checkpoint()
        if (
            current.main_outbox_high_watermark < 0
            or current.main_outbox_pending_count < 0
            or current.main_outbox_fingerprint == "<unavailable>"
        ):
            summary.update(ok=False, reason="main_outbox_unavailable")
            return
        save_joint_checkpoint(current)
        summary.update(
            current_fingerprint=current.joint_fingerprint,
            active_manifest_count=current.active_manifest_count,
            app_migration_count=current.app_migration_count,
        )
    except Exception as exc:
        summary.update(ok=False, reason=f"checkpoint_save_error:{exc!r}"[:400])


def _settle_observation_continuity(
    *, advance_windows: bool = False, batch_size: int = 500, max_batches: int = 100
) -> dict[str, Any]:
    """Settle Main/Lab transport and persist only one strictly verified cut."""
    from aletheia.config.loader import get_data_dir

    if not (get_data_dir() / "observation-lab" / "lab.db").is_file():
        return {"ok": False, "reason": "lab_unavailable"}
    from extensions.observation_lab.canonical import (
        compact_finalized_generations,
        delivery_import_interruption_status,
        import_delivery_receipts,
        mirror_lab_audit_outbox,
        prune_observation_delivery_outbox,
    )
    from extensions.observation_lab.checkpoint import verify_joint_checkpoint
    from extensions.observation_lab.kernel import advance_windows as advance
    from extensions.observation_lab.worker_lock import worker_cycle_exclusion

    with worker_cycle_exclusion():
        checkpoint = _pass_checkpoint()
        if not checkpoint.get("ok"):
            interruption = delivery_import_interruption_status()
            return {
                "ok": False,
                "reason": checkpoint.get("reason"),
                "checkpoint": checkpoint,
                "delivery_import_interruption": interruption,
            }
        def failed(reason: str, **details: Any) -> dict[str, Any]:
            return {"ok": False, "reason": reason, "checkpoint": checkpoint, **details}
        imported_total = 0
        imported: dict[str, Any] = {}
        batch = max(1, min(int(batch_size), 1000))
        limit = max(1, int(max_batches))
        import_limit_reached = False
        for index in range(limit):
            imported = import_delivery_receipts(limit=batch)
            imported_total += int(imported.get("processed") or 0)
            if (
                imported.get("available") is not True
                or imported.get("failed_sequence") is not None
                or imported.get("retry_sequences")
            ):
                return failed(
                    imported.get("reason") or imported.get("failed_reason") or "delivery_import_failed",
                    delivery_import=imported,
                )
            if int(imported.get("processed") or 0) < batch:
                break
        else:
            import_limit_reached = True
        mirrored_total = 0
        mirrored: dict[str, Any] = {}
        mirror_limit_reached = False
        for index in range(limit):
            mirrored = mirror_lab_audit_outbox(limit=batch)
            mirrored_total += int(mirrored.get("processed") or 0)
            if mirrored.get("available") is not True or mirrored.get("failed"):
                return failed(mirrored.get("reason") or "audit_mirror_failed", audit_mirror=mirrored)
            if int(mirrored.get("processed") or 0) < batch:
                break
        else:
            mirror_limit_reached = True
        advanced = advance(limit=10) if advance_windows else None
        if advanced is not None and any(
            item.get("status") in {"error", "unavailable"}
            for item in advanced.get("results", {}).values()
        ):
            return failed("window_advance_failed", windows=advanced)
        pruned = prune_observation_delivery_outbox()
        compacted = compact_finalized_generations(limit=10)
        if pruned.get("available") is not True or compacted.get("skipped"):
            return failed(pruned.get("reason") or "compaction_failed")
        _save_checkpoint(checkpoint)
        if not checkpoint.get("ok"):
            return {"ok": False, "reason": checkpoint.get("reason"), "checkpoint": checkpoint}
        strict = verify_joint_checkpoint()
        if not strict.ok or strict.reason != "fingerprint_match":
            return {"ok": False, "reason": strict.reason, "checkpoint": checkpoint}
        if import_limit_reached or mirror_limit_reached:
            return {
                "ok": False,
                "reason": "delivery_import_batch_limit" if import_limit_reached else "audit_mirror_batch_limit",
                "checkpoint": checkpoint,
            }
        return {
            "ok": True,
            "reason": strict.reason,
            "delivery_import": {**imported, "processed": imported_total},
            "audit_mirror": {**mirrored, "processed": mirrored_total},
            "windows": advanced,
            "pruning": pruned,
            "compaction": compacted,
            "checkpoint": checkpoint,
        }


def settle_observation_continuity(**kwargs) -> dict[str, Any]:
    from extensions.observation_lab.worker_lock import WorkerCycleLockError

    try:
        return _settle_observation_continuity(**kwargs)
    except WorkerCycleLockError:
        return {"ok": False, "reason": "cycle_lock_contended"}


@contextmanager
def observation_settlement_barrier():
    """Keep the minimal update cut locked through backup and activation."""
    from extensions.observation_lab.update_settlement import (
        settle_update_continuity_locked,
    )
    from extensions.observation_lab.worker_lock import (
        WorkerCycleLockError,
        worker_cycle_exclusion,
    )

    try:
        with worker_cycle_exclusion():
            yield settle_update_continuity_locked()
    except WorkerCycleLockError:
        yield {"ok": False, "reason": "cycle_lock_contended"}


def register_builtin_handlers() -> None:
    """Register every built-in durable Observation Window handler."""
    from aletheia.universe.discovery_benchmark import register_observation_handler
    from datetime import datetime, timezone

    from extensions.observation_lab.kernel import (
        ObservationHandler,
        ObservationProgress,
        register_handler,
        registered_handlers,
    )
    from extensions.observation_lab.canonical import (
        register_treatment_adapter,
        registered_treatment_adapters,
    )
    from extensions.observation_lab.canonical import NumericTreatmentAdapter

    register_observation_handler()

    def _canonical_progress(window: dict[str, Any]) -> ObservationProgress:
        evaluation: dict[str, Any] | None = None
        try:
            from extensions.observation_lab.evaluator import evaluate_observation

            evaluation = evaluate_observation(
                str(window["observation_id"]),
                int(window["generation"]),
            )
        except Exception as exc:
            # Evaluation is a Lab evidence failure, not permission to invent a
            # healthy/empty measurement or to stop unrelated windows.
            evaluation = {
                "available": False,
                "reason": "evaluation_unavailable",
                "error": type(exc).__name__,
            }
        try:
            due = datetime.fromisoformat(
                str(window.get("finalize_not_before") or "").replace("Z", "+00:00")
            ).astimezone(timezone.utc)
            can_finalize = datetime.now(timezone.utc) >= due
        except (TypeError, ValueError, OverflowError):
            can_finalize = False
        return ObservationProgress(
            summary={
                "status": "collecting",
                "handler": window.get("kind"),
                "evaluation": evaluation,
            },
            can_finalize=can_finalize and evaluation is not None and evaluation.get("available") is True,
        )

    def _canonical_capture(window: dict[str, Any], event: dict[str, Any]) -> ObservationProgress:
        return ObservationProgress(
            summary={"status": "captured", "event_keys": sorted(event)},
        )

    def _canonical_finalize(window: dict[str, Any]) -> dict[str, Any]:
        from extensions.observation_lab.canonical import latest_observation_result

        result = latest_observation_result(
            str(window["observation_id"]), int(window["generation"]),
        )
        if result is not None:
            try:
                payload = json.loads(str(result.get("result_json") or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = {}
            if isinstance(payload, dict):
                return {
                    **payload,
                    "status": "finalized",
                    "handler": window.get("kind"),
                }
        raise RuntimeError("canonical_evaluation_result_unavailable")

    def _canonical_claims(window: dict[str, Any]) -> tuple[dict[str, Any], ...]:
        return ()

    for kind in (
        "filter_shadow",
        "universe_family_calibration",
        "momentum_entry_geometry",
        "mechanism_entry_learning",
        "ranker_case_score",
    ):
        key = (kind, "canonical-v1")
        if key not in registered_handlers():
            register_handler(ObservationHandler(
                kind=kind,
                version=key[1],
                normalize_spec=lambda spec: dict(spec),
                capture=_canonical_capture,
                advance=_canonical_progress,
                finalize=_canonical_finalize,
                retention_claims=_canonical_claims,
            ))

    for path_id in (
        "universe_family_calibration",
        "momentum_entry_geometry",
        "ranker_case_score",
    ):
        key = (f"learning.numeric:{path_id}", "v1")
        if key not in registered_treatment_adapters():
            register_treatment_adapter(NumericTreatmentAdapter(
                adapter_id=key[0],
                adapter_version=key[1],
                behavior_dimension=path_id,
            ))
    from extensions.learning.entry_context import EntryLearningTreatmentAdapter

    entry_learning_adapter = EntryLearningTreatmentAdapter()
    entry_learning_key = (
        entry_learning_adapter.adapter_id,
        entry_learning_adapter.adapter_version,
    )
    if entry_learning_key not in registered_treatment_adapters():
        register_treatment_adapter(entry_learning_adapter)
def _durable_observation_windows() -> dict[str, Any]:
    register_builtin_handlers()
    from extensions.observation_lab.kernel import advance_windows

    return advance_windows(limit=10)


def _register_default_windows() -> None:
    """Register the built-in Observation Lab passes.

    Deferred imports keep the runner primitive independent from domain
    subsystems until each pass executes.
    """
    def _filter_shadow_evaluate() -> dict[str, Any]:
        from extensions.observation_lab.canonical import get_authority_source

        if get_authority_source().source != "lab":
            raise RuntimeError("authority_source_unavailable")
        # Durable ObservationHandler windows own evaluation; this pass only projects results.
        from extensions.observation_lab.filter_shadow import (
            list_filter_shadow_observations,
        )

        roots = list_filter_shadow_observations(status="active", limit=1000)
        if roots is None:
            raise RuntimeError("observation_lab_unavailable")
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
    # Guard against double-registration on module reimport (test harnesses).
    existing = {w.name for w in _WINDOWS}
    if "filter_shadow_evaluate" not in existing:
        register_window(LabWindow(
            name="filter_shadow_evaluate",
            kind="filter_shadow",
            fn=_filter_shadow_evaluate,
        ))
    if "durable_observation_windows" not in existing:
        register_window(LabWindow(
            name="durable_observation_windows",
            kind="observation_window",
            fn=_durable_observation_windows,
        ))


_register_default_windows()