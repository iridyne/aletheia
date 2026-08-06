"""Model provider configuration API routes.

Secrets are write-only over the API: POST accepts an api_key, but no GET ever
returns a key. GET responses only report whether a key is configured + last-4.
"""

import asyncio
import hashlib

from fastapi import APIRouter, HTTPException, Response  # pyright: ignore[reportMissingImports]
from pydantic import BaseModel, Field

from aletheia.config.loader import load_config, save_config
from aletheia.config.secrets import delete_secret, get_secret, set_secret
from aletheia.files.jsonl import write_audit
from aletheia.model_provider.databricks import DATABRICKS_PROTOCOLS, validate_workspace_url
from aletheia.model_provider.registry import (  # pyright: ignore[reportMissingImports]
    API_PROVIDER_TYPES,
    CLI_PROVIDER_TYPES,
    PROVIDER_TYPES,
    provider_kind,
)
from aletheia.model_provider.router import (
    clear_cache,
    clear_cache_async,
    healthcheck_all,
    PROVIDER_REGISTRY,
)

router = APIRouter(prefix="/api", tags=["model"])

VALID_PROVIDERS = set(PROVIDER_REGISTRY.keys())

HTTP_PROVIDERS = {"openai_compatible", "anthropic_compatible", "databricks", "anthropic_native", "openai_native"}
CLI_PROVIDERS = set(CLI_PROVIDER_TYPES)


async def _close_candidate_provider(provider) -> None:
    """Close every loop-owned pool used by an ephemeral candidate probe."""

    from aletheia.model_provider.broker import inference_broker

    if inference_broker.running:
        await inference_broker.run_auxiliary(provider.aclose)
    await provider.aclose()


def _key_status(secret_ref: str | None) -> dict:
    """Return {configured, last4} for a secret ref — never the raw key."""
    if not secret_ref:
        return {"configured": False, "last4": None}
    val = get_secret(secret_ref)
    if not val:
        return {"configured": False, "last4": None}
    return {"configured": True, "last4": val[-4:] if len(val) >= 4 else "****"}


@router.get("/model")
def get_model_config():
    """Return provider config + key-configured status (NEVER the raw key)."""
    cfg = load_config()
    providers = {}
    for name, pcfg in cfg.models.providers.items():
        ptype = pcfg.type or name
        definition = PROVIDER_TYPES.get(ptype)
        entry: dict = {
            "type": ptype,
            "kind": provider_kind(ptype),
            "display_name": pcfg.display_name or (definition.label if definition else name),
            "enabled": pcfg.enabled,
            "model": pcfg.model,
            "model_fallback_enabled": getattr(pcfg, "model_fallback_enabled", True),
            "fallback_models": list(getattr(pcfg, "fallback_models", []) or []),
            "api_key": _key_status(pcfg.api_key_secret_ref),
            "auth_type": pcfg.auth_type,
            "oauth": _key_status(pcfg.oauth_token_ref),
            "inference_max_concurrency": pcfg.inference_max_concurrency,
            "inference_requests_per_minute": pcfg.inference_requests_per_minute,
            "inference_tpm": pcfg.inference_tokens_per_minute,
            "inference_queue_capacity": pcfg.inference_queue_capacity,
            "inference_reserved_high_priority_slots": pcfg.inference_reserved_high_priority_slots,
            "inference_timeout_sec": pcfg.inference_timeout_sec,
        }
        if ptype in HTTP_PROVIDERS:
            entry["base_url"] = pcfg.base_url
            entry["protocol"] = pcfg.protocol
        if ptype == "databricks":
            entry["workspace_url"] = pcfg.workspace_url
        if ptype == "anthropic_compatible":
            entry["auth_scheme"] = getattr(pcfg, "auth_scheme", "x-api-key")
        if ptype in CLI_PROVIDERS:
            entry["command"] = pcfg.command
            entry["mode"] = pcfg.mode
            entry["codex_model"] = pcfg.codex_model
        providers[name] = entry
    return {
        "default_provider": cfg.models.default_provider,
        "deep_provider": cfg.models.deep_provider,
        "fast_provider": cfg.models.fast_provider,
        "providers": providers,
    }


@router.get("/model/health")
async def get_model_health():
    results = await healthcheck_all()
    return [
        {
            "provider": h.provider,
            "available": h.available,
            "model": h.model,
            "error": h.error,
            "models": h.models,
            "model_metadata": h.model_metadata,
        }
        for h in results
    ]


@router.get("/model/broker")
async def get_model_broker():
    """Sanitized process-local inference admission and queue-wait metrics."""

    from aletheia.model_provider.broker import inference_broker

    return await inference_broker.snapshot()


@router.post("/model/ping")
async def ping_model():
    """Instant inference test on the current default provider.

    Sends 'Say OK' with max_tokens=10. Returns latency, model name, and
    the actual response text. Never raises — errors are in the response body.
    """
    import time
    from aletheia.model_provider.router import get_provider_async
    from aletheia.model_provider.base import ModelRequest
    from aletheia.model_provider.inference_contract import build_inference_call
    from aletheia.economics.inference_calls import (
        record_inference_finished,
        record_inference_started,
    )
    from aletheia.model_provider.broker import (
        InferencePriority,
        brokered_complete,
    )
    from aletheia.config.loader import load_config

    t0 = time.monotonic()
    inference_call = None
    try:
        cfg = load_config()
        provider_name = cfg.models.default_provider
        provider = await get_provider_async(provider_name)
        messages = [{"role": "user", "content": "Say OK"}]
        inference_call = build_inference_call(
            caller="provider_settings.ping",
            system=None,
            messages=messages,
            context_adapter_id="none",
            requested_provider=provider_name,
            temperature=0,
            max_tokens=10,
            allow_model_fallback=False,
        )
        record_inference_started(inference_call)
        resp = await brokered_complete(
            provider_name,
            provider,
            ModelRequest(
                messages=messages,
                max_tokens=10,
                temperature=0,
                inference=inference_call,
                allow_model_fallback=False,
            ),
            priority=InferencePriority.INTERACTIVE,
        )
        record_inference_finished(inference_call, status="success", response=resp)
        latency_ms = int((time.monotonic() - t0) * 1000)
        content = (resp.content or "").strip()
        if not content:
            return {
                "ok": False,
                "latency_ms": latency_ms,
                "model": resp.model or "unknown",
                "response": "",
                "error": "empty response",
            }
        return {
            "ok": True,
            "latency_ms": latency_ms,
            "model": resp.model or "unknown",
            "response": content[:50],
            "error": None,
        }
    except Exception as e:
        if inference_call is not None:
            record_inference_finished(
                inference_call, status="error", error_type=type(e).__name__
            )
        latency_ms = int((time.monotonic() - t0) * 1000)
        return {"ok": False, "latency_ms": latency_ms, "model": "unknown", "response": "", "error": str(e)[:200]}


@router.get("/model/provider-types")
def get_provider_types():
    """Return access-category metadata for dynamic Settings forms."""
    return {
        provider_type: definition.public()
        for provider_type, definition in PROVIDER_TYPES.items()
        if provider_type in PROVIDER_REGISTRY
    }


class DetectModelsRequest(BaseModel):
    provider: str
    type: str | None = None
    base_url: str | None = None
    workspace_url: str | None = None
    protocol: str | None = None
    api_key: str | None = None
    auth_scheme: str | None = None


def _resolve_candidate_provider(provider_id: str, requested_type: str | None, cfg):
    pcfg = cfg.models.providers.get(provider_id)
    provider_type = requested_type or ((pcfg.type or provider_id) if pcfg is not None else provider_id)
    if provider_type not in VALID_PROVIDERS:
        raise HTTPException(400, f"Invalid provider type. Choose from: {sorted(VALID_PROVIDERS)}")
    return provider_type, pcfg


@router.post("/model/detect-models")
async def detect_models(req: DetectModelsRequest):
    """Probe a candidate provider identity without persisting credentials."""
    from aletheia.model_provider.openai_compatible import OpenAICompatibleProvider
    from aletheia.model_provider.anthropic_compatible import AnthropicCompatibleProvider
    from aletheia.model_provider.databricks import DatabricksProvider
    from aletheia.model_provider.anthropic_native import AnthropicNativeProvider
    from aletheia.model_provider.openai_native import OpenAINativeProvider
    from aletheia.config.secrets import get_secret
    from aletheia.config.loader import load_config

    cfg = load_config()
    provider_type, pcfg = _resolve_candidate_provider(req.provider, req.type, cfg)
    if provider_type in CLI_PROVIDERS:
        raise HTTPException(400, f"{provider_type} uses CLI introspection, not HTTP detection")
    stored_key = get_secret(pcfg.api_key_secret_ref) if pcfg and pcfg.api_key_secret_ref else None
    if provider_type == "databricks" and req.workspace_url:
        try:
            candidate_origin = validate_workspace_url(req.workspace_url)
            persisted_origin = validate_workspace_url(pcfg.workspace_url) if pcfg and pcfg.workspace_url else None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if candidate_origin != persisted_origin:
            stored_key = None
    api_key = req.api_key or stored_key

    if provider_type == "openai_compatible":
        provider = OpenAICompatibleProvider(
            base_url=req.base_url or (pcfg.base_url if pcfg else "") or "",
            model="default",
            api_key=api_key,
        )
    elif provider_type == "anthropic_compatible":
        base_url = req.base_url or (pcfg.base_url if pcfg else "") or ""
        auth_scheme = req.auth_scheme or (getattr(pcfg, "auth_scheme", "x-api-key") if pcfg else "x-api-key")
        provider = AnthropicCompatibleProvider(
            base_url=base_url,
            model="default",
            api_key=api_key,
            auth_scheme=auth_scheme,
        )
    elif provider_type == "databricks":
        try:
            provider = DatabricksProvider(
                workspace_url=req.workspace_url or (pcfg.workspace_url if pcfg else "") or "",
                model=(pcfg.model if pcfg else "default") or "default",
                protocol=req.protocol or (pcfg.protocol if pcfg else "serving_endpoint") or "serving_endpoint",
                api_key=api_key,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    elif provider_type == "anthropic_native":
        provider = AnthropicNativeProvider(model="default", api_key=api_key)
    elif provider_type == "openai_native":
        provider = OpenAINativeProvider(model="default", api_key=api_key)
    else:
        raise HTTPException(400, f"No detect-models support for {provider_type}")

    try:
        return {"models": await provider.list_models()}
    finally:
        await _close_candidate_provider(provider)


class TestCandidateRequest(BaseModel):
    provider: str
    type: str | None = None
    base_url: str | None = None
    workspace_url: str | None = None
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
    from aletheia.model_provider.openai_compatible import OpenAICompatibleProvider
    from aletheia.model_provider.anthropic_compatible import AnthropicCompatibleProvider
    from aletheia.model_provider.databricks import DatabricksProvider
    from aletheia.model_provider.anthropic_native import AnthropicNativeProvider
    from aletheia.model_provider.openai_native import OpenAINativeProvider
    from aletheia.config.secrets import get_secret
    from aletheia.config.loader import load_config

    cfg = load_config()
    provider_type, pcfg = _resolve_candidate_provider(req.provider, req.type, cfg)
    model = req.model or (pcfg.model if pcfg else "default")
    if provider_type in CLI_PROVIDERS:
        raise HTTPException(400, f"{provider_type} uses local auth, not a candidate key")
    stored_key = None
    if pcfg and pcfg.api_key_secret_ref:
        stored_key = get_secret(pcfg.api_key_secret_ref)
    if provider_type == "databricks" and req.workspace_url:
        try:
            candidate_origin = validate_workspace_url(req.workspace_url)
            persisted_origin = validate_workspace_url(pcfg.workspace_url) if pcfg and pcfg.workspace_url else None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if candidate_origin != persisted_origin:
            stored_key = None
    api_key = req.api_key or stored_key

    if provider_type == "openai_compatible":
        provider = OpenAICompatibleProvider(
            base_url=req.base_url or (pcfg.base_url if pcfg else "") or "",
            model=model,
            protocol=req.protocol or (pcfg.protocol if pcfg else "chat_completions"),
            api_key=api_key,
        )
    elif provider_type == "anthropic_compatible":
        auth_scheme = req.auth_scheme or (getattr(pcfg, "auth_scheme", "x-api-key") if pcfg else "x-api-key")
        provider = AnthropicCompatibleProvider(
            base_url=req.base_url or (pcfg.base_url if pcfg else "") or "",
            model=model,
            api_key=api_key,
            auth_scheme=auth_scheme,
        )
    elif provider_type == "databricks":
        try:
            provider = DatabricksProvider(
                workspace_url=req.workspace_url or (pcfg.workspace_url if pcfg else "") or "",
                model=model,
                protocol=req.protocol or (pcfg.protocol if pcfg else "serving_endpoint") or "serving_endpoint",
                api_key=api_key,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    elif provider_type == "anthropic_native":
        provider = AnthropicNativeProvider(model=model, api_key=api_key)
    elif provider_type == "openai_native":
        provider = OpenAINativeProvider(model=model, api_key=api_key)
    else:
        raise HTTPException(400, f"No test-candidate support for {provider_type}")

    try:
        health = await provider.healthcheck()
        # A known Databricks endpoint can be invokable without workspace-wide LIST access.
        if not health.available and not (provider_type == "databricks" and req.probe):
            return {"available": False, "model": health.model, "error": health.error}

        if req.probe:
            from aletheia.model_provider.base import ModelRequest
            from aletheia.model_provider.broker import InferencePriority, brokered_complete
            from aletheia.model_provider.inference_contract import build_inference_call
            from aletheia.economics.inference_calls import (
                record_inference_finished,
                record_inference_started,
            )

            inference_call = None
            try:
                messages = [{"role": "user", "content": "Say OK"}]
                inference_call = build_inference_call(
                    caller="provider_settings.candidate_probe",
                    system=None,
                    messages=messages,
                    context_adapter_id="none",
                    requested_provider=req.provider,
                    temperature=0,
                    max_tokens=200,
                    allow_model_fallback=False,
                )
                record_inference_started(inference_call)
                response = await brokered_complete(
                    req.provider,
                    provider,
                    ModelRequest(
                        messages=messages,
                        max_tokens=200,
                        temperature=0,
                        inference=inference_call,
                        allow_model_fallback=False,
                    ),
                    priority=InferencePriority.INTERACTIVE,
                )
                record_inference_finished(
                    inference_call, status="success", response=response
                )
                # Thinking-mode models may exhaust budget on reasoning without text;
                # a successful HTTP round-trip is sufficient proof of connectivity.
            except Exception as e:
                if inference_call is not None:
                    record_inference_finished(
                        inference_call, status="error", error_type=type(e).__name__
                    )
                return {"available": False, "model": health.model, "error": f"inference failed: {e}"}

        return {"available": True, "model": health.model, "error": None, "models": health.models}
    finally:
        await _close_candidate_provider(provider)


# --- Codex subscription provider: web-triggered device-auth login + model listing ---


@router.post("/model/codex/login-start")
async def codex_login_start():
    """Begin Codex device-auth login. Returns verification URL + code (never a token)."""
    from aletheia.model_provider import codex_auth

    result = await codex_auth.start_login()
    write_audit(
        {
            "actor": "human",
            "source": "dashboard",
            "event_type": "codex_login_started",
            "entity_type": "model_provider",
            "entity_id": "codex_cli",
            "message": f"Codex device-auth login start (state={result.get('state')})",
        }
    )
    return result


@router.get("/model/codex/login-status")
async def codex_login_status():
    """Poll Codex login status (authoritative via `codex login status`). No token."""
    from aletheia.model_provider import codex_auth

    return await codex_auth.poll_status()


@router.post("/model/codex/login-cancel")
def codex_login_cancel():
    from aletheia.model_provider import codex_auth

    result = codex_auth.cancel()
    write_audit(
        {
            "actor": "human",
            "source": "dashboard",
            "event_type": "codex_login_cancelled",
            "entity_type": "model_provider",
            "entity_id": "codex_cli",
            "message": "Codex device-auth login cancelled by user",
        }
    )
    return result


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
    type: str | None = None
    display_name: str | None = None
    base_url: str | None = None
    protocol: str | None = None
    model: str | None = None
    enabled: bool | None = None
    command: str | None = None
    mode: str | None = None
    codex_model: str | None = None
    workspace_url: str | None = None
    default: bool | None = None
    set_as_deep: bool | None = None
    set_as_fast: bool | None = None
    api_key: str | None = None
    auth_scheme: str | None = None
    model_fallback_enabled: bool | None = None
    fallback_models: list[str] | None = None
    inference_max_concurrency: int | None = Field(default=None, ge=1, le=16)
    inference_requests_per_minute: int | None = Field(default=None, ge=0, le=10_000)
    inference_tpm: int | None = Field(default=None, ge=0, le=100_000_000)
    inference_queue_capacity: int | None = Field(default=None, ge=1, le=1_000)
    inference_reserved_high_priority_slots: int | None = Field(default=None, ge=0, le=1_000)
    inference_timeout_sec: float | None = Field(default=None, ge=1, le=1_800)


class ModelProfileFields(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    base_url: str | None = None
    protocol: str | None = None
    model: str | None = None
    enabled: bool | None = None
    workspace_url: str | None = None
    api_key: str | None = None
    auth_scheme: str | None = None
    model_fallback_enabled: bool | None = None
    fallback_models: list[str] | None = None
    inference_max_concurrency: int | None = Field(default=None, ge=1, le=16)
    inference_requests_per_minute: int | None = Field(default=None, ge=0, le=10_000)
    inference_tpm: int | None = Field(default=None, ge=0, le=100_000_000)
    inference_queue_capacity: int | None = Field(default=None, ge=1, le=1_000)
    inference_reserved_high_priority_slots: int | None = Field(default=None, ge=0, le=1_000)
    inference_timeout_sec: float | None = Field(default=None, ge=1, le=1_800)


class CreateModelProfileRequest(BaseModel):
    profile_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    display_name: str = Field(min_length=1, max_length=80)
    type: str
    enabled: bool = False
    base_url: str | None = None
    protocol: str | None = None
    model: str | None = None
    workspace_url: str | None = None
    api_key: str | None = None
    auth_scheme: str | None = None
    model_fallback_enabled: bool | None = None
    fallback_models: list[str] | None = None
    inference_max_concurrency: int | None = Field(default=None, ge=1, le=16)
    inference_requests_per_minute: int | None = Field(default=None, ge=0, le=10_000)
    inference_tpm: int | None = Field(default=None, ge=0, le=100_000_000)
    inference_queue_capacity: int | None = Field(default=None, ge=1, le=1_000)
    inference_reserved_high_priority_slots: int | None = Field(default=None, ge=0, le=1_000)
    inference_timeout_sec: float | None = Field(default=None, ge=1, le=1_800)


class UpdateModelProfileRequest(ModelProfileFields):
    pass


class ModelRoutingRequest(BaseModel):
    default_provider: str | None = None
    deep_provider: str | None = None
    fast_provider: str | None = None


def _resolve_request_type(req: ModelConfigRequest, pcfg) -> str:
    existing_type = (pcfg.type or req.provider) if pcfg is not None else None
    provider_type = req.type or existing_type or (req.provider if req.provider in VALID_PROVIDERS else None)
    if provider_type not in VALID_PROVIDERS:
        raise HTTPException(400, f"Invalid provider type. Choose from: {sorted(VALID_PROVIDERS)}")
    if pcfg is not None and req.type and existing_type != req.type:
        raise HTTPException(422, "A provider profile's type cannot be changed")
    return provider_type


def _profile_api_key_ref(provider_id: str, provider_type: str, pcfg) -> str:
    is_legacy_identity = provider_id == provider_type
    if provider_type == "databricks":
        origin = validate_workspace_url(pcfg.workspace_url or "")
        origin_id = hashlib.sha256(origin.encode("utf-8")).hexdigest()[:16]
        if is_legacy_identity:
            return f"model.databricks.{origin_id}.api_key"
        return f"model.profile.{provider_id}.databricks.{origin_id}.api_key"
    if is_legacy_identity:
        return f"model.{provider_id}.api_key"
    return f"model.profile.{provider_id}.api_key"


def _apply_model_config_sync(req: ModelConfigRequest):
    if req.api_key is not None:
        req.api_key = req.api_key.strip() or None

    cfg = load_config()
    pcfg = cfg.models.providers.get(req.provider)
    provider_type = _resolve_request_type(req, pcfg)
    previous_workspace_url = (pcfg.workspace_url or pcfg.base_url) if pcfg is not None else None
    if pcfg is None:
        from aletheia.config.schema import ProviderConfig

        pcfg = ProviderConfig(
            type=provider_type,
            display_name=req.display_name or "",
            protocol="serving_endpoint" if provider_type == "databricks" else "chat_completions",
        )
        cfg.models.providers[req.provider] = pcfg

    if provider_type == "databricks":
        if req.base_url is not None:
            raise HTTPException(422, "Databricks uses workspace_url; base_url is not accepted")
        if req.protocol is not None and req.protocol not in DATABRICKS_PROTOCOLS:
            raise HTTPException(422, "Unsupported Databricks protocol")
        if req.workspace_url is not None:
            try:
                validate_workspace_url(req.workspace_url)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
        if req.api_key and not (req.workspace_url or pcfg.workspace_url):
            raise HTTPException(422, "Databricks workspace_url is required before storing a token")
        if req.workspace_url is not None:
            next_origin = validate_workspace_url(req.workspace_url)
            previous_origin = validate_workspace_url(previous_workspace_url) if previous_workspace_url else None
            if previous_origin != next_origin and pcfg.api_key_secret_ref and not req.api_key:
                raise HTTPException(
                    422,
                    "A new Databricks token is required when the workspace origin changes",
                )

    pcfg.type = provider_type
    for field_name in (
        "display_name",
        "base_url",
        "protocol",
        "model",
        "enabled",
        "command",
        "mode",
        "codex_model",
        "workspace_url",
        "auth_scheme",
        "model_fallback_enabled",
        "inference_max_concurrency",
        "inference_requests_per_minute",
        "inference_queue_capacity",
        "inference_reserved_high_priority_slots",
        "inference_timeout_sec",
    ):
        value = getattr(req, field_name)
        if value is not None:
            setattr(pcfg, field_name, value)
    if req.fallback_models is not None:
        pcfg.fallback_models = list(
            dict.fromkeys(model.strip() for model in req.fallback_models if isinstance(model, str) and model.strip())
        )
    if req.inference_tpm is not None:
        pcfg.inference_tokens_per_minute = req.inference_tpm
    if pcfg.inference_reserved_high_priority_slots > pcfg.inference_queue_capacity:
        raise HTTPException(
            422,
            "inference_reserved_high_priority_slots cannot exceed inference_queue_capacity",
        )
    if req.default:
        cfg.models.default_provider = req.provider
    if req.set_as_deep:
        cfg.models.deep_provider = req.provider
    if req.set_as_fast:
        cfg.models.fast_provider = req.provider

    written_secret_ref = None
    previous_secret = None
    if req.api_key:
        secret_ref = _profile_api_key_ref(req.provider, provider_type, pcfg)
        previous_secret = get_secret(secret_ref)
        set_secret(secret_ref, req.api_key)
        written_secret_ref = secret_ref
        pcfg.api_key_secret_ref = secret_ref

    try:
        save_config(cfg)
    except Exception:
        if written_secret_ref:
            if previous_secret is not None:
                set_secret(written_secret_ref, previous_secret)
            else:
                delete_secret(written_secret_ref)
        raise
    write_audit(
        {
            "actor": "human",
            "source": "dashboard",
            "event_type": "model_provider_changed",
            "entity_type": "model_provider",
            "entity_id": req.provider,
            "message": f"Model provider '{req.provider}' updated" + (" (api_key set)" if req.api_key else ""),
        }
    )
    return cfg, pcfg


async def _apply_model_config(req: ModelConfigRequest) -> dict:
    cfg, pcfg = await asyncio.to_thread(_apply_model_config_sync, req)
    await clear_cache_async()
    from aletheia.model_provider.broker import inference_broker

    await inference_broker.invalidate_provider(req.provider)
    key_status = await asyncio.to_thread(_key_status, pcfg.api_key_secret_ref)
    return {
        "provider": req.provider,
        "default_provider": cfg.models.default_provider,
        "deep_provider": cfg.models.deep_provider,
        "fast_provider": cfg.models.fast_provider,
        "api_key": key_status,
    }


@router.post("/settings/model")
async def set_model(req: ModelConfigRequest, response: Response):
    """Deprecated fixed-provider mutation path retained during profile migration."""
    if req.provider not in VALID_PROVIDERS:
        raise HTTPException(400, f"Invalid provider. Choose from: {sorted(VALID_PROVIDERS)}")
    response.headers["Deprecation"] = "true"
    response.headers["Link"] = '</docs/design/proposals/model-provider-profiles-and-access-methods>; rel="deprecation"'
    return await _apply_model_config(req)


async def _verify_profile_candidate(profile_id: str, provider_type: str, payload: dict) -> None:
    candidate_fields = set(TestCandidateRequest.model_fields) - {"provider", "type", "probe"}
    candidate = TestCandidateRequest(
        provider=profile_id,
        type=provider_type,
        probe=True,
        **{key: value for key, value in payload.items() if key in candidate_fields},
    )
    result = await test_model_candidate(candidate)
    if not result.get("available"):
        raise HTTPException(422, "Model profile verification failed")


@router.post("/settings/model-profiles", status_code=201)
async def create_model_profile(req: CreateModelProfileRequest):
    if req.type not in API_PROVIDER_TYPES:
        raise HTTPException(422, "Only API provider types can be created as profiles")
    cfg = await asyncio.to_thread(load_config)
    if req.profile_id in cfg.models.providers:
        raise HTTPException(409, f"Model provider profile already exists: {req.profile_id}")
    payload = req.model_dump(exclude={"profile_id"})
    await _verify_profile_candidate(req.profile_id, req.type, payload)
    return await _apply_model_config(ModelConfigRequest(provider=req.profile_id, **payload))


@router.patch("/settings/model-profiles/{profile_id}")
async def update_model_profile(profile_id: str, req: UpdateModelProfileRequest):
    cfg = await asyncio.to_thread(load_config)
    pcfg = cfg.models.providers.get(profile_id)
    if pcfg is None:
        raise HTTPException(404, f"Unknown model provider profile: {profile_id}")
    provider_type = pcfg.type or profile_id
    if provider_type not in API_PROVIDER_TYPES:
        raise HTTPException(422, "CLI and OAuth integrations are not editable API profiles")
    payload = req.model_dump(exclude_unset=True)
    verification_fields = {"base_url", "workspace_url", "protocol", "model", "api_key", "auth_scheme"}
    if verification_fields.intersection(payload) or bool(payload.get("enabled")):
        await _verify_profile_candidate(profile_id, provider_type, payload)
    return await _apply_model_config(ModelConfigRequest(provider=profile_id, **payload))


class UpdateModelIntegrationRequest(BaseModel):
    enabled: bool | None = None
    model: str | None = None
    codex_model: str | None = None
    inference_max_concurrency: int | None = Field(default=None, ge=1, le=16)
    inference_requests_per_minute: int | None = Field(default=None, ge=0, le=10_000)
    inference_tpm: int | None = Field(default=None, ge=0, le=100_000_000)
    inference_queue_capacity: int | None = Field(default=None, ge=1, le=1_000)
    inference_reserved_high_priority_slots: int | None = Field(default=None, ge=0, le=1_000)
    inference_timeout_sec: float | None = Field(default=None, ge=1, le=1_800)


@router.patch("/settings/model-integrations/{integration_id}")
async def update_model_integration(
    integration_id: str,
    req: UpdateModelIntegrationRequest,
):
    definition = PROVIDER_TYPES.get(integration_id)
    if (
        definition is None
        or not definition.singleton
        or definition.category not in {"cli", "oauth"}
        or integration_id not in PROVIDER_REGISTRY
    ):
        raise HTTPException(404, f"Unknown model integration: {integration_id}")
    payload = req.model_dump(exclude_unset=True)
    payload["type"] = integration_id
    payload["display_name"] = definition.label
    return await _apply_model_config(ModelConfigRequest(provider=integration_id, **payload))


def _update_model_routing_sync(req: ModelRoutingRequest) -> dict:
    cfg = load_config()
    changed: dict[str, str | None] = {}
    for field_name in req.model_fields_set:
        value = getattr(req, field_name)
        if value and value not in cfg.models.providers:
            raise HTTPException(404, f"Unknown model provider: {value}")
        if field_name == "default_provider":
            cfg.models.default_provider = value or ""
        else:
            setattr(cfg.models, field_name, value)
        changed[field_name] = value
    save_config(cfg)
    write_audit(
        {
            "actor": "human",
            "source": "dashboard",
            "event_type": "model_routing_changed",
            "entity_type": "model_routing",
            "entity_id": "models",
            "message": "Model routing roles updated",
            "after": changed,
        }
    )
    return {
        "default_provider": cfg.models.default_provider,
        "deep_provider": cfg.models.deep_provider,
        "fast_provider": cfg.models.fast_provider,
    }


@router.post("/settings/model-routing")
async def update_model_routing(req: ModelRoutingRequest):
    result = await asyncio.to_thread(_update_model_routing_sync, req)
    clear_cache()
    return result


def _delete_model_profile_sync(profile_id: str) -> dict:
    cfg = load_config()
    pcfg = cfg.models.providers.get(profile_id)
    if pcfg is None:
        raise HTTPException(404, f"Unknown model provider profile: {profile_id}")
    if (pcfg.type or profile_id) not in API_PROVIDER_TYPES:
        raise HTTPException(422, "CLI and OAuth integrations cannot be deleted as API profiles")

    cleared_roles: list[str] = []
    if cfg.models.default_provider == profile_id:
        cfg.models.default_provider = ""
        cleared_roles.append("default")
    if cfg.models.deep_provider == profile_id:
        cfg.models.deep_provider = None
        cleared_roles.append("deep")
    if cfg.models.fast_provider == profile_id:
        cfg.models.fast_provider = None
        cleared_roles.append("fast")

    secret_refs = [
        ref
        for ref in (
            pcfg.api_key_secret_ref,
            pcfg.oauth_token_ref,
            pcfg.oauth_refresh_ref,
        )
        if ref
    ]
    secret_values = {ref: get_secret(ref) for ref in secret_refs}
    try:
        for ref in secret_refs:
            delete_secret(ref)
    except Exception:
        for ref, value in secret_values.items():
            if value:
                set_secret(ref, value)
        raise
    del cfg.models.providers[profile_id]
    try:
        save_config(cfg)
    except Exception:
        for ref, value in secret_values.items():
            if value:
                set_secret(ref, value)
        raise
    write_audit(
        {
            "actor": "human",
            "source": "dashboard",
            "event_type": "model_provider_deleted",
            "entity_type": "model_provider",
            "entity_id": profile_id,
            "message": f"Model provider '{profile_id}' deleted",
            "after": {"cleared_roles": cleared_roles},
        }
    )
    return {"deleted": True, "provider": profile_id, "cleared_roles": cleared_roles}


@router.delete("/settings/model-profiles/{profile_id}")
async def delete_model_profile(profile_id: str, confirm: bool = False):
    if not confirm:
        raise HTTPException(409, "Deleting a model profile requires confirm=true")
    result = await asyncio.to_thread(_delete_model_profile_sync, profile_id)
    await clear_cache_async()
    from aletheia.model_provider.broker import inference_broker

    await inference_broker.invalidate_provider(profile_id)
    return result


# --- OpenAI subscription OAuth ---


@router.post("/model/openai-oauth/start")
async def openai_oauth_start():
    """Begin OpenAI OAuth PKCE flow. Returns {auth_url} for frontend to open."""
    from aletheia.model_provider import openai_oauth

    result = await openai_oauth.start_login()
    return result


@router.get("/model/openai-oauth/status")
def openai_oauth_status():
    """Poll OpenAI OAuth login status. No tokens exposed, no side-effects."""
    from aletheia.model_provider import openai_oauth

    return openai_oauth.get_status()


@router.post("/model/openai-oauth/finalize")
def openai_oauth_finalize():
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
    write_audit(
        {
            "actor": "human",
            "source": "dashboard",
            "event_type": "openai_oauth_finalized",
            "entity_type": "model_provider",
            "entity_id": "openai_native",
            "message": "OpenAI OAuth tokens persisted to config",
        }
    )
    return {"persisted": True}


@router.post("/model/openai-oauth/cancel")
async def openai_oauth_cancel():
    from aletheia.model_provider import openai_oauth

    return await openai_oauth.cancel()


@router.post("/model/openai-oauth/disconnect")
def openai_oauth_disconnect():
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

    write_audit(
        {
            "actor": "human",
            "source": "dashboard",
            "event_type": "oauth_disconnected",
            "entity_type": "model_provider",
            "entity_id": "openai_native",
            "message": "OpenAI OAuth disconnected, reverted to api_key auth",
        }
    )
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
def claude_oauth_status():
    """Poll Claude OAuth login status."""
    from aletheia.model_provider import claude_oauth

    return claude_oauth.get_status()


@router.post("/model/claude-oauth/cancel")
def claude_oauth_cancel():
    from aletheia.model_provider import claude_oauth

    return claude_oauth.cancel()


@router.post("/model/claude-oauth/disconnect")
def claude_oauth_disconnect():
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

    write_audit(
        {
            "actor": "human",
            "source": "dashboard",
            "event_type": "oauth_disconnected",
            "entity_type": "model_provider",
            "entity_id": "anthropic_native",
            "message": "Claude OAuth disconnected, reverted to api_key auth",
        }
    )
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
