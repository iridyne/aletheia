"""Focused tests for #1208: strict dual-commit identity in compare_update_continuity."""
from __future__ import annotations


def _snap(
    *,
    boot_commit: str | None = "abc111",
    current_commit: str | None = "abc111",
    gateway_available: bool = True,
    health_status: str = "healthy",
):
    return {
        "capture_available": True,
        "capture_source": "fresh_target_process",
        "control": {
            "available": True,
            "disk_sha": boot_commit,
            "data_root": {"available": True, "identity": "sha256:data"},
        },
        "gateway": {
            "available": gateway_available,
            "boot_commit": boot_commit,
            "current_commit": current_commit,
            "health_status": health_status,
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
            "exclusive_owner_contract": True,
            "worker": {"available": True, "source_commit": boot_commit},
            "overview_available": True,
            "identities": {"available": True, "ids": ["obs-a"], "total": 1},
            "manifest_generations": {"obs-a": 1},
            "checkpoint": {"verified": {"ok": True, "reason": "ok"}},
            "experiments": {"total": 1, "collecting": 1, "ready_for_review": 0, "invalidated": 0},
            "ufc_equivalence": {"recent_total": 1, "recent_matched": 1},
        },
    }


TARGET = "abc111"


def _compare(before, after, *, expected_target=TARGET, restart=True):
    from aletheia.update.continuity import compare_update_continuity
    return compare_update_continuity(before, after, expected_target=expected_target, restart=restart)


# ── boot_commit missing → identity_unproven ───────────────────────────────

def test_boot_commit_missing_identity_unproven():
    before = _snap()
    after = _snap(boot_commit=None, current_commit=TARGET)
    result = _compare(before, after)
    assert "boot_commit_missing" in result["proof_gaps"]
    assert result.get("continuity_verified") is False


# ── current_commit missing → identity_unproven ───────────────────────────

def test_current_commit_missing_identity_unproven():
    before = _snap()
    after = _snap(boot_commit=TARGET, current_commit=None)
    result = _compare(before, after)
    assert "current_commit_missing" in result["proof_gaps"]
    assert result.get("continuity_verified") is False


# ── both missing → identity_unproven ────────────────────────────────────

def test_both_commits_missing_identity_unproven():
    before = _snap()
    after = _snap(boot_commit=None, current_commit=None)
    result = _compare(before, after)
    assert "boot_commit_missing" in result["proof_gaps"]
    assert "current_commit_missing" in result["proof_gaps"]
    assert "identity_unproven" in result["proof_gaps"]
    assert result.get("continuity_verified") is False


# ── boot mismatch → boot_commit_mismatch ────────────────────────────────

def test_boot_commit_mismatch():
    before = _snap()
    after = _snap(boot_commit="wrong999", current_commit=TARGET)
    result = _compare(before, after)
    assert "boot_commit_mismatch" in result["failures"]
    assert result.get("continuity_verified") is False


# ── current mismatch → current_commit_mismatch ───────────────────────────

def test_current_commit_mismatch():
    before = _snap()
    after = _snap(boot_commit=TARGET, current_commit="wrong999")
    result = _compare(before, after)
    assert "current_commit_mismatch" in result["failures"]
    assert result.get("continuity_verified") is False


# ── both present and equal → continuity_verified = True ──────────────────

def test_both_commits_equal_target_continuity_verified():
    before = _snap()
    after = _snap(boot_commit=TARGET, current_commit=TARGET)
    result = _compare(before, after)
    assert "boot_commit_mismatch" not in result["failures"]
    assert "current_commit_mismatch" not in result["failures"]
    assert "boot_commit_missing" not in result["proof_gaps"]
    assert "current_commit_missing" not in result["proof_gaps"]
    assert result.get("continuity_verified") is True
