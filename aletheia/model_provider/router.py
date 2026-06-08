"""Model provider router — creates and caches provider instances from config."""

import asyncio

from aletheia.config.loader import load_config
from aletheia.model_provider.base import ModelProvider, ModelHealth
from aletheia.model_provider.openai_compatible import OpenAICompatibleProvider
from aletheia.model_provider.anthropic_compatible import AnthropicCompatibleProvider
from aletheia.model_provider.databricks import DatabricksProvider
from aletheia.model_provider.codex_cli import CodexCLIProvider
from aletheia.model_provider.claude_cli import ClaudeCLIProvider

PROVIDER_REGISTRY: dict[str, type[ModelProvider]] = {
    "openai_compatible": OpenAICompatibleProvider,
    "anthropic_compatible": AnthropicCompatibleProvider,
    "databricks": DatabricksProvider,
    "codex_cli": CodexCLIProvider,
    "claude_cli": ClaudeCLIProvider,
}

_cache: dict[str, ModelProvider] = {}


def _resolve_type(name: str, pcfg) -> str:
    """Resolve provider type from config. Falls back to key name for backward compat."""
    if pcfg.type and pcfg.type in PROVIDER_REGISTRY:
        return pcfg.type
    if name in PROVIDER_REGISTRY:
        return name
    raise ValueError(f"Cannot resolve provider type for: {name}")


def get_provider(name: str | None = None) -> ModelProvider:
    """Get a provider by name, or the default."""
    cfg = load_config()
    if name is None:
        name = cfg.models.default_provider

    if name in _cache:
        return _cache[name]

    pcfg = cfg.models.providers.get(name)
    if pcfg is None:
        raise ValueError(f"Unknown provider: {name}")

    api_key = None
    if pcfg.api_key_secret_ref:
        from aletheia.config.secrets import get_secret
        api_key = get_secret(pcfg.api_key_secret_ref)

    ptype = _resolve_type(name, pcfg)

    if ptype == "openai_compatible":
        provider = OpenAICompatibleProvider(
            base_url=pcfg.base_url,
            model=pcfg.model,
            protocol=pcfg.protocol,
            api_key=api_key,
        )
    elif ptype == "anthropic_compatible":
        provider = AnthropicCompatibleProvider(
            base_url=pcfg.base_url,
            model=pcfg.model,
            api_key=api_key,
        )
    elif ptype == "databricks":
        provider = DatabricksProvider(
            workspace_url=pcfg.workspace_url or pcfg.base_url,
            model=pcfg.model,
            api_key=api_key,
        )
    elif ptype == "codex_cli":
        provider = CodexCLIProvider(
            command=pcfg.command,
            mode=pcfg.mode,
            enabled=pcfg.enabled,
            model=pcfg.codex_model,
        )
    elif ptype == "claude_cli":
        provider = ClaudeCLIProvider(
            command=pcfg.command or "claude",
            mode=pcfg.mode or "print",
            enabled=pcfg.enabled,
            model=pcfg.model if pcfg.model != "default" else None,
        )
    else:
        raise ValueError(f"Unknown provider type: {ptype}")

    _cache[name] = provider
    return provider


def clear_cache() -> None:
    _cache.clear()


_HTTP_TYPES = {"openai_compatible", "anthropic_compatible", "databricks"}
_DEFAULT_URLS = {"http://localhost:8000/v1", "http://localhost:8001", ""}


async def healthcheck_all() -> list[ModelHealth]:
    """Run healthcheck on all configured providers concurrently (K8s-style fan-out)."""
    cfg = load_config()
    tasks: list = []
    for name, pcfg in cfg.models.providers.items():
        if not pcfg.enabled:
            continue
        ptype = pcfg.type or name
        if ptype in _HTTP_TYPES:
            has_key = bool(pcfg.api_key_secret_ref)
            # For Databricks the canonical URL is workspace_url, not base_url.
            # Check the right field to avoid skipping a configured-but-keyless instance.
            if ptype == "databricks":
                is_default_url = not (pcfg.workspace_url or pcfg.base_url)
            else:
                is_default_url = pcfg.base_url in _DEFAULT_URLS
            if not has_key and is_default_url:
                continue
        tasks.append(_safe_healthcheck(name))
    if not tasks:
        return []
    return list(await asyncio.gather(*tasks))


async def _safe_healthcheck(name: str) -> ModelHealth:
    """Single-provider healthcheck with catch-all."""
    try:
        provider = get_provider(name)
        return await provider.healthcheck()
    except Exception as e:
        return ModelHealth(provider=name, available=False, error=str(e))
