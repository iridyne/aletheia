"""CLI entrypoint for Aletheia Trading OS."""

import typer

# Windows: enforce SelectorEventLoop before any CLI command calls asyncio.run()
# (doctor / data backfill / model probe / bitget sync, etc.). See §17 in
# aletheia/async_utils.py, plus the CLI-provider incompatibility constraint.
from aletheia.async_utils import apply_windows_selector_policy

apply_windows_selector_policy()

from aletheia.cli.gateway import gateway_app
from aletheia.cli.model import model_app
from aletheia.cli.permission import permission_app
from aletheia.cli.secrets_cmd import secrets_app
from aletheia.cli.bitget_cmd import bitget_app
from aletheia.cli.risk_cmd import risk_app
from aletheia.cli.update_cmd import update_app
from aletheia.cli.runtime_cmd import runtime_app
from aletheia.cli.knowledge_cmd import knowledge_app
from aletheia.cli.doctor_cmd import doctor_app
from aletheia.cli.audit_cmd import audit_app
from aletheia.cli.operator import agent_app
from aletheia.cli.tunnel import tunnel_app
from aletheia.cli.inspect_cmd import inspect_app
from aletheia.cli.data_cmd import data_app
from aletheia.cli.exchange_cmd import exchange_app
from aletheia.cli.alpha import alpha_app
from aletheia.cli.flow import flow_app
from aletheia.cli.funnel import funnel_app
from aletheia.cli.sizing import sizing_app
from aletheia.cli.thesis import thesis_app

app = typer.Typer(
    name="aletheia",
    help="Aletheia Trading OS — local-first AI-native agentic trading system.",
    no_args_is_help=True,
)

# Bootstrap surface — keep on the CLI: these do what a not-yet-running web UI cannot.
app.add_typer(gateway_app, name="gateway", help="Gateway lifecycle commands.")
app.add_typer(doctor_app, name="doctor", help="Probe this machine's real state (single source of truth).")
app.add_typer(audit_app, name="audit", help="Audit trail verification (hash chain integrity).")
app.add_typer(agent_app, name="agent", help="Operator Agent control-plane commands.")
app.add_typer(runtime_app, name="runtime", help="PI runtime continuity commands.")
app.add_typer(tunnel_app, name="tunnel", help="Cloudflare Tunnel (public access).")
app.add_typer(inspect_app, name="inspect", help="Query runtime state (read-only, agent-friendly).")
app.add_typer(data_app, name="data", help="Historical data management.")
app.add_typer(exchange_app, name="exchange", help="Switch active exchange, mode, and view config.")
app.add_typer(alpha_app, name="alpha", help="Read-only alpha attribution audit.")
app.add_typer(flow_app, name="flow", help="Read-only opportunity flow ledger.")
app.add_typer(funnel_app, name="funnel", help="Read-only trade funnel audit.")
app.add_typer(sizing_app, name="sizing", help="Read-only sizing trace audit.")
app.add_typer(thesis_app, name="thesis", help="Read-only thesis lineage audit.")

# Operator/debug tools with NO web equivalent — kept (only some subcommands; see code).
app.add_typer(bitget_app, name="bitget", help="Bitget connection (test deprecated → dashboard; sync is a debug tool).")
app.add_typer(knowledge_app, name="knowledge", help="Knowledge base / experience-loop tools (reindex, search).")

# [DEPRECATED → dashboard] superseded by the web UI; still work, kept as an escape hatch.
app.add_typer(model_app, name="model", help="[deprecated → Settings] Model provider commands.")
app.add_typer(permission_app, name="permission", help="[deprecated → Settings] Agent permission mode.")
app.add_typer(secrets_app, name="secrets", help="[deprecated → Settings] Secrets management.")
app.add_typer(risk_app, name="risk", help="[deprecated → Risk panel] Risk / kill-switch commands.")
app.add_typer(update_app, name="update", help="[deprecated → Settings] Code self-upgrade commands.")


@app.command()
def version():
    """Show version."""
    from aletheia import __version__
    typer.echo(f"Aletheia Trading OS v{__version__}")


if __name__ == "__main__":
    app()
