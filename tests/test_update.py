"""Tests for the on-demand self-upgrade mechanism (Feature 2).

Governance: human-triggered only, never a daemon, never agent-callable. git is
fully mocked here (no network, no real pull). Verifies behind-count parsing,
state persistence, dirty-worktree refusal, conditional dependency reinstall,
graceful failure, CLI/API smoke, and the agent-isolation guard.
"""

import asyncio
import sqlite3
import threading

import pytest


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


def test_run_npm_uses_windows_cmd_shim(tmp_path, monkeypatch):
    from aletheia.update import applier

    calls = []
    monkeypatch.setattr(applier.os, "name", "nt")
    monkeypatch.setattr(
        applier.subprocess,
        "run",
        lambda command, **kwargs: calls.append(command) or _FakeProc(stdout="11.0.0\n"),
    )

    result = applier._run_npm(["--version"], env={})

    assert result.returncode == 0
    assert calls == [["npm.cmd", "--version"]]


def test_behind_count_parses_rev_list(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import git_ops
    monkeypatch.setattr(git_ops, "_git", lambda *a, **k: _FakeProc(stdout="3\n"))
    assert git_ops.behind_count("master") == 3
    monkeypatch.setattr(git_ops, "_git", lambda *a, **k: _FakeProc(stdout="0\n"))
    assert git_ops.behind_count("master") == 0


def test_pull_rejects_local_ahead_before_merge(monkeypatch):
    from aletheia.update import git_ops

    calls = []

    def fake_git(*args):
        calls.append(args)
        if args == (
            "fetch",
            "--no-write-fetch-head",
            "origin",
            "master:refs/aletheia/update/master",
        ):
            return _FakeProc()
        if args == ("rev-parse", "--verify", "refs/aletheia/update/master"):
            return _FakeProc(stdout="fetched111\n")
        if args[0] == "rev-list":
            return _FakeProc(stdout="1\n")
        pytest.fail(f"unexpected git call: {args}")

    monkeypatch.setattr(git_ops, "git_available", lambda: True)
    monkeypatch.setattr(git_ops, "_git", fake_git)

    result = git_ops.pull("master")

    assert result == {"ok": False, "reason": "local_ahead", "ahead_count": 1}
    assert not any(call[0] == "merge" for call in calls)


def test_pull_merges_to_operation_owned_ref_sha(monkeypatch):
    """The fetch target must not share FETCH_HEAD or origin/<branch> state."""
    from aletheia.update import git_ops

    calls = []

    def fake_git(*args):
        calls.append(args)
        if args == (
            "fetch",
            "--no-write-fetch-head",
            "origin",
            "master:refs/aletheia/update/master",
        ):
            return _FakeProc()
        if args == ("rev-parse", "--verify", "refs/aletheia/update/master"):
            return _FakeProc(stdout="fetched_sha\n")
        if args == ("rev-list", "--count", "fetched_sha..HEAD"):
            return _FakeProc(stdout="0\n")
        if args == ("merge", "--ff-only", "fetched_sha"):
            return _FakeProc(stdout="Fast-forward\n")
        if args == ("rev-parse", "HEAD"):
            return _FakeProc(stdout="fetched_sha\n")
        pytest.fail(f"unexpected git call: {args}")

    monkeypatch.setattr(git_ops, "git_available", lambda: True)
    monkeypatch.setattr(git_ops, "_git", fake_git)

    result = git_ops.pull("master")

    assert result["ok"] is True
    assert result["remote_commit"] == "fetched_sha"
    assert ("merge", "--ff-only", "fetched_sha") in calls
    assert not any("FETCH_HEAD" in call for call in calls)
    assert not any("origin/master" in call for call in calls)


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


def test_apply_refuses_source_checkout_on_wrong_branch(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import applier, git_ops

    monkeypatch.setattr(git_ops, "is_worktree_dirty", lambda: False)
    monkeypatch.setattr(git_ops, "current_branch", lambda: "feature/experiment")
    pulled = {"called": False}
    monkeypatch.setattr(
        git_ops, "pull", lambda branch="master": pulled.__setitem__("called", True)
    )

    result = applier.apply_update()

    assert result["applied"] is False
    assert result["reason"] == "wrong_branch"
    assert pulled["called"] is False


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



def test_cli_update_runs_full_source_deployment_without_subcommand(
    tmp_path, monkeypatch
):
    setup_env(tmp_path, monkeypatch)
    from aletheia.cli import update_cmd
    from aletheia.update import applier
    from typer.testing import CliRunner

    seen = {}

    def apply(*, restart):
        seen["restart"] = restart
        return {
            "applied": True,
            "old_commit": "old111",
            "new_commit": "new222",
            "deps_reinstalled": False,
        }

    monkeypatch.setattr(update_cmd, "_is_packaged", lambda: False)
    monkeypatch.setattr(applier, "apply_update", apply)

    result = CliRunner().invoke(update_cmd.update_app, [])

    assert result.exit_code == 0
    assert seen["restart"] is True
    assert "Updated old111 -> new222" in result.output
    assert "Gateway restarted and readiness verified." in result.output


def test_cli_update_same_source_commit_reports_noop(monkeypatch):
    from aletheia.cli import update_cmd
    from aletheia.update import continuity
    from typer.testing import CliRunner

    monkeypatch.setattr(update_cmd, "_is_packaged", lambda: False)
    monkeypatch.setattr(
        continuity,
        "apply_update_with_continuity",
        lambda *, restart: {
            "applied": True,
            "old_commit": "same111",
            "new_commit": "same111",
            "deps_reinstalled": False,
            "continuity_state": "verified",
            "continuity_verified": True,
            "continuity": {"comparison": {"main_health_status": "healthy"}},
        },
    )

    result = CliRunner().invoke(update_cmd.update_app, [])

    assert result.exit_code == 0
    assert "Already up to date at same111" in result.output
    assert "Updated same111 -> same111" not in result.output


def test_cli_update_restart_reports_runtime_version_transition(monkeypatch):
    from aletheia.cli import update_cmd
    from aletheia.update import continuity
    from typer.testing import CliRunner

    monkeypatch.setattr(update_cmd, "_is_packaged", lambda: False)
    monkeypatch.setattr(
        continuity,
        "apply_update_with_continuity",
        lambda *, restart: {
            "applied": True,
            "old_commit": "new22222",
            "new_commit": "new22222",
            "runtime_old_commit": "old11111",
            "source_changed": False,
            "deps_reinstalled": False,
            "continuity_state": "verified",
            "continuity_verified": True,
            "continuity": {"comparison": {"main_health_status": "healthy"}},
        },
    )

    result = CliRunner().invoke(update_cmd.update_app, [])

    assert result.exit_code == 0
    assert "Updated old11111 -> new22222" in result.output
    assert "Already up to date at new22222" not in result.output


def test_cli_update_same_source_commit_failure_is_not_labeled_updated(monkeypatch):
    from aletheia.cli import update_cmd
    from aletheia.update import continuity
    from typer.testing import CliRunner

    monkeypatch.setattr(update_cmd, "_is_packaged", lambda: False)
    monkeypatch.setattr(
        continuity,
        "apply_update_with_continuity",
        lambda *, restart: {
            "applied": True,
            "old_commit": "same111",
            "new_commit": "same111",
            "deps_reinstalled": False,
            "continuity_state": "failed",
            "continuity_verified": False,
            "continuity": {"comparison": {"failures": ["main_health_degraded"]}},
        },
    )

    result = CliRunner().invoke(update_cmd.update_app, [])

    assert result.exit_code == 1
    assert "Source unchanged at same111" in result.output
    assert "Updated same111 -> same111" not in result.output
    assert "Source update remains applied" not in result.output


def test_cli_update_without_subcommand_stages_packaged_release(monkeypatch):
    from aletheia.cli import update_cmd
    from aletheia.update import packaged_commands
    from typer.testing import CliRunner

    monkeypatch.setattr(update_cmd, "_is_packaged", lambda: True)
    monkeypatch.setattr(
        packaged_commands,
        "stage_update_blocking",
        lambda: {
            "accepted": True,
            "state": {
                "status": "ready",
                "phase": "ready_to_switch",
                "target_release": "0.7.0",
            },
        },
    )

    result = CliRunner().invoke(update_cmd.update_app, [])

    assert result.exit_code == 0
    assert "Release 0.7.0 is verified and migration-preflighted." in result.output


def test_cli_update_rejects_no_restart_in_packaged_mode(monkeypatch):
    from aletheia.cli import update_cmd
    from aletheia.update import packaged_commands
    from typer.testing import CliRunner

    monkeypatch.setattr(update_cmd, "_is_packaged", lambda: True)
    monkeypatch.setattr(
        packaged_commands,
        "stage_update_blocking",
        lambda: pytest.fail("packaged update must not start with --no-restart"),
    )

    result = CliRunner().invoke(update_cmd.update_app, ["--no-restart"])

    assert result.exit_code == 1
    assert "`--no-restart` is only available for source checkouts" in result.output


def test_top_level_update_help_has_no_apply_subcommand():
    from aletheia.main import app
    from typer.testing import CliRunner

    result = CliRunner().invoke(app, ["update", "--help"])

    assert result.exit_code == 0
    assert "Install the latest verified update for this runtime." in result.output
    assert "apply" not in result.output


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
    assert "[deprecated]" not in result.stderr


async def test_api_update_status_is_local_until_explicit_check(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import applier
    checks = 0

    def check_for_update():
        nonlocal checks
        checks += 1
        return {"ok": True, "has_update": True, "behind_count": 1,
                "current_commit": "abc", "remote_commit": "def"}

    monkeypatch.setattr(applier, "check_for_update", check_for_update)
    monkeypatch.setattr(
        applier,
        "update_progress",
        lambda: {"mode": "development", "status": "idle"},
    )
    from httpx import AsyncClient, ASGITransport
    from aletheia.server.app import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        status = await c.get("/api/update/status")
        checked = await c.post("/api/update/check")

    assert status.status_code == 200
    assert status.json()["reason"] == "update_check_required"
    assert checked.status_code == 200
    assert checked.json()["behind_count"] == 1
    assert checks == 1


async def test_api_update_check_does_not_block_the_event_loop(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import applier

    check_started = threading.Event()
    release_check = threading.Event()
    released_by_loop: list[bool] = []

    def blocking_check():
        check_started.set()
        released_by_loop.append(release_check.wait(timeout=0.5))
        return {"ok": True, "has_update": False, "behind_count": 0}

    monkeypatch.setattr(applier, "check_for_update", blocking_check)
    from httpx import ASGITransport, AsyncClient
    from aletheia.server.app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        request = asyncio.create_task(client.post("/api/update/check"))
        assert await asyncio.wait_for(asyncio.to_thread(check_started.wait), timeout=1.0)
        release_check.set()
        response = await request

    assert response.status_code == 200
    assert released_by_loop == [True]


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

def _stub_clean_pull(monkeypatch, pyproject_changed=False, changed_files=None):
    """Stub git_ops so apply_update reaches the smoke stage with a clean pull."""
    from aletheia.update import git_ops, applier
    commits = iter(["old111", "new222", "old111"])
    monkeypatch.setattr(git_ops, "git_available", lambda: True)
    monkeypatch.setattr(git_ops, "is_worktree_dirty", lambda: False)
    monkeypatch.setattr(git_ops, "current_commit", lambda: next(commits))
    monkeypatch.setattr(git_ops, "pull", lambda branch="master": {"ok": True})
    changed = changed_files if changed_files is not None else (["pyproject.toml"] if pyproject_changed else ["aletheia/x.py"])
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


def test_apply_restart_builds_changed_frontend_and_restarts(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import applier
    _stub_clean_pull(monkeypatch, changed_files=["frontend/src/pages/Settings.tsx"])
    monkeypatch.setattr(applier, "run_smoke_check", lambda: {"ok": True, "stage": "done", "detail": ""})
    calls = []
    monkeypatch.setattr(applier, "_build_frontend", lambda: calls.append("build") or {"ok": True})
    monkeypatch.setattr(applier, "_activate_frontend_build", lambda: calls.append("activate") or True)
    monkeypatch.setattr(applier, "_restart_gateway", lambda: calls.append("restart") or {"ok": True})

    result = applier.apply_update(restart=True)

    assert result["applied"] is True
    assert result["frontend_built"] is True
    assert result["restart_completed"] is True
    assert calls == ["build", "activate", "restart"]


def test_restart_gateway_uses_fresh_interpreter(monkeypatch):
    from aletheia.update import applier

    seen = {}

    def run(command, **kwargs):
        seen["command"] = command
        seen["cwd"] = kwargs["cwd"]
        return _FakeProc()

    monkeypatch.setattr(applier.subprocess, "run", run)

    assert applier._restart_gateway() == {"ok": True}
    assert seen["command"] == [
        applier.sys.executable,
        "-c",
        "from aletheia.gateway.lifecycle import do_start, do_stop; "
        "do_stop(); raise SystemExit(0 if do_start(no_browser=True) is not False else 2)",
    ]
    assert seen["cwd"] == str(applier._PROJECT_ROOT)

    monkeypatch.setattr(applier.subprocess, "run", lambda *_args, **_kwargs: _FakeProc(returncode=2))
    assert applier._restart_gateway()["ok"] is False


def test_apply_restart_discards_consumed_dashboard_backup(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import applier

    _stub_clean_pull(monkeypatch)
    monkeypatch.setattr(applier, "run_smoke_check", lambda: {"ok": True, "stage": "done", "detail": ""})
    backup = tmp_path / ".dashboard_static-previous"
    backup.mkdir()
    (backup / "old.html").write_text("old dashboard")
    monkeypatch.setattr(applier, "_PREVIOUS_DASHBOARD_STATIC", backup)
    monkeypatch.setattr(applier, "_build_frontend", lambda: {"ok": True})
    monkeypatch.setattr(applier, "_activate_frontend_build", lambda: True)
    monkeypatch.setattr(applier, "_restart_gateway", lambda: {"ok": True})

    result = applier.apply_update(restart=True)

    assert result["applied"] is True
    assert not backup.exists()


def test_apply_restart_restores_old_release_on_restart_failure(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import applier, git_ops
    _stub_clean_pull(monkeypatch, changed_files=["frontend/src/pages/Settings.tsx"])
    monkeypatch.setattr(applier, "run_smoke_check", lambda: {"ok": True, "stage": "done", "detail": ""})
    monkeypatch.setattr(applier, "_build_frontend", lambda: {"ok": True})
    monkeypatch.setattr(applier, "_activate_frontend_build", lambda: True)
    restored = []
    monkeypatch.setattr(applier, "_restore_frontend_build", lambda had_previous: restored.append(had_previous))
    resets = []
    monkeypatch.setattr(git_ops, "reset_hard", lambda commit: resets.append(commit) or {"ok": True})
    starts = iter([{"ok": False}, {"ok": True}])
    monkeypatch.setattr(applier, "_restart_gateway", lambda: next(starts))

    result = applier.apply_update(restart=True)

    assert result["applied"] is False
    assert result["rolled_back"] is True
    assert result["recovery_started"] is True
    assert restored == [True]
    assert resets == ["old111"]
    assert result["actual_head"] == "old111"


def test_apply_restart_keeps_new_code_after_schema_change(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import applier, git_ops

    _stub_clean_pull(
        monkeypatch,
        changed_files=["aletheia/storage/migrations.py"],
    )
    monkeypatch.setattr(applier, "run_smoke_check", lambda: {"ok": True})
    monkeypatch.setattr(applier, "_build_frontend", lambda: {"ok": True})
    monkeypatch.setattr(applier, "_activate_frontend_build", lambda: True)
    monkeypatch.setattr(applier, "_restart_gateway", lambda: {"ok": False})
    monkeypatch.setattr(
        git_ops,
        "reset_hard",
        lambda _commit: pytest.fail("schema-bearing update must not roll code back"),
    )

    result = applier.apply_update(restart=True)

    assert result["applied"] is False
    assert result["rolled_back"] is False
    assert result["reason"] == "gateway_restart_failed_after_schema_change"


def test_apply_restart_rebuilds_frontend_without_source_changes(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import applier
    _stub_clean_pull(monkeypatch)
    monkeypatch.setattr(applier, "run_smoke_check", lambda: {"ok": True, "stage": "done", "detail": ""})
    calls = []
    monkeypatch.setattr(applier, "_build_frontend", lambda: calls.append("build") or {"ok": True})
    monkeypatch.setattr(applier, "_activate_frontend_build", lambda: calls.append("activate") or True)
    monkeypatch.setattr(applier, "_restart_gateway", lambda: calls.append("restart") or {"ok": True})

    result = applier.apply_update(restart=True)

    assert result["applied"] is True
    assert result["frontend_built"] is True
    assert result["restart_completed"] is True
    assert calls == ["build", "activate", "restart"]


def test_apply_restart_rolls_back_when_frontend_build_fails(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import applier, git_ops
    _stub_clean_pull(monkeypatch, changed_files=["frontend/src/pages/Settings.tsx"])
    resets = []
    monkeypatch.setattr(git_ops, "reset_hard", lambda commit: resets.append(commit) or {"ok": True})
    monkeypatch.setattr(applier, "_build_frontend", lambda: {"ok": False, "reason": "frontend_build_failed"})
    monkeypatch.setattr(applier, "_restart_gateway", lambda: pytest.fail("gateway must not restart"))

    result = applier.apply_update(restart=True)

    assert result["applied"] is False
    assert result["reason"] == "frontend_build_failed"
    assert resets == ["old111"]


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
