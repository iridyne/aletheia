"""Tests for the on-demand self-upgrade mechanism (Feature 2).

Governance: human-triggered only, never a daemon, never agent-callable. git is
fully mocked here (no network, no real pull). Verifies behind-count parsing,
state persistence, dirty-worktree refusal, conditional dependency reinstall,
graceful failure, CLI/API smoke, and the agent-isolation guard.
"""

import asyncio
import sqlite3
import threading

import psutil
import pytest
from typer.testing import CliRunner

# psutil resolves its platform module at import time from `sys.platform`. The
# autouse fixture below fakes the platform to "darwin" for every test in this
# file (these exercise the on-demand update path as a development checkout), so a
# later lazy `import psutil` inside the code under test would look for the macOS
# build and fail on Linux. Importing it here, first, pins the real build.
# Reference: the failure was `ImportError: cannot import name '_psutil_osx' from
# partially initialized module 'psutil'` in test_apply_no_auto_restart.


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
    from ops.update import applier

    monkeypatch.setattr(applier, "_is_managed_venv", lambda: True)


class _FakeProc:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


def test_update_verbose_prints_phase_and_total_timings(monkeypatch):
    from aletheia.cli import update_cmd
    from ops.update import continuity

    monkeypatch.setattr(
        continuity,
        "apply_update_with_continuity",
        lambda **_kwargs: {
            "applied": True,
            "old_commit": "old111",
            "new_commit": "new222",
            "source_changed": True,
            "deps_reinstalled": False,
            "continuity_verified": True,
            "continuity_state": "verified",
            "continuity": {
                "comparison": {},
                "timings_ms": {"phases": {"Fetching latest commits": 12}, "total": 34},
            },
        },
    )

    result = CliRunner().invoke(update_cmd.update_app, ["-v"])

    assert result.exit_code == 0
    assert "Fetching latest commits: 12 ms" in result.stdout
    assert "Total: 34 ms" in result.stdout


def test_run_npm_uses_windows_cmd_shim(tmp_path, monkeypatch):
    from ops.update import applier

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
    from ops.update import git_ops as git_mutations

    monkeypatch.setattr(git_ops, "_git", lambda *a, **k: _FakeProc(stdout="3\n"))
    assert git_ops.behind_count("master") == 3
    monkeypatch.setattr(git_ops, "_git", lambda *a, **k: _FakeProc(stdout="0\n"))
    assert git_ops.behind_count("master") == 0


def test_changed_files_returns_unknown_on_git_failure(monkeypatch):
    from aletheia.update import git_ops
    from ops.update import git_ops as git_mutations

    monkeypatch.setattr(git_ops, "_git", lambda *a, **k: _FakeProc(returncode=1, stderr="bad revision"))

    assert git_ops.changed_files("old", "new") is None


@pytest.mark.parametrize(
    ("objects", "expected", "paths"),
    [
        ({"core/aletheia/__init__.py": '__version__ = "1.1.0"'},
         '__version__ = "1.1.0"', ["core/aletheia/__init__.py"]),
        ({"aletheia/__init__.py": '__version__ = "1.0.0"'},
         '__version__ = "1.0.0"', ["core/aletheia/__init__.py", "aletheia/__init__.py"]),
        ({"core/aletheia/__init__.py": "", "aletheia/__init__.py": '__version__ = "1.0.0"'},
         "", ["core/aletheia/__init__.py"]),
        ({}, "", ["core/aletheia/__init__.py", "aletheia/__init__.py"]),
    ],
)
def test_version_at_ref_reads_current_and_historical_git_objects(monkeypatch, objects, expected, paths):
    from aletheia.update import git_ops

    calls = []

    def fake_git(*args):
        calls.append(args)
        path = args[1].split(":", 1)[1]
        return _FakeProc(stdout=objects.get(path, ""), returncode=0 if path in objects else 128)

    monkeypatch.setattr(git_ops, "_git", fake_git)

    assert git_ops.version_at_ref("source-sha") == expected
    assert calls == [("show", f"source-sha:{path}") for path in paths]


def test_git_timeout_is_reported_as_unknown_state(monkeypatch):
    from aletheia.update import git_ops
    from ops.update import git_ops as git_mutations
    import subprocess

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["git"], 60)

    monkeypatch.setattr(git_ops.subprocess, "run", timeout)

    assert git_ops.current_commit() == ""
    assert git_ops.changed_files("old", "new") is None


def _stub_timed_out_git_mutation(monkeypatch, tmp_path, *, mutation, heads):
    from aletheia.update import git_ops
    from ops.update import git_ops as git_mutations

    head_values = iter(heads)

    def fake_git(*args):
        if args == mutation:
            return _FakeProc(returncode=124, stderr="git command exceeded 60s")
        if args == ("rev-parse", "HEAD"):
            return _FakeProc(stdout=f"{next(head_values)}\n")
        if args == ("status", "--porcelain"):
            return _FakeProc()
        if args == ("merge-base", "--is-ancestor", "old111", "new222"):
            return _FakeProc()
        if args == ("rev-parse", "--git-dir"):
            return _FakeProc(stdout=f"{tmp_path}\n")
        if args == (
            "fetch",
            "--no-write-fetch-head",
            "origin",
            "master:refs/aletheia/update/master",
        ):
            return _FakeProc()
        if args == ("rev-parse", "--verify", "refs/aletheia/update/master"):
            return _FakeProc(stdout="new222\n")
        if args == ("rev-list", "--count", "new222..HEAD"):
            return _FakeProc(stdout="0\n")
        pytest.fail(f"unexpected git call: {args}")

    monkeypatch.setattr(git_ops, "_git", fake_git)


def test_merge_timeout_reconciles_when_target_head_is_clean(tmp_path, monkeypatch):
    from aletheia.update import git_ops
    from ops.update import git_ops as git_mutations

    _stub_timed_out_git_mutation(
        monkeypatch,
        tmp_path,
        mutation=("merge", "--ff-only", "new222"),
        heads=["old111", "new222"],
    )

    result = git_mutations.merge_fast_forward("new222")

    assert result["ok"] is True
    assert result["reason"] == "git_mutation_timeout_reconciled"
    assert result["current_commit"] == "new222"


def test_merge_timeout_with_active_git_operation_stays_unknown(tmp_path, monkeypatch):
    from aletheia.update import git_ops
    from ops.update import git_ops as git_mutations

    (tmp_path / "MERGE_HEAD").write_text("unresolved\n", encoding="utf-8")
    _stub_timed_out_git_mutation(
        monkeypatch,
        tmp_path,
        mutation=("merge", "--ff-only", "new222"),
        heads=["old111", "new222"],
    )

    result = git_mutations.merge_fast_forward("new222")

    assert result["ok"] is False
    assert result["reason"] == "git_mutation_state_unknown"
    assert "MERGE_HEAD" in result["detail"]


def test_pull_timeout_reports_unknown_when_target_head_did_not_land(tmp_path, monkeypatch):
    from aletheia.update import git_ops
    from ops.update import git_ops as git_mutations

    _stub_timed_out_git_mutation(
        monkeypatch,
        tmp_path,
        mutation=("merge", "--ff-only", "new222"),
        heads=["old111", "old111"],
    )
    monkeypatch.setattr(git_ops, "git_available", lambda: True)

    result = git_mutations.pull("master")

    assert result["ok"] is False
    assert result["reason"] == "git_mutation_state_unknown"
    assert result["current_commit"] == "old111"


def test_reset_timeout_reconciles_when_rollback_head_is_clean(tmp_path, monkeypatch):
    from aletheia.update import git_ops
    from ops.update import git_ops as git_mutations

    _stub_timed_out_git_mutation(
        monkeypatch,
        tmp_path,
        mutation=("reset", "--hard", "old111"),
        heads=["old111"],
    )

    result = git_mutations.reset_hard("old111")

    assert result["ok"] is True
    assert result["reason"] == "git_mutation_timeout_reconciled"
    assert result["current_commit"] == "old111"


def test_pull_rejects_local_ahead_before_merge(monkeypatch):
    from aletheia.update import git_ops
    from ops.update import git_ops as git_mutations

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

    result = git_mutations.pull("master")

    assert result == {"ok": False, "reason": "local_ahead", "ahead_count": 1}
    assert not any(call[0] == "merge" for call in calls)


def test_pull_merges_to_operation_owned_ref_sha(monkeypatch):
    """The fetch target must not share FETCH_HEAD or origin/<branch> state."""
    from aletheia.update import git_ops
    from ops.update import git_ops as git_mutations

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

    result = git_mutations.pull("master")

    assert result["ok"] is True
    assert result["remote_commit"] == "fetched_sha"
    assert ("merge", "--ff-only", "fetched_sha") in calls
    assert not any("FETCH_HEAD" in call for call in calls)
    assert not any("origin/master" in call for call in calls)


def test_check_for_update_writes_last_check(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import git_ops
    from ops.update import applier, git_ops as git_mutations

    monkeypatch.setattr(git_mutations, "fetch", lambda branch="master": {"ok": True})
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

    cfg = load_config()
    cfg.update.enabled = False
    save_config(cfg)
    from ops.update import applier

    result = applier.check_for_update()
    assert result["ok"] is False
    assert result["reason"] == "update_disabled"


def test_check_rejects_unknown_remote_state(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import git_ops
    from ops.update import applier, git_ops as git_mutations

    monkeypatch.setattr(git_mutations, "fetch", lambda branch="master": {"ok": True})
    monkeypatch.setattr(git_ops, "current_commit", lambda: "old111")
    monkeypatch.setattr(git_ops, "remote_commit", lambda branch="master": "")
    monkeypatch.setattr(git_ops, "behind_count", lambda branch="master": -1)

    result = applier.check_for_update()

    assert result == {
        "ok": False,
        "reason": "remote_state_unavailable",
        "mode": "development",
    }


def test_update_child_env_strips_provider_and_updater_credentials(monkeypatch):
    from aletheia.update.process_env import safe_env

    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("ALETHEIA_UPDATE_BEARER_TOKEN", "token")
    monkeypatch.setenv("ALETHEIA_OPERATOR_SERVICE_TOKEN", "operator-token")
    monkeypatch.setenv("ALETHEIA_DATA_DIR", "/tmp/safe-data")

    env = safe_env()

    assert "OPENAI_API_KEY" not in env
    assert "ALETHEIA_UPDATE_BEARER_TOKEN" not in env
    assert "ALETHEIA_OPERATOR_SERVICE_TOKEN" not in env
    assert env["ALETHEIA_DATA_DIR"] == "/tmp/safe-data"


def test_apply_refuses_dirty_worktree(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import git_ops
    from ops.update import applier, git_ops as git_mutations

    monkeypatch.setattr(git_ops, "is_worktree_dirty", lambda: True)
    pulled = {"called": False}
    monkeypatch.setattr(git_mutations, "pull", lambda branch="master": pulled.__setitem__("called", True))

    result = applier.apply_update()
    assert result["applied"] is False
    assert result["reason"] == "dirty_worktree"
    assert pulled["called"] is False

    from aletheia.files.jsonl import read_jsonl

    assert any(e.get("event_type") == "update_blocked" for e in read_jsonl("audit.jsonl"))


def test_apply_refuses_source_checkout_on_wrong_branch(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import git_ops
    from ops.update import applier, git_ops as git_mutations

    monkeypatch.setattr(git_ops, "is_worktree_dirty", lambda: False)
    monkeypatch.setattr(git_ops, "current_branch", lambda: "feature/experiment")
    pulled = {"called": False}
    monkeypatch.setattr(git_mutations, "pull", lambda branch="master": pulled.__setitem__("called", True))

    result = applier.apply_update()

    assert result["applied"] is False
    assert result["reason"] == "wrong_branch"
    assert pulled["called"] is False


def test_apply_skips_deps_when_pyproject_unchanged(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import git_ops
    from ops.update import applier, git_ops as git_mutations

    monkeypatch.setattr(git_ops, "is_worktree_dirty", lambda: False)
    monkeypatch.setattr(git_ops, "current_commit", lambda: "old111")
    monkeypatch.setattr(git_mutations, "pull", lambda branch="master": {"ok": True})
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
    from aletheia.update import git_ops
    from ops.update import applier, git_ops as git_mutations

    monkeypatch.setattr(git_ops, "is_worktree_dirty", lambda: False)
    commits = iter(["old111", "new222"])
    monkeypatch.setattr(git_ops, "current_commit", lambda: next(commits))
    monkeypatch.setattr(git_mutations, "pull", lambda branch="master": {"ok": True})
    monkeypatch.setattr(git_ops, "changed_files", lambda old, new: ["pyproject.toml", "aletheia/x.py"])
    installed = {"called": 0}
    monkeypatch.setattr(
        applier, "_reinstall_deps", lambda: installed.__setitem__("called", installed["called"] + 1) or {"ok": True}
    )
    monkeypatch.setattr(applier, "run_smoke_check", lambda: {"ok": True, "stage": "done", "detail": ""})

    result = applier.apply_update()
    assert result["applied"] is True
    assert result["deps_reinstalled"] is True
    assert installed["called"] == 1


def test_apply_pull_failure_graceful(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import git_ops
    from ops.update import applier, git_ops as git_mutations

    monkeypatch.setattr(git_ops, "is_worktree_dirty", lambda: False)
    monkeypatch.setattr(git_ops, "current_commit", lambda: "old111")
    monkeypatch.setattr(
        git_mutations, "pull", lambda branch="master": {"ok": False, "reason": "pull_failed", "stderr": "conflict"}
    )

    result = applier.apply_update()
    assert result["applied"] is False
    assert result["reason"] == "pull_failed"

    from aletheia.files.jsonl import read_jsonl

    assert any(e.get("event_type") == "update_failed" for e in read_jsonl("audit.jsonl"))


def test_apply_fails_closed_when_changed_files_cannot_be_classified(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import git_ops
    from ops.update import applier, git_ops as git_mutations

    _stub_clean_pull(monkeypatch)
    monkeypatch.setattr(git_ops, "changed_files", lambda *_args: None)
    reset_calls = []
    monkeypatch.setattr(git_mutations, "reset_hard", lambda commit: reset_calls.append(commit) or {"ok": True})
    monkeypatch.setattr(applier, "_persist_current_commit_fresh", lambda _commit: {"ok": True})
    monkeypatch.setattr(applier, "run_smoke_check", lambda: pytest.fail("unknown diff must not reach smoke"))

    result = applier.apply_update()

    assert result["applied"] is False
    assert result["rolled_back"] is True
    assert result["reason"] == "changed_files_unavailable"
    assert reset_calls == ["old111"]
    from aletheia.files.jsonl import read_jsonl

    events = read_jsonl("audit.jsonl")
    assert any(
        event.get("event_type") == "update_failed" and "changed_files_unavailable" in event.get("message", "")
        for event in events
    )


def test_schema_candidate_failure_is_durably_audited(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from ops.backup import service as backup_service
    from aletheia.update import git_ops
    from ops.update import applier, auto, git_ops as git_mutations

    _stub_clean_pull(monkeypatch, changed_files=["core/aletheia/storage/migrations.py"])
    monkeypatch.setattr(git_mutations, "reset_hard", lambda _commit: {"ok": True})
    monkeypatch.setattr(
        backup_service,
        "BackupService",
        lambda _data_dir: type("Backup", (), {"create_backup": lambda self, *, label: tmp_path / label})(),
    )
    monkeypatch.setattr(
        auto,
        "_candidate_preflight",
        lambda *_args: (_ for _ in ()).throw(
            auto.AutoUpdateError("candidate_preflight_failed", "smoke_stage=fast_tests; smoke_detail=route mismatch")
        ),
    )

    result = applier.apply_update(restart=True)

    assert result["applied"] is False
    assert result["reason"] == "candidate_preflight_failed"
    assert "smoke_stage=fast_tests" in result["message"]
    from aletheia.files.jsonl import read_jsonl

    events = read_jsonl("audit.jsonl")
    assert any(
        event.get("event_type") == "update_failed" and "route mismatch" in event.get("message", "")
        for event in events
    )


def test_schema_preflight_source_restore_retries_and_reports_actual_head(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import git_ops
    from ops.update import applier, git_ops as git_mutations

    _stub_clean_pull(monkeypatch, changed_files=["core/aletheia/storage/migrations.py"])
    heads = iter(["old111", "new222", "unverified333"])
    monkeypatch.setattr(git_ops, "current_commit", lambda: next(heads))
    resets = []
    monkeypatch.setattr(
        git_mutations,
        "reset_hard",
        lambda commit: resets.append(commit) or {"ok": False, "reason": "reset_failed", "stderr": "index locked"},
    )

    result = applier.apply_update(restart=True)

    assert result["applied"] is False
    assert result["reason"] == "schema_preflight_source_restore_failed"
    assert result["actual_head"] == "unverified333"
    assert resets == ["old111", "old111"]
    from aletheia.files.jsonl import read_jsonl

    events = read_jsonl("audit.jsonl")
    assert any(
        event.get("event_type") == "update_failed"
        and event.get("after", {}).get("actual_head") == "unverified333"
        for event in events
    )


def _stopped_runtime_update(tmp_path, monkeypatch, *, changed_files):
    """Drive the real CLI update path with the runtime stopped and record lifecycle calls."""
    setup_env(tmp_path, monkeypatch)
    from extensions.observation_lab import runner
    from ops.update import applier, continuity, git_ops as git_mutations

    class _Barrier:
        def __enter__(self):
            return {"ok": True, "checkpoint": {"current_fingerprint": "trusted"}}

        def __exit__(self, *_exc):
            return False

    _stub_clean_pull(monkeypatch, changed_files=changed_files)
    calls = []
    monkeypatch.setattr(continuity, "capture_update_continuity", lambda: {})
    monkeypatch.setattr(continuity, "evaluate_preflight", lambda _before: {"ok": True, "failures": []})
    monkeypatch.setattr(runner, "observation_settlement_barrier", _Barrier)
    monkeypatch.setattr(applier, "_stop_gateway", lambda: calls.append(("stop",)) or {"ok": True})
    monkeypatch.setattr(applier, "_restart_gateway", lambda: calls.append(("restart",)) or {"ok": True})
    monkeypatch.setattr(git_mutations, "reset_hard", lambda commit: calls.append(("reset", commit)) or {"ok": True})
    monkeypatch.setattr(applier, "_persist_current_commit_fresh", lambda commit: {"ok": True})
    return calls


def test_candidate_adding_a_name_to_an_imported_module_cannot_strand_the_runtime(tmp_path, monkeypatch):
    """#3856: Fedora 1cea7366 -> aa09a87c stopped the runtime, pulled, then imported
    pulled `aletheia/backup/verify.py` (which needs `runtime_paths.large_temp_dir`, added by
    #3835) into a process whose `aletheia.runtime_paths` was the pre-pull module. The
    ImportError escaped and left the Gateway stopped while status still said up_to_date."""
    import importlib.abc
    import importlib.util
    import sys

    from aletheia import runtime_paths
    from ops.update import auto, continuity

    calls = _stopped_runtime_update(tmp_path, monkeypatch, changed_files=["core/aletheia/storage/migrations.py"])
    assert not hasattr(runtime_paths, "candidate_only_temp_dir")

    class _CandidateCheckout(importlib.abc.MetaPathFinder, importlib.abc.Loader):
        """What the source tree holds after the pull: a verify.py that needs a new name."""

        def find_spec(self, name, path=None, target=None):
            return importlib.util.spec_from_loader(name, self) if name == "aletheia.backup.verify" else None

        def create_module(self, spec):
            return None

        def exec_module(self, module):
            exec("from aletheia.runtime_paths import candidate_only_temp_dir\n", module.__dict__)

    def pull(branch="master"):
        # A fresh `aletheia update` process has not imported the preflight modules yet.
        for name in ("ops.update.auto", "ops.backup.restore", "aletheia.backup.verify"):
            monkeypatch.delitem(sys.modules, name, raising=False)
        monkeypatch.setattr(sys, "meta_path", [_CandidateCheckout(), *sys.meta_path])
        return {"ok": True}

    from ops.backup import service as backup_service
    from ops.update import git_ops as git_mutations

    monkeypatch.setattr(git_mutations, "pull", pull)
    monkeypatch.setattr(
        backup_service,
        "BackupService",
        lambda _data_dir: type("Backup", (), {"create_backup": lambda self, *, label: tmp_path / label})(),
    )
    monkeypatch.setattr(
        auto,
        "_candidate_preflight",
        lambda *_args: (_ for _ in ()).throw(auto.AutoUpdateError("candidate_preflight_failed", "candidate smoke failed")),
    )

    result = continuity.apply_update_with_continuity(restart=True)

    assert result["applied"] is False
    assert result["reason"] == "candidate_preflight_failed"
    assert calls == [("stop",), ("reset", "old111"), ("restart",)]
    from aletheia.files.jsonl import read_jsonl

    assert any(
        event.get("event_type") == "update_failed" and "candidate smoke failed" in event.get("message", "")
        for event in read_jsonl("audit.jsonl")
    )


def test_dashboard_build_check_does_not_import_the_pulled_preflight(tmp_path, monkeypatch):
    """#3856 follow-up: the post-pull dashboard check must use the preflight bound before
    the pull, not import the candidate's `aletheia.gateway.preflight` against pre-pull modules."""
    import importlib.abc
    import importlib.util
    import sys

    from ops.update import applier

    static = tmp_path / "dashboard_static"
    (static / ".vite").mkdir(parents=True)
    (static / "assets").mkdir()
    (static / ".aletheia-frontend-tree").write_text("tree-aa09a87c\n", encoding="utf-8")
    (static / ".vite" / "manifest.json").write_text("{}", encoding="utf-8")
    (static / "performance.json").write_text("{}", encoding="utf-8")
    (static / "assets" / "index.js").write_text("x", encoding="utf-8")
    (static / "index.html").write_text("x", encoding="utf-8")
    monkeypatch.setattr(applier, "_DASHBOARD_STATIC", static)

    class _CandidatePreflight(importlib.abc.MetaPathFinder, importlib.abc.Loader):
        def find_spec(self, name, path=None, target=None):
            return importlib.util.spec_from_loader(name, self) if name == "aletheia.gateway.preflight" else None

        def create_module(self, spec):
            return None

        def exec_module(self, module):
            exec("from aletheia.runtime_paths import candidate_only_temp_dir\n", module.__dict__)

    # What the process looks like right after the pull.
    monkeypatch.delitem(sys.modules, "aletheia.gateway.preflight", raising=False)
    monkeypatch.setattr(sys, "meta_path", [_CandidatePreflight(), *sys.meta_path])

    assert applier._dashboard_build_required("tree-aa09a87c") is False


def test_unexpected_exception_after_the_pull_rolls_back_and_restarts_the_old_runtime(tmp_path, monkeypatch):
    """#3856: no exception between the pull and activation may leave the stopped runtime down."""
    from ops.update import applier, continuity

    calls = _stopped_runtime_update(tmp_path, monkeypatch, changed_files=["frontend/src/App.tsx"])

    def candidate_import(_tree):
        raise ImportError("cannot import name 'candidate_only_name' from 'aletheia.runtime_paths'")

    monkeypatch.setattr(applier, "_dashboard_build_required", candidate_import)
    monkeypatch.setattr(applier, "_build_frontend", lambda _tree: pytest.fail("must not build after the failure"))

    result = continuity.apply_update_with_continuity(restart=True)

    assert result["applied"] is False
    assert result["rolled_back"] is True
    assert result["reason"] == "unexpected_ImportError"
    assert calls == [("stop",), ("reset", "old111"), ("restart",)]
    from aletheia.files.jsonl import read_jsonl

    assert any(
        event.get("event_type") == "update_rolled_back" and "candidate_only_name" in event.get("message", "")
        for event in read_jsonl("audit.jsonl")
    )


def test_continuity_restarts_the_stopped_runtime_when_the_applier_raises(tmp_path, monkeypatch):
    from ops.update import applier, continuity

    calls = _stopped_runtime_update(tmp_path, monkeypatch, changed_files=[])
    monkeypatch.setattr(
        applier, "apply_update", lambda **_kwargs: (_ for _ in ()).throw(OSError("audit disk full"))
    )

    result = continuity.apply_update_with_continuity(restart=True)

    assert result["applied"] is False
    assert result["reason"] == "unexpected_OSError"
    assert calls == [("stop",), ("restart",)]


def test_cli_update_runs_full_source_deployment_without_subcommand(monkeypatch):
    from aletheia.cli import update_cmd
    from ops.update import continuity
    from typer.testing import CliRunner

    seen = {}

    def apply(*, restart, progress=None):
        seen["restart"] = restart
        return {
            "applied": True,
            "old_commit": "old111",
            "new_commit": "new222",
            "deps_reinstalled": False,
            "continuity_state": "verified",
            "continuity_verified": True,
            "continuity": {"comparison": {"main_health_status": "healthy"}},
        }

    monkeypatch.setattr(continuity, "apply_update_with_continuity", apply)

    result = CliRunner().invoke(update_cmd.update_app, [])

    assert result.exit_code == 0
    assert seen["restart"] is True
    assert "Updated old111 -> new222" in result.output
    assert "Gateway restarted and readiness verified." in result.output


def test_cli_update_same_source_commit_reports_noop(monkeypatch):
    from aletheia.cli import update_cmd
    from ops.update import continuity
    from typer.testing import CliRunner

    monkeypatch.setattr(
        continuity,
        "apply_update_with_continuity",
        lambda *, restart, progress=None: {
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
    from ops.update import continuity
    from typer.testing import CliRunner

    monkeypatch.setattr(
        continuity,
        "apply_update_with_continuity",
        lambda *, restart, progress=None: {
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
    from ops.update import continuity
    from typer.testing import CliRunner

    monkeypatch.setattr(
        continuity,
        "apply_update_with_continuity",
        lambda *, restart, progress=None: {
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


def test_cli_update_unproven_continuity_exits_nonzero(monkeypatch):
    from aletheia.cli import update_cmd
    from ops.update import continuity
    from typer.testing import CliRunner

    monkeypatch.setattr(
        continuity,
        "apply_update_with_continuity",
        lambda *, restart, progress=None: {
            "applied": True,
            "old_commit": "old111",
            "new_commit": "new222",
            "deps_reinstalled": False,
            "continuity_state": "unproven",
            "continuity_verified": False,
            "continuity": {
                "comparison": {
                    "proof_gaps": ["worker_identity_comparison_unavailable"],
                },
            },
        },
    )

    result = CliRunner().invoke(update_cmd.update_app, [])

    assert result.exit_code == 2
    assert "Updated old111 -> new222" in result.output
    assert "Observer continuity is UNPROVEN" in result.output
    assert "no verified claim was emitted" in result.output


def test_cli_update_no_restart_pending_restart_is_not_verified(monkeypatch):
    from aletheia.cli import update_cmd
    from ops.update import continuity
    from typer.testing import CliRunner

    monkeypatch.setattr(
        continuity,
        "apply_update_with_continuity",
        lambda *, restart, progress=None: {
            "applied": True,
            "old_commit": "old111",
            "new_commit": "new222",
            "deps_reinstalled": False,
            "continuity_state": "pending_restart",
            "continuity_verified": False,
            "continuity": {
                "comparison": {
                    "proof_gaps": ["main_restart_required_for_full_verification"],
                },
            },
        },
    )

    result = CliRunner().invoke(update_cmd.update_app, ["--no-restart"])

    assert result.exit_code == 0
    assert "Observer continuity is PENDING RESTART" in result.output
    assert "Observer continuity verified" not in result.output


def test_top_level_update_help_has_no_apply_subcommand():
    from aletheia.main import app
    from typer.testing import CliRunner

    result = CliRunner().invoke(app, ["update", "--help"])

    assert result.exit_code == 0
    assert "Install the latest verified update for this runtime." in result.output
    assert "apply" not in result.output


def test_cli_update_check_smoke(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from ops.update import applier

    monkeypatch.setattr(
        applier,
        "check_for_update",
        lambda: {
            "ok": True,
            "has_update": False,
            "behind_count": 0,
            "current_commit": "abc",
            "remote_commit": "abc",
            "branch": "master",
        },
    )
    from typer.testing import CliRunner
    from aletheia.cli.update_cmd import update_app

    result = CliRunner().invoke(update_app, ["check"])
    assert result.exit_code == 0
    assert "[deprecated]" not in result.stderr


def test_cli_update_check_unavailable_exits_nonzero(monkeypatch):
    from ops.update import applier
    from typer.testing import CliRunner
    from aletheia.cli.update_cmd import update_app

    monkeypatch.setattr(
        applier,
        "check_for_update",
        lambda: {"ok": False, "reason": "fetch_failed"},
    )

    result = CliRunner().invoke(update_app, ["check"])

    assert result.exit_code == 1
    assert "fetch_failed" in result.output


async def test_api_update_status_is_local_until_explicit_check(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from ops.update import applier

    checks = 0

    def check_for_update():
        nonlocal checks
        checks += 1
        return {"ok": True, "has_update": True, "behind_count": 1, "current_commit": "abc", "remote_commit": "def"}

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
    from ops.update import applier

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
    forbidden = {
        "apply_update",
        "check_update",
        "check_for_update",
        "self_upgrade",
        "git_pull",
        "update_code",
        "code_update",
    }
    assert forbidden.isdisjoint(permissions.TOOL_REGISTRY.keys())


# --- Component 4: smoke verification + controlled rollback ---


def _stub_clean_pull(monkeypatch, pyproject_changed=False, changed_files=None):
    """Stub git_ops so apply_update reaches the smoke stage with a clean pull."""
    from aletheia.update import git_ops
    from ops.update import git_ops as git_mutations, applier

    commits = iter(["old111", "new222", "old111"])
    monkeypatch.setattr(git_ops, "git_available", lambda: True)
    monkeypatch.setattr(git_ops, "is_worktree_dirty", lambda: False)
    monkeypatch.setattr(git_ops, "current_commit", lambda: next(commits))
    monkeypatch.setattr(git_mutations, "pull", lambda branch="master": {"ok": True})
    changed = (
        changed_files if changed_files is not None else (["pyproject.toml"] if pyproject_changed else ["aletheia/x.py"])
    )
    monkeypatch.setattr(git_ops, "changed_files", lambda old, new: changed)
    monkeypatch.setattr(git_ops, "tree_hash", lambda commit, path: "frontend-tree")
    monkeypatch.setattr(applier, "_reinstall_deps", lambda: {"ok": True})


def test_apply_rolls_back_on_smoke_failure(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import git_ops
    from ops.update import git_ops as git_mutations, applier

    _stub_clean_pull(monkeypatch)
    reset_calls = []
    monkeypatch.setattr(git_mutations, "reset_hard", lambda commit: reset_calls.append(commit) or {"ok": True})
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
    from aletheia.update import git_ops
    from ops.update import git_ops as git_mutations, applier

    _stub_clean_pull(monkeypatch)
    reset_calls = []
    monkeypatch.setattr(git_mutations, "reset_hard", lambda commit: reset_calls.append(commit))
    monkeypatch.setattr(applier, "run_smoke_check", lambda: {"ok": True, "stage": "done", "detail": ""})

    result = applier.apply_update()
    assert result["applied"] is True
    assert result["restart_required"] is True
    assert reset_calls == []  # no rollback on success


def test_apply_restart_builds_changed_frontend_and_restarts(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from ops.update import applier

    _stub_clean_pull(monkeypatch, changed_files=["frontend/src/pages/Settings.tsx"])
    monkeypatch.setattr(applier, "_dashboard_build_required", lambda _tree: True)
    monkeypatch.setattr(applier, "run_smoke_check", lambda: {"ok": True, "stage": "done", "detail": ""})
    calls = []
    monkeypatch.setattr(applier, "_build_frontend", lambda _tree: calls.append("build") or {"ok": True})
    monkeypatch.setattr(applier, "_activate_frontend_build", lambda: calls.append("activate") or True)
    monkeypatch.setattr(applier, "_restart_gateway", lambda: calls.append("restart") or {"ok": True})

    result = applier.apply_update(restart=True)
    assert result["applied"] is True
    assert result["frontend_built"] is True
    assert result["restart_completed"] is True
    assert calls == ["build", "activate", "restart"]


def test_dashboard_build_required_for_matching_tree_and_complete_artifact(tmp_path, monkeypatch):
    from ops.update import applier

    static = tmp_path / "dashboard_static"
    (static / ".vite").mkdir(parents=True)
    (static / "assets").mkdir()
    (static / "index.html").write_text("<html>valid legacy dashboard fixture</html>")
    (static / ".vite" / "manifest.json").write_text("{}")
    (static / "performance.json").write_text("{}")
    (static / "assets" / "app.js").write_text("ok")
    (static / applier._DASHBOARD_FRONTEND_TREE).write_text("frontend-tree\n")
    monkeypatch.setattr(applier, "_DASHBOARD_STATIC", static)

    assert applier._dashboard_build_required("frontend-tree") is False
    assert applier._dashboard_build_required("new-frontend-tree") is True
    (static / applier._DASHBOARD_FRONTEND_TREE).unlink()
    assert applier._dashboard_build_required("frontend-tree") is True
    (static / applier._DASHBOARD_FRONTEND_TREE).write_text("frontend-tree\n")
    (static / ".vite" / "manifest.json").unlink()
    assert applier._dashboard_build_required("frontend-tree") is True


def test_build_frontend_records_validated_tree_fingerprint(tmp_path, monkeypatch):
    from ops.update import applier

    staged = tmp_path / "dashboard-next"
    monkeypatch.setattr(applier, "_STAGED_DASHBOARD_STATIC", staged)

    def build(_args, *, env):
        staged.mkdir()
        (staged / "index.html").write_text("ok")
        return _FakeProc()

    monkeypatch.setattr(applier, "_run_npm", build)
    monkeypatch.setattr(
        applier.target_fresh,
        "validate_dashboard_artifacts",
        lambda *_args, **_kwargs: {"ok": True},
    )

    assert applier._build_frontend("frontend-tree") == {"ok": True}
    assert (staged / applier._DASHBOARD_FRONTEND_TREE).read_text() == "frontend-tree\n"


def test_apply_restart_installs_changed_frontend_deps_before_build(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from ops.update import applier

    _stub_clean_pull(monkeypatch, changed_files=["frontend/package-lock.json"])
    monkeypatch.setattr(applier, "_dashboard_build_required", lambda _tree: True)
    monkeypatch.setattr(applier, "run_smoke_check", lambda: {"ok": True})
    calls = []
    monkeypatch.setattr(applier, "_install_frontend_deps", lambda: calls.append("npm-ci") or {"ok": True})
    monkeypatch.setattr(applier, "_build_frontend", lambda _tree: calls.append("build") or {"ok": True})
    monkeypatch.setattr(applier, "_activate_frontend_build", lambda: calls.append("activate") or True)
    monkeypatch.setattr(applier, "_restart_gateway", lambda: calls.append("restart") or {"ok": True})

    result = applier.apply_update(restart=True)

    assert result["applied"] is True
    assert calls == ["npm-ci", "build", "activate", "restart"]


def test_restart_gateway_uses_fresh_interpreter(monkeypatch):
    from ops.update import applier

    seen = {}

    def run(command, **kwargs):
        seen["command"] = command
        seen["cwd"] = kwargs["cwd"]
        return _FakeProc()

    monkeypatch.setattr(applier.subprocess, "run", run)

    assert applier._restart_gateway() == {"ok": True}
    assert seen["command"][:2] == [applier.sys.executable, "-c"]
    script = seen["command"][2]
    roots = [str(applier._PROJECT_ROOT / "core"), str(applier._PROJECT_ROOT)]
    assert f"sys.path[:0] = {roots!r}" in script
    assert str(applier._PROJECT_ROOT / "core/aletheia/__init__.py") in script
    assert "Path(aletheia.__file__).resolve()" in script
    assert "target_core_source_mismatch" in script
    assert script.endswith(
        "from aletheia.gateway.lifecycle import do_start, do_stop; "
        "do_stop(); raise SystemExit(0 if do_start(no_browser=True) is not False else 2)"
    )
    assert seen["cwd"] == str(applier._PROJECT_ROOT)

    # exit 2 = slow start (do_start returned False: alive but readiness timed out).
    # Not a failure — gateway is up, just slower than the 15s verification budget.
    monkeypatch.setattr(applier.subprocess, "run", lambda *_args, **_kwargs: _FakeProc(returncode=2))
    assert applier._restart_gateway()["ok"] is True


def test_restart_gateway_accepts_recovered_systemd_start(monkeypatch):
    from ops.update import applier

    monkeypatch.setattr(
        applier.subprocess,
        "run",
        lambda *_args, **_kwargs: _FakeProc(returncode=1, stderr="start job canceled"),
    )
    monkeypatch.setattr(applier, "_gateway_ready_after_restart_error", lambda: True)

    assert applier._restart_gateway() == {
        "ok": True,
        "restart_warning": "service_start_result_recovered",
    }


def test_restart_gateway_rejects_unrecovered_start_error(monkeypatch):
    from ops.update import applier

    monkeypatch.setattr(
        applier.subprocess,
        "run",
        lambda *_args, **_kwargs: _FakeProc(returncode=1, stderr="start failed"),
    )
    monkeypatch.setattr(applier, "_gateway_ready_after_restart_error", lambda: False)

    result = applier._restart_gateway()

    assert result["ok"] is False
    assert result["reason"] == "gateway_restart_failed"


def _probe_config():
    from types import SimpleNamespace

    return SimpleNamespace(app=SimpleNamespace(dashboard_port=8787))


def test_gateway_restart_error_probe_has_bounded_deadline(monkeypatch):
    from ops.update import applier

    now = [0.0]

    class FailingOpener:
        def open(self, *_args, **_kwargs):
            raise OSError("not ready")

    monkeypatch.setattr(applier.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(applier.time, "sleep", lambda value: now.__setitem__(0, now[0] + value))
    monkeypatch.setattr(applier.urllib.request, "build_opener", lambda *_args: FailingOpener())
    # The probe resolves config and the front door before it starts the deadline
    # loop; if either raises it returns False without consulting the clock, which
    # is how this test used to fail as `0.0 == 1.0` depending on ambient state.
    monkeypatch.setattr(applier, "load_config", lambda: _probe_config())
    monkeypatch.setattr(
        "aletheia.gateway.network.local_frontdoor_host", lambda: "127.0.0.1"
    )

    assert applier._gateway_ready_after_restart_error(timeout_sec=1.0) is False
    assert now[0] == pytest.approx(1.0)


def test_restart_gateway_succeeds_with_persistent_owner_and_unmanaged_transient(
    monkeypatch,
):
    from aletheia.gateway import service_control
    from ops.update import applier

    monkeypatch.setattr(
        service_control,
        "status",
        lambda: {
            "reason": None,
            "backend": "session",
            "installed": True,
            "session": {
                "installed": True,
                "enabled": True,
                "running": True,
                "owned": True,
                "config_valid": True,
            },
            "machine": {},
            "transient": {"managed": True, "running": False},
        },
    )
    monkeypatch.setattr(
        service_control.session_service,
        "stop",
        lambda: {"ok": True, "runtime_stopped": True},
    )
    monkeypatch.setattr(
        service_control.managed_service,
        "stop",
        lambda: pytest.fail("stopped transient cleanup must not block update"),
    )

    def run(*_args, **_kwargs):
        assert service_control.stop()["ok"] is True
        return _FakeProc()

    monkeypatch.setattr(applier.subprocess, "run", run)

    assert applier._restart_gateway() == {"ok": True}


def test_apply_restart_discards_consumed_dashboard_backup(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from ops.update import applier

    _stub_clean_pull(monkeypatch, changed_files=["frontend/src/App.tsx"])
    monkeypatch.setattr(applier, "_dashboard_build_required", lambda _tree: True)
    monkeypatch.setattr(applier, "run_smoke_check", lambda: {"ok": True, "stage": "done", "detail": ""})
    backup = tmp_path / ".dashboard_static-previous"
    backup.mkdir()
    (backup / "old.html").write_text("old dashboard")
    monkeypatch.setattr(applier, "_PREVIOUS_DASHBOARD_STATIC", backup)
    monkeypatch.setattr(applier, "_build_frontend", lambda _tree: {"ok": True})
    monkeypatch.setattr(applier, "_activate_frontend_build", lambda: True)
    monkeypatch.setattr(applier, "_restart_gateway", lambda: {"ok": True})

    result = applier.apply_update(restart=True)

    assert result["applied"] is True
    assert not backup.exists()


def test_apply_restart_restores_old_release_on_restart_failure(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import git_ops
    from ops.update import applier, git_ops as git_mutations

    _stub_clean_pull(monkeypatch, changed_files=["frontend/src/pages/Settings.tsx"])
    monkeypatch.setattr(applier, "_dashboard_build_required", lambda _tree: True)
    monkeypatch.setattr(applier, "run_smoke_check", lambda: {"ok": True, "stage": "done", "detail": ""})
    monkeypatch.setattr(applier, "_build_frontend", lambda _tree: {"ok": True})
    monkeypatch.setattr(applier, "_activate_frontend_build", lambda: True)
    restored = []
    monkeypatch.setattr(applier, "_restore_frontend_build", lambda had_previous: restored.append(had_previous))
    resets = []
    monkeypatch.setattr(git_mutations, "reset_hard", lambda commit: resets.append(commit) or {"ok": True})
    starts = iter([{"ok": False}, {"ok": True}])
    monkeypatch.setattr(applier, "_restart_gateway", lambda: next(starts))

    result = applier.apply_update(restart=True)

    assert result["applied"] is False
    assert result["rolled_back"] is True
    assert result["recovery_started"] is True
    assert restored == [True]
    assert resets == ["old111"]
    assert result["actual_head"] == "old111"


def test_apply_refuses_schema_change_without_restart(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import git_ops
    from ops.update import applier, git_ops as git_mutations

    _stub_clean_pull(
        monkeypatch,
        changed_files=["core/aletheia/storage/migrations.py"],
    )
    calls = []
    monkeypatch.setattr(
        git_mutations,
        "reset_hard",
        lambda commit: calls.append(("reset", commit)) or {"ok": True},
    )
    monkeypatch.setattr(
        applier,
        "run_smoke_check",
        lambda: pytest.fail("schema-bearing source must not be loaded in-process"),
    )

    result = applier.apply_update(restart=False)

    assert result["applied"] is False
    assert result["rolled_back"] is True
    assert result["reason"] == "schema_update_requires_restart"
    assert calls == [("reset", "old111")]


def test_apply_preflights_schema_change_against_backup_before_restart(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from ops.backup import service as backup_service
    from aletheia.update import git_ops
    from ops.update import applier, auto, git_ops as git_mutations

    _stub_clean_pull(monkeypatch, changed_files=["core/aletheia/storage/migrations.py"])
    monkeypatch.setattr(applier, "_dashboard_build_required", lambda _tree: False)
    calls = []
    backups = iter([tmp_path / "preflight.tar", tmp_path / "activation.tar"])

    class FakeBackupService:
        def __init__(self, data_dir):
            calls.append(("backup_service", data_dir))

        def create_backup(self, *, label, profile="online"):
            path = next(backups)
            calls.append(("backup", label, profile, path))
            return path

    monkeypatch.setattr(backup_service, "BackupService", FakeBackupService)
    monkeypatch.setattr(git_mutations, "reset_hard", lambda commit: calls.append(("reset", commit)) or {"ok": True})
    monkeypatch.setattr(git_mutations, "merge_fast_forward", lambda commit: calls.append(("advance", commit)) or {"ok": True})
    monkeypatch.setattr(
        auto,
        "_candidate_preflight",
        lambda commit, backup: calls.append(("preflight", commit, backup)) or (tmp_path / "candidate"),
    )
    monkeypatch.setattr(auto, "_remove_candidate", lambda candidate: calls.append(("remove", candidate)))
    monkeypatch.setattr(auto, "_recovery_checkpoint_problem", lambda backup: calls.append(("verify", backup)))
    monkeypatch.setattr(
        applier,
        "run_smoke_check",
        lambda: pytest.fail("live-data smoke is replaced by restored-copy candidate preflight"),
    )
    monkeypatch.setattr(applier, "_restart_gateway", lambda: calls.append(("restart",)) or {"ok": True})

    result = applier.apply_update(restart=True)

    assert result["applied"] is True
    assert result["schema_changed"] is True
    assert result["schema_backup"] == str(tmp_path / "activation.tar")
    assert ("preflight", "new222", tmp_path / "preflight.tar") in calls
    assert calls.index(("preflight", "new222", tmp_path / "preflight.tar")) < calls.index(("advance", "new222"))
    # The rollback point is an authoritative checkpoint, verified before activation (#1444).
    assert calls.index(
        ("backup", "interactive_schema_activate", "authoritative", tmp_path / "activation.tar")
    ) < calls.index(("verify", tmp_path / "activation.tar")) < calls.index(("restart",))


@pytest.mark.integration
def test_candidate_preflight_runs_real_migration_validation_and_smoke(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from ops.backup.service import BackupService
    from aletheia.update import git_ops
    from ops.update import auto, git_ops as git_mutations
    import shutil
    import subprocess

    source = tmp_path / "source"
    source.mkdir()
    for directory in ("core", "extensions", "ops", "legacy", "tests"):
        shutil.copytree(git_ops.REPO_ROOT / directory, source / directory,
                        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    for filename in ("pyproject.toml", "uv.lock", "README.md"):
        shutil.copy2(git_ops.REPO_ROOT / filename, source / filename)
    for arguments in (["init"], ["add", "."],
                      ["-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                       "-c", "commit.gpgsign=false", "commit", "-m", "isolated candidate"]):
        subprocess.run(["git", *arguments], cwd=source, check=True, capture_output=True)
    monkeypatch.setattr(git_ops, "REPO_ROOT", source)
    monkeypatch.setenv("UV_OFFLINE", "1")

    backup = BackupService(tmp_path).create_backup(label="candidate_integration")
    candidate = None
    try:
        candidate = auto._candidate_preflight(git_ops.current_commit(), backup)
        assert candidate.is_dir()
    finally:
        auto._remove_candidate(candidate)


def test_rollback_applied_update_restores_source_metadata_and_gateway(monkeypatch):
    from aletheia.update import git_ops
    from ops.update import applier, git_ops as git_mutations

    calls = []
    monkeypatch.setattr(git_mutations, "reset_hard", lambda commit: calls.append(("reset", commit)) or {"ok": True})
    monkeypatch.setattr(git_ops, "current_commit", lambda: "old111")
    monkeypatch.setattr(
        applier,
        "_persist_current_commit_fresh",
        lambda commit: calls.append(("metadata", commit)) or {"ok": True},
    )
    monkeypatch.setattr(applier, "_restart_gateway", lambda: calls.append(("restart",)) or {"ok": True})
    monkeypatch.setattr(applier, "write_audit", lambda event: calls.append(("audit", event["event_type"])))

    result = applier.rollback_applied_update({"old_commit": "old111", "new_commit": "new222", "schema_changed": False})

    assert result["rolled_back"] is True
    assert result["metadata_persisted"] is True
    assert calls == [
        ("reset", "old111"),
        ("metadata", "old111"),
        ("restart",),
        ("audit", "update_postflight_rolled_back"),
    ]


def test_rollback_applied_schema_update_restores_transaction_backup(tmp_path, monkeypatch):
    from aletheia.update import git_ops
    from ops.update import applier, auto, git_ops as git_mutations

    calls = []
    monkeypatch.setattr(
        auto,
        "_rollback_to_old_runtime",
        lambda old, backup, had_static, *, restore_static: calls.append(
            ("restore", old, backup, had_static, restore_static)
        )
        or {"ok": True},
    )
    monkeypatch.setattr(git_ops, "current_commit", lambda: "old111")
    monkeypatch.setattr(applier, "_persist_current_commit_fresh", lambda commit: {"ok": True})
    monkeypatch.setattr(applier, "write_audit", lambda event: calls.append(("audit", event["event_type"])))

    result = applier.rollback_applied_update(
        {
            "old_commit": "old111",
            "new_commit": "new222",
            "schema_changed": True,
            "schema_backup": str(tmp_path / "activation.tar"),
            "frontend_built": True,
            "had_previous_static": True,
        }
    )

    assert result["rolled_back"] is True
    assert calls == [
        ("restore", "old111", tmp_path / "activation.tar", True, True),
        ("audit", "update_postflight_rolled_back"),
    ]


def test_rollback_applied_update_reports_unproven_metadata_restore(monkeypatch):
    from aletheia.update import git_ops
    from ops.update import applier, git_ops as git_mutations

    monkeypatch.setattr(git_mutations, "reset_hard", lambda _commit: {"ok": True})
    monkeypatch.setattr(git_ops, "current_commit", lambda: "old111")
    monkeypatch.setattr(
        applier,
        "_persist_current_commit_fresh",
        lambda _commit: {"ok": False, "reason": "write_failed"},
    )
    monkeypatch.setattr(applier, "_record_metadata_failure", lambda *_args: None)
    monkeypatch.setattr(applier, "_restart_gateway", lambda: {"ok": True})
    monkeypatch.setattr(applier, "write_audit", lambda _event: None)

    result = applier.rollback_applied_update({"old_commit": "old111", "new_commit": "new222", "schema_changed": False})

    assert result["rolled_back"] is False
    assert result["recovery_started"] is True
    assert result["recovery_errors"] == ["metadata_restore_failed"]


def test_apply_restart_skips_valid_frontend_without_source_changes(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from ops.update import applier

    _stub_clean_pull(monkeypatch)
    monkeypatch.setattr(applier, "run_smoke_check", lambda: {"ok": True, "stage": "done", "detail": ""})
    calls = []
    monkeypatch.setattr(applier, "_dashboard_build_required", lambda _tree: False)
    monkeypatch.setattr(
        applier, "_build_frontend", lambda _tree: pytest.fail("valid unchanged dashboard must not rebuild")
    )
    monkeypatch.setattr(applier, "_activate_frontend_build", lambda: pytest.fail("no staged dashboard to activate"))
    monkeypatch.setattr(applier, "_restart_gateway", lambda: calls.append("restart") or {"ok": True})

    result = applier.apply_update(restart=True)

    assert result["applied"] is True
    assert result["frontend_built"] is False
    assert result["restart_completed"] is True
    assert calls == ["restart"]


def test_apply_restart_rebuilds_after_deferred_frontend_restart(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from ops.update import applier

    _stub_clean_pull(monkeypatch)
    monkeypatch.setattr(applier, "run_smoke_check", lambda: {"ok": True, "stage": "done", "detail": ""})
    calls = []
    monkeypatch.setattr(applier, "_dashboard_build_required", lambda tree: tree != "old-frontend-tree")
    monkeypatch.setattr(applier, "_build_frontend", lambda _tree: calls.append("build") or {"ok": True})
    monkeypatch.setattr(applier, "_activate_frontend_build", lambda: calls.append("activate") or True)
    monkeypatch.setattr(applier, "_restart_gateway", lambda: calls.append("restart") or {"ok": True})

    result = applier.apply_update(restart=True)

    assert result["frontend_built"] is True
    assert calls == ["build", "activate", "restart"]


def test_apply_restart_rolls_back_when_frontend_build_fails(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import git_ops
    from ops.update import applier, git_ops as git_mutations

    _stub_clean_pull(monkeypatch, changed_files=["frontend/src/pages/Settings.tsx"])
    monkeypatch.setattr(applier, "_dashboard_build_required", lambda _tree: True)
    resets = []
    monkeypatch.setattr(git_mutations, "reset_hard", lambda commit: resets.append(commit) or {"ok": True})
    monkeypatch.setattr(
        applier,
        "_build_frontend",
        lambda _tree: {"ok": False, "reason": "frontend_build_failed", "detail": "tsc failed"},
    )
    monkeypatch.setattr(applier, "_restart_gateway", lambda: pytest.fail("gateway must not restart"))

    result = applier.apply_update(restart=True)

    assert result["applied"] is False
    assert result["reason"] == "frontend_build_failed"
    assert "tsc failed" in result["message"]
    assert resets == ["old111"]


def test_smoke_import_gate_detects_broken_module(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from ops.update import smoke

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


def test_smoke_pytest_does_not_inherit_runtime_data_root(tmp_path, monkeypatch):
    from ops.update import smoke

    home = tmp_path / "home"
    runtime = home / ".aletheia"
    runtime.mkdir(parents=True)
    sentinel = runtime / "owner-sentinel.txt"
    sentinel.write_text("unchanged", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("ALETHEIA_DATA_DIR", str(runtime))
    monkeypatch.setenv("PYTEST_ADDOPTS", f"-o cache_dir={tmp_path / 'pytest-cache'}")

    result = smoke.run_smoke_check()
    assert result == {"ok": True, "stage": "done", "detail": ""}
    assert sentinel.read_text(encoding="utf-8") == "unchanged"


def test_apply_no_auto_restart(tmp_path, monkeypatch):
    """Neither success nor rollback may restart the gateway (user requirement)."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.update import git_ops
    from ops.update import git_ops as git_mutations, applier

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
    from ops.update import git_ops as git_mutations
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
    from ops.update import git_ops as git_mutations
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
    from ops.update import applier
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
    from ops.update import smoke
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


def _forbid_update_body(monkeypatch):
    from ops.update import applier, continuity

    monkeypatch.setattr(continuity, "capture_update_continuity", lambda: pytest.fail("preflight reached"))
    monkeypatch.setattr(applier, "_stop_gateway", lambda: pytest.fail("gateway stopped"))
    monkeypatch.setattr(applier, "apply_update", lambda **_k: pytest.fail("source mutated"))


def test_manual_update_refuses_while_the_deployment_lock_is_held(tmp_path, monkeypatch):
    """#3018: a timer tick holding the lock blocks a concurrent manual update before preflight."""
    import fcntl

    from ops.update import auto, continuity

    monkeypatch.setattr(auto, "_paths", lambda: {"lock": tmp_path / "update" / "auto-deploy.lock"})
    _forbid_update_body(monkeypatch)
    (tmp_path / "update").mkdir()
    with (tmp_path / "update" / "auto-deploy.lock").open("a+") as holder:
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # the other transaction
        result = continuity.apply_update_with_continuity(restart=True)

    assert result["applied"] is False
    assert result["reason"] == "deployment_busy"


@pytest.mark.parametrize(
    ("gate", "refused"),
    [
        ({"ok": False, "reason": "runtime_gate_failed", "failed": ["live_disabled"]}, "live_disabled"),
        ({"ok": False, "reason": "runtime_gate_failed", "failed": ["deployment_mode"]}, "deployment_mode"),
        ({"ok": False, "reason": "gateway_unhealthy", "detail": "URLError"}, "status_unavailable"),
    ],
)
def test_paper_role_manual_update_requires_the_deployment_admission(tmp_path, monkeypatch, gate, refused):
    """#3020: live_limited or an unapproved mode on a paper-role runtime refuses before mutation."""
    from ops.update import auto, continuity

    monkeypatch.setattr(auto, "_paths", lambda: {"lock": tmp_path / "update" / "auto-deploy.lock"})
    monkeypatch.setenv("ALETHEIA_RUNTIME_ROLE", "paper")
    monkeypatch.setattr(auto, "_runtime_gate", lambda **_k: gate)
    _forbid_update_body(monkeypatch)

    result = continuity.apply_update_with_continuity(restart=True)

    assert result["reason"] == "runtime_admission_failed"
    assert refused in result["message"]


def test_bare_manual_update_reads_the_paper_role_from_the_installed_timer_unit(tmp_path, monkeypatch):
    """#3020: DEPLOYMENT.md's bare ``aletheia update`` carries no role env; the unit does."""
    from ops.update import auto, continuity

    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    (unit_dir / "aletheia-auto-update.service").write_text(
        f"[Service]\n{auto._SYSTEMD_MARKER}\nEnvironment=ALETHEIA_RUNTIME_ROLE=paper\n", encoding="utf-8"
    )
    monkeypatch.setattr(auto, "_unit_dir", lambda: unit_dir)
    monkeypatch.setattr(auto, "_paths", lambda: {"lock": tmp_path / "update" / "auto-deploy.lock"})
    monkeypatch.delenv("ALETHEIA_RUNTIME_ROLE", raising=False)
    monkeypatch.setattr(
        auto, "_runtime_gate", lambda **_k: {"ok": False, "failed": ["live_disabled", "deployment_mode"]}
    )
    _forbid_update_body(monkeypatch)

    result = continuity.apply_update_with_continuity(restart=True)

    assert result["reason"] == "runtime_admission_failed"
    assert "live_disabled" in result["message"]


def test_unclassified_runtime_manual_update_skips_the_paper_admission(tmp_path, monkeypatch):
    from ops.update import auto, continuity

    monkeypatch.setattr(auto, "_paths", lambda: {"lock": tmp_path / "update" / "auto-deploy.lock"})
    monkeypatch.delenv("ALETHEIA_RUNTIME_ROLE", raising=False)
    monkeypatch.setattr(auto, "_runtime_gate", lambda **_k: pytest.fail("gate read without a paper role"))
    monkeypatch.setattr(continuity, "_apply_update_with_continuity", lambda **_k: {"applied": True})

    assert continuity.apply_update_with_continuity(restart=True) == {"applied": True}


def test_paper_role_manual_update_admits_paper_live_disabled_even_if_unhealthy(tmp_path, monkeypatch):
    from ops.update import auto, continuity

    monkeypatch.setattr(auto, "_paths", lambda: {"lock": tmp_path / "update" / "auto-deploy.lock"})
    monkeypatch.setenv("ALETHEIA_RUNTIME_ROLE", "paper")
    monkeypatch.setattr(auto, "_runtime_gate", lambda **_k: {"ok": False, "failed": ["health", "scheduler_running"]})
    monkeypatch.setattr(continuity, "_apply_update_with_continuity", lambda **_k: {"applied": True, "reached": True})

    assert continuity.apply_update_with_continuity(restart=True) == {"applied": True, "reached": True}
