"""CLI: aletheia data — historical data and accounting management."""

import asyncio
import json
from pathlib import Path
from typing import Optional

import typer

data_app = typer.Typer(no_args_is_help=True)


@data_app.command("macro-liquidity")
def macro_liquidity(cutoff: str = typer.Option(..., help="Timezone-aware ISO decision cutoff")):
    """Export observation-only FRED facts; no network, writes or vintage claims."""
    from aletheia.data.macro_liquidity import derive_macro_liquidity, read_source_batches

    try:
        result = derive_macro_liquidity(read_source_batches(cutoff), cutoff)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(json.dumps(result, indent=2, allow_nan=False))


@data_app.command("positioning-evidence")
def positioning_evidence(
    symbol: str = typer.Option(..., help="Canonical symbol, e.g. BTC/USDT"),
    cutoff: str = typer.Option(..., help="Timezone-aware ISO decision cutoff"),
    venue: list[str] = typer.Option(["binance", "okx", "bitget", "hyperliquid"], "--venue"),
):
    """Export research-only positioning evidence from existing durable facts."""
    try:
        from extensions.data.positioning_evidence import read_positioning_evidence
    except ModuleNotFoundError as exc:
        if exc.name not in {"extensions", "extensions.data", "extensions.data.positioning_evidence"}:
            raise
        typer.echo("Positioning evidence unavailable: optional Data not installed", err=True)
        raise typer.Exit(1) from exc

    try:
        result = read_positioning_evidence(symbol, cutoff, venues=venue)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(json.dumps(result, indent=2, allow_nan=False))


@data_app.command("options-volatility")
def options_volatility(
    symbol: str = typer.Option(..., help="BTC or ETH"),
    cutoff: str = typer.Option(..., help="Timezone-aware ISO decision cutoff"),
):
    """Export a bounded observation-only options IV snapshot without fetching."""
    from aletheia.storage.repositories import options_volatility_at

    try:
        result = options_volatility_at(symbol, cutoff)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(json.dumps(result, indent=2, allow_nan=False))


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
    latest_checkpoint = next(
        (
            sample["wal_checkpoint"]
            for sample in reversed(trend_history)
            if isinstance(sample.get("wal_checkpoint"), dict)
        ),
        None,
    )
    if latest_checkpoint is not None:
        payload["latest_wal_checkpoint"] = latest_checkpoint
    if history:
        payload["history"] = trend_history[-history:]
    if json_output:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
        if not snapshot.get("available"):
            raise typer.Exit(1)
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
    typer.echo(
        f"WAL: {snapshot['wal_bytes'] / (1024 * 1024):.1f} MiB "
        f"({snapshot['wal_status']}, retained limit 64 MiB)"
    )
    if latest_checkpoint is not None:
        typer.echo(
            "Latest checkpoint: "
            f"{latest_checkpoint.get('status')} with "
            f"{int(latest_checkpoint.get('remaining_frames', 0))} frame(s) remaining"
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
    """Reclaim pages and optimize eligible physical table layouts offline."""
    import time

    from aletheia.maintenance.database_capacity import compact_database

    started = time.perf_counter()

    def progress(stage: str, message: str) -> None:
        typer.echo(f"[{time.perf_counter() - started:6.1f}s] {stage}: {message}")

    if apply:
        typer.echo(
            "Offline compaction started. Duration depends on retained data and disk speed; "
            "keep the Gateway stopped."
        )
    try:
        result = compact_database(apply=apply, progress=progress if apply else None)
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
    """Import aggregate rows or a sanitized holder batch (observation-only)."""
    try:
        from extensions.data.collectors.vergex_structure import ingest_owner_aggregate
    except ModuleNotFoundError as exc:
        if exc.name not in {"extensions", "extensions.data", "extensions.data.collectors",
                            "extensions.data.collectors.vergex_structure", "extensions.data.position_x"}:
            raise
        typer.echo("Perp structure import unavailable: optional Data not installed", err=True)
        raise typer.Exit(1) from exc
    from aletheia.storage.migrations import assert_migrations_current

    assert_migrations_current()
    payload = json.loads(path.read_text(encoding="utf-8"))
    typer.echo(json.dumps(
        ingest_owner_aggregate(payload), indent=2, ensure_ascii=False, sort_keys=True,
    ))


@data_app.command("evaluate-perp-structure")
def evaluate_perp_structure(
    policy_epoch_id: str | None = typer.Option(None, "--policy-epoch-id"),
    minimum_samples: int = typer.Option(30, "--minimum-samples", min=1),
):
    """Evaluate settled Position X shadows without changing decision authority."""
    try:
        from extensions.data.perp_structure_shadow import evaluate_position_x_shadow
    except ModuleNotFoundError as exc:
        if exc.name not in {"extensions", "extensions.data", "extensions.data.perp_structure_shadow"}:
            raise
        typer.echo("Perp structure evaluation unavailable: optional Data not installed", err=True)
        raise typer.Exit(1) from exc
    from aletheia.storage.migrations import assert_migrations_current

    assert_migrations_current()
    typer.echo(json.dumps(
        evaluate_position_x_shadow(
            policy_epoch_id=policy_epoch_id,
            minimum_samples=minimum_samples,
        ),
        indent=2,
        ensure_ascii=False,
        sort_keys=True,
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
        active_members = skipped_members = 0
    else:
        members = get_active_universe_members(limit=None)
        active_members = len(members)
        skipped_members = 0
        instruments = []
        for member in members:
            asset = get_universe_asset(member.get("asset_id")) or {}
            venue = asset.get("venue")
            mkt = member.get("market_type") or asset.get("market_type")
            if venue and member.get("symbol") and mkt:
                instruments.append((venue, member["symbol"], mkt))
            else:
                skipped_members += 1

    if not instruments:
        if not symbols:
            typer.echo(
                f"Active members: {active_members}; eligible instruments: 0; "
                f"skipped: {skipped_members}."
            )
        typer.echo("No symbols to backfill (empty universe).")
        raise typer.Exit(1)

    tf_list = [t.strip() for t in timeframes.split(",")]
    typer.echo(f"Backfilling {len(instruments)} instruments × {len(tf_list)} timeframes ({months}mo)...")
    if not symbols:
        typer.echo(
            f"Active members: {active_members}; eligible instruments: {len(instruments)}; "
            f"skipped: {skipped_members}."
        )

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
