"""Model provider configuration API routes.

Secrets are write-only over the API: POST accepts an api_key, but no GET ever
returns a key. GET responses only report whether a key is configured + last-4.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from aletheia.config.loader import load_config, save_config
from aletheia.config.secrets import set_secret, get_secret
from aletheia.files.jsonl import write_audit
from aletheia.model_provider.router import clear_cache, healthcheck_all

router = APIRouter(prefix="/api", tags=["model"])

VALID_PROVIDERS = {"openai_compatible", "anthropic_compatible", "codex_cli"}


def _key_status(secret_ref: str | None) -> dict:
    """Return {configured, last4} for a secret ref — never the raw key."""
    if not secret_ref:
        return {"configured": False, "last4": None}
    val = get_secret(secret_ref)
    if not val:
        return {"configured": False, "last4": None}
    return {"configured": True, "last4": val[-4:] if len(val) >= 4 else "****"}


@router.get("/model")
async def get_model_config():
    """Return provider config + key-configured status (NEVER the raw key)."""
    cfg = load_config()
    providers = {}
    for name, pcfg in cfg.models.providers.items():
        providers[name] = {
            "base_url": pcfg.base_url,
            "protocol": pcfg.protocol,
            "model": pcfg.model,
            "enabled": pcfg.enabled,
            "command": pcfg.command,
            "mode": pcfg.mode,
            "codex_model": pcfg.codex_model,
            "api_key": _key_status(pcfg.api_key_secret_ref),
        }
    return {"default_provider": cfg.models.default_provider, "providers": providers}


@router.get("/model/health")
async def get_model_health():
    results = await healthcheck_all()
    return [
        {"provider": h.provider, "available": h.available, "model": h.model,
         "error": h.error, "models": h.models}
        for h in results
    ]


# --- Codex subscription provider: web-triggered device-auth login + model listing ---

@router.post("/model/codex/login-start")
async def codex_login_start():
    """Begin Codex device-auth login. Returns verification URL + code (never a token)."""
    from aletheia.model_provider import codex_auth
    result = await codex_auth.start_login()
    write_audit({
        "actor": "human", "source": "dashboard",
        "event_type": "codex_login_started", "entity_type": "model_provider",
        "entity_id": "codex_cli",
        "message": f"Codex device-auth login start (state={result.get('state')})",
    })
    return result


@router.get("/model/codex/login-status")
async def codex_login_status():
    """Poll Codex login status (authoritative via `codex login status`). No token."""
    from aletheia.model_provider import codex_auth
    return await codex_auth.poll_status()


@router.post("/model/codex/login-cancel")
async def codex_login_cancel():
    from aletheia.model_provider import codex_auth
    return codex_auth.cancel()


@router.get("/model/codex/models")
async def codex_models():
    """List available Codex models ([] if codex unavailable). Read-only."""
    from aletheia.model_provider import router as router_mod
    try:
        provider = router_mod.get_provider("codex_cli")
        return await provider.list_models()
    except Exception:
        return []


class ModelConfigRequest(BaseModel):
    provider: str
    base_url: str | None = None
    protocol: str | None = None
    model: str | None = None
    enabled: bool | None = None
    command: str | None = None
    mode: str | None = None
    codex_model: str | None = None
    default: bool | None = None
    api_key: str | None = None  # write-only; stored as secret, never returned


@router.post("/settings/model")
async def set_model(req: ModelConfigRequest):
    if req.provider not in VALID_PROVIDERS:
        raise HTTPException(400, f"Invalid provider. Choose from: {sorted(VALID_PROVIDERS)}")

    cfg = load_config()
    pcfg = cfg.models.providers.get(req.provider)
    if pcfg is None:
        # initialize an empty provider entry
        from aletheia.config.schema import ProviderConfig
        pcfg = ProviderConfig()
        cfg.models.providers[req.provider] = pcfg

    if req.base_url is not None:
        pcfg.base_url = req.base_url
    if req.protocol is not None:
        pcfg.protocol = req.protocol
    if req.model is not None:
        pcfg.model = req.model
    if req.enabled is not None:
        pcfg.enabled = req.enabled
    if req.command is not None:
        pcfg.command = req.command
    if req.mode is not None:
        pcfg.mode = req.mode
    if req.codex_model is not None:
        pcfg.codex_model = req.codex_model
    if req.default:
        cfg.models.default_provider = req.provider

    # Store API key as a secret, record only the reference in config
    if req.api_key:
        secret_ref = f"model.{req.provider}.api_key"
        set_secret(secret_ref, req.api_key)
        pcfg.api_key_secret_ref = secret_ref

    save_config(cfg)
    clear_cache()

    write_audit({
        "actor": "human",
        "source": "dashboard",
        "event_type": "model_provider_changed",
        "entity_type": "model_provider",
        "entity_id": req.provider,
        "message": f"Model provider '{req.provider}' updated"
                   + (" (api_key set)" if req.api_key else ""),
    })

    return {"provider": req.provider, "default_provider": cfg.models.default_provider,
            "api_key": _key_status(pcfg.api_key_secret_ref)}
