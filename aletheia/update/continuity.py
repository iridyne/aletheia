"""Observer-aware continuity verification for source updates (Issue #1135).

The continuity layer is observational except for audit events. It reuses the
existing control/Observation projections, distinguishes structural failure from
missing proof, and never turns normal Observation business progress into a
deployment gate.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any
import urllib.request

from aletheia.files.jsonl import write_audit

_SCHEMA_VERSION = "update_observer_continuity/v1"
_VALID_OWNERS = {"main", "worker"}
_FRESH_CAPTURE_PREFIX = "ALETHEIA_CONTINUITY_SNAPSHOT="


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _gateway_status() -> dict[str, Any]:
    """Read the running Gateway status contract without invoking another CLI."""
    try:
        from aletheia.config.loader import load_config

        try:
            cfg = load_config(persist_migrations=False)
        except TypeError:  # compatibility with older/custom loaders
            cfg = load_config()
        url = f"http://127.0.0.1:{cfg.app.dashboard_port}/api/status"
        request = urllib.request.Request(
            url, headers={"User-Agent": "aletheia-update-continuity"}
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
        execution = payload.get("observation_execution")
        execution = execution if isinstance(execution, dict) else {}
        health_issues = payload.get("health_issues")
        health_issues = health_issues if isinstance(health_issues, list) else []
        return {
            "available": True,
            "unavailable_reason": None,
            "boot_commit": payload.get("boot_commit"),
            "current_commit": payload.get("current_commit") or payload.get("head_commit"),
            "scheduler_running": payload.get("scheduler_running"),
            "scheduler_job_count": payload.get("scheduler_job_count"),
            "health_status": payload.get("health_status"),
            "health_issues": health_issues,
            "observation_execution": execution,
        }
    except Exception as exc:
        return {
            "available": False,
            "unavailable_reason": "gateway_status_unavailable",
            "error": type(exc).__name__,
            "boot_commit": None,
            "current_commit": None,
            "scheduler_running": None,
            "scheduler_job_count": None,
            "health_status": None,
            "health_issues": [],
            "observation_execution": {},
        }


def _worker_signature() -> dict[str, Any]:
    """Read the persistent Worker pass signature maintained by worker_lock."""
    try:
        from aletheia.observation_lab.worker_lock import _lock_path

        path = Path(_lock_path())
        if not path.exists():
            return {
                "available": False,
                "unavailable_reason": "worker_signature_not_recorded",
                "source_commit": None,
            }
        values: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            key, sep, value = line.partition("=")
            if sep and key:
                values[key.strip()] = value.strip()
        source_commit = values.get("source_commit")
        if not source_commit or source_commit == "unknown":
            return {
                "available": False,
                "unavailable_reason": "worker_source_commit_unknown",
                "source_commit": None,
                "pid": values.get("pid"),
                "acquired_at": values.get("acquired_at"),
            }
        return {
            "available": True,
            "unavailable_reason": None,
            "source_commit": source_commit,
            "pid": values.get("pid"),
            "acquired_at": values.get("acquired_at"),
        }
    except Exception as exc:
        return {
            "available": False,
            "unavailable_reason": "worker_signature_read_failed",
            "error": type(exc).__name__,
            "source_commit": None,
        }


def _observation_ids() -> dict[str, Any]:
    """Read durable Observation identities; statuses may progress independently."""
    try:
        from aletheia.storage import repositories as repo

        rows = repo.list_filter_shadow_experiments(limit=1000)
        return {
            "available": True,
            "unavailable_reason": None,
            "ids": sorted(str(row["id"]) for row in rows if row.get("id")),
            "total": len(rows),
        }
    except Exception as exc:
        return {
            "available": False,
            "unavailable_reason": "observation_identity_read_failed",
            "error": type(exc).__name__,
            "ids": [],
            "total": None,
        }


def capture_update_continuity() -> dict[str, Any]:
    """Capture one Main + Observation read-only deployment snapshot."""
    try:
        from aletheia.cli.control_cmd import build_control_status

        control = build_control_status()
        control_available = True
        control_error = None
    except Exception as exc:
        control = {}
        control_available = False
        control_error = type(exc).__name__

    try:
        from aletheia.cli.observe_cmd import _collect_overview

        overview = _collect_overview(equivalence_limit=5)
        overview_available = True
        overview_error = None
    except Exception as exc:
        overview = {}
        overview_available = False
        overview_error = type(exc).__name__

    try:
        from aletheia.observation_lab.execution_owner import get_execution_owner

        owner = get_execution_owner()
    except Exception:
        owner = "unavailable"

    runtime = control.get("runtime") if isinstance(control, dict) else None
    runtime = runtime if isinstance(runtime, dict) else {}
    data_root = runtime.get("data_root")
    data_root = data_root if isinstance(data_root, dict) else {}
    sha = control.get("sha") if isinstance(control, dict) else None
    sha = sha if isinstance(sha, dict) else {}

    manifests = overview.get("manifests") if isinstance(overview, dict) else None
    manifests = manifests if isinstance(manifests, dict) else {}
    active = manifests.get("active") if isinstance(manifests, dict) else None
    active = active if isinstance(active, list) else []
    manifest_generations = {
        str(item.get("observation_id")): int(item.get("generation"))
        for item in active
        if isinstance(item, dict)
        and item.get("observation_id") is not None
        and item.get("generation") is not None
    }

    checkpoint = overview.get("checkpoint") if isinstance(overview, dict) else None
    checkpoint = checkpoint if isinstance(checkpoint, dict) else {}

    return {
        "schema_version": _SCHEMA_VERSION,
        "captured_at": _now(),
        "capture_available": True,
        "capture_source": "in_process",
        "control": {
            "available": control_available,
            "error": control_error,
            "schema_version": control.get("schema_version") if isinstance(control, dict) else None,
            "disk_sha": sha.get("disk_head"),
            "running_sha": sha.get("deployed_head"),
            "data_root": data_root,
        },
        "gateway": _gateway_status(),
        "observation": {
            "owner": owner,
            "exclusive_owner_contract": owner in _VALID_OWNERS,
            "worker": _worker_signature(),
            "overview_available": overview_available,
            "overview_error": overview_error,
            "identities": _observation_ids(),
            "manifest_generations": manifest_generations,
            "checkpoint": checkpoint,
            "experiments": overview.get("experiments") if isinstance(overview, dict) else None,
            "ufc_equivalence": overview.get("ufc_equivalence") if isinstance(overview, dict) else None,
        },
    }


def _unavailable_fresh_snapshot(reason: str, *, detail: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": _SCHEMA_VERSION,
        "captured_at": _now(),
        "capture_available": False,
        "capture_source": "fresh_target_process",
        "capture_error": reason,
        "capture_detail": detail,
        "control": {},
        "gateway": {},
        "observation": {},
    }


def capture_update_continuity_fresh() -> dict[str, Any]:
    """Capture postflight from a fresh interpreter loaded from the target checkout.

    The updater process imported pre-update modules before `git pull`; reusing
    those modules after the pull would prove the target with stale Python code.
    A fresh interpreter preserves the same read-model helpers while loading the
    newly checked-out source from disk.
    """
    from aletheia.config.loader import _PROJECT_ROOT

    script = (
        "import json\n"
        "from aletheia.update.continuity import capture_update_continuity\n"
        "snapshot = capture_update_continuity()\n"
        "snapshot['capture_source'] = 'fresh_target_process'\n"
        f"print({_FRESH_CAPTURE_PREFIX!r} + json.dumps(snapshot, ensure_ascii=False, sort_keys=True))\n"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(_PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=20,
            env=os.environ.copy(),
        )
    except Exception as exc:
        return _unavailable_fresh_snapshot(
            "target_postflight_process_failed", detail=type(exc).__name__
        )
    if result.returncode != 0:
        return _unavailable_fresh_snapshot(
            "target_postflight_process_failed",
            detail=(result.stderr or result.stdout).strip()[-500:],
        )
    for line in reversed(result.stdout.splitlines()):
        if not line.startswith(_FRESH_CAPTURE_PREFIX):
            continue
        try:
            payload = json.loads(line[len(_FRESH_CAPTURE_PREFIX) :])
        except json.JSONDecodeError:
            break
        if isinstance(payload, dict):
            payload["capture_source"] = "fresh_target_process"
            payload["capture_available"] = True
            return payload
    return _unavailable_fresh_snapshot(
        "target_postflight_payload_missing",
        detail=result.stdout.strip()[-500:],
    )


def _runtime_owner_proof(snapshot: dict[str, Any]) -> dict[str, Any]:
    gateway = snapshot.get("gateway") or {}
    execution = gateway.get("observation_execution") or {}
    return execution if isinstance(execution, dict) else {}


def evaluate_preflight(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Fail only on directly observed structural pre-existing integrity faults."""
    failures: list[str] = []
    warnings: list[str] = []
    proof_gaps: list[str] = []
    observation = snapshot.get("observation") or {}
    owner = observation.get("owner")
    if owner not in _VALID_OWNERS:
        failures.append("observation_owner_invalid")

    runtime_owner = _runtime_owner_proof(snapshot)
    if runtime_owner.get("available"):
        runtime_configured_owner = runtime_owner.get("owner")
        if runtime_configured_owner in _VALID_OWNERS and runtime_configured_owner != owner:
            failures.append("observation_runtime_owner_mismatch")
        if runtime_owner.get("exclusive") is False:
            failures.append("duplicate_observation_cycle_owner")
    elif (snapshot.get("gateway") or {}).get("available"):
        warnings.append("observation_runtime_owner_proof_unavailable")

    checkpoint = observation.get("checkpoint") or {}
    verified = checkpoint.get("verified") if isinstance(checkpoint, dict) else None
    if isinstance(verified, dict) and verified.get("ok") is False:
        failures.append("observation_checkpoint_drift")

    control = snapshot.get("control") or {}
    data_root = control.get("data_root") or {}
    if not control.get("available"):
        warnings.append("control_status_unavailable")
    if not data_root.get("available"):
        proof_gaps.append("data_root_baseline_unavailable")
    if not observation.get("overview_available"):
        proof_gaps.append("observation_overview_baseline_unavailable")
    identities = observation.get("identities") or {}
    if not identities.get("available"):
        proof_gaps.append("observation_identity_baseline_unavailable")
    worker = observation.get("worker") or {}
    if owner == "worker" and not worker.get("available"):
        proof_gaps.append("worker_identity_baseline_unavailable")
    if not (snapshot.get("gateway") or {}).get("available"):
        warnings.append("gateway_status_unavailable")

    return {
        "ok": not failures,
        "failures": failures,
        "warnings": warnings,
        "proof_gaps": proof_gaps,
    }


def _identity(value: dict[str, Any] | None) -> str | None:
    if not isinstance(value, dict) or value.get("available") is not True:
        return None
    identity = value.get("identity")
    return str(identity) if identity else None


def compare_update_continuity(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    expected_target: str | None,
    restart: bool,
) -> dict[str, Any]:
    """Compare structural invariants and keep missing proof distinct from failure."""
    failures: list[str] = []
    warnings: list[str] = []
    proof_gaps: list[str] = []

    if after.get("capture_available") is False:
        failures.append("target_postflight_projection_unavailable")

    before_obs = before.get("observation") or {}
    after_obs = after.get("observation") or {}
    before_control = before.get("control") or {}
    after_control = after.get("control") or {}

    if before_obs.get("owner") != after_obs.get("owner"):
        failures.append("observation_owner_changed")
    if after_obs.get("owner") not in _VALID_OWNERS:
        failures.append("observation_owner_invalid")

    runtime_owner = _runtime_owner_proof(after)
    if runtime_owner.get("available"):
        runtime_configured_owner = runtime_owner.get("owner")
        if (
            runtime_configured_owner in _VALID_OWNERS
            and runtime_configured_owner != after_obs.get("owner")
        ):
            failures.append("observation_runtime_owner_mismatch")
        if runtime_owner.get("exclusive") is False:
            failures.append("duplicate_observation_cycle_owner")
    elif after_obs.get("owner") == "worker":
        proof_gaps.append("observation_runtime_owner_proof_unavailable")

    before_root = _identity(before_control.get("data_root"))
    after_root = _identity(after_control.get("data_root"))
    if before_root and not after_root:
        failures.append("data_root_identity_lost")
    elif before_root and after_root and before_root != after_root:
        failures.append("data_root_changed")
    elif not before_root or not after_root:
        proof_gaps.append("data_root_comparison_unavailable")

    before_worker = before_obs.get("worker") or {}
    after_worker = after_obs.get("worker") or {}
    before_worker_sha = before_worker.get("source_commit") if before_worker.get("available") else None
    after_worker_sha = after_worker.get("source_commit") if after_worker.get("available") else None
    if before_obs.get("owner") == "worker":
        if before_worker_sha and not after_worker_sha:
            failures.append("worker_identity_lost")
        elif before_worker_sha and after_worker_sha and before_worker_sha != after_worker_sha:
            failures.append("worker_identity_changed")
        elif not before_worker_sha or not after_worker_sha:
            proof_gaps.append("worker_identity_comparison_unavailable")

    if before_obs.get("overview_available") and not after_obs.get("overview_available"):
        failures.append("observation_state_unreadable")
    elif not before_obs.get("overview_available") or not after_obs.get("overview_available"):
        proof_gaps.append("observation_overview_comparison_unavailable")

    before_ids = before_obs.get("identities") or {}
    after_ids = after_obs.get("identities") or {}
    if before_ids.get("available") and after_ids.get("available"):
        missing = sorted(set(before_ids.get("ids") or []) - set(after_ids.get("ids") or []))
        if missing:
            failures.append("observation_history_missing")
    elif before_ids.get("available") and not after_ids.get("available"):
        failures.append("observation_identity_read_lost")
    else:
        proof_gaps.append("observation_identity_comparison_unavailable")

    before_gen = before_obs.get("manifest_generations") or {}
    after_gen = after_obs.get("manifest_generations") or {}
    for observation_id, generation in before_gen.items():
        if observation_id not in after_gen:
            warnings.append(f"manifest_no_longer_active:{observation_id}")
            continue
        if int(after_gen[observation_id]) < int(generation):
            failures.append(f"manifest_generation_regressed:{observation_id}")

    checkpoint = after_obs.get("checkpoint") or {}
    verified = checkpoint.get("verified") if isinstance(checkpoint, dict) else None
    if isinstance(verified, dict) and verified.get("ok") is False:
        # A legitimate Main migration or independent Worker progress can move
        # the checkpoint fingerprint. The runner re-anchors on the next pass.
        warnings.append("observation_checkpoint_changed")

    gateway = after.get("gateway") or {}
    if expected_target:
        disk_sha = after_control.get("disk_sha")
        if disk_sha and disk_sha != expected_target:
            failures.append("main_disk_target_mismatch")
        elif not disk_sha:
            proof_gaps.append("main_disk_identity_unavailable")

        if restart:
            if not gateway.get("available"):
                failures.append("main_gateway_identity_unavailable")
            else:
                boot = gateway.get("boot_commit")
                current = gateway.get("current_commit")
                if boot and boot != expected_target:
                    failures.append("main_gateway_boot_target_mismatch")
                if current and current != expected_target:
                    failures.append("main_gateway_disk_target_mismatch")
                if not boot and not current:
                    proof_gaps.append("main_gateway_identity_unavailable")
        else:
            proof_gaps.append("main_restart_required_for_full_verification")
    else:
        proof_gaps.append("main_target_identity_unavailable")

    if restart:
        if not gateway.get("available"):
            proof_gaps.append("main_health_unavailable")
        else:
            health_status = gateway.get("health_status")
            if health_status == "degraded":
                failures.append("main_health_degraded")
            elif health_status != "healthy":
                proof_gaps.append("main_health_not_verified")

    return {
        "ok": not failures and not proof_gaps,
        "structural_ok": not failures,
        "failures": sorted(set(failures)),
        "proof_gaps": sorted(set(proof_gaps)),
        "warnings": sorted(set(warnings)),
        "owner": after_obs.get("owner"),
        "worker_sha_before": before_worker_sha,
        "worker_sha_after": after_worker_sha,
        "data_root_unchanged": bool(before_root and after_root and before_root == after_root),
        "main_health_status": gateway.get("health_status"),
        "main_health_issues": gateway.get("health_issues") or [],
        "main_observation_cycle_registered": runtime_owner.get("main_cycle_registered"),
        "observation_execution_exclusive": runtime_owner.get("exclusive"),
        "postflight_capture_source": after.get("capture_source"),
    }


def _record(event_type: str, commit: str, message: str, **extra: Any) -> None:
    write_audit(
        {
            "actor": "human",
            "source": "cli",
            "event_type": event_type,
            "entity_type": "system_update",
            "entity_id": commit or "unknown",
            "message": message,
            **extra,
        }
    )


def _postflight_settled(snapshot: dict[str, Any], target: str) -> bool:
    gateway = snapshot.get("gateway") or {}
    if not gateway.get("available"):
        return False
    if target not in {gateway.get("boot_commit"), gateway.get("current_commit")}:
        return False
    return gateway.get("health_status") in {"healthy", "degraded"}


def apply_update_with_continuity(*, restart: bool = False) -> dict[str, Any]:
    """Run the existing source updater with Observer pre/postflight verification."""
    before = capture_update_continuity()
    preflight = evaluate_preflight(before)
    if not preflight["ok"]:
        old = (before.get("control") or {}).get("disk_sha") or "unknown"
        _record(
            "update_observer_preflight_failed",
            old,
            "Source update blocked by Observer structural preflight",
            continuity={"preflight": preflight, "before": before},
        )
        return {
            "applied": False,
            "mode": "development",
            "reason": "observer_preflight_failed",
            "message": ", ".join(preflight["failures"]),
            "continuity_verified": False,
            "continuity_state": "failed",
            "continuity": {"preflight": preflight, "before": before},
        }

    from aletheia.update.applier import apply_update

    result = apply_update(restart=restart)
    if not result.get("applied"):
        return {
            **result,
            "continuity_verified": None,
            "continuity_state": "not_run",
            "continuity": {"preflight": preflight, "before": before},
        }

    target = result.get("new_commit")
    expected_target = (
        str(target)
        if result.get("mode") == "development" and target
        else None
    )

    # Only a development result with an operation-owned target SHA can be
    # proven against target-loaded code. If that contract is unavailable, keep
    # the result explicitly unproven rather than launching a subprocess and
    # guessing which code/data root it represents.
    after = (
        capture_update_continuity_fresh()
        if expected_target
        else capture_update_continuity()
    )
    if restart and expected_target:
        for _ in range(4):
            if _postflight_settled(after, expected_target):
                break
            time.sleep(1)
            after = capture_update_continuity_fresh()

    comparison = compare_update_continuity(
        before,
        after,
        expected_target=expected_target,
        restart=restart,
    )
    continuity = {
        "preflight": preflight,
        "before": before,
        "after": after,
        "comparison": comparison,
    }

    if comparison["failures"]:
        _record(
            "update_observer_postflight_failed",
            str(target or "unknown"),
            "Source update applied but Observer continuity verification failed",
            continuity=continuity,
        )
        return {
            **result,
            "reason": "observer_continuity_failed",
            "message": (
                "Update was applied, but Observer continuity verification failed: "
                + ", ".join(comparison["failures"])
            ),
            "continuity_verified": False,
            "continuity_state": "failed",
            "continuity": continuity,
        }

    if comparison["proof_gaps"]:
        only_restart_pending = set(comparison["proof_gaps"]) == {
            "main_restart_required_for_full_verification"
        }
        state = "pending_restart" if only_restart_pending else "unproven"
        _record(
            "update_observer_postflight_unproven",
            str(target or "unknown"),
            "Source update applied but Observer continuity proof is incomplete",
            continuity=continuity,
        )
        return {
            **result,
            "reason": (
                "restart_required_for_continuity_verification"
                if only_restart_pending
                else "observer_continuity_unproven"
            ),
            "message": (
                "Update was applied, but continuity is not proven: "
                + ", ".join(comparison["proof_gaps"])
            ),
            "continuity_verified": False,
            "continuity_state": state,
            "continuity": continuity,
        }

    _record(
        "update_observer_continuity_verified",
        str(target or "unknown"),
        "Source update and Observer continuity verification succeeded",
        continuity={
            "comparison": comparison,
            "warnings": sorted(
                set(preflight["warnings"] + comparison["warnings"])
            ),
        },
    )
    return {
        **result,
        "continuity_verified": True,
        "continuity_state": "verified",
        "continuity": continuity,
    }
