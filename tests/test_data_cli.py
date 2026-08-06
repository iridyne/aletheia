"""User-facing progress contracts for explicit data maintenance commands."""

from typer.testing import CliRunner


def test_data_migrate_shows_step_progress_elapsed_and_qualified_eta(monkeypatch):
    from aletheia.cli.data_cmd import data_app
    from aletheia.storage import migrations

    statuses = iter([
        {
            "current": False,
            "pending": ["20260806_0001_legacy_schema_v2"],
            "schema_version": "aletheia-sqlite-v5",
        },
        {
            "current": True,
            "pending": [],
            "schema_version": "aletheia-sqlite-v5",
        },
    ])
    monkeypatch.setattr(migrations, "migration_status", lambda: next(statuses))

    def apply(*, progress):
        progress(migrations.MigrationProgressEvent(
            phase="started",
            migration_id="20260806_0001_legacy_schema_v2",
            description="legacy schema, indexes, and idempotent backfills",
            step_index=1,
            step_count=1,
            estimated_remaining_sec=(20.0, 50.0),
            estimate_basis="broad local calibration",
        ))
        progress(migrations.MigrationProgressEvent(
            phase="completed",
            migration_id="20260806_0001_legacy_schema_v2",
            description="legacy schema, indexes, and idempotent backfills",
            step_index=1,
            step_count=1,
            duration_sec=31.0,
            estimated_remaining_sec=(0.0, 0.0),
        ))
        return True

    monkeypatch.setattr(migrations, "apply_migrations", apply)

    result = CliRunner().invoke(data_app, ["migrate"])

    assert result.exit_code == 0, result.output
    assert "Applying pending migration" in result.output
    assert "Completed 1/1" in result.output
    assert "31s" in result.output
    assert "schema=aletheia-sqlite-v5" in result.output
