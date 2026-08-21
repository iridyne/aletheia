"""Model provider router — creates and caches provider instances from config."""

import asyncio
from concurrent.futures import Future
from datetime import datetime, timezone
import hashlib
import json
import logging
import threading
import time

from aletheia.config.loader import load_config, mutate_config, save_config
from aletheia.config.secrets import get_secret
from aletheia.model_provider.base import (
    MODEL_HEALTH_STALE_AFTER_SECONDS,
    ModelHealth,
    ModelProvider,
)
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
_HEALTH_TTL_SEC = MODEL_HEALTH_STALE_AFTER_SECONDS
_health_cache: tuple[float, list[ModelHealth]] | None = None
_health_inflight: Future[list[ModelHealth]] | None = None
_health_lock = threading.Lock()

_REFRESH_MARGIN_MS = 5 * 60 * 1000  # refresh 5 min before expiry
_oauth_refresh_inflight: dict[str, Future[str | None]] = {}
_oauth_refresh_lock = threading.Lock()

_HTTP_PROVIDER_TYPES = {
    "openai_compatible",
    "anthropic_compatible",
    "databricks",
}
_VERIFICATION_FIELDS = (
    "verification_identity",
    "verification_verified",
    "verification_at",
    "verification_source",
    "verification_reason_code",
    "verification_detail",
)


def clear_provider_verification(pcfg) -> None:
    """Remove durable verification evidence from one provider config."""

    for field_name in _VERIFICATION_FIELDS:
        setattr(pcfg, field_name, None)


def provider_is_configured(name: str, pcfg) -> bool:
    """Return whether the provider has enough owner-supplied config to probe."""

    if pcfg is None:
        return False
    provider_type = str(getattr(pcfg, "type", "") or name)
    if provider_type in _HTTP_PROVIDER_TYPES:
        endpoint = str(
            getattr(pcfg, "workspace_url", None)
            or getattr(pcfg, "base_url", None)
            or ""
        ).strip()
        return bool(endpoint)
    if provider_type in {"anthropic_native", "openai_native"}:
        credential_refs = (
            getattr(pcfg, "api_key_secret_ref", None),
            getattr(pcfg, "oauth_token_ref", None),
        )
        return any(get_secret(ref) for ref in credential_refs if ref)
    # CLI integrations own their credentials outside Aletheia.  An entry in
    # ModelsConfig is the explicit integration configuration boundary.
    return True


def provider_configuration_identity(
    name: str,
    pcfg,
    *,
    credential_overrides: dict[str, str | None] | None = None,
) -> str:
    """Fingerprint probe-relevant config without persisting raw credentials."""

    refs = (
        getattr(pcfg, "api_key_secret_ref", None),
        getattr(pcfg, "oauth_token_ref", None),
        getattr(pcfg, "oauth_refresh_ref", None),
    )
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
        "protocol": str(getattr(pcfg, "protocol", "") or ""),
        "model": str(getattr(pcfg, "model", "") or ""),
        "auth_scheme": str(getattr(pcfg, "auth_scheme", "") or ""),
        "auth_type": str(getattr(pcfg, "auth_type", "") or ""),
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


def provider_verification_snapshot(name: str, pcfg) -> dict[str, object]:
    """Return current-identity verification facts, failing closed on mismatch."""

    if pcfg is None:
        return {
            "verified": None,
            "verified_at": None,
            "verification_identity": None,
            "verification_source": None,
            "verification_invalidated": False,
            "verification_detail": None,
        }
    current_identity = provider_configuration_identity(name, pcfg)
    stored_identity = getattr(pcfg, "verification_identity", None)
    invalidated = bool(stored_identity and stored_identity != current_identity)
    matches = bool(stored_identity and stored_identity == current_identity)
    return {
        "verified": getattr(pcfg, "verification_verified", None) if matches else None,
        "verified_at": (
            getattr(pcfg, "verification_at", None)
            if matches
            else None
        ),
        "verification_identity": stored_identity if matches else None,
        "verification_source": getattr(pcfg, "verification_source", None) if matches else None,
        "verification_invalidated": invalidated,
        "verification_detail": getattr(pcfg, "verification_detail", None) if matches else None,
    }


def persist_provider_verification(
    name: str,
    *,
    identity: str,
    verified: bool,
    source: str,
    reason_code: str,
    detail: str | None = None,
    verified_at: str | None = None,
) -> bool:
    """Persist verification only when it still describes current config."""

    def apply(cfg) -> bool:
        pcfg = cfg.models.providers.get(name)
        if pcfg is None or provider_configuration_identity(name, pcfg) != identity:
            return False
        pcfg.verification_identity = identity
        pcfg.verification_verified = verified
        pcfg.verification_at = (
            verified_at
            or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        )
        pcfg.verification_source = source
        pcfg.verification_reason_code = reason_code
        pcfg.verification_detail = detail
        return True

    persisted = mutate_config(apply)
    if persisted:
        invalidate_health_cache()
    return persisted


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

    # For a cached OAuth-native provider, verify the token is still current.
    # The sync get_provider path cannot refresh inside a running event loop, so this
    # check is the only place where cached credentials can be renewed.
    if name in _cache:
        cached = _cache[name]
        ptype = _resolve_type(name, pcfg)
        if ptype in ("openai_native", "anthropic_native") and pcfg.auth_type == "oauth":
            now_ms = int(time.time() * 1000)
            in_refresh_window = bool(
                pcfg.oauth_expires_at
                and now_ms > (pcfg.oauth_expires_at - _REFRESH_MARGIN_MS)
            )
            hard_expired = bool(
                pcfg.oauth_expires_at and now_ms > pcfg.oauth_expires_at
            )
            if in_refresh_window:
                try:
                    fresh_token = await _resolve_oauth_token_async(name, pcfg)
                except Exception as e:
                    if hard_expired:
                        _cache.pop(name, None)
                        raise ValueError(
                            f"OAuth token for {name} has expired and refresh failed: {e}"
                        ) from e
                    logger.warning(
                        "OAuth refresh failed for %s (token not yet expired): %s", name, e
                    )
                    return cached
                if fresh_token:
                    ak = get_secret(pcfg.api_key_secret_ref) if pcfg.api_key_secret_ref else None
                    if ptype == "openai_native":
                        fresh_provider: ModelProvider = OpenAINativeProvider(
                            model=pcfg.model, api_key=ak, oauth_token=fresh_token
                        )
                    else:
                        fresh_provider = AnthropicNativeProvider(
                            model=pcfg.model, api_key=ak, oauth_token=fresh_token
                        )
                    _cache[name] = fresh_provider
                    return fresh_provider
                # Refresh returned no token
                if hard_expired:
                    _cache.pop(name, None)
                    raise ValueError(
                        f"OAuth token for {name} has expired and refresh returned no token. "
                        "Reconfigure the provider or check network connectivity."
                    )
                # Still in proactive margin — continue with cached provider until hard expiry
        return cached

    api_key = None
    if pcfg.api_key_secret_ref:
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


async def healthcheck_all(*, force: bool = False) -> list[ModelHealth]:
    """Return a cached, cross-loop single-flight health snapshot."""

    results, _cached = await healthcheck_all_with_metadata(force=force)
    return results


def _health_observed_at() -> str:
    """Return the UTC completion time for one provider observation."""

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _unobserved_health(provider: str, *, error: str) -> ModelHealth:
    """Represent a configured provider that was intentionally not probed."""

    return ModelHealth(
        provider=provider,
        available=False,
        error=error,
        freshness="unknown",
        stale_after_seconds=_HEALTH_TTL_SEC,
    )


def _routing_roles(cfg, provider: str) -> list[str]:
    roles: list[str] = []
    if cfg.models.default_provider == provider:
        roles.append("default")
    if cfg.models.deep_provider == provider:
        roles.append("deep")
    if cfg.models.fast_provider == provider:
        roles.append("fast")
    return roles


def _classify_health_error(error: str | None) -> str:
    """Map provider diagnostics to a bounded, stable reason vocabulary."""

    detail = str(error or "").casefold()
    if detail == "disabled":
        return "disabled"
    if not detail:
        return "no_observation"
    if detail in {"not_logged_in", "no_configured_models"}:
        return "authentication_failure"
    if detail == "not_installed":
        return "not_installed"
    if "cooldown" in detail:
        return "cooldown"
    if any(token in detail for token in ("401", "403", "unauthor", "forbidden", "authentication", "api key", "token")):
        return "authentication_failure"
    if "policy reload" in detail:
        return "policy_reload"
    if any(token in detail for token in ("timeout", "timed out", "connect", "connection", "network", "dns", "refused", "unreachable", "pool")):
        return "network_failure"
    if any(token in detail for token in ("400", "404", "405", "422", "catalogue", "catalog", "endpoint state", "not found", "unsupported", "invalid")):
        return "provider_rejection"
    return "probe_failure"


def _decorate_health(
    name: str,
    health: ModelHealth,
    cfg,
    pcfg,
    *,
    configured: bool | None = None,
) -> ModelHealth:
    """Attach independent config, reachability, verification, and routing facts."""

    if configured is None:
        configured = provider_is_configured(name, pcfg)
    health.provider = name
    health.configured = configured
    health.enabled = bool(getattr(pcfg, "enabled", False)) if pcfg is not None else False
    health.routing_roles = _routing_roles(cfg, name)
    if pcfg is not None and not health.enabled:
        snapshot = provider_verification_snapshot(name, pcfg)
        health.available = False
        health.reachable = None
        health.verified = snapshot["verified"]  # type: ignore[assignment]
        health.verified_at = snapshot["verified_at"]  # type: ignore[assignment]
        health.verification_identity = snapshot["verification_identity"]  # type: ignore[assignment]
        health.verification_source = snapshot["verification_source"]  # type: ignore[assignment]
        health.verification_invalidated = bool(snapshot["verification_invalidated"])
        health.verification_detail = snapshot["verification_detail"]  # type: ignore[assignment]
        health.reason_code = "disabled"
        health.state = "disabled"
        return health
    if not configured:
        health.available = False
        health.reachable = None
        health.verified = None
        health.reason_code = "missing_configuration"
        health.state = "not_configured"
        health.error = health.error or "provider configuration is incomplete"
        return health

    snapshot = provider_verification_snapshot(name, pcfg)
    health.verified = snapshot["verified"]  # type: ignore[assignment]
    health.verified_at = snapshot["verified_at"]  # type: ignore[assignment]
    health.verification_identity = snapshot["verification_identity"]  # type: ignore[assignment]
    health.verification_source = snapshot["verification_source"]  # type: ignore[assignment]
    health.verification_invalidated = bool(snapshot["verification_invalidated"])
    health.verification_detail = snapshot["verification_detail"]  # type: ignore[assignment]
    if health.observed_at is not None:
        health.reachable = bool(health.available)
        if health.reachable:
            health.reason_code = "verified" if health.verified is True else "reachable"
            health.state = "verified" if health.verified is True else "reachable"
        else:
            health.reason_code = _classify_health_error(health.error)
            health.state = "unavailable"
    else:
        health.reachable = None
        if health.verified is False:
            health.reason_code = getattr(pcfg, "verification_reason_code", None) or _classify_health_error(health.error)
            health.state = "unavailable"
        elif health.verified is True:
            health.reason_code = "verified"
            health.state = "verified"
        elif health.verification_invalidated:
            health.reason_code = "verification_invalidated"
            health.state = "unknown"
        else:
            health.reason_code = "no_observation"
            health.state = "unknown"
    return health


async def _decorate_health_async(
    name: str,
    health: ModelHealth,
    cfg,
    pcfg,
    *,
    configured: bool,
) -> ModelHealth:
    """Keep credential-backed identity checks off the request event loop."""

    return await asyncio.to_thread(
        _decorate_health,
        name,
        health,
        cfg,
        pcfg,
        configured=configured,
    )


async def healthcheck_all_with_metadata(
    *, force: bool = False
) -> tuple[list[ModelHealth], bool]:
    """Return model health plus whether the response came from the TTL cache.

    The list API remains the compatibility surface. The metadata variant is used
    by the HTTP contract so clients can distinguish a fresh probe from a cached
    response without maintaining a local receipt-time clock.
    """

    global _health_cache, _health_inflight
    now = time.monotonic()
    leader = False
    with _health_lock:
        if not force and _health_cache is not None and _health_cache[0] > now:
            return list(_health_cache[1]), True
        inflight = _health_inflight
        if inflight is None:
            inflight = Future()
            _health_inflight = inflight
            leader = True

    if not leader:
        return list(await asyncio.wrap_future(inflight)), False

    try:
        results = await _refresh_health_all()
        with _health_lock:
            _health_cache = (time.monotonic() + _HEALTH_TTL_SEC, results)
        inflight.set_result(results)
        return list(results), False
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
    results: list[ModelHealth] = []
    tasks: list[tuple[str, object, object, bool]] = []
    for name, pcfg in cfg.models.providers.items():
        configured = await asyncio.to_thread(provider_is_configured, name, pcfg)
        if not configured:
            results.append(
                await _decorate_health_async(
                    name,
                    _unobserved_health(name, error="missing_configuration"),
                    cfg,
                    pcfg,
                    configured=False,
                )
            )
            continue
        if not pcfg.enabled:
            # Keep disabled profiles visible without invoking their provider.
            results.append(
                await _decorate_health_async(
                    name,
                    _unobserved_health(name, error="disabled"),
                    cfg,
                    pcfg,
                    configured=True,
                )
            )
            continue
        # Enabled is the operator's probe decision. Keyless local endpoints are
        # valid, so credential/URL heuristics must not turn a configured profile
        # into an unobserved row.
        tasks.append((name, pcfg, _safe_healthcheck(name), True))
    if tasks:
        probed = await asyncio.gather(*(task for _, _, task, _ in tasks))
        results.extend(
            await asyncio.gather(
                *(
                    _decorate_health_async(
                        name,
                        health,
                        cfg,
                        pcfg,
                        configured=configured,
                    )
                    for (name, pcfg, _, configured), health in zip(
                        tasks, probed, strict=True
                    )
                )
            )
        )
    # Include registry-backed singleton and adapter rows even before an owner
    # creates a config entry, so consumers never infer health from omission.
    for name in PROVIDER_REGISTRY:
        if name in cfg.models.providers:
            continue
        results.append(
            await _decorate_health_async(
                name,
                _unobserved_health(name, error="missing_configuration"),
                cfg,
                None,
                configured=False,
            )
        )
    return results


async def _safe_healthcheck(name: str) -> ModelHealth:
    """Single-provider healthcheck with catch-all. Uses async OAuth refresh path."""
    try:
        provider = await get_provider_async(name)
        from aletheia.model_provider.broker import inference_broker

        health = await inference_broker.run_auxiliary(provider.healthcheck)
        health.provider = name
        health.observed_at = _health_observed_at()
        health.age_seconds = None
        health.freshness = "unknown"
        health.cached = False
        health.stale_after_seconds = _HEALTH_TTL_SEC
        return health
    except Exception as e:
        return ModelHealth(
            provider=name,
            available=False,
            error=str(e),
            observed_at=_health_observed_at(),
            stale_after_seconds=_HEALTH_TTL_SEC,
        )


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
