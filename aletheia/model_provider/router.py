"""Model provider router — creates and caches provider instances from config."""

import asyncio
from concurrent.futures import Future
import logging
import threading
import time

from aletheia.config.loader import load_config, save_config
from aletheia.model_provider.base import ModelProvider, ModelHealth
from aletheia.model_provider.openai_compatible import OpenAICompatibleProvider
from aletheia.model_provider.anthropic_compatible import AnthropicCompatibleProvider
from aletheia.model_provider.databricks import DatabricksProvider
from aletheia.model_provider.anthropic_native import AnthropicNativeProvider
from aletheia.model_provider.openai_native import OpenAINativeProvider
from aletheia.model_provider.codex_cli import CodexCLIProvider
from aletheia.model_provider.claude_cli import ClaudeCLIProvider
from aletheia.model_provider.pi_cli import PiCLIProvider

logger = logging.getLogger("aletheia.model_provider.router")

PROVIDER_REGISTRY: dict[str, type[ModelProvider]] = {
    "openai_compatible": OpenAICompatibleProvider,
    "anthropic_compatible": AnthropicCompatibleProvider,
    "databricks": DatabricksProvider,
    "anthropic_native": AnthropicNativeProvider,
    "openai_native": OpenAINativeProvider,
    "codex_cli": CodexCLIProvider,
    "claude_cli": ClaudeCLIProvider,
    "pi_cli": PiCLIProvider,
}

_cache: dict[str, ModelProvider] = {}

_MODEL_DIRECTORY_TTL_SEC = 15 * 60
_model_directory_cache: dict[str, tuple[float, list[dict]]] = {}
_model_directory_inflight: dict[str, Future[list[dict]]] = {}
_model_directory_lock = threading.Lock()
_HEALTH_TTL_SEC = 30.0
_health_cache: tuple[float, list[ModelHealth]] | None = None
_health_inflight: Future[list[ModelHealth]] | None = None
_health_lock = threading.Lock()

_REFRESH_MARGIN_MS = 5 * 60 * 1000  # refresh 5 min before expiry
_oauth_refresh_inflight: dict[str, Future[str | None]] = {}
_oauth_refresh_lock = threading.Lock()


def _claim_oauth_refresh(name: str) -> tuple[Future[str | None], bool]:
    with _oauth_refresh_lock:
        existing = _oauth_refresh_inflight.get(name)
        if existing is not None:
            return existing, False
        future: Future[str | None] = Future()
        _oauth_refresh_inflight[name] = future
        return future, True


def _release_oauth_refresh(name: str, future: Future[str | None]) -> None:
    with _oauth_refresh_lock:
        if _oauth_refresh_inflight.get(name) is future:
            del _oauth_refresh_inflight[name]


def _refresh_oauth_singleflight(name: str) -> str | None:
    future, owner = _claim_oauth_refresh(name)
    if not owner:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            return None
        return future.result()
    try:
        from aletheia.config.secrets import get_secret

        fresh = load_config().models.providers.get(name)
        if fresh is None or fresh.auth_type != "oauth" or not fresh.oauth_token_ref:
            result = None
        elif not fresh.oauth_expires_at or int(time.time() * 1000) <= (fresh.oauth_expires_at - _REFRESH_MARGIN_MS):
            result = get_secret(fresh.oauth_token_ref)
        else:
            refresh_val = get_secret(fresh.oauth_refresh_ref) if fresh.oauth_refresh_ref else None
            result = _do_refresh(name, fresh, refresh_val) if refresh_val else None
        future.set_result(result)
        return result
    except BaseException as exc:
        future.set_exception(exc)
        raise
    finally:
        _release_oauth_refresh(name, future)


async def _refresh_oauth_singleflight_async(name: str) -> str | None:
    future, owner = _claim_oauth_refresh(name)
    if not owner:
        return await asyncio.shield(asyncio.wrap_future(future))
    try:
        from aletheia.config.secrets import get_secret

        fresh = await asyncio.to_thread(lambda: load_config().models.providers.get(name))
        if fresh is None or fresh.auth_type != "oauth" or not fresh.oauth_token_ref:
            result = None
        elif not fresh.oauth_expires_at or int(time.time() * 1000) <= (fresh.oauth_expires_at - _REFRESH_MARGIN_MS):
            result = await asyncio.to_thread(get_secret, fresh.oauth_token_ref)
        else:
            refresh_val = await asyncio.to_thread(get_secret, fresh.oauth_refresh_ref) if fresh.oauth_refresh_ref else None
            result = await _do_refresh_async(name, fresh, refresh_val) if refresh_val else None
        future.set_result(result)
        return result
    except asyncio.CancelledError:
        future.cancel()
        raise
    except BaseException as exc:
        future.set_exception(exc)
        raise
    finally:
        _release_oauth_refresh(name, future)


def _resolve_oauth_token(name: str, pcfg) -> str | None:
    """Load OAuth token from secrets, auto-refreshing if near expiry."""
    if pcfg.auth_type != "oauth" or not pcfg.oauth_token_ref:
        return None

    from aletheia.config.secrets import get_secret

    token = get_secret(pcfg.oauth_token_ref)
    if not token:
        return None

    # Check expiry — refresh proactively
    now_ms = int(time.time() * 1000)
    if pcfg.oauth_expires_at and now_ms > (pcfg.oauth_expires_at - _REFRESH_MARGIN_MS):
        try:
            new_token = _refresh_oauth_singleflight(name)
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
        try:
            new_token = await _refresh_oauth_singleflight_async(name)
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
    """Get an enabled provider by name, or the configured default."""
    cfg = load_config()
    if name is None:
        name = cfg.models.default_provider

    if not name:
        raise ValueError(
            "No LLM provider configured. Add and enable one under Settings → AI Model Providers before running agents."
        )

    pcfg = cfg.models.providers.get(name)
    if pcfg is None:
        raise ValueError(f"Unknown provider: {name}")
    if not pcfg.enabled:
        raise ValueError(f"Model provider is disabled: {name}")

    if name in _cache:
        return _cache[name]

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
    elif ptype == "databricks":
        provider = DatabricksProvider(
            workspace_url=pcfg.workspace_url or pcfg.base_url,
            model=pcfg.model,
            protocol=pcfg.protocol or "serving_endpoint",
            api_key=api_key,
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
    elif ptype == "pi_cli":
        provider = PiCLIProvider(
            command=pcfg.command or "pi",
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

    if not name:
        raise ValueError(
            "No LLM provider configured. Add and enable one under Settings → AI Model Providers before running agents."
        )

    pcfg = cfg.models.providers.get(name)
    if pcfg is None:
        raise ValueError(f"Unknown provider: {name}")
    if not pcfg.enabled:
        raise ValueError(f"Model provider is disabled: {name}")

    if name in _cache:
        return _cache[name]

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
    elif ptype == "databricks":
        provider = DatabricksProvider(
            workspace_url=pcfg.workspace_url or pcfg.base_url,
            model=pcfg.model,
            protocol=pcfg.protocol or "serving_endpoint",
            api_key=api_key,
        )
    elif ptype == "codex_cli":
        provider = CodexCLIProvider(
            command=pcfg.command,
            mode=pcfg.mode,
            enabled=pcfg.enabled,
            model=pcfg.codex_model,
        )
    elif ptype == "anthropic_native":
        oauth_token = await _resolve_oauth_token_async(name, pcfg)
        provider = AnthropicNativeProvider(model=pcfg.model, api_key=api_key, oauth_token=oauth_token)
    elif ptype == "openai_native":
        oauth_token = await _resolve_oauth_token_async(name, pcfg)
        provider = OpenAINativeProvider(model=pcfg.model, api_key=api_key, oauth_token=oauth_token)
    elif ptype == "claude_cli":
        provider = ClaudeCLIProvider(
            command=pcfg.command or "claude",
            mode=pcfg.mode or "print",
            enabled=pcfg.enabled,
            model=pcfg.model if pcfg.model != "default" else None,
        )
    elif ptype == "pi_cli":
        provider = PiCLIProvider(
            command=pcfg.command or "pi",
            enabled=pcfg.enabled,
            model=pcfg.model if pcfg.model != "default" else None,
        )
    else:
        raise ValueError(f"Unknown provider type: {ptype}")

    _cache[name] = provider
    return provider


def invalidate_model_directory(name: str | None = None) -> None:
    """Invalidate cached remote model catalogues after config changes."""

    with _model_directory_lock:
        if name is None:
            _model_directory_cache.clear()
        else:
            _model_directory_cache.pop(name, None)


def invalidate_health_cache() -> None:
    global _health_cache

    with _health_lock:
        _health_cache = None


async def list_models_cached(
    name: str,
    provider: ModelProvider | None = None,
    *,
    force: bool = False,
) -> list[dict]:
    """Return one Provider catalogue with a 15-minute cross-loop single-flight."""

    now = time.monotonic()
    leader = False
    with _model_directory_lock:
        cached = _model_directory_cache.get(name)
        if not force and cached is not None and cached[0] > now:
            return [dict(item) for item in cached[1]]
        inflight = _model_directory_inflight.get(name)
        if inflight is None:
            inflight = Future()
            _model_directory_inflight[name] = inflight
            leader = True

    if not leader:
        result = await asyncio.wrap_future(inflight)
        return [dict(item) for item in result]

    try:
        resolved = provider or await get_provider_async(name)
        from aletheia.model_provider.broker import inference_broker

        models = await inference_broker.run_auxiliary(resolved.list_models)
        sanitized = [dict(item) for item in models if isinstance(item, dict)]
        with _model_directory_lock:
            _model_directory_cache[name] = (
                time.monotonic() + _MODEL_DIRECTORY_TTL_SEC,
                sanitized,
            )
        inflight.set_result(sanitized)
        return [dict(item) for item in sanitized]
    except asyncio.CancelledError:
        error = RuntimeError(f"model directory refresh cancelled for {name}")
        inflight.set_exception(error)
        raise
    # pi-lens-ignore: bare-except — BaseException settles shared Future before re-raising.
    except BaseException as exc:
        inflight.set_exception(exc)
        raise
    finally:
        with _model_directory_lock:
            _model_directory_inflight.pop(name, None)


def clear_cache() -> None:
    """Drop provider routing and catalogue caches; async callers should close clients too."""

    _cache.clear()
    invalidate_model_directory()
    invalidate_health_cache()


async def clear_cache_async() -> None:
    """Invalidate cached providers and close their loop-owned HTTP pools."""

    providers = list({id(provider): provider for provider in _cache.values()}.values())
    clear_cache()
    if not providers:
        return
    from aletheia.model_provider.broker import inference_broker

    for provider in providers:
        if inference_broker.running:
            await inference_broker.run_auxiliary(provider.aclose)
        await provider.aclose()


_HTTP_TYPES = {"openai_compatible", "anthropic_compatible", "databricks", "anthropic_native", "openai_native"}
_DEFAULT_URLS = {"http://localhost:8000/v1", "http://localhost:8001", ""}


async def healthcheck_all(*, force: bool = False) -> list[ModelHealth]:
    """Return a 30-second cached, cross-loop single-flight health snapshot."""

    global _health_cache, _health_inflight
    now = time.monotonic()
    leader = False
    with _health_lock:
        if not force and _health_cache is not None and _health_cache[0] > now:
            return list(_health_cache[1])
        inflight = _health_inflight
        if inflight is None:
            inflight = Future()
            _health_inflight = inflight
            leader = True

    if not leader:
        return list(await asyncio.wrap_future(inflight))

    try:
        results = await _refresh_health_all()
        with _health_lock:
            _health_cache = (time.monotonic() + _HEALTH_TTL_SEC, results)
        inflight.set_result(results)
        return list(results)
    except asyncio.CancelledError:
        inflight.set_exception(RuntimeError("model health refresh cancelled"))
        raise
    # pi-lens-ignore: bare-except — BaseException settles shared Future before re-raising.
    except BaseException as exc:
        inflight.set_exception(exc)
        raise
    finally:
        with _health_lock:
            _health_inflight = None


async def _refresh_health_all() -> list[ModelHealth]:
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
            is_default_url = (pcfg.workspace_url or pcfg.base_url) in _DEFAULT_URLS
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
        from aletheia.model_provider.broker import inference_broker

        health = await inference_broker.run_auxiliary(provider.healthcheck)
        health.provider = name
        return health
    except Exception as e:
        return ModelHealth(provider=name, available=False, error=str(e))


def get_provider_for_tier(tier: str | None) -> str:
    """Translate a routing tier label to a configured provider name.

    tier="deep"  → cfg.models.deep_provider  (falls back to default_provider if None)
    tier="fast"  → cfg.models.fast_provider  (falls back to default_provider if None)
    tier=None    → cfg.models.default_provider

    Unknown tiers fall back to default_provider — never raises.
    """
    cfg = load_config()
    if tier == "deep":
        return cfg.models.deep_provider or cfg.models.default_provider
    if tier == "fast":
        return cfg.models.fast_provider or cfg.models.default_provider
    return cfg.models.default_provider
