"""Request-level LLM model fallback.

This module does not mutate provider config or secrets. It only retries the
current request with alternate models when the primary model reports a
transient capacity/rate-limit failure (429/502/503/504, "at capacity", etc.).
"""

from __future__ import annotations

import copy
import logging
from typing import Any

import httpx

from aletheia.config.loader import load_config
from aletheia.model_provider.base import ModelProvider, ModelRequest, ModelResponse
from aletheia.model_provider import router as provider_router

logger = logging.getLogger("aletheia.model_provider.fallback")

_TRANSIENT_STATUS_CODES = {429, 502, 503, 504}
_TRANSIENT_TEXT_MARKERS = (
    "429",
    "502",
    "503",
    "504",
    "rate limit",
    "rate_limit",
    "rate-limited",
    "too many requests",
    "at capacity",
    "capacity",
    "temporarily unavailable",
    "overloaded",
    "service unavailable",
    "bad gateway",
    "gateway timeout",
)
_MAX_MODELS_PER_REQUEST = 6


def is_transient_model_error(exc: BaseException) -> bool:
    """True for model failures worth retrying on another model."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status in _TRANSIENT_STATUS_CODES:
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code if exc.response is not None else None
        if status in _TRANSIENT_STATUS_CODES:
            return True
    text = str(exc).lower()
    return any(marker in text for marker in _TRANSIENT_TEXT_MARKERS)


def _clone_provider_with_model(provider: ModelProvider, model: str) -> ModelProvider:
    clone = copy.copy(provider)
    setattr(clone, "model", model)
    return clone


async def _candidate_models(provider_name: str, provider: ModelProvider) -> list[str]:
    cfg = load_config()
    pcfg = cfg.models.providers.get(provider_name)
    primary = str(getattr(provider, "model", "") or "")
    if pcfg is not None and not getattr(pcfg, "model_fallback_enabled", True):
        return [primary] if primary else []

    candidates: list[str] = []
    if primary:
        candidates.append(primary)
    if pcfg is not None:
        candidates.extend(str(m).strip() for m in getattr(pcfg, "fallback_models", []) or [])

    # Best effort: reuse the existing endpoint/model discovery path so the
    # runtime learns the live catalogue instead of relying only on static config.
    try:
        detected = await provider.list_models()
        candidates.extend(str(m.get("slug") or "").strip() for m in detected if isinstance(m, dict))
    except Exception as exc:  # noqa: BLE001 - detection is advisory only
        logger.debug("model fallback list_models failed for %s: %s", provider_name, exc)

    seen: set[str] = set()
    ordered: list[str] = []
    for model in candidates:
        if not model or model in seen:
            continue
        seen.add(model)
        ordered.append(model)
        if len(ordered) >= _MAX_MODELS_PER_REQUEST:
            break
    return ordered


def _with_fallback_meta(
    response: ModelResponse,
    *,
    provider_name: str,
    requested_model: str,
    attempted_models: list[str],
    fallback_used: bool,
) -> ModelResponse:
    raw: Any = response.raw
    if isinstance(raw, dict):
        raw = dict(raw)
    else:
        raw = {"provider_raw": raw}
    raw["aletheia_model_fallback"] = {
        "provider": provider_name,
        "requested_model": requested_model,
        "final_model": response.model,
        "attempted_models": attempted_models,
        "fallback_used": fallback_used,
    }
    response.raw = raw
    return response


async def complete_with_fallback(
    request: ModelRequest,
    *,
    provider_name: str | None = None,
    provider: ModelProvider | None = None,
) -> ModelResponse:
    """Complete with request-level model fallback on transient model errors."""
    cfg = load_config()
    resolved_provider_name = provider_name or cfg.models.default_provider
    provider = provider or provider_router.get_provider(resolved_provider_name)
    models = await _candidate_models(resolved_provider_name, provider)
    if not models:
        return await provider.complete(request)

    requested_model = models[0]
    attempted: list[str] = []
    last_exc: BaseException | None = None
    for idx, model in enumerate(models):
        attempted.append(model)
        candidate = provider if idx == 0 else _clone_provider_with_model(provider, model)
        try:
            response = await candidate.complete(request)
            return _with_fallback_meta(
                response,
                provider_name=resolved_provider_name,
                requested_model=requested_model,
                attempted_models=attempted,
                fallback_used=idx > 0,
            )
        except Exception as exc:  # noqa: BLE001 - caller expects provider errors
            last_exc = exc
            if not is_transient_model_error(exc) or idx == len(models) - 1:
                raise
            logger.warning(
                "LLM model %s/%s transient failure on %s; trying fallback %s",
                resolved_provider_name,
                model,
                type(exc).__name__,
                models[idx + 1],
            )
    assert last_exc is not None
    raise last_exc
