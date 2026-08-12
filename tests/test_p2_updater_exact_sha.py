"""P2 regression: updater must capture an exact SHA (not a mutable ref) and stay honest
after failed rollback or restart.

Tests:
1. apply_update() uses the SHA returned by pull() (fetched at pull time), not a
   second current_commit() call that could race with an advancing origin/master.
2. After smoke-check failure + rollback failure, cfg.update.current_commit is set
   to the actual on-disk HEAD (the unverified new commit), not left as the old SHA.
3. After gateway restart failure (post-activate), a successful rollback records the
   rolled-back (old) SHA in config.
4. Packaged _preflight_candidate uses expected_sha from the signed feed, not the
   candidate's own manifest (circular self-certification).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch, call

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
    """The new commit in config must come from pull()'s remote_commit, not a re-read."""
    from aletheia.update import applier, git_ops

    old_sha = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    # SHA that was at origin/master at fetch time:
    fetched_sha = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    # SHA that origin/master moved to BETWEEN fetch and a second current_commit() call:
    later_sha  = "cccccccccccccccccccccccccccccccccccccccc"

    cfg = _make_cfg(current_commit=old_sha)
    saved_commits: list[str] = []

    def _save_cfg(c):
        saved_commits.append(c.update.current_commit)

    call_count = [0]

    def _current_commit():
        call_count[0] += 1
        # First call: during old = git_ops.current_commit() before pull
        # Any subsequent call returns the advanced SHA
        if call_count[0] == 1:
            return old_sha
        return later_sha  # would be wrong if we re-read HEAD post-merge

    with (
        patch.object(git_ops, "current_commit", side_effect=_current_commit),
        patch.object(git_ops, "current_branch", return_value="master"),
        patch.object(git_ops, "is_worktree_dirty", return_value=False),
        patch.object(git_ops, "pull", return_value={
            "ok": True,
            "output": "Fast-forward",
            "remote_commit": fetched_sha,  # <- pinned at fetch time
        }),
        patch.object(git_ops, "changed_files", return_value=[]),
        patch("aletheia.update.applier.load_config", return_value=cfg),
        patch("aletheia.update.applier.save_config", side_effect=_save_cfg),
        patch("aletheia.update.applier.run_smoke_check", return_value={"ok": True}),
        patch("aletheia.update.applier.write_audit"),
        patch("aletheia.update.applier._is_packaged", return_value=False),
    ):
        result = applier.apply_update(restart=False)

    assert result["applied"] is True
    # Config must record the fetch-pinned SHA, not the later_sha
    assert saved_commits, "save_config was never called"
    assert saved_commits[-1] == fetched_sha, (
        f"Expected pinned SHA {fetched_sha!r}, got {saved_commits[-1]!r}"
    )
    assert result.get("new_commit") == fetched_sha


# ---------------------------------------------------------------------------
# Test 2: after smoke failure + rollback failure, config shows actual HEAD
# ---------------------------------------------------------------------------

def test_rollback_failure_records_actual_head_in_config():
    """When rollback fails, cfg.update.current_commit = actual HEAD (unverified new)."""
    from aletheia.update import applier, git_ops

    old_sha = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    new_sha = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

    cfg = _make_cfg(current_commit=old_sha)
    saved_commits: list[str] = []

    def _save_cfg(c):
        saved_commits.append(c.update.current_commit)

    call_count = [0]

    def _current_commit_seq():
        call_count[0] += 1
        # First call: old (before pull)
        # Any later call (during rollback failure path): still shows new (reset failed)
        return old_sha if call_count[0] == 1 else new_sha

    with (
        patch.object(git_ops, "current_commit", side_effect=_current_commit_seq),
        patch.object(git_ops, "current_branch", return_value="master"),
        patch.object(git_ops, "is_worktree_dirty", return_value=False),
        patch.object(git_ops, "pull", return_value={
            "ok": True,
            "output": "Fast-forward",
            "remote_commit": new_sha,
        }),
        patch.object(git_ops, "changed_files", return_value=[]),
        patch.object(git_ops, "reset_hard", return_value={"ok": False, "reason": "reset_failed"}),
        patch("aletheia.update.applier.load_config", return_value=cfg),
        patch("aletheia.update.applier.save_config", side_effect=_save_cfg),
        patch("aletheia.update.applier.run_smoke_check", return_value={
            "ok": False, "stage": "import", "detail": "ImportError"
        }),
        patch("aletheia.update.applier.write_audit"),
        patch("aletheia.update.applier._is_packaged", return_value=False),
    ):
        result = applier.apply_update(restart=False)

    assert result["applied"] is False
    assert result["rolled_back"] is False
    assert result["reason"] == "rollback_failed"
    # config must show the actual (unverified new) head, not old
    assert saved_commits, "save_config was never called"
    assert saved_commits[-1] == new_sha, (
        f"Expected unverified new SHA {new_sha!r}, got {saved_commits[-1]!r}"
    )
    assert result.get("actual_head") == new_sha


# ---------------------------------------------------------------------------
# Test 3: after gateway restart failure + successful rollback, config shows old SHA
# ---------------------------------------------------------------------------

def test_gateway_restart_failure_records_rolled_back_sha_in_config():
    """When restart fails and rollback succeeds, config shows the old (rolled back) SHA."""
    from aletheia.update import applier, git_ops

    old_sha = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    new_sha = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

    cfg = _make_cfg(current_commit=old_sha)
    saved_commits: list[str] = []

    def _save_cfg(c):
        saved_commits.append(c.update.current_commit)

    call_count = [0]

    def _current_commit_seq():
        call_count[0] += 1
        # 1st: before pull; 2nd: after successful rollback → old again
        return old_sha if call_count[0] == 1 else old_sha

    with (
        patch.object(git_ops, "current_commit", side_effect=_current_commit_seq),
        patch.object(git_ops, "current_branch", return_value="master"),
        patch.object(git_ops, "is_worktree_dirty", return_value=False),
        patch.object(git_ops, "pull", return_value={
            "ok": True, "output": "Fast-forward", "remote_commit": new_sha,
        }),
        patch.object(git_ops, "changed_files", return_value=[]),
        patch.object(git_ops, "reset_hard", return_value={"ok": True, "output": "HEAD is now..."}),
        patch("aletheia.update.applier.load_config", return_value=cfg),
        patch("aletheia.update.applier.save_config", side_effect=_save_cfg),
        patch("aletheia.update.applier.run_smoke_check", return_value={"ok": True}),
        patch("aletheia.update.applier._build_frontend", return_value={"ok": True}),
        patch("aletheia.update.applier._activate_frontend_build", return_value=True),
        patch("aletheia.update.applier._restore_frontend_build"),
        patch("aletheia.update.applier._restart_gateway", return_value={"ok": False, "reason": "gateway_restart_failed"}),
        patch("aletheia.update.applier.write_audit"),
        patch("aletheia.update.applier._is_packaged", return_value=False),
    ):
        result = applier.apply_update(restart=True)

    assert result["applied"] is False
    assert result["rolled_back"] is True
    assert result["reason"] == "gateway_restart_failed"
    # Config must show old_sha (rolled back successfully)
    assert saved_commits, "save_config was never called"
    assert saved_commits[-1] == old_sha, (
        f"Expected old SHA {old_sha!r} after rollback, got {saved_commits[-1]!r}"
    )


# ---------------------------------------------------------------------------
# Test 4: _preflight_candidate uses expected_sha from caller, not candidate manifest
# ---------------------------------------------------------------------------

def test_preflight_candidate_uses_caller_provided_sha(tmp_path):
    """_preflight_candidate must use the feed-pinned SHA, not read it from the manifest."""
    from aletheia.update import packaged

    candidate = tmp_path / "release"
    candidate.mkdir()
    # Write a manifest with a *different* SHA than what the feed says
    manifest_sha = "feed_sha_from_signed_feed"
    tampered_sha = "tampered_sha_in_manifest"
    (candidate / "release-manifest.json").write_text(
        json.dumps({
            "schema_version": 1,
            "target": "packaged-runtime",
            "platform": packaged.platform_id(),
            "git_sha": tampered_sha,  # different from feed SHA
            "version": "1.0.0",
            "runtime_entrypoint": "aletheia-core",
            "files": [],
        }),
        encoding="utf-8",
    )

    validated_sha_values: list[str] = []

    def _mock_validate(root, *, expected_release_id, expected_sha):
        validated_sha_values.append(expected_sha)
        # Simulate successful validation
        return {"entrypoint_path": str(root / "aletheia-core"), "git_sha": expected_sha}

    with (
        patch.object(packaged, "_validate_release_dir", side_effect=_mock_validate),
        patch.object(packaged, "RestoreService") as mock_restore_cls,
    ):
        mock_restore = MagicMock()
        mock_restore.restore.return_value = MagicMock(success=True)
        mock_restore_cls.return_value = mock_restore

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            packaged._preflight_candidate(
                candidate,
                backup_path=tmp_path / "backup.tar.gz",
                operation_id="op-123",
                target_release="v1.0.0",
                expected_sha=manifest_sha,  # feed-pinned, must be used
            )

    assert validated_sha_values, "_validate_release_dir was never called"
    assert validated_sha_values[0] == manifest_sha, (
        f"_validate_release_dir received {validated_sha_values[0]!r} instead of "
        f"the feed-pinned SHA {manifest_sha!r}"
    )
