"""CLI entrypoint for Aletheia Trading OS."""

import atexit
import sys
import typer
from pathlib import Path

# Windows: enforce SelectorEventLoop before any CLI command calls asyncio.run()
# (doctor / data backfill / model probe / bitget sync, etc.). See §17 in
# aletheia/async_utils.py, plus the CLI-provider incompatibility constraint.
from aletheia.async_utils import apply_windows_selector_policy

apply_windows_selector_policy()

from aletheia.cli.gateway import gateway_app
from aletheia.cli.model import model_app
from aletheia.cli.secrets_cmd import secrets_app
from aletheia.cli.bitget_cmd import bitget_app
from aletheia.cli.risk_cmd import risk_app
from aletheia.cli.update_cmd import update_app
from aletheia.cli.runtime_cmd import runtime_app
from aletheia.cli.knowledge_cmd import knowledge_app
from aletheia.cli.guard_cmd import guard_app
from aletheia.cli.doctor_cmd import doctor_app
from aletheia.cli.audit_cmd import audit_app
from aletheia.cli.install import install as _run_install, uninstall as _run_uninstall, _is_interactive
from aletheia.cli.operator import agent_app
from aletheia.cli.tunnel import tunnel_app
from aletheia.cli.inspect_cmd import inspect_app
from aletheia.cli.data_cmd import data_app
from aletheia.cli.exchange_cmd import exchange_app
from aletheia.cli.network_cmd import network_app
from aletheia.cli.opportunity import opportunity_app
from aletheia.cli.thesis import thesis_app
from aletheia.cli.dev import dev_app
from aletheia.cli.evidence_cmd import evidence_app
from aletheia.cli.control_cmd import control_app
from aletheia.cli.observe_cmd import observe_app
from aletheia.cli.learning_cmd import learning_app


def _register_telegram_extension() -> None:
    from aletheia.telegram import configure_transport
    configure_transport(None, None)
    try:
        from extensions.telegram import register_transport
    except ImportError:
        return
    register_transport()


_register_telegram_extension()

def _register_exchange_extension() -> None:
    try:
        from extensions.exchanges.bitget.extension import register_uta_provider
    except (ImportError, ModuleNotFoundError):
        return
    register_uta_provider()


_register_exchange_extension()

def _register_fast_stream_extension() -> None:
    from aletheia.universe.fast_stream import configure_fast_market_stream
    configure_fast_market_stream("bitget_demo", None, market_types=frozenset())
    try:
        from extensions.exchanges.bitget.fast_stream import register_fast_market_stream
        register_fast_market_stream()
    except ModuleNotFoundError:
        return


_register_fast_stream_extension()

def _register_model_extension() -> None:
    try:
        from extensions.model_endpoints import register_model_providers
        register_model_providers()
    except ImportError:
        return


_register_model_extension()


def _register_news_extension() -> None:
    try:
        from extensions.news import register_news_jobs
    except ModuleNotFoundError:
        return
    register_news_jobs()


_register_news_extension()


def _register_defillama_extension() -> None:
    try:
        from extensions.defillama import register_defillama_collector
        register_defillama_collector()
    except ModuleNotFoundError:
        return


_register_defillama_extension()


def _register_macro_extension() -> None:
    try:
        from extensions.macro import register_collector
        register_collector()
    except ModuleNotFoundError:
        return


_register_macro_extension()


def _register_fred_extension() -> None:
    try:
        from extensions.fred import register_collector
        register_collector()
    except ModuleNotFoundError:
        return


_register_fred_extension()


def _register_coinalyze_extension() -> None:
    try:
        from extensions.coinalyze import register_collector
        register_collector()
    except ModuleNotFoundError:
        return


_register_coinalyze_extension()


def _register_volume_scanner_extension() -> None:
    try:
        from extensions.volume_scanner import register_collector
        register_collector()
    except ModuleNotFoundError:
        return


_register_volume_scanner_extension()


def _register_coingecko_extension() -> None:
    try:
        from extensions.coingecko import register_top_collector
        register_top_collector()
    except ModuleNotFoundError:
        return


_register_coingecko_extension()


def _register_hyperliquid_extension() -> None:
    try:
        from extensions.hyperliquid import register_collector
        register_collector()
    except ModuleNotFoundError:
        return


_register_hyperliquid_extension()


def _register_deribit_extension() -> None:
    try:
        from extensions.deribit import register_collector
        register_collector()
    except ModuleNotFoundError:
        return


_register_deribit_extension()


def _register_market_data_extensions() -> None:
    try:
        from extensions.market_data import register_market_data
    except ModuleNotFoundError:
        return
    register_market_data()


_register_market_data_extensions()


def _register_research_processor() -> None:
    from aletheia.agent_runtime.scheduler import configure_research_processor

    try:
        from extensions.serenity.research_worker import _process_research_run, handle_research_crash
    except ModuleNotFoundError:
        configure_research_processor(None, None)
    else:
        configure_research_processor(_process_research_run, handle_research_crash)


_register_research_processor()


def _register_research_extension() -> None:
    try:
        from extensions.research import register_research_agent
        register_research_agent()
    except ModuleNotFoundError:
        return


_register_research_extension()


def _register_observation_extension() -> None:
    try:
        from extensions.observation import register_equity_observer
        register_equity_observer()
    except ModuleNotFoundError:
        return


_register_observation_extension()


def _register_learning_extension() -> None:
    try:
        from extensions.learning import register_lesson_reviewer
        register_lesson_reviewer()
    except ModuleNotFoundError:
        return


_register_learning_extension()


def _register_feedback_extension() -> None:
    from aletheia.analytics.reasoning_feedback import configure_feedback_builder
    configure_feedback_builder(None)
    try:
        from extensions.learning import register_feedback_builder
        register_feedback_builder()
    except ModuleNotFoundError:
        return


_register_feedback_extension()


def _register_calibration_extension() -> None:
    from aletheia.analytics.confidence_calibration import configure_calibration_builder
    configure_calibration_builder(None)
    try:
        from extensions.learning import register_calibration_builder
        register_calibration_builder()
    except ModuleNotFoundError:
        return


_register_calibration_extension()


def _register_memo_extension() -> None:
    from aletheia.agent_runtime.scheduler import configure_memo_synthesizer
    configure_memo_synthesizer(None)
    try:
        from extensions.learning import register_memo_synthesizer
        register_memo_synthesizer()
    except ModuleNotFoundError:
        return


_register_memo_extension()


def _register_dreaming_extension() -> None:
    from aletheia.agent_runtime.scheduler import configure_lesson_extractor
    configure_lesson_extractor(None)
    try:
        from extensions.learning import register_dreaming_agent
        register_dreaming_agent()
    except ModuleNotFoundError:
        return


_register_dreaming_extension()


def _register_post_trade_extension() -> None:
    try:
        from extensions.learning import register_post_trade_reviewer
        register_post_trade_reviewer()
    except ModuleNotFoundError:
        return


_register_post_trade_extension()


def _register_prediction_extension() -> None:
    try:
        from extensions.prediction import register_prediction_observer
        register_prediction_observer()
    except ModuleNotFoundError:
        return


_register_prediction_extension()


app = typer.Typer(
    name="aletheia",
    help="Aletheia Trading OS — local-first AI-native agentic trading system.",
    no_args_is_help=True,
)

# Bootstrap surface — keep on the CLI: these do what a not-yet-running web UI cannot.
app.add_typer(gateway_app, name="gateway", help="Gateway lifecycle commands.")
app.add_typer(doctor_app, name="doctor", help="Probe this machine's real state (single source of truth).")
app.command(name="install", help="Install a global `aletheia` command shim.")(_run_install)
app.command(name="uninstall", help="Remove the global `aletheia` command shim.")(_run_uninstall)
app.add_typer(audit_app, name="audit", help="Audit trail verification (hash chain integrity).")
app.add_typer(agent_app, name="agent", help="Operator Agent control-plane commands.")
app.add_typer(runtime_app, name="runtime", help="Runtime home migration and offline schema retirement.")
app.add_typer(tunnel_app, name="tunnel", help="Cloudflare Tunnel (public access).")
app.add_typer(network_app, name="network", help="System proxy / network egress configuration.")
app.add_typer(inspect_app, name="inspect", help="Query runtime state (read-only, agent-friendly).")
try:
    from ops.maintenance.settlement_requeue import requeue_unobserved_settlements
except ModuleNotFoundError:
    pass
else:
    data_app.command("requeue-unobserved-settlements")(requeue_unobserved_settlements)

try:
    from ops.maintenance.outcome_integrity import remediate_outcomes
except ModuleNotFoundError:
    pass
else:
    knowledge_app.command("remediate-outcomes")(remediate_outcomes)

try:
    from ops.maintenance.database_compaction import compact_database_command
except ModuleNotFoundError:
    pass
else:
    data_app.command("compact")(compact_database_command)

app.add_typer(data_app, name="data", help="Historical data management.")
app.add_typer(exchange_app, name="exchange", help="Switch active exchange, mode, and view config.")
app.add_typer(opportunity_app, name="opportunity", help="Analyze opportunity lifecycle and outcomes.")
app.add_typer(thesis_app, name="thesis", help="Thesis lineage snapshots and record export.")
app.add_typer(dev_app, name="dev", help="Isolated worktree development preview commands.")
app.add_typer(evidence_app, name="evidence", help="Evidence epoch management and observation window anchoring.")
app.add_typer(control_app, name="control", help="Machine governance control plane: SHA, epochs, authority, window continuity.")
app.add_typer(observe_app, name="observe", help="Observation Lab: register/list/status/sync/evaluate/close.")
app.add_typer(
    learning_app,
    name="learning",
    help="Read-only hypothesis lineage, Economic Knowledge, yield, and research advice.",
)

# Operator/debug tools with NO web equivalent — kept (only some subcommands; see code).
app.add_typer(bitget_app, name="bitget", help="Bitget connection (test deprecated → dashboard; sync is a debug tool).")
app.add_typer(knowledge_app, name="knowledge", help="Knowledge base / experience-loop tools (reindex, search).")
app.add_typer(guard_app, name="guard", help="Trading-guard admin: audit learned guards, downgrade to shadow-only.")

# [DEPRECATED → dashboard] superseded by the web UI; still work, kept as an escape hatch.
app.add_typer(model_app, name="model", help="Model endpoint diagnostics; configure endpoints in Settings.")
app.add_typer(secrets_app, name="secrets", help="[deprecated → Settings] Secrets management.")
app.add_typer(risk_app, name="risk", help="[deprecated → Risk panel] Risk / kill-switch commands.")

# Update crosses the running dashboard process boundary, so its CLI remains supported.
app.add_typer(
    update_app,
    name="update",
    help="Install the latest verified update for this runtime.",
)


@app.command()
def version():
    """Show version."""
    from aletheia import __version__

    typer.echo(f"Aletheia Trading OS v{__version__}")


# Evaluated at import time so the atexit handler can use it.
_RAN_INSTALL: bool = sys.argv[1:2] == ["install"]


def _install_asked_path() -> Path:
    return Path.home() / ".aletheia_install_asked"


def _entry_point() -> None:
    atexit.register(_post_command_hint)
    app()


def _post_command_hint() -> None:
    try:
        _do_post_command_hint()
    except Exception:
        pass


def _do_post_command_hint() -> None:
    asked = _install_asked_path()
    if asked.exists():
        return
    if _RAN_INSTALL:
        asked.touch()
        return
    if not _is_interactive():
        return
    sys.stdout.write("\nInstall a global `aletheia` command? [Y/n] ")
    sys.stdout.flush()
    try:
        answer = sys.stdin.readline().strip().lower()
    except (EOFError, OSError):
        return
    asked.touch()
    if answer in ("", "y", "yes"):
        try:
            _run_install(force=False, yes=True, no_update_shell=False)
        except SystemExit:
            pass


if __name__ == "__main__":
    app()
