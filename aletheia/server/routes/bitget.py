"""Exchange connection + credentials API routes (Bitget + Binance Testnet + OKX).

Credentials are write-only over the API: POST accepts api_key/secret,
GET only reports configured + last-4. Raw secrets are never returned.
"""

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from aletheia.config.loader import load_config
from aletheia.config.secrets import set_secret, get_secret
from aletheia.files.jsonl import write_audit
from aletheia.exchanges.bitget.auth import build_headers
from aletheia.exchanges.bitget.client import BitgetClient, BASE_URL

router = APIRouter(prefix="/api", tags=["bitget"])


def _cred_status(profile: str) -> dict:
    """Report whether each Bitget credential field is configured (no raw values)."""
    out = {}
    for field in ("api_key", "api_secret", "passphrase"):
        val = get_secret(f"bitget.{profile}.{field}")
        out[field] = {
            "configured": bool(val),
            "last4": (val[-4:] if val and len(val) >= 4 else None),
        }
    return out


@router.get("/bitget/credentials-status")
async def credentials_status():
    return {"demo": _cred_status("demo"), "live": _cred_status("live")}


class ExchangeCredentialsRequest(BaseModel):
    profile: str  # "demo" | "live"
    api_key: str | None = None
    api_secret: str | None = None
    passphrase: str | None = None


@router.post("/settings/exchange-credentials")
async def set_exchange_credentials(req: ExchangeCredentialsRequest):
    if req.profile not in ("demo", "live"):
        raise HTTPException(400, "profile must be 'demo' or 'live'")

    fields_set = []
    for field, value in (("api_key", req.api_key), ("api_secret", req.api_secret), ("passphrase", req.passphrase)):
        if value:
            set_secret(f"bitget.{req.profile}.{field}", value)
            fields_set.append(field)

    if not fields_set:
        raise HTTPException(400, "No credential fields provided")

    write_audit({
        "actor": "human",
        "source": "dashboard",
        "event_type": "exchange_credentials_changed",
        "entity_type": "bitget_credentials",
        "entity_id": req.profile,
        "message": f"Bitget {req.profile} credentials updated: {', '.join(fields_set)}",
    })

    return {"profile": req.profile, "updated": fields_set, "status": _cred_status(req.profile)}


class TestConnectionRequest(BaseModel):
    mode: str | None = None  # optional override; defaults to configured exchange.mode


@router.post("/bitget/test-connection")
async def test_connection(req: TestConnectionRequest | None = None):
    cfg = load_config()
    mode = (req.mode if req and req.mode else cfg.exchange.mode)
    client = BitgetClient(mode=mode)
    result = await client.test_connection()
    return result


class TestCandidateCredsRequest(BaseModel):
    profile: str  # "demo" | "live"
    api_key: str
    api_secret: str
    passphrase: str


@router.post("/bitget/test-candidate")
async def test_candidate_credentials(req: TestCandidateCredsRequest):
    """Verify-before-commit probe: test CANDIDATE Bitget credentials live without
    persisting them. Credentials are used to sign one read-only account-info request
    and are never stored, logged, or echoed back. No audit (nothing is persisted)."""
    if req.profile not in ("demo", "live"):
        raise HTTPException(400, "profile must be 'demo' or 'live'")

    path = "/api/v2/spot/account/info"
    demo = req.profile == "demo"
    try:
        from aletheia.net import httpx_client
        headers = build_headers(req.api_key, req.api_secret, req.passphrase, "GET", path, "", demo=demo)
        async with httpx_client(timeout=15) as client:
            resp = await client.get(f"{BASE_URL}{path}", headers=headers)
            data = resp.json()
        if data.get("code") == "00000":
            return {"connected": True, "profile": req.profile}
        return {"connected": False, "profile": req.profile, "error": data.get("msg", "unknown")}
    except Exception as e:
        err_msg = type(e).__name__
        if "timeout" in str(e).lower():
            err_msg = "connection_timeout"
        elif "connect" in str(e).lower():
            err_msg = "connection_failed"
        return {"connected": False, "profile": req.profile, "error": err_msg}


# ─── Binance credentials ───


def _binance_cred_status() -> dict:
    out = {}
    for profile in ("testnet", "live"):
        profile_out = {}
        for field in ("api_key", "api_secret"):
            val = get_secret(f"binance.{profile}.{field}")
            profile_out[field] = {
                "configured": bool(val),
                "last4": (val[-4:] if val and len(val) >= 4 else None),
            }
        out[profile] = profile_out
    return out


@router.get("/binance/credentials-status")
async def binance_credentials_status():
    return _binance_cred_status()


# Legacy endpoint — keep for backward compat during transition
@router.get("/binance-testnet/credentials-status")
async def binance_testnet_credentials_status():
    full = _binance_cred_status()
    return full["testnet"]


class BinanceCredentialsRequest(BaseModel):
    profile: str = "testnet"
    api_key: str | None = None
    api_secret: str | None = None


@router.post("/settings/binance-credentials")
async def set_binance_credentials(req: BinanceCredentialsRequest):
    if req.profile not in ("testnet", "live"):
        raise HTTPException(400, "profile must be 'testnet' or 'live'")

    fields_set = []
    for field, value in (("api_key", req.api_key), ("api_secret", req.api_secret)):
        if value:
            set_secret(f"binance.{req.profile}.{field}", value)
            fields_set.append(field)

    if not fields_set:
        raise HTTPException(400, "No credential fields provided")

    write_audit({
        "actor": "human",
        "source": "dashboard",
        "event_type": "exchange_credentials_changed",
        "entity_type": "binance_credentials",
        "entity_id": req.profile,
        "message": f"Binance {req.profile} credentials updated: {', '.join(fields_set)}",
    })

    return {"updated": fields_set, "status": _binance_cred_status()}


# Legacy endpoint
@router.post("/settings/binance-testnet-credentials")
async def set_binance_testnet_credentials_legacy(req: BinanceCredentialsRequest):
    req.profile = "testnet"
    return await set_binance_credentials(req)


class BinanceTestRequest(BaseModel):
    mode: str = "testnet"


@router.post("/binance/test-connection")
async def binance_test_connection(req: BinanceTestRequest = BinanceTestRequest()):
    """Test Binance Futures connectivity with stored credentials."""
    if req.mode not in ("testnet", "live"):
        raise HTTPException(400, "mode must be 'testnet' or 'live'")
    try:
        from aletheia.exchanges.binance.client import BinanceClient
        client = BinanceClient(mode=req.mode)
        positions = await client.get_positions()
        await client.close()
        return {"connected": True, "mode": req.mode, "positions_count": len(positions)}
    except Exception as e:
        err_msg = str(e)
        if "not configured" in err_msg.lower():
            return {"connected": False, "error": "credentials_not_configured"}
        if "timeout" in err_msg.lower():
            return {"connected": False, "error": "connection_timeout"}
        # error-leak 2.4: never echo raw str(e) — a CCXT/exchange error can carry the
        # request (incl. candidate api_key/secret) or internal URLs. The categorised
        # cases above cover the actionable ones; fall back to the exception class name.
        return {"connected": False, "error": type(e).__name__}


# Legacy endpoint
@router.post("/binance-testnet/test-connection")
async def binance_testnet_test_connection():
    return await binance_test_connection(BinanceTestRequest(mode="testnet"))


class BinanceCandidateRequest(BaseModel):
    mode: str = "testnet"
    api_key: str
    api_secret: str


@router.post("/binance/test-candidate")
async def binance_test_candidate(req: BinanceCandidateRequest):
    """Test Binance connectivity with candidate (unsaved) credentials."""
    if req.mode not in ("testnet", "live"):
        raise HTTPException(400, "mode must be 'testnet' or 'live'")
    try:
        import ccxt.async_support as ccxt
        exchange = ccxt.binanceusdm({
            "apiKey": req.api_key,
            "secret": req.api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "future"},
        })
        if req.mode == "testnet":
            exchange.set_sandbox_mode(True)
        await exchange.fetch_time()
        await exchange.close()
        return {"connected": True, "mode": req.mode}
    except Exception as e:
        err_msg = str(e)
        if "timeout" in err_msg.lower():
            return {"connected": False, "error": "connection_timeout"}
        # error-leak 2.4: never echo raw str(e) — a CCXT/exchange error can carry the
        # request (incl. candidate api_key/secret) or internal URLs. The categorised
        # cases above cover the actionable ones; fall back to the exception class name.
        return {"connected": False, "error": type(e).__name__}


# ─── OKX credentials ───


def _okx_cred_status() -> dict:
    out = {}
    for profile in ("demo", "live"):
        profile_out = {}
        for field in ("api_key", "api_secret", "passphrase"):
            val = get_secret(f"okx.{profile}.{field}")
            profile_out[field] = {
                "configured": bool(val),
                "last4": (val[-4:] if val and len(val) >= 4 else None),
            }
        out[profile] = profile_out
    return out


@router.get("/okx/credentials-status")
async def okx_credentials_status():
    return _okx_cred_status()


class OkxCredentialsRequest(BaseModel):
    profile: str = "demo"
    api_key: str | None = None
    api_secret: str | None = None
    passphrase: str | None = None


@router.post("/settings/okx-credentials")
async def set_okx_credentials(req: OkxCredentialsRequest):
    if req.profile not in ("demo", "live"):
        raise HTTPException(400, "profile must be 'demo' or 'live'")

    fields_set = []
    for field, value in (("api_key", req.api_key), ("api_secret", req.api_secret), ("passphrase", req.passphrase)):
        if value:
            set_secret(f"okx.{req.profile}.{field}", value)
            fields_set.append(field)

    if not fields_set:
        raise HTTPException(400, "No credential fields provided")

    write_audit({
        "actor": "human",
        "source": "dashboard",
        "event_type": "exchange_credentials_changed",
        "entity_type": "okx_credentials",
        "entity_id": req.profile,
        "message": f"OKX {req.profile} credentials updated: {', '.join(fields_set)}",
    })

    return {"updated": fields_set, "status": _okx_cred_status()}


class OkxTestRequest(BaseModel):
    mode: str = "demo"


@router.post("/okx/test-connection")
async def okx_test_connection(req: OkxTestRequest = OkxTestRequest()):
    """Test OKX connectivity with stored credentials."""
    if req.mode not in ("demo", "live"):
        raise HTTPException(400, "mode must be 'demo' or 'live'")
    try:
        from aletheia.exchanges.okx.client import OKXClient
        client = OKXClient(mode=req.mode)
        await client.fetch_time()
        await client.close()
        return {"connected": True, "mode": req.mode}
    except Exception as e:
        err_msg = str(e)
        if "not configured" in err_msg.lower():
            return {"connected": False, "error": "credentials_not_configured"}
        if "timeout" in err_msg.lower():
            return {"connected": False, "error": "connection_timeout"}
        # error-leak 2.4: never echo raw str(e) — a CCXT/exchange error can carry the
        # request (incl. candidate api_key/secret) or internal URLs. The categorised
        # cases above cover the actionable ones; fall back to the exception class name.
        return {"connected": False, "error": type(e).__name__}


class OkxCandidateRequest(BaseModel):
    mode: str = "demo"
    api_key: str
    api_secret: str
    passphrase: str


@router.post("/okx/test-candidate")
async def okx_test_candidate(req: OkxCandidateRequest):
    """Test OKX connectivity with candidate (unsaved) credentials."""
    if req.mode not in ("demo", "live"):
        raise HTTPException(400, "mode must be 'demo' or 'live'")
    try:
        import ccxt.async_support as ccxt
        from aletheia.net import ccxt_timeout_options
        exchange = ccxt.okx({
            **ccxt_timeout_options(),
            "apiKey": req.api_key,
            "secret": req.api_secret,
            "password": req.passphrase,
            "enableRateLimit": True,
        })
        if req.mode == "demo":
            exchange.set_sandbox_mode(True)
        await exchange.fetch_time()
        await exchange.close()
        return {"connected": True, "mode": req.mode}
    except Exception as e:
        err_msg = str(e)
        if "timeout" in err_msg.lower():
            return {"connected": False, "error": "connection_timeout"}
        # error-leak 2.4: never echo raw str(e) — a CCXT/exchange error can carry the
        # request (incl. candidate api_key/secret) or internal URLs. The categorised
        # cases above cover the actionable ones; fall back to the exception class name.
        return {"connected": False, "error": type(e).__name__}


@router.get("/uta/state")
async def uta_state():
    """Return the cached unified (multi-broker) account state.

    Fast path: reads the in-memory cache written by sync_all_exchanges().
    Falls back to per-broker Bitget cache when the unified cache is empty
    (first run before the scheduler has fired).
    Never blocks on a network call.
    """
    from aletheia.portfolio.exchange_sync import (
        get_unified_cached_state, get_cached_state, status_for
    )

    unified = get_unified_cached_state()
    if unified is not None:
        age = unified.get("age_seconds")
        sync_status = "ok" if (age is not None and age <= 600) else "stale"
        return {**unified, "sync_status": sync_status}

    # Fallback: wrap the Bitget-only cache in the unified shape
    bitget = get_cached_state()
    if bitget is not None:
        sync_status = status_for(bitget)
        return {
            "total_equity_usdt": bitget.get("equity_usdt", 0),
            "total_available_usdt": bitget.get("available_usdt", 0),
            "total_unrealized_pnl": bitget.get("futures_unrealized_pnl", 0),
            "accounts": [{
                "broker_id": "bitget_demo",
                "display_name": "Bitget Demo",
                "equity_usdt": bitget.get("equity_usdt", 0),
                "available_usdt": bitget.get("available_usdt", 0),
                "frozen_usdt": bitget.get("frozen_usdt", 0),
                "unrealized_pnl": bitget.get("futures_unrealized_pnl", 0),
                "asset_breakdown": bitget.get("asset_breakdown", []),
            }],
            "positions": bitget.get("positions", []),
            "errors": [],
            "fetched_at": bitget.get("fetched_at", ""),
            "sync_status": sync_status,
        }

    return {
        "total_equity_usdt": 0,
        "total_available_usdt": 0,
        "total_unrealized_pnl": 0,
        "accounts": [],
        "positions": [],
        "errors": [],
        "fetched_at": None,
        "sync_status": "unconfigured",
    }
