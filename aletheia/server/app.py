"""FastAPI application for Aletheia Trading OS."""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from aletheia import __version__

app = FastAPI(title="Aletheia Trading OS", version=__version__)


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": __version__}


@app.get("/api/status")
async def status():
    from aletheia.config.loader import load_config
    cfg = load_config()
    from aletheia.gateway.pidfile import is_running
    return {
        "gateway_running": is_running(),
        "paused": cfg.runtime.paused,
        "exchange": cfg.exchange.name,
        "exchange_mode": cfg.exchange.mode,
        "live_state": cfg.live.state,
        "permission_mode": cfg.agent.permission_mode,
    }


# Serve static dashboard if the directory exists
_static_dir = Path(__file__).resolve().parent.parent / "dashboard_static"
if _static_dir.exists() and (_static_dir / "index.html").exists():
    app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="dashboard")
