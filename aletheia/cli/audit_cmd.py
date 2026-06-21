"""`aletheia audit` — audit trail verification commands."""

import json
from typing import Optional

import typer

audit_app = typer.Typer(no_args_is_help=True)


@audit_app.command("verify")
def verify():
    """Verify the integrity of the audit.jsonl hash chain."""
    from aletheia.files.jsonl import verify_audit_chain

    result = verify_audit_chain()
    typer.echo(f"Records: {result['records']}")

    if result["valid"]:
        typer.echo("Hash chain: VALID ✓")
        raise typer.Exit(0)
    else:
        typer.echo(f"Hash chain: BROKEN ({len(result['errors'])} error(s))")
        for err in result["errors"][:20]:
            typer.echo(f"  • {err}")
        if len(result["errors"]) > 20:
            typer.echo(f"  … and {len(result['errors']) - 20} more")
        raise typer.Exit(1)


@audit_app.command("data")
def data(
    output_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Check OHLCV coverage and signal effectiveness for all active members."""
    from aletheia.storage.repositories import get_active_universe_members
    from aletheia.storage.sqlite import get_db
    from aletheia.config.loader import load_config
    from datetime import datetime, timezone, timedelta

    cfg = load_config()
    min_bars = cfg.historical_data.min_bars_for_backtest
    db = get_db()

    # Per-symbol coverage matrix
    members = get_active_universe_members()
    symbol_coverage: list[dict] = []
    for m in members:
        sym = m.get("symbol", "")
        if not sym:
            continue
        for tf in ("1h", "4h", "1d"):
            row = db.execute(
                "SELECT COUNT(*), MIN(ts), MAX(ts) FROM market_ohlcv WHERE symbol=? AND timeframe=?",
                (sym, tf),
            ).fetchone()
            count, oldest_ts, newest_ts = row if row else (0, None, None)
            symbol_coverage.append({
                "symbol": sym,
                "timeframe": tf,
                "bars": count,
                "ready": count >= min_bars,
                "oldest_ts": oldest_ts,
                "newest_ts": newest_ts,
            })

    # Signal effectiveness (last 7d, 24h horizon)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    row = db.execute(
        """SELECT COUNT(*),
                  SUM(CASE WHEN forward_return_pct > 0 THEN 1 ELSE 0 END),
                  AVG(forward_return_pct)
           FROM universe_forward_outcomes
           WHERE status = 'settled'
             AND horizon = '24h'
             AND settled_at >= ?
             AND ABS(forward_return_pct) <= 100""",
        (cutoff,),
    ).fetchone()
    total_outcomes, wins, avg_ret = row if row else (0, None, None)
    win_rate = round((wins or 0) / total_outcomes * 100, 1) if total_outcomes else None

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "min_bars_for_backtest": min_bars,
        "total_active_members": len(members),
        "symbol_coverage": symbol_coverage,
        "signal_effectiveness_7d": {
            "horizon": "24h",
            "settled_outcomes": total_outcomes,
            "win_rate_pct": win_rate,
            "avg_return_pct": round(avg_ret, 4) if avg_ret is not None else None,
        },
    }

    if output_json:
        typer.echo(json.dumps(report, indent=2))
        return

    # Human-readable output
    typer.echo(f"\nData Audit — {report['generated_at']}")
    typer.echo(f"Active members: {len(members)}  |  min_bars_for_backtest: {min_bars}")
    typer.echo("")

    # Group by symbol
    by_symbol: dict[str, list] = {}
    for entry in symbol_coverage:
        by_symbol.setdefault(entry["symbol"], []).append(entry)

    typer.echo(f"{'Symbol':<16} {'1h bars':>9} {'4h bars':>9} {'1d bars':>9} {'Ready?':>7}")
    typer.echo("-" * 56)
    for sym, entries in by_symbol.items():
        by_tf = {e["timeframe"]: e for e in entries}
        b1h = by_tf.get("1h", {}).get("bars", 0)
        b4h = by_tf.get("4h", {}).get("bars", 0)
        b1d = by_tf.get("1d", {}).get("bars", 0)
        ready = "YES" if b1h >= min_bars else "NO"
        typer.echo(f"{sym:<16} {b1h:>9} {b4h:>9} {b1d:>9} {ready:>7}")

    typer.echo("")
    typer.echo("Signal Effectiveness (last 7d, 24h horizon):")
    if not total_outcomes:
        typer.echo("  No settled outcomes yet.")
    else:
        status = "healthy" if win_rate and win_rate >= 50 else ("degraded" if win_rate and win_rate < 40 else "neutral")
        typer.echo(f"  Settled: {total_outcomes}  |  Win rate: {win_rate}%  |  Avg return: {round(avg_ret, 4) if avg_ret else 'n/a'}%  |  Status: {status}")
