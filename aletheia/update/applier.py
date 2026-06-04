"""Self-upgrade orchestration: check for updates and apply them on demand.

Never raises into callers. Never auto-restarts the gateway — it returns
restart_required=True and lets the human restart. Refuses to pull over a dirty
worktree. Reinstalls dependencies only when pyproject.toml changed.
"""

import logging
import subprocess
from datetime import datetime, timezone

from aletheia.config.loader import load_config, save_config, _PROJECT_ROOT
from aletheia.files.jsonl import write_audit
from aletheia.update import git_ops

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
    for cmd in (["uv", "pip", "install", "-e", "."], ["pip", "install", "-e", "."]):
        try:
            r = subprocess.run(cmd, cwd=str(_PROJECT_ROOT), capture_output=True, text=True)
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
