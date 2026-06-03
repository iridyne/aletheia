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
            "api_key": _key_status(pcfg.api_key_secret_ref),
        }
    return {"default_provider": cfg.models.default_provider, "providers": providers}


@router.get("/model/health")
async def get_model_health():
    results = await healthcheck_all()
    return [
        {"provider": h.provider, "available": h.available, "model": h.model, "error": h.error}
        for h in results
    ]


class ModelConfigRequest(BaseModel):
    provider: str
    base_url: str | None = None
    protocol: str | None = None
    model: str | None = None
    enabled: bool | None = None
    command: str | None = None
    mode: str | None = None
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
