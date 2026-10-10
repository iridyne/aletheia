"""Exchange configuration CLI: `aletheia exchange ...`

Switch primary exchange, toggle demo/live mode, and show current state.
"""

import typer

from aletheia.config.exchange_alignment import market_data_exchange
from aletheia.config.exchanges import EXCHANGE_UNIFICATION_MAP, VALID_EXCHANGES

exchange_app = typer.Typer(no_args_is_help=True)


@exchange_app.command("use")
def use(
    exchange_id: str = typer.Argument(
        ..., help=f"Exchange to activate: {', '.join(VALID_EXCHANGES)}"
    ),
):
    """Switch primary exchange (atomically sets data source, execution, and risk)."""
    from aletheia.config.loader import edit_config

    eid = exchange_id.strip().lower()
    if eid not in VALID_EXCHANGES:
        typer.echo(f"Error: exchange_id must be one of: {VALID_EXCHANGES}", err=True)
        raise typer.Exit(1)

    with edit_config() as cfg:
        old_name = cfg.exchange.name
        old_mode = cfg.exchange.paper_execution_mode
        desired_mode = EXCHANGE_UNIFICATION_MAP[eid][1]
        if cfg.live.state == "live_limited" and (
            eid != str(old_name or "").lower() or desired_mode != old_mode
        ):
            typer.echo(
                "Error: lock live trading and re-run the selected-account preflight before switching exchange.",
                err=True,
            )
            raise typer.Exit(1)

        from aletheia.config.exchange_alignment import apply_primary_exchange
        apply_primary_exchange(cfg, eid)


    from aletheia.files.jsonl import write_audit
    write_audit({
        "actor": "human", "source": "cli",
        "event_type": "primary_exchange_changed",
        "entity_type": "exchange_config",
        "before": {"exchange.name": old_name, "paper_execution_mode": old_mode},
        "after": {"exchange.name": cfg.exchange.name, "paper_execution_mode": cfg.exchange.paper_execution_mode},
        "message": f"Primary exchange set to {eid} via CLI",
    })

    typer.echo(f"Switched to: {eid}")
    typer.echo(f"  exchange.name:             {cfg.exchange.name}")
    typer.echo(f"  paper_execution_mode:      {cfg.exchange.paper_execution_mode}")
    typer.echo(f"  market data exchange:      {market_data_exchange(cfg)} (follows the account)")
    typer.echo(f"  risk.allowed_exchange:      {cfg.risk.allowed_exchange}")
    typer.echo("")
    typer.echo("Note: restart gateway for changes to take full effect.")


@exchange_app.command("mode")
def mode(
    value: str = typer.Argument(..., help="Trading mode: demo | live"),
):
    """Toggle exchange mode between demo and live."""
    from aletheia.config.loader import edit_config

    val = value.strip().lower()
    if val not in ("demo", "live"):
        typer.echo("Error: mode must be 'demo' or 'live'", err=True)
        raise typer.Exit(1)

    if val == "live":
        typer.echo("WARNING: switching to LIVE mode enables real-money trading.", err=True)
        typer.confirm("Are you sure you want to enable live mode?", abort=True)

    with edit_config() as cfg:
        old = cfg.exchange.mode
        if cfg.live.state == "live_limited" and val != old:
            typer.echo(
                "Error: lock live trading and re-run the selected-account preflight before changing mode.",
                err=True,
            )
            raise typer.Exit(1)
        cfg.exchange.mode = val

    from aletheia.files.jsonl import write_audit
    write_audit({
        "actor": "human", "source": "cli",
        "event_type": "exchange_mode_changed",
        "entity_type": "exchange_config",
        "before": {"mode": old},
        "after": {"mode": val},
        "message": f"Exchange mode set to {val} via CLI",
    })

    typer.echo(f"Exchange mode: {old} → {val}")


@exchange_app.command("show")
def show():
    """Show current exchange configuration."""
    from aletheia.config.loader import load_config

    cfg = load_config()
    typer.echo("=== Exchange Config ===")
    typer.echo(f"  name:                 {cfg.exchange.name}")
    typer.echo(f"  mode:                 {cfg.exchange.mode}")
    typer.echo(f"  paper_execution_mode: {cfg.exchange.paper_execution_mode}")
    typer.echo(f"  market data exchange: {market_data_exchange(cfg)}"
               f"{'' if cfg.market_data.ccxt_exchange else ' (follows the account)'}")
    typer.echo(f"  allowed_exchange:     {cfg.risk.allowed_exchange}")
    typer.echo(f"  enabled_markets:      {cfg.exchange.enabled_markets}")


def _configured_account() -> str:
    """The execution mode the configured exchange.mode actually trades through."""
    from aletheia.config.exchanges import live_execution_mode_for_exchange
    from aletheia.config.loader import load_config

    cfg = load_config()
    if cfg.exchange.mode == "live":
        mode = live_execution_mode_for_exchange(cfg.exchange.name) or ""
    else:
        mode = str(cfg.exchange.paper_execution_mode or "")
    if not mode:
        typer.echo("Error: no trading account configured (exchange.paper_execution_mode).", err=True)
        raise typer.Exit(1)
    return mode


@exchange_app.command("test")
def test():
    """Test the configured trading account's connection."""
    from aletheia.uta.manager import probe_account_reachable

    mode = _configured_account()
    result = probe_account_reachable(mode)
    if result["connected"]:
        typer.echo(f"✓ {mode} connection successful")
    else:
        typer.echo(f"✗ {mode} connection failed: {result['error']}", err=True)
        raise typer.Exit(1)


@exchange_app.command("sync")
def sync():
    """Refresh the configured trading account through UTA (debug tool)."""
    from aletheia.portfolio.exchange_sync import status_for, sync_all_exchanges
    from aletheia.storage.migrations import assert_migrations_current

    assert_migrations_current()
    mode = _configured_account()
    state = sync_all_exchanges(execution_modes=(mode,))
    if state is None or status_for(state) != "ok":
        typer.echo(f"UTA account unavailable: {state}", err=True)
        raise typer.Exit(1)
    account = next(row for row in state["accounts"] if row["broker_id"] == mode)
    typer.echo(f"mode: {mode}")
    typer.echo(f"equity_usdt: {account.get('equity_usdt')}")
    typer.echo(f"available_usdt: {account.get('available_usdt')}")
    typer.echo(f"fetched_at: {state.get('fetched_at')}")

