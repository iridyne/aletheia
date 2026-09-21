"""HTTP Extension credential ingress; routing roles remain Core-owned."""

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from aletheia.config.loader import load_config, save_config
from aletheia.config.secrets import delete_secret, get_secret, set_secret
from aletheia.files.jsonl import write_audit
from aletheia.model_provider.registry import (
    API_PROVIDER_TYPES, default_protocol_for_provider_type,
    provider_config_protocol, validate_provider_protocol,
)
from aletheia.model_provider.router import (
    clear_cache_async, clear_provider_verification, _classify_health_error,
    persist_provider_verification, provider_configuration_identity,
    provider_credential_status, clear_model_profile_roles, apply_provider_verification,
)

router = APIRouter(prefix="/api", tags=["model"])
VALID_PROVIDERS = API_PROVIDER_TYPES


async def _close_candidate_provider(provider) -> None:
    """Close every loop-owned pool used by an ephemeral candidate probe."""

    from aletheia.model_provider.broker import inference_broker

    if inference_broker.running:
        await inference_broker.run_auxiliary(provider.aclose)
    await provider.aclose()



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
    from extensions.model_endpoints.openai_compatible import OpenAICompatibleProvider
    from extensions.model_endpoints.anthropic_compatible import AnthropicCompatibleProvider
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
    from extensions.model_endpoints.openai_compatible import OpenAICompatibleProvider
    from extensions.model_endpoints.anthropic_compatible import AnthropicCompatibleProvider
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
    from extensions.model_endpoints.http_runtime import http_configuration_identity

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
    return http_configuration_identity(
        req.provider,
        candidate,
        credential_overrides=overrides,
    )



def _apply_model_config_sync(req: ModelProfileMutation, verification: dict | None = None):
    from extensions.model_endpoints.http_runtime import http_configuration_identity

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

    identity_after = http_configuration_identity(
        req.provider,
        pcfg,
        credential_overrides=secret_override,
    )
    if verification is not None:
        if not apply_provider_verification(
            pcfg, current_identity=identity_after, identity=verification.get("identity"),
            verified=bool(verification.get("verified", False)),
            verified_at=str(verification["verified_at"]) if verification.get("verified_at") else None,
            source=str(verification.get("source") or "explicit_probe"),
            reason_code=str(verification.get("reason_code") or "verified"),
            detail=verification.get("detail"),
        ):
            raise HTTPException(409, "Provider configuration changed during verification")
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
    key_status = await asyncio.to_thread(provider_credential_status, req.provider, pcfg)
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



def _delete_model_profile_sync(profile_id: str) -> dict:
    cfg = load_config()
    pcfg = cfg.models.providers.get(profile_id)
    if pcfg is None:
        raise HTTPException(404, f"Unknown model provider profile: {profile_id}")
    if (pcfg.type or profile_id) not in API_PROVIDER_TYPES:
        raise HTTPException(422, "Non-API integrations cannot be deleted as API profiles")

    cleared_roles = clear_model_profile_roles(cfg, profile_id)

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
