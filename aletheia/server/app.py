"""FastAPI application for Aletheia Trading OS."""

import logging
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
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

logger = logging.getLogger("aletheia")


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = bootstrap()
    logger.info("Aletheia Trading OS v%s starting", __version__)
    logger.info("Dashboard: http://%s:%d", cfg.app.bind_host, cfg.app.dashboard_port)
    # Ensure DB schema + register agents/tools, then start the scheduler.
    from aletheia.storage.migrations import run_migrations
    from aletheia.agent_runtime.tool_registry import init_tool_registry
    from aletheia.agent_runtime.scheduler import init_scheduler, stop_scheduler
    from aletheia.telegram.bot import TelegramBot
    run_migrations()
    init_tool_registry()
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
    logger.info("Aletheia shutting down")


app = FastAPI(title="Aletheia Trading OS", version=__version__, lifespan=lifespan)

app.include_router(health_router)
app.include_router(settings_router)
app.include_router(data_router)
app.include_router(live_router)
app.include_router(model_router)
app.include_router(bitget_router)
app.include_router(risk_router)
app.include_router(update_router)
app.include_router(gateway_router)

# Serve static dashboard if the directory exists
_static_dir = Path(__file__).resolve().parent.parent / "dashboard_static"
if _static_dir.exists() and (_static_dir / "index.html").exists():
    app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="dashboard")
