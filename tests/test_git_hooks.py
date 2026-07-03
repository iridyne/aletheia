"""Auto-restart git hooks — install/idempotency/safety.

These exercise the real filesystem + a real `git init` temp repo (no mocks of the
git boundary), per the project's no-mock-call-verify testing rule.
"""

import subprocess
from pathlib import Path

import pytest

from aletheia.gateway import git_hooks

HOOK_NAMES = ("post-merge", "post-checkout", "post-rewrite")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    return tmp_path


def _hooks_dir(repo: Path) -> Path:
    return repo / ".git" / "hooks"


def test_install_creates_all_three_hooks(repo: Path):
    actions = git_hooks.ensure_git_hooks(repo_dir=repo, port=9999)
    assert len([a for a in actions if a.startswith("installed")]) == 3
    for name in HOOK_NAMES:
        body = (_hooks_dir(repo) / name).read_text(encoding="utf-8")
        assert git_hooks._MARKER_PREFIX in body
        assert "9999" in body                       # port templated in
        assert 'git rev-parse --git-common-dir' in body  # worktree self-guard present
        assert body.startswith("#!/bin/sh")


def test_installed_hook_debounces_restart_bursts(repo: Path):
    git_hooks.ensure_git_hooks(repo_dir=repo, port=9999)
    body = (_hooks_dir(repo) / "post-merge").read_text(encoding="utf-8")

    assert "aletheia-auto-restart.last" in body
    assert "$((now - last)) -lt 15" in body
    assert "printf" in body


def test_installed_hook_only_restarts_for_runtime_paths(repo: Path):
    git_hooks.ensure_git_hooks(repo_dir=repo, port=9999)
    body = (_hooks_dir(repo) / "post-merge").read_text(encoding="utf-8")

    assert "git diff --name-only ORIG_HEAD HEAD" in body
    assert "grep -Eq" in body
    assert "aletheia/" in body
    assert "frontend/" in body
    assert "docs/" not in git_hooks._RUNTIME_CHANGE_PATTERN
    assert "tests/" not in git_hooks._RUNTIME_CHANGE_PATTERN


def test_install_is_idempotent(repo: Path):
    git_hooks.ensure_git_hooks(repo_dir=repo, port=9999)
    assert git_hooks.ensure_git_hooks(repo_dir=repo, port=9999) == []  # nothing to do


def test_port_change_rewrites_managed_hook(repo: Path):
    git_hooks.ensure_git_hooks(repo_dir=repo, port=8787)
    actions = git_hooks.ensure_git_hooks(repo_dir=repo, port=8788)
    assert any(a.startswith("updated") for a in actions)
    body = (_hooks_dir(repo) / "post-merge").read_text(encoding="utf-8")
    assert "8788" in body and "8787" not in body


def test_foreign_hook_is_never_clobbered(repo: Path):
    foreign = _hooks_dir(repo) / "post-merge"
    foreign.parent.mkdir(parents=True, exist_ok=True)
    foreign.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
    actions = git_hooks.ensure_git_hooks(repo_dir=repo, port=9999)
    assert any("post-merge: foreign hook present" in a for a in actions)
    assert foreign.read_text(encoding="utf-8") == "#!/bin/sh\necho mine\n"  # untouched
    # the other two still get installed
    assert (_hooks_dir(repo) / "post-checkout").exists()


def test_post_checkout_guards_against_file_restores(repo: Path):
    git_hooks.ensure_git_hooks(repo_dir=repo, port=9999)
    body = (_hooks_dir(repo) / "post-checkout").read_text(encoding="utf-8")
    assert '[ "$3" = "1" ]' in body  # only branch switches restart, not `checkout -- file`
    # post-merge has no such guard
    assert '[ "$3" = "1" ]' not in (_hooks_dir(repo) / "post-merge").read_text(encoding="utf-8")


def test_status_reports_installed_missing_foreign(repo: Path):
    st = git_hooks.git_hooks_status(repo_dir=repo)
    assert st["error"] is None
    assert set(st["missing"]) == set(HOOK_NAMES) and not st["installed"]

    git_hooks.ensure_git_hooks(repo_dir=repo, port=9999)
    st = git_hooks.git_hooks_status(repo_dir=repo)
    assert set(st["installed"]) == set(HOOK_NAMES) and not st["missing"]


def test_not_a_git_repo_is_a_quiet_noop(tmp_path: Path):
    assert git_hooks.ensure_git_hooks(repo_dir=tmp_path, port=9999) == []
    st = git_hooks.git_hooks_status(repo_dir=tmp_path)
    assert st["error"] == "not a git repository"


def test_install_never_raises_on_bad_repo_dir():
    # A non-existent path must degrade to [] / error, never blow up startup.
    bad = Path("/nonexistent-aletheia-xyz-123")
    assert git_hooks.ensure_git_hooks(repo_dir=bad) == []
