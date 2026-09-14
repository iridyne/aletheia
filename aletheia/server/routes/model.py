"""Core model metadata and routing API; credential ingress lives in Model."""

import asyncio
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException  # pyright: ignore[reportMissingImports]
from pydantic import BaseModel, ConfigDict, Field, field_validator

from aletheia.config.loader import load_config, mutate_config
from aletheia.config.schema import ModelSelection
from aletheia.files.jsonl import write_audit
from aletheia.model_provider.base import MODEL_HEALTH_STALE_AFTER_SECONDS
from aletheia.model_provider.registry import (  # pyright: ignore[reportMissingImports]
    API_PROVIDER_TYPES,
    PROVIDER_TYPES,
    provider_kind,
)
from aletheia.model_provider.router import (
    clear_cache,
    _classify_health_error,
    healthcheck_all_with_metadata,
    persist_provider_verification,
    provider_configuration_identity,
    provider_credential_status,
    provider_is_configured,
    ModelAdapterUnavailable,
    verify_model_selection,
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
            "api_key": provider_credential_status(name, pcfg),
            "auth_type": pcfg.auth_type,
            "oauth": {"configured": False, "last4": None},
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
        if not content and not resp.completion_confirmed:
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
        try:
            verifications[tier] = await verify_model_selection(
                selected.endpoint_id, provider_type, selected.model_id,
            )
        except ModelAdapterUnavailable:
            raise HTTPException(503, "model_adapter_unavailable") from None
    result = await asyncio.to_thread(_update_model_routing_sync, req, verifications)
    clear_cache()
    return result
