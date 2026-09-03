"""Minimal Observer continuity proof for source updates (Issue #1135)."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any
import urllib.request

from aletheia.files.jsonl import write_audit
from aletheia.update.process_env import safe_env

_SCHEMA_VERSION = "update_observer_continuity/v2"
_VALID_OWNERS = {"main", "worker"}
_FRESH_CAPTURE_PREFIX = "ALETHEIA_CONTINUITY_SNAPSHOT="
_POSTFLIGHT_SETTLE_TIMEOUT_SEC = 120.0
_POSTFLIGHT_POLL_SEC = 2.0
_CHECKPOINT_MISMATCH_RE = re.compile(
    r"^fingerprint_mismatch: app_head\((.*?)→(.*?)\) "
    r"count\((\d+)→(\d+)\) manifests\((\d+)→(\d+)\)$"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _gateway_status(*, timeout_sec: float = 2.0) -> dict[str, Any]:
    """Read the running Gateway status contract without invoking another CLI."""
    try:
        from aletheia.config.loader import load_config

        try:
            cfg = load_config(persist_migrations=False)
        except TypeError:  # compatibility with older/custom loaders
            cfg = load_config()
        url = f"http://127.0.0.1:{cfg.app.dashboard_port}/api/status"
        request = urllib.request.Request(url, headers={"User-Agent": "aletheia-update-continuity"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=max(0.001, min(2.0, float(timeout_sec)))) as response:
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
        from aletheia.observation_lab.canonical import get_authority_source

        if get_authority_source().source == "lab":
            from aletheia.observation_lab.db import readonly_lab_db

            with readonly_lab_db() as lab:
                rows = lab.execute(
                    "SELECT observation_id, generation FROM observation_windows "
                    "ORDER BY observation_id, generation"
                ).fetchall()
            ids = [f"{row['observation_id']}:g{int(row['generation'])}" for row in rows]
            return {
                "available": True,
                "unavailable_reason": None,
                "ids": ids,
                "total": len(ids),
            }
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
    """Capture only the structural evidence needed by the update boundary."""
    try:
        from aletheia.update import git_ops

        disk_sha = git_ops.current_commit() or None
    except Exception as exc:
        disk_sha = None
        control_error = type(exc).__name__
    else:
        control_error = None

    try:
        from aletheia.cli.control_cmd import _data_root_identity

        data_root = _data_root_identity()
    except Exception as exc:
        data_root = {
            "available": False,
            "identity": None,
            "unavailable_reason": "data_root_read_failed",
            "error": type(exc).__name__,
        }

    try:
        from aletheia.config.loader import read_only_config
        from aletheia.observation_lab.execution_owner import get_execution_owner

        with read_only_config():
            owner = get_execution_owner()
    except Exception:
        owner = "unavailable"

    identities: dict[str, Any]
    manifest_generations: dict[str, int] = {}
    checkpoint: dict[str, Any] = {}
    observation_available = False
    observation_error = None
    try:
        from aletheia.observation_lab.checkpoint import verify_joint_checkpoint
        from aletheia.observation_lab.db import readonly_lab_db
        from aletheia.observation_lab.manifest import load_active_manifests
        from aletheia.storage.sqlite import readonly_db

        with readonly_db(), readonly_lab_db():
            identities = _observation_ids()
            manifest_generations = {str(item.observation_id): int(item.generation) for item in load_active_manifests()}
            verified = verify_joint_checkpoint()
            checkpoint = {
                "verified": {
                    "ok": verified.ok,
                    "reason": verified.reason,
                    "current_fingerprint": verified.current_fingerprint,
                }
            }
        observation_available = identities.get("available") is True
    except Exception as exc:
        identities = {
            "available": False,
            "unavailable_reason": "observation_identity_read_failed",
            "error": type(exc).__name__,
            "ids": [],
            "total": None,
        }
        observation_error = type(exc).__name__

    return {
        "schema_version": _SCHEMA_VERSION,
        "captured_at": _now(),
        "capture_available": True,
        "capture_source": "in_process",
        "control": {
            "available": disk_sha is not None and data_root.get("available") is True,
            "error": control_error,
            "disk_sha": disk_sha,
            "running_sha": None,
            "data_root": data_root,
        },
        "gateway": _gateway_status(),
        "observation": {
            "owner": owner,
            "exclusive_owner_contract": owner in _VALID_OWNERS,
            "worker": _worker_signature(),
            # v1 compatibility: now means only continuity evidence was readable.
            "overview_available": observation_available,
            "overview_error": observation_error,
            "identities": identities,
            "manifest_generations": manifest_generations,
            "checkpoint": checkpoint,
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


def capture_update_continuity_fresh(*, timeout_sec: float = 20.0) -> dict[str, Any]:
    """Capture postflight from a fresh interpreter loaded from the target checkout."""
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
            timeout=max(0.001, float(timeout_sec)),
            env=safe_env(),
        )
    except subprocess.TimeoutExpired:
        return _unavailable_fresh_snapshot("target_postflight_projection_unavailable", detail="TimeoutExpired")
    except Exception as exc:
        return _unavailable_fresh_snapshot("target_postflight_process_failed", detail=type(exc).__name__)
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


def _checkpoint_preflight_verdict(checkpoint: dict[str, Any]) -> tuple[str | None, str | None]:
    """Classify checkpoint drift without turning normal manifest expiry into a gate.

    The canonical Observation runner warns and re-anchors when the joint
    fingerprint changes. During deployment preflight we only hard-fail the part
    that proves app.db structural drift: migration head/count movement. A
    mismatch caused solely by the time-dependent active-manifest set is normal
    Observation lifecycle progress and remains warning-only. Unknown mismatch
    shapes fail closed rather than being silently reclassified.
    """
    verified = checkpoint.get("verified") if isinstance(checkpoint, dict) else None
    if not isinstance(verified, dict) or verified.get("ok") is not False:
        return (None, None)
    reason = str(verified.get("reason") or "")
    match = _CHECKPOINT_MISMATCH_RE.fullmatch(reason)
    if match is None:
        return ("failure", "observation_checkpoint_drift")
    old_head, new_head, old_count, new_count, _old_manifests, _new_manifests = match.groups()
    if old_head != new_head or old_count != new_count:
        return ("failure", "observation_checkpoint_app_migration_drift")
    return ("warning", "observation_checkpoint_changed")


# (description, fix_hint) — fix_hint is "" when there is no specific user action.
_CODE_HINTS: dict[str, tuple[str, str]] = {
    # ── checkpoint verdict ────────────────────────────────────────────────────
    "observation_checkpoint_app_migration_drift": (
        "DB migrations ran since the last observation checkpoint was recorded",
        "aletheia observe checkpoint",
    ),
    "observation_checkpoint_drift": (
        "Observation checkpoint fingerprint has an unrecognised mismatch shape",
        "aletheia observe overview --json",
    ),
    "observation_checkpoint_changed": (
        "Observation checkpoint fingerprint changed (normal lifecycle, not blocking)",
        "",
    ),
    # ── owner / runtime proof ─────────────────────────────────────────────────
    "observation_owner_invalid": (
        "Observation owner field is missing or unrecognised",
        "aletheia observe overview --json",
    ),
    "observation_owner_changed": (
        "Observation owner changed during the update",
        "aletheia observe overview --json",
    ),
    "observation_runtime_owner_mismatch": (
        "Runtime observation owner does not match the stored owner",
        "aletheia observe overview --json",
    ),
    "duplicate_observation_cycle_owner": (
        "More than one observation cycle owner is active simultaneously",
        "aletheia observe overview --json",
    ),
    "observation_runtime_owner_proof_unavailable": (
        "Runtime owner proof unavailable; ownership claim cannot be verified",
        "",
    ),
    # ── postflight projection ─────────────────────────────────────────────────
    "target_postflight_projection_unavailable": (
        "Could not capture post-update Observer state for comparison",
        "aletheia doctor",
    ),
    # ── data root ─────────────────────────────────────────────────────────────
    "data_root_identity_lost": (
        "Data root hash was known before the update but is missing after",
        "aletheia doctor",
    ),
    "data_root_changed": (
        "Data root hash changed during the update (unexpected DB mutation)",
        "aletheia control status --json",
    ),
    "data_root_baseline_unavailable": (
        "Data root baseline was not captured before the update",
        "",
    ),
    "data_root_comparison_unavailable": (
        "Data root comparison unavailable (missing before or after snapshot)",
        "",
    ),
    # ── worker identity ───────────────────────────────────────────────────────
    "worker_identity_lost": (
        "Worker commit identity was known before the update but is missing after",
        "aletheia doctor",
    ),
    "worker_identity_changed": (
        "Worker commit changed unexpectedly during the update",
        "aletheia observe overview --json",
    ),
    "worker_identity_baseline_unavailable": (
        "Worker identity baseline was not captured before the update",
        "",
    ),
    "worker_identity_comparison_unavailable": (
        "Worker identity comparison unavailable (missing before or after snapshot)",
        "",
    ),
    # ── observation overview / history ────────────────────────────────────────
    "observation_state_unreadable": (
        "Observation overview was readable before the update but unreadable after",
        "aletheia observe overview --json",
    ),
    "observation_history_missing": (
        "One or more Observation IDs present before the update are missing after",
        "aletheia observe list",
    ),
    "observation_identity_read_lost": (
        "Observation identities were readable before the update but cannot be read after",
        "aletheia observe overview --json",
    ),
    "observation_overview_baseline_unavailable": (
        "Observation overview baseline was not captured before the update",
        "",
    ),
    "observation_identity_baseline_unavailable": (
        "Observation identity baseline was not captured before the update",
        "",
    ),
    "observation_overview_comparison_unavailable": (
        "Observation overview not available for before/after comparison",
        "",
    ),
    "observation_identity_comparison_unavailable": (
        "Observation identities not available for before/after comparison",
        "",
    ),
    # ── gateway / main process ────────────────────────────────────────────────
    "main_disk_target_mismatch": (
        "On-disk source commit does not match the expected update target",
        "aletheia control status --json",
    ),
    "main_disk_identity_unavailable": (
        "On-disk commit identity could not be read after update",
        "",
    ),
    "main_gateway_identity_unavailable": (
        "Gateway identity unavailable; could not verify post-restart commit",
        "aletheia doctor",
    ),
    "main_gateway_boot_target_mismatch": (
        "Gateway booted from an unexpected commit (≠ update target)",
        "aletheia gateway restart",
    ),
    "main_gateway_disk_target_mismatch": (
        "Gateway reports an unexpected current commit (≠ update target)",
        "aletheia gateway restart",
    ),
    "main_health_degraded": (
        "Gateway health is degraded after the update (not pre-existing)",
        "aletheia doctor",
    ),
    "main_health_unavailable": (
        "Gateway health status unavailable after restart",
        "aletheia doctor",
    ),
    "main_health_not_verified": (
        "Gateway health not yet verified (may still be initialising)",
        "",
    ),
    "main_restart_required_for_full_verification": (
        "Full continuity verification requires a gateway restart",
        "aletheia gateway restart",
    ),
    "main_target_identity_unavailable": (
        "Cannot determine the expected update target commit",
        "",
    ),
    # ── control / metadata ────────────────────────────────────────────────────
    "control_status_unavailable": (
        "Control-plane status unavailable; continuity partially unverifiable",
        "",
    ),
    "gateway_status_unavailable": (
        "Gateway status unavailable; health check skipped",
        "",
    ),
    "update_metadata_persist_unverified": (
        "Update metadata persistence could not be verified",
        "",
    ),
}


def describe_failure_code(code: str) -> str:
    """Return a human-readable description for a failure/proof-gap/warning code.

    Falls back to the raw code if no description is registered.
    Dynamic codes (e.g. manifest_generation_regressed:obs-id) are matched by
    prefix so the human-readable part is prepended to the dynamic suffix.
    """
    if code in _CODE_HINTS:
        return _CODE_HINTS[code][0]
    for prefix, (desc, _) in _CODE_HINTS.items():
        if ":" in prefix and code.startswith(prefix.split(":")[0] + ":"):
            suffix = code[len(prefix.split(":")[0]) + 1 :]
            return f"{desc}: {suffix}"
    return code


def _preflight_failure_message(failures: list[str]) -> tuple[str, str]:
    """Return (human-readable detail, fix hint) for a preflight failure list."""
    if len(failures) == 1:
        desc, fix = _CODE_HINTS.get(failures[0], (failures[0], ""))
        return desc, fix
    # Multiple failures: list descriptions, no single fix hint
    descs = [describe_failure_code(f) for f in failures]
    return "; ".join(descs), ""


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
    elif owner == "worker":
        proof_gaps.append("observation_runtime_owner_proof_unavailable")

    checkpoint_kind, checkpoint_reason = _checkpoint_preflight_verdict(observation.get("checkpoint") or {})
    if checkpoint_kind == "failure" and checkpoint_reason:
        failures.append(checkpoint_reason)
    elif checkpoint_kind == "warning" and checkpoint_reason:
        warnings.append(checkpoint_reason)

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
        "failures": sorted(set(failures)),
        "warnings": sorted(set(warnings)),
        "proof_gaps": sorted(set(proof_gaps)),
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
        gateway = after.get("gateway") or {}
        before_health = (before.get("gateway") or {}).get("health_status")
        if expected_target and restart:
            boot = gateway.get("boot_commit")
            current = gateway.get("current_commit")
            if boot and boot != expected_target:
                failures.append("boot_commit_mismatch")
            if current and current != expected_target:
                failures.append("current_commit_mismatch")
        if gateway.get("health_status") == "degraded" and before_health != "degraded":
            failures.append("main_health_degraded")
        return {
            "ok": False,
            "structural_ok": not failures,
            "continuity_verified": False,
            "failures": sorted(set(failures)),
            "proof_gaps": ["target_postflight_projection_unavailable"],
            "warnings": [],
            "owner": None,
            "worker_sha_before": None,
            "worker_sha_after": None,
            "data_root_unchanged": False,
            "main_health_status": gateway.get("health_status"),
            "main_health_issues": gateway.get("health_issues") or [],
            "main_observation_cycle_registered": None,
            "observation_execution_exclusive": None,
            "postflight_capture_source": after.get("capture_source"),
        }

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
        if runtime_configured_owner in _VALID_OWNERS and runtime_configured_owner != after_obs.get("owner"):
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
        warnings.append("observation_checkpoint_changed")

    gateway = after.get("gateway") or {}
    if expected_target:
        disk_sha = after_control.get("disk_sha")
        if disk_sha and disk_sha != expected_target:
            failures.append("main_disk_target_mismatch")
        elif not disk_sha:
            proof_gaps.append("main_disk_identity_unavailable")

        if restart:
            # #1208: strict dual-commit identity — both must be present AND equal target
            boot = gateway.get("boot_commit")
            current = gateway.get("current_commit")
            if not boot:
                proof_gaps.append("boot_commit_missing")
            elif boot != expected_target:
                failures.append("boot_commit_mismatch")
            if not current:
                proof_gaps.append("current_commit_missing")
            elif current != expected_target:
                failures.append("current_commit_mismatch")
            if not boot and not current:
                proof_gaps.append("identity_unproven")
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
                before_health = (before.get("gateway") or {}).get("health_status")
                if before_health == "degraded":
                    # Pre-existing degradation: the update did not cause it.
                    warnings.append("main_health_degraded")
                else:
                    failures.append("main_health_degraded")
            elif health_status == "initializing":
                warnings.append("main_health_initializing")
            elif health_status != "healthy":
                proof_gaps.append("main_health_not_verified")

    # #1208: continuity_verified when both identities are strictly confirmed
    _boot = gateway.get("boot_commit")
    _current = gateway.get("current_commit")
    continuity_verified = bool(expected_target and restart and _boot == expected_target and _current == expected_target)
    return {
        "ok": not failures and not proof_gaps,
        "structural_ok": not failures,
        "continuity_verified": continuity_verified,
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
    if gateway.get("boot_commit") != target or gateway.get("current_commit") != target:
        return False
    return gateway.get("health_status") in {"healthy", "degraded", "initializing"}


def capture_update_continuity_until_settled(
    target: str,
    *,
    timeout_sec: float = _POSTFLIGHT_SETTLE_TIMEOUT_SEC,
    poll_sec: float = _POSTFLIGHT_POLL_SEC,
) -> dict[str, Any]:
    """Wait for Gateway readiness, then capture target structural evidence once."""
    started = time.monotonic()
    deadline = started + max(0.0, float(timeout_sec))
    poll = max(0.1, float(poll_sec))
    gateway_started = time.monotonic()
    if deadline <= gateway_started:
        gateway = {"available": False, "health_status": None}
    else:
        gateway = _gateway_status(timeout_sec=deadline - gateway_started)
    while not _postflight_settled({"gateway": gateway}, target):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(poll, remaining))
        if deadline - time.monotonic() <= 0:
            break
        gateway = _gateway_status(timeout_sec=max(0.001, deadline - time.monotonic()))
    gateway_done = time.monotonic()

    remaining = deadline - gateway_done
    if not _postflight_settled({"gateway": gateway}, target) or remaining <= 0:
        after = _unavailable_fresh_snapshot(
            "target_postflight_projection_unavailable",
            detail="gateway_readiness_deadline_exhausted",
        )
        after["gateway"] = gateway
        target_capture_ms = 0
        last_stage = "gateway_wait"
    else:
        capture_started = time.monotonic()
        after = capture_update_continuity_fresh(timeout_sec=min(20.0, remaining))
        if after.get("capture_available") is False:
            after["gateway"] = gateway
        target_capture_ms = round((time.monotonic() - capture_started) * 1000)
        last_stage = "target_capture"
    after["timings_ms"] = {
        "gateway_wait": round((gateway_done - gateway_started) * 1000),
        "target_capture": target_capture_ms,
        "total": round((time.monotonic() - started) * 1000),
    }
    after["deadline_sec"] = float(timeout_sec)
    after["last_stage"] = last_stage
    return after


def apply_update_with_continuity(*, restart: bool = False, progress=None) -> dict[str, Any]:
    """Run the existing source updater with Observer pre/postflight verification."""
    total_started = time.monotonic()
    phase_name: str | None = None
    phase_started = total_started
    phase_started_at = _now()
    phase_timings_ms: dict[str, int] = {}
    phase_timeline: list[dict[str, Any]] = []

    def mark_phase(message: str) -> None:
        nonlocal phase_name, phase_started, phase_started_at
        now = time.monotonic()
        if phase_name is not None:
            duration = round((now - phase_started) * 1000)
            phase_timings_ms[phase_name] = duration
            phase_timeline.append({"phase": phase_name, "started_at": phase_started_at, "duration_ms": duration})
        phase_name = message
        phase_started = now
        phase_started_at = _now()
        if progress:
            progress(message)

    def finish_phase() -> None:
        nonlocal phase_name
        if phase_name is None:
            return
        now = time.monotonic()
        duration = round((now - phase_started) * 1000)
        phase_timings_ms[phase_name] = duration
        phase_timeline.append({"phase": phase_name, "started_at": phase_started_at, "duration_ms": duration})
        phase_name = None

    mark_phase("Running preflight checks")
    preflight_started = time.monotonic()
    before = capture_update_continuity()
    preflight = evaluate_preflight(before)
    preflight_capture_ms = round((time.monotonic() - preflight_started) * 1000)
    if not preflight["ok"]:
        finish_phase()
        timings_ms = {
            "preflight_capture": preflight_capture_ms,
            "gateway_wait": 0,
            "target_capture": 0,
            "comparison": 0,
            "total": round((time.monotonic() - total_started) * 1000),
            "phases": phase_timings_ms,
        }
        old = (before.get("control") or {}).get("disk_sha") or "unknown"
        _record(
            "update_observer_preflight_failed",
            old,
            "Source update blocked by Observer structural preflight",
            continuity={"preflight": preflight, "before": before, "timings_ms": timings_ms},
        )
        failures = preflight["failures"]
        message, fix_hint = _preflight_failure_message(failures)
        return {
            "applied": False,
            "mode": "development",
            "reason": "observer_preflight_failed",
            "message": message,
            "fix_hint": fix_hint,
            "continuity_verified": False,
            "continuity_state": "failed",
            "continuity": {
                "preflight": preflight,
                "before": before,
                "timings_ms": timings_ms,
                "phase_timeline": phase_timeline,
            },
        }

    from aletheia.update.applier import apply_update

    result = apply_update(restart=restart, progress=mark_phase)
    if not result.get("applied"):
        finish_phase()
        return {
            **result,
            "continuity_verified": None,
            "continuity_state": "not_run",
            "continuity": {
                "preflight": preflight,
                "before": before,
                "timings_ms": {
                    "preflight_capture": preflight_capture_ms,
                    "gateway_wait": 0,
                    "target_capture": 0,
                    "comparison": 0,
                    "total": round((time.monotonic() - total_started) * 1000),
                    "phases": phase_timings_ms,
                },
                "phase_timeline": phase_timeline,
            },
        }

    # Re-anchor the observation checkpoint immediately after migrations run so
    # the next preflight sees the current migration state instead of the
    # pre-update one (which would trigger observation_checkpoint_app_migration_drift).
    checkpoint_reanchor_error: str | None = None
    try:
        from aletheia.config.loader import _PROJECT_ROOT, get_data_dir
        from aletheia.update import target_fresh

        reanchor = target_fresh.reanchor_joint_checkpoint(
            project_root=_PROJECT_ROOT,
            data_dir=get_data_dir(),
            env=safe_env(),
            python_executable=sys.executable,
        )
        if reanchor.get("ok") is not True:
            checkpoint_reanchor_error = str(
                reanchor.get("detail")
                or reanchor.get("reason")
                or "target_fresh_reanchor_failed"
            )
    except Exception as exc:
        checkpoint_reanchor_error = type(exc).__name__
    if checkpoint_reanchor_error:
        _record(
            "update_observer_checkpoint_reanchor_failed",
            str(result.get("new_commit") or "unknown"),
            "Update checkpoint re-anchor failed; continuing to postflight verification",
            error=checkpoint_reanchor_error,
        )

    before_gateway = before.get("gateway") or {}
    before_control = before.get("control") or {}
    # `current_commit` can already reflect a newer disk checkout while the
    # running process is still on its older boot commit.
    runtime_old_commit = (
        before_gateway.get("boot_commit") or before_control.get("running_sha") or before_gateway.get("current_commit")
    )
    result = {**result, "runtime_old_commit": runtime_old_commit}

    target = result.get("new_commit")
    expected_target = str(target) if result.get("mode") == "development" and target else None

    mark_phase("Verifying continuity")
    if expected_target and restart:
        after = capture_update_continuity_until_settled(expected_target)
    elif expected_target:
        after = capture_update_continuity_fresh()
    else:
        after = capture_update_continuity()

    comparison_started = time.monotonic()
    comparison = compare_update_continuity(
        before,
        after,
        expected_target=expected_target,
        restart=restart,
    )
    if checkpoint_reanchor_error:
        comparison["warnings"] = sorted(
            set((comparison.get("warnings") or []) + ["observation_checkpoint_reanchor_failed"])
        )
    comparison_ms = round((time.monotonic() - comparison_started) * 1000)
    finish_phase()
    after_timings = after.get("timings_ms") or {}
    timings_ms = {
        "preflight_capture": preflight_capture_ms,
        "gateway_wait": int(after_timings.get("gateway_wait") or 0),
        "target_capture": int(after_timings.get("target_capture") or 0),
        "comparison": comparison_ms,
        "total": round((time.monotonic() - total_started) * 1000),
        "phases": phase_timings_ms,
    }

    metadata_proof_gaps = ["update_metadata_persist_unverified"] if result.get("metadata_persisted") is False else []
    # A verified postflight cannot erase evidence that was already missing
    # before mutation, nor can it ignore target-side metadata persistence loss.
    combined_proof_gaps = sorted(
        set((preflight.get("proof_gaps") or []) + (comparison.get("proof_gaps") or []) + metadata_proof_gaps)
    )
    comparison["proof_gaps"] = combined_proof_gaps
    comparison["ok"] = not comparison["failures"] and not combined_proof_gaps

    continuity = {
        "preflight": preflight,
        "before": before,
        "after": after,
        "comparison": comparison,
        "timings_ms": timings_ms,
        "phase_timeline": phase_timeline,
    }

    if comparison["failures"]:
        recovery = None
        if restart and result.get("mode") == "development":
            from aletheia.update.applier import rollback_applied_update

            mark_phase("Rolling back failed update")
            recovery = rollback_applied_update(result)
            finish_phase()
            timings_ms["phases"] = phase_timings_ms
            timings_ms["total"] = round((time.monotonic() - total_started) * 1000)
            continuity["recovery"] = recovery
        _record(
            "update_observer_postflight_failed",
            str(target or "unknown"),
            "Source update applied but Observer continuity verification failed",
            continuity=continuity,
        )
        return {
            **result,
            "applied": False if recovery else result.get("applied"),
            "rolled_back": recovery.get("rolled_back") if recovery else False,
            "reason": "observer_continuity_failed",
            "message": (
                "Observer continuity verification failed"
                + (
                    " and the previous runtime was restored: "
                    if recovery and recovery.get("rolled_back")
                    else " and rollback could not be proven: "
                    if recovery
                    else ": "
                )
                + ", ".join(comparison["failures"])
            ),
            "continuity_verified": False,
            "continuity_state": "failed",
            "continuity": continuity,
        }

    if combined_proof_gaps:
        only_restart_pending = set(combined_proof_gaps) == {"main_restart_required_for_full_verification"}
        state = "pending_restart" if only_restart_pending else "unproven"
        recovery = None
        if restart and not only_restart_pending and result.get("mode") == "development":
            from aletheia.update.applier import rollback_applied_update

            mark_phase("Rolling back unproven update")
            recovery = rollback_applied_update(result)
            finish_phase()
            timings_ms["phases"] = phase_timings_ms
            timings_ms["total"] = round((time.monotonic() - total_started) * 1000)
            continuity["recovery"] = recovery
        _record(
            "update_observer_postflight_unproven",
            str(target or "unknown"),
            "Source update applied but Observer continuity proof is incomplete",
            continuity=continuity,
        )
        return {
            **result,
            "applied": False if recovery else result.get("applied"),
            "rolled_back": recovery.get("rolled_back") if recovery else False,
            "reason": (
                "restart_required_for_continuity_verification"
                if only_restart_pending
                else "observer_continuity_unproven"
            ),
            "message": (
                (
                    "Continuity is not proven and the previous runtime was restored: "
                    if recovery and recovery.get("rolled_back")
                    else "Continuity is not proven and rollback could not be proven: "
                    if recovery
                    else "Update was applied, but continuity is not proven: "
                )
                + ", ".join(combined_proof_gaps)
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
            "warnings": sorted(set((preflight.get("warnings") or []) + (comparison.get("warnings") or []))),
        },
    )
    if result.get("schema_changed") and result.get("frontend_built"):
        from aletheia.update.applier import _PREVIOUS_DASHBOARD_STATIC

        shutil.rmtree(_PREVIOUS_DASHBOARD_STATIC, ignore_errors=True)
    return {
        **result,
        "continuity_verified": True,
        "continuity_state": "verified",
        "continuity": continuity,
    }
