"""Tests for the version-drift guard (aletheia/agent_runtime/build_info.py).

The guard's job: detect when the gateway process is running stale code — the
disk source has been committed past the commit this process booted on. That was
the invisible root cause of the chart-blackout bug (process booted before the
CCXTProvider fix was committed, /api/health still reported healthy).

All tests are pure (no git, no network) — git_ops.current_commit is monkeypatched.
"""

from aletheia.agent_runtime import build_info
from aletheia.update import git_ops


def _set_boot(monkeypatch, value):
    """Directly set the module-level boot commit (bypasses git)."""
    monkeypatch.setattr(build_info, "_boot_commit", value)


def test_record_boot_commit_captures_current(monkeypatch):
    monkeypatch.setattr(git_ops, "current_commit", lambda: "abc1234deadbeef")
    build_info.record_boot_commit()
    assert build_info.get_boot_commit() == "abc1234deadbeef"


def test_record_boot_commit_empty_becomes_none(monkeypatch):
    from aletheia.update import packaged

    # Neither a source checkout nor a packaged manifest can identify the build.
    monkeypatch.setattr(git_ops, "current_commit", lambda: "")
    monkeypatch.setattr(packaged, "current_selection", lambda: {})
    monkeypatch.setattr(packaged, "_current_release_git_sha", lambda _selection: None)
    build_info.record_boot_commit()
    assert build_info.get_boot_commit() is None


def test_record_boot_commit_uses_packaged_release_manifest(monkeypatch):
    from aletheia.update import packaged

    monkeypatch.setattr(git_ops, "current_commit", lambda: "")
    monkeypatch.setattr(packaged, "current_selection", lambda: {"current": "v1"})
    monkeypatch.setattr(
        packaged,
        "_current_release_git_sha",
        lambda selection: "packaged-sha" if selection["current"] == "v1" else None,
    )

    build_info.record_boot_commit()

    assert build_info.get_boot_commit() == "packaged-sha"


def test_no_drift_when_head_unchanged(monkeypatch):
    _set_boot(monkeypatch, "abc1234deadbeef")
    monkeypatch.setattr(git_ops, "current_commit", lambda: "abc1234deadbeef")
    assert build_info.code_drift_issue() is None


def test_drift_flagged_when_head_moved(monkeypatch):
    _set_boot(monkeypatch, "aaaaaaa000000")
    monkeypatch.setattr(git_ops, "current_commit", lambda: "bbbbbbb111111")
    issue = build_info.code_drift_issue()
    assert issue is not None
    assert issue.startswith("code_updated_pending_restart:")
    # Carries both short SHAs so the operator can see what moved.
    assert "aaaaaaa" in issue
    assert "bbbbbbb" in issue


def test_no_drift_when_boot_commit_unknown(monkeypatch):
    # Non-repo at boot → _boot_commit is None → never false-alarm (can't compare).
    _set_boot(monkeypatch, None)
    monkeypatch.setattr(git_ops, "current_commit", lambda: "bbbbbbb111111")
    assert build_info.code_drift_issue() is None


def test_no_drift_when_head_unreadable(monkeypatch):
    # git became unavailable after boot → current_commit() "" → don't flag.
    _set_boot(monkeypatch, "aaaaaaa000000")
    monkeypatch.setattr(git_ops, "current_commit", lambda: "")
    assert build_info.code_drift_issue() is None
