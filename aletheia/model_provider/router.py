"""Model provider router — creates and caches provider instances from config."""

from aletheia.config.loader import load_config
from aletheia.model_provider.base import ModelProvider, ModelHealth
from aletheia.model_provider.openai_compatible import OpenAICompatibleProvider
from aletheia.model_provider.anthropic_compatible import AnthropicCompatibleProvider
from aletheia.model_provider.codex_cli import CodexCLIProvider

_cache: dict[str, ModelProvider] = {}


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

    if name == "openai_compatible":
        provider = OpenAICompatibleProvider(
            base_url=pcfg.base_url,
            model=pcfg.model,
            protocol=pcfg.protocol,
        )
    elif name == "anthropic_compatible":
        provider = AnthropicCompatibleProvider(
            base_url=pcfg.base_url,
            model=pcfg.model,
        )
    elif name == "codex_cli":
        provider = CodexCLIProvider(
            command=pcfg.command,
            mode=pcfg.mode,
            enabled=pcfg.enabled,
        )
    else:
        raise ValueError(f"Unknown provider type: {name}")

    _cache[name] = provider
    return provider


def clear_cache() -> None:
    _cache.clear()


async def healthcheck_all() -> list[ModelHealth]:
    """Run healthcheck on all configured providers."""
    cfg = load_config()
    results = []
    for name in cfg.models.providers:
        try:
            provider = get_provider(name)
            health = await provider.healthcheck()
        except Exception as e:
            health = ModelHealth(provider=name, available=False, error=str(e))
        results.append(health)
    return results
