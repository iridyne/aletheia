"""Legacy auto-restart git hook cleanup — removal/idempotency/safety.

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


def _write_legacy_managed_hook(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/bin/sh\n"
        "# >>> aletheia-auto-restart v2 (managed by doctor) >>>\n"
        "curl http://127.0.0.1:8787/api/gateway/restart\n"
        "# <<< aletheia-auto-restart <<<\n",
        encoding="utf-8",
    )


def test_cleanup_removes_all_standalone_managed_hooks_and_stamp(repo: Path):
    for name in HOOK_NAMES:
        _write_legacy_managed_hook(_hooks_dir(repo) / name)
    stamp = repo / ".git" / "aletheia-auto-restart.last"
    stamp.write_text("123", encoding="utf-8")

    actions = git_hooks.remove_managed_git_hooks(repo_dir=repo)

    assert len([a for a in actions if a.startswith("removed")]) == 3
    assert all(not (_hooks_dir(repo) / name).exists() for name in HOOK_NAMES)
    assert not stamp.exists()


def test_cleanup_is_idempotent(repo: Path):
    assert git_hooks.remove_managed_git_hooks(repo_dir=repo) == []
    assert git_hooks.remove_managed_git_hooks(repo_dir=repo) == []


def test_foreign_hook_is_never_clobbered(repo: Path):
    foreign = _hooks_dir(repo) / "post-merge"
    foreign.parent.mkdir(parents=True, exist_ok=True)
    foreign.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
    actions = git_hooks.remove_managed_git_hooks(repo_dir=repo)
    assert actions == []
    assert foreign.read_text(encoding="utf-8") == "#!/bin/sh\necho mine\n"  # untouched


def test_marker_inside_foreign_wrapper_is_not_deleted(repo: Path):
    wrapped = _hooks_dir(repo) / "post-merge"
    wrapped.parent.mkdir(parents=True, exist_ok=True)
    wrapped.write_text(
        "#!/bin/sh\necho before\n"
        "# >>> aletheia-auto-restart v2 (managed by doctor) >>>\n"
        "# <<< aletheia-auto-restart <<<\n",
        encoding="utf-8",
    )

    assert git_hooks.remove_managed_git_hooks(repo_dir=repo) == []
    assert wrapped.exists()


def test_status_reports_installed_missing_foreign(repo: Path):
    st = git_hooks.git_hooks_status(repo_dir=repo)
    assert st["error"] is None
    assert set(st["missing"]) == set(HOOK_NAMES) and not st["installed"]

    for name in HOOK_NAMES:
        _write_legacy_managed_hook(_hooks_dir(repo) / name)
    st = git_hooks.git_hooks_status(repo_dir=repo)
    assert set(st["installed"]) == set(HOOK_NAMES) and not st["missing"]


def test_not_a_git_repo_is_a_quiet_noop(tmp_path: Path):
    assert git_hooks.remove_managed_git_hooks(repo_dir=tmp_path) == []
    st = git_hooks.git_hooks_status(repo_dir=tmp_path)
    assert st["error"] == "not a git repository"


def test_cleanup_never_raises_on_bad_repo_dir():
    # A non-existent path must degrade to [] / error, never blow up startup.
    bad = Path("/nonexistent-aletheia-xyz-123")
    assert git_hooks.remove_managed_git_hooks(repo_dir=bad) == []
