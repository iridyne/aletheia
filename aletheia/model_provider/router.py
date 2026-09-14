"""Model provider router — creates and caches provider instances from config."""

import asyncio
from collections.abc import Callable
from concurrent.futures import Future
from datetime import datetime, timezone
import threading
import time

from aletheia.config.loader import load_config, mutate_config
from aletheia.config.schema import ModelSelection
from aletheia.model_provider.base import (
    MODEL_HEALTH_STALE_AFTER_SECONDS,
    ModelHealth,
    ModelProvider,
    clone_provider_with_model,
)
from aletheia.model_provider.registry import API_PROVIDER_TYPES, PROVIDER_TYPES

_provider_factory: Callable[..., ModelProvider] | None = None
_provider_identity_reader: Callable[..., str] | None = None


def configure_model_provider(
    factory: Callable[..., ModelProvider] | None,
    *,
    identity_reader: Callable[..., str] | None = None,
) -> None:
    """Bind Model implementation operations at the existing entrypoint, without I/O."""
    global _provider_factory, _provider_identity_reader
    _provider_factory = factory
    _provider_identity_reader = identity_reader

_cache: dict[str, ModelProvider] = {}


def _cache_provider(name: str, provider: ModelProvider) -> ModelProvider:
    setattr(provider, "_aletheia_provider_name", name)
    # ponytail: cold lookups may build spare pool-free instances; serialize if construction becomes costly.
    return _cache.setdefault(name, provider)


_MODEL_DIRECTORY_TTL_SEC = 15 * 60
_model_directory_cache: dict[str, tuple[float, list[dict]]] = {}
_model_directory_inflight: dict[str, Future[list[dict]]] = {}
_model_directory_lock = threading.Lock()
_HEALTH_TTL_SEC = MODEL_HEALTH_STALE_AFTER_SECONDS
_health_cache: tuple[float, list[ModelHealth]] | None = None
_health_inflight: Future[list[ModelHealth]] | None = None
_health_lock = threading.Lock()

_VERIFICATION_FIELDS = (
    "verification_identity",
    "verification_verified",
    "verification_at",
    "verification_source",
    "verification_reason_code",
    "verification_detail",
)


class ProviderNotConfigured(ValueError):
    """A selected provider lacks the owner-supplied config required to run."""

    reason_code = "not_configured"

    def __init__(self, name: str):
        self.provider_name = name
        super().__init__(f"Model provider is not_configured: {name}")


class ModelAdapterUnavailable(ValueError):
    """The optional Model implementation is not bound in this process."""


def clear_provider_verification(pcfg) -> None:
    """Remove durable verification evidence from one provider config."""

    for field_name in _VERIFICATION_FIELDS:
        setattr(pcfg, field_name, None)


def provider_is_configured(name: str, pcfg) -> bool:
    """Return whether the provider has enough owner-supplied config to probe."""

    if pcfg is None:
        return False
    provider_type = str(getattr(pcfg, "type", "") or name)
    if provider_type in API_PROVIDER_TYPES:
        endpoint = str(
            getattr(pcfg, "base_url", None)
            or ""
        ).strip()
        return bool(endpoint)
    return False


def _require_provider_configured(name: str, pcfg) -> None:
    if not provider_is_configured(name, pcfg):
        raise ProviderNotConfigured(name)


def provider_configuration_identity(name: str, pcfg) -> str:
    """Read opaque current-configuration evidence; Core never accepts secret values."""
    if _provider_identity_reader is None:
        raise ModelAdapterUnavailable("model_adapter_unavailable")
    return _provider_identity_reader(name, pcfg)


def provider_verification_snapshot(name: str, pcfg) -> dict[str, object]:
    """Return current-identity verification facts, failing closed on mismatch."""

    if pcfg is None or str(getattr(pcfg, "type", "") or name) not in API_PROVIDER_TYPES:
        return {
            "verified": None,
            "verified_at": None,
            "verification_identity": None,
            "verification_source": None,
            "verification_invalidated": False,
            "verification_detail": None,
        }
    unavailable_detail = None
    try:
        current_identity = provider_configuration_identity(name, pcfg)
    except ModelAdapterUnavailable as exc:
        current_identity = None
        unavailable_detail = str(exc)
    stored_identity = getattr(pcfg, "verification_identity", None)
    invalidated = bool(stored_identity and current_identity is not None and stored_identity != current_identity)
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
        "verification_detail": unavailable_detail or (
            getattr(pcfg, "verification_detail", None) if matches else None
        ),
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
        if (
            pcfg is None
            or str(getattr(pcfg, "type", "") or name) not in API_PROVIDER_TYPES
        ):
            return False
        try:
            if provider_configuration_identity(name, pcfg) != identity:
                return False
        except ModelAdapterUnavailable:
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


def get_provider(name: str | ModelSelection | None = None) -> ModelProvider:
    """Get an enabled provider by name, or the configured default."""
    if isinstance(name, ModelSelection):
        if not name.endpoint_id.strip() or not name.model_id.strip():
            raise ProviderNotConfigured(name.endpoint_id)
        return clone_provider_with_model(get_provider(name.endpoint_id), name.model_id)
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
    _require_provider_configured(name, pcfg)

    if _provider_factory is None:
        raise ModelAdapterUnavailable("model_adapter_unavailable")
    if name in _cache:
        return _cache[name]

    return _cache_provider(name, _provider_factory(name, pcfg))


async def get_provider_async(name: str | ModelSelection | None = None) -> ModelProvider:
    """Resolve through the single constructor owner without blocking the loop."""
    return await asyncio.to_thread(get_provider, name)


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
    if cfg.models.deep and cfg.models.deep.endpoint_id == provider:
        roles.append("deep")
    if cfg.models.fast and cfg.models.fast.endpoint_id == provider:
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
            health.reason_code = (
                health.reason_code
                if health.reason_code != "no_observation"
                else _classify_health_error(health.error)
            )
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
    for name in PROVIDER_TYPES:
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
    """Single-provider healthcheck with catch-all via the shared inference broker."""
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


def get_provider_for_tier(tier: str | None) -> ModelSelection:
    """Resolve one explicit pair, never substituting another tier's endpoint."""
    cfg = load_config()
    if tier in {"deep", "fast"}:
        selected = getattr(cfg.models, tier)
        if selected is None or not selected.endpoint_id.strip() or not selected.model_id.strip():
            raise ProviderNotConfigured(str(tier))
        return selected
    if tier not in {None, "default"}:
        raise ValueError(f"Unknown model tier: {tier}")
    endpoint = cfg.models.default_provider
    profile = cfg.models.providers.get(endpoint)
    if profile is None or not profile.model:
        raise ProviderNotConfigured(endpoint)
    return ModelSelection(endpoint_id=endpoint, model_id=profile.model)
