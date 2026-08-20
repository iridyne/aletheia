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
from datetime import datetime, timezone

from aletheia.config.loader import _PROJECT_ROOT, get_data_dir, load_config
from aletheia.files.jsonl import write_audit
from aletheia.gateway.pidfile import _no_window_kwargs
from aletheia.update import git_ops, target_fresh

_SECRET_PREFIXES = (
    "BITGET_", "OKX_", "BINANCE_", "OPENAI_", "ANTHROPIC_", "LLM_",
    "TELEGRAM_BOT_TOKEN", "COINALYZE_", "FRED_",
)

logger = logging.getLogger("aletheia.update.applier")


def _packaged_module():
    from aletheia.update import packaged_commands
    return packaged_commands


def _is_packaged() -> bool:
    return _packaged_module().is_packaged_mode()


def _safe_env() -> dict:
    return {
        key: value for key, value in os.environ.items()
        if not key.startswith(_SECRET_PREFIXES)
    }


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
    write_audit({
        "actor": "human",
        "source": "api",
        "event_type": "update_checked",
        "entity_type": "system_update",
        "entity_id": cur,
        "message": f"Update check on {branch}: behind={behind}",
    })
    return {
        "ok": True,
        "mode": "development",
        "has_update": behind > 0,
        "behind_count": behind,
        "current_commit": cur,
        "remote_commit": remote,
        "branch": branch,
    }


def _reinstall_deps() -> dict:
    env = _safe_env()
    for cmd in (["uv", "pip", "install", "-e", "."], ["pip", "install", "-e", "."]):
        try:
            result = subprocess.run(
                cmd,
                cwd=str(_PROJECT_ROOT),
                capture_output=True,
                text=True,
                env=env,
                **_no_window_kwargs(),
            )
            if result.returncode == 0:
                return {"ok": True, "tool": cmd[0]}
        except FileNotFoundError:
            continue
    return {"ok": False, "reason": "install_failed"}


_FRONTEND_DIR = _PROJECT_ROOT / "frontend"
_DASHBOARD_STATIC = _PROJECT_ROOT / "aletheia" / "dashboard_static"
_STAGED_DASHBOARD_STATIC = _PROJECT_ROOT / "aletheia" / ".dashboard_static-next"
_PREVIOUS_DASHBOARD_STATIC = _PROJECT_ROOT / "aletheia" / ".dashboard_static-previous"


def _frontend_deps_changed(changed_files: list[str]) -> bool:
    return any(
        path in {"frontend/package.json", "frontend/package-lock.json"}
        for path in changed_files
    )


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
                **_no_window_kwargs(),
            )
        except FileNotFoundError:
            continue
    return None


def _install_frontend_deps() -> dict:
    result = _run_npm(["ci"], env=_safe_env())
    if result is None:
        return {"ok": False, "reason": "npm_unavailable"}
    return {
        "ok": result.returncode == 0,
        "reason": "npm_ci_failed" if result.returncode else None,
    }


def _build_frontend() -> dict:
    """Build staging output, then validate it with target-loaded Python code."""
    shutil.rmtree(_STAGED_DASHBOARD_STATIC, ignore_errors=True)
    env = _safe_env()
    env["VITE_BUILD_OUT_DIR"] = str(_STAGED_DASHBOARD_STATIC)
    result = _run_npm(["run", "build"], env=env)
    if result is None:
        return {"ok": False, "reason": "npm_unavailable"}
    if result.returncode or not (_STAGED_DASHBOARD_STATIC / "index.html").is_file():
        shutil.rmtree(_STAGED_DASHBOARD_STATIC, ignore_errors=True)
        return {"ok": False, "reason": "frontend_build_failed"}

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
    except Exception as exc:
        return {"ok": False, "reason": type(exc).__name__}
    if result.returncode:
        return {
            "ok": False,
            "reason": "gateway_restart_failed",
            "detail": (result.stderr or result.stdout).strip()[-500:],
        }
    return {"ok": True}


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


def _record_metadata_failure(commit: str, result: dict | None) -> None:
    fields = _metadata_fields(result)
    if fields["metadata_persisted"]:
        return
    write_audit({
        "actor": "system",
        "source": "updater",
        "event_type": "update_metadata_persist_failed",
        "entity_type": "system_update",
        "entity_id": commit or "unknown",
        "message": (
            "Source state changed successfully, but target-loaded update metadata "
            f"could not be persisted: {fields['metadata_error']}"
        ),
    })


def apply_update(*, restart: bool = False) -> dict:
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
        write_audit({
            "actor": "human",
            "source": "api",
            "event_type": "packaged_update_requested",
            "entity_type": "system_update",
            "entity_id": str(result.get("operation_id") or "unknown"),
            "message": result.get("message") or "Packaged update requested",
        })
        return {"mode": "packaged", **result}

    # This object is PRE-PULL ONLY. It is used for eligibility and branch
    # selection, then deliberately never written after source switches.
    cfg = load_config()
    if not cfg.update.enabled:
        return {"applied": False, "reason": "update_disabled", "mode": "development"}
    if not git_ops.git_available():
        return {"applied": False, "reason": "git_unavailable", "mode": "development"}
    branch = cfg.update.branch

    if git_ops.is_worktree_dirty():
        write_audit({
            "actor": "human", "source": "api", "event_type": "update_blocked",
            "entity_type": "system_update", "entity_id": git_ops.current_commit(),
            "message": "Update blocked: uncommitted local changes in source worktree",
        })
        return {
            "applied": False,
            "reason": "dirty_worktree",
            "mode": "development",
            "message": "Refusing to pull: the source worktree has uncommitted changes.",
        }

    current_branch = git_ops.current_branch()
    if current_branch != branch:
        write_audit({
            "actor": "human", "source": "api", "event_type": "update_blocked",
            "entity_type": "system_update", "entity_id": git_ops.current_commit(),
            "message": (
                "Update blocked: source checkout branch "
                f"{current_branch or 'detached'} does not match configured branch {branch}"
            ),
        })
        return {
            "applied": False,
            "reason": "wrong_branch",
            "mode": "development",
            "message": (
                f"Refusing to update from {current_branch or 'detached'}; "
                f"use the configured {branch} source checkout."
            ),
        }

    old = git_ops.current_commit()
    pulled = git_ops.pull(branch)
    if not pulled.get("ok"):
        write_audit({
            "actor": "human", "source": "api", "event_type": "update_failed",
            "entity_type": "system_update", "entity_id": old,
            "message": f"Update failed: {pulled.get('reason')}",
        })
        return {
            "applied": False,
            "reason": pulled.get("reason", "pull_failed"),
            "mode": "development",
        }

    new = pulled.get("remote_commit") or git_ops.current_commit()
    if not new:
        return {"applied": False, "reason": "new_commit_unavailable", "mode": "development"}
    changed_files = git_ops.changed_files(old, new)
    python_deps_changed = bool({"pyproject.toml", "uv.lock"}.intersection(changed_files))
    frontend_deps_changed = _frontend_deps_changed(changed_files)
    deps_reinstalled = False

    if python_deps_changed:
        install = _reinstall_deps()
        if not install.get("ok"):
            git_ops.reset_hard(old)
            return {"applied": False, "reason": "python_dependency_install_failed", "mode": "development"}
        deps_reinstalled = True
    if restart and frontend_deps_changed:
        install = _install_frontend_deps()
        if not install.get("ok"):
            git_ops.reset_hard(old)
            return {"applied": False, "reason": install["reason"], "mode": "development"}
    if restart:
        build = _build_frontend()
        if not build.get("ok"):
            git_ops.reset_hard(old)
            if python_deps_changed:
                _reinstall_deps()
            if frontend_deps_changed:
                _install_frontend_deps()
            return {"applied": False, "reason": build["reason"], "mode": "development"}

    smoke = run_smoke_check()
    if not smoke.get("ok"):
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
            write_audit({
                "actor": "human", "source": "api", "event_type": "update_rollback_failed",
                "entity_type": "system_update", "entity_id": new,
                "before": {"commit": old}, "after": {"commit": actual_head},
                "message": (
                    f"Rollback FAILED after smoke failure at {smoke.get('stage')}: "
                    f"{rollback.get('reason')}; on-disk HEAD is {actual_head}"
                ),
            })
            return {
                "applied": False,
                "rolled_back": False,
                "reason": "rollback_failed",
                "mode": "development",
                "smoke_stage": smoke.get("stage"),
                "old_commit": old,
                "attempted_commit": new,
                "actual_head": actual_head,
                "rollback_error": rollback.get("reason"),
                "message": (
                    "Smoke check failed AND rollback failed — the gateway is "
                    f"on unverified code at {actual_head}. Manual intervention required."
                ),
                **_metadata_fields(metadata),
            }
        metadata = _persist_current_commit_fresh(old)
        _record_metadata_failure(old, metadata)
        write_audit({
            "actor": "human", "source": "api", "event_type": "update_rolled_back",
            "entity_type": "system_update", "entity_id": old,
            "before": {"commit": new}, "after": {"commit": old},
            "message": (
                f"Update rolled back: smoke failed at {smoke.get('stage')}: "
                f"{smoke.get('detail')}"
            ),
        })
        return {
            "applied": False,
            "rolled_back": True,
            "reason": "smoke_failed",
            "mode": "development",
            "smoke_stage": smoke.get("stage"),
            "old_commit": old,
            "attempted_commit": new,
            "message": "Update rolled back after smoke check failed. No restart needed.",
            **_metadata_fields(metadata),
        }

    frontend_built = False
    if restart:
        had_previous_static = _activate_frontend_build()
        frontend_built = True
        restarted = _restart_gateway()
        if not restarted.get("ok"):
            if "aletheia/storage/migrations.py" in changed_files:
                metadata = _persist_current_commit_fresh(new)
                _record_metadata_failure(new, metadata)
                write_audit({
                    "actor": "human", "source": "api",
                    "event_type": "update_restart_failed_after_schema_change",
                    "entity_type": "system_update", "entity_id": new,
                    "before": {"commit": old}, "after": {"commit": new},
                    "message": (
                        "Gateway restart failed after a schema-bearing update; "
                        "new code was retained because database migrations are not reversible"
                    ),
                })
                return {
                    "applied": False,
                    "rolled_back": False,
                    "reason": "gateway_restart_failed_after_schema_change",
                    "mode": "development",
                    "old_commit": old,
                    "attempted_commit": new,
                    "restart_error": restarted.get("reason"),
                    "message": "New code retained; inspect migration and gateway logs, then retry start.",
                    **_metadata_fields(metadata),
                }

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
            return {
                "applied": False,
                "rolled_back": bool(rollback.get("ok")),
                "reason": "gateway_restart_failed",
                "mode": "development",
                "old_commit": old,
                "attempted_commit": new,
                "actual_head": actual_head_after_rollback,
                "recovery_started": bool(recovery.get("ok")),
                **_metadata_fields(metadata),
            }
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
    write_audit({
        "actor": "human", "source": "api", "event_type": "update_applied",
        "entity_type": "system_update", "entity_id": new,
        "before": {"commit": old}, "after": {"commit": new},
        "message": (
            (
                f"Updated {old[:8]} -> {new[:8]}"
                if source_changed
                else f"Source already at {new[:8]}"
            )
            + " "
            f"(deps_reinstalled={deps_reinstalled}, frontend_built={frontend_built}, restart={restart}, "
            f"metadata_persisted={metadata.get('ok') is True})"
        ),
    })
    return {
        "applied": True,
        "mode": "development",
        "old_commit": old,
        "new_commit": new,
        "source_changed": source_changed,
        "deps_reinstalled": deps_reinstalled,
        "frontend_built": frontend_built,
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
