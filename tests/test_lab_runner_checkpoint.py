"""Runner checkpoint wiring (G3) tests.

Cluster 5 built the ``verify_joint_checkpoint`` primitive; this file
proves the runner actually invokes it every pass, records the
fingerprint chain, and stays failure-isolated when the checkpoint step
itself raises.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def clean_registry():
    from aletheia.observation_lab.runner import _WINDOWS, registered_windows

    snapshot = list(registered_windows())
    _WINDOWS.clear()
    yield
    _WINDOWS.clear()
    _WINDOWS.extend(snapshot)


def test_runner_saves_and_reports_joint_checkpoint(clean_registry):
    from aletheia.observation_lab.checkpoint import latest_joint_checkpoint
    from aletheia.observation_lab.runner import LabWindow, register_window, run_lab_windows

    register_window(LabWindow(name="noop", kind="test", fn=lambda: {"status": "ok"}))
    summary = run_lab_windows()

    assert "joint_checkpoint" in summary
    cp = summary["joint_checkpoint"]
    assert cp["ok"] is True
    # First pass on a fresh ledger reports first_checkpoint, then saves it.
    assert cp["reason"] in {"first_checkpoint", "fingerprint_match"}
    assert cp["current_fingerprint"] is not None

    stored = latest_joint_checkpoint()
    assert stored is not None
    assert stored.joint_fingerprint == cp["current_fingerprint"]


def test_runner_reports_mismatch_on_manifest_change(clean_registry):
    """Publishing a new active manifest between passes surfaces as ``ok=False``."""
    from aletheia.observation_lab.manifest import build_manifest, publish_manifest
    from aletheia.observation_lab.runner import LabWindow, register_window, run_lab_windows

    register_window(LabWindow(name="noop", kind="test", fn=lambda: {"status": "ok"}))

    # First pass — baseline checkpoint recorded.
    first = run_lab_windows()
    assert first["joint_checkpoint"]["ok"] is True

    # Publish a manifest between passes, but do NOT rerun the runner yet.
    manifest = build_manifest(
        observation_id="drift-check",
        generation=1,
        spec_hash="s",
        treatment_adapter_id="t",
        treatment_adapter_version="v1",
        assignment_policy="stable",
        assignment_salt="salt",
        assignment_unit_key_template="{k}",
        allocation_pct=0.5,
        control_payload={"m": 1.0},
        treatment_payload={"m": 1.1},
        eligible_population={"path_id": "test"},
        behavior_contract_hash="bh",
        interference_key="ifk",
        composability_contract="exclusive",
        effective_from="2020-01-01T00:00:00+00:00",
        deterministic_baseline={"m": 1.0},
        unavailable_fallback="baseline",
    )
    publish_manifest(manifest)

    # Second pass sees the drift versus the last stored fingerprint.
    second = run_lab_windows()
    assert second["joint_checkpoint"]["ok"] is False
    assert "fingerprint_mismatch" in second["joint_checkpoint"]["reason"]

    # But it also SAVES the new fingerprint, so a third pass reads clean again.
    third = run_lab_windows()
    assert third["joint_checkpoint"]["ok"] is True


def test_runner_isolates_checkpoint_error_from_window_execution(clean_registry):
    """A raise inside the checkpoint step must not stop windows from running."""
    from unittest.mock import patch

    from aletheia.observation_lab.runner import LabWindow, register_window, run_lab_windows

    ran = {"count": 0}

    def _fn():
        ran["count"] += 1
        return {"status": "ok"}

    register_window(LabWindow(name="alpha", kind="test", fn=_fn))

    with patch(
        "aletheia.observation_lab.checkpoint.verify_joint_checkpoint",
        side_effect=RuntimeError("simulated checkpoint failure"),
    ):
        summary = run_lab_windows()

    # Window still ran; checkpoint step reported the error.
    assert ran["count"] == 1
    assert summary["windows_run"] == 1
    assert summary["joint_checkpoint"]["ok"] is False
    assert "checkpoint_error" in summary["joint_checkpoint"]["reason"]
