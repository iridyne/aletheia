"""Focused contracts for Fedora release-tag auto deployment."""

from __future__ import annotations

import fcntl
from pathlib import Path
from unittest.mock import MagicMock

import pytest


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
