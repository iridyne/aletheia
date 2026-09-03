"""Self-upgrade orchestration for development and installed runtimes.

A development checkout keeps the existing explicit ``git pull`` workflow. A frozen,
installed payload must never modify source or invoke ``uv``; it stages a signed immutable
runtime in the background and hands the short activation/rollback transaction to the
stable tray host.
"""

import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone

from aletheia.config.loader import _PROJECT_ROOT, get_data_dir, load_config
from aletheia.files.jsonl import write_audit
from aletheia.gateway.pidfile import _no_window_kwargs
from aletheia.update import git_ops, target_fresh
from aletheia.update.process_env import safe_env

logger = logging.getLogger("aletheia.update.applier")

_DEPENDENCY_SYNC_TIMEOUT_SEC = 300
_NPM_COMMAND_TIMEOUT_SEC = 300


def _packaged_module():
    from aletheia.update import packaged_commands

    return packaged_commands


def _is_packaged() -> bool:
    return _packaged_module().is_packaged_mode()


def _safe_env() -> dict:
    return safe_env()


def update_progress() -> dict:
    if _is_packaged():
        return _packaged_module().read_update_state()
    cfg = load_config()
    return {
        "schema_version": 1,
        "mode": "development",
        "phase": "idle",
        "status": "idle",
        "current_commit": cfg.update.current_commit,
        "last_known_remote_commit": cfg.update.last_known_remote_commit,
        "last_check_time": cfg.update.last_check_time,
    }


def check_for_update() -> dict:
    """Fetch and report whether an update is available. Read-only except metadata."""
    if _is_packaged():
        return _packaged_module().check_for_update()

    cfg = load_config()
    if not cfg.update.enabled:
        return {"ok": False, "reason": "update_disabled", "mode": "development"}
    if not git_ops.git_available():
        return {"ok": False, "reason": "git_unavailable", "mode": "development"}

    branch = cfg.update.branch
    fetched = git_ops.fetch(branch)
    if not fetched.get("ok"):
        return {
            "ok": False,
            "reason": fetched.get("reason", "fetch_failed"),
            "mode": "development",
        }

    cur = git_ops.current_commit()
    remote = git_ops.remote_commit(branch)
    behind = git_ops.behind_count(branch)
    if not cur or not remote or not isinstance(behind, int) or behind < 0:
        return {
            "ok": False,
            "reason": "remote_state_unavailable",
            "mode": "development",
        }
    checked_at = datetime.now(timezone.utc).isoformat()

    # Never whole-save the snapshot loaded before network IO. Another process may
    # have changed unrelated settings while fetch was in flight. Targeted mutation
    # reloads the latest durable config under the cross-process writer permit.
    from aletheia.config.loader import mutate_config

    def _record_check(latest) -> bool:
        changed = (
            latest.update.last_check_time != checked_at
            or latest.update.current_commit != cur
            or latest.update.last_known_remote_commit != remote
        )
        latest.update.last_check_time = checked_at
        latest.update.current_commit = cur
        latest.update.last_known_remote_commit = remote
        return changed

    mutate_config(_record_check)
    write_audit(
        {
            "actor": "human",
            "source": "api",
            "event_type": "update_checked",
            "entity_type": "system_update",
            "entity_id": cur,
            "message": f"Update check on {branch}: behind={behind}",
        }
    )
    return {
        "ok": True,
        "mode": "development",
        "has_update": behind > 0,
        "behind_count": behind,
        "current_commit": cur,
        "remote_commit": remote,
        "branch": branch,
    }


def _is_managed_venv() -> bool:
    """True when running inside the project-managed venv (not a foreign Python)."""
    venv = _PROJECT_ROOT / ".venv"
    try:
        return Path(sys.prefix).resolve() == venv.resolve()
    except Exception:
        return False


def _reinstall_deps() -> dict:
    # #1206: enforce uv.lock — only uv sync --locked is authoritative; no pip fallback
    env = _safe_env()
    try:
        result = subprocess.run(
            ["uv", "sync", "--locked"],
            cwd=str(_PROJECT_ROOT),
            capture_output=True,
            text=True,
            env=env,
            timeout=_DEPENDENCY_SYNC_TIMEOUT_SEC,
            **_no_window_kwargs(),
        )
        if result.returncode == 0:
            return {"ok": True, "tool": "uv"}
    except FileNotFoundError:
        return {"ok": False, "reason": "update_dependency_sync_failed", "detail": "uv unavailable"}
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "reason": "update_dependency_sync_timeout",
            "detail": f"uv sync exceeded {_DEPENDENCY_SYNC_TIMEOUT_SEC}s",
        }
    return {
        "ok": False,
        "reason": "update_dependency_sync_failed",
        "detail": (result.stderr or result.stdout or "uv sync failed").strip()[-500:],
    }


_FRONTEND_DIR = _PROJECT_ROOT / "frontend"
_DASHBOARD_STATIC = _PROJECT_ROOT / "aletheia" / "dashboard_static"
_STAGED_DASHBOARD_STATIC = _PROJECT_ROOT / "aletheia" / ".dashboard_static-next"
_PREVIOUS_DASHBOARD_STATIC = _PROJECT_ROOT / "aletheia" / ".dashboard_static-previous"
_DASHBOARD_FRONTEND_TREE = ".aletheia-frontend-tree"


def _frontend_deps_changed(changed_files: list[str]) -> bool:
    return any(path in {"frontend/package.json", "frontend/package-lock.json"} for path in changed_files)


def _dashboard_build_required(frontend_tree: str) -> bool:
    marker = _DASHBOARD_STATIC / _DASHBOARD_FRONTEND_TREE
    try:
        if not frontend_tree or marker.read_text().strip() != frontend_tree:
            return True
    except OSError:
        return True
    if not (
        (_DASHBOARD_STATIC / ".vite" / "manifest.json").is_file()
        and (_DASHBOARD_STATIC / "performance.json").is_file()
        and (_DASHBOARD_STATIC / "assets").is_dir()
        and any((_DASHBOARD_STATIC / "assets").iterdir())
    ):
        return True
    from aletheia.gateway.preflight import dashboard_artifact_errors

    return bool(dashboard_artifact_errors(_DASHBOARD_STATIC))


def _run_npm(args: list[str], *, env: dict) -> subprocess.CompletedProcess | None:
    commands = ("npm.cmd", "npm") if os.name == "nt" else ("npm",)
    for command in commands:
        try:
            return subprocess.run(
                [command, *args],
                cwd=str(_FRONTEND_DIR),
                capture_output=True,
                text=True,
                env=env,
                timeout=_NPM_COMMAND_TIMEOUT_SEC,
                **_no_window_kwargs(),
            )
        except FileNotFoundError:
            continue
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(
                [command, *args],
                124,
                "",
                f"npm {' '.join(args)} exceeded {_NPM_COMMAND_TIMEOUT_SEC}s",
            )
        except OSError as exc:
            return subprocess.CompletedProcess([command, *args], 127, "", str(exc))
    return None


def _install_frontend_deps() -> dict:
    result = _run_npm(["ci"], env=_safe_env())
    if result is None:
        return {"ok": False, "reason": "npm_unavailable"}
    return {
        "ok": result.returncode == 0,
        "reason": "npm_ci_failed" if result.returncode else None,
        "detail": (getattr(result, "stderr", "") or getattr(result, "stdout", "")).strip()[-500:],
    }


def _build_frontend(frontend_tree: str) -> dict:
    """Build staging output, then validate it with target-loaded Python code."""
    shutil.rmtree(_STAGED_DASHBOARD_STATIC, ignore_errors=True)
    env = _safe_env()
    env["VITE_BUILD_OUT_DIR"] = str(_STAGED_DASHBOARD_STATIC)
    result = _run_npm(["run", "build"], env=env)
    if result is None:
        return {"ok": False, "reason": "npm_unavailable"}
    if result.returncode or not (_STAGED_DASHBOARD_STATIC / "index.html").is_file():
        shutil.rmtree(_STAGED_DASHBOARD_STATIC, ignore_errors=True)
        return {
            "ok": False,
            "reason": "frontend_build_failed",
            "detail": (getattr(result, "stderr", "") or getattr(result, "stdout", "")).strip()[-500:],
        }

    validated = target_fresh.validate_dashboard_artifacts(
        _STAGED_DASHBOARD_STATIC,
        project_root=_PROJECT_ROOT,
        env=_safe_env(),
        python_executable=sys.executable,
    )
    if not validated.get("ok"):
        shutil.rmtree(_STAGED_DASHBOARD_STATIC, ignore_errors=True)
        if validated.get("errors"):
            return {
                "ok": False,
                "reason": "frontend_artifact_incomplete",
                "detail": validated.get("errors"),
            }
        return {
            "ok": False,
            "reason": "frontend_artifact_validation_failed",
            "detail": validated.get("detail") or validated.get("reason"),
        }
    try:
        (_STAGED_DASHBOARD_STATIC / _DASHBOARD_FRONTEND_TREE).write_text(frontend_tree + "\n")
    except OSError as exc:
        shutil.rmtree(_STAGED_DASHBOARD_STATIC, ignore_errors=True)
        return {
            "ok": False,
            "reason": "frontend_artifact_fingerprint_failed",
            "detail": str(exc),
        }
    return {"ok": True}


def _activate_frontend_build() -> bool:
    shutil.rmtree(_PREVIOUS_DASHBOARD_STATIC, ignore_errors=True)
    if _DASHBOARD_STATIC.exists():
        _DASHBOARD_STATIC.replace(_PREVIOUS_DASHBOARD_STATIC)
    _STAGED_DASHBOARD_STATIC.replace(_DASHBOARD_STATIC)
    return _PREVIOUS_DASHBOARD_STATIC.exists()


def _restore_frontend_build(had_previous: bool) -> None:
    shutil.rmtree(_DASHBOARD_STATIC, ignore_errors=True)
    if had_previous and _PREVIOUS_DASHBOARD_STATIC.exists():
        _PREVIOUS_DASHBOARD_STATIC.replace(_DASHBOARD_STATIC)
    shutil.rmtree(_STAGED_DASHBOARD_STATIC, ignore_errors=True)


def _restart_gateway() -> dict:
    """Restart from a fresh interpreter so pulled migrations are visible."""
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from aletheia.gateway.lifecycle import do_start, do_stop; "
                "do_stop(); raise SystemExit(0 if do_start(no_browser=True) is not False else 2)",
            ],
            cwd=str(_PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=90,
            **_no_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "reason": "gateway_restart_timeout",
            "detail": "gateway restart exceeded 90s",
        }
    except Exception as exc:
        return {"ok": False, "reason": type(exc).__name__}
    if result.returncode not in {0, 2}:
        # exit 1 (or other non-zero): genuine failure — preflight blocked, port conflict,
        # spawn died. exit 2 means do_start returned False (slow start: alive but readiness
        # not confirmed within the 15s deadline) — gateway is up, not a rollback condition.
        if _gateway_ready_after_restart_error():
            return {"ok": True, "restart_warning": "service_start_result_recovered"}
        return {
            "ok": False,
            "reason": "gateway_restart_failed",
            "detail": (result.stderr or result.stdout).strip()[-500:],
        }
    return {"ok": True}


def _gateway_ready_after_restart_error(*, timeout_sec: float = 5.0) -> bool:
    """Accept a canceled systemd start only after the front door recovers."""
    try:
        from aletheia.gateway.network import local_frontdoor_host

        deadline = time.monotonic() + timeout_sec
        cfg = load_config()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        url = f"http://{local_frontdoor_host()}:{cfg.app.dashboard_port}/api/ready"
    except Exception:
        return False
    while (remaining := deadline - time.monotonic()) > 0:
        try:
            with opener.open(url, timeout=min(1.0, remaining)) as response:
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
    return False


def run_smoke_check() -> dict:
    """Load and execute the target checkout's smoke policy in a fresh process."""
    return target_fresh.run_smoke_check(
        project_root=_PROJECT_ROOT,
        env=_safe_env(),
        python_executable=sys.executable,
    )


def _persist_current_commit_fresh(commit: str) -> dict:
    """Persist commit metadata without serializing a pre-pull config snapshot."""
    return target_fresh.persist_current_commit(
        commit,
        project_root=_PROJECT_ROOT,
        data_dir=get_data_dir(),
        env=_safe_env(),
        python_executable=sys.executable,
    )


def _metadata_fields(result: dict | None) -> dict:
    result = result or {}
    ok = result.get("ok") is True
    return {
        "metadata_persisted": ok,
        "metadata_error": None if ok else (result.get("detail") or result.get("reason") or "unknown"),
    }


def _record_update_failure(
    old: str,
    reason: str,
    *,
    attempted: str | None = None,
    detail: str = "",
    actual_head: str | None = None,
) -> None:
    """Persist one failure event before returning an unapplied update result."""
    after = {"commit": attempted or ""}
    if actual_head is not None:
        after["actual_head"] = actual_head
    detail = str(detail)[-1000:]
    write_audit(
        {
            "actor": "human",
            "source": "api",
            "event_type": "update_failed",
            "entity_type": "system_update",
            "entity_id": attempted or old or "unknown",
            "before": {"commit": old},
            "after": after,
            "message": f"Update failed: {reason}" + (f": {detail}" if detail else ""),
        }
    )


def _record_metadata_failure(commit: str, result: dict | None) -> None:
    fields = _metadata_fields(result)
    if fields["metadata_persisted"]:
        return
    write_audit(
        {
            "actor": "system",
            "source": "updater",
            "event_type": "update_metadata_persist_failed",
            "entity_type": "system_update",
            "entity_id": commit or "unknown",
            "message": (
                "Source state changed successfully, but target-loaded update metadata "
                f"could not be persisted: {fields['metadata_error']}"
            ),
        }
    )


def _rollback_update(
    old: str,
    new: str,
    python_deps_changed: bool,
    frontend_deps_changed: bool,
    restart: bool,
    failure_reason: str,
    failure_detail: str | None = None,
) -> dict:
    """#1207: canonical verified rollback with environment restoration."""
    rollback = git_ops.reset_hard(old)
    shutil.rmtree(_STAGED_DASHBOARD_STATIC, ignore_errors=True)
    if python_deps_changed:
        _reinstall_deps()
    if restart and frontend_deps_changed:
        _install_frontend_deps()
    if not rollback.get("ok"):
        actual_head = git_ops.current_commit() or ""
        metadata = _persist_current_commit_fresh(actual_head)
        _record_metadata_failure(actual_head, metadata)
        write_audit(
            {
                "actor": "human",
                "source": "api",
                "event_type": "update_rollback_failed",
                "entity_type": "system_update",
                "entity_id": new,
                "before": {"commit": old},
                "after": {"commit": actual_head},
                "message": f"Rollback FAILED: {rollback.get('reason')}; on-disk HEAD is {actual_head}",
            }
        )
        return {
            "applied": False,
            "rolled_back": False,
            "reason": "rollback_failed",
            "mode": "development",
            "old_commit": old,
            "attempted_commit": new,
            "actual_head": actual_head,
            "rollback_error": rollback.get("reason"),
            "message": f"Rollback failed — on unverified code at {actual_head}. Manual intervention required.",
            **_metadata_fields(metadata),
        }
    metadata = _persist_current_commit_fresh(old)
    _record_metadata_failure(old, metadata)
    write_audit(
        {
            "actor": "human",
            "source": "api",
            "event_type": "update_rolled_back",
            "entity_type": "system_update",
            "entity_id": old,
            "before": {"commit": new},
            "after": {"commit": old},
            "message": f"Update rolled back: {failure_reason}: {failure_detail or ''}",
        }
    )
    return {
        "applied": False,
        "rolled_back": True,
        "reason": failure_reason,
        "mode": "development",
        "old_commit": old,
        "attempted_commit": new,
        "message": f"Update rolled back: {failure_reason}" + (f": {failure_detail}" if failure_detail else "") + ". No restart needed.",
        **_metadata_fields(metadata),
    }


def rollback_applied_update(result: dict) -> dict:
    """Restore a successfully activated source update and restart it."""
    old = str(result.get("old_commit") or "")
    new = str(result.get("new_commit") or "")
    if not old or not new:
        return {"rolled_back": False, "reason": "rollback_context_unavailable"}

    if result.get("schema_changed"):
        backup = str(result.get("schema_backup") or "")
        if not backup:
            return {"rolled_back": False, "reason": "rollback_context_unavailable"}
        from aletheia.update.auto import _rollback_to_old_runtime

        recovery = _rollback_to_old_runtime(
            old,
            Path(backup),
            bool(result.get("had_previous_static")),
            restore_static=bool(result.get("frontend_built")),
        )
        metadata = _persist_current_commit_fresh(old)
        _record_metadata_failure(old, metadata)
        actual_head = git_ops.current_commit() or ""
        rolled_back = bool(recovery.get("ok")) and metadata.get("ok") is True and actual_head == old
        write_audit(
            {
                "actor": "human",
                "source": "cli",
                "event_type": "update_postflight_rolled_back" if rolled_back else "update_postflight_rollback_failed",
                "entity_type": "system_update",
                "entity_id": old,
                "before": {"commit": new},
                "after": {"commit": actual_head},
                "message": "Schema update rollback " + ("succeeded" if rolled_back else "failed"),
            }
        )
        return {
            "rolled_back": rolled_back,
            "reason": None if rolled_back else (recovery.get("reason") or "rollback_recovery_failed"),
            "actual_head": actual_head,
            "recovery_started": bool(recovery.get("ok")),
            "recovery_errors": [] if rolled_back else [recovery.get("reason") or "rollback_recovery_failed"],
            **_metadata_fields(metadata),
        }

    rollback = git_ops.reset_hard(old)
    if not rollback.get("ok"):
        return {
            "rolled_back": False,
            "reason": "rollback_failed",
            "actual_head": git_ops.current_commit() or "",
        }

    recovery_errors: list[str] = []
    if result.get("python_deps_changed") and not _reinstall_deps().get("ok"):
        recovery_errors.append("python_dependency_restore_failed")
    if result.get("frontend_deps_changed") and not _install_frontend_deps().get("ok"):
        recovery_errors.append("frontend_dependency_restore_failed")
    if result.get("frontend_changed"):
        old_tree = git_ops.tree_hash(old, "frontend")
        build = _build_frontend(old_tree)
        if build.get("ok"):
            _activate_frontend_build()
            shutil.rmtree(_PREVIOUS_DASHBOARD_STATIC, ignore_errors=True)
        else:
            recovery_errors.append("frontend_restore_failed")

    metadata = _persist_current_commit_fresh(old)
    _record_metadata_failure(old, metadata)
    if metadata.get("ok") is not True:
        recovery_errors.append("metadata_restore_failed")
    restarted = _restart_gateway()
    if not restarted.get("ok"):
        recovery_errors.append("gateway_recovery_failed")
    actual_head = git_ops.current_commit() or ""
    rolled_back = actual_head == old and not recovery_errors
    write_audit(
        {
            "actor": "human",
            "source": "cli",
            "event_type": ("update_postflight_rolled_back" if rolled_back else "update_postflight_rollback_failed"),
            "entity_type": "system_update",
            "entity_id": old,
            "before": {"commit": new},
            "after": {"commit": actual_head},
            "message": "Postflight rollback " + ("succeeded" if rolled_back else "failed"),
            "recovery_errors": recovery_errors,
        }
    )
    return {
        "rolled_back": rolled_back,
        "reason": None if rolled_back else "rollback_recovery_failed",
        "actual_head": actual_head,
        "recovery_started": bool(restarted.get("ok")),
        "recovery_errors": recovery_errors,
        **_metadata_fields(metadata),
    }


def apply_update(*, restart: bool = False, progress=None) -> dict:
    """Apply an update according to the current execution mode."""
    if _is_packaged():
        try:
            result = _packaged_module().request_update()
        except Exception as exc:
            return {
                "accepted": False,
                "applied": False,
                "mode": "packaged",
                "reason": getattr(exc, "reason", "packaged_update_request_failed"),
                "message": str(exc),
            }
        write_audit(
            {
                "actor": "human",
                "source": "api",
                "event_type": "packaged_update_requested",
                "entity_type": "system_update",
                "entity_id": str(result.get("operation_id") or "unknown"),
                "message": result.get("message") or "Packaged update requested",
            }
        )
        return {"mode": "packaged", **result}

    # This object is PRE-PULL ONLY. It is used for eligibility and branch
    # selection, then deliberately never written after source switches.
    cfg = load_config()
    if not cfg.update.enabled:
        return {"applied": False, "reason": "update_disabled", "mode": "development"}
    # #1206: refuse updates when not inside the project-managed venv
    if not _is_managed_venv():
        return {"applied": False, "reason": "update_wrong_interpreter", "mode": "development"}
    if not git_ops.git_available():
        return {"applied": False, "reason": "git_unavailable", "mode": "development"}
    branch = cfg.update.branch

    if git_ops.is_worktree_dirty():
        write_audit(
            {
                "actor": "human",
                "source": "api",
                "event_type": "update_blocked",
                "entity_type": "system_update",
                "entity_id": git_ops.current_commit(),
                "message": "Update blocked: uncommitted local changes in source worktree",
            }
        )
        return {
            "applied": False,
            "reason": "dirty_worktree",
            "mode": "development",
            "message": "Refusing to pull: the source worktree has uncommitted changes.",
        }

    current_branch = git_ops.current_branch()
    if current_branch != branch:
        write_audit(
            {
                "actor": "human",
                "source": "api",
                "event_type": "update_blocked",
                "entity_type": "system_update",
                "entity_id": git_ops.current_commit(),
                "message": (
                    "Update blocked: source checkout branch "
                    f"{current_branch or 'detached'} does not match configured branch {branch}"
                ),
            }
        )
        return {
            "applied": False,
            "reason": "wrong_branch",
            "mode": "development",
            "message": (
                f"Refusing to update from {current_branch or 'detached'}; use the configured {branch} source checkout."
            ),
        }

    if progress:
        progress("Fetching latest commits")
    old = git_ops.current_commit()
    if not old:
        _record_update_failure("", "current_commit_unavailable")
        return {
            "applied": False,
            "reason": "current_commit_unavailable",
            "mode": "development",
        }
    pulled = git_ops.pull(branch)
    if not pulled.get("ok"):
        _record_update_failure(
            old,
            pulled.get("reason", "pull_failed"),
            detail=pulled.get("stderr") or "",
            actual_head=pulled.get("current_commit"),
        )
        return {
            "applied": False,
            "reason": pulled.get("reason", "pull_failed"),
            "message": pulled.get("stderr") or "",
            "actual_head": pulled.get("current_commit", ""),
            "mode": "development",
        }

    new = pulled.get("remote_commit") or git_ops.current_commit()
    if not new:
        _record_update_failure(old, "new_commit_unavailable")
        return {"applied": False, "reason": "new_commit_unavailable", "mode": "development"}
    try:
        changed_files = git_ops.changed_files(old, new)
    except Exception as exc:
        changed_files = None
        changed_files_error = f"{type(exc).__name__}: {exc}"
    else:
        changed_files_error = "git diff --name-only failed"
    if changed_files is None:
        _record_update_failure(old, "changed_files_unavailable", attempted=new, detail=changed_files_error)
        return _rollback_update(
            old,
            new,
            python_deps_changed=False,
            frontend_deps_changed=False,
            restart=False,
            failure_reason="changed_files_unavailable",
            failure_detail=changed_files_error,
        )
    python_deps_changed = bool({"pyproject.toml", "uv.lock"}.intersection(changed_files))
    frontend_deps_changed = _frontend_deps_changed(changed_files)
    frontend_changed = any(path.startswith("frontend/") for path in changed_files)
    deps_reinstalled = False

    schema_changed = "aletheia/storage/migrations.py" in changed_files
    schema_backup = ""
    if schema_changed:
        if not restart:
            return _rollback_update(
                old,
                new,
                python_deps_changed=False,
                frontend_deps_changed=False,
                restart=False,
                failure_reason="schema_update_requires_restart",
            )
        if progress:
            progress("Preflighting schema migration")
        from aletheia.backup.service import BackupService
        from aletheia.update.auto import AutoUpdateError, _candidate_preflight, _remove_candidate

        reset = git_ops.reset_hard(old)
        if not reset.get("ok"):
            # A single retry covers a transient Git/index lock without leaving the
            # post-pull checkout unclassified indefinitely.
            reset = git_ops.reset_hard(old)
        if not reset.get("ok"):
            actual_head = git_ops.current_commit() or ""
            detail = reset.get("stderr") or reset.get("reason") or "hard reset failed"
            _record_update_failure(
                old,
                "schema_preflight_source_restore_failed",
                attempted=new,
                detail=detail,
                actual_head=actual_head,
            )
            return {
                "applied": False,
                "reason": "schema_preflight_source_restore_failed",
                "message": f"Could not restore the pre-pull source checkout: {detail}",
                "old_commit": old,
                "attempted_commit": new,
                "actual_head": actual_head,
                "mode": "development",
            }
        candidate = None
        try:
            preflight_backup = BackupService(get_data_dir()).create_backup(label="interactive_schema_preflight")
            candidate = _candidate_preflight(new, preflight_backup)
        except AutoUpdateError as exc:
            _record_update_failure(old, exc.reason, attempted=new, detail=exc.detail)
            return {
                "applied": False,
                "reason": exc.reason,
                "message": exc.detail,
                "mode": "development",
            }
        except Exception as exc:
            _record_update_failure(old, "schema_preflight_failed", attempted=new, detail=type(exc).__name__)
            return {
                "applied": False,
                "reason": "schema_preflight_failed",
                "message": type(exc).__name__,
                "mode": "development",
            }
        finally:
            _remove_candidate(candidate)
        advanced = git_ops.merge_fast_forward(new)
        if not advanced.get("ok"):
            _record_update_failure(
                old,
                advanced.get("reason", "ff_only_rejected"),
                attempted=new,
                detail=advanced.get("stderr", ""),
                actual_head=advanced.get("current_commit"),
            )
            return {
                "applied": False,
                "reason": advanced.get("reason", "ff_only_rejected"),
                "message": advanced.get("stderr", ""),
                "actual_head": advanced.get("current_commit", ""),
                "mode": "development",
            }

    if python_deps_changed:
        if progress:
            progress("Reinstalling Python dependencies")
        install = _reinstall_deps()
        if not install.get("ok"):
            return _rollback_update(
                old,
                new,
                python_deps_changed=False,
                frontend_deps_changed=frontend_deps_changed,
                restart=restart,
                failure_reason=install.get("reason", "python_dependency_install_failed"),
                failure_detail=install.get("detail"),
            )
        deps_reinstalled = True
    if restart and frontend_deps_changed:
        if progress:
            progress("Updating frontend packages")
        install = _install_frontend_deps()
        if not install.get("ok"):
            return _rollback_update(
                old,
                new,
                python_deps_changed=python_deps_changed,
                frontend_deps_changed=False,
                restart=restart,
                failure_reason=install["reason"],
                failure_detail=install.get("detail"),
            )
    frontend_tree = git_ops.tree_hash(new, "frontend") if restart else ""
    dashboard_build_required = restart and _dashboard_build_required(frontend_tree)
    if dashboard_build_required:
        if progress:
            progress("Building dashboard")
        build = _build_frontend(frontend_tree)
        if not build.get("ok"):
            return _rollback_update(
                old,
                new,
                python_deps_changed=python_deps_changed,
                frontend_deps_changed=frontend_deps_changed,
                restart=restart,
                failure_reason=build["reason"],
                failure_detail=build.get("detail"),
            )

    if not schema_changed:
        if progress:
            progress("Running smoke check")
        smoke = run_smoke_check()
        if not smoke.get("ok"):
            return _rollback_update(
                old,
                new,
                python_deps_changed=python_deps_changed,
                frontend_deps_changed=frontend_deps_changed,
                restart=restart,
                failure_reason="smoke_failed",
                failure_detail=f"stage={smoke.get('stage')}: {smoke.get('detail')}",
            )

    frontend_built = False
    had_previous_static = False
    if restart:
        if schema_changed:
            from aletheia.backup.service import BackupService

            try:
                schema_backup = str(BackupService(get_data_dir()).create_backup(label="interactive_schema_activate"))
            except Exception as exc:
                return _rollback_update(
                    old,
                    new,
                    python_deps_changed=python_deps_changed,
                    frontend_deps_changed=frontend_deps_changed,
                    restart=False,
                    failure_reason="schema_activation_backup_failed",
                    failure_detail=type(exc).__name__,
                )
        if dashboard_build_required:
            had_previous_static = _activate_frontend_build()
            frontend_built = True
        if progress:
            progress("Restarting gateway")
        restarted = _restart_gateway()
        if not restarted.get("ok"):
            if schema_changed:
                recovery = rollback_applied_update(
                    {
                        "old_commit": old,
                        "new_commit": new,
                        "schema_changed": True,
                        "schema_backup": schema_backup,
                        "frontend_built": frontend_built,
                        "had_previous_static": had_previous_static,
                    }
                )
                return {
                    "applied": False,
                    "rolled_back": recovery.get("rolled_back", False),
                    "reason": "gateway_restart_failed",
                    "mode": "development",
                    "old_commit": old,
                    "attempted_commit": new,
                    "actual_head": recovery.get("actual_head", ""),
                    "recovery_started": recovery.get("recovery_started", False),
                    "message": restarted.get("detail", ""),
                }
            if frontend_built:
                _restore_frontend_build(had_previous_static)
            rollback = git_ops.reset_hard(old)
            if python_deps_changed:
                _reinstall_deps()
            if frontend_deps_changed:
                _install_frontend_deps()
            actual_head_after_rollback = git_ops.current_commit() or ""
            metadata = _persist_current_commit_fresh(actual_head_after_rollback)
            _record_metadata_failure(actual_head_after_rollback, metadata)
            recovery = _restart_gateway() if rollback.get("ok") else {"ok": False}
            restart_detail = restarted.get("detail", "")
            recovery_detail = recovery.get("detail", "") if rollback.get("ok") else ""
            return {
                "applied": False,
                "rolled_back": bool(rollback.get("ok")),
                "reason": "gateway_restart_failed",
                "mode": "development",
                "old_commit": old,
                "attempted_commit": new,
                "actual_head": actual_head_after_rollback,
                "recovery_started": bool(recovery.get("ok")),
                "message": (
                    f"Gateway restart failed during update. "
                    f"Restart error: {restart_detail}. "
                    f"Recovery {('succeeded' if recovery.get('ok') else 'failed')}: {recovery_detail}"
                ),
                **_metadata_fields(metadata),
            }
        if frontend_built and not schema_changed:
            shutil.rmtree(_PREVIOUS_DASHBOARD_STATIC, ignore_errors=True)

    metadata = _persist_current_commit_fresh(new)
    _record_metadata_failure(new, metadata)
    source_changed = old != new
    if restart:
        message = (
            "Update applied and gateway restarted."
            if source_changed
            else "Source already up to date; gateway restarted."
        )
    else:
        message = (
            "Update applied. Restart the gateway to load the new code."
            if source_changed
            else "Source already up to date; no source restart was requested."
        )
    write_audit(
        {
            "actor": "human",
            "source": "api",
            "event_type": "update_applied",
            "entity_type": "system_update",
            "entity_id": new,
            "before": {"commit": old},
            "after": {"commit": new},
            "message": (
                (f"Updated {old[:8]} -> {new[:8]}" if source_changed else f"Source already at {new[:8]}") + " "
                f"(deps_reinstalled={deps_reinstalled}, frontend_built={frontend_built}, restart={restart}, "
                f"metadata_persisted={metadata.get('ok') is True})"
            ),
        }
    )
    return {
        "applied": True,
        "mode": "development",
        "old_commit": old,
        "new_commit": new,
        "source_changed": source_changed,
        "deps_reinstalled": deps_reinstalled,
        "frontend_built": frontend_built,
        "python_deps_changed": python_deps_changed,
        "frontend_deps_changed": frontend_deps_changed,
        "frontend_changed": frontend_changed,
        "schema_changed": schema_changed,
        "schema_backup": schema_backup or None,
        "had_previous_static": had_previous_static,
        "restart_requested": restart,
        "restart_completed": restart,
        "restart_required": not restart,
        "message": message,
        **_metadata_fields(metadata),
    }


def rollback_update() -> dict:
    if not _is_packaged():
        return {
            "accepted": False,
            "reason": "rollback_only_available_in_packaged_mode",
            "mode": "development",
        }
    try:
        result = _packaged_module().request_rollback()
    except Exception as exc:
        return {
            "accepted": False,
            "reason": getattr(exc, "reason", "packaged_rollback_request_failed"),
            "message": str(exc),
            "mode": "packaged",
        }
    return {"mode": "packaged", **result}
