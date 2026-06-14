"""Model provider configuration API routes.

Secrets are write-only over the API: POST accepts an api_key, but no GET ever
returns a key. GET responses only report whether a key is configured + last-4.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from aletheia.config.loader import load_config, save_config
from aletheia.config.secrets import set_secret, get_secret
from aletheia.files.jsonl import write_audit
from aletheia.model_provider.router import clear_cache, healthcheck_all, PROVIDER_REGISTRY

router = APIRouter(prefix="/api", tags=["model"])

VALID_PROVIDERS = set(PROVIDER_REGISTRY.keys())

HTTP_PROVIDERS = {"openai_compatible", "anthropic_compatible", "anthropic_native", "openai_native"}
CLI_PROVIDERS = {"codex_cli", "claude_cli"}


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
        ptype = pcfg.type or name
        entry: dict = {
            "type": ptype,
            "enabled": pcfg.enabled,
            "model": pcfg.model,
            "api_key": _key_status(pcfg.api_key_secret_ref),
            "auth_type": pcfg.auth_type,
            "oauth": _key_status(pcfg.oauth_token_ref),
        }
        if ptype in HTTP_PROVIDERS:
            entry["base_url"] = pcfg.base_url
            entry["protocol"] = pcfg.protocol
        if ptype == "anthropic_compatible":
            entry["auth_scheme"] = getattr(pcfg, "auth_scheme", "x-api-key")
        if ptype in CLI_PROVIDERS:
            entry["command"] = pcfg.command
            entry["mode"] = pcfg.mode
            entry["codex_model"] = pcfg.codex_model
        providers[name] = entry
    return {"default_provider": cfg.models.default_provider, "providers": providers}


@router.get("/model/health")
async def get_model_health():
    results = await healthcheck_all()
    return [
        {"provider": h.provider, "available": h.available, "model": h.model,
         "error": h.error, "models": h.models,
         "model_metadata": h.model_metadata}
        for h in results
    ]


@router.get("/model/provider-types")
async def get_provider_types():
    """Return schema metadata for each provider type (used by frontend for dynamic forms)."""
    return {
        "openai_compatible": {
            "label": "OpenAI Compatible",
            "category": "http",
            "fields": ["base_url", "model", "protocol", "api_key"],
        },
        "anthropic_compatible": {
            "label": "Anthropic Compatible",
            "category": "http",
            "fields": ["base_url", "model", "api_key"],
        },
        "codex_cli": {
            "label": "Codex CLI",
            "category": "cli",
            "fields": ["command", "codex_model"],
        },
        "claude_cli": {
            "label": "Claude Code CLI",
            "category": "cli",
            "fields": ["command", "model"],
        },
        "anthropic_native": {
            "label": "Claude OAuth (Experimental)",
            "category": "http",
            "fields": ["model", "api_key", "oauth"],
        },
        "openai_native": {
            "label": "OpenAI OAuth (Experimental)",
            "category": "http",
            "fields": ["model", "api_key", "oauth"],
        },
    }


class DetectModelsRequest(BaseModel):
    provider: str
    base_url: str | None = None
    api_key: str | None = None
    auth_scheme: str | None = None


@router.post("/model/detect-models")
async def detect_models(req: DetectModelsRequest):
    """Probe a candidate provider config and return available models without
    persisting anything. The api_key is used for the probe only."""
    if req.provider not in VALID_PROVIDERS:
        raise HTTPException(400, f"Invalid provider. Choose from: {sorted(VALID_PROVIDERS)}")
    if req.provider in CLI_PROVIDERS:
        raise HTTPException(400, f"{req.provider} uses CLI introspection, not HTTP detection")

    from aletheia.model_provider.openai_compatible import OpenAICompatibleProvider
    from aletheia.model_provider.anthropic_compatible import AnthropicCompatibleProvider
    from aletheia.model_provider.anthropic_native import AnthropicNativeProvider
    from aletheia.model_provider.openai_native import OpenAINativeProvider
    from aletheia.config.secrets import get_secret
    from aletheia.config.loader import load_config

    cfg = load_config()
    pcfg = cfg.models.providers.get(req.provider)
    stored_key = None
    if pcfg and pcfg.api_key_secret_ref:
        stored_key = get_secret(pcfg.api_key_secret_ref)
    api_key = req.api_key or stored_key

    if req.provider == "openai_compatible":
        provider = OpenAICompatibleProvider(
            base_url=req.base_url or (pcfg.base_url if pcfg else "") or "",
            model="default", api_key=api_key,
        )
    elif req.provider == "anthropic_compatible":
        base_url = req.base_url or (pcfg.base_url if pcfg else "") or ""
        auth_scheme = req.auth_scheme or (getattr(pcfg, "auth_scheme", "x-api-key") if pcfg else "x-api-key")
        provider = AnthropicCompatibleProvider(
            base_url=base_url, model="default", api_key=api_key, auth_scheme=auth_scheme,
        )
    elif req.provider == "anthropic_native":
        provider = AnthropicNativeProvider(model="default", api_key=api_key)
    elif req.provider == "openai_native":
        provider = OpenAINativeProvider(model="default", api_key=api_key)
    else:
        raise HTTPException(400, f"No detect-models support for {req.provider}")

    models = await provider.list_models()
    return {"models": models}


class TestCandidateRequest(BaseModel):
    provider: str
    base_url: str | None = None
    protocol: str | None = None
    model: str | None = None
    api_key: str | None = None
    auth_scheme: str | None = None
    probe: bool = False


@router.post("/model/test-candidate")
async def test_model_candidate(req: TestCandidateRequest):
    """Verify-before-commit probe: health-check a CANDIDATE provider config without
    persisting anything. The candidate api_key is used for the live check only and is
    never stored, logged, or echoed back."""
    if req.provider not in VALID_PROVIDERS:
        raise HTTPException(400, f"Invalid provider. Choose from: {sorted(VALID_PROVIDERS)}")
    if req.provider in CLI_PROVIDERS:
        raise HTTPException(400, f"{req.provider} uses local auth, not a candidate key")

    from aletheia.model_provider.openai_compatible import OpenAICompatibleProvider
    from aletheia.model_provider.anthropic_compatible import AnthropicCompatibleProvider
    from aletheia.model_provider.anthropic_native import AnthropicNativeProvider
    from aletheia.model_provider.openai_native import OpenAINativeProvider
    from aletheia.config.secrets import get_secret
    from aletheia.config.loader import load_config

    model = req.model or "default"

    cfg = load_config()
    pcfg = cfg.models.providers.get(req.provider)
    stored_key = None
    if pcfg and pcfg.api_key_secret_ref:
        stored_key = get_secret(pcfg.api_key_secret_ref)
    api_key = req.api_key or stored_key

    if req.provider == "openai_compatible":
        provider = OpenAICompatibleProvider(
            base_url=req.base_url or (pcfg.base_url if pcfg else "") or "",
            model=model,
            protocol=req.protocol or (pcfg.protocol if pcfg else "chat_completions"),
            api_key=api_key,
        )
    elif req.provider == "anthropic_compatible":
        auth_scheme = req.auth_scheme or (getattr(pcfg, "auth_scheme", "x-api-key") if pcfg else "x-api-key")
        provider = AnthropicCompatibleProvider(
            base_url=req.base_url or (pcfg.base_url if pcfg else "") or "",
            model=model,
            api_key=api_key,
            auth_scheme=auth_scheme,
        )
    elif req.provider == "anthropic_native":
        provider = AnthropicNativeProvider(model=model, api_key=api_key)
    elif req.provider == "openai_native":
        provider = OpenAINativeProvider(model=model, api_key=api_key)
    else:
        raise HTTPException(400, f"No test-candidate support for {req.provider}")

    health = await provider.healthcheck()
    if not health.available:
        return {"available": False, "model": health.model, "error": health.error}

    if req.probe:
        from aletheia.model_provider.base import ModelRequest
        try:
            resp = await provider.complete(ModelRequest(
                messages=[{"role": "user", "content": "Say OK"}],
                max_tokens=10, temperature=0,
            ))
            if not resp.content.strip():
                return {"available": False, "model": health.model, "error": "inference returned empty response"}
        except Exception as e:
            return {"available": False, "model": health.model, "error": f"inference failed: {e}"}

    return {"available": True, "model": health.model, "error": None, "models": health.models}


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
    workspace_url: str | None = None
    default: bool | None = None
    api_key: str | None = None
    auth_scheme: str | None = None


@router.post("/settings/model")
async def set_model(req: ModelConfigRequest):
    if req.provider not in VALID_PROVIDERS:
        raise HTTPException(400, f"Invalid provider. Choose from: {sorted(VALID_PROVIDERS)}")

    cfg = load_config()
    pcfg = cfg.models.providers.get(req.provider)
    if pcfg is None:
        from aletheia.config.schema import ProviderConfig
        pcfg = ProviderConfig(type=req.provider)
        cfg.models.providers[req.provider] = pcfg

    if not pcfg.type:
        pcfg.type = req.provider

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
    if req.workspace_url is not None:
        pcfg.workspace_url = req.workspace_url
    if req.auth_scheme is not None:
        pcfg.auth_scheme = req.auth_scheme
    if req.default:
        cfg.models.default_provider = req.provider

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


# --- OpenAI subscription OAuth ---

@router.post("/model/openai-oauth/start")
async def openai_oauth_start():
    """Begin OpenAI OAuth PKCE flow. Returns {auth_url} for frontend to open."""
    from aletheia.model_provider import openai_oauth
    result = await openai_oauth.start_login()
    return result


@router.get("/model/openai-oauth/status")
async def openai_oauth_status():
    """Poll OpenAI OAuth login status. No tokens exposed, no side-effects."""
    from aletheia.model_provider import openai_oauth
    return openai_oauth.get_status()


@router.post("/model/openai-oauth/finalize")
async def openai_oauth_finalize():
    """Persist completed OpenAI OAuth tokens. Idempotent — safe to call multiple times."""
    from aletheia.model_provider import openai_oauth
    from aletheia.config.secrets import get_secret

    status = openai_oauth.get_status()
    if status["state"] != "completed":
        raise HTTPException(400, "OAuth flow not completed yet")

    cfg = load_config()
    pcfg = cfg.models.providers.get("openai_native")
    if pcfg and pcfg.oauth_token_ref and get_secret(pcfg.oauth_token_ref):
        return {"persisted": False, "reason": "already_persisted"}

    tokens = openai_oauth.get_tokens()
    if not tokens:
        raise HTTPException(400, "No tokens available from completed flow")

    _persist_openai_oauth(tokens)
    write_audit({
        "actor": "human", "source": "dashboard",
        "event_type": "openai_oauth_finalized", "entity_type": "model_provider",
        "entity_id": "openai_native",
        "message": "OpenAI OAuth tokens persisted to config",
    })
    return {"persisted": True}


@router.post("/model/openai-oauth/cancel")
async def openai_oauth_cancel():
    from aletheia.model_provider import openai_oauth
    return await openai_oauth.cancel()


@router.post("/model/openai-oauth/disconnect")
async def openai_oauth_disconnect():
    """Disconnect OpenAI OAuth — clear tokens, revert to api_key auth."""
    from aletheia.config.secrets import set_secret
    cfg = load_config()
    pcfg = cfg.models.providers.get("openai_native")
    if not pcfg:
        raise HTTPException(400, "openai_native provider not configured")

    if pcfg.oauth_token_ref:
        set_secret(pcfg.oauth_token_ref, "")
        pcfg.oauth_token_ref = None
    if pcfg.oauth_refresh_ref:
        set_secret(pcfg.oauth_refresh_ref, "")
        pcfg.oauth_refresh_ref = None
    pcfg.oauth_expires_at = None
    pcfg.auth_type = "api_key"

    save_config(cfg)
    clear_cache()

    write_audit({
        "actor": "human", "source": "dashboard",
        "event_type": "oauth_disconnected", "entity_type": "model_provider",
        "entity_id": "openai_native",
        "message": "OpenAI OAuth disconnected, reverted to api_key auth",
    })
    return {"disconnected": True}


def _persist_openai_oauth(tokens: dict) -> None:
    """Save OpenAI OAuth tokens to secrets + update config."""
    cfg = load_config()
    pcfg = cfg.models.providers.get("openai_native")
    if pcfg is None:
        from aletheia.config.schema import ProviderConfig
        pcfg = ProviderConfig(type="openai_native")
        cfg.models.providers["openai_native"] = pcfg

    pcfg.auth_type = "oauth"
    pcfg.enabled = True

    if not pcfg.model or pcfg.model == "default":
        pcfg.model = "gpt-4o"

    if tokens.get("api_key"):
        ref = "model.openai_native.oauth_token"
        set_secret(ref, tokens["api_key"])
        pcfg.oauth_token_ref = ref

    if tokens.get("refresh_token"):
        ref = "model.openai_native.oauth_refresh"
        set_secret(ref, tokens["refresh_token"])
        pcfg.oauth_refresh_ref = ref

    if tokens.get("expires_at"):
        pcfg.oauth_expires_at = tokens["expires_at"]

    save_config(cfg)
    clear_cache()


# --- Claude subscription OAuth ---

@router.post("/model/claude-oauth/start")
async def claude_oauth_start():
    """Begin Claude OAuth PKCE flow. Returns {auth_url} for frontend to open."""
    from aletheia.model_provider import claude_oauth
    result = await claude_oauth.start_login()
    return result


class ClaudeOAuthCompleteRequest(BaseModel):
    code: str


@router.post("/model/claude-oauth/complete")
async def claude_oauth_complete(req: ClaudeOAuthCompleteRequest):
    """Complete Claude OAuth by exchanging user-provided code."""
    from aletheia.model_provider import claude_oauth
    result = await claude_oauth.complete_login(req.code)
    # If completed, persist tokens
    if result.get("state") == "completed":
        tokens = claude_oauth.get_tokens()
        if tokens:
            _persist_claude_oauth(tokens)
    return result


@router.get("/model/claude-oauth/status")
async def claude_oauth_status():
    """Poll Claude OAuth login status."""
    from aletheia.model_provider import claude_oauth
    return claude_oauth.get_status()


@router.post("/model/claude-oauth/cancel")
async def claude_oauth_cancel():
    from aletheia.model_provider import claude_oauth
    return claude_oauth.cancel()


@router.post("/model/claude-oauth/disconnect")
async def claude_oauth_disconnect():
    """Disconnect Claude OAuth — clear tokens, revert to api_key auth."""
    from aletheia.config.secrets import set_secret
    cfg = load_config()
    pcfg = cfg.models.providers.get("anthropic_native")
    if not pcfg:
        raise HTTPException(400, "anthropic_native provider not configured")

    if pcfg.oauth_token_ref:
        set_secret(pcfg.oauth_token_ref, "")
        pcfg.oauth_token_ref = None
    if pcfg.oauth_refresh_ref:
        set_secret(pcfg.oauth_refresh_ref, "")
        pcfg.oauth_refresh_ref = None
    pcfg.oauth_expires_at = None
    pcfg.auth_type = "api_key"

    save_config(cfg)
    clear_cache()

    write_audit({
        "actor": "human", "source": "dashboard",
        "event_type": "oauth_disconnected", "entity_type": "model_provider",
        "entity_id": "anthropic_native",
        "message": "Claude OAuth disconnected, reverted to api_key auth",
    })
    return {"disconnected": True}


def _persist_claude_oauth(tokens: dict) -> None:
    """Save Claude OAuth tokens to secrets + update config."""
    cfg = load_config()
    pcfg = cfg.models.providers.get("anthropic_native")
    if pcfg is None:
        from aletheia.config.schema import ProviderConfig
        pcfg = ProviderConfig(type="anthropic_native")
        cfg.models.providers["anthropic_native"] = pcfg

    pcfg.auth_type = "oauth"
    pcfg.enabled = True

    if not pcfg.model or pcfg.model == "default":
        pcfg.model = "claude-sonnet-4-6"

    if tokens.get("access_token"):
        ref = "model.anthropic_native.oauth_token"
        set_secret(ref, tokens["access_token"])
        pcfg.oauth_token_ref = ref

    if tokens.get("refresh_token"):
        ref = "model.anthropic_native.oauth_refresh"
        set_secret(ref, tokens["refresh_token"])
        pcfg.oauth_refresh_ref = ref

    if tokens.get("expires_at"):
        pcfg.oauth_expires_at = tokens["expires_at"]

    save_config(cfg)
    clear_cache()
