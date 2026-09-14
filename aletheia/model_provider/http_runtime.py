"""Configured HTTP endpoint construction; Core owns routing and cached lifetime."""

from aletheia.config.secrets import get_secret
from aletheia.model_provider.registry import API_PROVIDER_TYPES, provider_config_protocol


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
