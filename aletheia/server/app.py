"""FastAPI application for Aletheia Trading OS."""

import logging
import asyncio
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from aletheia import __version__
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

logger = logging.getLogger("aletheia")


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
        logger.warning("LLM startup gate probe errored (skipping gate): %s", e)
        return

    if result["any_usable"]:
        # Clear any stale failure count so /api/health starts clean.
        from aletheia.agent_runtime.health import record_llm_success
        record_llm_success()
        return

    # No provider could complete a real inference call — pause the loop.
    # Pause the live scheduler too (it is already running at this point, having
    # been started before this background task), not just the persisted flag —
    # otherwise jobs keep firing for the gap until each one re-reads the flag.
    try:
        from aletheia.agent_runtime.scheduler import pause_scheduler
        pause_scheduler()
    except Exception:
        pass
    # Read-modify-write a FRESH on-disk config rather than the boot snapshot, so
    # we only flip `paused` and don't clobber any edit made during the probe
    # window (e.g. an operator changing settings via the dashboard, now reachable
    # because the port is already serving). Full elimination of the last-writer
    # race would need a file lock — tracked in docs/BACKEND_ISSUES.md.
    from aletheia.config.loader import load_config as _load_config, save_config
    fresh = _load_config()
    fresh.runtime.paused = True
    save_config(fresh)

    from aletheia.agent_runtime.health import record_llm_failure
    for _ in range(max(1, cfg.health.llm_unavailable_alert_after)):
        record_llm_failure()

    errors = "; ".join(
        f"{r['provider']}={r['error']}" for r in result["results"]
    ) or "no enabled providers"
    logger.warning(
        "LLM startup gate: NO usable provider (%d checked) — trading loop PAUSED. "
        "Fix a provider and resume from the dashboard. Details: %s",
        result["checked"], errors,
    )
    try:
        from aletheia.files.jsonl import write_audit
        attempts = result.get("attempts", 1)
        write_audit({
            "actor": "system", "source": "startup_gate",
            "event_type": "llm_startup_gate_paused",
            "entity_type": "gateway", "entity_id": "gateway",
            "message": (
                f"No usable LLM provider at startup ({result['checked']} checked, "
                f"{attempts} attempts); trading loop paused. {errors}"
            ),
        })
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = bootstrap()
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
    logger.info("Aletheia Trading OS v%s starting", __version__)
    logger.info("Dashboard: http://%s:%d", cfg.app.bind_host, runtime_context["port"] or cfg.app.dashboard_port)
    # Ensure DB schema + register agents/tools, then start the scheduler.
    from aletheia.storage.migrations import run_migrations
    from aletheia.agent_runtime.tool_registry import init_tool_registry
    from aletheia.agent_runtime.scheduler import init_scheduler, stop_scheduler
    run_migrations()
    # Backfill FTS5 index so the experience loop has full coverage on every boot.
    try:
        from aletheia.knowledge.retrieval import reindex_all
        fts_count = reindex_all()
        if fts_count:
            logger.info("FTS reindex: %d knowledge items indexed", fts_count)
    except Exception as e:
        logger.warning("FTS reindex failed (non-fatal): %s", e)
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
    init_tool_registry()
    if background_services_enabled:
        try:
            init_scheduler()
        except Exception as e:
            logger.warning("Scheduler failed to start: %s", e)
        try:
            from aletheia.portfolio.exchange_sync import sync_all_exchanges
            sync_all_exchanges()
            logger.info("startup exchange sync complete")
        except Exception as e:
            logger.warning("startup exchange sync failed (non-fatal): %s", e)
    else:
        logger.info("Runtime mode %s: background services disabled", runtime_context["runtime_mode"])
    # Fail-loud LLM gate, run as a BACKGROUND task (not awaited here): if no
    # provider can actually do inference it pauses the scheduler and persists
    # runtime.paused. Deferring it until AFTER init_scheduler + yield means the
    # HTTP port binds immediately, so the spawn-verifier/watchdog see a live port
    # and the dashboard is reachable WHILE the probe runs. Booting un-paused for
    # the few seconds the probe takes is safe: scheduler jobs themselves re-check
    # cfg.runtime.paused, and the only time-sensitive job (market_data) is
    # harmless to run once. See _llm_startup_gate.
    llm_gate_task = asyncio.create_task(_llm_startup_gate(cfg)) if background_services_enabled else None

    # PI Agent event-driven daemon — wakes immediately on signal arrival
    pi_daemon_task = None
    if background_services_enabled and cfg.pi_daemon.enabled:
        try:
            from aletheia.agent_runtime.pi_daemon import start_pi_daemon
            pi_daemon_task = start_pi_daemon(
                loop=asyncio.get_running_loop(),
                debounce_sec=cfg.pi_daemon.debounce_sec,
                cooldown_sec=cfg.pi_daemon.cooldown_sec,
                heartbeat_sec=cfg.pi_daemon.heartbeat_sec,
            )
        except Exception as e:
            logger.warning("PI daemon failed to start: %s", e)

    telegram_bot = None
    telegram_task = None
    if background_services_enabled:
        from aletheia.telegram.bot import TelegramBot
        telegram_bot = TelegramBot()
        if telegram_bot.configure():
            if telegram_bot.allowed_chat_ids:
                try:
                    telegram_task = asyncio.create_task(telegram_bot.run())
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
    yield
    # Cancel the LLM gate if it's still probing at shutdown (slow/hung provider).
    if llm_gate_task is not None and not llm_gate_task.done():
        llm_gate_task.cancel()
        try:
            await llm_gate_task
        except (asyncio.CancelledError, Exception):
            pass
    if telegram_task is not None and telegram_bot is not None:
        try:
            telegram_bot.stop()
            telegram_task.cancel()
            try:
                await telegram_task
            except asyncio.CancelledError:
                pass
        except Exception:
            pass
    if pi_daemon_task is not None and not pi_daemon_task.done():
        try:
            from aletheia.agent_runtime.pi_daemon import stop_pi_daemon
            stop_pi_daemon()
            try:
                await pi_daemon_task
            except (asyncio.CancelledError, Exception):
                pass
        except Exception:
            pass
    if background_services_enabled:
        try:
            stop_scheduler()
        except Exception:
            pass
    # Only clean PID file if we wrote it (bare uvicorn / foreground mode).
    # In supervised mode the PID belongs to the supervisor — don't touch it.
    if getattr(app.state, "_owns_pid", False):
        from aletheia.gateway.pidfile import clear_pid
        clear_pid()
    logger.info("Aletheia shutting down")


app = FastAPI(title="Aletheia Trading OS", version=__version__, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173",
                   "http://localhost:8787", "http://127.0.0.1:8787"],
    allow_methods=["*"],
    allow_headers=["*"],
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

# Dev-reload routes — only mounted when ALETHEIA_DEV_RELOAD=1 and live is disabled.
from aletheia.server.dev_reload import is_dev_reload_enabled
if is_dev_reload_enabled():
    from aletheia.server.routes.dev_reload import router as dev_reload_router
    app.include_router(dev_reload_router)

# Static dashboard is mounted during lifespan startup (see _mount_static_dashboard)
