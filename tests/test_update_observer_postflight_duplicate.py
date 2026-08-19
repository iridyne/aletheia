"""Postflight duplicate-owner regression for Issue #1135."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.tier_unit


def _snapshot(*, disk_sha: str, gateway_sha: str, main_cycle_registered: bool) -> dict:
    return {
        "schema_version": "update_observer_continuity/v1",
        "capture_available": True,
        "capture_source": "fresh_target_process",
        "control": {
            "available": True,
            "disk_sha": disk_sha,
            "data_root": {"available": True, "identity": "sha256:data"},
        },
        "gateway": {
            "available": True,
            "boot_commit": gateway_sha,
            "current_commit": gateway_sha,
            "health_status": "healthy",
            "health_issues": [],
            "observation_execution": {
                "available": True,
                "owner": "worker",
                "job_id": "filter_controller_observe",
                "main_cycle_registered": main_cycle_registered,
                "exclusive": not main_cycle_registered,
            },
        },
        "observation": {
            "owner": "worker",
            "worker": {"available": True, "source_commit": "worker999"},
            "overview_available": True,
            "identities": {"available": True, "ids": ["obs-a"], "total": 1},
            "manifest_generations": {"obs-a": 2},
            "checkpoint": {"verified": {"ok": True, "reason": "ok"}},
        },
    }


def test_postflight_detects_main_reclaiming_worker_owned_cycle():
    from aletheia.update.continuity import compare_update_continuity

    result = compare_update_continuity(
        _snapshot(
            disk_sha="old111",
            gateway_sha="old111",
            main_cycle_registered=False,
        ),
        _snapshot(
            disk_sha="new222",
            gateway_sha="new222",
            main_cycle_registered=True,
        ),
        expected_target="new222",
        restart=True,
    )

    assert result["ok"] is False
    assert "duplicate_observation_cycle_owner" in result["failures"]
