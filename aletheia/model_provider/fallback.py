"""Request-level LLM model fallback.

This module does not mutate provider config or secrets. It only retries the
current request with alternate models when the primary model reports a
transient capacity/rate-limit failure (429/502/503/504, "at capacity", etc.).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, asdict
import logging
import threading
import time
from typing import Any

import httpx

from aletheia.config.loader import load_config
from aletheia.infra.events import emit_infra_event
from aletheia.model_provider.base import ModelProvider, ModelRequest, ModelResponse
from aletheia.model_provider.broker import (
    InferenceBrokerClosed,
    InferenceBrokerError,
    InferenceQueueFull,
    InferenceRateLimitExceeded,
    InferenceTimeout,
    brokered_complete,
)
from aletheia.model_provider import router as provider_router

logger = logging.getLogger("aletheia.model_provider.fallback")

_TRANSIENT_STATUS_CODES = {429, 502, 503, 504}
_TRANSIENT_TRANSPORT_ERRORS = (
    httpx.NetworkError,
    httpx.RemoteProtocolError,
    httpx.ProxyError,
)
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
_COOLDOWN_FAILURE_THRESHOLD = 2
_COOLDOWN_SEC = 300
_MODEL_HEALTH: dict[tuple[str, str], "ModelHealthState"] = {}
# Guards _MODEL_HEALTH against concurrent mutation (agent threads recording
# success/failure) + iteration (ASGI thread building the health snapshot), which
# could otherwise raise "dict changed size during iteration" (CC-1).
_MODEL_HEALTH_LOCK = threading.Lock()
_SAFETY_LOGICAL_AGENTS = frozenset({
    "pi_trading_decision", "risk_critic", "paper_trader", "agent_shell/trading_decision",
})


class BudgetExceededError(RuntimeError):
    """A configured non-safety budget denied this logical LLM call."""

    def __init__(self, observations: list[dict[str, Any]]):
        self.observations = observations
        super().__init__("llm_budget_exhausted")


@dataclass
class ModelHealthState:
    provider: str
    model: str
    consecutive_failures: int = 0
    cooldown_until: float = 0
    last_error: str | None = None
    last_failure_at: float | None = None
    last_success_at: float | None = None

    @property
    def cooling_down(self) -> bool:
        return self.cooldown_until > time.time()


def is_transient_model_error(exc: BaseException) -> bool:
    """True for model failures worth retrying on another model."""
    if isinstance(exc, _TRANSIENT_TRANSPORT_ERRORS):
        return True
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


def _state(provider_name: str, model: str) -> ModelHealthState:
    key = (provider_name, model)
    with _MODEL_HEALTH_LOCK:
        state = _MODEL_HEALTH.get(key)
        if state is None:
            state = ModelHealthState(provider=provider_name, model=model)
            _MODEL_HEALTH[key] = state
        return state


def _is_in_cooldown(provider_name: str, model: str) -> bool:
    state = _MODEL_HEALTH.get((provider_name, model))
    return bool(state and state.cooling_down)


def _record_model_success(provider_name: str, model: str) -> None:
    state = _state(provider_name, model)
    was_cooling = state.cooling_down
    state.consecutive_failures = 0
    state.cooldown_until = 0
    state.last_error = None
    state.last_success_at = time.time()
    if was_cooling:
        emit_infra_event(
            severity="info",
            source="llm",
            code="llm_model_recovered",
            entity_id=f"{provider_name}/{model}",
            message=f"LLM model recovered: {provider_name}/{model}",
            action="model_reenabled",
            dedupe_sec=60,
        )


def _record_model_failure(provider_name: str, model: str, exc: BaseException) -> None:
    state = _state(provider_name, model)
    state.consecutive_failures += 1
    state.last_error = str(exc)[:300]
    state.last_failure_at = time.time()
    if state.consecutive_failures >= _COOLDOWN_FAILURE_THRESHOLD:
        state.cooldown_until = time.time() + _COOLDOWN_SEC
        emit_infra_event(
            severity="warning",
            source="llm",
            code="llm_model_cooldown",
            entity_id=f"{provider_name}/{model}",
            message=f"LLM model temporarily cooling down after transient failures: {provider_name}/{model}",
            action="using_fallback_model",
            metadata={
                "provider": provider_name,
                "model": model,
                "consecutive_failures": state.consecutive_failures,
                "cooldown_sec": _COOLDOWN_SEC,
            },
            dedupe_sec=60,
        )


def get_model_health_snapshot() -> list[dict[str, Any]]:
    """Return sanitized in-process LLM model health for status surfaces."""
    now = time.time()
    snapshot = []
    # Snapshot the values under lock so a concurrent _state() insert can't mutate
    # the dict mid-iteration (CC-1).
    with _MODEL_HEALTH_LOCK:
        states = list(_MODEL_HEALTH.values())
    for state in states:
        item = asdict(state)
        item["cooling_down"] = state.cooldown_until > now
        item["cooldown_remaining_sec"] = max(0, round(state.cooldown_until - now, 1))
        snapshot.append(item)
    return sorted(snapshot, key=lambda x: (x["provider"], x["model"]))


def reset_model_health() -> None:
    """Test helper."""
    with _MODEL_HEALTH_LOCK:
        _MODEL_HEALTH.clear()


def _observe_would_block(
    cfg,
    request: ModelRequest,
    provider: ModelProvider,
    provider_name: str,
) -> None:
    """Attach Phase 2B-2 shadow-mode would-block observations to the request.

    Runs before the fallback loop. Never raises: any failure is swallowed
    so measurement cannot break the LLM call. Observations are stored on
    the request object (rather than a ContextVar) because the request is
    shared across all fallback attempts and any downstream broker loop,
    where ContextVars set on the caller loop would not be visible.

    Gated by ``EconomicsConfig.enabled and .budgets_shadow_enabled`` plus
    the tracing kill switch. Off by default — no ledger row gets a
    ``would_block`` key until an operator explicitly opts in.
    """
    try:
        econ = getattr(cfg, "economics", None)
        if econ is None or not econ.enabled or not econ.budgets_shadow_enabled:
            return
        # Repeat guard: skip when tracing itself is disabled — the resulting
        # ledger row would be missing anyway, so a would_block observation
        # against no persisted evidence would be misleading.
        from aletheia.economics.tracing import tracing_enabled

        if not tracing_enabled():
            return
        from aletheia.economics.budgets import check_would_block
        from aletheia.economics.tracing import resolve_pricing_snapshot
        from datetime import datetime, timezone

        model = str(getattr(provider, "model", "") or "")
        if not model:
            return
        now_iso = datetime.now(timezone.utc).isoformat()
        pricing = resolve_pricing_snapshot(provider_name, model, now_iso)
        if not pricing:
            # No pricing snapshot means the ledger records cost_status='unknown';
            # a hypothetical estimate would be dishonest here.
            return
        input_tokens_est = _estimate_input_tokens(request)
        output_tokens_est = max(0, int(getattr(request, "max_tokens", 0) or 0))
        input_rate = float(pricing.get("input_usd_per_million") or 0.0)
        output_rate = float(pricing.get("output_usd_per_million") or 0.0)
        estimated_cost_usd = (
            input_tokens_est * input_rate / 1_000_000.0
            + output_tokens_est * output_rate / 1_000_000.0
        )
        if not (estimated_cost_usd > 0):
            return
        try:
            from aletheia.agent_runtime.run_context import (
                current_agent_run_id,
                current_economic_attribution,
            )
            run_id = current_agent_run_id()
            attribution = current_economic_attribution()
            logical_agent = attribution.get("logical_agent") if attribution else None
        except Exception:
            run_id = None
            logical_agent = None
        observations = check_would_block(
            estimated_cost_usd=estimated_cost_usd,
            run_id=run_id,
            logical_agent=logical_agent,
            provider=provider_name,
        )
        if observations:
            setattr(
                request, "_aletheia_would_block_observations", observations
            )
    except Exception as exc:  # noqa: BLE001 — observation must never break inference
        logger.debug("would-block observation failed silently: %s", exc)


def _observe_prompt_guardrail(cfg, request: ModelRequest) -> None:
    """Attach content-free Phase 4 observations; detection cannot deny inference."""
    econ = getattr(cfg, "economics", None)
    if econ is None or not econ.enabled or not econ.prompt_guardrail_detection_enabled:
        return
    try:
        from aletheia.economics.tracing import tracing_enabled
        from aletheia.model_provider.guardrails import detect_prompt_secrets

        if not tracing_enabled():
            return
        observations = detect_prompt_secrets(
            [request.system, *(message.get("content") for message in request.messages)]
        )
        if observations:
            setattr(request, "_aletheia_guardrail_observations", observations)
    except Exception as exc:  # noqa: BLE001 -- detection must not alter inference
        logger.debug("prompt guardrail observation failed: %s", exc)


def _estimate_input_tokens(request: ModelRequest) -> int:
    """Character-count / 4 heuristic; conservative enough for shadow mode."""
    chars = len(request.system or "")
    for message in request.messages or []:
        chars += len(str(message.get("role") or ""))
        chars += len(str(message.get("content") or ""))
    return max(1, chars // 4)


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
        detected = await provider_router.list_models_cached(provider_name, provider)
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


def _emit_terminal_failure(
    exc: BaseException,
    *,
    provider_name: str,
    requested_model: str,
    attempted_models: list[str],
    skipped_cooldown_models: list[str],
) -> None:
    """Expose a terminal failure without copying exception or request contents."""
    broker_events = {
        InferenceBrokerClosed: (
            "critical",
            "llm_broker_closed",
            "LLM inference broker is unavailable",
            "wait_for_broker_availability",
        ),
        InferenceQueueFull: (
            "warning",
            "llm_broker_queue_full",
            "LLM inference broker queue is full",
            "reduce_inference_load",
        ),
        InferenceRateLimitExceeded: (
            "warning",
            "llm_broker_rate_limit_exceeded",
            "LLM inference broker rate limit denied request",
            "review_provider_rate_limit",
        ),
        InferenceTimeout: (
            "warning",
            "llm_broker_timeout",
            "LLM inference broker deadline expired",
            "review_inference_capacity",
        ),
    }
    if isinstance(exc, InferenceBrokerError):
        severity, code, message, action = next(
            (event for error_type, event in broker_events.items() if isinstance(exc, error_type)),
            ("warning", "llm_broker_error", "LLM inference broker rejected request", "review_inference_broker"),
        )
    elif is_transient_model_error(exc):
        severity, code, message, action = (
            "warning", "llm_call_exhausted", "LLM model fallback attempts exhausted", "retry_after_provider_recovers"
        )
    else:
        severity, code, message, action = (
            "warning", "llm_call_non_transient_error", "LLM provider rejected request", "review_provider_configuration"
        )
    emit_infra_event(
        severity=severity,
        source="llm",
        code=code,
        entity_id=f"{provider_name}/{requested_model}",
        message=message,
        action=action,
        metadata={
            "provider": provider_name,
            "requested_model": requested_model,
            "attempted_models": attempted_models,
            "skipped_cooldown_models": skipped_cooldown_models,
        },
        dedupe_sec=60,
    )


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
        "skipped_cooldown_models": raw.get("skipped_cooldown_models", []),
        "fallback_used": fallback_used,
    }
    response.raw = raw
    return response


def _enforce_budget_if_configured(cfg, request: ModelRequest) -> None:
    """Apply opt-in policy after the Phase 2 observation has been computed."""
    econ = getattr(cfg, "economics", None)
    observations = getattr(request, "_aletheia_would_block_observations", None)
    if econ is None or not econ.enabled or econ.enforcement_mode == "warn_only" or not observations:
        return
    try:
        from aletheia.agent_runtime.run_context import current_economic_attribution

        logical_agent = (current_economic_attribution() or {}).get("logical_agent")
    except Exception:
        logical_agent = None
    if logical_agent in _SAFETY_LOGICAL_AGENTS:
        return
    raise BudgetExceededError(list(observations))


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
    _observe_would_block(cfg, request, provider, resolved_provider_name)
    _observe_prompt_guardrail(cfg, request)
    _enforce_budget_if_configured(cfg, request)
    if not request.allow_model_fallback:
        return await brokered_complete(resolved_provider_name, provider, request)
    models = await _candidate_models(resolved_provider_name, provider)
    if not models:
        try:
            return await brokered_complete(resolved_provider_name, provider, request)
        except Exception as exc:  # noqa: BLE001 - caller expects provider errors
            _emit_terminal_failure(
                exc,
                provider_name=resolved_provider_name,
                requested_model=str(getattr(provider, "model", "") or ""),
                attempted_models=[],
                skipped_cooldown_models=[],
            )
            raise

    requested_model = models[0]
    skipped_cooldown = [
        model for model in models if _is_in_cooldown(resolved_provider_name, model)
    ]
    models_to_try = [
        model for model in models if not _is_in_cooldown(resolved_provider_name, model)
    ]
    if not models_to_try:
        emit_infra_event(
            severity="critical",
            source="llm",
            code="llm_all_models_cooling_down",
            entity_id=resolved_provider_name,
            message=f"All candidate LLM models are cooling down for provider {resolved_provider_name}",
            action="wait_for_cooldown_or_change_provider",
            metadata={
                "provider": resolved_provider_name,
                "models": models,
                "attempted_models": [],
                "skipped_cooldown_models": skipped_cooldown,
            },
            dedupe_sec=60,
        )
        raise RuntimeError(f"all LLM candidate models are cooling down for {resolved_provider_name}")
    attempted: list[str] = []
    last_exc: BaseException | None = None
    for idx, model in enumerate(models_to_try):
        attempted.append(model)
        candidate = provider if model == str(getattr(provider, "model", "") or "") else _clone_provider_with_model(provider, model)
        try:
            response = await brokered_complete(
                resolved_provider_name,
                candidate,
                request,
            )
            _record_model_success(resolved_provider_name, model)
            if skipped_cooldown and isinstance(response.raw, dict):
                response.raw = dict(response.raw)
                response.raw["skipped_cooldown_models"] = skipped_cooldown
            fallback_used = model != requested_model
            if fallback_used:
                emit_infra_event(
                    severity="warning",
                    source="llm",
                    code="llm_model_fallback_used",
                    entity_id=f"{resolved_provider_name}/{requested_model}",
                    message=f"LLM request fell back from {requested_model} to {model}",
                    action="fallback_success",
                    metadata={
                        "provider": resolved_provider_name,
                        "requested_model": requested_model,
                        "final_model": model,
                        "attempted_models": attempted,
                        "skipped_cooldown_models": skipped_cooldown,
                    },
                    dedupe_sec=30,
                )
            return _with_fallback_meta(
                response,
                provider_name=resolved_provider_name,
                requested_model=requested_model,
                attempted_models=attempted,
                fallback_used=fallback_used,
            )
        except Exception as exc:  # noqa: BLE001 - caller expects provider errors
            last_exc = exc
            if isinstance(exc, InferenceBrokerError):
                _emit_terminal_failure(
                    exc,
                    provider_name=resolved_provider_name,
                    requested_model=requested_model,
                    attempted_models=attempted,
                    skipped_cooldown_models=skipped_cooldown,
                )
                raise
            transient = is_transient_model_error(exc)
            if transient:
                _record_model_failure(resolved_provider_name, model, exc)
            if not transient or idx == len(models_to_try) - 1:
                _emit_terminal_failure(
                    exc,
                    provider_name=resolved_provider_name,
                    requested_model=requested_model,
                    attempted_models=attempted,
                    skipped_cooldown_models=skipped_cooldown,
                )
                raise
            logger.warning(
                "LLM model %s/%s transient failure on %s; trying fallback %s",
                resolved_provider_name,
                model,
                type(exc).__name__,
                models_to_try[idx + 1],
            )
    assert last_exc is not None
    raise last_exc
