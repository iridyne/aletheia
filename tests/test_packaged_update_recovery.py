from __future__ import annotations

import json
import os
from pathlib import Path
import time

from aletheia.update import packaged, packaged_commands


def _paths(root: Path) -> dict[str, Path]:
    return {
        "root": root,
        "releases": root / "releases",
        "downloads": root / "downloads",
        "preflight": root / "preflight",
        "current": root / "current.json",
        "state": root / "update-state.json",
        "request": root / "update-request.json",
        "lock": root / "update-stage.lock",
        "feed_url": root / "update-feed-url.txt",
        "public_key": root / "update-public-key.pem",
    }


def test_dead_staging_phase_is_recoverable(monkeypatch, tmp_path):
    paths = _paths(tmp_path)
    monkeypatch.setattr(packaged, "_paths", lambda: paths)
    old_state = {
        "schema_version": 1,
        "operation_id": "update-dead",
        "phase": "downloading",
        "status": "running",
        "updated_at": "2026-07-16T00:00:00Z",
    }
    paths["state"].write_text(json.dumps(old_state), encoding="utf-8")

    assert packaged_commands._operation_is_really_active(old_state) is False


def test_live_stage_lock_keeps_operation_exclusive(monkeypatch, tmp_path):
    paths = _paths(tmp_path)
    monkeypatch.setattr(packaged, "_paths", lambda: paths)
    monkeypatch.setattr(packaged, "_process_alive", lambda pid: pid == os.getpid())
    paths["lock"].write_text(
        json.dumps({"operation_id": "update-live", "pid": os.getpid()}),
        encoding="utf-8",
    )
    state = {
        "operation_id": "update-live",
        "phase": "preflight",
        "status": "running",
        "updated_at": f"unix:{time.time()}",
    }

    assert packaged_commands._operation_is_really_active(state) is True


def test_switch_request_remains_recoverable_without_python_worker(monkeypatch, tmp_path):
    paths = _paths(tmp_path)
    monkeypatch.setattr(packaged, "_paths", lambda: paths)
    paths["request"].write_text(
        json.dumps(
            {
                "schema_version": 1,
                "operation_id": "update-switch",
                "action": "activate",
                "candidate_release": "0.5.1+new",
                "previous_release": "0.5.0+old",
            }
        ),
        encoding="utf-8",
    )
    state = {
        "operation_id": "update-switch",
        "phase": "starting",
        "status": "running",
        "updated_at": "2026-07-16T00:00:00Z",
    }

    assert packaged_commands._operation_is_really_active(state) is True


def test_orphaned_switch_phase_without_request_can_be_retried(monkeypatch, tmp_path):
    paths = _paths(tmp_path)
    monkeypatch.setattr(packaged, "_paths", lambda: paths)
    state = {
        "operation_id": "update-orphaned",
        "phase": "rolling_back",
        "status": "running",
        "updated_at": "2026-07-16T00:00:00Z",
    }

    assert packaged_commands._operation_is_really_active(state) is False
