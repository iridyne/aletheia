"""CLI: aletheia data — historical data and accounting management."""

import asyncio
import json
from pathlib import Path
from typing import Optional

import typer

data_app = typer.Typer(no_args_is_help=True)


@data_app.command("migrate")
def migrate_database():
    """Explicitly apply pending versioned database migrations."""
    import time

    from aletheia.storage.migrations import apply_migrations, migration_status

    before = migration_status()
    if before["current"]:
        typer.echo(
            f"Database schema is current ({before['schema_version']}); no migrations applied."
        )
        return
    typer.echo("Applying pending migration(s): " + ", ".join(before["pending"]))
    started = time.perf_counter()
    from aletheia.cli.migration_progress import MigrationProgressDisplay

    with MigrationProgressDisplay() as progress:
        apply_migrations(progress=progress)
    after = migration_status()
    typer.echo(
        f"Database migrations completed in {time.perf_counter() - started:.1f}s; "
        f"schema={after['schema_version']}."
    )


@data_app.command("capacity")
def database_capacity(
    deep: bool = typer.Option(
        False,
        "--deep",
        help="Attribute current bytes to the largest SQLite objects (slower).",
    ),
    history: int = typer.Option(
        0,
        "--history",
        min=0,
        max=3650,
        help="Include this many exact nightly capacity samples.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
):
    """Inspect app.db capacity without migrating or mutating it."""
    from aletheia.config.loader import load_config
    from aletheia.maintenance.database_capacity import (
        database_capacity_history,
        database_capacity_snapshot,
        database_capacity_trend,
    )

    budget = load_config(persist_migrations=False).retention.app_db_capacity_mb
    snapshot = database_capacity_snapshot(capacity_mb=budget, deep=deep)
    trend_history = database_capacity_history(limit=max(90, history))
    trend = database_capacity_trend(
        [*trend_history, snapshot],
        capacity_bytes=snapshot.get("capacity_bytes"),
    )
    payload = {"current": snapshot, "trend": trend}
    if history:
        payload["history"] = trend_history[-history:]
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
        return
    if not snapshot.get("available"):
        typer.echo(f"Database not available: {snapshot.get('path')}")
        raise typer.Exit(1)
    typer.echo(
        "app.db: "
        f"{snapshot['physical_bytes'] / (1024 * 1024):.1f} MiB physical, "
        f"{snapshot['logical_used_bytes'] / (1024 * 1024):.1f} MiB used, "
        f"{snapshot['reclaimable_bytes'] / (1024 * 1024):.1f} MiB reusable"
    )
    typer.echo(
        f"Capacity: {snapshot.get('capacity_status')} "
        f"({float(snapshot.get('capacity_ratio', 0)):.1%} of {budget} MiB); "
        f"incremental_vacuum={'enabled' if snapshot['incremental_vacuum_enabled'] else 'disabled'}"
    )
    for row in snapshot.get("top_objects", []):
        packing = row.get("packing_ratio")
        packing_text = f", {float(packing):.1%} packed" if packing is not None else ""
        typer.echo(
            f"  {row['name']}: {row['bytes'] / (1024 * 1024):.1f} MiB{packing_text}"
        )
    if trend["status"] == "insufficient_history":
        typer.echo(
            "Trend: insufficient exact history; need at least 3 samples across 7 days."
        )
    else:
        typer.echo(
            f"Trend ({trend['status']}): {trend['slope_mib_per_day']:+.2f} MiB/day "
            f"across {trend['span_days']:.1f} days"
        )
        if trend.get("capacity_eta_days") is not None:
            typer.echo(
                f"Capacity ETA: {trend['capacity_eta_days']:.1f} days "
                f"({trend['capacity_eta_at']})"
            )
    if history:
        typer.echo(f"History samples: {len(payload.get('history', []))}")


@data_app.command("compact")
def compact_database_command(
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Run offline compaction. Default is a read-only plan.",
    ),
):
    """Reclaim reusable pages and enable future incremental vacuuming."""
    from aletheia.maintenance.database_capacity import compact_database

    try:
        result = compact_database(apply=apply)
    except RuntimeError as exc:
        typer.echo(f"Compaction refused: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    if not apply:
        typer.echo(
            "Dry-run only. Stop the Gateway and repeat with "
            "`uv run aletheia data compact --apply`."
        )


@data_app.command("ingest-perp-structure")
def ingest_perp_structure(
    path: Path = typer.Argument(..., exists=True, readable=True, dir_okay=False),
):
    """Import an owner-authorized aggregate Position X export (observation-only)."""
    from aletheia.data.collectors.vergex_structure import ingest_owner_aggregate
    from aletheia.storage.migrations import assert_migrations_current

    assert_migrations_current()
    payload = json.loads(path.read_text(encoding="utf-8"))
    typer.echo(json.dumps(
        ingest_owner_aggregate(payload), indent=2, ensure_ascii=False, sort_keys=True,
    ))


@data_app.command("reconcile-local-paper-accounting")
def reconcile_local_paper_accounting(
    apply: bool = typer.Option(
        False, "--apply",
        help="Apply only exact backlinks and additive reconciliation rows. Default is dry-run.",
    ),
):
    """Recover provable historical local-paper accounting without changing raw PnL.

    This command never writes an exchange, Risk decision, Position economics, or
    paper cash event.  It reports unavailable evidence instead of estimating it.
    """
    from aletheia.storage.migrations import assert_migrations_current
    from aletheia.maintenance.historical_accounting import reconcile_historical_local_paper_accounting

    assert_migrations_current()
    typer.echo(json.dumps(
        reconcile_historical_local_paper_accounting(dry_run=not apply),
        indent=2, ensure_ascii=False, sort_keys=True,
    ))


@data_app.command("requeue-unobserved-settlements")
def requeue_unobserved_settlements(
    apply: bool = typer.Option(
        False, "--apply",
        help="Return never-observed 'failed' rows to 'pending'. Default is dry-run.",
    ),
):
    """Reclaim settlement rows written off before any observation was taken.

    Targets exactly the rows whose status is 'failed' while both exit_price_usd and
    settlement_price_source are NULL — the signature of a terminal write that
    happened because the venue could not be reached, not because the row was
    unmeasurable. Resets status and settled_at only; never touches economics,
    deletes history, or reopens a row that carries a real verdict.
    """
    from aletheia.maintenance.settlement_requeue import (
        requeue_unobserved_failed_outcomes,
    )
    from aletheia.storage.migrations import assert_migrations_current

    assert_migrations_current()
    typer.echo(json.dumps(
        requeue_unobserved_failed_outcomes(dry_run=not apply),
        indent=2, ensure_ascii=False, sort_keys=True,
    ))


@data_app.command("archive-unmeasurable-learning-experiments")
def archive_unmeasurable_learning_experiments(
    apply: bool = typer.Option(
        False, "--apply",
        help="Archive open experiments without a measurement adapter. Default is dry-run.",
    ),
):
    """Soft-terminalize historical experiments that cannot produce verdicts."""
    from aletheia.learning.maintenance import archive_unmeasurable_experiments
    from aletheia.storage.migrations import assert_migrations_current

    assert_migrations_current()
    typer.echo(json.dumps(
        archive_unmeasurable_experiments(dry_run=not apply),
        indent=2, ensure_ascii=False, sort_keys=True,
    ))


@data_app.command("reconcile-paper-funding")
def reconcile_paper_funding(
    position_ids: list[str] = typer.Option([], "--position-id"),
    apply: bool = typer.Option(False, "--apply"),
    expected_fingerprint: str | None = typer.Option(
        None, "--expected-fingerprint",
    ),
):
    """Inspect or apply fingerprint-guarded paper funding reconciliation."""
    if apply:
        if not expected_fingerprint:
            raise typer.BadParameter("--apply requires --expected-fingerprint")
        if not position_ids:
            raise typer.BadParameter("--apply requires at least one --position-id")
    elif expected_fingerprint:
        raise typer.BadParameter("--expected-fingerprint is valid only with --apply")

    from aletheia.maintenance.paper_funding_reconciliation import (
        apply_paper_funding_reconciliation,
        inspect_paper_funding_reconciliation,
    )

    try:
        if apply:
            from aletheia.storage.migrations import assert_migrations_current

            assert_migrations_current()
            result = apply_paper_funding_reconciliation(
                position_ids=position_ids,
                expected_fingerprint=expected_fingerprint,
            )
        else:
            # The default inspection path is contractually read-only. Schema
            # upgrades belong to explicit apply/startup paths, never a dry run.
            result = inspect_paper_funding_reconciliation(position_ids=position_ids)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))


@data_app.command("download")
def download(
    symbol: str = typer.Argument(..., help="Canonical symbol, e.g. BTC/USDT"),
    timeframe: str = typer.Option("1h", help="Candle timeframe (1m/5m/15m/1h/4h/1d)"),
    months: int = typer.Option(6, help="How many months of history to fetch"),
    exchange: str = typer.Option("", help="Source exchange (default: configured primary)"),
    market_type: str = typer.Option("spot", help="Instrument market type: spot | perp"),
):
    """Bulk-download historical OHLCV candles from the configured exchange."""
    from aletheia.data.history_downloader import download_symbol_history

    typer.echo(f"Downloading {symbol} {timeframe} — {months} months...")

    def _progress(n: int):
        typer.echo(f"  {n} bars fetched...", nl=False)
        typer.echo("\r", nl=False)

    result = asyncio.run(download_symbol_history(
        symbol, timeframe, months=months, exchange=exchange or None,
        market_type=market_type, progress_callback=_progress,
    ))

    if result.error:
        typer.echo(f"\nCompleted with error: {result.error}")
    typer.echo(f"\nDone: {result.bars_fetched} bars [{result.oldest_ts} → {result.newest_ts}]")


@data_app.command("backfill")
def backfill(
    months: int = typer.Option(1, help="Months of history to backfill"),
    symbols: Optional[str] = typer.Option(None, help="Comma-separated symbols (default: all active members)"),
    timeframes: str = typer.Option("1h,4h", help="Comma-separated timeframes"),
    exchange: str = typer.Option("", help="Venue for explicit --symbols"),
    market_type: str = typer.Option("spot", help="Market type for explicit --symbols"),
):
    """Batch-backfill OHLCV history for all active universe members."""
    from aletheia.data.history_downloader import download_symbol_history
    from aletheia.config.loader import load_config
    from aletheia.storage.repositories import get_active_universe_members, get_universe_asset

    if symbols:
        venue = exchange or load_config().market_data.ccxt_exchange
        instruments = [(venue, s.strip(), market_type) for s in symbols.split(",") if s.strip()]
    else:
        members = get_active_universe_members()
        instruments = []
        for member in members:
            asset = get_universe_asset(member.get("asset_id")) or {}
            venue = asset.get("venue")
            mkt = member.get("market_type") or asset.get("market_type")
            if venue and member.get("symbol") and mkt:
                instruments.append((venue, member["symbol"], mkt))

    if not instruments:
        typer.echo("No symbols to backfill (empty universe).")
        raise typer.Exit(1)

    tf_list = [t.strip() for t in timeframes.split(",")]
    typer.echo(f"Backfilling {len(instruments)} instruments × {len(tf_list)} timeframes ({months}mo)...")

    total_bars = 0
    for venue, sym, mkt in instruments:
        for tf in tf_list:
            result = asyncio.run(download_symbol_history(
                sym, tf, months=months, exchange=venue, market_type=mkt,
            ))
            total_bars += result.bars_fetched
            status = f"  {venue}:{sym}:{mkt}/{tf}: +{result.bars_fetched} bars"
            if result.error:
                status += f" (error: {result.error})"
            typer.echo(status)

    typer.echo(f"\nTotal: +{total_bars} bars")


@data_app.command("coverage")
def coverage(
    symbol: str = typer.Argument(..., help="Canonical symbol, e.g. BTC/USDT"),
    timeframe: str = typer.Option("1h", help="Candle timeframe"),
    exchange: str = typer.Option("", help="Filter by source exchange"),
    market_type: str = typer.Option("", help="Filter by market type: spot | perp"),
):
    """Show local OHLCV data coverage for a symbol."""
    from aletheia.storage.repositories import get_ohlcv_coverage

    cov = get_ohlcv_coverage(
        symbol, timeframe, exchange=exchange, market_type=market_type,
    )
    identity = f"{exchange or '*'}:{symbol}:{market_type or '*'}"
    typer.echo(f"{identity} {timeframe}: {cov['bar_count']} bars")
    if cov["oldest_ts"]:
        from datetime import datetime, timezone
        oldest = datetime.fromtimestamp(cov["oldest_ts"] / 1000, tz=timezone.utc)
        newest = datetime.fromtimestamp(cov["newest_ts"] / 1000, tz=timezone.utc)
        typer.echo(f"  Range: {oldest.strftime('%Y-%m-%d %H:%M')} → {newest.strftime('%Y-%m-%d %H:%M')} UTC")
