"""P3 regression: Evidence epoch anchoring for Entry v2 + trailing 90/10.

Tests:
1. ensure_evidence_epochs() creates open epochs for both evidence paths.
2. Re-running on the same commit is idempotent (returns same epoch ids).
3. A new commit SHA rotates the epoch.
4. evidence_epoch_summary() reflects consistency between open epoch SHA and HEAD.
5. exit_trailing_evidence and entry_correction_v2 are declared in the registry
   with measurement_supported=True and the correct behavior_contract_version.
"""
from __future__ import annotations

import pytest


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Isolated in-memory SQLite with migrations applied."""
    import aletheia.storage.sqlite as sqlite_mod

    # Use a temp file DB so migrations work correctly
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("ALETHEIA_DATA_DIR", str(tmp_path))

    from aletheia.storage.migrations import apply_migrations
    apply_migrations()

    yield

    # Cleanup thread-local connection
    try:
        sqlite_mod._local.conn.close()
    except AttributeError:
        pass


# ---------------------------------------------------------------------------
# Registry: new paths declared correctly
# ---------------------------------------------------------------------------

def test_exit_trailing_evidence_path_declared():
    from aletheia.learning.registry import get_learning_path

    path = get_learning_path("exit_trailing_evidence")
    # Evidence epoch paths are observation-only anchors, NOT closed influence loops.
    # measurement_supported=False keeps them out of the Learning influence machinery
    # (spec LC-S01 reserves that flag for universe_family_calibration only).
    assert path.measurement_supported is False
    assert path.is_learning_treatment is False
    assert path.consumer_wired is False
    assert path.settings_supported is False
    assert path.behavior_contract_version == "exit-trailing-evidence/v1"
    assert "observe" in path.allowed_effects


def test_entry_correction_v2_path_declared():
    from aletheia.learning.registry import get_learning_path

    path = get_learning_path("entry_correction_v2")
    assert path.measurement_supported is False
    assert path.is_learning_treatment is False
    assert path.consumer_wired is False
    assert path.settings_supported is False
    assert path.behavior_contract_version == "entry-correction-v2/v1"
    assert "observe" in path.allowed_effects


# ---------------------------------------------------------------------------
# ensure_evidence_epochs: creates epochs for both paths
# ---------------------------------------------------------------------------

def test_ensure_evidence_epochs_creates_both_paths(db):
    from aletheia.learning.evidence_epoch import ensure_evidence_epochs

    result = ensure_evidence_epochs(commit_sha="abc123deadbeef")

    assert "exit_trailing_evidence" in result
    assert "entry_correction_v2" in result
    assert result["exit_trailing_evidence"]
    assert result["entry_correction_v2"]


def test_ensure_evidence_epochs_is_idempotent(db):
    """Same commit → same epoch ids on second call."""
    from aletheia.learning.evidence_epoch import ensure_evidence_epochs

    sha = "abc123deadbeef"
    first = ensure_evidence_epochs(commit_sha=sha)
    second = ensure_evidence_epochs(commit_sha=sha)

    assert first["exit_trailing_evidence"] == second["exit_trailing_evidence"]
    assert first["entry_correction_v2"] == second["entry_correction_v2"]


def test_ensure_evidence_epochs_rotates_on_new_commit(db):
    """Different commit SHA → new epoch ids (existing epoch closed)."""
    from aletheia.learning.evidence_epoch import ensure_evidence_epochs

    first = ensure_evidence_epochs(commit_sha="sha_old_deploy")
    second = ensure_evidence_epochs(commit_sha="sha_new_deploy")

    # New build should rotate the epoch
    assert first["exit_trailing_evidence"] != second["exit_trailing_evidence"]
    assert first["entry_correction_v2"] != second["entry_correction_v2"]


# ---------------------------------------------------------------------------
# evidence_epoch_summary: SHA consistency
# ---------------------------------------------------------------------------

def test_evidence_epoch_summary_sha_consistent(db, monkeypatch):
    """Summary shows sha_consistent=True when epoch commit matches HEAD."""
    import aletheia.learning.evidence_epoch as ev_mod
    from aletheia.learning.evidence_epoch import ensure_evidence_epochs, evidence_epoch_summary

    sha = "abcdef1234567890"
    monkeypatch.setattr(ev_mod, "_current_commit", lambda: sha)

    ensure_evidence_epochs(commit_sha=sha)
    summary = evidence_epoch_summary()

    assert summary["all_anchored"] is True
    for path in summary["paths"]:
        assert path["status"] == "open"
        assert path["sha_consistent"] is True, (
            f"{path['path_id']}: sha_consistent should be True, got {path['sha_consistent']}"
        )


def test_evidence_epoch_summary_sha_inconsistent_on_stale_epoch(db, monkeypatch):
    """Summary shows sha_consistent=False when HEAD has advanced past the epoch."""
    import aletheia.learning.evidence_epoch as ev_mod
    from aletheia.learning.evidence_epoch import ensure_evidence_epochs, evidence_epoch_summary

    old_sha = "old_sha_from_last_deploy"
    new_sha = "new_sha_after_update"

    # Register with old SHA
    ensure_evidence_epochs(commit_sha=old_sha)

    # Simulate HEAD advancing (new deployment not yet registered)
    monkeypatch.setattr(ev_mod, "_current_commit", lambda: new_sha)

    summary = evidence_epoch_summary()
    # Epochs are still open (not rotated) but SHA doesn't match HEAD
    for path in summary["paths"]:
        if path["status"] == "open":
            assert path["sha_consistent"] is False, (
                f"{path['path_id']}: expected sha_consistent=False for stale epoch"
            )


def test_evidence_epoch_summary_not_registered(db):
    """Summary shows not_registered when no epoch exists."""
    from aletheia.learning.evidence_epoch import evidence_epoch_summary

    summary = evidence_epoch_summary()
    for path in summary["paths"]:
        assert path["status"] == "not_registered"
    assert summary["all_anchored"] is False
