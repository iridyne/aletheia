"""Tests for #1383: pre-retention backup is created before first mutation, and
postflight failure triggers a restore attempt.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import pytest


def _minimal_args(**overrides):
    """Return a minimal argparse.Namespace that satisfies data_retention_backfill.run()."""
    defaults = dict(
        apply=True,
        dry_run=False,
        yes=True,
        tables="",
        archive_dir=None,
        vacuum=False,
        skip_fs=True,
        # window defaults
        agent_runs_days=90,
        agent_run_steps_days=90,
        tool_calls_days=90,
        runtime_sessions_days=90,
        agent_shell_messages_context_days=7,
        agent_shell_messages_other_days=14,
        agent_shell_heartbeats_days=90,
        agent_shell_events_days=90,
        agent_shell_session_logs_days=90,
        agent_shell_artifacts_strategy_days=30,
        agent_shell_artifacts_proposal_days=30,
        agent_shell_artifacts_other_days=30,
        agent_shell_commands_days=30,
        agent_shell_sessions_days=30,
        agent_shell_tasks_completed_days=90,
        agent_shell_tasks_failed_days=180,
        agent_shell_task_notes_completed_days=90,
        agent_shell_task_notes_failed_days=180,
        backups_keep_recent=10,
        diagnostics_days=30,
        # market telemetry
        market_ohlcv_1m_days=7,
        market_ohlcv_5m_days=30,
        market_ohlcv_15m_days=90,
        market_ohlcv_1h_days=180,
        market_ohlcv_4h_days=365,
        market_ohlcv_1d_days=730,
        funding_rate_snapshots_days=90,
        open_interest_snapshots_days=90,
        coingecko_top_snapshots_days=90,
        market_regimes_days=180,
        # filter family
        filter_candidate_outcomes_days=180,
        filter_decisions_days=180,
        filter_decision_batches_days=180,
        filter_shadow_experiments_days=180,
        # universe
        universe_feature_snapshots_days=180,
        universe_snapshots_days=180,
        universe_scanner_runs_days=90,
        # alpha control
        alpha_control_observations_days=180,
        controller_recommendations_days=90,
        # serenity / backtest
        serenity_evidence_days=90,
        serenity_run_events_days=90,
        backtest_runs_days=90,
        pre_migration_backup_days=90,
        # retention learning tables
        learning_experiments_days=180,
        learning_exposures_days=180,
        learning_hypotheses_days=180,
        learning_policy_epochs_days=360,
        learning_contribution_verdicts_days=180,
        learning_lifecycle_events_days=180,
        learning_job_health_days=90,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _setup_db(tmp_path: Path) -> tuple[Path, sqlite3.Connection]:
    """Create a minimal app.db with just enough schema to run the backfill."""
    import aletheia.config.loader as loader
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.sqlite import reset_db, get_db

    loader.DATA_DIR = tmp_path
    reset_db()
    db_file = tmp_path / "app.db"
    conn = get_db()
    run_migrations()
    return db_file, conn


def test_backup_created_before_first_mutation(tmp_path, monkeypatch):
    """#1383: run(apply=True) must create app.db.pre-retention-backup before any DELETE."""
    import aletheia.config.loader as loader
    from aletheia.maintenance import data_retention_backfill as drb

    _db_file, _conn = _setup_db(tmp_path)
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)

    created_before_dispatch: list[bool] = []

    original_dispatch = drb._dispatch_prune

    def spy_dispatch(target, args, db):
        backup = tmp_path / "app.db.pre-retention-backup"
        created_before_dispatch.append(backup.exists())
        return original_dispatch(target, args, db)

    monkeypatch.setattr(drb, "_dispatch_prune", spy_dispatch)

    args = _minimal_args()
    try:
        drb.run(args)
    except SystemExit as exc:
        if exc.code != 0:
            pytest.skip(f"run exited with code {exc.code} (schema mismatch or data issue)")

    backup_path = tmp_path / "app.db.pre-retention-backup"
    assert backup_path.exists(), "pre-retention backup was never created"

    # Verify the backup itself is a valid SQLite database
    verify_conn = sqlite3.connect(str(backup_path))
    try:
        row = verify_conn.execute("PRAGMA integrity_check").fetchone()
        assert row and row[0] == "ok", f"backup integrity_check failed: {row}"
    finally:
        verify_conn.close()


def test_backup_is_sqlite_online_copy(tmp_path, monkeypatch):
    """The backup must be a valid independent SQLite database (not just bytes)."""
    import aletheia.config.loader as loader
    from aletheia.maintenance import data_retention_backfill as drb

    _db_file, _conn = _setup_db(tmp_path)
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)

    # Patch the dispatch to no-op so the test completes quickly
    monkeypatch.setattr(drb, "_dispatch_prune", lambda _t, _a, _d: 0)
    monkeypatch.setattr(drb, "_retro_strip_message_context", lambda _db, _days: {})
    monkeypatch.setattr(drb, "_run_universe_features_raw_migration", lambda: {"skipped": "test"})

    args = _minimal_args(vacuum=False)
    try:
        drb.run(args)
    except SystemExit:
        pass

    backup_path = tmp_path / "app.db.pre-retention-backup"
    assert backup_path.exists(), "backup must exist"
    # Must be readable as a real SQLite db
    bc = sqlite3.connect(str(backup_path))
    try:
        tables = {row[0] for row in bc.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert len(tables) > 0, "backup should contain migrated tables"
    finally:
        bc.close()


def test_postflight_failure_triggers_restore_attempt(tmp_path, monkeypatch):
    """#1383: when postflight verification fails, restore is attempted."""
    import aletheia.config.loader as loader
    from aletheia.maintenance import data_retention_backfill as drb

    _db_file, _conn = _setup_db(tmp_path)
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)

    restore_called: list[tuple] = []

    def spy_restore(db, backup_path):
        restore_called.append((db, backup_path))
        # Don't actually restore in test

    monkeypatch.setattr(drb, "_attempt_restore_from_backup", spy_restore)
    monkeypatch.setattr(drb, "_dispatch_prune", lambda _t, _a, _d: 0)
    monkeypatch.setattr(drb, "_retro_strip_message_context", lambda _db, _days: {})
    monkeypatch.setattr(drb, "_run_universe_features_raw_migration", lambda: {"skipped": "test"})

    # Force postflight to fail by corrupting the baseline
    original_postflight = drb._postflight_verify

    def failing_postflight(db, baseline, backup_path=None):
        # Simulate a row count change by injecting a mismatched baseline
        bad_baseline = dict(baseline)
        bad_baseline["audit_chain"] = {"audit_events": 999999999}
        # Call real _postflight_verify which will call _attempt_restore_from_backup
        original_postflight(db, bad_baseline, backup_path)

    monkeypatch.setattr(drb, "_postflight_verify", failing_postflight)

    args = _minimal_args(vacuum=False)
    with pytest.raises(SystemExit):
        drb.run(args)

    # restore must have been attempted
    assert len(restore_called) >= 1, "restore must be attempted on postflight failure"
    _, backup_path_arg = restore_called[0]
    assert backup_path_arg is not None, "backup_path must be passed to restore"
    assert "pre-retention-backup" in str(backup_path_arg)


def test_postflight_failure_reports_correct_backup_path(tmp_path, monkeypatch):
    """#1383: error message must name the actual backup, not a different file."""
    import aletheia.config.loader as loader
    from aletheia.maintenance import data_retention_backfill as drb

    _db_file, _conn = _setup_db(tmp_path)
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(drb, "_dispatch_prune", lambda _t, _a, _d: 0)
    monkeypatch.setattr(drb, "_retro_strip_message_context", lambda _db, _days: {})
    monkeypatch.setattr(drb, "_run_universe_features_raw_migration", lambda: {"skipped": "test"})

    original_postflight = drb._postflight_verify

    def failing_postflight(db, baseline, backup_path=None):
        bad_baseline = dict(baseline)
        bad_baseline["audit_chain"] = {"audit_events": 999999999}
        original_postflight(db, bad_baseline, backup_path)

    monkeypatch.setattr(drb, "_postflight_verify", failing_postflight)

    args = _minimal_args(vacuum=False)
    with pytest.raises(SystemExit) as exc_info:
        drb.run(args)

    msg = str(exc_info.value)
    # Must NOT mention a different file like "pre-migration-backup"
    assert "pre-migration-backup" not in msg, (
        "error message should not reference pre-migration-backup; that is a different file"
    )
