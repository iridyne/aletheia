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
    monkeypatch.setattr(applier, "run_smoke_check", lambda: {"ok": True, "stage": "done", "detail": ""})

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
    monkeypatch.setattr(applier, "run_smoke_check", lambda: {"ok": True, "stage": "done", "detail": ""})

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


# --- Component 4: smoke verification + controlled rollback ---

def _stub_clean_pull(monkeypatch, pyproject_changed=False):
    """Stub git_ops so apply_update reaches the smoke stage with a clean pull."""
    from aletheia.update import git_ops, applier
    commits = iter(["old111", "new222"])
    monkeypatch.setattr(git_ops, "git_available", lambda: True)
    monkeypatch.setattr(git_ops, "is_worktree_dirty", lambda: False)
    monkeypatch.setattr(git_ops, "current_commit", lambda: next(commits))
    monkeypatch.setattr(git_ops, "pull", lambda branch="master": {"ok": True})
    changed = ["pyproject.toml"] if pyproject_changed else ["aletheia/x.py"]
    monkeypatch.setattr(git_ops, "changed_files", lambda old, new: changed)
    monkeypatch.setattr(applier, "_reinstall_deps", lambda: {"ok": True})


def test_apply_rolls_back_on_smoke_failure(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import git_ops, applier
    _stub_clean_pull(monkeypatch)
    reset_calls = []
    monkeypatch.setattr(git_ops, "reset_hard", lambda commit: reset_calls.append(commit) or {"ok": True})
    monkeypatch.setattr(applier, "run_smoke_check", lambda: {"ok": False, "stage": "import", "detail": "boom"})

    result = applier.apply_update()
    assert result["applied"] is False
    assert result["rolled_back"] is True
    assert reset_calls == ["old111"]  # rolled back to the pre-pull commit

    from aletheia.files.jsonl import read_jsonl
    assert any(e.get("event_type") == "update_rolled_back" for e in read_jsonl("audit.jsonl"))
    from aletheia.config.loader import load_config
    assert load_config().update.current_commit == "old111"  # restored


def test_apply_succeeds_when_smoke_passes(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import git_ops, applier
    _stub_clean_pull(monkeypatch)
    reset_calls = []
    monkeypatch.setattr(git_ops, "reset_hard", lambda commit: reset_calls.append(commit))
    monkeypatch.setattr(applier, "run_smoke_check", lambda: {"ok": True, "stage": "done", "detail": ""})

    result = applier.apply_update()
    assert result["applied"] is True
    assert result["restart_required"] is True
    assert reset_calls == []  # no rollback on success


def test_smoke_import_gate_detects_broken_module(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import smoke
    # Force the import subprocess to fail.
    import subprocess

    class _Fail:
        returncode = 1
        stdout = ""
        stderr = "ModuleNotFoundError"

    monkeypatch.setattr(smoke.subprocess, "run", lambda *a, **k: _Fail())
    result = smoke.run_smoke_check()
    assert result["ok"] is False
    assert result["stage"] == "import"


def test_apply_no_auto_restart(tmp_path, monkeypatch):
    """Neither success nor rollback may restart the gateway (user requirement)."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import git_ops, applier
    _stub_clean_pull(monkeypatch)
    monkeypatch.setattr(applier, "run_smoke_check", lambda: {"ok": True, "stage": "done", "detail": ""})
    import aletheia.gateway.lifecycle as lifecycle
    restart_calls = []
    if hasattr(lifecycle, "do_start"):
        monkeypatch.setattr(lifecycle, "do_start", lambda *a, **k: restart_calls.append(1))
    result = applier.apply_update()
    assert result["restart_required"] is True  # signals, does not act
    assert restart_calls == []


# --- Win32 console suppression: every shell-out from update routes must be silent ---
# These run from /api/update/* routes inside the inner uvicorn (a detached child of
# the supervisor), so any subprocess.run without CREATE_NO_WINDOW would flash wt.

def test_git_ops_passes_create_no_window_on_windows(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import git_ops
    from aletheia.gateway import pidfile
    monkeypatch.setattr(pidfile.platform, "system", lambda: "Windows")
    calls = {}
    def fake_run(argv, **kwargs):
        calls["argv"] = argv
        calls["kwargs"] = kwargs
        return _FakeProc(stdout="abc1234\n", returncode=0)
    monkeypatch.setattr(git_ops.subprocess, "run", fake_run)
    git_ops.current_commit()
    assert calls["argv"][0] == "git"
    assert calls["kwargs"].get("creationflags", 0) & 0x08000000 == 0x08000000


def test_git_ops_no_creationflags_on_posix(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import git_ops
    from aletheia.gateway import pidfile
    monkeypatch.setattr(pidfile.platform, "system", lambda: "Linux")
    calls = {}
    def fake_run(argv, **kwargs):
        calls["argv"] = argv
        calls["kwargs"] = kwargs
        return _FakeProc(stdout="abc1234\n", returncode=0)
    monkeypatch.setattr(git_ops.subprocess, "run", fake_run)
    git_ops.current_commit()
    assert "creationflags" not in calls["kwargs"]


def test_reinstall_deps_passes_create_no_window_on_windows(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import applier
    from aletheia.gateway import pidfile
    monkeypatch.setattr(pidfile.platform, "system", lambda: "Windows")
    calls = {}
    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        calls["kwargs"] = kwargs
        return _FakeProc(stdout="", returncode=0)
    monkeypatch.setattr(applier.subprocess, "run", fake_run)
    applier._reinstall_deps()
    assert calls["kwargs"].get("creationflags", 0) & 0x08000000 == 0x08000000


def test_smoke_import_gate_passes_create_no_window_on_windows(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import smoke
    from aletheia.gateway import pidfile
    # Force the helper's platform check to Windows even though the test runs on
    # whatever the host is (helper reads pidfile's platform module).
    monkeypatch.setattr(pidfile.platform, "system", lambda: "Windows")
    calls = {}
    def fake_run(cmd, **kwargs):
        calls.setdefault("kwargs_list", []).append(kwargs)
        return _FakeProc(stdout="", returncode=0)
    monkeypatch.setattr(smoke.subprocess, "run", fake_run)
    smoke.run_smoke_check()
    # Both the import gate (python -c) and the fast-tests gate (python -m pytest)
    # must include CREATE_NO_WINDOW.
    assert calls["kwargs_list"], "expected at least one subprocess call"
    for kw in calls["kwargs_list"]:
        assert kw.get("creationflags", 0) & 0x08000000 == 0x08000000
