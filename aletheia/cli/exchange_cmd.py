"""Exchange configuration CLI: `aletheia exchange ...`

Switch primary exchange, toggle demo/live mode, and show current state.
"""

import typer

exchange_app = typer.Typer(no_args_is_help=True)

_EXCHANGE_UNIFICATION_MAP: dict[str, tuple[str, str]] = {
    "okx": ("okx", "okx_demo"),
    "bitget": ("bitget", "bitget_demo"),
    "binance": ("binance", "binance_testnet"),
}

_VALID_EXCHANGES = sorted(_EXCHANGE_UNIFICATION_MAP.keys()) + ["paper_internal"]


@exchange_app.command("use")
def use(
    exchange_id: str = typer.Argument(
        ..., help=f"Exchange to activate: {', '.join(_VALID_EXCHANGES)}"
    ),
):
    """Switch primary exchange (atomically sets data source, execution, and risk)."""
    from aletheia.config.loader import load_config, save_config

    eid = exchange_id.strip().lower()
    if eid not in _VALID_EXCHANGES:
        typer.echo(f"Error: exchange_id must be one of: {_VALID_EXCHANGES}", err=True)
        raise typer.Exit(1)

    cfg = load_config()
    old_name = cfg.exchange.name
    old_mode = cfg.exchange.paper_execution_mode

    if eid == "paper_internal":
        cfg.exchange.paper_execution_mode = "paper_internal"
    else:
        ccxt_ex, paper_mode = _EXCHANGE_UNIFICATION_MAP[eid]
        cfg.exchange.name = eid
        cfg.market_data.ccxt_exchange = ccxt_ex
        cfg.exchange.paper_execution_mode = paper_mode
        cfg.risk.allowed_exchange = eid

    save_config(cfg)

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
    typer.echo(f"  market_data.ccxt_exchange:  {cfg.market_data.ccxt_exchange}")
    typer.echo(f"  risk.allowed_exchange:      {cfg.risk.allowed_exchange}")
    typer.echo("")
    typer.echo("Note: restart gateway for changes to take full effect.")


@exchange_app.command("mode")
def mode(
    value: str = typer.Argument(..., help="Trading mode: demo | live"),
):
    """Toggle exchange mode between demo and live."""
    from aletheia.config.loader import load_config, save_config

    val = value.strip().lower()
    if val not in ("demo", "live"):
        typer.echo("Error: mode must be 'demo' or 'live'", err=True)
        raise typer.Exit(1)

    cfg = load_config()
    old = cfg.exchange.mode
    cfg.exchange.mode = val
    save_config(cfg)

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
    typer.echo(f"  ccxt_exchange:        {cfg.market_data.ccxt_exchange}")
    typer.echo(f"  allowed_exchange:     {cfg.risk.allowed_exchange}")
    typer.echo(f"  enabled_markets:      {cfg.exchange.enabled_markets}")
