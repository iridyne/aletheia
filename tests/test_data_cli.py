"""User-facing progress contracts for explicit data maintenance commands."""

from typer.testing import CliRunner


def test_data_migrate_shows_step_progress_elapsed_and_qualified_eta(monkeypatch):
    from aletheia.cli.data_cmd import data_app
    from aletheia.storage import migrations

    statuses = iter([
        {
            "current": False,
            "pending": ["20260806_0001_legacy_schema_v2"],
            "schema_version": "aletheia-sqlite-v13",
        },
        {
            "current": True,
            "pending": [],
            "schema_version": "aletheia-sqlite-v13",
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
    assert "schema=aletheia-sqlite-v13" in result.output


def test_data_backfill_without_symbols_reads_all_active_members(monkeypatch):
    from aletheia.cli import data_cmd
    from aletheia.data import history_downloader
    from aletheia.data.history_downloader import DownloadResult

    members = [
        {"asset_id": f"asset-{index}", "symbol": f"ASSET{index}/USDT", "market_type": "spot"}
        for index in range(21)
    ]
    seen_limits = []
    seen_downloads = []

    async def download(symbol, timeframe, **kwargs):
        seen_downloads.append((kwargs["exchange"], symbol, kwargs["market_type"], timeframe))
        return DownloadResult(symbol, timeframe, 1, 1, 1, None)

    monkeypatch.setattr(history_downloader, "download_symbol_history", download)
    monkeypatch.setattr(
        "aletheia.storage.repositories.get_active_universe_members",
        lambda limit=20: (seen_limits.append(limit), members)[1],
    )
    monkeypatch.setattr(
        "aletheia.storage.repositories.get_universe_asset",
        lambda asset_id: {"venue": "bitget", "market_type": "spot"},
    )

    result = CliRunner().invoke(data_cmd.data_app, ["backfill", "--timeframes", "1h"])

    assert result.exit_code == 0, result.output
    assert seen_limits == [None]
    assert len(seen_downloads) == 21
    assert {symbol for _, symbol, _, _ in seen_downloads} == {
        f"ASSET{index}/USDT" for index in range(21)
    }
    assert "Backfilling 21 instruments" in result.output
    assert "Active members: 21; eligible instruments: 21; skipped: 0." in result.output
