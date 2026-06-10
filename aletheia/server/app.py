"""FastAPI application for Aletheia Trading OS."""

import logging
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from aletheia import __version__
from aletheia.config.loader import bootstrap
from aletheia.server.routes.health import router as health_router
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

logger = logging.getLogger("aletheia")


async def _llm_startup_gate(cfg) -> None:
    """Probe LLM providers at boot; pause the trading loop if none can do inference.

    Fail-loud, but keep the process alive: setting runtime.paused stops every
    scheduler job (they no-op on cfg.runtime.paused) and persists so the Web UI
    shows the degraded state. We feed record_llm_failure() so /api/health reports
    `llm_unavailable:N` with no extra plumbing, and audit the decision so it is
    never silent. The process is NOT killed — that would just trigger a
    supervisor/watchdog respawn loop. Operator fixes a provider and hits
    POST /api/gateway/resume. Never raises (degrades to a logged warning)."""
    if not cfg.health.require_llm_at_startup:
        return
    try:
        from aletheia.agent_runtime.startup_gate import probe_llm_providers_at_startup
        result = await probe_llm_providers_at_startup(cfg)
    except Exception as e:
        logger.warning("LLM startup gate probe errored (skipping gate): %s", e)
        return

    if result["any_usable"]:
        # Clear any stale failure count so /api/health starts clean.
        from aletheia.agent_runtime.health import record_llm_success
        record_llm_success()
        return

    # No provider could complete a real inference call — pause the loop.
    from aletheia.config.loader import save_config
    cfg.runtime.paused = True
    save_config(cfg)

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
        write_audit({
            "actor": "system", "source": "startup_gate",
            "event_type": "llm_startup_gate_paused",
            "entity_type": "gateway", "entity_id": "gateway",
            "message": (
                f"No usable LLM provider at startup ({result['checked']} checked); "
                f"trading loop paused. {errors}"
            ),
        })
    except Exception:
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = bootstrap()
    # Ensure PID file exists regardless of how we were launched (background
    # supervisor, foreground CLI, or bare uvicorn). Without this, /api/status
    # and /api/gateway/restart break when the supervisor PID file is missing.
    from aletheia.gateway.pidfile import write_pid, read_pid
    _owns_pid = read_pid() is None
    if _owns_pid:
        write_pid()
    app.state._owns_pid = _owns_pid
    # Record the source commit this process booted on, BEFORE any scheduler
    # thread starts — lets /api/health flag "code updated, restart needed".
    from aletheia.agent_runtime.build_info import record_boot_commit
    record_boot_commit()
    logger.info("Aletheia Trading OS v%s starting", __version__)
    logger.info("Dashboard: http://%s:%d", cfg.app.bind_host, cfg.app.dashboard_port)
    # Ensure DB schema + register agents/tools, then start the scheduler.
    from aletheia.storage.migrations import run_migrations
    from aletheia.agent_runtime.tool_registry import init_tool_registry
    from aletheia.agent_runtime.scheduler import init_scheduler, stop_scheduler
    from aletheia.telegram.bot import TelegramBot
    run_migrations()
    init_tool_registry()
    # Fail-loud LLM gate: if no provider can actually do inference, pause the
    # trading loop BEFORE the scheduler starts (it then boots paused via the
    # existing runtime.paused path) rather than running blind and producing
    # neutral-only opportunities while looking alive.
    await _llm_startup_gate(cfg)
    try:
        init_scheduler()
    except Exception as e:
        logger.warning("Scheduler failed to start: %s", e)
    telegram_bot = TelegramBot()
    telegram_task = None
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
    yield
    if telegram_task is not None:
        try:
            telegram_bot.stop()
            telegram_task.cancel()
            try:
                await telegram_task
            except asyncio.CancelledError:
                pass
        except Exception:
            pass
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

# Serve static dashboard if the directory exists
_static_dir = Path(__file__).resolve().parent.parent / "dashboard_static"
if _static_dir.exists() and (_static_dir / "index.html").exists():

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
