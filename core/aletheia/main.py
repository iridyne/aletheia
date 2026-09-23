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
from aletheia.cli.permission import permission_app
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
from aletheia.cli.alpha import alpha_app
from aletheia.cli.opportunity import opportunity_app
from aletheia.cli.flow import flow_app
from aletheia.cli.funnel import funnel_app
from aletheia.cli.sizing import sizing_app
from aletheia.cli.thesis import thesis_app
from aletheia.cli.dev import dev_app
from aletheia.cli.evidence_cmd import evidence_app
from aletheia.cli.control_cmd import control_app
from aletheia.cli.observe_cmd import observe_app
from aletheia.cli.learning_cmd import learning_app

app = typer.Typer(
    name="aletheia",
    help="Aletheia Trading OS — local-first AI-native agentic trading system.",
    no_args_is_help=True,
)


@app.callback()
def _configure_runtime() -> None:
    from aletheia.agents import register_agent, unregister_agent

    from aletheia.agent_runtime.scheduler import configure_optional_evidence_jobs, configure_learning_job_lifecycle
    from aletheia.analytics.family_scorecard import configure_shadow_cohort_reader
    from aletheia.agent_shell.context import configure_learning_context_reader
    from aletheia.universe.outcome_recorder import configure_guard_candidate_writer

    try:
        from extensions.learning.lessons import LessonStore
    except ModuleNotFoundError as exc:
        if exc.name not in {"extensions", "extensions.learning", "extensions.learning.lessons"}:
            raise
        configure_learning_context_reader(None)
    else:
        configure_learning_context_reader(LessonStore().list_all)
    try:
        from extensions.knowledge.manager import create_guard_candidate
    except ModuleNotFoundError as exc:
        if exc.name not in {"extensions", "extensions.knowledge", "extensions.knowledge.manager"}:
            raise
        configure_guard_candidate_writer(None)
    else:
        configure_guard_candidate_writer(create_guard_candidate)

    try:
        from extensions.counterfactual.trades import resolve_stale_shadows
        from extensions.counterfactual.family_scorecard import read_shadow_cohort
    except ModuleNotFoundError as exc:
        if exc.name not in {"extensions", "extensions.counterfactual", "extensions.counterfactual.trades", "extensions.counterfactual.family_scorecard"}:
            raise
        resolve_stale_shadows = read_shadow_cohort = None
    try:
        from extensions.analytics.guard_promotion import _deprecate_expired_learned_guards
    except ModuleNotFoundError as exc:
        if exc.name not in {"extensions", "extensions.analytics", "extensions.analytics.guard_promotion", "extensions.knowledge"}:
            raise
        _deprecate_expired_learned_guards = None
    try:
        from extensions.analytics.filter_controller import run_controller_observe
    except ModuleNotFoundError as exc:
        if exc.name not in {"extensions", "extensions.analytics", "extensions.analytics.filter_controller"}:
            raise
        run_controller_observe = None
    try:
        from extensions.learning.control import (
            daily_catchup_due, learning_job_gate_reason, record_learning_job_skipped,
            record_learning_job_started, record_learning_job_success, record_learning_job_failure,
        )
    except ModuleNotFoundError as exc:
        if exc.name not in {"extensions", "extensions.learning", "extensions.learning.control"}:
            raise
        daily_catchup_due = None
        learning_job_gate_reason = record_learning_job_skipped = record_learning_job_started = None
        record_learning_job_success = record_learning_job_failure = None
    configure_learning_job_lifecycle(
        gate=learning_job_gate_reason, skipped=record_learning_job_skipped,
        started=record_learning_job_started, succeeded=record_learning_job_success,
        failed=record_learning_job_failure,
    )
    configure_optional_evidence_jobs(
        resolve_shadows=resolve_stale_shadows, expire_guards=_deprecate_expired_learned_guards,
        observe_filters=run_controller_observe, daily_catchup_due=daily_catchup_due,
    )
    configure_shadow_cohort_reader(read_shadow_cohort)

    from aletheia.storage.repositories import configure_observation_readers

    try:
        from extensions.observation_lab.main_readers import (
            _canonical_lab_authority, _observation_retention_floor,
            _active_observation_claim_refs, list_observation_filter_outcome_priority_keys,
            active_benchmark, outcome_write_guard,
        )
    except ModuleNotFoundError as exc:
        if exc.name not in {"extensions", "extensions.observation_lab", "extensions.observation_lab.main_readers"}:
            raise
        configure_observation_readers()
    else:
        configure_observation_readers(
            authority=_canonical_lab_authority, retention_floor=_observation_retention_floor,
            retention_refs=_active_observation_claim_refs,
            priority_keys=list_observation_filter_outcome_priority_keys,
            active_benchmark=active_benchmark, outcome_guard=outcome_write_guard,
        )


    try:
        from extensions.research.equity_observer import run_equity_observer
        from extensions.research.market_observer import run_market_observer
    except ModuleNotFoundError as exc:
        if exc.name not in {"extensions", "extensions.research"}:
            raise
        unregister_agent("equity_observer")
        unregister_agent("market_observer")
    else:
        register_agent("equity_observer")(run_equity_observer)
        register_agent("market_observer")(run_market_observer)

    try:
        from extensions.research.research_agent import run_research_agent
        from extensions.research.risk_critic import run_risk_critic
        from extensions.research.post_trade_reviewer import run_post_trade_reviewer
    except ModuleNotFoundError as exc:
        if exc.name not in {"extensions", "extensions.research", "extensions.knowledge"}:
            raise
        unregister_agent("research_agent")
        unregister_agent("risk_critic")
        unregister_agent("post_trade_reviewer")
    else:
        register_agent("research_agent")(run_research_agent)
        register_agent("risk_critic")(run_risk_critic)
        register_agent("post_trade_reviewer")(run_post_trade_reviewer)

    try:
        from extensions.research.prediction_observer import run_prediction_observer
        from extensions.research.divergence_scanner import run_divergence_scanner
    except ModuleNotFoundError as exc:
        if exc.name not in {
            "extensions", "extensions.research", "extensions.data", "extensions.data.ccxt_provider", "extensions.data.polymarket_provider",
        }:
            raise
        unregister_agent("prediction_observer")
        unregister_agent("divergence_scanner")
    else:
        register_agent("prediction_observer")(run_prediction_observer)
        register_agent("divergence_scanner")(run_divergence_scanner)

    from aletheia.agent_runtime.scheduler import configure_research_producers

    try:
        from extensions.research.dreaming_agent import _extract_lessons, run_dreaming_agent
        from extensions.research.lesson_reviewer import run_lesson_reviewer
    except ModuleNotFoundError as exc:
        if exc.name not in {"extensions", "extensions.research", "extensions.knowledge", "extensions.learning", "extensions.learning.control"}:
            raise
        unregister_agent("dreaming_agent")
        unregister_agent("lesson_reviewer")
        _extract_lessons = None
    else:
        register_agent("dreaming_agent")(run_dreaming_agent)
        register_agent("lesson_reviewer")(run_lesson_reviewer)

    try:
        from extensions.research.research_worker import enqueue_research_run
    except ModuleNotFoundError as exc:
        if exc.name not in {"extensions", "extensions.research"}:
            raise
        enqueue_research_run = None

    configure_research_producers(
        extract_lessons=_extract_lessons, enqueue_run=enqueue_research_run,
    )

    from aletheia.notifications import configure_telegram_sender

    try:
        from extensions.telegram.delivery import send_telegram_alert
    except ModuleNotFoundError as exc:
        if exc.name not in {"extensions", "extensions.telegram", "extensions.telegram.delivery"}:
            raise
        configure_telegram_sender(None)
    else:
        configure_telegram_sender(send_telegram_alert)

    from aletheia.model_provider.router import configure_model_provider
    from aletheia.data.providers import configure_macro_reader, configure_prediction_history_reader
    from aletheia.universe.outcome_recorder import configure_structure_shadow_settler

    try:
        from extensions.data.yfinance_provider import fetch_macro_context
    except ModuleNotFoundError as exc:
        if exc.name not in {"extensions", "extensions.data", "extensions.data.yfinance_provider"}:
            raise
        configure_macro_reader(None)
    else:
        configure_macro_reader(fetch_macro_context)

    try:
        from extensions.data.polymarket_provider import fetch_market_history
    except ModuleNotFoundError as exc:
        if exc.name not in {"extensions", "extensions.data", "extensions.data.polymarket_provider"}:
            raise
        configure_prediction_history_reader(None)
    else:
        configure_prediction_history_reader(fetch_market_history)

    try:
        from extensions.data.perp_structure_shadow import settle_due_perp_structure_outcomes
    except ModuleNotFoundError as exc:
        if exc.name not in {"extensions", "extensions.data", "extensions.data.perp_structure_shadow"}:
            raise
        configure_structure_shadow_settler(None)
    else:
        configure_structure_shadow_settler(settle_due_perp_structure_outcomes)

    from aletheia.data.providers import configure_listing_reader, configure_market_data_factory

    try:
        from extensions.data.ccxt_provider import read_market_listing, create_market_data_provider
    except ModuleNotFoundError as exc:
        if exc.name not in {"extensions", "extensions.data", "extensions.data.ccxt_provider"}:
            raise
        configure_listing_reader(None)
        configure_market_data_factory(None)
    else:
        configure_listing_reader(read_market_listing)
        configure_market_data_factory(create_market_data_provider)

    from aletheia.economics.pricing_sync import configure_pricing_fetcher

    try:
        from extensions.data.public_pricing import fetch_price_map
    except ModuleNotFoundError as exc:
        if exc.name not in {"extensions", "extensions.data", "extensions.data.public_pricing"}:
            raise
        configure_pricing_fetcher(None)
    else:
        configure_pricing_fetcher(fetch_price_map)

    from aletheia.agent_runtime.scheduler import configure_analytics_reports
    from aletheia.analytics.go_live_scorecard import configure_calibration_readers

    try:
        from extensions.analytics.insight_synthesizer import synthesize_trading_memo
        from extensions.analytics.reasoning_feedback import build_reasoning_feedback
        from extensions.analytics.confidence_calibration import (
            build_calibration_report, compute_calibration_score, get_latest_calibration_report,
        )
    except ModuleNotFoundError as exc:
        if exc.name not in {"extensions", "extensions.analytics"}:
            raise
        configure_analytics_reports()
        configure_calibration_readers()
    else:
        configure_analytics_reports(
            synthesize_memo=synthesize_trading_memo,
            build_feedback=build_reasoning_feedback,
            build_calibration=build_calibration_report,
        )
        configure_calibration_readers(
            score_reader=compute_calibration_score,
            report_reader=get_latest_calibration_report,
        )

    from aletheia.uta.manager import configure_uta_provider
    from aletheia.execution.order_constraints import configure_order_constraints_reader

    try:
        from extensions.exchanges.composition import bind_uta_provider
    except ModuleNotFoundError as exc:
        if exc.name not in {
            "extensions.exchanges.composition",
            "extensions", "extensions.exchanges", "extensions.exchanges.ccxt",
            "extensions.exchanges.ccxt.adapter", "extensions.exchanges.ccxt.profile",
        }:
            raise
        configure_uta_provider(registry_factory=None, retention_contract=None)
        configure_order_constraints_reader(None)
    else:
        bind_uta_provider()

    from aletheia.risk.demo_entry_quality import configure_regime_reader
    from aletheia.universe.service import configure_regime_refresh

    try:
        from extensions.regime.detector import read_published_regime, collect_regime_record
    except ModuleNotFoundError as exc:
        if exc.name not in {"extensions", "extensions.regime", "extensions.regime.detector"}:
            raise
        configure_regime_reader(None)
        configure_regime_refresh(None)
    else:
        configure_regime_reader(read_published_regime)
        configure_regime_refresh(collect_regime_record)

    from aletheia.trust.behavior_authority import configure_candidate_filter_path

    try:
        from extensions.learning.registry import candidate_filter_path_declared
    except ModuleNotFoundError as exc:
        if exc.name not in {"extensions", "extensions.learning", "extensions.learning.registry"}:
            raise
        configure_candidate_filter_path(None)
    else:
        configure_candidate_filter_path(candidate_filter_path_declared)

    from aletheia.trust.learning_safety import configure_learning_safety

    try:
        from extensions.learning.control import learning_influence_gate_reason, global_learning_refreeze_reason
    except ModuleNotFoundError as exc:
        if exc.name not in {"extensions", "extensions.learning", "extensions.learning.control"}:
            raise
        configure_learning_safety()
    else:
        configure_learning_safety(influence_gate=learning_influence_gate_reason, refreeze_reason=global_learning_refreeze_reason)

    from aletheia.trading.sizing_policy import configure_sizing_evidence

    try:
        from extensions.learning.sizing_evidence import resolve_sizing_evidence
    except ModuleNotFoundError as exc:
        if exc.name not in {"extensions", "extensions.learning", "extensions.learning.sizing_evidence"}:
            raise
        configure_sizing_evidence(None)
    else:
        configure_sizing_evidence(resolve_sizing_evidence)

    from aletheia.trading.entry_learning import configure_entry_learning

    try:
        from extensions.learning.entry_context import load_entry_learning_context
    except ModuleNotFoundError as exc:
        if exc.name not in {"extensions", "extensions.learning", "extensions.learning.entry_context"}:
            raise
        configure_entry_learning(None)
    else:
        configure_entry_learning(load_entry_learning_context)

    from aletheia.risk.demo_entry_quality import configure_demo_quality_epoch

    try:
        from extensions.learning.demo_entry_quality import ensure_demo_entry_quality_policy_epoch
    except ModuleNotFoundError as exc:
        if exc.name not in {"extensions", "extensions.learning", "extensions.learning.demo_entry_quality"}:
            raise
        configure_demo_quality_epoch(None)
    else:
        configure_demo_quality_epoch(ensure_demo_entry_quality_policy_epoch)

    from aletheia.risk.engine import configure_risk_lesson_treatment

    try:
        from extensions.learning.risk_evidence import authorize_lesson_treatment
    except ModuleNotFoundError as exc:
        if exc.name not in {"extensions", "extensions.learning", "extensions.learning.risk_evidence"}:
            raise
        configure_risk_lesson_treatment(None)
    else:
        configure_risk_lesson_treatment(authorize_lesson_treatment)

    from aletheia.portfolio.outcome_hooks import configure_outcome_feedback

    try:
        from extensions.learning.outcome_feedback import execute_outcome_feedback
    except ModuleNotFoundError as exc:
        if exc.name not in {"extensions", "extensions.learning", "extensions.learning.outcome_feedback"}:
            raise
        configure_outcome_feedback(None)
    else:
        configure_outcome_feedback(execute_outcome_feedback)

    try:
        from extensions.model_endpoints.http_runtime import build_http_provider, http_configuration_identity, http_credential_status, verify_http_model_selection
    except ModuleNotFoundError as exc:
        if exc.name not in {"extensions", "extensions.model_endpoints", "extensions.model_endpoints.http_runtime"}:
            raise
        configure_model_provider(None)
    else:
        configure_model_provider(
            build_http_provider, identity_reader=http_configuration_identity,
            status_reader=http_credential_status, selection_verifier=verify_http_model_selection,
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
app.add_typer(data_app, name="data", help="Historical data management.")
app.add_typer(exchange_app, name="exchange", help="Switch active exchange, mode, and view config.")
app.add_typer(opportunity_app, name="opportunity", help="Analyze opportunity lifecycle and outcomes.")
app.add_typer(thesis_app, name="thesis", help="Thesis lineage snapshots and record export.")
app.add_typer(alpha_app, name="alpha", help="Read-only alpha attribution audit.", hidden=True)
app.add_typer(flow_app, name="flow", help="Deprecated alias for opportunity.", hidden=True)
app.add_typer(funnel_app, name="funnel", help="Read-only trade funnel audit.", hidden=True)
app.add_typer(sizing_app, name="sizing", help="Read-only sizing trace audit.", hidden=True)
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

try:
    from extensions.model_endpoints.credential_cli import set_cmd as set_model_credentials
except ModuleNotFoundError as exc:
    if exc.name not in {"extensions", "extensions.model_endpoints", "extensions.model_endpoints.credential_cli"}:
        raise
else:
    model_app.command(name="set")(set_model_credentials)

# [DEPRECATED → dashboard] superseded by the web UI; still work, kept as an escape hatch.
app.add_typer(model_app, name="model", help="[deprecated → Settings] Model provider commands.")
app.add_typer(permission_app, name="permission", help="[deprecated → Settings] Agent permission mode.")
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
