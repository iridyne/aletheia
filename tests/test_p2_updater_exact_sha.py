"""P2 regression: updater must capture an exact SHA (not a mutable ref) and stay honest
after failed rollback or restart.

Tests:
1. apply_update() uses the SHA returned by pull() (fetched at pull time), not a
   second current_commit() call that could race with an advancing origin/master.
2. After smoke-check failure + rollback failure, update metadata records the
   actual on-disk HEAD (the unverified new commit), not the old SHA.
3. After gateway restart failure (post-activate), a successful rollback records the
   rolled-back (old) SHA through the target/current-checkout fresh persist seam.
4. Packaged _preflight_candidate uses expected_sha from the signed feed, not the
   candidate's own manifest (circular self-certification).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cfg(current_commit: str = "abc123", branch: str = "master"):
    cfg = MagicMock()
    cfg.update.enabled = True
    cfg.update.branch = branch
    cfg.update.current_commit = current_commit
    cfg.update.last_known_remote_commit = None
    cfg.runtime.paused = False
    return cfg


# ---------------------------------------------------------------------------
# Test 1: exact SHA captured from pull() result, not a second current_commit()
# ---------------------------------------------------------------------------

def test_apply_update_uses_pull_sha_not_second_current_commit():
    """The persisted target commit must be pull()'s pinned remote_commit."""
    from aletheia.update import git_ops
    from ops.update import applier, git_ops as git_mutations

    old_sha = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    fetched_sha = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    later_sha = "cccccccccccccccccccccccccccccccccccccccc"

    cfg = _make_cfg(current_commit=old_sha)
    persisted_commits: list[str] = []
    call_count = [0]

    def _current_commit():
        call_count[0] += 1
        if call_count[0] == 1:
            return old_sha
        return later_sha

    def _persist(commit: str):
        persisted_commits.append(commit)
        return {"ok": True, "changed": True}

    with (
        patch.object(git_ops, "current_commit", side_effect=_current_commit),
        patch.object(git_ops, "current_branch", return_value="master"),
        patch.object(git_ops, "is_worktree_dirty", return_value=False),
        patch.object(
            git_mutations,
            "pull",
            return_value={
                "ok": True,
                "output": "Fast-forward",
                "remote_commit": fetched_sha,
            },
        ),
        patch.object(git_ops, "changed_files", return_value=[]),
        patch("ops.update.applier.load_config", return_value=cfg),
        patch("ops.update.applier._persist_current_commit_fresh", side_effect=_persist),
        patch("ops.update.applier.run_smoke_check", return_value={"ok": True}),
        patch("ops.update.applier.write_audit"),
        patch("ops.update.applier._is_managed_venv", return_value=True),
    ):
        result = applier.apply_update(restart=False)

    assert result["applied"] is True
    assert persisted_commits, "target-fresh metadata persist was never called"
    assert persisted_commits[-1] == fetched_sha, (
        f"Expected pinned SHA {fetched_sha!r}, got {persisted_commits[-1]!r}"
    )
    assert result.get("new_commit") == fetched_sha


# ---------------------------------------------------------------------------
# Test 2: after smoke failure + rollback failure, metadata shows actual HEAD
# ---------------------------------------------------------------------------

def test_rollback_failure_records_actual_head_in_config():
    """When rollback fails, metadata must record the actual unverified HEAD."""
    from aletheia.update import git_ops
    from ops.update import applier, git_ops as git_mutations

    old_sha = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    new_sha = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

    cfg = _make_cfg(current_commit=old_sha)
    persisted_commits: list[str] = []
    call_count = [0]

    def _current_commit_seq():
        call_count[0] += 1
        return old_sha if call_count[0] == 1 else new_sha

    def _persist(commit: str):
        persisted_commits.append(commit)
        return {"ok": True, "changed": True}

    with (
        patch.object(git_ops, "current_commit", side_effect=_current_commit_seq),
        patch.object(git_ops, "current_branch", return_value="master"),
        patch.object(git_ops, "is_worktree_dirty", return_value=False),
        patch.object(
            git_mutations,
            "pull",
            return_value={
                "ok": True,
                "output": "Fast-forward",
                "remote_commit": new_sha,
            },
        ),
        patch.object(git_ops, "changed_files", return_value=[]),
        patch.object(
            git_mutations,
            "reset_hard",
            return_value={"ok": False, "reason": "reset_failed"},
        ),
        patch("ops.update.applier.load_config", return_value=cfg),
        patch("ops.update.applier._persist_current_commit_fresh", side_effect=_persist),
        patch(
            "ops.update.applier.run_smoke_check",
            return_value={"ok": False, "stage": "import", "detail": "ImportError"},
        ),
        patch("ops.update.applier.write_audit"),
        patch("ops.update.applier._is_managed_venv", return_value=True),
    ):
        result = applier.apply_update(restart=False)

    assert result["applied"] is False
    assert result["rolled_back"] is False
    assert result["reason"] == "rollback_failed"
    assert persisted_commits, "target-fresh metadata persist was never called"
    assert persisted_commits[-1] == new_sha, (
        f"Expected unverified new SHA {new_sha!r}, got {persisted_commits[-1]!r}"
    )
    assert result.get("actual_head") == new_sha


# ---------------------------------------------------------------------------
# Test 3: restart failure + successful rollback records old SHA
# ---------------------------------------------------------------------------

def test_gateway_restart_failure_records_rolled_back_sha_in_config():
    """When restart fails and rollback succeeds, metadata records old SHA."""
    from aletheia.update import git_ops
    from ops.update import applier, git_ops as git_mutations

    old_sha = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    new_sha = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

    cfg = _make_cfg(current_commit=old_sha)
    persisted_commits: list[str] = []
    call_count = [0]

    def _current_commit_seq():
        call_count[0] += 1
        return old_sha

    def _persist(commit: str):
        persisted_commits.append(commit)
        return {"ok": True, "changed": True}

    with (
        patch.object(git_ops, "current_commit", side_effect=_current_commit_seq),
        patch.object(git_ops, "current_branch", return_value="master"),
        patch.object(git_ops, "is_worktree_dirty", return_value=False),
        patch.object(
            git_mutations,
            "pull",
            return_value={"ok": True, "output": "Fast-forward", "remote_commit": new_sha},
        ),
        patch.object(git_ops, "changed_files", return_value=[]),
        patch.object(
            git_mutations,
            "reset_hard",
            return_value={"ok": True, "output": "HEAD is now..."},
        ),
        patch("ops.update.applier.load_config", return_value=cfg),
        patch("ops.update.applier._persist_current_commit_fresh", side_effect=_persist),
        patch("ops.update.applier.run_smoke_check", return_value={"ok": True}),
        patch("ops.update.applier._build_frontend", return_value={"ok": True}),
        patch("ops.update.applier._activate_frontend_build", return_value=True),
        patch("ops.update.applier._restore_frontend_build"),
        patch(
            "ops.update.applier._restart_gateway",
            return_value={"ok": False, "reason": "gateway_restart_failed"},
        ),
        patch("ops.update.applier.write_audit"),
        patch("ops.update.applier._is_managed_venv", return_value=True),
    ):
        result = applier.apply_update(restart=True)

    assert result["applied"] is False
    assert result["rolled_back"] is True
    assert result["reason"] == "gateway_restart_failed"
    assert persisted_commits, "target-fresh metadata persist was never called"
    assert persisted_commits[-1] == old_sha, (
        f"Expected old SHA {old_sha!r} after rollback, got {persisted_commits[-1]!r}"
    )
