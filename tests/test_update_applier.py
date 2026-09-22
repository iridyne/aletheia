"""Focused tests for #1206 (uv sync --locked) and #1207 (rollback completeness)."""
import sqlite3

import pytest


@pytest.mark.parametrize("migration_path", ["aletheia/storage/migrations.py", "core/aletheia/storage/migrations.py"])
def test_layout_transition_and_new_schema_path_retain_backup_gate(tmp_path, monkeypatch, migration_path):
    from types import SimpleNamespace
    from ops.update import applier, auto, git_ops as mutations
    from ops.backup import service
    from aletheia.update import git_ops
    setup_env(tmp_path, monkeypatch)
    _stub_pull_to_smoke(monkeypatch)
    monkeypatch.setattr(git_ops, "changed_files", lambda *a: [migration_path])
    monkeypatch.setattr(mutations, "reset_hard", lambda *a: {"ok": True})
    calls = []
    monkeypatch.setattr(service, "BackupService", lambda *a: SimpleNamespace(create_backup=lambda **kw: calls.append(kw["label"]) or tmp_path / "fake.tar"))

    def stop_at_candidate(*args):
        raise auto.AutoUpdateError("fixture_candidate_stop", "no actual update")

    monkeypatch.setattr(auto, "_candidate_preflight", stop_at_candidate)
    monkeypatch.setattr(auto, "_remove_candidate", lambda *a: None)
    assert applier.apply_update(restart=True)["reason"] == "fixture_candidate_stop"
    assert calls == ["interactive_schema_preflight"]


def test_dashboard_layout_activation_and_rollback(tmp_path, monkeypatch):
    from ops.update import applier
    for attr, name in (("_DASHBOARD_STATIC", "dashboard_static"), ("_STAGED_DASHBOARD_STATIC", ".dashboard_static-next"), ("_PREVIOUS_DASHBOARD_STATIC", ".dashboard_static-previous")):
        assert getattr(applier, attr) == applier._PROJECT_ROOT / "core/aletheia" / name
        monkeypatch.setattr(applier, attr, tmp_path / "core/aletheia" / name)
    active, staged = applier._DASHBOARD_STATIC, applier._STAGED_DASHBOARD_STATIC
    for path, text in ((active, "old"), (staged, "new"), (tmp_path / "aletheia/dashboard_static", "untouched")):
        path.mkdir(parents=True)
        (path / "index.html").write_text(text)
    def fake_build(command, *, env):
        from types import SimpleNamespace
        assert env["VITE_BUILD_OUT_DIR"] == str(staged)
        staged.mkdir()
        (staged / "index.html").write_text("new")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(applier, "_run_npm", fake_build)
    monkeypatch.setattr(applier.target_fresh, "validate_dashboard_artifacts", lambda *a, **kw: {"ok": True})
    assert applier._build_frontend("fixture-tree")["ok"]
    previous = applier._activate_frontend_build()
    assert (active / "index.html").read_text() == "new"
    applier._restore_frontend_build(previous)
    assert (active / "index.html").read_text() == "old"
    assert (tmp_path / "aletheia/dashboard_static/index.html").read_text() == "untouched"


def test_ordinary_core_change_does_not_enter_schema_gate(tmp_path, monkeypatch):
    from ops.update import applier
    from aletheia.update import git_ops
    setup_env(tmp_path, monkeypatch)
    _stub_pull_to_smoke(monkeypatch)
    monkeypatch.setattr(git_ops, "changed_files", lambda *a: ["core/aletheia/analytics/trade_stats.py"])
    monkeypatch.setattr(applier, "run_smoke_check", lambda: {"ok": False, "stage": "fixture", "detail": "stop"})
    monkeypatch.setattr(applier, "_rollback_update", lambda *a, **kw: {"reason": kw["failure_reason"]})
    assert applier.apply_update()["reason"] == "smoke_failed"


@pytest.mark.parametrize("missing", [False, True])
def test_lifecycle_child_selects_target_core_without_starting_services(tmp_path, monkeypatch, missing):
    from ops.update import applier
    target = tmp_path / "target"
    target.mkdir()
    if not missing:
        package = target / "core/aletheia/gateway"
        package.mkdir(parents=True)
        (package.parent / "__init__.py").write_text("")
        (package / "__init__.py").write_text("")
        (package / "lifecycle.py").write_text("def do_stop(): pass\ndef do_start(**kwargs): return True\n")
    monkeypatch.setattr(applier, "_PROJECT_ROOT", target)
    # Existing healthy service must not turn a missing target source into success.
    monkeypatch.setattr(applier, "_gateway_ready_after_restart_error", lambda: True)
    assert applier._stop_gateway()["ok"] is (not missing)
    assert applier._restart_gateway()["ok"] is (not missing)

@pytest.fixture(autouse=True)
def _development_checkout(monkeypatch):
    import sys

    monkeypatch.setattr(sys, "platform", "darwin")


def setup_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    from aletheia.storage.migrations import run_migrations
    from aletheia.update import git_ops
    from ops.update import git_ops as git_mutations
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
    from ops.update import applier
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
    from ops.update import applier
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
    from ops.update import applier

    def fake_run(cmd, **kwargs):
        return _FakeProc(returncode=1)

    monkeypatch.setattr(applier.subprocess, "run", fake_run)
    result = applier._reinstall_deps()
    assert result["ok"] is False
    assert result["reason"] == "update_dependency_sync_failed"


def test_apply_update_rejects_wrong_interpreter(tmp_path, monkeypatch):
    """#1206: apply_update must return update_wrong_interpreter when not in managed venv."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import git_ops
    from ops.update import applier, git_ops as git_mutations
    monkeypatch.setattr(git_ops, "git_available", lambda: True)
    monkeypatch.setattr(applier, "_is_managed_venv", lambda: False)
    result = applier.apply_update()
    assert result["applied"] is False
    assert result["reason"] == "update_wrong_interpreter"


def test_managed_venv_accepts_external_python_launcher(tmp_path, monkeypatch):
    from ops.update import applier

    monkeypatch.setattr(applier, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(applier.sys, "prefix", str(tmp_path / ".venv"))
    monkeypatch.setattr(applier.sys, "executable", "/usr/bin/python3")

    assert applier._is_managed_venv() is True


def test_managed_venv_rejects_foreign_prefix(tmp_path, monkeypatch):
    from ops.update import applier

    monkeypatch.setattr(applier, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(applier.sys, "prefix", str(tmp_path / "foreign-venv"))
    monkeypatch.setattr(applier.sys, "executable", str(tmp_path / ".venv" / "bin" / "python"))

    assert applier._is_managed_venv() is False


# ── #1207: rollback completeness ─────────────────────────────────────────────

def _stub_pull_to_smoke(monkeypatch, python_deps_changed=False):
    from aletheia.update import git_ops
    from ops.update import git_ops as git_mutations, applier
    commits = iter(["old111", "new222", "old111"])
    monkeypatch.setattr(git_ops, "git_available", lambda: True)
    monkeypatch.setattr(git_ops, "is_worktree_dirty", lambda: False)
    monkeypatch.setattr(git_ops, "current_commit", lambda: next(commits))
    monkeypatch.setattr(git_mutations, "pull", lambda branch="master": {"ok": True})
    changed = ["pyproject.toml"] if python_deps_changed else ["aletheia/x.py"]
    monkeypatch.setattr(git_ops, "changed_files", lambda old, new: changed)
    monkeypatch.setattr(applier, "_reinstall_deps", lambda: {"ok": True})
    monkeypatch.setattr(applier, "_is_managed_venv", lambda: True)


def test_smoke_fail_rollback_uses_canonical_helper(tmp_path, monkeypatch):
    """#1207: smoke failure routes through _rollback_update, which reinstalls deps."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import git_ops
    from ops.update import git_ops as git_mutations, applier
    _stub_pull_to_smoke(monkeypatch, python_deps_changed=True)

    reset_calls = []
    reinstall_calls = []

    monkeypatch.setattr(git_mutations, "reset_hard", lambda commit: reset_calls.append(commit) or {"ok": True})
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
    from aletheia.update import git_ops
    from ops.update import git_ops as git_mutations, applier
    _stub_pull_to_smoke(monkeypatch, python_deps_changed=True)

    reset_calls = []
    monkeypatch.setattr(git_mutations, "reset_hard", lambda commit: reset_calls.append(commit) or {"ok": True})
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
    from aletheia.update import git_ops
    from ops.update import git_ops as git_mutations, applier
    commits_iter = iter(["old111", "new222", "stuck333"])
    monkeypatch.setattr(git_ops, "git_available", lambda: True)
    monkeypatch.setattr(git_ops, "is_worktree_dirty", lambda: False)
    monkeypatch.setattr(git_ops, "current_commit", lambda: next(commits_iter))
    monkeypatch.setattr(git_mutations, "pull", lambda branch="master": {"ok": True})
    monkeypatch.setattr(git_ops, "changed_files", lambda old, new: ["aletheia/x.py"])
    monkeypatch.setattr(applier, "_reinstall_deps", lambda: {"ok": True})
    monkeypatch.setattr(applier, "_is_managed_venv", lambda: True)
    monkeypatch.setattr(git_mutations, "reset_hard", lambda commit: {"ok": False, "reason": "hard_reset_error"})
    monkeypatch.setattr(applier, "run_smoke_check", lambda: {"ok": False, "stage": "import", "detail": "boom"})

    result = applier.apply_update()
    assert result["applied"] is False
    assert result["rolled_back"] is False
    assert result["reason"] == "rollback_failed"
    assert result["actual_head"] == "stuck333"
