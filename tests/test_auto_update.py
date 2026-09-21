"""Focused contracts for Fedora release-tag auto deployment."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
from pathlib import Path
import subprocess
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner





def test_release_check_persists_exact_candidate_without_activation(tmp_path, monkeypatch):
    from aletheia.update import applier

    events = []
    auto, git_ops, backup = _configured_candidate_run(tmp_path, monkeypatch, events)
    monkeypatch.setattr(applier, "_is_packaged", lambda: False)
    monkeypatch.setattr(git_ops, "behind_count", lambda *args: pytest.fail("branch distance is not a release"))
    checked = applier.check_for_update()
    assert checked["has_update"] is True
    assert (checked["target_tag"], checked["target_sha"], checked["target_version"]) == (
        "v1.1.0", "target-sha", "1.1.0",
    )
    assert events == []
    backup.create_backup.assert_not_called()
    monkeypatch.setattr(git_ops, "fetch_release_tags", lambda: pytest.fail("GET fetched"))
    progress = applier.update_progress()
    assert progress["mode"] == "source_release"
    assert progress["status"] == "available"
    assert progress["target_sha"] == "target-sha"


@pytest.mark.parametrize("invalid", ["tag_not_on_master", "tag_version_mismatch"])
def test_explicit_check_uses_release_validation(tmp_path, monkeypatch, invalid):
    auto, git_ops, backup = _configured_candidate_run(tmp_path, monkeypatch, [])
    if invalid == "tag_not_on_master":
        monkeypatch.setattr(git_ops, "is_ancestor", lambda older, newer: older != "target-sha")
    else:
        monkeypatch.setattr(git_ops, "version_at_ref", lambda ref: '__version__ = "1.0.0"')
    result = auto.check()
    assert result["reason"] == invalid
    assert result["has_update"] is False
    backup.create_backup.assert_not_called()

def _state_paths(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / "update"
    return {"root": root, "state": root / "auto-deploy.json", "lock": root / "auto-deploy.lock"}


def _linux_source(monkeypatch):
    from aletheia.update import auto, git_ops

    monkeypatch.setattr(auto.sys, "platform", "linux")
    monkeypatch.setenv("ALETHEIA_RUNTIME_ROLE", "paper")
    monkeypatch.setattr(git_ops, "is_repo", lambda: True)
    monkeypatch.setattr(git_ops, "fetch_release_tags", lambda: {"ok": True})
    monkeypatch.setattr(git_ops, "is_worktree_dirty", lambda: False)
    monkeypatch.setattr(git_ops, "current_branch", lambda: "master")
    monkeypatch.setattr(git_ops, "current_commit", lambda: "old-sha")
    monkeypatch.setattr(git_ops, "is_ancestor", lambda older, newer: True)
    monkeypatch.setattr(git_ops, "remote_tag_names", lambda: set(git_ops.tag_names()))
    monkeypatch.setattr(auto, "_record_audit", lambda *args, **kwargs: None)


def _successful_activation_boundary(monkeypatch, events: list[str] | None = None):
    from aletheia.observation_lab import runner
    from aletheia.update import target_fresh

    events = events if events is not None else []

    @contextmanager
    def barrier():
        events.append("barrier:enter")
        try:
            yield {"ok": True, "checkpoint": {"current_fingerprint": "settled-cut"}}
        finally:
            events.append("barrier:exit")

    def reanchor(**kwargs):
        assert kwargs["expected_checkpoint_fingerprint"] == "settled-cut"
        events.append("reanchor")
        return {"ok": True, "reason": "fingerprint_match"}

    monkeypatch.setattr(runner, "observation_settlement_barrier", barrier)
    monkeypatch.setattr(target_fresh, "reanchor_joint_checkpoint", reanchor)
    return events


def _configured_candidate_run(tmp_path, monkeypatch, events: list[str]):
    from aletheia.update import auto, git_ops

    _linux_source(monkeypatch)
    _successful_activation_boundary(monkeypatch, events)
    monkeypatch.setattr(auto, "_paths", lambda: _state_paths(tmp_path))
    monkeypatch.setattr(git_ops, "tag_names", lambda: ["v1.1.0"])
    monkeypatch.setattr(git_ops, "tag_commit", lambda tag: "target-sha")
    monkeypatch.setattr(
        git_ops,
        "version_at_ref",
        lambda ref: '__version__ = "1.0.0"\n' if ref == "HEAD" else '__version__ = "1.1.0"\n',
    )
    monkeypatch.setattr(auto, "_runtime_gate", lambda **kwargs: {"ok": True})
    monkeypatch.setattr(auto, "_doctor_healthy", lambda: {"ok": True})
    monkeypatch.setattr(auto, "_observer_auto_preflight", lambda: {"required": False})
    service = MagicMock()
    service.create_backup.side_effect = [tmp_path / "preflight", tmp_path / "activation"]
    monkeypatch.setattr(auto, "BackupService", lambda data_dir: service)
    monkeypatch.setattr(auto, "_candidate_preflight", lambda target, backup: tmp_path / "candidate")
    monkeypatch.setattr(auto, "_sync_candidate_dependencies", lambda worktree: None)
    monkeypatch.setattr(auto, "_build_candidate_frontend", lambda *args, **kwargs: None)
    monkeypatch.setattr(auto, "_remove_candidate", lambda worktree: None)
    monkeypatch.setattr(
        auto,
        "_gateway_command",
        lambda action: events.append(f"gateway:{action}") or {"ok": True},
    )
    monkeypatch.setattr(
        git_ops,
        "merge_fast_forward",
        lambda target: events.append("source:activate") or {"ok": True},
    )
    monkeypatch.setattr(auto, "_install_source_dependencies", lambda: True)
    monkeypatch.setattr(auto, "_activate_frontend_build", lambda: True)
    monkeypatch.setattr(
        auto,
        "_wait_for_runtime",
        lambda sha, version: events.append("runtime:verified") or {"ok": True},
    )
    return auto, git_ops, service


def test_only_strict_stable_semver_tags_are_candidates(tmp_path, monkeypatch):
    from aletheia.update import auto, git_ops

    _linux_source(monkeypatch)
    monkeypatch.setattr(auto, "_paths", lambda: _state_paths(tmp_path))
    monkeypatch.setattr(git_ops, "tag_names", lambda: ["v1.1.0-rc.1", "v1.1.0-hold.1", "v1.1.0-noauto.1"])
    monkeypatch.setattr(git_ops, "tag_commit", lambda tag: "ignored")
    monkeypatch.setattr(git_ops, "version_at_ref", lambda ref: '__version__ = "1.0.0"\n')

    result = auto.run()

    assert result["reason"] == "up_to_date"
    assert result["state"]["observed_tags"] == {}


def test_product_version_parser_accepts_release_please_marker(monkeypatch):
    from aletheia.update import auto, git_ops

    monkeypatch.setattr(
        git_ops,
        "version_at_ref",
        lambda ref: '__version__ = "0.7.0"  # x-release-please-version\n',
    )

    assert auto._version_from_ref("HEAD") == "0.7.0"


def test_local_or_revoked_tag_is_not_a_candidate(tmp_path, monkeypatch):
    from aletheia.update import auto, git_ops

    _linux_source(monkeypatch)
    monkeypatch.setattr(auto, "_paths", lambda: _state_paths(tmp_path))
    monkeypatch.setattr(git_ops, "tag_names", lambda: ["v1.1.0"])
    monkeypatch.setattr(git_ops, "remote_tag_names", lambda: set())
    monkeypatch.setattr(git_ops, "version_at_ref", lambda ref: '__version__ = "1.0.0"\n')

    assert auto.run()["reason"] == "up_to_date"


def test_local_tag_discovery_failure_does_not_report_up_to_date(tmp_path, monkeypatch):
    from aletheia.update import auto, git_ops

    _linux_source(monkeypatch)
    monkeypatch.setattr(auto, "_paths", lambda: _state_paths(tmp_path))
    monkeypatch.setattr(git_ops, "remote_tag_names", lambda: {"v1.1.0"})
    monkeypatch.setattr(git_ops, "tag_names", lambda: None)

    assert auto.run()["reason"] == "local_tag_discovery_failed"


def test_release_fetch_is_master_plus_tags(monkeypatch):
    from aletheia.update import git_ops

    calls = []

    class Proc:
        returncode = 0
        stderr = ""

    monkeypatch.setattr(git_ops, "git_available", lambda: True)
    monkeypatch.setattr(git_ops, "_git", lambda *args: calls.append(args) or Proc())

    assert git_ops.fetch_release_tags() == {"ok": True}
    assert calls == [("fetch", "origin", "master", "--tags")]


def test_tag_requires_master_ancestry_and_matching_product_version(monkeypatch):
    from aletheia.update import auto, git_ops

    monkeypatch.setattr(git_ops, "is_ancestor", lambda older, newer: False)
    with pytest.raises(auto.AutoUpdateError) as error:
        auto._validate_candidate("v1.2.3", "target", (1, 0, 0))
    assert error.value.reason == "tag_not_on_master"

    monkeypatch.setattr(git_ops, "is_ancestor", lambda older, newer: True)
    monkeypatch.setattr(git_ops, "version_at_ref", lambda ref: '__version__ = "1.2.2"\n')
    with pytest.raises(auto.AutoUpdateError) as error:
        auto._validate_candidate("v1.2.3", "target", (1, 0, 0))
    assert error.value.reason == "tag_version_mismatch"


def test_moved_existing_tag_is_rejected(tmp_path, monkeypatch):
    from aletheia.update import auto, git_ops

    _linux_source(monkeypatch)
    paths = _state_paths(tmp_path)
    monkeypatch.setattr(auto, "_paths", lambda: paths)
    auto._write_state({}, observed_tags={"v1.1.0": "old-tag-sha"})
    monkeypatch.setattr(git_ops, "tag_names", lambda: ["v1.1.0"])
    monkeypatch.setattr(git_ops, "tag_commit", lambda tag: "new-tag-sha")

    result = auto.run()

    assert result["reason"] == "tag_moved"
    assert result["state"]["failed_tags"]["v1.1.0"]["sha"] == "new-tag-sha"


def test_dirty_or_non_fast_forward_source_is_rejected_before_candidate_selection(tmp_path, monkeypatch):
    from aletheia.update import auto, git_ops

    _linux_source(monkeypatch)
    monkeypatch.setattr(auto, "_paths", lambda: _state_paths(tmp_path))
    monkeypatch.setattr(git_ops, "is_worktree_dirty", lambda: True)

    assert auto.run()["reason"] == "source_not_clean_master"


def test_same_failed_tag_is_skipped_but_does_not_create_a_hold(tmp_path, monkeypatch):
    from aletheia.update import auto, git_ops

    _linux_source(monkeypatch)
    paths = _state_paths(tmp_path)
    monkeypatch.setattr(auto, "_paths", lambda: paths)
    auto._write_state({}, failed_tags={"v1.1.0": {"sha": "tag-sha", "reason": "smoke_failed"}})
    monkeypatch.setattr(git_ops, "tag_names", lambda: ["v1.1.0"])
    monkeypatch.setattr(git_ops, "tag_commit", lambda tag: "tag-sha")
    monkeypatch.setattr(git_ops, "version_at_ref", lambda ref: '__version__ = "1.0.0"\n')

    result = auto.run()

    assert result["reason"] == "failed_tag_skipped"
    assert result["state"].get("hold") is False


def test_transient_baseline_failure_is_not_blacklisted(tmp_path, monkeypatch):
    from aletheia.update import auto, git_ops

    _linux_source(monkeypatch)
    monkeypatch.setattr(auto, "_paths", lambda: _state_paths(tmp_path))
    monkeypatch.setattr(git_ops, "tag_names", lambda: ["v1.1.0"])
    monkeypatch.setattr(git_ops, "tag_commit", lambda tag: "target-sha")
    monkeypatch.setattr(
        git_ops, "version_at_ref", lambda ref: '__version__ = "1.0.0"\n' if ref == "HEAD" else '__version__ = "1.1.0"\n'
    )
    monkeypatch.setattr(auto, "_runtime_gate", lambda **kwargs: {"ok": False, "reason": "gateway_unhealthy"})

    result = auto.run()

    assert result["reason"] == "baseline_unhealthy"
    assert result["state"].get("failed_tags") in (None, {})


def test_start_failure_restores_old_runtime_and_permanently_skips_tag(tmp_path, monkeypatch):
    from aletheia.update import auto, git_ops

    _linux_source(monkeypatch)
    boundary_events = _successful_activation_boundary(monkeypatch)
    monkeypatch.setattr(auto, "_paths", lambda: _state_paths(tmp_path))
    monkeypatch.setattr(git_ops, "tag_names", lambda: ["v1.1.0"])
    monkeypatch.setattr(git_ops, "tag_commit", lambda tag: "target-sha")
    monkeypatch.setattr(
        git_ops,
        "version_at_ref",
        lambda ref: '__version__ = "1.0.0"\n' if ref == "HEAD" else '__version__ = "1.1.0"\n',
    )
    monkeypatch.setattr(auto, "_runtime_gate", lambda **kwargs: {"ok": True})
    monkeypatch.setattr(auto, "_doctor_healthy", lambda: {"ok": True})
    backups = [tmp_path / "preflight.tar.gz", tmp_path / "activation.tar.gz"]
    service = MagicMock()
    service.create_backup.side_effect = backups
    monkeypatch.setattr(auto, "BackupService", lambda data_dir: service)
    candidate = tmp_path / "candidate"
    monkeypatch.setattr(auto, "_candidate_preflight", lambda target, backup: candidate)
    monkeypatch.setattr(auto, "_sync_candidate_dependencies", lambda worktree: None)
    monkeypatch.setattr(auto, "_build_candidate_frontend", lambda *args, **kwargs: None)
    monkeypatch.setattr(auto, "_remove_candidate", lambda worktree: None)
    monkeypatch.setattr(auto, "_gateway_command", lambda action: {"ok": action == "stop", "reason": "gateway_start_failed"})
    monkeypatch.setattr(git_ops, "merge_fast_forward", lambda target: {"ok": True})
    monkeypatch.setattr(auto, "_install_source_dependencies", lambda: True)
    monkeypatch.setattr(auto, "_activate_frontend_build", lambda: True)
    restored = []
    monkeypatch.setattr(
        auto,
        "_restore_old_runtime",
        lambda old, backup, had_previous_static, *, restore_static: restored.append(
            (old, backup, had_previous_static, restore_static)
        ) or {"ok": True},
    )

    result = auto.run()

    assert result["reason"] == "gateway_start_failed"
    assert result["state"]["hold"] is False
    assert result["state"]["failed_tags"]["v1.1.0"]["sha"] == "target-sha"
    assert restored == [("old-sha", backups[1], True, True)]
    assert boundary_events == ["barrier:enter", "barrier:exit"]


def test_unknown_source_mutation_holds_without_restarting_gateway(tmp_path, monkeypatch):
    from aletheia.update import auto, git_ops

    _linux_source(monkeypatch)
    boundary_events = _successful_activation_boundary(monkeypatch)
    monkeypatch.setattr(auto, "_paths", lambda: _state_paths(tmp_path))
    monkeypatch.setattr(git_ops, "tag_names", lambda: ["v1.1.0"])
    monkeypatch.setattr(git_ops, "tag_commit", lambda tag: "target-sha")
    monkeypatch.setattr(
        git_ops,
        "version_at_ref",
        lambda ref: '__version__ = "1.0.0"\n' if ref == "HEAD" else '__version__ = "1.1.0"\n',
    )
    monkeypatch.setattr(auto, "_runtime_gate", lambda **kwargs: {"ok": True})
    monkeypatch.setattr(auto, "_doctor_healthy", lambda: {"ok": True})
    monkeypatch.setattr(auto, "_observer_auto_preflight", lambda: {"required": False})
    service = MagicMock()
    service.create_backup.side_effect = [tmp_path / "preflight", tmp_path / "activation"]
    monkeypatch.setattr(auto, "BackupService", lambda data_dir: service)
    monkeypatch.setattr(auto, "_candidate_preflight", lambda target, backup: tmp_path / "candidate")
    monkeypatch.setattr(auto, "_sync_candidate_dependencies", lambda worktree: None)
    monkeypatch.setattr(auto, "_build_candidate_frontend", lambda *args, **kwargs: None)
    monkeypatch.setattr(auto, "_remove_candidate", lambda worktree: None)
    actions = []
    monkeypatch.setattr(
        auto,
        "_gateway_command",
        lambda action: actions.append(action) or {"ok": action == "stop"},
    )
    monkeypatch.setattr(
        git_ops,
        "merge_fast_forward",
        lambda target: {
            "ok": False,
            "reason": "git_mutation_state_unknown",
            "detail": "HEAD/worktree state unavailable",
        },
    )
    monkeypatch.setattr(auto, "_install_source_dependencies", lambda: True)

    result = auto.run()

    assert result["reason"] == "git_mutation_state_unknown"
    assert result["state"]["hold"] is True
    assert actions == ["stop"]
    assert boundary_events == ["barrier:enter", "barrier:exit"]


def test_settlement_failure_keeps_old_source_and_release_retryable(tmp_path, monkeypatch):
    from aletheia.observation_lab import runner
    events: list[str] = []
    auto, _, service = _configured_candidate_run(tmp_path, monkeypatch, events)

    @contextmanager
    def failed_barrier():
        yield {"ok": False, "reason": "poison_receipt"}

    monkeypatch.setattr(runner, "observation_settlement_barrier", failed_barrier)

    result = auto.run()

    assert result["reason"] == "observation_settlement_failed"
    assert result["state"].get("failed_tags") in (None, {})
    assert events == ["gateway:stop", "gateway:start"]
    assert [call.kwargs["profile"] for call in service.create_backup.call_args_list] == ["online"]


def test_schema_bearing_tag_reanchor_failure_rolls_back_before_postflight(tmp_path, monkeypatch):
    from aletheia.update import target_fresh

    events: list[str] = []
    auto, _, service = _configured_candidate_run(tmp_path, monkeypatch, events)
    monkeypatch.setattr(
        target_fresh,
        "reanchor_joint_checkpoint",
        lambda **kwargs: events.append("reanchor:failed")
        or {"ok": False, "reason": "target_checkpoint_delta_unproven"},
    )
    monkeypatch.setattr(
        auto,
        "_observer_auto_postflight",
        lambda *_args, **_kwargs: pytest.fail("failed strict reanchor must not reach postflight"),
    )
    worker_guard = {
        "required": True,
        "ok": True,
        "before": {"observation": {"worker": {"acquired_at": "settled-cut"}}},
        "preflight": {"ok": True},
    }
    monkeypatch.setattr(auto, "_observer_auto_preflight", lambda: worker_guard)

    @contextmanager
    def rollback_guard(guard):
        assert guard is worker_guard
        events.append("rollback-guard:enter")
        try:
            yield True
        finally:
            events.append("rollback-guard:exit")

    monkeypatch.setattr(auto, "_observer_rollback_guard", rollback_guard)
    monkeypatch.setattr(
        auto,
        "_rollback_to_old_runtime",
        lambda old, backup, had_previous_static, *, restore_static: events.append("rollback")
        or {"ok": True},
    )

    result = auto.run()

    assert result["reason"] == "observation_checkpoint_reanchor_failed"
    assert result["state"]["hold"] is False
    assert result["state"]["failed_tags"]["v1.1.0"]["sha"] == "target-sha"
    assert events == [
        "gateway:stop",
        "barrier:enter",
        "source:activate",
        "gateway:start",
        "runtime:verified",
        "reanchor:failed",
        "rollback-guard:enter",
        "rollback",
        "rollback-guard:exit",
        "barrier:exit",
    ]
    assert [call.kwargs["profile"] for call in service.create_backup.call_args_list] == [
        "online",
        "authoritative",
    ]


def test_state_is_atomic_and_resume_only_clears_global_hold(tmp_path, monkeypatch):
    from aletheia.update import auto

    _linux_source(monkeypatch)
    monkeypatch.setattr(auto, "_paths", lambda: _state_paths(tmp_path))
    auto._write_state({}, hold=True, phase="held", status="held")

    result = auto.resume()

    assert result["ok"] is True
    assert auto.status()["hold"] is False
    assert not _state_paths(tmp_path)["state"].with_suffix(".tmp").exists()


def test_corrupt_state_fails_closed(tmp_path, monkeypatch):
    from aletheia.update import auto

    _linux_source(monkeypatch)
    paths = _state_paths(tmp_path)
    monkeypatch.setattr(auto, "_paths", lambda: paths)
    paths["root"].mkdir(parents=True)
    paths["state"].write_text("not-json", encoding="utf-8")

    assert auto.run()["reason"] == "state_corrupt"
    assert auto.status()["status"] == "corrupt"


def test_candidate_frontend_always_installs_worktree_dependencies(tmp_path, monkeypatch):
    from aletheia.update import auto

    worktree = tmp_path / "candidate"
    frontend = worktree / "frontend"
    frontend.mkdir(parents=True)
    staged = tmp_path / "staged"
    monkeypatch.setattr(auto, "_STAGED_DASHBOARD_STATIC", staged)
    monkeypatch.setattr(auto.shutil, "which", lambda name: "/usr/bin/npm")
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command[1:])
        if command[1:] == ["run", "build"]:
            (staged / "index.html").parent.mkdir(parents=True, exist_ok=True)
            (staged / "index.html").write_text("ok", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(auto, "_run", fake_run)

    auto._build_candidate_frontend(worktree, old="old", target="target")

    assert calls == [["ci"], ["run", "build"]]


def test_candidate_preflight_preserves_smoke_stage_and_detail(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from aletheia.update import auto, git_ops

    root = tmp_path / "candidates"
    monkeypatch.setattr(auto, "_paths", lambda: {"root": root})
    monkeypatch.setattr(git_ops, "_git", lambda *_args: SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(
        auto,
        "RestoreService",
        lambda: SimpleNamespace(restore=lambda *_args: SimpleNamespace(success=True, errors=[])),
    )
    monkeypatch.setattr(auto, "_uv", lambda: "/usr/bin/uv")
    monkeypatch.setattr(
        auto,
        "_run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="RuntimeError: smoke_stage=fast_tests; smoke_detail=workflow route mismatch",
        ),
    )

    with pytest.raises(auto.AutoUpdateError) as error:
        auto._candidate_preflight("target", tmp_path / "backup.tar")

    assert error.value.reason == "candidate_preflight_failed"
    assert "smoke_stage=fast_tests" in error.value.detail
    assert "workflow route mismatch" in error.value.detail


def test_candidate_preflight_uses_current_interpreter_and_pythonpath(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from aletheia.update import auto, git_ops

    root = tmp_path / "candidates"
    monkeypatch.setattr(auto, "_paths", lambda: {"root": root})
    monkeypatch.setattr(git_ops, "_git", lambda *_args: SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(
        auto,
        "RestoreService",
        lambda: SimpleNamespace(restore=lambda *_args: SimpleNamespace(success=True, errors=[])),
    )
    monkeypatch.setattr(auto, "_uv", lambda: pytest.fail("preflight must not resolve uv"))

    calls = []
    monkeypatch.setattr(
        auto,
        "_run",
        lambda command, **kwargs: calls.append((command, kwargs)) or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    worktree = auto._candidate_preflight("target_commit", tmp_path / "backup.tar")
    assert worktree.is_dir()
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command[0] == auto.sys.executable
    assert command[1] == "-c"
    assert kwargs["cwd"] == worktree
    assert str(worktree) in kwargs["env"]["PYTHONPATH"]

def test_doctor_runs_in_a_fresh_interpreter(monkeypatch):
    from aletheia.update import auto

    calls = []
    monkeypatch.setattr(
        auto,
        "_run",
        lambda command, **kwargs: calls.append((command, kwargs)) or subprocess.CompletedProcess(
            command, 0, '{"ok": true, "detail": "ready"}', ""
        ),
    )

    assert auto._doctor_healthy() == {"ok": True, "detail": "ready"}
    assert calls[0][0][:2] == [auto.sys.executable, "-c"]


def test_fresh_doctor_binds_real_credential_status_before_reporting(monkeypatch):
    from aletheia.update import auto

    prefix = '''
import socket
from aletheia.config import loader, secrets
from aletheia.config.schema import AletheiaConfig, ProviderConfig
from aletheia.diagnostics import doctor
from extensions.exchanges import composition
from extensions.model_endpoints import http_runtime
from aletheia.storage import migrations
cfg = AletheiaConfig()
cfg.exchange.mode = "demo"
cfg.exchange.paper_execution_mode = "bitget_demo"
cfg.models.default_provider = "selected"
cfg.models.providers["selected"] = ProviderConfig(type="openai_compatible", enabled=True, api_key_secret_ref="selected-reference")
loader.load_config = lambda: cfg
def forbidden(*args, **kwargs):
    raise AssertionError("unexpected raw secret, client construction or network")
secrets.get_secret = forbidden
socket.socket.connect = forbidden
composition.CCXTUTAAdapter = forbidden
def synthetic_secret(ref, *, strict):
    assert strict is True
    assert ref in {"bitget.demo.api_key", "bitget.demo.api_secret", "bitget.demo.passphrase", "selected-reference"}
    return "synthetic-value"
composition.get_secret = synthetic_secret
http_runtime.get_secret = synthetic_secret
def isolated_report():
    report = doctor.Report()
    doctor._probe_credentials(report)
    assert all(check.severity == "ok" for check in report.checks), report
    report.add("llm.inference", "ok", "isolated fixture, not an online probe")
    report.add("content.signal_settlement", "info", "isolated fixture")
    return report
doctor.build_report = isolated_report
migrations.migration_status = lambda: {"current": True}
'''
    def run(command, **kwargs):
        assert command[:2] == [auto.sys.executable, "-c"]
        return subprocess.run(
            [*command[:2], prefix + command[2]], cwd=Path(__file__).resolve().parents[1],
            env=kwargs["env"], capture_output=True, text=True, timeout=20,
        )
    monkeypatch.setattr(auto, "_run", run)
    assert auto._doctor_healthy() == {"ok": True, "detail": "ready"}


@pytest.mark.parametrize(("mode", "target", "live_state", "allowed"), [
    ("paper_internal", "local_paper", "live_disabled", False),
    ("bitget_demo", "external_exchange", "live_disabled", True),
    ("bitget_demo", "local_paper", "live_disabled", False),
    ("paper_internal", "external_exchange", "live_disabled", False),
    ("binance_demo", "external_exchange", "live_disabled", False),
    ("bitget_live", "external_exchange", "live_disabled", False),
    ("bitget_demo", "external_exchange", "live_enabled", False),
])
def test_runtime_gate_uses_public_health_response(monkeypatch, mode, target, live_state, allowed):
    from aletheia.update import auto

    responses = {
        "/api/health": {"status": "healthy", "issues": [], "version": "1.2.3"},
        "/api/status": {
            "gateway_running": True,
            "scheduler_running": True,
            "paper_execution_mode": mode,
            "execution_target": target,
            "live_state": live_state,
            "boot_commit": "target-sha",
            "current_commit": "target-sha",
            "version": "1.2.3",
        },
    }
    monkeypatch.setattr(auto, "_http_json", lambda path: responses[path])

    assert auto._runtime_gate(
        expected_sha="target-sha", expected_version="1.2.3"
    )["ok"] is allowed
    responses["/api/health"]["status"] = "degraded"
    assert auto._runtime_gate()["ok"] is False


def test_runtime_diagnostics_ignore_unrelated_warnings_but_keep_deployment_gates():
    from aletheia.update.auto import _evaluate_runtime_diagnostics

    report = {
        "checks": [
            {"name": "llm.inference", "severity": "ok", "detail": "34/34 succeeded"},
            {"name": "content.signal_settlement", "severity": "info", "detail": "within settler cadence"},
            {"name": "content.lesson_chain", "severity": "warn", "detail": "backlog"},
        ],
        "summary": {"blockers": [], "warns": ["content.lesson_chain"]},
    }

    assert _evaluate_runtime_diagnostics(report, {"current": True}) == {"ok": True, "detail": "ready"}
    report["checks"][0]["severity"] = "warn"
    assert _evaluate_runtime_diagnostics(report, {"current": True}) == {
        "ok": False,
        "detail": "llm.inference",
    }
    report["checks"][0]["severity"] = "ok"
    assert _evaluate_runtime_diagnostics(report, {"current": False}) == {
        "ok": False,
        "detail": "schema.migrations",
    }


def test_success_removes_previous_static_tree(tmp_path, monkeypatch):
    from aletheia.update import auto, git_ops

    _linux_source(monkeypatch)
    activation_events: list[str] = []
    _successful_activation_boundary(monkeypatch, activation_events)
    monkeypatch.setattr(auto, "_paths", lambda: _state_paths(tmp_path))
    monkeypatch.setattr(git_ops, "tag_names", lambda: ["v1.1.0"])
    monkeypatch.setattr(git_ops, "tag_commit", lambda tag: "target-sha")
    monkeypatch.setattr(
        git_ops, "version_at_ref", lambda ref: '__version__ = "1.0.0"\n' if ref == "HEAD" else '__version__ = "1.1.0"\n'
    )
    monkeypatch.setattr(auto, "_runtime_gate", lambda **kwargs: {"ok": True})
    monkeypatch.setattr(auto, "_doctor_healthy", lambda: {"ok": True})
    service = MagicMock()
    service.create_backup.side_effect = [tmp_path / "preflight", tmp_path / "activation"]
    monkeypatch.setattr(auto, "BackupService", lambda data_dir: service)
    monkeypatch.setattr(auto, "_candidate_preflight", lambda target, backup: tmp_path / "candidate")
    monkeypatch.setattr(auto, "_sync_candidate_dependencies", lambda worktree: None)
    monkeypatch.setattr(auto, "_build_candidate_frontend", lambda *args, **kwargs: None)
    monkeypatch.setattr(auto, "_remove_candidate", lambda worktree: None)
    monkeypatch.setattr(
        auto,
        "_gateway_command",
        lambda action: activation_events.append(f"gateway:{action}") or {"ok": True},
    )
    monkeypatch.setattr(
        git_ops,
        "merge_fast_forward",
        lambda target: activation_events.append("source:activate") or {"ok": True},
    )
    monkeypatch.setattr(auto, "_install_source_dependencies", lambda: True)
    monkeypatch.setattr(auto, "_activate_frontend_build", lambda: True)
    monkeypatch.setattr(
        auto,
        "_wait_for_runtime",
        lambda sha, version: activation_events.append("runtime:verified") or {"ok": True},
    )
    cleaned = []
    monkeypatch.setattr(auto.shutil, "rmtree", lambda path, **kwargs: cleaned.append(path))

    assert auto.run()["reason"] == "deployed"
    assert auto._PREVIOUS_DASHBOARD_STATIC in cleaned
    assert activation_events == [
        "gateway:stop",
        "barrier:enter",
        "source:activate",
        "gateway:start",
        "runtime:verified",
        "reanchor",
        "barrier:exit",
    ]
    assert [call.kwargs["profile"] for call in service.create_backup.call_args_list] == [
        "online",
        "authoritative",
    ]


def test_rollback_stops_candidate_before_restoring(monkeypatch, tmp_path):
    from aletheia.update import auto

    calls = []
    monkeypatch.setattr(auto, "_gateway_command", lambda action: calls.append(action) or {"ok": True})
    monkeypatch.setattr(auto, "_restore_old_runtime", lambda *args, **kwargs: calls.append("restore") or {"ok": True})

    assert auto._rollback_to_old_runtime("old", tmp_path / "backup", True, restore_static=True)["ok"] is True
    assert calls == ["stop", "restore"]


def test_gateway_command_start_slow_start_is_ok(monkeypatch):
    """exit 2 from _gateway_command('start') is slow start, not failure."""
    from aletheia.update import auto

    class _FakeProc:
        def __init__(self, rc):
            self.returncode = rc
            self.stdout = ""
            self.stderr = ""

    monkeypatch.setattr(auto, "_run", lambda *args, **kwargs: _FakeProc(2))
    result = auto._gateway_command("start")
    assert result["ok"] is True
    assert result["reason"] is None

    monkeypatch.setattr(auto, "_run", lambda *args, **kwargs: _FakeProc(1))
    result = auto._gateway_command("start")
    assert result["ok"] is False
    assert result["reason"] == "gateway_start_failed"

    # stop never emits exit 2; exit 2 for stop is a genuine failure
    monkeypatch.setattr(auto, "_run", lambda *args, **kwargs: _FakeProc(2))
    result = auto._gateway_command("stop")
    assert result["ok"] is False


def test_auto_run_command_timeout_is_classified(monkeypatch):
    from aletheia.update import auto

    monkeypatch.setattr(
        auto.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired(["uv"], 300)),
    )

    result = auto._run(["uv"], cwd=Path("."), timeout=3)

    assert result.returncode == 124
    assert "3s" in result.stderr


def test_nonblocking_flock_excludes_concurrent_timer(tmp_path, monkeypatch):
    from aletheia.update import auto

    paths = _state_paths(tmp_path)
    monkeypatch.setattr(auto, "_paths", lambda: paths)
    paths["root"].mkdir(parents=True)
    with paths["lock"].open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with auto._deployment_lock() as acquired:
            assert acquired is False


def test_systemd_units_are_user_timer_contract(monkeypatch):
    from aletheia.update import auto

    monkeypatch.setattr(auto, "_uv", lambda: "/usr/bin/uv")
    units = auto._units()

    assert "Type=oneshot" in units["aletheia-auto-update.service"]
    assert "update auto run" in units["aletheia-auto-update.service"]
    assert "OnBootSec=1min" in units["aletheia-auto-update.timer"]
    assert "OnUnitInactiveSec=5min" in units["aletheia-auto-update.timer"]
    assert "Persistent=true" in units["aletheia-auto-update.timer"]
    assert "RandomizedDelaySec=30s" in units["aletheia-auto-update.timer"]


def test_systemctl_timeout_has_stable_reason(monkeypatch):
    from aletheia.update import auto

    monkeypatch.setattr(
        auto.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(["systemctl"], auto._SYSTEMCTL_TIMEOUT_SEC)
        ),
    )

    with pytest.raises(auto.AutoUpdateError) as error:
        auto._systemctl("daemon-reload")

    assert error.value.reason == "systemd_user_operation_timeout"


def test_auto_update_requires_explicit_paper_runtime_role(monkeypatch):
    import aletheia.update.auto as auto

    monkeypatch.setattr(auto.sys, "platform", "linux")
    monkeypatch.setattr(auto.git_ops, "is_repo", lambda: True)
    monkeypatch.delenv("ALETHEIA_RUNTIME_ROLE", raising=False)
    result = auto.run()
    assert result["ok"] is True
    assert result["reason"] == "qualification_runtime_pinned"
    assert "migrate an existing Paper timer" in result["detail"]

    monkeypatch.setenv("ALETHEIA_RUNTIME_ROLE", "paper")
    monkeypatch.setattr(auto, "_uv", lambda: "/usr/bin/uv")
    assert "Environment=ALETHEIA_RUNTIME_ROLE=paper" in auto._units()["aletheia-auto-update.service"]


def test_systemd_install_and_owned_uninstall_are_idempotent(tmp_path, monkeypatch):
    from aletheia.update import auto, git_ops

    monkeypatch.setattr(auto.sys, "platform", "linux")
    monkeypatch.setenv("ALETHEIA_RUNTIME_ROLE", "paper")
    monkeypatch.setattr(git_ops, "is_repo", lambda: True)
    monkeypatch.setattr(auto, "_unit_dir", lambda: tmp_path / "units")
    monkeypatch.setattr(auto, "_uv", lambda: "/usr/bin/uv")
    systemctl_calls = []
    monkeypatch.setattr(auto, "_systemctl", lambda *args: systemctl_calls.append(args))

    assert auto.install()["reason"] == "installed"
    assert auto.install()["reason"] == "installed"
    assert (tmp_path / "units" / "aletheia-auto-update.timer").exists()
    assert auto.uninstall()["reason"] == "uninstalled"
    assert auto.uninstall()["reason"] == "not_installed"
    assert ("enable", "--now", "aletheia-auto-update.timer") in systemctl_calls
    assert ("disable", "--now", "aletheia-auto-update.timer") in systemctl_calls


def test_systemd_install_refuses_unowned_unit_and_uninstall_needs_no_uv(tmp_path, monkeypatch):
    from aletheia.update import auto, git_ops

    monkeypatch.setattr(auto.sys, "platform", "linux")
    monkeypatch.setenv("ALETHEIA_RUNTIME_ROLE", "paper")
    monkeypatch.setattr(git_ops, "is_repo", lambda: True)
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    monkeypatch.setattr(auto, "_unit_dir", lambda: unit_dir)
    service = unit_dir / "aletheia-auto-update.service"
    service.write_text("operator-owned", encoding="utf-8")

    assert auto.install()["reason"] == "systemd_unit_ownership_conflict"
    assert service.read_text(encoding="utf-8") == "operator-owned"

    service.write_text(auto._SYSTEMD_MARKER, encoding="utf-8")
    monkeypatch.setattr(auto, "_systemctl", lambda *args: None)
    monkeypatch.setattr(auto, "_uv", lambda: pytest.fail("uninstall must not resolve uv"))
    assert auto.uninstall()["reason"] == "uninstalled"


def test_systemd_install_requires_paper_runtime_role(tmp_path, monkeypatch):
    from aletheia.update import auto, git_ops

    monkeypatch.setattr(auto.sys, "platform", "linux")
    monkeypatch.delenv("ALETHEIA_RUNTIME_ROLE", raising=False)
    monkeypatch.setattr(git_ops, "is_repo", lambda: True)
    monkeypatch.setattr(auto, "_unit_dir", lambda: tmp_path / "units")

    assert auto.install() == {"ok": False, "reason": "paper_runtime_role_required"}
    assert not (tmp_path / "units").exists()


def test_systemd_install_migrates_owned_legacy_timer(tmp_path, monkeypatch):
    from aletheia.update import auto, git_ops

    monkeypatch.setattr(auto.sys, "platform", "linux")
    monkeypatch.setenv("ALETHEIA_RUNTIME_ROLE", "paper")
    monkeypatch.delenv("ALETHEIA_DATA_DIR", raising=False)
    monkeypatch.setattr(git_ops, "is_repo", lambda: True)
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    (unit_dir / "aletheia-auto-update.service").write_text(
        auto._SYSTEMD_MARKER + "\nEnvironment=ALETHEIA_DATA_DIR=/srv/aletheia-data\nlegacy",
        encoding="utf-8",
    )
    (unit_dir / "aletheia-auto-update.timer").write_text(
        auto._SYSTEMD_MARKER + "\nlegacy", encoding="utf-8"
    )
    monkeypatch.setattr(auto, "_unit_dir", lambda: unit_dir)
    monkeypatch.setattr(auto, "_uv", lambda: "/usr/bin/uv")
    monkeypatch.setattr(auto, "_systemctl", lambda *args: None)

    assert auto.install()["reason"] == "installed"
    assert "Environment=ALETHEIA_RUNTIME_ROLE=paper" in (
        unit_dir / "aletheia-auto-update.service"
    ).read_text(encoding="utf-8")
    assert "Environment=ALETHEIA_DATA_DIR=/srv/aletheia-data" in (
        unit_dir / "aletheia-auto-update.service"
    ).read_text(encoding="utf-8")
    assert "OnUnitInactiveSec=5min" in (unit_dir / "aletheia-auto-update.timer").read_text(encoding="utf-8")


def test_resume_audits_the_operator(tmp_path, monkeypatch):
    from aletheia.update import auto

    _linux_source(monkeypatch)
    monkeypatch.setattr(auto, "_paths", lambda: _state_paths(tmp_path))
    auto._write_state({}, hold=True)
    audits = []
    monkeypatch.setattr(auto, "_record_audit", lambda *args, **kwargs: audits.append((args, kwargs)))

    assert auto.resume()["ok"] is True
    assert audits[-1][1] == {"actor": "operator", "source": "cli"}


def test_auto_run_reports_failures_to_systemd(monkeypatch):
    from aletheia.cli import update_cmd

    monkeypatch.setattr("aletheia.update.auto.run", lambda: {"ok": False, "reason": "global_hold"})

    result = CliRunner().invoke(update_cmd.update_app, ["auto", "run"])

    assert result.exit_code == 1
    assert result.stdout.strip() == "global_hold"
def test_source_restore_invalidates_unproven_observation_continuity(monkeypatch, tmp_path):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from aletheia.observation_lab import kernel, worker_lock
    from aletheia.update import auto, git_ops

    calls: list[str] = []

    @contextmanager
    def guard():
        calls.append("lock-enter")
        yield
        calls.append("lock-exit")

    monkeypatch.setattr(git_ops, "reset_hard", lambda old: {"ok": True})
    monkeypatch.setattr(auto, "_install_source_dependencies", lambda: True)
    monkeypatch.setattr(auto, "get_data_dir", lambda: tmp_path)
    monkeypatch.setattr(
        auto,
        "RestoreService",
        lambda: SimpleNamespace(
            restore=lambda *_args: calls.append("restore") or SimpleNamespace(success=True),
        ),
    )
    monkeypatch.setattr(auto, "_gateway_command", lambda action: {"ok": True})
    monkeypatch.setattr(auto, "_version_from_ref", lambda old: "1.0.0")
    monkeypatch.setattr(auto, "_wait_for_runtime", lambda *_args: {"ok": True, "reason": "restored"})
    monkeypatch.setattr(worker_lock, "worker_cycle_exclusion", guard)
    monkeypatch.setattr(
        kernel,
        "invalidate_active_for_restore",
        lambda *, joint_cut_proven: calls.append(f"invalidate:{joint_cut_proven}") or 1,
    )

    result = auto._restore_old_runtime("old", tmp_path / "backup", False, restore_static=False)

    assert result == {"ok": True, "reason": "restored"}
    assert calls == ["lock-enter", "restore", "invalidate:False", "lock-exit"]
