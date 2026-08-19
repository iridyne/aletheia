"""Release-tag deployment for the Fedora source runtime.

This is deliberately a small wrapper around the existing source updater pieces:
Git supplies a pinned release commit, ``BackupService`` supplies recoverable data,
and the Gateway lifecycle remains the only process owner. It never enables Live
trading or changes Risk, provider, or scheduler policy.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterator
import urllib.request

try:  # Windows supports the rest of the update CLI, but not Fedora auto deployment.
    import fcntl
except ImportError:  # pragma: no cover - exercised only on Windows
    fcntl = None

from aletheia.backup.restore import RestoreService
from aletheia.backup.service import BackupService
from aletheia.config.loader import _PROJECT_ROOT, get_data_dir, load_config
from aletheia.files.jsonl import write_audit
from aletheia.update import git_ops
from aletheia.update.applier import (
    _PREVIOUS_DASHBOARD_STATIC,
    _STAGED_DASHBOARD_STATIC,
    _activate_frontend_build,
    _restore_frontend_build,
    _safe_env,
)

_STABLE_TAG = re.compile(r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_VERSION = re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\'](?:\s*#.*)?\s*$', re.MULTILINE)
_STATE_SCHEMA = 1
_SYSTEMD_MARKER = "# Managed-By: Aletheia auto update"
_UNIT_NAMES = ("aletheia-auto-update.service", "aletheia-auto-update.timer")
_TRANSIENT_CANDIDATE_FAILURES = {"gateway_stop_failed"}
_RUNTIME_ROLE_ENV = "ALETHEIA_RUNTIME_ROLE"
_AUTO_DEPLOY_ROLE = "paper"
_ROLE_MIGRATION_DETAIL = (
    "Automatic deployment is pinned until this runtime is classified; "
    "migrate an existing Paper timer with ALETHEIA_RUNTIME_ROLE=paper "
    "aletheia update auto install."
)


class AutoUpdateError(RuntimeError):
    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        self.detail = detail
        super().__init__(detail or reason)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _paths() -> dict[str, Path]:
    root = get_data_dir() / "update"
    return {"root": root, "state": root / "auto-deploy.json", "lock": root / "auto-deploy.lock"}


def _read_state() -> dict[str, Any]:
    path = _paths()["state"]
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AutoUpdateError("state_corrupt", type(exc).__name__) from exc
    if not isinstance(value, dict):
        raise AutoUpdateError("state_corrupt", "state is not an object")
    return value


def _write_state(state: dict[str, Any], **changes: Any) -> dict[str, Any]:
    value = {**state, **changes, "schema_version": _STATE_SCHEMA, "updated_at": _now()}
    path = _paths()["state"]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(path)
    directory_fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return value


@contextmanager
def _deployment_lock() -> Iterator[bool]:
    """Hold the source-runtime lock without waiting behind another timer tick."""
    if fcntl is None:
        yield False
        return
    path = _paths()["lock"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _semver(value: str) -> tuple[int, int, int] | None:
    match = _STABLE_TAG.fullmatch(value if value.startswith("v") else f"v{value}")
    return tuple(map(int, match.groups())) if match else None


def _version_from_ref(ref: str) -> str:
    match = _VERSION.search(git_ops.version_at_ref(ref))
    return match.group(1) if match else ""


def _stable_tags(remote_tags: set[str]) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    for tag in git_ops.tag_names():
        if tag not in remote_tags:
            continue
        version = _semver(tag)
        commit = git_ops.tag_commit(tag)
        if version and commit:
            values[tag] = {"version": version, "sha": commit}
    return values


def _record_audit(
    event_type: str,
    entity_id: str,
    message: str,
    *,
    actor: str = "system",
    source: str = "auto_release_tag",
    **extra: Any,
) -> None:
    write_audit({
        "actor": actor,
        "source": source,
        "event_type": event_type,
        "entity_type": "system_update",
        "entity_id": entity_id,
        "message": message,
        **extra,
    })


def _http_json(path: str) -> dict[str, Any]:
    cfg = load_config()
    url = f"http://127.0.0.1:{cfg.app.dashboard_port}{path}"
    request = urllib.request.Request(url, headers={"User-Agent": "aletheia-auto-update"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=3) as response:
        return json.loads(response.read().decode("utf-8"))


def _runtime_gate(*, expected_sha: str | None = None, expected_version: str | None = None) -> dict[str, Any]:
    """Read the existing health/status contracts; do not repair runtime state."""
    try:
        health = _http_json("/api/health")
        status = _http_json("/api/status")
    except Exception as exc:
        return {"ok": False, "reason": "gateway_unhealthy", "detail": type(exc).__name__}

    required = {
        "gateway_running": status.get("gateway_running") is True,
        "scheduler_running": status.get("scheduler_running") is True,
        "paper_internal": status.get("paper_execution_mode") == "paper_internal",
        "local_paper": status.get("execution_target") == "local_paper",
        "live_disabled": status.get("live_state") == "live_disabled",
        "health": health.get("status") == "healthy",
    }
    if expected_sha:
        required["boot_commit"] = status.get("boot_commit") == expected_sha
        required["current_commit"] = status.get("current_commit") == expected_sha
    if expected_version:
        required["version"] = status.get("version") == expected_version
    failed = [name for name, passed in required.items() if not passed]
    return {"ok": not failed, "reason": "runtime_gate_failed" if failed else None, "failed": failed}


def _evaluate_runtime_diagnostics(report: dict[str, Any], migrations: dict[str, Any]) -> dict[str, Any]:
    checks = {check.get("name"): check for check in report.get("checks", [])}
    failed = set(report.get("summary", {}).get("blockers", []))
    if checks.get("llm.inference", {}).get("severity") != "ok":
        failed.add("llm.inference")
    settlement = checks.get("content.signal_settlement", {})
    if settlement.get("severity") not in {"ok", "info"} or str(settlement.get("detail", "")).startswith("probe error"):
        failed.add("content.signal_settlement")
    if migrations.get("current") is not True:
        failed.add("schema.migrations")
    return {"ok": not failed, "detail": ",".join(sorted(failed)) or "ready"}


def _doctor_healthy() -> dict[str, Any]:
    """Run deployment-critical diagnostics in a fresh source-bound interpreter."""
    try:
        code = (
            "import json; "
            "from aletheia.diagnostics.doctor import build_report; "
            "from aletheia.storage.migrations import migration_status; "
            "from aletheia.update.auto import _evaluate_runtime_diagnostics; "
            "print(json.dumps(_evaluate_runtime_diagnostics(build_report().to_dict(), migration_status())))"
        )
        result = _run([sys.executable, "-c", code], cwd=_PROJECT_ROOT, env=_safe_env(), timeout=120)
        if result.returncode:
            return {"ok": False, "reason": "doctor_failed", "detail": (result.stderr or result.stdout)[-500:]}
        report = json.loads(result.stdout)
        return {"ok": bool(report.get("ok")), "detail": report.get("detail")}
    except Exception as exc:
        return {"ok": False, "reason": "doctor_failed", "detail": type(exc).__name__}


def _wait_for_runtime(expected_sha: str, expected_version: str) -> dict[str, Any]:
    deadline = time.monotonic() + 120
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = _runtime_gate(expected_sha=expected_sha, expected_version=expected_version)
        if last.get("ok"):
            doctor = _doctor_healthy()
            if doctor.get("ok"):
                return {"ok": True}
            last = {"ok": False, "reason": "doctor_unhealthy", "detail": doctor.get("detail")}
        time.sleep(2)
    return last or {"ok": False, "reason": "runtime_verification_timeout"}


def _observer_auto_preflight() -> dict[str, Any]:
    """Require complete structural proof only when a sibling Worker owns Observation."""
    from aletheia.observation_lab.execution_owner import get_execution_owner

    if get_execution_owner() != "worker":
        return {"required": False, "ok": True, "before": None, "preflight": None}

    from aletheia.update import continuity

    before = continuity.capture_update_continuity()
    preflight = continuity.evaluate_preflight(before)
    proof_gaps = list(preflight.get("proof_gaps") or [])
    worker = ((before.get("observation") or {}).get("worker") or {})
    if not worker.get("acquired_at"):
        proof_gaps.append("worker_pass_timestamp_unavailable")
    proof_gaps = sorted(set(proof_gaps))
    preflight = {**preflight, "proof_gaps": proof_gaps}
    return {
        "required": True,
        "ok": bool(preflight.get("ok")) and not proof_gaps,
        "before": before,
        "preflight": preflight,
    }


def _observer_auto_postflight(guard: dict[str, Any], target: str) -> dict[str, Any]:
    if not guard.get("required"):
        return {"required": False, "ok": True, "comparison": None, "after": None}

    from aletheia.update import continuity

    before = guard.get("before") or {}
    after = continuity.capture_update_continuity_until_settled(
        target,
        timeout_sec=10,
        poll_sec=1,
    )
    comparison = continuity.compare_update_continuity(
        before,
        after,
        expected_target=target,
        restart=True,
    )
    return {
        "required": True,
        "ok": bool(comparison.get("ok")),
        "comparison": comparison,
        "after": after,
    }


def _observer_rollback_safe(guard: dict[str, Any]) -> bool:
    """Prove the Worker's persisted pass signature still matches preflight."""
    if not guard.get("required"):
        return True
    before_worker = (((guard.get("before") or {}).get("observation") or {}).get("worker") or {})
    before_acquired = before_worker.get("acquired_at")
    if not before_acquired:
        return False
    try:
        from aletheia.update import continuity

        current = continuity.capture_update_continuity()
    except Exception:
        return False
    current_worker = (((current.get("observation") or {}).get("worker") or {}))
    return bool(
        current_worker.get("available")
        and current_worker.get("acquired_at") == before_acquired
    )


@contextmanager
def _observer_rollback_guard(guard: dict[str, Any]) -> Iterator[bool]:
    """Hold Worker exclusion for the complete exceptional Main rollback window.

    Enter failures are converted to ``False`` before yielding. Exceptions raised
    by the rollback body itself are never caught/reinterpreted as lock contention;
    they propagate normally after the exclusion lock is released.
    """
    if not guard.get("required"):
        yield True
        return

    from aletheia.observation_lab.worker_lock import worker_cycle_exclusion

    exclusion = worker_cycle_exclusion()
    try:
        exclusion.__enter__()
    except Exception:
        yield False
        return

    try:
        try:
            safe = _observer_rollback_safe(guard)
        except Exception:
            safe = False
        yield safe
    finally:
        exclusion.__exit__(None, None, None)


def _observer_hold_after_activation(
    state: dict[str, Any],
    *,
    tag: str,
    target: str,
    reason: str,
    detail: str,
    continuity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary = None
    if continuity:
        comparison = continuity.get("comparison") or {}
        summary = {
            "failures": comparison.get("failures") or [],
            "proof_gaps": comparison.get("proof_gaps") or [],
            "warnings": comparison.get("warnings") or [],
        }
    held = _write_state(
        state,
        phase="held",
        status="held",
        hold=True,
        target_tag=tag,
        target_sha=target,
        reason=reason,
        detail=detail,
        observer_continuity=summary,
    )
    _record_audit(
        "auto_update_observer_hold",
        tag,
        detail or reason,
        reason=reason,
        continuity=continuity,
    )
    return {
        "ok": False,
        "reason": reason,
        "state": held,
        "continuity": continuity,
    }


def _run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=str(cwd), capture_output=True, text=True, env=env, timeout=timeout)


def _uv() -> str:
    value = shutil.which("uv")
    if not value:
        raise AutoUpdateError("uv_unavailable")
    return value


def _candidate_preflight(target: str, backup: Path) -> Path:
    """Restore into a temporary root and migrate it using the pinned candidate."""
    worktree = Path(tempfile.mkdtemp(prefix="aletheia-auto-", dir=_paths()["root"]))
    added = git_ops._git("worktree", "add", "--detach", str(worktree), target)  # noqa: SLF001
    if added.returncode:
        shutil.rmtree(worktree, ignore_errors=True)
        raise AutoUpdateError("candidate_worktree_failed", added.stderr[-500:])
    data_copy = worktree / ".auto-preflight-data"
    try:
        restored = RestoreService().restore(backup, data_copy)
        if not restored.success:
            raise AutoUpdateError("preflight_restore_failed", "; ".join(restored.errors))
        env = _safe_env()
        env["ALETHEIA_DATA_DIR"] = str(data_copy)
        check = (
            "from aletheia.storage.migrations import apply_migrations, migration_status; "
            "status=migration_status(); "
            "apply_migrations() if not status['current'] else None; "
            "from aletheia.update.packaged import validate_current_data; "
            "assert validate_current_data()['ok']; "
            "from aletheia.update.smoke import run_smoke_check; "
            "assert run_smoke_check()['ok']"
        )
        result = _run([_uv(), "run", "--directory", str(worktree), "python", "-c", check], cwd=worktree, env=env)
        if result.returncode:
            raise AutoUpdateError("candidate_preflight_failed", (result.stderr or result.stdout)[-1000:])
        return worktree
    except Exception:
        git_ops._git("worktree", "remove", "--force", str(worktree))  # noqa: SLF001
        shutil.rmtree(worktree, ignore_errors=True)
        raise


def _remove_candidate(worktree: Path | None) -> None:
    if worktree is None:
        return
    git_ops._git("worktree", "remove", "--force", str(worktree))  # noqa: SLF001
    shutil.rmtree(worktree, ignore_errors=True)


def _sync_candidate_dependencies(worktree: Path) -> None:
    result = _run([_uv(), "pip", "install", "-e", str(worktree)], cwd=_PROJECT_ROOT, env=_safe_env())
    if result.returncode:
        raise AutoUpdateError("candidate_dependency_install_failed", (result.stderr or result.stdout)[-1000:])


def _build_candidate_frontend(worktree: Path, *, old: str, target: str) -> None:
    frontend = worktree / "frontend"
    env = _safe_env()
    env["VITE_BUILD_OUT_DIR"] = str(_STAGED_DASHBOARD_STATIC)
    npm = shutil.which("npm")
    if not npm:
        raise AutoUpdateError("npm_unavailable")
    install = _run([npm, "ci"], cwd=frontend, env=env)
    if install.returncode:
        raise AutoUpdateError("candidate_frontend_dependency_install_failed", (install.stderr or install.stdout)[-1000:])
    shutil.rmtree(_STAGED_DASHBOARD_STATIC, ignore_errors=True)
    build = _run([npm, "run", "build"], cwd=frontend, env=env)
    if build.returncode or not (_STAGED_DASHBOARD_STATIC / "index.html").is_file():
        shutil.rmtree(_STAGED_DASHBOARD_STATIC, ignore_errors=True)
        raise AutoUpdateError("candidate_frontend_build_failed", (build.stderr or build.stdout)[-1000:])


def _gateway_command(action: str) -> dict[str, Any]:
    code = (
        "from aletheia.gateway.lifecycle import do_start, do_stop; "
        "result = do_stop() if 'stop' == '" + action + "' else do_start(no_browser=True); "
        "raise SystemExit(0 if result is not False else 2)"
    )
    try:
        result = _run([sys.executable, "-c", code], cwd=_PROJECT_ROOT, env=_safe_env(), timeout=120)
    except Exception as exc:
        return {"ok": False, "reason": type(exc).__name__}
    return {"ok": result.returncode == 0, "reason": "gateway_" + action + "_failed" if result.returncode else None,
            "detail": (result.stderr or result.stdout)[-1000:]}


def _install_source_dependencies() -> bool:
    try:
        result = _run([_uv(), "pip", "install", "-e", "."], cwd=_PROJECT_ROOT, env=_safe_env())
        return result.returncode == 0
    except Exception:
        return False


def _restore_old_runtime(old: str, backup: Path, had_previous_static: bool, *, restore_static: bool) -> dict[str, Any]:
    try:
        reset = git_ops.reset_hard(old)
        if not reset.get("ok"):
            return {"ok": False, "reason": "source_restore_failed"}
        if not _install_source_dependencies():
            return {"ok": False, "reason": "dependency_restore_failed"}
        if restore_static:
            _restore_frontend_build(had_previous_static)
        restored = RestoreService().restore(backup, get_data_dir())
        if not restored.success:
            return {"ok": False, "reason": "data_restore_failed", "detail": "; ".join(restored.errors)}
        started = _gateway_command("start")
        if not started.get("ok"):
            return {"ok": False, "reason": started.get("reason", "old_gateway_restore_failed")}
        verified = _wait_for_runtime(old, _version_from_ref(old))
        return {"ok": bool(verified.get("ok")), "reason": verified.get("reason", "old_gateway_restore_failed")}
    except Exception as exc:
        return {"ok": False, "reason": f"restore_exception_{type(exc).__name__}"}


def _rollback_to_old_runtime(
    old: str, backup: Path, had_previous_static: bool, *, restore_static: bool
) -> dict[str, Any]:
    """Quiesce the candidate before restoring files it may still have open."""
    stopped = _gateway_command("stop")
    if not stopped.get("ok"):
        return {"ok": False, "reason": stopped.get("reason", "candidate_gateway_stop_failed")}
    return _restore_old_runtime(old, backup, had_previous_static, restore_static=restore_static)


def _validate_candidate(tag: str, target: str, current_version: tuple[int, int, int]) -> str:
    tag_version = _semver(tag)
    if tag_version is None or tag_version <= current_version:
        raise AutoUpdateError("tag_not_newer")
    if not git_ops.is_ancestor(target, "origin/master"):
        raise AutoUpdateError("tag_not_on_master")
    version = _version_from_ref(target)
    if version != tag[1:]:
        raise AutoUpdateError("tag_version_mismatch", f"tag={tag}; code={version or 'missing'}")
    return version


def _failed(
    state: dict[str, Any],
    *,
    tag: str | None,
    sha: str | None,
    reason: str,
    detail: str = "",
    hold: bool = False,
    blacklist: bool = False,
) -> dict[str, Any]:
    failed_tags = dict(state.get("failed_tags") or {})
    if blacklist and tag and sha:
        failed_tags[tag] = {"sha": sha, "reason": reason, "failed_at": _now()}
    updated = _write_state(state, phase="held" if hold else "failed", status="held" if hold else "failed",
                           target_tag=tag, target_sha=sha, reason=reason, detail=detail, hold=hold,
                           failed_tags=failed_tags)
    _record_audit("auto_update_held" if hold else "auto_update_failed", tag or sha or "unknown", reason)
    return {"ok": False, "reason": reason, "state": updated}


def run() -> dict[str, Any]:
    """Check once and, only for a new stable release tag, deploy that pinned SHA."""
    if sys.platform != "linux":
        return {"ok": False, "reason": "linux_source_checkout_required"}
    if not git_ops.is_repo():
        return {"ok": False, "reason": "source_checkout_required"}
    if os.environ.get(_RUNTIME_ROLE_ENV, "").strip().lower() != _AUTO_DEPLOY_ROLE:
        return {"ok": True, "reason": "qualification_runtime_pinned", "detail": _ROLE_MIGRATION_DETAIL}

    with _deployment_lock() as acquired:
        if not acquired:
            return {"ok": True, "reason": "already_running"}
        try:
            state = _read_state()
        except AutoUpdateError as exc:
            return {"ok": False, "reason": exc.reason, "detail": exc.detail}
        if state.get("hold"):
            return {"ok": False, "reason": "global_hold", "state": state}
        state = _write_state(state, phase="checking", status="running", reason=None, detail=None, hold=False)

        fetched = git_ops.fetch_release_tags()
        if not fetched.get("ok"):
            return _failed(state, tag=None, sha=None, reason=fetched.get("reason", "fetch_failed"), detail=fetched.get("stderr", ""))
        if git_ops.is_worktree_dirty() or git_ops.current_branch() != "master":
            return _failed(state, tag=None, sha=None, reason="source_not_clean_master")
        head = git_ops.current_commit()
        if not head or not git_ops.is_ancestor(head, "origin/master"):
            return _failed(state, tag=None, sha=None, reason="source_not_fast_forward")

        remote_tags = git_ops.remote_tag_names()
        if remote_tags is None:
            return _failed(state, tag=None, sha=None, reason="remote_tag_discovery_failed")
        tags = _stable_tags(remote_tags)
        observed = dict(state.get("observed_tags") or {})
        moved = [tag for tag, info in tags.items() if tag in observed and observed[tag] != info["sha"]]
        if moved:
            return _failed(state, tag=moved[0], sha=tags[moved[0]]["sha"], reason="tag_moved", blacklist=True)
        observed.update({tag: info["sha"] for tag, info in tags.items()})
        state = _write_state(state, observed_tags=observed)

        current = _semver(_version_from_ref("HEAD"))
        if current is None:
            return _failed(state, tag=None, sha=head, reason="current_version_invalid")
        candidates = [(info["version"], tag, info["sha"]) for tag, info in tags.items() if info["version"] > current]
        if not candidates:
            done = _write_state(state, phase="idle", status="up_to_date", target_tag=None, target_sha=None, reason=None, detail=None)
            return {"ok": True, "reason": "up_to_date", "state": done}
        _, tag, target = max(candidates)
        failed_tag = (state.get("failed_tags") or {}).get(tag)
        if isinstance(failed_tag, dict) and failed_tag.get("sha") == target:
            done = _write_state(state, phase="idle", status="skipped", target_tag=tag, target_sha=target,
                                reason="failed_tag_skipped", detail=None)
            return {"ok": True, "reason": "failed_tag_skipped", "state": done}

        try:
            version = _validate_candidate(tag, target, current)
        except AutoUpdateError as exc:
            return _failed(state, tag=tag, sha=target, reason=exc.reason, detail=exc.detail, blacklist=True)
        baseline = _runtime_gate()
        if baseline.get("ok"):
            baseline = _doctor_healthy()
        if not baseline.get("ok"):
            detail = ",".join(baseline.get("failed", [])) or str(baseline.get("detail") or baseline.get("reason") or "")
            return _failed(state, tag=tag, sha=target, reason="baseline_unhealthy", detail=detail)

        observer_guard = _observer_auto_preflight()
        if observer_guard.get("required") and not observer_guard.get("ok"):
            preflight = observer_guard.get("preflight") or {}
            failures = preflight.get("failures") or []
            proof_gaps = preflight.get("proof_gaps") or []
            reason = "observer_preflight_failed" if failures else "observer_preflight_unproven"
            detail = ",".join(str(item) for item in (failures or proof_gaps))
            return _failed(state, tag=tag, sha=target, reason=reason, detail=detail)

        worktree: Path | None = None
        preflight_backup: Path | None = None
        final_backup: Path | None = None
        had_previous_static = False
        dependencies_synced = False
        stopped = False
        source_switched = False
        static_activated = False
        observer_postflight: dict[str, Any] | None = None
        try:
            state = _write_state(state, phase="preflight", status="running", target_tag=tag, target_sha=target)
            preflight_backup = BackupService(get_data_dir()).create_backup(label=f"auto_preflight_{tag[1:]}")
            worktree = _candidate_preflight(target, preflight_backup)
            _sync_candidate_dependencies(worktree)
            dependencies_synced = True
            _build_candidate_frontend(worktree, old=head, target=target)
            _remove_candidate(worktree)
            worktree = None

            state = _write_state(state, phase="quiescing", status="running")
            stopped_result = _gateway_command("stop")
            if not stopped_result.get("ok"):
                raise AutoUpdateError(stopped_result.get("reason", "gateway_stop_failed"), stopped_result.get("detail", ""))
            stopped = True
            final_backup = BackupService(get_data_dir()).create_backup(label=f"auto_activate_{tag[1:]}")

            state = _write_state(state, phase="activating", status="running")
            merged = git_ops.merge_fast_forward(target)
            if not merged.get("ok"):
                raise AutoUpdateError(merged.get("reason", "ff_only_rejected"), merged.get("stderr", ""))
            source_switched = True
            if not _install_source_dependencies():
                raise AutoUpdateError("target_dependency_bind_failed")
            had_previous_static = _activate_frontend_build()
            static_activated = True
            started = _gateway_command("start")
            if not started.get("ok"):
                raise AutoUpdateError(started.get("reason", "gateway_start_failed"), started.get("detail", ""))
            verified = _wait_for_runtime(target, version)
            if not verified.get("ok"):
                raise AutoUpdateError(verified.get("reason", "runtime_verification_failed"), str(verified.get("detail", "")))

            observer_postflight = _observer_auto_postflight(observer_guard, target)
            if observer_postflight.get("required") and not observer_postflight.get("ok"):
                comparison = observer_postflight.get("comparison") or {}
                failures = comparison.get("failures") or []
                proof_gaps = comparison.get("proof_gaps") or []
                reason = "observer_continuity_failed" if failures else "observer_continuity_unproven"
                detail = ",".join(str(item) for item in (failures or proof_gaps))
                return _observer_hold_after_activation(
                    state,
                    tag=tag,
                    target=target,
                    reason=reason,
                    detail=detail,
                    continuity={
                        "preflight": observer_guard.get("preflight"),
                        "before": observer_guard.get("before"),
                        "after": observer_postflight.get("after"),
                        "comparison": comparison,
                    },
                )
        except AutoUpdateError as exc:
            _remove_candidate(worktree)
            if not source_switched:
                shutil.rmtree(_STAGED_DASHBOARD_STATIC, ignore_errors=True)
                if dependencies_synced:
                    _install_source_dependencies()
                recovery = _gateway_command("start") if stopped else {"ok": True}
                return _failed(state, tag=tag, sha=target, reason=exc.reason, detail=exc.detail,
                               hold=not recovery.get("ok"), blacklist=exc.reason not in _TRANSIENT_CANDIDATE_FAILURES)
            with _observer_rollback_guard(observer_guard) as rollback_safe:
                if not rollback_safe:
                    return _observer_hold_after_activation(
                        state,
                        tag=tag,
                        target=target,
                        reason="observer_advanced_during_failed_activation",
                        detail=(
                            "Target activation failed, but Worker progress changed or could not be "
                            "proven unchanged; refusing to restore an older app.db beneath newer Lab state."
                        ),
                    )
                state = _write_state(state, phase="rolling_back", status="running", reason=exc.reason, detail=exc.detail)
                recovery = _rollback_to_old_runtime(head, final_backup or preflight_backup, had_previous_static,
                                                    restore_static=static_activated)
                return _failed(state, tag=tag, sha=target, reason=exc.reason, detail=exc.detail,
                               hold=not recovery.get("ok"), blacklist=True)
        except Exception as exc:
            _remove_candidate(worktree)
            reason = f"unexpected_{type(exc).__name__}"
            if not source_switched:
                shutil.rmtree(_STAGED_DASHBOARD_STATIC, ignore_errors=True)
                if dependencies_synced:
                    _install_source_dependencies()
                recovery = _gateway_command("start") if stopped else {"ok": True}
                return _failed(state, tag=tag, sha=target, reason=reason, hold=not recovery.get("ok"))
            with _observer_rollback_guard(observer_guard) as rollback_safe:
                if not rollback_safe:
                    return _observer_hold_after_activation(
                        state,
                        tag=tag,
                        target=target,
                        reason="observer_advanced_during_failed_activation",
                        detail=(
                            f"Unexpected target activation failure ({type(exc).__name__}), and Worker "
                            "progress changed or could not be proven unchanged; refusing cross-time rollback."
                        ),
                    )
                state = _write_state(state, phase="rolling_back", status="running", reason=reason)
                recovery = _rollback_to_old_runtime(head, final_backup or preflight_backup, had_previous_static,
                                                    restore_static=static_activated)
                return _failed(state, tag=tag, sha=target, reason=reason, hold=not recovery.get("ok"), blacklist=True)

        shutil.rmtree(_PREVIOUS_DASHBOARD_STATIC, ignore_errors=True)
        observer_summary = None
        if observer_postflight and observer_postflight.get("required"):
            comparison = observer_postflight.get("comparison") or {}
            observer_summary = {
                "verified": True,
                "owner": comparison.get("owner"),
                "worker_sha": comparison.get("worker_sha_after"),
                "data_root_unchanged": comparison.get("data_root_unchanged"),
                "main_health_status": comparison.get("main_health_status"),
                "observation_execution_exclusive": comparison.get("observation_execution_exclusive"),
            }
        success = _write_state(state, phase="complete", status="deployed", target_tag=tag, target_sha=target,
                               reason=None, detail=None, observer_continuity=observer_summary,
                               last_successful={"tag": tag, "sha": target, "at": _now()})
        _record_audit(
            "auto_update_deployed",
            tag,
            f"Deployed {tag} at {target}",
            observer_continuity=observer_summary,
        )
        return {"ok": True, "reason": "deployed", "state": success}


def status() -> dict[str, Any]:
    try:
        return _read_state() or {"schema_version": _STATE_SCHEMA, "phase": "idle", "status": "not_installed"}
    except AutoUpdateError as exc:
        return {"schema_version": _STATE_SCHEMA, "phase": "held", "status": "corrupt", "hold": True,
                "reason": exc.reason, "detail": exc.detail}


def resume() -> dict[str, Any]:
    if sys.platform != "linux" or not git_ops.is_repo():
        return {"ok": False, "reason": "linux_source_checkout_required"}
    with _deployment_lock() as acquired:
        if not acquired:
            return {"ok": False, "reason": "already_running"}
        try:
            state = _read_state()
        except AutoUpdateError as exc:
            return {"ok": False, "reason": exc.reason, "detail": exc.detail}
        updated = _write_state(state, hold=False, phase="idle", status="resumed", reason=None, detail=None)
        _record_audit(
            "auto_update_resumed", "auto", "Automatic release-tag deployment hold cleared by operator",
            actor="operator", source="cli",
        )
        return {"ok": True, "state": updated}


def _unit_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def _units(*, data_dir: str | None = None) -> dict[str, str]:
    uv = _uv()
    if data_dir is None:
        data_dir = os.environ.get("ALETHEIA_DATA_DIR", "").strip()
    runtime_role = os.environ.get(_RUNTIME_ROLE_ENV, "").strip()
    env = "".join(
        f"Environment={name}={value}\n"
        for name, value in (("ALETHEIA_DATA_DIR", data_dir), (_RUNTIME_ROLE_ENV, runtime_role))
        if value
    )
    service = (
        "[Unit]\nDescription=Aletheia release-tag Paper deployment\n" + _SYSTEMD_MARKER + "\n\n"
        "[Service]\nType=oneshot\n"
        f"WorkingDirectory={_PROJECT_ROOT}\n{env}ExecStart={uv} run aletheia update auto run\n"
    )
    timer = (
        "[Unit]\nDescription=Check Aletheia release tags every five minutes\n" + _SYSTEMD_MARKER + "\n\n"
        "[Timer]\nOnBootSec=1min\nOnUnitInactiveSec=5min\nPersistent=true\nRandomizedDelaySec=30s\n"
        "Unit=aletheia-auto-update.service\n\n[Install]\nWantedBy=timers.target\n"
    )
    return {"aletheia-auto-update.service": service, "aletheia-auto-update.timer": timer}


def _owned_unit_data_dir(path: Path) -> str:
    """Read the generated data root so a role-only migration cannot drop it."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    prefix = "Environment=ALETHEIA_DATA_DIR="
    for line in text.splitlines():
        if line.startswith(prefix):
            return line.removeprefix(prefix).strip()
    return ""


def _systemctl(*args: str) -> None:
    result = subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)
    if result.returncode:
        raise AutoUpdateError("systemd_user_operation_failed", (result.stderr or result.stdout)[-500:])


def install() -> dict[str, Any]:
    if sys.platform != "linux" or not git_ops.is_repo():
        return {"ok": False, "reason": "linux_source_checkout_required"}
    if os.environ.get(_RUNTIME_ROLE_ENV, "").strip().lower() != _AUTO_DEPLOY_ROLE:
        return {"ok": False, "reason": "paper_runtime_role_required"}
    directory = _unit_dir()
    directory.mkdir(parents=True, exist_ok=True)
    try:
        for name in _UNIT_NAMES:
            path = directory / name
            if path.exists() and _SYSTEMD_MARKER not in path.read_text(encoding="utf-8", errors="replace"):
                return {"ok": False, "reason": "systemd_unit_ownership_conflict", "detail": name}
        data_dir = os.environ.get("ALETHEIA_DATA_DIR", "").strip()
        if not data_dir:
            data_dir = _owned_unit_data_dir(directory / "aletheia-auto-update.service")
        units = _units(data_dir=data_dir or None)
    except AutoUpdateError as exc:
        return {"ok": False, "reason": exc.reason, "detail": exc.detail}
    for name, content in units.items():
        path = directory / name
        path.write_text(content, encoding="utf-8")
    try:
        _systemctl("daemon-reload")
        _systemctl("enable", "--now", "aletheia-auto-update.timer")
    except AutoUpdateError as exc:
        return {"ok": False, "reason": exc.reason, "detail": exc.detail}
    return {"ok": True, "reason": "installed"}


def uninstall() -> dict[str, Any]:
    if sys.platform != "linux" or not git_ops.is_repo():
        return {"ok": False, "reason": "linux_source_checkout_required"}
    directory = _unit_dir()
    owned = []
    for name in _UNIT_NAMES:
        path = directory / name
        if path.exists() and _SYSTEMD_MARKER in path.read_text(encoding="utf-8", errors="replace"):
            owned.append(path)
    if not owned:
        return {"ok": True, "reason": "not_installed"}
    try:
        _systemctl("disable", "--now", "aletheia-auto-update.timer")
    except AutoUpdateError as exc:
        return {"ok": False, "reason": exc.reason, "detail": exc.detail}
    for path in owned:
        path.unlink()
    try:
        _systemctl("daemon-reload")
    except AutoUpdateError as exc:
        return {"ok": False, "reason": exc.reason, "detail": exc.detail}
    return {"ok": True, "reason": "uninstalled"}
