"""Model provider router — creates and caches provider instances from config."""

import asyncio
import logging
import time

from aletheia.config.loader import load_config, save_config
from aletheia.model_provider.base import ModelProvider, ModelHealth
from aletheia.model_provider.openai_compatible import OpenAICompatibleProvider
from aletheia.model_provider.anthropic_compatible import AnthropicCompatibleProvider
from aletheia.model_provider.anthropic_native import AnthropicNativeProvider
from aletheia.model_provider.openai_native import OpenAINativeProvider
from aletheia.model_provider.codex_cli import CodexCLIProvider
from aletheia.model_provider.claude_cli import ClaudeCLIProvider

logger = logging.getLogger("aletheia.model_provider.router")

PROVIDER_REGISTRY: dict[str, type[ModelProvider]] = {
    "openai_compatible": OpenAICompatibleProvider,
    "anthropic_compatible": AnthropicCompatibleProvider,
    "anthropic_native": AnthropicNativeProvider,
    "openai_native": OpenAINativeProvider,
    "codex_cli": CodexCLIProvider,
    "claude_cli": ClaudeCLIProvider,
}

_cache: dict[str, ModelProvider] = {}

_REFRESH_MARGIN_MS = 5 * 60 * 1000  # refresh 5 min before expiry


def _resolve_oauth_token(name: str, pcfg) -> str | None:
    """Load OAuth token from secrets, auto-refreshing if near expiry."""
    if pcfg.auth_type != "oauth" or not pcfg.oauth_token_ref:
        return None

    from aletheia.config.secrets import get_secret, set_secret

    token = get_secret(pcfg.oauth_token_ref)
    if not token:
        return None

    # Check expiry — refresh proactively
    now_ms = int(time.time() * 1000)
    if pcfg.oauth_expires_at and now_ms > (pcfg.oauth_expires_at - _REFRESH_MARGIN_MS):
        refresh_val = get_secret(pcfg.oauth_refresh_ref) if pcfg.oauth_refresh_ref else None
        if refresh_val:
            try:
                new_token = _do_refresh(name, pcfg, refresh_val)
                if new_token:
                    token = new_token
            except Exception as e:
                logger.warning("OAuth token refresh failed for %s: %s", name, e)
    return token


def _do_refresh(name: str, pcfg, refresh_val: str) -> str | None:
    """Synchronously run async refresh (called from get_provider hot path)."""
    import asyncio
    from aletheia.config.secrets import set_secret

    loop = None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        pass

    # If we're already in an async context, can't block — return stale token
    if loop and loop.is_running():
        logger.debug("Skipping sync refresh for %s (in async context)", name)
        return None

    if name == "openai_native":
        from aletheia.model_provider.openai_oauth import refresh_openai_tokens
        result = asyncio.run(refresh_openai_tokens(refresh_val))
        new_token = result.get("api_key")
    elif name == "anthropic_native":
        from aletheia.model_provider.claude_oauth import refresh_claude_tokens
        result = asyncio.run(refresh_claude_tokens(refresh_val))
        new_token = result.get("access_token")
    else:
        return None

    if new_token:
        set_secret(pcfg.oauth_token_ref, new_token)
        if result.get("refresh_token") and pcfg.oauth_refresh_ref:
            set_secret(pcfg.oauth_refresh_ref, result["refresh_token"])
        if result.get("expires_at"):
            pcfg.oauth_expires_at = result["expires_at"]
            cfg = load_config()
            if name in cfg.models.providers:
                cfg.models.providers[name].oauth_expires_at = result["expires_at"]
                save_config(cfg)
        return new_token
    return None


async def _resolve_oauth_token_async(name: str, pcfg) -> str | None:
    """Async version of _resolve_oauth_token — can actually refresh in async context."""
    if pcfg.auth_type != "oauth" or not pcfg.oauth_token_ref:
        return None

    from aletheia.config.secrets import get_secret

    token = get_secret(pcfg.oauth_token_ref)
    if not token:
        return None

    now_ms = int(time.time() * 1000)
    if pcfg.oauth_expires_at and now_ms > (pcfg.oauth_expires_at - _REFRESH_MARGIN_MS):
        refresh_val = get_secret(pcfg.oauth_refresh_ref) if pcfg.oauth_refresh_ref else None
        if refresh_val:
            try:
                new_token = await _do_refresh_async(name, pcfg, refresh_val)
                if new_token:
                    token = new_token
            except Exception as e:
                logger.warning("Async OAuth token refresh failed for %s: %s", name, e)
    return token


async def _do_refresh_async(name: str, pcfg, refresh_val: str) -> str | None:
    """Actually refresh OAuth token asynchronously."""
    from aletheia.config.secrets import set_secret

    if name == "openai_native":
        from aletheia.model_provider.openai_oauth import refresh_openai_tokens
        result = await refresh_openai_tokens(refresh_val)
        new_token = result.get("api_key")
    elif name == "anthropic_native":
        from aletheia.model_provider.claude_oauth import refresh_claude_tokens
        result = await refresh_claude_tokens(refresh_val)
        new_token = result.get("access_token")
    else:
        return None

    if new_token:
        set_secret(pcfg.oauth_token_ref, new_token)
        if result.get("refresh_token") and pcfg.oauth_refresh_ref:
            set_secret(pcfg.oauth_refresh_ref, result["refresh_token"])
        if result.get("expires_at"):
            pcfg.oauth_expires_at = result["expires_at"]
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _persist_oauth_expiry, name, result["expires_at"])
        return new_token
    return None


def _persist_oauth_expiry(name: str, expires_at: int) -> None:
    """Persist OAuth expiry to config (runs in executor to avoid blocking loop)."""
    cfg = load_config()
    if name in cfg.models.providers:
        cfg.models.providers[name].oauth_expires_at = expires_at
        save_config(cfg)


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
            auth_scheme=getattr(pcfg, "auth_scheme", "x-api-key"),
        )
    elif ptype == "codex_cli":
        provider = CodexCLIProvider(
            command=pcfg.command,
            mode=pcfg.mode,
            enabled=pcfg.enabled,
            model=pcfg.codex_model,
        )
    elif ptype == "anthropic_native":
        oauth_token = _resolve_oauth_token(name, pcfg)
        provider = AnthropicNativeProvider(model=pcfg.model, api_key=api_key, oauth_token=oauth_token)
    elif ptype == "openai_native":
        oauth_token = _resolve_oauth_token(name, pcfg)
        provider = OpenAINativeProvider(model=pcfg.model, api_key=api_key, oauth_token=oauth_token)
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


async def get_provider_async(name: str | None = None) -> ModelProvider:
    """Async variant of get_provider — uses async OAuth refresh path."""
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
            base_url=pcfg.base_url, model=pcfg.model,
            protocol=pcfg.protocol, api_key=api_key,
        )
    elif ptype == "anthropic_compatible":
        provider = AnthropicCompatibleProvider(
            base_url=pcfg.base_url, model=pcfg.model, api_key=api_key,
            auth_scheme=getattr(pcfg, "auth_scheme", "x-api-key"),
        )
    elif ptype == "codex_cli":
        provider = CodexCLIProvider(
            command=pcfg.command, mode=pcfg.mode,
            enabled=pcfg.enabled, model=pcfg.codex_model,
        )
    elif ptype == "anthropic_native":
        oauth_token = await _resolve_oauth_token_async(name, pcfg)
        provider = AnthropicNativeProvider(model=pcfg.model, api_key=api_key, oauth_token=oauth_token)
    elif ptype == "openai_native":
        oauth_token = await _resolve_oauth_token_async(name, pcfg)
        provider = OpenAINativeProvider(model=pcfg.model, api_key=api_key, oauth_token=oauth_token)
    elif ptype == "claude_cli":
        provider = ClaudeCLIProvider(
            command=pcfg.command or "claude", mode=pcfg.mode or "print",
            enabled=pcfg.enabled,
            model=pcfg.model if pcfg.model != "default" else None,
        )
    else:
        raise ValueError(f"Unknown provider type: {ptype}")

    _cache[name] = provider
    return provider


def clear_cache() -> None:
    _cache.clear()


_HTTP_TYPES = {"openai_compatible", "anthropic_compatible", "anthropic_native", "openai_native"}
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
            has_credential = bool(pcfg.api_key_secret_ref) or (
                pcfg.auth_type == "oauth" and bool(pcfg.oauth_token_ref)
            )
            is_default_url = pcfg.base_url in _DEFAULT_URLS
            if not has_credential and is_default_url:
                continue
        tasks.append(_safe_healthcheck(name))
    if not tasks:
        return []
    return list(await asyncio.gather(*tasks))


async def _safe_healthcheck(name: str) -> ModelHealth:
    """Single-provider healthcheck with catch-all. Uses async OAuth refresh path."""
    try:
        provider = await get_provider_async(name)
        return await provider.healthcheck()
    except Exception as e:
        return ModelHealth(provider=name, available=False, error=str(e))
