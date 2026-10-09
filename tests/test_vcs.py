"""The local gates refuse to read a change set that is not this checkout's own."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from scripts.ci import vcs

needs_jj = pytest.mark.skipif(shutil.which("jj") is None, reason="vcs reads the change set through jj")


@pytest.fixture
def jj_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("JJ_USER", "Test")
    monkeypatch.setenv("JJ_EMAIL", "test@example.invalid")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["jj", "git", "init"], cwd=repo, check=True, capture_output=True)
    (repo / "a.md").write_text("a\n")
    monkeypatch.setattr(vcs, "_checked_root", False, raising=False)
    return repo


@needs_jj
def test_workspace_root_is_accepted(jj_repo, monkeypatch):
    monkeypatch.setattr(vcs, "REPO_ROOT", jj_repo)
    assert vcs.changed_files() == ["a.md"]


@needs_jj
def test_nested_plain_checkout_fails_instead_of_reading_the_parent(jj_repo, monkeypatch):
    # A `git worktree` nested under the main checkout has no `.jj`; jj would walk up
    # and report the parent's change set, so a gate there would check the wrong files.
    nested = jj_repo / ".claude" / "worktrees" / "plain"
    nested.mkdir(parents=True)
    monkeypatch.setattr(vcs, "REPO_ROOT", nested)
    with pytest.raises(SystemExit, match="not a jj workspace root"):
        vcs.changed_files()


def test_missing_jj_binary_is_a_bounded_failure(monkeypatch, tmp_path):
    def missing(*_args, **_kwargs):
        raise FileNotFoundError("jj")

    monkeypatch.setattr(vcs, "_checked_root", False, raising=False)
    monkeypatch.setattr(vcs, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(vcs.subprocess, "run", missing)
    with pytest.raises(SystemExit, match="jj unavailable"):
        vcs.file_list("glob:**/*.md")
