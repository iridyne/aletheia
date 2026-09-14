"""Model provider configuration API routes.

Secrets are write-only over the API: POST accepts an api_key, but no GET ever
returns a key. GET responses only report whether a key is configured + last-4.
"""

import asyncio
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException  # pyright: ignore[reportMissingImports]
from pydantic import BaseModel, ConfigDict, Field, field_validator

from aletheia.config.loader import load_config, save_config, mutate_config
from aletheia.config.schema import ModelSelection
from aletheia.config.secrets import delete_secret, get_secret, set_secret
from aletheia.files.jsonl import write_audit
from aletheia.model_provider.base import MODEL_HEALTH_STALE_AFTER_SECONDS
from aletheia.model_provider.registry import (  # pyright: ignore[reportMissingImports]
    API_PROVIDER_TYPES,
    PROVIDER_TYPES,
    default_protocol_for_provider_type,
    provider_config_protocol,
    provider_kind,
    validate_provider_protocol,
)
from aletheia.model_provider.router import (
    clear_cache,
    clear_cache_async,
    clear_provider_verification,
    _classify_health_error,
    healthcheck_all_with_metadata,
    persist_provider_verification,
    provider_configuration_identity,
    provider_is_configured,
)

router = APIRouter(prefix="/api", tags=["model"])

VALID_PROVIDERS = API_PROVIDER_TYPES


class ModelHealthRecord(BaseModel):
    """Canonical provider health observation returned to UI/API consumers."""

    provider: str
    configured: bool
    enabled: bool
    reachable: bool | None = None
    verified: bool | None = None
    verified_at: str | None = None
    verification_identity: str | None = None
    verification_source: str | None = None
    verification_invalidated: bool = False
    verification_detail: str | None = None
    state: Literal[
        "not_configured",
        "disabled",
        "unknown",
        "stale",
        "unavailable",
        "reachable",
        "verified",
    ] = "unknown"
    reason_code: str = "no_observation"
    routing_roles: list[str] = Field(default_factory=list)
    # Compatibility alias retained while consumers migrate to reachable.
    available: bool = False
    model: str = ""
    error: str | None = None
    models: list[str] = Field(default_factory=list)
    model_metadata: list[dict[str, Any]] = Field(default_factory=list)
    observed_at: str | None = None
    age_seconds: float | None = None
    freshness: Literal["fresh", "stale", "unknown"]
    cached: bool
    stale_after_seconds: float


def _health_observation(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _model_health_record(
    health: object,
    *,
    cached: bool,
    now: datetime | None = None,
) -> ModelHealthRecord:
    """Project one router result without using the client's receipt time."""

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    observed = _health_observation(getattr(health, "observed_at", None))
    if observed is None:
        observed_at = None
        age_seconds = None
        freshness: Literal["fresh", "stale", "unknown"] = "unknown"
    else:
        observed_at = observed.isoformat().replace("+00:00", "Z")
        age_seconds = round(max(0.0, (current - observed).total_seconds()), 3)
        stale_after_seconds = float(
            getattr(health, "stale_after_seconds", MODEL_HEALTH_STALE_AFTER_SECONDS)
            or MODEL_HEALTH_STALE_AFTER_SECONDS
        )
        freshness = "fresh" if age_seconds <= stale_after_seconds else "stale"

    configured = bool(getattr(health, "configured", True))
    enabled = bool(getattr(health, "enabled", True))
    raw_reachable = getattr(health, "reachable", None)
    reachable = (
        bool(getattr(health, "available", False))
        if raw_reachable is None and observed is not None
        else raw_reachable
    )
    verified = getattr(health, "verified", None)
    verification_invalidated = bool(getattr(health, "verification_invalidated", False))
    reason_code = str(getattr(health, "reason_code", "") or "")
    raw_state = str(getattr(health, "state", "") or "")
    if not enabled and raw_state == "disabled":
        state: Literal[
            "not_configured", "disabled", "unknown", "stale", "unavailable", "reachable", "verified"
        ] = "disabled"
        reason_code = "disabled"
    elif not configured:
        state: Literal[
            "not_configured", "disabled", "unknown", "stale", "unavailable", "reachable", "verified"
        ] = "not_configured"
        reason_code = "missing_configuration"
    elif observed is not None and freshness == "stale":
        state = "stale"
    elif reachable is True:
        state = "verified" if verified is True else "reachable"
        reason_code = reason_code or ("verified" if verified is True else "reachable")
    elif reachable is False:
        state = "unavailable"
    elif verified is True:
        state = "verified"
        reason_code = reason_code or "verified"
    elif verification_invalidated:
        state = "unknown"
        reason_code = "verification_invalidated"
    else:
        state = "unknown"
        reason_code = reason_code or "no_observation"

    return ModelHealthRecord(
        provider=str(getattr(health, "provider", "")),
        configured=configured,
        enabled=enabled,
        reachable=reachable,
        verified=verified,
        verified_at=getattr(health, "verified_at", None),
        verification_identity=getattr(health, "verification_identity", None),
        verification_source=getattr(health, "verification_source", None),
        verification_invalidated=verification_invalidated,
        verification_detail=getattr(health, "verification_detail", None),
        state=state,
        reason_code=reason_code,
        routing_roles=list(getattr(health, "routing_roles", None) or []),
        available=bool(getattr(health, "available", False)),
        model=str(getattr(health, "model", "") or ""),
        error=getattr(health, "error", None),
        models=list(getattr(health, "models", None) or []),
        model_metadata=list(getattr(health, "model_metadata", None) or []),
        observed_at=observed_at,
        age_seconds=age_seconds,
        freshness=freshness,
        cached=cached,
        stale_after_seconds=float(
            getattr(health, "stale_after_seconds", MODEL_HEALTH_STALE_AFTER_SECONDS)
            or MODEL_HEALTH_STALE_AFTER_SECONDS
        ),
    )


_ANTHROPIC_EMPTY_TEXT_SUCCESS_STOP_REASONS = {
    "end_turn",
    "max_tokens",
    "stop_sequence",
    "tool_use",
}


def _has_provider_completion_evidence(raw: object) -> bool:
    """Recognize protocol-level completion when no final text was emitted."""

    if not isinstance(raw, dict):
        return False

    choices = raw.get("choices")
    if isinstance(choices, list) and any(
        isinstance(choice, dict)
        and isinstance(choice.get("finish_reason"), str)
        and bool(choice["finish_reason"].strip())
        for choice in choices
    ):
        return True

    if raw.get("status") == "completed":
        return True

    stop_reason = raw.get("stop_reason")
    return (
        isinstance(stop_reason, str)
        and stop_reason in _ANTHROPIC_EMPTY_TEXT_SUCCESS_STOP_REASONS
    )


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
            "configured": provider_is_configured(name, pcfg),
            "display_name": pcfg.display_name or (definition.label if definition else name),
            "enabled": pcfg.enabled,
            "model": pcfg.model,
            "model_fallback_enabled": getattr(pcfg, "model_fallback_enabled", True),
            "fallback_models": list(getattr(pcfg, "fallback_models", []) or []),
            "api_key": _key_status(pcfg.api_key_secret_ref if definition else None),
            "auth_type": pcfg.auth_type,
            "oauth": _key_status(None),
            "inference_max_concurrency": pcfg.inference_max_concurrency,
            "inference_requests_per_minute": pcfg.inference_requests_per_minute,
            "inference_tpm": pcfg.inference_tokens_per_minute,
            "inference_queue_capacity": pcfg.inference_queue_capacity,
            "inference_reserved_high_priority_slots": pcfg.inference_reserved_high_priority_slots,
            "inference_timeout_sec": pcfg.inference_timeout_sec,
        }
        if ptype in API_PROVIDER_TYPES:
            entry["base_url"] = pcfg.base_url
            entry["protocol"] = pcfg.protocol
        if ptype == "anthropic_compatible":
            entry["auth_scheme"] = getattr(pcfg, "auth_scheme", "x-api-key")
        providers[name] = entry
    return {
        "default_provider": cfg.models.default_provider,
        "deep": cfg.models.deep,
        "fast": cfg.models.fast,
        "providers": providers,
    }


@router.get("/model/health", response_model=list[ModelHealthRecord])
async def get_model_health():
    results, cached = await healthcheck_all_with_metadata()
    return [_model_health_record(health, cached=cached) for health in results]


@router.get("/model/broker")
async def get_model_broker():
    """Sanitized process-local inference admission and queue-wait metrics."""

    from aletheia.model_provider.broker import inference_broker

    return await inference_broker.snapshot()


@router.post("/model/ping")
async def ping_model():
    """Instant inference test on the current default provider.

    Sends 'Say OK' with max_tokens=10. Returns latency, model name, and
    any final response text. Reasoning-only protocol completions are successful
    even when the bounded probe emits no final text. Never raises — errors are
    in the response body.
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
    provider_name: str | None = None
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
        latency_ms = int((time.monotonic() - t0) * 1000)
        content = (resp.content or "").strip()
        if not content and not _has_provider_completion_evidence(resp.raw):
            record_inference_finished(
                inference_call, status="error", error_type="EmptyModelResponse"
            )
            verification_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            verification_identity = None
            verification_persisted = False
            try:
                current_cfg = await asyncio.to_thread(load_config)
                current_pcfg = current_cfg.models.providers.get(provider_name)
                if current_pcfg is not None:
                    verification_identity = await asyncio.to_thread(
                        provider_configuration_identity, provider_name, current_pcfg
                    )
                    verification_persisted = await asyncio.to_thread(
                        persist_provider_verification,
                        provider_name,
                        identity=verification_identity,
                        verified=False,
                        source="model_ping",
                        reason_code="probe_failure",
                        detail="empty response",
                        verified_at=verification_at,
                    )
            except Exception:
                verification_persisted = False
            return {
                "ok": False,
                "latency_ms": latency_ms,
                "model": resp.model or "unknown",
                "response": "",
                "error": "empty response",
                "verified": False,
                "verified_at": verification_at,
                "verification_identity": verification_identity,
                "verification_persisted": verification_persisted,
            }
        record_inference_finished(inference_call, status="success", response=resp)
        verification_persisted = False
        verification_identity = None
        verification_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        try:
            current_cfg = await asyncio.to_thread(load_config)
            current_pcfg = current_cfg.models.providers.get(provider_name)
            if current_pcfg is not None:
                verification_identity = await asyncio.to_thread(
                    provider_configuration_identity, provider_name, current_pcfg
                )
                verification_persisted = await asyncio.to_thread(
                    persist_provider_verification,
                    provider_name,
                    identity=verification_identity,
                    verified=True,
                    source="model_ping",
                    reason_code="verified",
                    verified_at=verification_at,
                )
        except Exception:
            verification_persisted = False
        return {
            "ok": True,
            "latency_ms": latency_ms,
            "model": resp.model or "unknown",
            "response": content[:50],
            "error": None,
            "verified": True,
            "verified_at": verification_at,
            "verification_identity": verification_identity,
            "verification_persisted": verification_persisted,
        }
    except Exception as e:
        if inference_call is not None:
            record_inference_finished(
                inference_call, status="error", error_type=type(e).__name__
            )
        latency_ms = int((time.monotonic() - t0) * 1000)
        verification_persisted = False
        verification_identity = None
        verification_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        try:
            failed_cfg = await asyncio.to_thread(load_config)
            failed_pcfg = (
                failed_cfg.models.providers.get(provider_name)
                if provider_name
                else None
            )
            if provider_name and failed_pcfg is not None:
                verification_identity = await asyncio.to_thread(
                    provider_configuration_identity,
                    provider_name,
                    failed_pcfg,
                )
                verification_persisted = await asyncio.to_thread(
                    persist_provider_verification,
                    provider_name,
                    identity=verification_identity,
                    verified=False,
                    source="model_ping",
                    reason_code=_classify_health_error(str(e)),
                    detail=str(e)[:200],
                    verified_at=verification_at,
                )
        except Exception:
            verification_persisted = False
        return {
            "ok": False,
            "latency_ms": latency_ms,
            "model": "unknown",
            "response": "",
            "error": str(e)[:200],
            "verified": False,
            "verified_at": verification_at,
            "verification_identity": verification_identity,
            "verification_persisted": verification_persisted,
        }


@router.get("/model/provider-types")
def get_provider_types():
    """Return access-category metadata for dynamic Settings forms."""
    return {
        provider_type: definition.public()
        for provider_type, definition in PROVIDER_TYPES.items()
        if provider_type in API_PROVIDER_TYPES
    }


class DetectModelsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    type: str | None = None
    base_url: str | None = None
    protocol: str | None = None
    api_key: str | None = None
    auth_scheme: str | None = None


def _resolve_candidate_provider(provider_id: str, requested_type: str | None, cfg):
    pcfg = cfg.models.providers.get(provider_id)
    provider_type = requested_type or ((pcfg.type or provider_id) if pcfg is not None else provider_id)
    if provider_type not in VALID_PROVIDERS:
        raise HTTPException(400, f"Invalid provider type. Choose from: {sorted(VALID_PROVIDERS)}")
    return provider_type, pcfg


def _candidate_protocol(provider_type: str, requested_protocol: str | None, pcfg) -> str:
    try:
        if requested_protocol is not None:
            return validate_provider_protocol(
                provider_type, requested_protocol, explicit=True
            )
        if pcfg is not None:
            return provider_config_protocol(provider_type, pcfg)
        return default_protocol_for_provider_type(provider_type)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


async def _candidate_api_key(req, provider_type: str, pcfg, protocol: str | None) -> str | None:
    """Never lend a configured endpoint's credential to a different candidate."""
    candidate_key = req.api_key.strip() if isinstance(req.api_key, str) else req.api_key
    if candidate_key:
        return candidate_key
    if pcfg is None or not pcfg.api_key_secret_ref:
        return None
    saved_type = pcfg.type or req.provider
    if (
        saved_type != provider_type
        or (req.base_url or pcfg.base_url or "").rstrip("/") != (pcfg.base_url or "").rstrip("/")
        or protocol != provider_config_protocol(saved_type, pcfg)
        or (
            provider_type == "anthropic_compatible"
            and (req.auth_scheme or pcfg.auth_scheme) != pcfg.auth_scheme
        )
    ):
        raise HTTPException(422, "candidate_credentials_required_for_changed_endpoint")
    return await asyncio.to_thread(get_secret, pcfg.api_key_secret_ref)


@router.post("/model/detect-models")
async def detect_models(req: DetectModelsRequest):
    """Probe a candidate provider identity without persisting credentials."""
    from aletheia.model_provider.openai_compatible import OpenAICompatibleProvider
    from aletheia.model_provider.anthropic_compatible import AnthropicCompatibleProvider
    from aletheia.config.loader import load_config

    cfg = load_config()
    provider_type, pcfg = _resolve_candidate_provider(req.provider, req.type, cfg)
    protocol = (
        _candidate_protocol(provider_type, req.protocol, pcfg)
        if provider_type in API_PROVIDER_TYPES
        else None
    )
    api_key = await _candidate_api_key(req, provider_type, pcfg, protocol)

    if provider_type == "openai_compatible":
        provider = OpenAICompatibleProvider(
            base_url=req.base_url or (pcfg.base_url if pcfg else "") or "",
            model="default",
            protocol=protocol or default_protocol_for_provider_type(provider_type),
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
            protocol=protocol or default_protocol_for_provider_type(provider_type),
        )
    else:
        raise HTTPException(400, f"No detect-models support for {provider_type}")

    setattr(provider, "_aletheia_provider_name", req.provider)
    try:
        return {"models": await provider.list_models()}
    finally:
        await _close_candidate_provider(provider)


class TestCandidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    type: str | None = None
    base_url: str | None = None
    protocol: str | None = None
    model: str | None = None
    api_key: str | None = None
    auth_scheme: str | None = None
    probe: bool = False


@router.post("/model/test-candidate")
async def test_model_candidate(req: TestCandidateRequest):
    """Probe a candidate without storing its credentials.

    Passive checks only report reachability. ``probe=true`` performs explicit
    inference verification and persists evidence only when the candidate matches
    the currently saved provider identity.
    """
    from aletheia.model_provider.openai_compatible import OpenAICompatibleProvider
    from aletheia.model_provider.anthropic_compatible import AnthropicCompatibleProvider
    from aletheia.config.loader import load_config

    cfg = load_config()
    provider_type, pcfg = _resolve_candidate_provider(req.provider, req.type, cfg)
    model = req.model or (pcfg.model if pcfg else "default")
    protocol = (
        _candidate_protocol(provider_type, req.protocol, pcfg)
        if provider_type in API_PROVIDER_TYPES
        else None
    )
    api_key = await _candidate_api_key(req, provider_type, pcfg, protocol)

    if provider_type == "openai_compatible":
        provider = OpenAICompatibleProvider(
            base_url=req.base_url or (pcfg.base_url if pcfg else "") or "",
            model=model,
            protocol=protocol or default_protocol_for_provider_type(provider_type),
            api_key=api_key,
        )
    elif provider_type == "anthropic_compatible":
        auth_scheme = req.auth_scheme or (getattr(pcfg, "auth_scheme", "x-api-key") if pcfg else "x-api-key")
        provider = AnthropicCompatibleProvider(
            base_url=req.base_url or (pcfg.base_url if pcfg else "") or "",
            model=model,
            api_key=api_key,
            auth_scheme=auth_scheme,
            protocol=protocol or default_protocol_for_provider_type(provider_type),
        )
    else:
        raise HTTPException(400, f"No test-candidate support for {provider_type}")

    setattr(provider, "_aletheia_provider_name", req.provider)
    candidate_identity = await asyncio.to_thread(
        _candidate_configuration_identity, req, provider_type, pcfg, api_key, protocol
    )
    try:
        health = await provider.healthcheck()
        # /models access is optional: gateways can permit inference while denying or
        # intermittently failing catalogue listing. An explicit probe is the authority.
        if not health.available and not req.probe:
            return {
                "available": False,
                "configured": True,
                "reachable": False,
                "verified": None,
                "verified_at": None,
                "verification_identity": candidate_identity,
                "verification_source": None,
                "state": "unavailable",
                "reason_code": _classify_health_error(health.error),
                "model": health.model,
                "error": health.error,
                "persisted": False,
            }

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
                detail = f"inference failed: {e}"
                verification_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                persisted = await asyncio.to_thread(
                    persist_provider_verification,
                    req.provider,
                    identity=candidate_identity,
                    verified=False,
                    source="explicit_probe",
                    reason_code=_classify_health_error(detail),
                    detail=detail[:200],
                    verified_at=verification_at,
                )
                reachable = bool(health.available)
                return {
                    "available": reachable,
                    "configured": True,
                    "reachable": reachable,
                    "verified": False,
                    "verified_at": verification_at,
                    "verification_identity": candidate_identity,
                    "verification_source": "explicit_probe",
                    "state": "reachable" if reachable else "unavailable",
                    "reason_code": _classify_health_error(detail),
                    "model": health.model,
                    "error": detail,
                    "persisted": persisted,
                }

        persisted = False
        verification_at = None
        if req.probe:
            verification_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            persisted = await asyncio.to_thread(
                persist_provider_verification,
                req.provider,
                identity=candidate_identity,
                verified=True,
                source="explicit_probe",
                reason_code="verified",
                verified_at=verification_at,
            )
        return {
            "available": True,
            "configured": True,
            "reachable": True,
            "verified": True if req.probe else None,
            "verified_at": verification_at,
            "verification_identity": candidate_identity,
            "verification_source": "explicit_probe" if req.probe else None,
            "state": "verified" if req.probe else "reachable",
            "reason_code": "verified" if req.probe else "reachable",
            "model": health.model,
            "error": None,
            "models": health.models,
            "persisted": persisted,
        }
    finally:
        await _close_candidate_provider(provider)


class ModelProfileFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    base_url: str | None = None
    protocol: str | None = None
    model: str | None = None
    enabled: bool | None = None
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
    model_config = ConfigDict(extra="forbid")

    profile_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    display_name: str = Field(min_length=1, max_length=80)
    type: str
    enabled: bool = False
    base_url: str | None = None
    protocol: str | None = None
    model: str | None = None
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


class ModelProfileMutation(ModelProfileFields):
    provider: str
    type: str | None = None


class ModelSelectionRequest(ModelSelection):
    @field_validator("endpoint_id", "model_id")
    @classmethod
    def require_explicit_identity(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Endpoint and model IDs must be nonempty")
        return value


class ModelRoutingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_provider: str | None = None
    deep: ModelSelectionRequest | None = None
    fast: ModelSelectionRequest | None = None


def _resolve_request_type(req: ModelProfileMutation, pcfg) -> str:
    existing_type = (pcfg.type or req.provider) if pcfg is not None else None
    provider_type = req.type or existing_type or (req.provider if req.provider in VALID_PROVIDERS else None)
    if provider_type not in VALID_PROVIDERS:
        raise HTTPException(400, f"Invalid provider type. Choose from: {sorted(VALID_PROVIDERS)}")
    if pcfg is not None and req.type and existing_type != req.type:
        raise HTTPException(422, "A provider profile's type cannot be changed")
    return provider_type


def _profile_api_key_ref(provider_id: str, provider_type: str) -> str:
    is_legacy_identity = provider_id == provider_type
    if is_legacy_identity:
        return f"model.{provider_id}.api_key"
    return f"model.profile.{provider_id}.api_key"


def _candidate_configuration_identity(
    req, provider_type: str, pcfg, api_key: str | None, protocol: str | None = None
) -> str:
    """Build the same identity that a verified profile will persist."""

    from aletheia.config.schema import ProviderConfig

    values = pcfg.model_dump(mode="python") if pcfg is not None else {}
    for field_name in ProviderConfig.model_fields:
        if field_name in req.model_fields and getattr(req, field_name) is not None:
            values[field_name] = getattr(req, field_name)
    values["type"] = provider_type
    if protocol is not None:
        values["protocol"] = protocol
    candidate = ProviderConfig.model_validate(values)
    if api_key:
        candidate.api_key_secret_ref = _profile_api_key_ref(req.provider, provider_type)
        overrides = {candidate.api_key_secret_ref: api_key}
    else:
        overrides = None
    return provider_configuration_identity(
        req.provider,
        candidate,
        credential_overrides=overrides,
    )


def _apply_model_config_sync(req: ModelProfileMutation, verification: dict | None = None):
    if req.api_key is not None:
        req.api_key = req.api_key.strip() or None

    cfg = load_config()
    pcfg = cfg.models.providers.get(req.provider)
    provider_type = _resolve_request_type(req, pcfg)
    identity_before = (
        provider_configuration_identity(req.provider, pcfg) if pcfg is not None else None
    )
    if pcfg is None:
        from aletheia.config.schema import ProviderConfig

        pcfg = ProviderConfig(
            type=provider_type,
            display_name=req.display_name or "",
            protocol=default_protocol_for_provider_type(provider_type),
        )
        cfg.models.providers[req.provider] = pcfg

    try:
        if req.protocol is not None:
            req.protocol = validate_provider_protocol(
                provider_type, req.protocol, explicit=True
            )
        else:
            pcfg.protocol = provider_config_protocol(provider_type, pcfg)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    pcfg.type = provider_type
    for field_name in (
        "display_name",
        "base_url",
        "protocol",
        "model",
        "enabled",
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
    written_secret_ref = None
    previous_secret = None
    secret_override = None
    if req.api_key:
        secret_ref = _profile_api_key_ref(req.provider, provider_type)
        previous_secret = get_secret(secret_ref)
        pcfg.api_key_secret_ref = secret_ref
        secret_override = {secret_ref: req.api_key}

    identity_after = provider_configuration_identity(
        req.provider,
        pcfg,
        credential_overrides=secret_override,
    )
    if verification is not None:
        if verification.get("identity") != identity_after:
            raise HTTPException(409, "Provider configuration changed during verification")
        pcfg.verification_identity = identity_after
        pcfg.verification_verified = bool(verification.get("verified", False))
        pcfg.verification_at = str(
            verification.get("verified_at")
            or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        )
        pcfg.verification_source = str(verification.get("source") or "explicit_probe")
        pcfg.verification_reason_code = str(verification.get("reason_code") or "verified")
        pcfg.verification_detail = verification.get("detail")
    elif identity_before is not None and identity_before != identity_after:
        clear_provider_verification(pcfg)

    try:
        if req.api_key:
            set_secret(secret_ref, req.api_key)
            written_secret_ref = secret_ref
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


async def _apply_model_config(req: ModelProfileMutation, verification: dict | None = None) -> dict:
    cfg, pcfg = await asyncio.to_thread(_apply_model_config_sync, req, verification)
    await clear_cache_async()
    from aletheia.model_provider.broker import inference_broker

    await inference_broker.invalidate_provider(req.provider)
    key_status = await asyncio.to_thread(_key_status, pcfg.api_key_secret_ref)
    return {
        "provider": req.provider,
        "default_provider": cfg.models.default_provider,
        "deep": cfg.models.deep,
        "fast": cfg.models.fast,
        "api_key": key_status,
        "verification": {
            "verified": pcfg.verification_verified,
            "verified_at": pcfg.verification_at,
            "verification_identity": pcfg.verification_identity,
            "source": pcfg.verification_source,
        },
    }


async def _verify_profile_candidate(profile_id: str, provider_type: str, payload: dict) -> dict:
    candidate_fields = set(TestCandidateRequest.model_fields) - {"provider", "type", "probe"}
    normalized = dict(payload)
    if isinstance(normalized.get("api_key"), str):
        normalized["api_key"] = normalized["api_key"].strip() or None
    candidate = TestCandidateRequest(
        provider=profile_id,
        type=provider_type,
        probe=True,
        **{key: value for key, value in normalized.items() if key in candidate_fields},
    )
    result = await test_model_candidate(candidate)
    if not result.get("verified"):
        raise HTTPException(
            422,
            {
                "code": result.get("reason_code") or "verification_failed",
                "message": "Model profile verification failed",
                "detail": result.get("error"),
            },
        )
    return {
        "identity": result.get("verification_identity"),
        "verified": True,
        "verified_at": result.get("verified_at"),
        "source": "settings.model_profile",
        "reason_code": "verified",
    }


@router.post("/settings/model-profiles", status_code=201)
async def create_model_profile(req: CreateModelProfileRequest):
    if req.type not in API_PROVIDER_TYPES:
        raise HTTPException(422, "Only API provider types can be created as profiles")
    cfg = await asyncio.to_thread(load_config)
    if req.profile_id in cfg.models.providers:
        raise HTTPException(409, f"Model provider profile already exists: {req.profile_id}")
    payload = req.model_dump(exclude={"profile_id"})
    verification = await _verify_profile_candidate(req.profile_id, req.type, payload)
    return await _apply_model_config(
        ModelProfileMutation(provider=req.profile_id, **payload), verification
    )


@router.patch("/settings/model-profiles/{profile_id}")
async def update_model_profile(profile_id: str, req: UpdateModelProfileRequest):
    cfg = await asyncio.to_thread(load_config)
    pcfg = cfg.models.providers.get(profile_id)
    if pcfg is None:
        raise HTTPException(404, f"Unknown model provider profile: {profile_id}")
    provider_type = pcfg.type or profile_id
    if provider_type not in API_PROVIDER_TYPES:
        raise HTTPException(422, "Non-API integrations are not editable API profiles")
    payload = req.model_dump(exclude_unset=True)
    verification_fields = {"base_url", "protocol", "model", "api_key", "auth_scheme"}
    verification = None
    if verification_fields.intersection(payload) or bool(payload.get("enabled")):
        verification = await _verify_profile_candidate(profile_id, provider_type, payload)
    return await _apply_model_config(
        ModelProfileMutation(provider=profile_id, **payload), verification
    )


def _update_model_routing_sync(req: ModelRoutingRequest, verifications: dict) -> dict:
    changed = {}
    result = {}

    def apply(cfg):
        for field_name in req.model_fields_set:
            value = getattr(req, field_name)
            endpoint = value.endpoint_id if isinstance(value, ModelSelection) else value
            if endpoint and endpoint not in cfg.models.providers:
                raise HTTPException(404, f"Unknown model provider: {endpoint}")
            if endpoint:
                pcfg = cfg.models.providers[endpoint]
                if (pcfg.type or endpoint) not in VALID_PROVIDERS:
                    raise HTTPException(422, f"Unsupported model provider: {endpoint}")
                if isinstance(value, ModelSelection):
                    identity = provider_configuration_identity(
                        endpoint, pcfg.model_copy(update={"model": value.model_id}),
                    )
                    if identity != verifications[field_name]["identity"]:
                        raise HTTPException(409, "Model endpoint changed during routing verification")
            setattr(cfg.models, field_name, (value or "") if field_name == "default_provider" else value)
            changed[field_name] = value.model_dump() if isinstance(value, ModelSelection) else value
        result.update(
            default_provider=cfg.models.default_provider,
            deep=cfg.models.deep,
            fast=cfg.models.fast,
        )
        return bool(changed)

    mutate_config(apply)
    if changed:
        write_audit({
            "actor": "human", "source": "dashboard", "event_type": "model_routing_changed",
            "entity_type": "model_routing", "entity_id": "models",
            "message": "Model routing roles updated", "after": changed,
            "verification": verifications,
        })
    return result


@router.post("/settings/model-routing")
async def update_model_routing(req: ModelRoutingRequest):
    cfg = await asyncio.to_thread(load_config)
    verifications = {}
    for tier in ("deep", "fast"):
        selected = getattr(req, tier)
        if selected is None:
            continue
        pcfg = cfg.models.providers.get(selected.endpoint_id)
        if pcfg is None:
            raise HTTPException(404, f"Unknown model provider: {selected.endpoint_id}")
        provider_type = pcfg.type or selected.endpoint_id
        if provider_type not in VALID_PROVIDERS:
            raise HTTPException(422, f"Unsupported model provider: {selected.endpoint_id}")
        verifications[tier] = await _verify_profile_candidate(
            selected.endpoint_id, provider_type, {"model": selected.model_id},
        )
    result = await asyncio.to_thread(_update_model_routing_sync, req, verifications)
    clear_cache()
    return result


def _delete_model_profile_sync(profile_id: str) -> dict:
    cfg = load_config()
    pcfg = cfg.models.providers.get(profile_id)
    if pcfg is None:
        raise HTTPException(404, f"Unknown model provider profile: {profile_id}")
    if (pcfg.type or profile_id) not in API_PROVIDER_TYPES:
        raise HTTPException(422, "Non-API integrations cannot be deleted as API profiles")

    cleared_roles: list[str] = []
    if cfg.models.default_provider == profile_id:
        cfg.models.default_provider = ""
        cleared_roles.append("default")
    if cfg.models.deep and cfg.models.deep.endpoint_id == profile_id:
        cfg.models.deep = None
        cleared_roles.append("deep")
    if cfg.models.fast and cfg.models.fast.endpoint_id == profile_id:
        cfg.models.fast = None
        cleared_roles.append("fast")

    secret_refs = [pcfg.api_key_secret_ref] if pcfg.api_key_secret_ref else []
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
