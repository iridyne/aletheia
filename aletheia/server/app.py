"""FastAPI application for Aletheia Trading OS."""

import logging
import asyncio
import os
import sys
import time

# Windows: enforce SelectorEventLoop process-wide BEFORE uvicorn imports this
# module and creates its server loop, and before any broker asyncio.run() in
# worker threads. Fixes §17 (IOCP drain hang) + §23/§24 (WinError 64).
# See aletheia/async_utils.py for the CLI-provider incompatibility constraint.
from aletheia.async_utils import apply_windows_selector_policy

apply_windows_selector_policy()

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from aletheia import __version__
from aletheia.config.loader import load_config
from aletheia.config.loader import bootstrap
from aletheia.server.routes.health import infra_router, router as health_router
from aletheia.server.routes.settings import router as settings_router
from aletheia.server.routes.data import router as data_router
from aletheia.server.routes.live import router as live_router
from aletheia.server.routes.model import router as model_router
from aletheia.server.routes.bitget import router as bitget_router
from aletheia.server.routes.risk import router as risk_router
from aletheia.server.routes.update import router as update_router
from aletheia.server.routes.gateway import router as gateway_router
from aletheia.server.routes.telegram import router as telegram_router
from aletheia.server.routes.news import router as news_router
from aletheia.server.routes.command import router as command_router
from aletheia.server.routes.operator import router as operator_router
from aletheia.server.routes.runtime import router as runtime_router
from aletheia.server.routes.persistence import router as persistence_router
from aletheia.server.routes.prediction import router as prediction_router
from aletheia.server.routes.universe import router as universe_router
from aletheia.server.routes.analytics import router as analytics_router
from aletheia.server.routes.capital import router as capital_router
from aletheia.server.routes.economics import router as economics_router
from aletheia.server.routes.export import router as export_router
from aletheia.server.routes.ws import router as ws_router

logger = logging.getLogger("aletheia")

LIFESPAN_TASK_CATALOG = {
    "llm_startup_gate": {"owner": "server.lifespan", "failure_policy": "pause_ai_and_trading"},
    "startup_exchange_sync": {"owner": "server.lifespan", "failure_policy": "warn"},
    "startup_exchange_gate": {"owner": "server.lifespan", "failure_policy": "pause_trading"},
    "startup_behavior_gate": {"owner": "server.lifespan", "failure_policy": "pause_ai_and_trading"},
    "pi_daemon": {"owner": "server.lifespan", "failure_policy": "warn"},
    "telegram_polling": {"owner": "server.lifespan", "failure_policy": "warn"},
    "event_loop_lag_monitor": {"owner": "server.lifespan", "failure_policy": "observe"},
    "startup_gate_recheck": {"owner": "startup_behavior_gate", "failure_policy": "retry"},
    "llm_auto_resume": {"owner": "auto_resume", "failure_policy": "retry"},
    "exchange_auto_resume": {"owner": "connectivity", "failure_policy": "retry"},
}
_lifespan_tasks: dict[str, asyncio.Task | None] = {}


def lifecycle_task_snapshot() -> list[dict[str, object]]:
    """Read-only ownership facts; this registry never starts or permits work."""
    rows = []
    for task_id, policy in LIFESPAN_TASK_CATALOG.items():
        task = _lifespan_tasks.get(task_id)
        direct = policy["owner"] == "server.lifespan"
        rows.append(
            {
                "task_id": task_id,
                **policy,
                "ownership": "direct" if direct else "module_owned",
                "state": (
                    "not_started"
                    if task is None
                    else "cancelled"
                    if task.cancelled()
                    else "finished"
                    if task.done()
                    else "running"
                )
                if direct
                else "module_owned",
                "stop_timeout_sec": None,
            }
        )
    return rows


def _log_startup_stage(stage: str, stage_started: float, startup_started: float) -> float:
    finished = time.perf_counter()
    logger.info(
        "gateway_startup_stage stage=%s duration_ms=%.1f elapsed_ms=%.1f",
        stage,
        (finished - stage_started) * 1000.0,
        (finished - startup_started) * 1000.0,
    )
    return finished


def _mount_static_dashboard() -> None:
    """Mount static dashboard files at root path.

    Called during lifespan startup, AFTER Vite watch has had time to build.
    Mounting at module-top previously hit a race: uvicorn loaded app.py before
    Vite finished its first build, so index.html was missing and the mount failed.
    """
    _static_dir = Path(__file__).resolve().parent.parent / "dashboard_static"
    if not (_static_dir.exists() and (_static_dir / "index.html").exists()):
        logger.warning(f"Static dashboard not mounted: {_static_dir} / index.html missing")
        _mount_dashboard_unavailable_page(_static_dir)
        return

    class _StaticCacheHeaders(StaticFiles):
        """StaticFiles wrapper that adds correct Cache-Control headers.

        Two cache profiles, picked by URL path:
          /assets/* — vite emits hashed filenames (index-B8SVnxU1.js); the bytes
                      at a given path are immutable forever, so cache aggressively.
          everything else (index.html, /favicon, fallback routes) — must be
                      revalidated every time so the dashboard picks up new builds
                      without ctrl+shift+R. ETag (already added by StaticFiles)
                      keeps revalidations cheap (304 Not Modified).
        """

        async def get_response(self, path, scope):
            response = await super().get_response(path, scope)
            # `path` arg uses OS separator on Windows ("assets\\foo.js"), so match
            # against scope["path"] (the original URL) which is always forward-slash.
            url_path = scope.get("path", "")
            if url_path.startswith("/assets/"):
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            else:
                response.headers["Cache-Control"] = "no-cache, must-revalidate"
            return response

    app.mount("/", _StaticCacheHeaders(directory=str(_static_dir), html=True), name="dashboard")
    logger.info(f"Static dashboard mounted from {_static_dir}")


def _mount_dashboard_unavailable_page(static_dir: Path) -> None:
    """Serve a clear emergency page instead of a JSON 404 when assets are absent.

    This route is only used in broken/direct-start cases. Normal CLI starts fail
    preflight before the API process comes up, and lifecycle verification now
    kills API-only starts so the watchdog can retry.
    """
    if getattr(app.state, "_dashboard_unavailable_route", False):
        return

    @app.get("/", include_in_schema=False)
    async def dashboard_unavailable():
        return HTMLResponse(
            status_code=503,
            content=f"""<!doctype html>
<html><head>
<meta charset=\"utf-8\" />
<meta http-equiv=\"refresh\" content=\"5\" />
<title>Aletheia dashboard unavailable</title>
<style>
body {{ background:#0a0e12; color:#e6edf3; font-family:system-ui,sans-serif; padding:32px; }}
code {{ color:#22d3ee; }}
.box {{ max-width:760px; border:1px solid #ff5d6c; border-radius:8px; padding:20px; background:#141a21; }}
</style>
</head><body>
<div class=\"box\">
<h1>Aletheia dashboard assets are unavailable</h1>
<p>The backend API is running, but the static dashboard was not mounted.</p>
<p>Expected assets under: <code>{static_dir}</code></p>
<p>Recovery: <code>cd frontend && npm run build</code>, then <code>uv run aletheia gateway restart --no-browser</code>.</p>
<p>This page refreshes every 5 seconds.</p>
</div>
</body></html>""",
        )

    app.state._dashboard_unavailable_route = True


def runtime_process_context(environ: dict[str, str] | None = None) -> dict:
    env = environ or os.environ
    raw_port = env.get("ALETHEIA_RUNTIME_PORT")
    try:
        port = int(raw_port) if raw_port else None
    except ValueError:
        port = None
    return {
        "runtime_id": env.get("ALETHEIA_RUNTIME_ID", "runtime-local-current"),
        "runtime_mode": env.get("ALETHEIA_RUNTIME_MODE", "current"),
        "runtime_state": env.get("ALETHEIA_RUNTIME_STATE", "current"),
        "port": port,
    }


def should_start_background_services(context: dict) -> bool:
    return context.get("runtime_mode") == "current"


def should_write_gateway_pidfile(context: dict) -> bool:
    return context.get("runtime_mode") == "current"


async def _llm_startup_gate(cfg) -> None:
    """Probe LLM providers at boot; pause the trading loop if none can do inference.

    Runs as a BACKGROUND task launched AFTER the scheduler is up and the HTTP port
    is already serving (see lifespan). Probing each provider can take up to
    _PROBE_TIMEOUT_SEC; doing it inline before `yield` previously delayed the port
    bind, so the spawn-verifier and OS watchdog saw a dead port and falsely
    reported the gateway as failed. Running it post-yield means the dashboard and
    /api/health are reachable WHILE the probe runs — the operator can see the
    degraded state instead of a connection-refused page.

    Fail-loud, but keep the process alive: when no provider works we pause the
    scheduler AND persist runtime.paused (so the state survives a restart and the
    Web UI shows it). We feed record_llm_failure() so /api/health reports
    `llm_unavailable:N` with no extra plumbing, and audit the decision so it is
    never silent. The process is NOT killed — that would just trigger a
    supervisor/watchdog respawn loop. Operator fixes a provider and hits
    POST /api/gateway/resume. Never raises (degrades to a logged warning)."""
    if not cfg.health.require_llm_at_startup:
        return
    try:
        from aletheia.agent_runtime.startup_gate import probe_llm_providers_at_startup

        result = await probe_llm_providers_at_startup(cfg)
    except asyncio.CancelledError:
        raise  # lifespan shutdown cancelled us mid-probe — propagate, don't swallow
    except Exception as e:
        # Issue #390: an orchestration-level exception must NOT skip the gate
        # (fail-open); it must pause the scheduler just like a "no usable provider"
        # verdict.  We synthesise a minimal result so the unified pause path below
        # can run without code duplication.
        logger.warning(
            "LLM startup gate probe raised an unexpected error — pausing trading loop "
            "(fail-safe).  Fix the error and resume from the dashboard.  Error: %s",
            e,
        )
        result = {"any_usable": False, "checked": 0, "results": [], "attempts": 1, "_probe_exception": str(e)}

    if result["any_usable"]:
        # Clear any stale failure count so /api/health starts clean.
        from aletheia.agent_runtime.health import record_llm_success
        from aletheia.agent_runtime.pause_ownership import (
            LLM_STARTUP_OWNER,
            release_pause,
        )

        record_llm_success()
        release = release_pause(
            LLM_STARTUP_OWNER,
            resume_when_clear=True,
            source="llm_startup_gate",
        )
        if release.resume_failed:
            logger.warning(
                "LLM startup gate: scheduler resume failed — pause owner retained for retry"
            )
            from aletheia.agent_runtime.auto_resume import start_auto_resume_task
            from aletheia.config.loader import load_config as _load_config

            fresh = _load_config()
            if fresh.health.auto_resume_enabled:
                start_auto_resume_task(
                    interval_sec=fresh.health.auto_resume_interval_sec
                )
        return

    # No provider could complete a real inference call — pause the loop.
    # Read-modify-write a FRESH on-disk config rather than the boot snapshot, so
    # we only flip `paused` and don't clobber any edit made during the probe
    # window (e.g. an operator changing settings via the dashboard, now reachable
    # because the port is already serving). Full elimination of the last-writer
    # race would need a file lock and is outside this startup safeguard's scope.
    from aletheia.agent_runtime.pause_ownership import LLM_STARTUP_OWNER, acquire_pause
    from aletheia.config.loader import load_config as _load_config

    acquire_pause(
        LLM_STARTUP_OWNER,
        reason="no_usable_llm_provider",
        source="llm_startup_gate",
    )
    fresh = _load_config()

    from aletheia.agent_runtime.health import record_llm_failure

    for _ in range(max(1, cfg.health.llm_unavailable_alert_after)):
        record_llm_failure()

    errors = "; ".join(f"{r['provider']}={r['error']}" for r in result["results"]) or "no enabled providers"
    logger.warning(
        "LLM startup gate: NO usable provider (%d checked) — trading loop PAUSED. "
        "Fix a provider and resume from the dashboard. Details: %s",
        result["checked"],
        errors,
    )
    try:
        from aletheia.files.jsonl import write_audit

        attempts = result.get("attempts", 1)
        write_audit(
            {
                "actor": "system",
                "source": "startup_gate",
                "event_type": "llm_startup_gate_paused",
                "entity_type": "gateway",
                "entity_id": "gateway",
                "message": (
                    f"No usable LLM provider at startup ({result['checked']} checked, "
                    f"{attempts} attempts); trading loop paused. {errors}"
                ),
            }
        )
    except Exception:
        pass

    try:
        from aletheia.notifications import notify_operator

        attempts = result.get("attempts", 1)
        notify_operator(
            "Aletheia Gateway Paused",
            f"LLM unavailable ({result['checked']} providers, {attempts} retries). Check Databricks/network.",
            source="startup_gate",
            event_type=None,
            audit=False,
            error_log=True,
        )
    except Exception:
        pass

    # Start auto-resume probe loop (runs as independent asyncio.Task, not via
    # APScheduler which is now paused). Periodically re-probes LLM and resumes
    # the system when connectivity is restored.
    if fresh.health.auto_resume_enabled:
        try:
            from aletheia.agent_runtime.auto_resume import start_auto_resume_task

            start_auto_resume_task(interval_sec=fresh.health.auto_resume_interval_sec)
        except Exception as e:
            logger.debug("auto_resume task start failed: %s", e)


async def _cancel_background_task(task: asyncio.Task | None, label: str) -> None:
    """Cancel and consume one lifespan-owned task without masking shutdown."""
    if task is None:
        return
    if not task.done():
        task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.debug("%s task ended during shutdown: %s", label, exc)


async def _run_startup_sync(sync_fn) -> None:
    """Run startup account sync outside the event loop's default executor.

    A cancelled ``asyncio.to_thread`` call remains owned by the loop's default
    executor, and loop shutdown waits for that worker indefinitely. A dedicated
    executor can be released without holding gateway/TestClient shutdown hostage;
    the broker clients still enforce their normal request timeouts.
    """
    from concurrent.futures import ThreadPoolExecutor

    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="aletheia-startup-sync")
    future = executor.submit(sync_fn)
    try:
        await asyncio.wrap_future(future)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    startup_started = time.perf_counter()
    stage_started = startup_started
    # Bump aletheia logger to DEBUG level. The default Python logging level
    # (WARNING) silently drops all info/debug output from our modules.
    # Uvicorn already configures root logging handlers, so we just set level.
    try:
        from aletheia.logging_config import configure_logging

        configure_logging()
    except Exception:
        pass
    _aletheia_logger = logging.getLogger("aletheia")
    if _aletheia_logger.level == logging.NOTSET:
        _aletheia_logger.setLevel(logging.DEBUG)
    stage_started = _log_startup_stage("logging", stage_started, startup_started)

    cfg = bootstrap()
    event_loop_lag_task = None
    # Apply proxy config to process env before any HTTP client or scheduler runs.
    # Ensures watchdog-started processes (which inherit no session env vars) still
    # route through the proxy specified in config.yaml.
    from aletheia.net import apply_proxy_env

    apply_proxy_env()
    runtime_context = runtime_process_context()
    app.state.runtime_context = runtime_context
    background_services_enabled = should_start_background_services(runtime_context)
    # Ensure PID file exists regardless of how we were launched (background
    # supervisor, foreground CLI, or bare uvicorn). Without this, /api/status
    # and /api/gateway/restart break when the supervisor PID file is missing.
    from aletheia.gateway.pidfile import write_pid, read_pid

    _owns_pid = should_write_gateway_pidfile(runtime_context) and read_pid() is None
    if _owns_pid:
        write_pid()
    app.state._owns_pid = _owns_pid
    # Record the source commit this process booted on, BEFORE any scheduler
    # thread starts — lets /api/health flag "code updated, restart needed".
    if background_services_enabled:
        from aletheia.agent_runtime.build_info import record_boot_commit

        record_boot_commit()
        # Migrate legacy checkouts away from implicit git-triggered restarts.
        # Lifecycle is owner-controlled; cleanup is idempotent and never touches
        # foreign hooks.
        try:
            from aletheia.gateway.git_hooks import remove_managed_git_hooks

            for action in remove_managed_git_hooks():
                logger.info("git-hook: %s", action)
        except Exception as e:
            logger.warning("git-hook cleanup skipped (non-fatal): %s", e)
    stage_started = _log_startup_stage("runtime_context", stage_started, startup_started)
    logger.info("Aletheia Trading OS v%s starting", __version__)
    logger.info("Dashboard: http://%s:%d", cfg.app.bind_host, runtime_context["port"] or cfg.app.dashboard_port)
    # Migrate keyring secrets to the encrypted file store so that CLI / service
    # processes (which may run under a different Windows logon session and cannot
    # reach the OS keyring) always find secrets in the file fallback path.
    try:
        from aletheia.config.secrets import migrate_keyring_to_file

        n = migrate_keyring_to_file()
        if n:
            logger.info("Migrated %d secret(s) from keyring to file store", n)
    except Exception as _e:
        logger.debug("Keyring-to-file migration skipped: %s", _e)
    stage_started = _log_startup_stage("secret_migration", stage_started, startup_started)
    # Database upgrades belong to the owner-controlled gateway/deployment phase.
    # The backend only verifies the durable ledger before starting any writer.
    from aletheia.storage.migrations import assert_migrations_current
    from aletheia.agent_runtime.tool_registry import init_tool_registry
    from aletheia.agent_runtime.scheduler import init_scheduler, stop_scheduler

    stage_started = _log_startup_stage("startup_dependency_imports", stage_started, startup_started)
    assert_migrations_current()
    stage_started = _log_startup_stage("database_migration_verification", stage_started, startup_started)
    logger.info("gateway_startup_migrations verified=true pending=0")
    if background_services_enabled:
        try:
            from aletheia.agent_runtime.run_logger import mark_stale_running_runs

            stale_runs = mark_stale_running_runs(
                reason="gateway startup reconciled previous incomplete agent run",
                source="startup",
            )
            if stale_runs:
                logger.warning("Marked %d stale agent run(s) during startup reconciliation", stale_runs)
        except Exception as e:
            logger.warning("Agent run startup reconciliation failed: %s", e)
        # Runtime Session: one row per process lifecycle. Created AFTER migrations
        # (its table must exist) and after record_boot_commit (for the boot commit).
        # Core trading facts produced this boot inherit its identity. Best-effort.
        try:
            from aletheia.agent_runtime.runtime_session import start_runtime_session

            start_runtime_session(cfg, runtime_context)
        except Exception as e:
            logger.warning("Runtime session start failed (non-fatal): %s", e)
        try:
            from aletheia.storage.event_store import get_event_store

            mirror_result = get_event_store().reconcile_jsonl(limit=100)
            if mirror_result["attempted"]:
                logger.info("Event JSONL mirror reconciliation: %s", mirror_result)
        except Exception as e:
            logger.warning("Event JSONL mirror reconciliation failed (non-fatal): %s", e)
    stage_started = _log_startup_stage("runtime_reconciliation", stage_started, startup_started)
    init_tool_registry()
    from aletheia.agent_runtime.job_queue import get_agent_job_manager

    agent_job_manager = get_agent_job_manager()
    if background_services_enabled:
        agent_job_manager.start()
    if background_services_enabled:
        try:
            init_scheduler()
        except Exception as e:
            logger.warning("Scheduler failed to start: %s", e)
    else:
        logger.info("Runtime mode %s: background services disabled", runtime_context["runtime_mode"])
    stage_started = _log_startup_stage("scheduler_initialization", stage_started, startup_started)
    # Fail-loud LLM gate, run as a BACKGROUND task (not awaited here): if no
    # provider can actually do inference it pauses the scheduler and persists
    # runtime.paused. Deferring it until AFTER init_scheduler + yield means the
    # HTTP port binds immediately, so the spawn-verifier/watchdog see a live port
    # and the dashboard is reachable WHILE the probe runs. Booting un-paused for
    # the few seconds the probe takes is safe: scheduler jobs themselves re-check
    # cfg.runtime.paused, and the only time-sensitive job (market_data) is
    # harmless to run once. See _llm_startup_gate.
    llm_gate_task = (
        asyncio.create_task(_llm_startup_gate(cfg), name="llm_startup_gate") if background_services_enabled else None
    )

    # #138: initial exchange-state sync deferred to a POST-yield worker-thread task.
    # It is a blocking aggregation that can take up to 30s on a slow/unreachable broker;
    # running it inline before `yield` delayed the HTTP port bind, so the spawn-verifier /
    # OS watchdog could see a dead port and falsely report gateway failure — the same
    # reason the LLM/exchange gates are deferred. asyncio.to_thread keeps it off the loop.
    # The scheduler's periodic exchange-sync keeps state fresh regardless.
    startup_sync_task = None
    if background_services_enabled:

        async def _startup_exchange_sync():
            try:
                from aletheia.portfolio.exchange_sync import sync_all_exchanges

                await _run_startup_sync(sync_all_exchanges)
                logger.info("startup exchange sync complete")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("startup exchange sync failed (non-fatal): %s", e)

        startup_sync_task = asyncio.create_task(_startup_exchange_sync(), name="startup_exchange_sync")

    # Exchange connectivity gate — if exchange is unreachable at startup, pause
    # immediately and start auto-resume (same pattern as LLM gate).
    exchange_gate_task = None
    if background_services_enabled:
        from aletheia.agent_runtime.connectivity import startup_exchange_gate, set_uvicorn_loop

        # #145: capture the persistent uvicorn loop so a mid-run connectivity loss (detected
        # on an ephemeral scheduler-job loop) can schedule its auto-resume task HERE, where it
        # survives — not on the per-job loop that is torn down right after the check runs.
        set_uvicorn_loop(asyncio.get_running_loop())
        exchange_gate_task = asyncio.create_task(startup_exchange_gate(cfg), name="startup_exchange_gate")

    # Startup Behavior Gate — hold AI decisions + new trading PAUSED until a set
    # of ground-truth checks pass (DB integrity, config↔active-scope, exchange
    # sync / paper_internal, no unexplained pending orders/closes, no active
    # reconciliation incident, learning influence within bounds, no user
    # pause/kill switch). Runs as a post-yield task (so the port binds first) but
    # its checks are all LOCAL/fast reads. On failure it applies a layered pause
    # (essential data jobs keep running), records a durable blocker, and starts a
    # recheck loop. Default off — a no-op unless health.startup_behavior_gate_enabled.
    behavior_gate_task = None
    gate_permits_pi_daemon = True
    if background_services_enabled:
        from aletheia.agent_runtime import startup_behavior_gate as behavior_gate

        behavior_gate.set_uvicorn_loop(asyncio.get_running_loop())
        # Fast local verdict decides whether the System Operator daemon may start
        # at all — "pi_daemon must not start before the gate passes".
        try:
            gate_permits_pi_daemon = behavior_gate.gate_allows_startup(cfg)
        except Exception as e:
            logger.error("startup behaviour gate pre-check raised — blocking PI daemon: %s", e)
            gate_permits_pi_daemon = False
        behavior_gate_task = asyncio.create_task(behavior_gate.startup_behavior_gate(cfg), name="startup_behavior_gate")

    # System Operator event-driven daemon — wakes immediately on signal arrival.
    # Gated by the Startup Behavior Gate: it must not start while the gate blocks
    # (the recheck loop restarts it on resume).
    pi_daemon_task = None
    if background_services_enabled and cfg.pi_daemon.enabled and gate_permits_pi_daemon:
        try:
            from aletheia.agent_runtime.pi_daemon import start_pi_daemon

            pi_daemon_task = start_pi_daemon(
                loop=asyncio.get_running_loop(),
                debounce_sec=cfg.pi_daemon.debounce_sec,
                cooldown_sec=cfg.pi_daemon.cooldown_sec,
                heartbeat_sec=cfg.pi_daemon.heartbeat_sec,
            )
        except Exception as e:
            logger.warning("PI daemon failed to start: %s", e, exc_info=True)

    telegram_bot = None
    telegram_task = None
    if background_services_enabled:
        from aletheia.telegram.bot import TelegramBot

        telegram_bot = TelegramBot()
        if telegram_bot.configure():
            if telegram_bot.allowed_chat_ids:
                try:
                    telegram_task = asyncio.create_task(telegram_bot.run(), name="telegram_polling")
                except Exception as e:
                    logger.warning("Telegram polling startup failed: %s", e)
                    telegram_task = None
            else:
                logger.warning("Telegram enabled but no allowed_chat_ids configured; skipping Telegram polling.")
    app.state.telegram_bot = telegram_bot
    app.state.telegram_task = telegram_task
    # Mount static files AFTER scheduler init — by this point Vite watch (if running)
    # has had time to build dashboard_static/. Mounting at module-top hits a race:
    # uvicorn loads app.py before Vite finishes its first build, so index.html is
    # missing and the mount never happens. Deferred mount fixes the issue.
    _mount_static_dashboard()
    from aletheia.server.responsiveness import event_loop_lag_monitor

    event_loop_lag_task = asyncio.create_task(
        event_loop_lag_monitor.run(),
        name="event_loop_lag_monitor",
    )
    _lifespan_tasks.clear()
    _lifespan_tasks.update(
        {
            "llm_startup_gate": llm_gate_task,
            "startup_exchange_sync": startup_sync_task,
            "startup_exchange_gate": exchange_gate_task,
            "startup_behavior_gate": behavior_gate_task,
            "pi_daemon": pi_daemon_task,
            "telegram_polling": telegram_task,
            "event_loop_lag_monitor": event_loop_lag_task,
        }
    )
    _log_startup_stage("pre_yield_services", stage_started, startup_started)
    try:
        yield
    finally:
        # Stop producers before consumers so shutdown cannot spawn fresh recovery work.
        if background_services_enabled:
            try:
                stop_scheduler()
            except Exception:
                pass
            await asyncio.to_thread(agent_job_manager.stop)

        await _cancel_background_task(llm_gate_task, "LLM startup gate")
        await _cancel_background_task(event_loop_lag_task, "event-loop lag monitor")
        await _cancel_background_task(exchange_gate_task, "exchange startup gate")
        await _cancel_background_task(startup_sync_task, "startup exchange sync")
        await _cancel_background_task(behavior_gate_task, "startup behaviour gate")
        # The behaviour gate can spawn a module-owned recheck loop after pausing;
        # stop it explicitly (same pattern as the LLM/exchange auto-resume loops).
        try:
            from aletheia.agent_runtime.startup_behavior_gate import stop_startup_gate_recheck

            await stop_startup_gate_recheck()
        except Exception as exc:
            logger.debug("startup gate recheck shutdown failed: %s", exc)

        # Startup gates can create independent recovery loops after pausing. They are
        # module-owned rather than direct lifespan children, so stop them explicitly.
        try:
            from aletheia.agent_runtime.auto_resume import stop_auto_resume_task

            await stop_auto_resume_task()
        except Exception as exc:
            logger.debug("LLM auto-resume shutdown failed: %s", exc)
        try:
            from aletheia.agent_runtime.connectivity import stop_exchange_auto_resume_task

            await stop_exchange_auto_resume_task()
        except Exception as exc:
            logger.debug("exchange auto-resume shutdown failed: %s", exc)

        try:
            from aletheia.model_provider.broker import inference_broker

            await inference_broker.aclose()
        except Exception as exc:
            logger.debug("inference broker shutdown failed: %s", exc)

        if telegram_task is not None and telegram_bot is not None:
            try:
                telegram_bot.stop()
                await _cancel_background_task(telegram_task, "Telegram polling")
            except Exception:
                pass
        if pi_daemon_task is not None:
            try:
                from aletheia.agent_runtime.pi_daemon import stop_pi_daemon

                stop_pi_daemon()
                await _cancel_background_task(pi_daemon_task, "PI daemon")
            except Exception:
                pass
        _lifespan_tasks.clear()
        # Mark the Runtime Session ended cleanly. Reaching this finally means an
        # orderly lifespan exit; a hard kill skips it, leaving ended_at NULL +
        # shutdown_clean=0 so a crash stays distinguishable from a clean stop.
        if background_services_enabled:
            try:
                from aletheia.agent_runtime.runtime_session import end_runtime_session

                end_runtime_session(clean=True)
            except Exception as e:
                logger.debug("Runtime session end failed: %s", e)
        # Only clean PID file if we wrote it (bare uvicorn / foreground mode).
        # In supervised mode the PID belongs to the supervisor — don't touch it.
        if getattr(app.state, "_owns_pid", False):
            from aletheia.gateway.pidfile import clear_pid

            clear_pid()
        logger.info("Aletheia shutting down")


app = FastAPI(title="Aletheia Trading OS", version=__version__, lifespan=lifespan)


# #3 fix: FastAPI's default RequestValidationError handler echoes the rejected
# input value in the 422 body (under "input": <raw_value>). With extra="forbid"
# on settings models, an attacker/fuzzer can probe for field names and see their
# payloads reflected verbatim — XSS-safe (JSON) but still unnecessary information
# exposure. Strip the raw `input` from each error entry and keep only field
# location + message.
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


@app.exception_handler(RequestValidationError)
async def _sanitized_validation_error(_request, exc: RequestValidationError):
    safe_errors = []
    for err in exc.errors():
        safe_errors.append(
            {
                "loc": err.get("loc"),
                "msg": err.get("msg"),
                "type": err.get("type"),
            }
        )
    return JSONResponse(status_code=422, content={"detail": safe_errors})


_cfg_for_cors = load_config()
_bind_host = _cfg_for_cors.app.bind_host
_loopback_origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8787",
    "http://127.0.0.1:8787",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _bind_host not in {"127.0.0.1", "localhost", "::1"} else _loopback_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Trust-boundary auth: when tunnel is enabled, ALL /api/* and /_infra/* routes
# require owner authentication by default.  Only the minimal liveness/bootstrap
# paths in _PUBLIC_PATHS may be reached anonymously.
#
# _PROTECTED_PATHS, _PROTECTED_PATH_PREFIXES, _PROTECTED_MUTATION_PATHS, and
# _PROTECTED_METHOD_PREFIXES document the prior allowlist-based guard that named
# only specific trust-boundary operations.  Issue #398 replaced it with a
# default-deny policy; these constants are retained because spec tests
# (spec_api_reference.py) verify that the critical paths they list are declared,
# which remains true: under default-deny they are protected implicitly, and their
# explicit presence here serves as a checklist that they cannot accidentally be
# moved to _PUBLIC_PATHS.
_PROTECTED_PATHS = {
    "/api/settings/live-unlock",
    "/api/settings/live-lock",
    "/api/risk/kill-switch",
    # #137/#42: these config mutations are trust-boundary (they can disable the
    # kill switch, widen risk limits, flip demo->live, or elevate the agent
    # permission mode), so they must require the tunnel token too — otherwise the
    # protection on /api/risk/kill-switch is trivially bypassed via /settings/risk.
    "/api/settings/risk",
    "/api/settings/exchange-mode",
    "/api/settings/permission-mode",
    "/api/settings/learning",
    "/api/settings/autonomous-learning",
    "/api/gateway/restart",
    "/api/gateway/stop",
    # #42 completion: operator command surfaces can trigger the AI to execute trades
    # (even under permission-gate #40, the gate only checks the configured mode —
    # if mode is PaperAuto, an unauthenticated tunnel caller can inject commands).
    "/api/operator/command",
    "/api/operator/command-preview",
    "/api/operator/pause",
    "/api/operator/resume",
    "/api/operator/wake",
    "/api/operator/loop/trigger",
    # Capital events (deposit/withdraw) alter the capital baseline used by drawdown
    # and kill-switch calculations — fabricating one lowers the safety net.
    "/api/capital/events",
    # #404: Local Paper account cash mutations and bulk-close controls alter the
    # authoritative paper capital baseline and can force-close real paper positions.
    # Unauthenticated access would allow fabricating deposits, draining capital,
    # or contaminating Learning/attribution evidence without owner authentication.
    "/api/settings/paper-account/credit",
    "/api/settings/paper-account/debit",
    "/api/settings/paper-account/set-balance",
    "/api/settings/paper-account/close-all",
    "/api/settings/paper-account/close-spot",
    # #395: Manual Agent-run endpoint can consume LLM budget, create durable artifacts,
    # and fill the bounded queue — treat it as an owner-authenticated mutation.
    "/api/agent-runs/start",
}

# #41 fix: whole path PREFIXES that must require the tunnel token when the tunnel
# is enabled. The operator-shell internal writer endpoints (`/api/operator/internal/*`)
# had no auth guard at all — a tunneled caller could write heartbeats, tasks,
# messages, events, artifacts, and repair decisions impersonating the operator.
# These are LEGITIMATE for the local TypeScript bridge (loopback), so we don't
# strictly forbid them; we require the same tunnel token the trust-boundary
# routes require when public exposure is on.
_PROTECTED_PATH_PREFIXES = ("/api/operator/internal/",)

_PROTECTED_MUTATION_PATHS = (
    ("/api/settings/learning/experiments/", "/admit-trial"),
    ("/api/persistence/lessons/", "/accept"),
    ("/api/persistence/lessons/", "/reject"),
    ("/api/persistence/lessons/", "/archive"),
)

# Declarative method+prefix guards: any request whose HTTP method is in the
# set AND whose path starts with the prefix requires tunnel-owner auth.
# Adding a new mutation under a covered prefix is automatically protected;
# new prefixes must be declared here explicitly.
_PROTECTED_METHOD_PREFIXES: tuple[tuple[frozenset[str], str], ...] = (
    # All owner-facing Proposal mutations (approve, reject, and any future
    # state-changing routes under /api/proposals/) must be authenticated.
    (frozenset({"POST", "PUT", "PATCH", "DELETE"}), "/api/proposals/"),
    # Manual position close. A POST to /api/positions/{id}/close executes a real
    # exchange order (demo) or a local close (paper) on behalf of the owner.
    # Unauthenticated tunnel callers must not be able to force-close positions.
    (frozenset({"POST"}), "/api/positions/"),
)

# Minimal anonymous surface when tunnel is active.  Every /api/* and /_infra/*
# path not listed here requires owner authentication.  Keep this list as small
# as possible: liveness probes only, no config/positions/diagnostics content.
_PUBLIC_PATHS = frozenset(
    {
        "/api/ready",
        "/_infra/livez",
        "/_infra/readyz",
    }
)


@app.middleware("http")
async def _tunnel_auth_guard(request, call_next):
    """Enforce owner authentication for all private API/infra routes when tunnel is active.

    Policy (tunnel.enabled=True or remote_control.require_token=True):
      - Paths in _PUBLIC_PATHS are always accessible anonymously (minimal liveness).
      - Every other /api/* and /_infra/* route requires a valid X-Aletheia-Token header,
        including all GET/read endpoints. Read access exposes positions, strategy
        reasoning, agent output, and research data that are as sensitive as mutations.
      - A missing remote_control_api_token (or legacy tunnel_api_token) secret
        fails closed with 401; unauthenticated callers cannot reach a private route.
    Policy (Remote Control enabled without require_token):
      - Private-network API access remains unauthenticated for MVP convenience.
    Policy (both modes disabled):
      - No authentication required; local-first frictionless access is preserved.

    Successful authentication sets both request-state flags so existing audited
    routes continue to attribute authenticated owner actions correctly.
    """
    path = request.url.path
    _is_private = (path.startswith("/api/") or path.startswith("/_infra/")) and path not in _PUBLIC_PATHS

    if _is_private:
        # persist_migrations=False: the auth guard is read-only; it must not
        # trigger a config rewrite as a side effect of checking tunnel.enabled.
        cfg = load_config(persist_migrations=False)
        tunnel_enabled = bool(getattr(getattr(cfg, "tunnel", None), "enabled", False))
        remote_control = getattr(cfg, "remote_control", None)
        remote_token_required = bool(
            getattr(remote_control, "enabled", False) and getattr(remote_control, "require_token", False)
        )
        if tunnel_enabled or remote_token_required:
            from aletheia.config.secrets import get_secret
            from fastapi.responses import JSONResponse

            # remote_control_api_token is the independent name for private-network
            # API access. The legacy tunnel name remains valid for existing installs.
            expected = get_secret("remote_control_api_token") or get_secret("tunnel_api_token")
            if not expected:
                return JSONResponse(
                    {
                        "detail": "Remote Control is enabled but no API token is configured. "
                        "Save a token in Settings or run "
                        "'aletheia secrets set remote_control_api_token <value>'."
                    },
                    status_code=401,
                )
            import hmac as _hmac

            token = request.headers.get("x-aletheia-token", "")
            if not _hmac.compare_digest(token, expected):
                return JSONResponse(
                    {"detail": "Unauthorized: X-Aletheia-Token required when Remote Control is active"},
                    status_code=401,
                )
            # Existing route audit code checks tunnel_authenticated. Keep that
            # compatibility flag while exposing the clearer remote-control fact.
            request.state.tunnel_authenticated = True
            request.state.remote_control_authenticated = True
    return await call_next(request)


def _diagnostic_env_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _slow_request_threshold_ms() -> float:
    try:
        return max(float(os.environ.get("ALETHEIA_SLOW_REQUEST_MS", "500")), 0.0)
    except ValueError:
        return 500.0


@app.middleware("http")
async def _request_timing(request, call_next):
    """Expose API wall time and log slow requests without logging user input."""
    path = request.url.path
    if not path.startswith(("/api/", "/_infra/")):
        return await call_next(request)

    started = time.perf_counter()
    response = None
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        duration_ms = (time.perf_counter() - started) * 1000.0
        if response is not None:
            app_timing = f"app;dur={duration_ms:.1f}"
            existing = response.headers.get("Server-Timing")
            response.headers["Server-Timing"] = f"{existing}, {app_timing}" if existing else app_timing

        http_logger = logging.getLogger("aletheia.http")
        log_args = (request.method, path, status_code, duration_ms)
        if duration_ms >= _slow_request_threshold_ms():
            http_logger.warning(
                "http_request_slow method=%s path=%s status=%d duration_ms=%.1f",
                *log_args,
            )
        elif _diagnostic_env_enabled("ALETHEIA_HTTP_TIMING"):
            http_logger.info(
                "http_request method=%s path=%s status=%d duration_ms=%.1f",
                *log_args,
            )


app.include_router(health_router)
app.include_router(infra_router)
app.include_router(settings_router)
app.include_router(data_router)
app.include_router(live_router)
app.include_router(model_router)
app.include_router(bitget_router)
app.include_router(risk_router)
app.include_router(update_router)
app.include_router(gateway_router)
app.include_router(telegram_router)
app.include_router(news_router)
app.include_router(command_router)
app.include_router(operator_router)
app.include_router(runtime_router)
app.include_router(persistence_router)
app.include_router(prediction_router)
app.include_router(universe_router)
app.include_router(analytics_router)
app.include_router(capital_router)
app.include_router(economics_router)
app.include_router(export_router)
app.include_router(ws_router, prefix="/api")

# Dev-reload routes — only mounted when ALETHEIA_DEV_RELOAD=1 and live is disabled.
from aletheia.server.dev_reload import is_dev_reload_enabled

if is_dev_reload_enabled():
    from aletheia.server.routes.dev_reload import router as dev_reload_router

    app.include_router(dev_reload_router)

# Static dashboard is mounted during lifespan startup (see _mount_static_dashboard)
