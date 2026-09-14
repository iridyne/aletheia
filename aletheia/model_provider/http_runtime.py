"""HTTP construction and credential-backed identity; Core owns routing and lifetime."""

import hashlib
import json

from aletheia.config.secrets import get_secret
from aletheia.model_provider.registry import API_PROVIDER_TYPES, provider_config_protocol


def http_response_facts(data: dict) -> dict:
    """Normalize HTTP completion evidence before it reaches Core policy consumers."""
    choices = data.get("choices")
    choices = choices if isinstance(choices, list) else []
    first = choices[0] if choices and isinstance(choices[0], dict) else {}
    stop_reason = data.get("stop_reason")
    confirmed = any(
        isinstance(choice, dict)
        and isinstance(choice.get("finish_reason"), str)
        and bool(choice["finish_reason"].strip())
        for choice in choices
    ) or data.get("status") == "completed" or (
        isinstance(stop_reason, str)
        and stop_reason in {"end_turn", "max_tokens", "stop_sequence", "tool_use"}
    )
    reason = first.get("finish_reason") if choices else stop_reason
    incomplete = data.get("incomplete_details")
    if isinstance(incomplete, dict) and incomplete.get("reason") == "max_output_tokens":
        reason = "incomplete"
    usage = data.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    output_tokens = usage.get("completion_tokens", usage.get("output_tokens"))
    return {
        "completion_confirmed": confirmed,
        "finish_reason": reason if isinstance(reason, str) and reason in {"stop", "length", "max_tokens", "incomplete"} else "unknown",
        "output_tokens": output_tokens if type(output_tokens) is int and output_tokens >= 0 else None,
    }


def http_configuration_identity(
    name: str,
    pcfg,
    *,
    credential_overrides: dict[str, str | None] | None = None,
) -> str:
    """Fingerprint one endpoint; only credential ingress supplies candidate overrides."""

    provider_type = str(getattr(pcfg, "type", "") or name)
    refs = (
        getattr(pcfg, "api_key_secret_ref", None),
    ) if provider_type in API_PROVIDER_TYPES else ()
    credential_fingerprints: list[dict[str, str | None]] = []
    for ref in refs:
        if not ref:
            continue
        if credential_overrides is not None and ref in credential_overrides:
            value = credential_overrides[ref]
        else:
            value = get_secret(ref)
        credential_fingerprints.append(
            {
                "ref": ref,
                "sha256": hashlib.sha256((value or "").encode("utf-8")).hexdigest()
                if value
                else None,
            }
        )
    payload = {
        "provider": name,
        "type": str(getattr(pcfg, "type", "") or name),
        "base_url": str(getattr(pcfg, "base_url", "") or ""),
        "workspace_url": str(getattr(pcfg, "workspace_url", "") or ""),
        "protocol": provider_config_protocol(str(getattr(pcfg, "type", "") or name), pcfg),
        "model": str(getattr(pcfg, "model", "") or ""),
        "auth_scheme": str(getattr(pcfg, "auth_scheme", "") or ""),
        "auth_type": str(getattr(pcfg, "auth_type", "") or ""),
        # Historical identity inputs only; no transport consumes these fields.
        "command": str(getattr(pcfg, "command", "") or ""),
        "mode": str(getattr(pcfg, "mode", "") or ""),
        "codex_model": str(getattr(pcfg, "codex_model", "") or ""),
        "api_key_secret_ref": getattr(pcfg, "api_key_secret_ref", None),
        "oauth_token_ref": getattr(pcfg, "oauth_token_ref", None),
        "oauth_refresh_ref": getattr(pcfg, "oauth_refresh_ref", None),
        "credential_fingerprints": credential_fingerprints,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def http_credential_status(name: str, pcfg) -> dict:
    """Expose only selected-endpoint presence and masking metadata."""
    if (pcfg.type or name) not in API_PROVIDER_TYPES or not pcfg.api_key_secret_ref:
        return {"configured": False, "last4": None}
    try:
        value = get_secret(pcfg.api_key_secret_ref, strict=True)
    except Exception:  # Secret-store failure is unknown, never absent or exception text.
        return {"configured": None, "last4": None, "unavailable_reason": "model_credentials_unavailable"}
    return {
        "configured": bool(value),
        "last4": (value[-4:] if len(value) >= 4 else "****") if value else None,
    }


async def verify_http_model_selection(endpoint_id: str, provider_type: str, model_id: str) -> dict:
    """Reuse the credential-ingress probe for one configured endpoint/model pair."""
    try:
        from aletheia.model_provider.credential_routes import _verify_profile_candidate
    except ModuleNotFoundError as exc:
        if exc.name != "aletheia.model_provider.credential_routes":
            raise
        from aletheia.model_provider.router import ModelAdapterUnavailable

        raise ModelAdapterUnavailable("model_adapter_unavailable") from None
    return await _verify_profile_candidate(endpoint_id, provider_type, {"model": model_id})


def build_http_provider(name, pcfg):
    """Resolve only this selected endpoint's API credential reference."""
    provider_type = pcfg.type or name
    if provider_type not in API_PROVIDER_TYPES:
        raise ValueError(f"Cannot resolve provider type for: {name}")
    api_key = get_secret(pcfg.api_key_secret_ref) if pcfg.api_key_secret_ref else None
    protocol = provider_config_protocol(provider_type, pcfg)
    if provider_type == "openai_compatible":
        from aletheia.model_provider.openai_compatible import OpenAICompatibleProvider

        return OpenAICompatibleProvider(
            base_url=pcfg.base_url, model=pcfg.model, protocol=protocol, api_key=api_key,
        )
    if provider_type == "anthropic_compatible":
        from aletheia.model_provider.anthropic_compatible import AnthropicCompatibleProvider

        return AnthropicCompatibleProvider(
            base_url=pcfg.base_url, model=pcfg.model, api_key=api_key,
            auth_scheme=getattr(pcfg, "auth_scheme", "x-api-key"), protocol=protocol,
        )
    raise ValueError("model_adapter_unavailable")
