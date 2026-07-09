"""Self-upgrade orchestration: check for updates and apply them on demand.

Never raises into callers. Never auto-restarts the gateway — it returns
restart_required=True and lets the human restart. Refuses to pull over a dirty
worktree. Reinstalls dependencies only when pyproject.toml changed.
"""

import logging
import os
import subprocess
from datetime import datetime, timezone

from aletheia.config.loader import load_config, save_config, _PROJECT_ROOT
from aletheia.files.jsonl import write_audit
from aletheia.gateway.pidfile import _no_window_kwargs
from aletheia.update import git_ops
from aletheia.update.smoke import run_smoke_check

# #136: strip every exchange/API credential prefix the app reads from os.environ,
# not just BITGET_. COINALYZE_/FRED_ are read from os.environ directly (coinalyze.py,
# macro.py); OKX_/BINANCE_ are included for cross-broker parity.
_SECRET_PREFIXES = ("BITGET_", "OKX_", "BINANCE_", "OPENAI_", "ANTHROPIC_", "LLM_",
                    "TELEGRAM_BOT_TOKEN", "COINALYZE_", "FRED_")


def _safe_env() -> dict:
    """Return os.environ with secret-bearing keys stripped."""
    return {k: v for k, v in os.environ.items()
            if not k.startswith(_SECRET_PREFIXES)}

logger = logging.getLogger("aletheia.update.applier")


def check_for_update() -> dict:
    """Fetch and report whether an update is available. Read-only."""
    cfg = load_config()
    if not cfg.update.enabled:
        return {"ok": False, "reason": "update_disabled"}
    if not git_ops.git_available():
        return {"ok": False, "reason": "git_unavailable"}

    branch = cfg.update.branch
    fetched = git_ops.fetch(branch)
    if not fetched.get("ok"):
        # Never claim "up to date" on a fetch error (UI honesty).
        return {"ok": False, "reason": fetched.get("reason", "fetch_failed")}

    cur = git_ops.current_commit()
    remote = git_ops.remote_commit(branch)
    behind = git_ops.behind_count(branch)
    has_update = behind > 0

    cfg.update.last_check_time = datetime.now(timezone.utc).isoformat()
    cfg.update.current_commit = cur
    cfg.update.last_known_remote_commit = remote
    save_config(cfg)

    write_audit({
        "actor": "human", "source": "api", "event_type": "update_checked",
        "entity_type": "system_update", "entity_id": cur,
        "message": f"Update check on {branch}: behind={behind}",
    })
    return {
        "ok": True, "has_update": has_update, "behind_count": behind,
        "current_commit": cur, "remote_commit": remote, "branch": branch,
    }


def _reinstall_deps() -> dict:
    """Reinstall the package (editable) so changed dependencies take effect."""
    env = _safe_env()
    for cmd in (["uv", "pip", "install", "-e", "."], ["pip", "install", "-e", "."]):
        try:
            r = subprocess.run(
                cmd,
                cwd=str(_PROJECT_ROOT), capture_output=True, text=True,
                env=env, **_no_window_kwargs(),
            )
            if r.returncode == 0:
                return {"ok": True, "tool": cmd[0]}
        except FileNotFoundError:
            continue
    return {"ok": False, "reason": "install_failed"}


def apply_update() -> dict:
    """Pull the latest code, reinstall deps if needed, and signal restart needed."""
    cfg = load_config()
    if not cfg.update.enabled:
        return {"applied": False, "reason": "update_disabled"}
    if not git_ops.git_available():
        return {"applied": False, "reason": "git_unavailable"}

    branch = cfg.update.branch

    if git_ops.is_worktree_dirty():
        write_audit({
            "actor": "human", "source": "api", "event_type": "update_blocked",
            "entity_type": "system_update", "entity_id": git_ops.current_commit(),
            "message": "Update blocked: uncommitted local changes in source worktree",
        })
        return {"applied": False, "reason": "dirty_worktree",
                "message": "Refusing to pull: the source worktree has uncommitted changes."}

    old = git_ops.current_commit()
    pulled = git_ops.pull(branch)
    if not pulled.get("ok"):
        write_audit({
            "actor": "human", "source": "api", "event_type": "update_failed",
            "entity_type": "system_update", "entity_id": old,
            "message": f"Update failed: {pulled.get('reason')}",
        })
        return {"applied": False, "reason": pulled.get("reason", "pull_failed")}

    new = git_ops.current_commit()
    pyproject_changed = "pyproject.toml" in git_ops.changed_files(old, new)
    deps_reinstalled = False
    if pyproject_changed:
        install = _reinstall_deps()
        deps_reinstalled = bool(install.get("ok"))

    # Smoke-verify the pulled code before committing to it. On failure, roll back
    # to the recorded pre-pull commit (user-authorized controlled rollback) so a
    # bad update can never brick the gateway. Never auto-restarts.
    smoke = run_smoke_check()
    if not smoke.get("ok"):
        rollback = git_ops.reset_hard(old)
        if pyproject_changed:
            _reinstall_deps()  # restore old deps to match the reverted code
        if not rollback.get("ok"):
            # #135: reset_hard failed — on-disk HEAD is still the broken NEW commit.
            # Do NOT claim a clean revert (UI honesty): surface the true failed state
            # so the operator knows the box may be on unverified code (config/audit
            # must not disagree with the real HEAD).
            write_audit({
                "actor": "human", "source": "api", "event_type": "update_rollback_failed",
                "entity_type": "system_update", "entity_id": new,
                "before": {"commit": old}, "after": {"commit": new},
                "message": (f"Rollback FAILED after smoke failure at {smoke.get('stage')}: "
                            f"{rollback.get('reason')}; on-disk HEAD may still be {new}"),
            })
            return {
                "applied": False, "rolled_back": False, "reason": "rollback_failed",
                "smoke_stage": smoke.get("stage"), "old_commit": old, "attempted_commit": new,
                "rollback_error": rollback.get("reason"),
                "message": ("Smoke check failed AND rollback failed — the gateway may be "
                            "on unverified code. Manual intervention required."),
            }
        cfg.update.current_commit = old
        save_config(cfg)
        write_audit({
            "actor": "human", "source": "api", "event_type": "update_rolled_back",
            "entity_type": "system_update", "entity_id": old,
            "before": {"commit": new}, "after": {"commit": old},
            "message": f"Update rolled back: smoke failed at {smoke.get('stage')}: {smoke.get('detail')}",
        })
        return {
            "applied": False, "rolled_back": True, "reason": "smoke_failed",
            "smoke_stage": smoke.get("stage"), "old_commit": old, "attempted_commit": new,
            "message": "Update rolled back after smoke check failed. No restart needed.",
        }

    cfg.update.current_commit = new
    save_config(cfg)

    write_audit({
        "actor": "human", "source": "api", "event_type": "update_applied",
        "entity_type": "system_update", "entity_id": new,
        "before": {"commit": old}, "after": {"commit": new},
        "message": f"Updated {old[:8]} -> {new[:8]} (deps_reinstalled={deps_reinstalled})",
    })
    return {
        "applied": True, "old_commit": old, "new_commit": new,
        "deps_reinstalled": deps_reinstalled, "restart_required": True,
        "message": "Update applied. Restart the gateway to load the new code.",
    }
