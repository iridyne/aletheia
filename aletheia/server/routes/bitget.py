"""Bitget connection + credentials API routes.

Credentials are write-only over the API: POST accepts api_key/secret/passphrase,
GET only reports configured + last-4. Raw secrets are never returned.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from aletheia.config.loader import load_config
from aletheia.config.secrets import set_secret, get_secret
from aletheia.files.jsonl import write_audit
from aletheia.exchanges.bitget.client import BitgetClient

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
