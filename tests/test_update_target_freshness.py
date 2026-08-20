"""Regressions for post-pull target-code/config freshness (Issue #1135)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


def test_check_for_update_mutates_latest_config_not_prepull_snapshot(monkeypatch):
    from aletheia.config import loader
    from aletheia.config.schema import AletheiaConfig
    from aletheia.update import applier, git_ops

    pre_pull = AletheiaConfig()
    pre_pull.update.branch = "master"
    pre_pull.update.enabled = True
    pre_pull.runtime.paused = False

    latest = AletheiaConfig()
    latest.runtime.paused = True  # concurrent/unrelated durable change

    monkeypatch.setattr(applier, "load_config", lambda: pre_pull)
    monkeypatch.setattr(git_ops, "fetch", lambda branch: {"ok": True})
    monkeypatch.setattr(git_ops, "current_commit", lambda: "old-sha")
    monkeypatch.setattr(git_ops, "remote_commit", lambda branch: "new-sha")
    monkeypatch.setattr(git_ops, "behind_count", lambda branch: 1)
    monkeypatch.setattr(applier, "write_audit", lambda *args, **kwargs: None)

    def mutate(mutator):
        return mutator(latest)

    monkeypatch.setattr(loader, "mutate_config", mutate)

    result = applier.check_for_update()

    assert result["has_update"] is True
    assert latest.runtime.paused is True
    assert latest.update.current_commit == "old-sha"
    assert latest.update.last_known_remote_commit == "new-sha"
    # The stale object used for eligibility was never repurposed as the write base.
    assert pre_pull.update.current_commit != "old-sha"


def test_target_fresh_metadata_helper_passes_only_commit_and_data_root(monkeypatch, tmp_path):
    from aletheia.update import target_fresh

    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout=target_fresh._RESULT_PREFIX + '{"ok": true, "changed": true}\n',
            stderr="",
        )

    monkeypatch.setattr(target_fresh.subprocess, "run", run)
    result = target_fresh.persist_current_commit(
        "target-sha",
        project_root=tmp_path / "checkout",
        data_dir=tmp_path / "data",
        env={"SENTINEL": "kept"},
        python_executable="python-test",
    )

    assert result == {"ok": True, "changed": True}
    command, kwargs = calls[0]
    assert command[:2] == ["python-test", "-c"]
    assert "from aletheia.config.loader import mutate_config" in command[2]
    assert "target-sha" in command[2]
    assert kwargs["cwd"] == str(tmp_path / "checkout")
    assert kwargs["env"]["ALETHEIA_DATA_DIR"] == str(tmp_path / "data")
    assert kwargs["env"]["SENTINEL"] == "kept"


def test_target_fresh_smoke_loads_target_smoke_module(monkeypatch, tmp_path):
    from aletheia.update import target_fresh

    seen = {}

    def run(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout=(
                target_fresh._RESULT_PREFIX
                + '{"ok": true, "stage": "done", "detail": ""}\n'
            ),
            stderr="",
        )

    monkeypatch.setattr(target_fresh.subprocess, "run", run)
    result = target_fresh.run_smoke_check(
        project_root=tmp_path,
        env={},
        python_executable="python-test",
    )

    assert result["ok"] is True
    assert "from aletheia.update.smoke import run_smoke_check" in seen["command"][2]
    assert seen["kwargs"]["cwd"] == str(tmp_path)


def test_target_fresh_dashboard_validation_loads_target_validator(monkeypatch, tmp_path):
    from aletheia.update import target_fresh

    seen = {}

    def run(command, **kwargs):
        seen["script"] = command[2]
        return SimpleNamespace(
            returncode=0,
            stdout=target_fresh._RESULT_PREFIX + '{"ok": true, "errors": []}\n',
            stderr="",
        )

    monkeypatch.setattr(target_fresh.subprocess, "run", run)
    static_dir = tmp_path / "static"
    result = target_fresh.validate_dashboard_artifacts(
        static_dir,
        project_root=tmp_path,
        env={},
        python_executable="python-test",
    )

    assert result["ok"] is True
    assert "from aletheia.gateway.preflight import dashboard_artifact_errors" in seen["script"]
    assert str(static_dir) in seen["script"]


def test_apply_update_persists_target_commit_without_saving_prepull_config(monkeypatch):
    from aletheia.config.schema import AletheiaConfig
    from aletheia.update import applier, git_ops

    pre_pull = AletheiaConfig()
    pre_pull.update.enabled = True
    pre_pull.update.branch = "master"
    pre_pull.runtime.paused = True  # stale snapshot marker

    monkeypatch.setattr(applier, "load_config", lambda: pre_pull)
    monkeypatch.setattr(git_ops, "is_worktree_dirty", lambda: False)
    monkeypatch.setattr(git_ops, "current_branch", lambda: "master")
    monkeypatch.setattr(git_ops, "current_commit", lambda: "old-sha")
    monkeypatch.setattr(
        git_ops,
        "pull",
        lambda branch: {"ok": True, "remote_commit": "target-sha"},
    )
    monkeypatch.setattr(git_ops, "changed_files", lambda old, new: ["aletheia/x.py"])
    monkeypatch.setattr(applier, "run_smoke_check", lambda: {"ok": True, "stage": "done", "detail": ""})
    monkeypatch.setattr(applier, "write_audit", lambda *args, **kwargs: None)
    persisted = []
    monkeypatch.setattr(
        applier,
        "_persist_current_commit_fresh",
        lambda commit: persisted.append(commit) or {"ok": True, "changed": True},
    )

    result = applier.apply_update(restart=False)

    assert result["applied"] is True
    assert result["metadata_persisted"] is True
    assert persisted == ["target-sha"]
    # The pre-pull config object stays only an eligibility snapshot.
    assert pre_pull.runtime.paused is True


def _snapshot(*, disk_sha: str, health: str = "healthy") -> dict:
    return {
        "capture_available": True,
        "capture_source": "fresh_target_process",
        "control": {
            "available": True,
            "disk_sha": disk_sha,
            "data_root": {"available": True, "identity": "root"},
        },
        "gateway": {
            "available": True,
            "boot_commit": disk_sha,
            "current_commit": disk_sha,
            "health_status": health,
            "health_issues": [],
            "observation_execution": {
                "available": True,
                "owner": "worker",
                "exclusive": True,
                "main_cycle_registered": False,
            },
        },
        "observation": {
            "owner": "worker",
            "overview_available": True,
            "worker": {"available": True, "source_commit": "worker-sha"},
            "identities": {"available": True, "ids": ["obs"]},
            "manifest_generations": {"obs": 1},
            "checkpoint": {"verified": {"ok": True, "reason": "fingerprint_match"}},
        },
    }


def test_metadata_persist_failure_forces_continuity_unproven(monkeypatch):
    from aletheia.update import applier, continuity

    before = _snapshot(disk_sha="old-sha")
    before["capture_source"] = "in_process"
    after = _snapshot(disk_sha="target-sha")

    monkeypatch.setattr(continuity, "capture_update_continuity", lambda: before)
    monkeypatch.setattr(
        continuity,
        "capture_update_continuity_until_settled",
        lambda target: after,
    )
    monkeypatch.setattr(
        applier,
        "apply_update",
        lambda *, restart, progress=None: {
            "applied": True,
            "mode": "development",
            "old_commit": "old-sha",
            "new_commit": "target-sha",
            "deps_reinstalled": False,
            "metadata_persisted": False,
            "metadata_error": "config_write_lock_timeout",
        },
    )
    monkeypatch.setattr(continuity, "_record", lambda *args, **kwargs: None)

    result = continuity.apply_update_with_continuity(restart=True)

    assert result["applied"] is True
    assert result["continuity_verified"] is False
    assert result["continuity_state"] == "unproven"
    assert "update_metadata_persist_unverified" in result["continuity"]["comparison"]["proof_gaps"]


def test_metadata_failure_does_not_turn_applied_update_into_false_rollback(monkeypatch):
    from aletheia.config.schema import AletheiaConfig
    from aletheia.update import applier, git_ops

    cfg = AletheiaConfig()
    cfg.update.enabled = True
    cfg.update.branch = "master"
    monkeypatch.setattr(applier, "load_config", lambda: cfg)
    monkeypatch.setattr(git_ops, "is_worktree_dirty", lambda: False)
    monkeypatch.setattr(git_ops, "current_branch", lambda: "master")
    monkeypatch.setattr(git_ops, "current_commit", lambda: "old-sha")
    monkeypatch.setattr(git_ops, "pull", lambda branch: {"ok": True, "remote_commit": "target-sha"})
    monkeypatch.setattr(git_ops, "changed_files", lambda old, new: ["aletheia/x.py"])
    monkeypatch.setattr(applier, "run_smoke_check", lambda: {"ok": True, "stage": "done", "detail": ""})
    monkeypatch.setattr(applier, "write_audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        applier,
        "_persist_current_commit_fresh",
        lambda commit: {"ok": False, "reason": "target_fresh_process_failed", "detail": "boom"},
    )

    result = applier.apply_update(restart=False)

    assert result["applied"] is True
    assert result["new_commit"] == "target-sha"
    assert result["metadata_persisted"] is False
    assert result["metadata_error"] == "boom"
