"""P3 regression: managed observation-only evidence epoch anchoring.

Tests:
1. ensure_evidence_epochs() manages exit_trailing_evidence,
   entry_correction_v2, and profit_loop_observability.
2. Re-running the same identity is idempotent (returns the same epoch ids).
3. A changed managed identity rotates the affected epoch.
4. evidence_epoch_summary() reflects consistency between open epoch SHA and HEAD.
5. exit_trailing_evidence and entry_correction_v2 are declared in the registry
   as observation-only paths with the correct behavior_contract_version.
"""

from __future__ import annotations

import inspect

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
    import aletheia.agent_runtime.runtime_session as runtime_session

    monkeypatch.setattr(
        runtime_session,
        "current_session",
        lambda: {
            "durable": True,
            "boot_commit": "default-runtime-commit",
            "behavior_fingerprint": "default-runtime-fingerprint",
        },
    )

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


def test_durable_runtime_session_creates_profit_loop_epoch(db, monkeypatch):
    import aletheia.agent_runtime.runtime_session as runtime_session
    from aletheia.analytics.profit_loop_counterfactual import profit_loop_registry_hash
    from aletheia.learning.evidence_epoch import ensure_evidence_epochs, evidence_epoch_summary

    monkeypatch.setattr(
        runtime_session,
        "current_session",
        lambda: {
            "durable": True,
            "boot_commit": "runtime-commit",
            "behavior_fingerprint": "runtime-fingerprint",
        },
    )

    result = ensure_evidence_epochs(commit_sha="cli-legacy-commit")
    summary = evidence_epoch_summary()
    profit = next(path for path in summary["paths"] if path["path_id"] == "profit_loop_observability")

    assert set(result) == {
        "exit_trailing_evidence",
        "entry_correction_v2",
        "profit_loop_observability",
    }
    assert profit["deploy_commit"] == "runtime-commit"
    assert profit["behavior_fingerprint"] == "runtime-fingerprint"
    assert profit["registry_hash"] == profit_loop_registry_hash()
    assert profit["cutoff_at"]


def test_ensure_evidence_epochs_is_idempotent(db):
    """Same commit → same epoch ids on second call."""
    from aletheia.learning.evidence_epoch import ensure_evidence_epochs

    sha = "abc123deadbeef"
    first = ensure_evidence_epochs(commit_sha=sha)
    second = ensure_evidence_epochs(commit_sha=sha)

    assert first["exit_trailing_evidence"] == second["exit_trailing_evidence"]
    assert first["entry_correction_v2"] == second["entry_correction_v2"]


def test_profit_loop_restart_reuses_identity_and_rotates_commit_or_fingerprint(db, monkeypatch):
    import aletheia.agent_runtime.runtime_session as runtime_session
    from aletheia.learning.evidence_epoch import ensure_evidence_epochs

    identity = {
        "durable": True,
        "boot_commit": "commit-a",
        "behavior_fingerprint": "fingerprint-a",
    }
    monkeypatch.setattr(runtime_session, "current_session", lambda: dict(identity))
    first = ensure_evidence_epochs(commit_sha="legacy")
    restart = ensure_evidence_epochs(commit_sha="legacy")
    identity["behavior_fingerprint"] = "fingerprint-b"
    changed_fingerprint = ensure_evidence_epochs(commit_sha="legacy")
    identity["boot_commit"] = "commit-b"
    changed_commit = ensure_evidence_epochs(commit_sha="legacy")

    assert first["profit_loop_observability"] == restart["profit_loop_observability"]
    assert changed_fingerprint["profit_loop_observability"] != first["profit_loop_observability"]
    assert changed_commit["profit_loop_observability"] != changed_fingerprint["profit_loop_observability"]


def test_profit_loop_fails_closed_without_complete_durable_session(db, monkeypatch):
    import aletheia.agent_runtime.runtime_session as runtime_session
    from aletheia.learning.evidence_epoch import ensure_evidence_epochs

    for session in (
        None,
        {"durable": False, "boot_commit": "commit", "behavior_fingerprint": "fingerprint"},
        {"durable": True, "boot_commit": "", "behavior_fingerprint": "fingerprint"},
        {"durable": True, "boot_commit": "commit", "behavior_fingerprint": ""},
    ):
        monkeypatch.setattr(runtime_session, "current_session", lambda session=session: session)
        result = ensure_evidence_epochs(commit_sha="legacy")
        assert set(result) == {"exit_trailing_evidence", "entry_correction_v2"}


def test_ensure_evidence_epochs_acquires_one_writer_per_registration(db, monkeypatch):
    from aletheia.learning.evidence_epoch import ensure_evidence_epochs
    from aletheia.storage import repositories as repo

    real_acquire = repo.acquire_write_lock
    calls = []

    def counted_acquire(**kwargs):
        calls.append(True)
        return real_acquire(**kwargs)

    monkeypatch.setattr(repo, "acquire_write_lock", counted_acquire)

    ensure_evidence_epochs(commit_sha="one-writer-wait")

    assert calls == [True]


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
    import aletheia.agent_runtime.runtime_session as runtime_session
    from aletheia.learning.evidence_epoch import ensure_evidence_epochs, evidence_epoch_summary

    sha = "abcdef1234567890"
    monkeypatch.setattr(ev_mod, "_current_commit", lambda: sha)
    monkeypatch.setattr(
        runtime_session,
        "current_session",
        lambda: {
            "durable": True,
            "boot_commit": sha,
            "behavior_fingerprint": "default-runtime-fingerprint",
        },
    )

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
            assert path["sha_consistent"] is False, f"{path['path_id']}: expected sha_consistent=False for stale epoch"


def test_evidence_epoch_summary_uses_full_sha_equality(db, monkeypatch):
    import aletheia.learning.evidence_epoch as ev_mod
    from aletheia.learning.evidence_epoch import ensure_evidence_epochs, evidence_epoch_summary

    ensure_evidence_epochs(commit_sha="same-prefix-111111")
    monkeypatch.setattr(ev_mod, "_current_commit", lambda: "same-prefix-222222")

    summary = evidence_epoch_summary()

    legacy = next(path for path in summary["paths"] if path["path_id"] == "exit_trailing_evidence")
    assert legacy["sha_consistent"] is False


@pytest.mark.parametrize("started_at", ["", "not-a-timestamp"])
def test_profit_loop_summary_fails_closed_on_malformed_started_at(db, started_at):
    from aletheia.learning.evidence_epoch import ensure_evidence_epochs, evidence_epoch_summary
    from aletheia.storage.sqlite import get_db

    ensure_evidence_epochs(commit_sha="legacy")
    get_db().execute(
        "UPDATE learning_policy_epochs SET started_at=? "
        "WHERE epoch_kind='evidence_anchor:profit_loop_observability'",
        (started_at,),
    )
    get_db().commit()

    summary = evidence_epoch_summary()
    profit = next(path for path in summary["paths"] if path["path_id"] == "profit_loop_observability")

    assert profit["status"] == "malformed"
    assert profit["cutoff_at"] is None
    assert summary["all_anchored"] is False
    assert summary["all_sha_consistent"] is False


def test_evidence_epoch_summary_not_registered(db, monkeypatch):
    """Absent epochs cannot report SHA consistency as healthy."""
    import aletheia.learning.evidence_epoch as ev_mod
    from aletheia.learning.evidence_epoch import evidence_epoch_summary

    monkeypatch.setattr(ev_mod, "_current_commit", lambda: "deployed-sha")
    summary = evidence_epoch_summary()
    for path in summary["paths"]:
        assert path["status"] == "not_registered"
    assert summary["all_anchored"] is False
    assert summary["all_sha_consistent"] is False


def test_ensure_evidence_epochs_rejects_missing_deployment_sha(db, monkeypatch):
    import aletheia.learning.evidence_epoch as ev_mod

    monkeypatch.setattr(ev_mod, "_current_commit", lambda: "")
    with pytest.raises(ValueError, match="running deployment commit"):
        ev_mod.ensure_evidence_epochs()


def test_gateway_registers_evidence_epochs_before_scheduler_start():
    from aletheia.server import app as server_app

    source = inspect.getsource(server_app.lifespan)

    assert source.index("ensure_evidence_epochs") > source.index("start_runtime_session")
    assert source.index("ensure_evidence_epochs") < source.index("    init_tool_registry()")
    assert 'runtime_session.get("durable") is True' in source
    assert "EXPECTED_EVIDENCE_PATH_IDS - evidence_epochs.keys()" in source
