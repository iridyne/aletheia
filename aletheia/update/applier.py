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
from datetime import datetime, timezone

from aletheia.config.loader import _PROJECT_ROOT, load_config, save_config
from aletheia.files.jsonl import write_audit
from aletheia.gateway.pidfile import _no_window_kwargs
from aletheia.update import git_ops
from aletheia.update.smoke import run_smoke_check

# #136: strip every exchange/API credential prefix the app reads from os.environ,
# not just BITGET_. COINALYZE_/FRED_ are read from os.environ directly (coinalyze.py,
# macro.py); OKX_/BINANCE_ are included for cross-broker parity.
_SECRET_PREFIXES = (
    "BITGET_",
    "OKX_",
    "BINANCE_",
    "OPENAI_",
    "ANTHROPIC_",
    "LLM_",
    "TELEGRAM_BOT_TOKEN",
    "COINALYZE_",
    "FRED_",
)

logger = logging.getLogger("aletheia.update.applier")


def _packaged_module():
    # All installed callers go through the safety facade. The low-level staging module
    # remains an implementation detail and cannot be invoked by Web/CLI code directly.
    from aletheia.update import packaged_commands

    return packaged_commands


def _is_packaged() -> bool:
    return _packaged_module().is_packaged_mode()


def _safe_env() -> dict:
    """Return os.environ with secret-bearing keys stripped."""

    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(_SECRET_PREFIXES)
    }


def update_progress() -> dict:
    """Return durable local progress without performing a network check."""

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
    """Fetch and report whether an update is available. Read-only."""

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
        # Never claim "up to date" on a fetch error (UI honesty).
        return {
            "ok": False,
            "reason": fetched.get("reason", "fetch_failed"),
            "mode": "development",
        }

    cur = git_ops.current_commit()
    remote = git_ops.remote_commit(branch)
    behind = git_ops.behind_count(branch)
    has_update = behind > 0

    cfg.update.last_check_time = datetime.now(timezone.utc).isoformat()
    cfg.update.current_commit = cur
    cfg.update.last_known_remote_commit = remote
    save_config(cfg)

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
        "has_update": has_update,
        "behind_count": behind,
        "current_commit": cur,
        "remote_commit": remote,
        "branch": branch,
    }


def _reinstall_deps() -> dict:
    """Reinstall the package (editable) so changed dependencies take effect."""

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
    return any(path in {"frontend/package.json", "frontend/package-lock.json"} for path in changed_files)


def _run_npm(args: list[str], *, env: dict) -> subprocess.CompletedProcess | None:
    """Run npm, using its Windows command shim when present."""
    commands = ("npm.cmd", "npm") if os.name == "nt" else ("npm",)
    for command in commands:
        try:
            return subprocess.run(
                [command, *args], cwd=str(_FRONTEND_DIR), capture_output=True, text=True,
                env=env, **_no_window_kwargs(),
            )
        except FileNotFoundError:
            continue
    return None


def _install_frontend_deps() -> dict:
    result = _run_npm(["ci"], env=_safe_env())
    if result is None:
        return {"ok": False, "reason": "npm_unavailable"}
    return {"ok": result.returncode == 0, "reason": "npm_ci_failed" if result.returncode else None}


def _build_frontend() -> dict:
    """Build into an unserved staging directory; do not mutate live UI yet."""
    shutil.rmtree(_STAGED_DASHBOARD_STATIC, ignore_errors=True)
    env = _safe_env()
    env["VITE_BUILD_OUT_DIR"] = str(_STAGED_DASHBOARD_STATIC)
    result = _run_npm(["run", "build"], env=env)
    if result is None:
        return {"ok": False, "reason": "npm_unavailable"}
    if result.returncode or not (_STAGED_DASHBOARD_STATIC / "index.html").is_file():
        shutil.rmtree(_STAGED_DASHBOARD_STATIC, ignore_errors=True)
        return {"ok": False, "reason": "frontend_build_failed"}
    return {"ok": True}


def _activate_frontend_build() -> bool:
    """Swap complete static output while the gateway is stopped."""
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
    """Use the managed lifecycle, including its preflight and readiness proof."""
    from aletheia.gateway import lifecycle
    try:
        lifecycle.do_stop()
        lifecycle.do_start(no_browser=True)
    except Exception as exc:
        return {"ok": False, "reason": type(exc).__name__}
    return {"ok": True}


def apply_update(*, restart: bool = False) -> dict:
    """Apply an update according to the current execution mode."""

    if _is_packaged():
        try:
            result = _packaged_module().request_update()
        except Exception as exc:
            reason = getattr(exc, "reason", "packaged_update_request_failed")
            return {
                "accepted": False,
                "applied": False,
                "mode": "packaged",
                "reason": reason,
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

    cfg = load_config()
    if not cfg.update.enabled:
        return {"applied": False, "reason": "update_disabled", "mode": "development"}
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
                f"Refusing to update from {current_branch or 'detached'}; "
                f"use the configured {branch} source checkout."
            ),
        }

    old = git_ops.current_commit()
    pulled = git_ops.pull(branch)
    if not pulled.get("ok"):
        write_audit(
            {
                "actor": "human",
                "source": "api",
                "event_type": "update_failed",
                "entity_type": "system_update",
                "entity_id": old,
                "message": f"Update failed: {pulled.get('reason')}",
            }
        )
        return {
            "applied": False,
            "reason": pulled.get("reason", "pull_failed"),
            "mode": "development",
        }

    new = git_ops.current_commit()
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

    # Smoke-verify the pulled code before committing to it. On failure, roll back
    # to the recorded pre-pull commit (user-authorized controlled rollback) so a
    # bad update can never brick the gateway.
    smoke = run_smoke_check()
    if not smoke.get("ok"):
        rollback = git_ops.reset_hard(old)
        shutil.rmtree(_STAGED_DASHBOARD_STATIC, ignore_errors=True)
        if python_deps_changed:
            _reinstall_deps()  # restore old deps to match the reverted code
        if restart and frontend_deps_changed:
            _install_frontend_deps()
        if not rollback.get("ok"):
            # #135: reset_hard failed — on-disk HEAD is still the broken NEW commit.
            # Do NOT claim a clean revert (UI honesty): surface the true failed state
            # so the operator knows the box may be on unverified code (config/audit
            # must not disagree with the real HEAD).
            write_audit(
                {
                    "actor": "human",
                    "source": "api",
                    "event_type": "update_rollback_failed",
                    "entity_type": "system_update",
                    "entity_id": new,
                    "before": {"commit": old},
                    "after": {"commit": new},
                    "message": (
                        f"Rollback FAILED after smoke failure at {smoke.get('stage')}: "
                        f"{rollback.get('reason')}; on-disk HEAD may still be {new}"
                    ),
                }
            )
            return {
                "applied": False,
                "rolled_back": False,
                "reason": "rollback_failed",
                "mode": "development",
                "smoke_stage": smoke.get("stage"),
                "old_commit": old,
                "attempted_commit": new,
                "rollback_error": rollback.get("reason"),
                "message": (
                    "Smoke check failed AND rollback failed — the gateway may be "
                    "on unverified code. Manual intervention required."
                ),
            }
        cfg.update.current_commit = old
        save_config(cfg)
        write_audit(
            {
                "actor": "human",
                "source": "api",
                "event_type": "update_rolled_back",
                "entity_type": "system_update",
                "entity_id": old,
                "before": {"commit": new},
                "after": {"commit": old},
                "message": (
                    f"Update rolled back: smoke failed at {smoke.get('stage')}: "
                    f"{smoke.get('detail')}"
                ),
            }
        )
        return {
            "applied": False,
            "rolled_back": True,
            "reason": "smoke_failed",
            "mode": "development",
            "smoke_stage": smoke.get("stage"),
            "old_commit": old,
            "attempted_commit": new,
            "message": "Update rolled back after smoke check failed. No restart needed.",
        }

    frontend_built = False
    if restart:
        had_previous_static = _activate_frontend_build()
        frontend_built = True
        restarted = _restart_gateway()
        if not restarted.get("ok"):
            _restore_frontend_build(had_previous_static)
            rollback = git_ops.reset_hard(old)
            if python_deps_changed:
                _reinstall_deps()
            if frontend_deps_changed:
                _install_frontend_deps()
            recovery = _restart_gateway() if rollback.get("ok") else {"ok": False}
            return {
                "applied": False,
                "rolled_back": bool(rollback.get("ok")),
                "reason": "gateway_restart_failed",
                "mode": "development",
                "old_commit": old,
                "attempted_commit": new,
                "recovery_started": bool(recovery.get("ok")),
            }
        shutil.rmtree(_PREVIOUS_DASHBOARD_STATIC, ignore_errors=True)

    cfg.update.current_commit = new
    save_config(cfg)

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
                f"Updated {old[:8]} -> {new[:8]} "
                f"(deps_reinstalled={deps_reinstalled}, frontend_built={frontend_built}, restart={restart})"
            ),
        }
    )
    return {
        "applied": True,
        "mode": "development",
        "old_commit": old,
        "new_commit": new,
        "deps_reinstalled": deps_reinstalled,
        "frontend_built": frontend_built,
        "restart_requested": restart,
        "restart_completed": restart,
        "restart_required": not restart,
        "message": "Update applied and gateway restarted." if restart else "Update applied. Restart the gateway to load the new code.",
    }


def rollback_update() -> dict:
    """Request rollback to the previous installed release."""

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
