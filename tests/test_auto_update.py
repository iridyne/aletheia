"""Focused contracts for Fedora release-tag auto deployment."""

from __future__ import annotations

import fcntl
from pathlib import Path
import subprocess
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner


def _state_paths(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / "update"
    return {"root": root, "state": root / "auto-deploy.json", "lock": root / "auto-deploy.lock"}


def _linux_source(monkeypatch):
    from aletheia.update import auto, git_ops

    monkeypatch.setattr(auto.sys, "platform", "linux")
    monkeypatch.setattr(git_ops, "is_repo", lambda: True)
    monkeypatch.setattr(git_ops, "fetch_release_tags", lambda: {"ok": True})
    monkeypatch.setattr(git_ops, "is_worktree_dirty", lambda: False)
    monkeypatch.setattr(git_ops, "current_branch", lambda: "master")
    monkeypatch.setattr(git_ops, "current_commit", lambda: "old-sha")
    monkeypatch.setattr(git_ops, "is_ancestor", lambda older, newer: True)
    monkeypatch.setattr(git_ops, "remote_tag_names", lambda: set(git_ops.tag_names()))
    monkeypatch.setattr(auto, "_record_audit", lambda *args, **kwargs: None)


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


def test_runtime_gate_uses_public_health_response(monkeypatch):
    from aletheia.update import auto

    responses = {
        "/api/health": {"status": "healthy", "issues": [], "version": "1.2.3"},
        "/api/status": {
            "gateway_running": True,
            "scheduler_running": True,
            "paper_execution_mode": "paper_internal",
            "execution_target": "local_paper",
            "live_state": "live_disabled",
            "boot_commit": "target-sha",
            "current_commit": "target-sha",
            "version": "1.2.3",
        },
    }
    monkeypatch.setattr(auto, "_http_json", lambda path: responses[path])

    assert auto._runtime_gate(
        expected_sha="target-sha", expected_version="1.2.3"
    ) == {"ok": True, "reason": None, "failed": []}
    responses["/api/health"]["status"] = "degraded"
    assert auto._runtime_gate()["failed"] == ["health"]


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
    monkeypatch.setattr(auto, "_gateway_command", lambda action: {"ok": True})
    monkeypatch.setattr(git_ops, "merge_fast_forward", lambda target: {"ok": True})
    monkeypatch.setattr(auto, "_install_source_dependencies", lambda: True)
    monkeypatch.setattr(auto, "_activate_frontend_build", lambda: True)
    monkeypatch.setattr(auto, "_wait_for_runtime", lambda sha, version: {"ok": True})
    cleaned = []
    monkeypatch.setattr(auto.shutil, "rmtree", lambda path, **kwargs: cleaned.append(path))

    assert auto.run()["reason"] == "deployed"
    assert auto._PREVIOUS_DASHBOARD_STATIC in cleaned


def test_rollback_stops_candidate_before_restoring(monkeypatch, tmp_path):
    from aletheia.update import auto

    calls = []
    monkeypatch.setattr(auto, "_gateway_command", lambda action: calls.append(action) or {"ok": True})
    monkeypatch.setattr(auto, "_restore_old_runtime", lambda *args, **kwargs: calls.append("restore") or {"ok": True})

    assert auto._rollback_to_old_runtime("old", tmp_path / "backup", True, restore_static=True)["ok"] is True
    assert calls == ["stop", "restore"]


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
    assert "OnCalendar=hourly" in units["aletheia-auto-update.timer"]
    assert "Persistent=true" in units["aletheia-auto-update.timer"]
    assert "RandomizedDelaySec=10m" in units["aletheia-auto-update.timer"]


def test_systemd_install_and_owned_uninstall_are_idempotent(tmp_path, monkeypatch):
    from aletheia.update import auto, git_ops

    monkeypatch.setattr(auto.sys, "platform", "linux")
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
