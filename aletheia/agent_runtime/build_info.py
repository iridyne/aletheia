"""Boot-commit tracking — detects "process is running stale code".

The gateway imports modules once at startup. If the source repo is updated
(committed) but the process is not restarted, the running code silently
diverges from disk. That exact failure once made every market chart go blank
with no visible signal — /api/health still reported healthy.

This module captures the commit the process booted on, and exposes a health
issue when disk HEAD has moved past it. The boot commit is written once at
lifespan startup (before any scheduler thread or request handler runs), so the
write happens-before every read; a single str rebinding is atomic under the
GIL, so no lock is needed.

Known limitation (accepted): uncommitted working-tree edits do NOT move HEAD,
so live edits on an editable install are not flagged. The real failure mode is
"committed but not restarted", which is a HEAD divergence and is caught.
"""

from __future__ import annotations

_boot_commit: str | None = None


def record_boot_commit() -> None:
    """Capture the source commit at process start. Call once from lifespan()."""
    global _boot_commit
    from aletheia.update.git_ops import current_commit

    commit = current_commit()
    if not commit:
        try:
            from aletheia.update.packaged import (
                _current_release_git_sha,
                current_selection,
            )

            commit = _current_release_git_sha(current_selection()) or ""
        except Exception:
            pass
    _boot_commit = commit or None  # "" -> None (build identity unavailable)


def get_boot_commit() -> str | None:
    return _boot_commit


def code_drift_issue() -> str | None:
    """Return a health issue string if disk HEAD has moved past the booted commit.

    Returns None when there is nothing to flag: boot commit was never captured
    (non-repo / git missing), git is now unavailable, or HEAD matches the boot
    commit. detached HEAD still resolves to a SHA, so comparison works normally.
    """
    if not _boot_commit:
        return None  # didn't capture — can't compare, don't false-alarm
    from aletheia.update.git_ops import current_commit
    head = current_commit()
    if not head or head == _boot_commit:
        return None
    return f"code_updated_pending_restart:{_boot_commit[:7]}..{head[:7]}"
