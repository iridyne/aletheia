"""Tests for the on-demand self-upgrade mechanism (Feature 2).

Governance: human-triggered only, never a daemon, never agent-callable. git is
fully mocked here (no network, no real pull). Verifies behind-count parsing,
state persistence, dirty-worktree refusal, conditional dependency reinstall,
graceful failure, CLI/API smoke, and the agent-isolation guard.
"""

import sqlite3

import pytest


def setup_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    from aletheia.storage.migrations import run_migrations
    sq.reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()


class _FakeProc:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


def test_behind_count_parses_rev_list(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import git_ops
    monkeypatch.setattr(git_ops, "_git", lambda *a, **k: _FakeProc(stdout="3\n"))
    assert git_ops.behind_count("master") == 3
    monkeypatch.setattr(git_ops, "_git", lambda *a, **k: _FakeProc(stdout="0\n"))
    assert git_ops.behind_count("master") == 0


def test_check_for_update_writes_last_check(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import applier, git_ops
    monkeypatch.setattr(git_ops, "fetch", lambda branch="master": {"ok": True})
    monkeypatch.setattr(git_ops, "current_commit", lambda: "aaaa111")
    monkeypatch.setattr(git_ops, "remote_commit", lambda branch="master": "bbbb222")
    monkeypatch.setattr(git_ops, "behind_count", lambda branch="master": 2)

    result = applier.check_for_update()
    assert result["ok"] is True
    assert result["has_update"] is True
    assert result["behind_count"] == 2
    assert result["current_commit"] == "aaaa111"

    from aletheia.config.loader import load_config
    cfg = load_config()
    assert cfg.update.last_check_time is not None
    assert cfg.update.current_commit == "aaaa111"


def test_check_disabled_returns_reason(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config(); cfg.update.enabled = False; save_config(cfg)
    from aletheia.update import applier
    result = applier.check_for_update()
    assert result["ok"] is False
    assert result["reason"] == "update_disabled"


def test_apply_refuses_dirty_worktree(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import applier, git_ops
    monkeypatch.setattr(git_ops, "is_worktree_dirty", lambda: True)
    pulled = {"called": False}
    monkeypatch.setattr(git_ops, "pull", lambda branch="master": pulled.__setitem__("called", True))

    result = applier.apply_update()
    assert result["applied"] is False
    assert result["reason"] == "dirty_worktree"
    assert pulled["called"] is False

    from aletheia.files.jsonl import read_jsonl
    assert any(e.get("event_type") == "update_blocked" for e in read_jsonl("audit.jsonl"))


def test_apply_skips_deps_when_pyproject_unchanged(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import applier, git_ops
    monkeypatch.setattr(git_ops, "is_worktree_dirty", lambda: False)
    monkeypatch.setattr(git_ops, "current_commit", lambda: "old111")
    monkeypatch.setattr(git_ops, "pull", lambda branch="master": {"ok": True})
    monkeypatch.setattr(git_ops, "changed_files", lambda old, new: ["aletheia/foo.py"])
    # current_commit is called twice (before+after); make the second return new
    commits = iter(["old111", "new222"])
    monkeypatch.setattr(git_ops, "current_commit", lambda: next(commits))
    installed = {"called": False}
    monkeypatch.setattr(applier, "_reinstall_deps", lambda: installed.__setitem__("called", True))

    result = applier.apply_update()
    assert result["applied"] is True
    assert result["deps_reinstalled"] is False
    assert result["restart_required"] is True
    assert installed["called"] is False


def test_apply_reinstalls_deps_when_pyproject_changed(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import applier, git_ops
    monkeypatch.setattr(git_ops, "is_worktree_dirty", lambda: False)
    commits = iter(["old111", "new222"])
    monkeypatch.setattr(git_ops, "current_commit", lambda: next(commits))
    monkeypatch.setattr(git_ops, "pull", lambda branch="master": {"ok": True})
    monkeypatch.setattr(git_ops, "changed_files", lambda old, new: ["pyproject.toml", "aletheia/x.py"])
    installed = {"called": 0}
    monkeypatch.setattr(applier, "_reinstall_deps", lambda: installed.__setitem__("called", installed["called"] + 1) or {"ok": True})

    result = applier.apply_update()
    assert result["applied"] is True
    assert result["deps_reinstalled"] is True
    assert installed["called"] == 1


def test_apply_pull_failure_graceful(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import applier, git_ops
    monkeypatch.setattr(git_ops, "is_worktree_dirty", lambda: False)
    monkeypatch.setattr(git_ops, "current_commit", lambda: "old111")
    monkeypatch.setattr(git_ops, "pull", lambda branch="master": {"ok": False, "reason": "pull_failed", "stderr": "conflict"})

    result = applier.apply_update()
    assert result["applied"] is False
    assert result["reason"] == "pull_failed"

    from aletheia.files.jsonl import read_jsonl
    assert any(e.get("event_type") == "update_failed" for e in read_jsonl("audit.jsonl"))


def test_cli_update_check_smoke(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import applier
    monkeypatch.setattr(applier, "check_for_update",
                        lambda: {"ok": True, "has_update": False, "behind_count": 0,
                                 "current_commit": "abc", "remote_commit": "abc", "branch": "master"})
    from typer.testing import CliRunner
    from aletheia.cli.update_cmd import update_app
    result = CliRunner().invoke(update_app, ["check"])
    assert result.exit_code == 0


async def test_api_update_status_smoke(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import applier
    monkeypatch.setattr(applier, "check_for_update",
                        lambda: {"ok": True, "has_update": True, "behind_count": 1,
                                 "current_commit": "abc", "remote_commit": "def"})
    from httpx import AsyncClient, ASGITransport
    from aletheia.server.app import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/api/update/status")
    assert resp.status_code == 200
    assert resp.json()["behind_count"] == 1


def test_update_not_agent_callable(tmp_path, monkeypatch):
    """Governance guard: update must never be an LLM-callable tool."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime.tool_registry import init_tool_registry
    from aletheia.agent_runtime import permissions
    init_tool_registry()
    # No self-upgrade tool may ever be registered as agent-callable.
    forbidden = {"apply_update", "check_update", "check_for_update", "self_upgrade",
                 "git_pull", "update_code", "code_update"}
    assert forbidden.isdisjoint(permissions.TOOL_REGISTRY.keys())

    # tool_registry must not import the update package
    import inspect
    from aletheia.agent_runtime import tool_registry
    src = inspect.getsource(tool_registry)
    assert "aletheia.update" not in src
