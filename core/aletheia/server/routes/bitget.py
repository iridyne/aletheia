"""Configured-account connectivity and cached UTA state; no raw credentials."""

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from aletheia.config.loader import load_config
from aletheia.uta.manager import probe_account_reachable

router = APIRouter(prefix="/api", tags=["bitget"])


class TestConnectionRequest(BaseModel):
    mode: str | None = None  # optional override; defaults to configured exchange.mode


@router.post("/bitget/test-connection")
async def test_connection(req: TestConnectionRequest | None = None):
    cfg = load_config()
    mode = (req.mode if req and req.mode else cfg.exchange.mode)
    if mode not in ("demo", "live"):
        raise HTTPException(400, "mode must be 'demo' or 'live'")
    execution_mode = "bitget_demo" if mode == "demo" else "bitget_live_limited"
    result = await asyncio.to_thread(probe_account_reachable, execution_mode, cfg=cfg)
    return {**result, "mode": mode}


class BinanceTestRequest(BaseModel):
    mode: str = "testnet"


@router.post("/binance/test-connection")
async def binance_test_connection(req: BinanceTestRequest = BinanceTestRequest()):
    """Test Binance Futures connectivity with stored credentials."""
    if req.mode not in ("testnet", "live"):
        raise HTTPException(400, "mode must be 'testnet' or 'live'")
    execution_mode = "binance_testnet" if req.mode == "testnet" else "binance_live_limited"
    result = await asyncio.to_thread(probe_account_reachable, execution_mode)
    return {**result, "mode": req.mode}


class OkxTestRequest(BaseModel):
    mode: str = "demo"


@router.post("/okx/test-connection")
async def okx_test_connection(req: OkxTestRequest = OkxTestRequest()):
    """Test OKX connectivity with stored credentials."""
    if req.mode not in ("demo", "live"):
        raise HTTPException(400, "mode must be 'demo' or 'live'")
    execution_mode = "okx_demo" if req.mode == "demo" else "okx_live_limited"
    result = await asyncio.to_thread(probe_account_reachable, execution_mode)
    return {**result, "mode": req.mode}


@router.get("/uta/state")
async def uta_state():
    """Return the cached UTA projection without polling private accounts."""
    from aletheia.portfolio.exchange_sync import get_unified_cached_state, status_for

    unified = await asyncio.to_thread(get_unified_cached_state)
    if unified is not None:
        sync_status = status_for(unified)
        return {**unified, "sync_status": sync_status}

    return {
        "total_equity_usdt": None,
        "total_available_usdt": None,
        "total_unrealized_pnl": None,
        "accounts": [],
        "positions": [],
        "errors": [],
        "connected_brokers": [],
        "fetched_at": None,
        "sync_status": "unconfigured",
        "sync_complete": False,
    }
