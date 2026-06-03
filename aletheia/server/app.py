"""FastAPI application for Aletheia Trading OS."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from aletheia import __version__
from aletheia.config.loader import bootstrap
from aletheia.server.routes.health import router as health_router
from aletheia.server.routes.settings import router as settings_router
from aletheia.server.routes.data import router as data_router

logger = logging.getLogger("aletheia")


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = bootstrap()
    logger.info("Aletheia Trading OS v%s starting", __version__)
    logger.info("Dashboard: http://%s:%d", cfg.app.bind_host, cfg.app.dashboard_port)
    yield
    logger.info("Aletheia shutting down")


app = FastAPI(title="Aletheia Trading OS", version=__version__, lifespan=lifespan)

app.include_router(health_router)
app.include_router(settings_router)
app.include_router(data_router)

# Serve static dashboard if the directory exists
_static_dir = Path(__file__).resolve().parent.parent / "dashboard_static"
if _static_dir.exists() and (_static_dir / "index.html").exists():
    app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="dashboard")
