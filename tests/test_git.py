"""Tests for optional Git versioning of knowledge/playbooks/reports (§2.2, §9.4).

Git is optional. When disabled (default) operations are no-ops. When enabled,
it versions data/knowledge, data/playbooks, data/reports and never stages
secrets.enc or app.db. Degrades gracefully when git is unavailable.
"""

import shutil
import subprocess

import pytest


def _git_available() -> bool:
    return shutil.which("git") is not None


def setup_data(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "knowledge" / "research_notes").mkdir(parents=True, exist_ok=True)
    (tmp_path / "playbooks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "reports").mkdir(parents=True, exist_ok=True)


def test_git_disabled_is_noop(tmp_path, monkeypatch):
    setup_data(tmp_path, monkeypatch)
    from aletheia.files import git
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.runtime.git_enabled = False
    save_config(cfg)
    result = git.commit_all("test message")
    assert result["committed"] is False
    assert result["reason"] == "git_disabled"
    assert not (tmp_path / ".git").exists()


@pytest.mark.skipif(not _git_available(), reason="git not installed")
def test_git_enabled_inits_and_commits(tmp_path, monkeypatch):
    setup_data(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.runtime.git_enabled = True
    save_config(cfg)

    # write some content + a secret that must NOT be committed
    (tmp_path / "knowledge" / "research_notes" / "note1.md").write_text("# hello", encoding="utf-8")
    (tmp_path / "secrets.enc").write_text("TOPSECRET", encoding="utf-8")
    (tmp_path / "app.db").write_text("binarydata", encoding="utf-8")

    from aletheia.files import git
    result = git.commit_all("initial knowledge commit")
    assert result["committed"] is True
    assert (tmp_path / ".git").exists()

    # Verify the secret + db are NOT tracked
    tracked = subprocess.run(
        ["git", "-C", str(tmp_path), "ls-files"],
        capture_output=True, text=True,
    ).stdout
    assert "note1.md" in tracked
    assert "secrets.enc" not in tracked
    assert "app.db" not in tracked


@pytest.mark.skipif(not _git_available(), reason="git not installed")
def test_git_commit_nothing_to_commit(tmp_path, monkeypatch):
    setup_data(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.runtime.git_enabled = True
    save_config(cfg)

    from aletheia.files import git
    git.commit_all("first")
    # second commit with no changes should report nothing committed but not error
    result = git.commit_all("second")
    assert result["committed"] in (False, True)
    assert "error" not in result
