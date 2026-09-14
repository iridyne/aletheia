"""Scoped operator credential ingress for the installed Exchange adapters.

Existing HTTP paths, credential fields, Live-lock checks and audit are unchanged.
Core receives only sanitized status and configured-account UTA reports.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from aletheia.config.loader import load_config
from aletheia.config.secrets import get_secret, set_secret
from aletheia.exchanges.bitget.auth import probe_candidate_credentials
from aletheia.files.jsonl import write_audit
from aletheia.trust.live_unlock import active_live_profile_conflict

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
    if active_live_profile_conflict(load_config(), "bitget", req.profile):
        raise HTTPException(409, "Cannot change the selected live credentials while live is unlocked")

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

    return await probe_candidate_credentials(
        req.api_key, req.api_secret, req.passphrase, req.profile
    )


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


class BinanceCredentialsRequest(BaseModel):
    profile: str = "testnet"
    api_key: str | None = None
    api_secret: str | None = None


@router.post("/settings/binance-credentials")
async def set_binance_credentials(req: BinanceCredentialsRequest):
    if req.profile not in ("testnet", "live"):
        raise HTTPException(400, "profile must be 'testnet' or 'live'")
    if active_live_profile_conflict(load_config(), "binance", req.profile):
        raise HTTPException(409, "Cannot change the selected live credentials while live is unlocked")

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


class BinanceCandidateRequest(BaseModel):
    mode: str = "testnet"
    api_key: str
    api_secret: str


@router.post("/binance/test-candidate")
async def binance_test_candidate(req: BinanceCandidateRequest):
    """Verify candidate Binance credentials with a private read-only request."""
    if req.mode not in ("testnet", "live"):
        raise HTTPException(400, "mode must be 'testnet' or 'live'")
    try:
        from aletheia.net import close_ccxt, make_ccxt
        exchange, session = make_ccxt(
            "binanceusdm",
            apiKey=req.api_key,
            secret=req.api_secret,
            options={"defaultType": "future"},
        )
        try:
            if req.mode == "testnet":
                exchange.set_sandbox_mode(True)
            await exchange.fetch_balance()
        finally:
            if not await close_ccxt(exchange, session):
                raise RuntimeError("credential_probe_cleanup_failed")
        return {"connected": True, "mode": req.mode}
    except Exception as e:
        err_msg = str(e)
        if "timeout" in err_msg.lower():
            return {"connected": False, "error": "connection_timeout"}
        # error-leak 2.4: never echo raw str(e) — a CCXT/exchange error can carry the
        # request (incl. candidate api_key/secret) or internal URLs. The categorised
        # cases above cover the actionable ones; fall back to the exception class name.
        return {"connected": False, "error": type(e).__name__}


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
    if active_live_profile_conflict(load_config(), "okx", req.profile):
        raise HTTPException(409, "Cannot change the selected live credentials while live is unlocked")

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


class OkxCandidateRequest(BaseModel):
    mode: str = "demo"
    api_key: str
    api_secret: str
    passphrase: str


@router.post("/okx/test-candidate")
async def okx_test_candidate(req: OkxCandidateRequest):
    """Verify candidate OKX credentials with a private read-only request."""
    if req.mode not in ("demo", "live"):
        raise HTTPException(400, "mode must be 'demo' or 'live'")
    try:
        from aletheia.net import close_ccxt, make_ccxt
        exchange, session = make_ccxt(
            "okx",
            apiKey=req.api_key,
            secret=req.api_secret,
            password=req.passphrase,
        )
        try:
            if req.mode == "demo":
                exchange.set_sandbox_mode(True)
            await exchange.fetch_balance()
        finally:
            if not await close_ccxt(exchange, session):
                raise RuntimeError("credential_probe_cleanup_failed")
        return {"connected": True, "mode": req.mode}
    except Exception as e:
        err_msg = str(e)
        if "timeout" in err_msg.lower():
            return {"connected": False, "error": "connection_timeout"}
        # error-leak 2.4: never echo raw str(e) — a CCXT/exchange error can carry the
        # request (incl. candidate api_key/secret) or internal URLs. The categorised
        # cases above cover the actionable ones; fall back to the exception class name.
        return {"connected": False, "error": type(e).__name__}
