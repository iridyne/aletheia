"""CLI entrypoint for Aletheia Trading OS."""

import typer

from aletheia.cli.gateway import gateway_app
from aletheia.cli.model import model_app
from aletheia.cli.permission import permission_app
from aletheia.cli.secrets_cmd import secrets_app
from aletheia.cli.bitget_cmd import bitget_app
from aletheia.cli.strategy import strategy_app
from aletheia.cli.risk_cmd import risk_app

app = typer.Typer(
    name="aletheia",
    help="Aletheia Trading OS — local-first AI-native agentic trading system.",
    no_args_is_help=True,
)

app.add_typer(gateway_app, name="gateway", help="Gateway lifecycle commands.")
app.add_typer(model_app, name="model", help="Model provider commands.")
app.add_typer(permission_app, name="permission", help="Agent permission mode.")
app.add_typer(secrets_app, name="secrets", help="Secrets management.")
app.add_typer(bitget_app, name="bitget", help="Bitget connection commands.")
app.add_typer(strategy_app, name="strategy", help="Strategy lifecycle commands.")
app.add_typer(risk_app, name="risk", help="Risk / kill-switch commands.")


@app.command()
def version():
    """Show version."""
    from aletheia import __version__
    typer.echo(f"Aletheia Trading OS v{__version__}")


if __name__ == "__main__":
    app()
