"""Request-level LLM model fallback.

This module does not mutate provider config or secrets. It only retries the
current request with alternate models when the primary model reports a
transient failure (400/404/408/429/500/502/503/504, missing chat ``choices``,
"at capacity", etc.). Structured JSON callers may append JSON-reliable models
(glm-5.2) later in the chain without promoting them ahead of the configured
primary, and skip models that already returned unusable JSON for this call.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass, asdict
import logging
import threading
import time
from typing import Any

import httpx

from aletheia.config.loader import load_config
from aletheia.infra.events import emit_infra_event
from aletheia.model_provider.base import (
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ProviderResponseError,
)
from aletheia.model_provider.broker import (
    InferenceBrokerClosed,
    InferenceBrokerError,
    InferencePolicyReload,
    InferenceQueueFull,
    InferenceRateLimitExceeded,
    InferenceTimeout,
    brokered_complete,
)
from aletheia.model_provider import router as provider_router

logger = logging.getLogger("aletheia.model_provider.fallback")

_TRANSIENT_STATUS_CODES = {400, 404, 408, 429, 500, 502, 503, 504}
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
# Primary + configured fallbacks; keep headroom for the owner's 7-step SenseNova chain.
_MAX_MODELS_PER_REQUEST = 8
# Evidence: glm-5.2 keeps Operator JSON but is slow; keep it as a later fallback.
_JSON_PREFERRED_MODEL_MARKERS = ("glm-5.2",)
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
    if isinstance(exc, ProviderResponseError):
        return True
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


def _parse_fallback_entry(entry: str, default_provider: str) -> tuple[str, str | None]:
    """Parse a fallback candidate into (provider_name, model).

    Plain entries (``"author/model"``) stay on the current provider.
    Entries prefixed with ``@`` are cross-provider: ``"@provider:model"`` routes
    the fallback to a different provider (e.g. ``"@anthropic_compatible:LongCat-2.0"``).
    A bare ``"@provider"`` (no ``:model``) uses that provider's configured default model.
    """
    entry = str(entry).strip()
    if entry.startswith("@"):
        spec = entry[1:]
        if ":" in spec:
            prov, model = spec.split(":", 1)
            return prov.strip(), model.strip()
        return spec.strip(), None
    return default_provider, entry


def _state(provider_name: str, model: str) -> ModelHealthState:
    key = (provider_name, model)
    with _MODEL_HEALTH_LOCK:
        return _state_locked(provider_name, model)


def _state_locked(provider_name: str, model: str) -> ModelHealthState:
    state = _MODEL_HEALTH.get((provider_name, model))
    if state is None:
        state = ModelHealthState(provider=provider_name, model=model)
        _MODEL_HEALTH[(provider_name, model)] = state
    return state


def _is_in_cooldown(provider_name: str, model: str) -> bool:
    with _MODEL_HEALTH_LOCK:
        state = _MODEL_HEALTH.get((provider_name, model))
        return bool(state and state.cooling_down)


def _record_model_success(provider_name: str, model: str) -> None:
    with _MODEL_HEALTH_LOCK:
        state = _state_locked(provider_name, model)
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
    cooldown_event: dict[str, Any] | None = None
    with _MODEL_HEALTH_LOCK:
        state = _state_locked(provider_name, model)
        now = time.time()
        was_cooling = state.cooldown_until > now
        state.consecutive_failures += 1
        state.last_error = str(exc)[:300]
        state.last_failure_at = now
        if state.consecutive_failures >= _COOLDOWN_FAILURE_THRESHOLD:
            state.cooldown_until = now + _COOLDOWN_SEC
            if not was_cooling:
                cooldown_event = {
                    "provider": provider_name,
                    "model": model,
                    "consecutive_failures": state.consecutive_failures,
                    "cooldown_sec": _COOLDOWN_SEC,
                }
    if cooldown_event is not None:
        emit_infra_event(
            severity="warning",
            source="llm",
            code="llm_model_cooldown",
            entity_id=f"{provider_name}/{model}",
            message=f"LLM model temporarily cooling down after transient failures: {provider_name}/{model}",
            action="using_fallback_model",
            metadata=cooldown_event,
            dedupe_sec=60,
        )


def get_model_health_snapshot() -> list[dict[str, Any]]:
    """Return sanitized in-process LLM model health for status surfaces."""
    with _MODEL_HEALTH_LOCK:
        now = time.time()
        snapshot = []
        for state in _MODEL_HEALTH.values():
            item = asdict(state)
            item["cooling_down"] = state.cooldown_until > now
            item["cooldown_remaining_sec"] = max(
                0, round(state.cooldown_until - now, 1)
            )
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
    fb_models: list[str] = []
    if pcfg is not None:
        fb_models = [str(m).strip() for m in getattr(pcfg, "fallback_models", []) or []]
        candidates.extend(fb_models)

    # When explicit fallback models are configured we do NOT also append the
    # provider's full live catalogue. For OpenRouter that catalogue includes
    # paid and sibling-free models; the user constraint is "OpenRouter only ever
    # calls free models", so explicit config must win and discovery must be
    # suppressed to avoid injecting non-free models as fallback candidates.
    if not fb_models:
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


def _json_model_rank(model: str) -> int:
    name = str(model or "").strip().lower()
    for idx, marker in enumerate(_JSON_PREFERRED_MODEL_MARKERS):
        if marker in name:
            return idx
    if "glm" in name:
        return len(_JSON_PREFERRED_MODEL_MARKERS)
    return len(_JSON_PREFERRED_MODEL_MARKERS) + 1


def _enabled_json_preferred_models() -> list[tuple[str, str]]:
    """Return enabled providers whose primary model is JSON-reliable.

    Reads model ids only. Does not inspect secrets or mutate owner config.
    """
    try:
        cfg = load_config()
    except Exception:
        return []
    providers = getattr(getattr(cfg, "models", None), "providers", None) or {}
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for name, pcfg in providers.items():
        if not getattr(pcfg, "enabled", False):
            continue
        model = str(getattr(pcfg, "model", "") or "").strip()
        if not model or _json_model_rank(model) != 0:
            continue
        key = (str(name), model)
        if key in seen:
            continue
        seen.add(key)
        found.append(key)
    return found


def _order_structured_json_candidates(
    parsed: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Keep configured order; append glm-5.2 later if it is missing.

    Does not promote glm ahead of the deep/flash primary. If the capped
    candidate list already contains glm, the original order is unchanged.
    """
    have: set[tuple[str, str]] = set(parsed)
    already_has_glm = any(_json_model_rank(model) == 0 for _provider, model in parsed)
    extra: list[tuple[str, str]] = []
    if not already_has_glm:
        for key in _enabled_json_preferred_models():
            if key in have or not key[0] or not key[1]:
                continue
            extra.append(key)
            have.add(key)
    combined = list(parsed) + extra
    if len(combined) <= _MAX_MODELS_PER_REQUEST:
        return combined or list(parsed)
    if extra and not already_has_glm:
        # Preserve the configured primary/flash head; keep glm reachable at the tail.
        head = list(parsed)[: _MAX_MODELS_PER_REQUEST - 1]
        return head + extra[:1]
    return combined[:_MAX_MODELS_PER_REQUEST] or list(parsed)


def _excluded_model_names(request: ModelRequest) -> set[str]:
    raw = getattr(request, "exclude_models", ()) or ()
    return {str(model).strip().lower() for model in raw if str(model).strip()}


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
        InferencePolicyReload: (
            "warning",
            "llm_broker_policy_reload",
            "LLM inference broker policy reload interrupted request",
            "retry_after_broker_reload",
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
    response_validator: Callable[[ModelResponse], bool] | None = None,
) -> ModelResponse:
    """Complete with request-level model fallback on transient model errors.

    Fallback candidates may be cross-provider via the ``@provider:model`` syntax,
    so e.g. an OpenRouter free model can fall back to a different provider (longcat)
    instead of a paid sibling.
    """
    cfg = load_config()
    resolved_provider_name = provider_name or cfg.models.default_provider
    inference = getattr(request, "inference", None)
    if inference is not None:
        inference.set_resolved_provider(resolved_provider_name)
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

    # Resolve each candidate to (provider_name, model), honoring @provider:model.
    parsed: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for entry in models:
        pn, md = _parse_fallback_entry(entry, resolved_provider_name)
        if not md:
            # bare @provider -> use its configured default model
            try:
                pn_provider = provider if pn == resolved_provider_name else provider_router.get_provider(pn)
                md = str(getattr(pn_provider, "model", "") or "")
            except Exception:  # noqa: BLE001
                md = ""
        if not md:
            continue
        key = (pn, md)
        if key in seen:
            continue
        seen.add(key)
        parsed.append(key)
    if not parsed:
        return await provider.complete(request)

    if getattr(request, "prefer_json_reliable_models", False):
        parsed = _order_structured_json_candidates(parsed)

    requested = parsed[0]
    requested_model = f"{requested[0]}/{requested[1]}"
    skipped_cooldown = [k for k in parsed if _is_in_cooldown(*k)]
    to_try = [k for k in parsed if not _is_in_cooldown(*k)]
    exclude = _excluded_model_names(request)
    if exclude:
        filtered = [k for k in to_try if k[1].strip().lower() not in exclude]
        if filtered:
            to_try = filtered
    if not to_try:
        emit_infra_event(
            severity="critical",
            source="llm",
            code="llm_all_models_cooling_down",
            entity_id=resolved_provider_name,
            message=f"All candidate LLM models are cooling down for provider {resolved_provider_name}",
            action="wait_for_cooldown_or_change_provider",
metadata={
                "provider": resolved_provider_name,
                "models": [f"{p}/{m}" for p, m in parsed],
                "attempted_models": [],
                "skipped_cooldown_models": [f"{p}/{m}" for p, m in skipped_cooldown],
            },
            dedupe_sec=60,
        )
        raise RuntimeError(f"all LLM candidate models are cooling down for {resolved_provider_name}")
    attempted: list[str] = []
    last_exc: BaseException | None = None
    for idx, (pn, md) in enumerate(to_try):
        attempted.append(md)
        cand_provider = provider if pn == resolved_provider_name else provider_router.get_provider(pn)
        candidate = cand_provider if md == str(getattr(cand_provider, "model", "") or "") else _clone_provider_with_model(cand_provider, md)
        try:
            response = await brokered_complete(
                pn,
                candidate,
                request,
            )
            if not (response.content or "").strip():
                failure = ProviderResponseError("model_response_empty_content")
                _record_model_failure(pn, md, failure)
                if idx == len(to_try) - 1:
                    _emit_terminal_failure(
                        failure,
                        provider_name=resolved_provider_name,
                        requested_model=requested_model,
                        attempted_models=attempted,
                        skipped_cooldown_models=[
                            f"{p}/{m}" for p, m in skipped_cooldown
                        ],
                    )
                    return _with_fallback_meta(
                        response,
                        provider_name=resolved_provider_name,
                        requested_model=requested_model,
                        attempted_models=attempted,
                        fallback_used=(pn, md) != requested,
                    )
                logger.warning(
                    "LLM model %s/%s returned empty content; trying fallback %s",
                    pn,
                    md,
                    "/".join(to_try[idx + 1]),
                )
                continue
            # Response content validator (optional) — for structured JSON calls, check
            # if the response is parseable JSON matching the schema. If not, record
            # model failure and continue to the next fallback candidate, unless this
            # is the last attempt.
            if response_validator is not None and not response_validator(response):
                failure = ProviderResponseError("response_validator_rejected")
                _record_model_failure(pn, md, failure)
                if idx == len(to_try) - 1:
                    _emit_terminal_failure(
                        failure,
                        provider_name=resolved_provider_name,
                        requested_model=requested_model,
                        attempted_models=attempted,
                        skipped_cooldown_models=[
                            f"{p}/{m}" for p, m in skipped_cooldown
                        ],
                    )
                    return _with_fallback_meta(
                        response,
                        provider_name=resolved_provider_name,
                        requested_model=requested_model,
                        attempted_models=attempted,
                        fallback_used=(pn, md) != requested,
                    )
                logger.warning(
                    "LLM model %s/%s response failed validation; trying fallback %s",
                    pn,
                    md,
                    "/".join(to_try[idx + 1]),
                )
                continue
            _record_model_success(pn, md)
            if skipped_cooldown and isinstance(response.raw, dict):
                response.raw = dict(response.raw)
                response.raw["skipped_cooldown_models"] = [f"{p}/{m}" for p, m in skipped_cooldown]
            fallback_used = (pn, md) != requested
            if fallback_used:
                emit_infra_event(
                    severity="warning",
                    source="llm",
                    code="llm_model_fallback_used",
                    entity_id=f"{pn}/{md}",
                    message=f"LLM request fell back from {requested_model} to {pn}/{md}",
                    action="fallback_success",
                    metadata={
                        "provider": pn,
                        "requested_model": requested_model,
                        "final_model": md,
                        "attempted_models": attempted,
                        "skipped_cooldown_models": [f"{p}/{m}" for p, m in skipped_cooldown],
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
                _record_model_failure(pn, md, exc)
            if not transient or idx == len(to_try) - 1:
                _emit_terminal_failure(
                    exc,
                    provider_name=resolved_provider_name,
                    requested_model=requested_model,
                    attempted_models=attempted,
                    skipped_cooldown_models=[f"{p}/{m}" for p, m in skipped_cooldown],
                )
                raise
            logger.warning(
                "LLM model %s/%s transient failure on %s; trying fallback %s",
                pn,
                md,
                type(exc).__name__,
                "/".join(to_try[idx + 1]),
            )
    assert last_exc is not None
    raise last_exc
