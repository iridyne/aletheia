"""jj_ops reads bookmark / ahead / behind / dirty from a real colocated jj checkout."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from aletheia.update import git_ops, jj_ops

needs_jj = pytest.mark.skipif(shutil.which("jj") is None, reason="jj_ops needs the jj binary")


def _jj(cwd, *args: str) -> None:
    subprocess.run(["jj", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    monkeypatch.setenv("JJ_USER", "Test")
    monkeypatch.setenv("JJ_EMAIL", "test@example.invalid")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    work = tmp_path / "work"
    work.mkdir()
    _jj(work, "git", "init")
    _jj(work, "git", "remote", "add", "origin", str(remote))
    (work / "a.txt").write_text("a\n")
    _jj(work, "commit", "-m", "base")
    _jj(work, "bookmark", "create", "main", "-r", "@-")
    _jj(work, "git", "push", "--bookmark", "main")
    monkeypatch.setattr(git_ops, "REPO_ROOT", work)
    return work


@needs_jj
def test_clean_checkout_at_the_remote_bookmark(checkout):
    assert jj_ops.is_jj_repo()
    assert jj_ops.current_bookmark() == "main"
    assert (jj_ops.ahead_count("main"), jj_ops.behind_count("main")) == (0, 0)
    assert not jj_ops.is_dirty()


@needs_jj
def test_unpushed_commit_and_dirty_working_copy_are_reported(checkout):
    (checkout / "b.txt").write_text("b\n")
    _jj(checkout, "commit", "-m", "local")
    (checkout / "c.txt").write_text("c\n")

    assert jj_ops.ahead_count("main") == 1
    assert jj_ops.is_dirty()
    assert jj_ops.ahead_count("no-such-bookmark") == -1
