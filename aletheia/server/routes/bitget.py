"""Bitget connection + credentials API routes.

Credentials are write-only over the API: POST accepts api_key/secret/passphrase,
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
        headers = build_headers(req.api_key, req.api_secret, req.passphrase, "GET", path, "", demo=demo)
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{BASE_URL}{path}", headers=headers)
            data = resp.json()
        if data.get("code") == "00000":
            return {"connected": True, "profile": req.profile}
        return {"connected": False, "profile": req.profile, "error": data.get("msg", "unknown")}
    except Exception as e:
        return {"connected": False, "profile": req.profile, "error": str(e)}
