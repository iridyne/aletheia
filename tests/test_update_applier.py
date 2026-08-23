"""Focused tests for #1206 (uv sync --locked) and #1207 (rollback completeness)."""
import sqlite3


def setup_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    from aletheia.storage.migrations import run_migrations
    from aletheia.update import git_ops
    sq.reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    monkeypatch.setattr(git_ops, "current_branch", lambda: "master")


class _FakeProc:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


# ── #1206: pip commands must not appear; uv sync --locked must be used ──────

def test_reinstall_deps_uses_uv_sync_locked(monkeypatch):
    from aletheia.update import applier
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _FakeProc(returncode=0)

    monkeypatch.setattr(applier.subprocess, "run", fake_run)
    result = applier._reinstall_deps()
    assert result["ok"] is True
    assert len(calls) == 1
    assert calls[0] == ["uv", "sync", "--locked"]


def test_reinstall_deps_no_pip_command(monkeypatch):
    from aletheia.update import applier
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _FakeProc(returncode=0)

    monkeypatch.setattr(applier.subprocess, "run", fake_run)
    applier._reinstall_deps()
    # No pip invocation of any kind
    for call in calls:
        assert "pip" not in call, f"pip appeared in subprocess call: {call}"


def test_reinstall_deps_failure_returns_sync_failed(monkeypatch):
    from aletheia.update import applier

    def fake_run(cmd, **kwargs):
        return _FakeProc(returncode=1)

    monkeypatch.setattr(applier.subprocess, "run", fake_run)
    result = applier._reinstall_deps()
    assert result["ok"] is False
    assert result["reason"] == "update_dependency_sync_failed"


def test_apply_update_rejects_wrong_interpreter(tmp_path, monkeypatch):
    """#1206: apply_update must return update_wrong_interpreter when not in managed venv."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import applier, git_ops
    monkeypatch.setattr(git_ops, "git_available", lambda: True)
    monkeypatch.setattr(applier, "_is_managed_venv", lambda: False)
    result = applier.apply_update()
    assert result["applied"] is False
    assert result["reason"] == "update_wrong_interpreter"


# ── #1207: rollback completeness ─────────────────────────────────────────────

def _stub_pull_to_smoke(monkeypatch, python_deps_changed=False):
    from aletheia.update import git_ops, applier
    commits = iter(["old111", "new222", "old111"])
    monkeypatch.setattr(git_ops, "git_available", lambda: True)
    monkeypatch.setattr(git_ops, "is_worktree_dirty", lambda: False)
    monkeypatch.setattr(git_ops, "current_commit", lambda: next(commits))
    monkeypatch.setattr(git_ops, "pull", lambda branch="master": {"ok": True})
    changed = ["pyproject.toml"] if python_deps_changed else ["aletheia/x.py"]
    monkeypatch.setattr(git_ops, "changed_files", lambda old, new: changed)
    monkeypatch.setattr(applier, "_reinstall_deps", lambda: {"ok": True})
    monkeypatch.setattr(applier, "_is_managed_venv", lambda: True)


def test_smoke_fail_rollback_uses_canonical_helper(tmp_path, monkeypatch):
    """#1207: smoke failure routes through _rollback_update, which reinstalls deps."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import git_ops, applier
    _stub_pull_to_smoke(monkeypatch, python_deps_changed=True)

    reset_calls = []
    reinstall_calls = []

    monkeypatch.setattr(git_ops, "reset_hard", lambda commit: reset_calls.append(commit) or {"ok": True})
    monkeypatch.setattr(applier, "_reinstall_deps", lambda: reinstall_calls.append(1) or {"ok": True})
    monkeypatch.setattr(applier, "run_smoke_check", lambda: {"ok": False, "stage": "import", "detail": "boom"})

    result = applier.apply_update()
    assert result["applied"] is False
    assert result["rolled_back"] is True
    assert reset_calls == ["old111"]
    # canonical rollback reinstalls deps when python_deps_changed
    assert reinstall_calls, "expected _reinstall_deps to be called during rollback"


def test_dep_install_fail_routes_through_canonical_rollback(tmp_path, monkeypatch):
    """#1207: dep install failure now routes through canonical rollback helper."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import git_ops, applier
    _stub_pull_to_smoke(monkeypatch, python_deps_changed=True)

    reset_calls = []
    monkeypatch.setattr(git_ops, "reset_hard", lambda commit: reset_calls.append(commit) or {"ok": True})
    # Override reinstall to fail (undo the stub set above)
    monkeypatch.setattr(applier, "_reinstall_deps", lambda: {"ok": False, "reason": "update_dependency_sync_failed"})

    result = applier.apply_update()
    assert result["applied"] is False
    # canonical rollback was executed (reset_hard called)
    assert reset_calls == ["old111"]
    assert result.get("rolled_back") is True


def test_rollback_fail_returns_rollback_failed_with_actual_head(tmp_path, monkeypatch):
    """#1207: if canonical rollback itself fails, return rollback_failed with actual_head."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import git_ops, applier
    commits_iter = iter(["old111", "new222", "stuck333"])
    monkeypatch.setattr(git_ops, "git_available", lambda: True)
    monkeypatch.setattr(git_ops, "is_worktree_dirty", lambda: False)
    monkeypatch.setattr(git_ops, "current_commit", lambda: next(commits_iter))
    monkeypatch.setattr(git_ops, "pull", lambda branch="master": {"ok": True})
    monkeypatch.setattr(git_ops, "changed_files", lambda old, new: ["aletheia/x.py"])
    monkeypatch.setattr(applier, "_reinstall_deps", lambda: {"ok": True})
    monkeypatch.setattr(applier, "_is_managed_venv", lambda: True)
    monkeypatch.setattr(git_ops, "reset_hard", lambda commit: {"ok": False, "reason": "hard_reset_error"})
    monkeypatch.setattr(applier, "run_smoke_check", lambda: {"ok": False, "stage": "import", "detail": "boom"})

    result = applier.apply_update()
    assert result["applied"] is False
    assert result["rolled_back"] is False
    assert result["reason"] == "rollback_failed"
    assert result["actual_head"] == "stuck333"
