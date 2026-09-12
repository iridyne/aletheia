"""Base model provider abstraction."""

from collections.abc import AsyncIterator
import asyncio
import copy
import json
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Literal

_ACTIVE_STRUCTURED_REQUEST: ContextVar[Any | None] = ContextVar("aletheia_active_structured_cost_request", default=None)
_ACTIVE_STREAM_REQUEST: ContextVar[Any | None] = ContextVar("aletheia_active_stream_cost_request", default=None)


def clone_provider_with_model(provider: "ModelProvider", model: str) -> "ModelProvider":
    clone = copy.copy(provider)
    setattr(clone, "model", model)
    return clone


async def _record_llm_attempt_async(**kwargs: Any) -> None:
    """Keep synchronous best-effort cost persistence off the provider loop."""
    try:
        from aletheia.economics.tracing import record_llm_attempt

        await asyncio.to_thread(record_llm_attempt, **kwargs)
    except Exception:
        pass


def model_json_schema_payload(schema: Any) -> dict[str, Any]:
    """Return a JSON Schema payload from schema-like objects.

    Prefer this over hasattr-based fallbacks: callers must provide an object with an
    explicit `model_json_schema()` method (Pydantic models or lightweight shims).
    """

    schema_json = schema.model_json_schema()  # type: ignore[union-attr]
    if not isinstance(schema_json, dict):
        raise TypeError("model_json_schema() must return a dict")
    return schema_json


_PORTABLE_SCHEMA_REMOVED_KEYS = frozenset(
    {
        "$id",
        "$schema",
        "title",
        "description",
        "default",
        "format",
        "examples",
        "pattern",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "contains",
        "patternProperties",
        "dependencies",
        "dependentRequired",
        "dependentSchemas",
        "allOf",
        "oneOf",
    }
)


def portable_json_schema_payload(schema: Any) -> dict[str, Any]:
    """Return a strict JSON-Schema subset shared by native providers."""

    source = model_json_schema_payload(schema)
    resolving: set[str] = set()
    has_conditional_branch = False

    def contains_conditional(node: Any) -> bool:
        if isinstance(node, dict):
            if "allOf" in node or "oneOf" in node:
                return True
            return any(contains_conditional(value) for value in node.values())
        if isinstance(node, list):
            return any(contains_conditional(value) for value in node)
        return False

    has_conditional_branch = contains_conditional(source)

    def nullable(node: Any) -> Any:
        if not isinstance(node, dict):
            return node
        node_type = node.get("type")
        if isinstance(node_type, list):
            types = list(node_type)
            if "null" in types:
                return node
        elif isinstance(node_type, str):
            types = [node_type]
        elif "properties" in node:
            types = ["object"]
        else:
            return node
        types.append("null")
        result = {**node, "type": types}
        enum = result.get("enum")
        if isinstance(enum, list) and None not in enum:
            result["enum"] = [*enum, None]
        return result

    def normalize(node: Any) -> Any:
        if isinstance(node, list):
            return [normalize(item) for item in node]
        if not isinstance(node, dict):
            return node

        ref = node.get("$ref")
        if isinstance(ref, str):
            if not ref.startswith("#/"):
                raise ValueError("portable_schema_external_ref")
            target: Any = source
            for part in ref[2:].split("/"):
                if not isinstance(target, dict) or part not in target:
                    raise ValueError("portable_schema_unresolved_ref")
                target = target[part]
            if ref in resolving:
                raise ValueError("portable_schema_recursive_ref")
            resolving.add(ref)
            try:
                expanded = normalize(target)
            finally:
                resolving.remove(ref)
            overlay = {key: value for key, value in node.items() if key != "$ref"}
            if overlay:
                expanded = {**expanded, **overlay}
            return normalize(expanded)

        variants = node.get("anyOf")
        if isinstance(variants, list) and len(variants) == 2:
            non_null = [
                variant
                for variant in variants
                if not (isinstance(variant, dict) and variant.get("type") == "null")
            ]
            has_null = len(non_null) == 1 and len(variants) == 2
            if has_null:
                primary = normalize(non_null[0])
                if isinstance(primary, dict) and primary.get("type"):
                    node = {
                        **nullable(primary),
                        **{key: value for key, value in node.items() if key != "anyOf"},
                    }

        normalized: dict[str, Any] = {}
        required = set(node.get("required") or ())
        for key, value in node.items():
            if key in _PORTABLE_SCHEMA_REMOVED_KEYS or key in {"$defs", "definitions", "$ref"}:
                continue
            if key == "anyOf":
                raise ValueError("portable_schema_unsupported_anyOf")
            if key == "properties" and isinstance(value, dict):
                properties = {}
                for name, child in value.items():
                    child_schema = normalize(child)
                    if has_conditional_branch and name not in required:
                        child_schema = nullable(child_schema)
                    properties[name] = child_schema
                normalized[key] = properties
            else:
                normalized[key] = normalize(value)
        if normalized.get("type") == "object" or "properties" in normalized:
            properties = normalized.get("properties")
            normalized["additionalProperties"] = False
            if isinstance(properties, dict):
                normalized["required"] = list(properties)
        return normalized

    payload = normalize(source)
    if not isinstance(payload, dict):
        raise TypeError("portable JSON schema must be an object")
    return payload



def _configured_provider_name(provider: Any) -> str:
    """Resolve an exact configured alias without changing provider routing."""
    existing = str(getattr(provider, "_aletheia_provider_name", "") or "").strip()
    if existing:
        return existing
    fallback = str(getattr(provider, "name", "") or type(provider).__name__)
    try:
        from aletheia.config.loader import load_config

        expected_type = {
            "OpenAICompatibleProvider": "openai_compatible",
            "AnthropicCompatibleProvider": "anthropic_compatible",
        }.get(type(provider).__name__)
        provider_model = str(getattr(provider, "model", "") or "")
        provider_base_url = str(getattr(provider, "base_url", "") or "")
        candidates: list[tuple[str, bool]] = []
        for alias, pcfg in load_config().models.providers.items():
            configured_type = str(getattr(pcfg, "type", "") or alias)
            if expected_type and configured_type != expected_type:
                continue
            configured_base_url = str(getattr(pcfg, "base_url", "") or "")
            if provider_base_url and configured_base_url and provider_base_url != configured_base_url:
                continue
            configured_model = str(getattr(pcfg, "model", "") or "")
            candidates.append((alias, configured_model == provider_model))
        exact = [alias for alias, matches_model in candidates if matches_model]
        if len(exact) == 1:
            resolved = exact[0]
        elif len(candidates) == 1:
            resolved = candidates[0][0]
        else:
            resolved = fallback
    except Exception:
        resolved = fallback
    try:
        setattr(provider, "_aletheia_provider_name", resolved)
    except Exception:
        pass
    return resolved


def _cost_metadata(provider: Any, *, mode: str | None = None, request: Any = None) -> dict[str, Any]:
    """Return explicit task-local accounting identity; never inspect the stack.

    When `request` is provided, additionally reads
    `_aletheia_would_block_observations` from it and merges those into the
    metadata under a `would_block` key. This is the storage decision from
    docs/research/tokenomics/phase2_design_decisions_2026-07-31.md:
    would-block observations live in metadata_json, not a new column.
    """
    metadata: dict[str, Any] = {"provider_class": type(provider).__name__}
    if mode:
        metadata[mode] = True
    try:
        from aletheia.agent_runtime.run_context import current_economic_attribution

        attribution = current_economic_attribution()
    except Exception:
        attribution = {}
    if attribution:
        metadata.update(attribution)
        metadata["identity_source"] = "explicit_economic_context"
    if request is not None:
        inference = getattr(request, "inference", None)
        if inference is not None:
            metadata.update(inference.attempt_metadata())
        observations = getattr(request, "_aletheia_would_block_observations", None)
        if observations:
            metadata["would_block"] = list(observations)
        guardrail_observations = getattr(request, "_aletheia_guardrail_observations", None)
        if guardrail_observations:
            metadata["prompt_guardrail"] = list(guardrail_observations)
    return metadata


async def structured_via_complete(
    provider: "ModelProvider", request: "StructuredModelRequest", schema: type
) -> dict:
    """Ask ``complete`` for a json_schema response and parse it.

    The four HTTP providers all obtain structured output this way. Kept a
    function rather than the ``ModelProvider.structured`` default so that
    default stays ``NotImplementedError``: a provider that cannot honour
    ``response_format="json_schema"`` must fail loudly, not return whatever its
    prose happened to parse into.
    """
    response = await provider.complete(
        ModelRequest(
            messages=list(request.messages),
            system=request.system,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            response_format="json_schema",
            structured_schema=portable_json_schema_payload(schema),
        )
    )
    return json.loads(response.content)


async def health_from_catalogue_probe(provider: "ModelProvider") -> "ModelHealth":
    """Report health from the model catalogue without spending an inference call."""
    try:
        models = await provider.list_models()
        if models:
            return health_from_model_catalogue(provider.name, provider.model, models)
        return ModelHealth(
            provider=provider.name,
            available=False,
            error="model catalogue unavailable; use explicit Ping to test inference",
        )
    except Exception as exc:
        return ModelHealth(provider=provider.name, available=False, error=str(exc))


@dataclass
class ModelRequest:
    messages: list[dict[str, str]]
    system: str | None = None
    temperature: float = 0.7
    max_tokens: int = 4096
    inference: Any | None = None
    allow_model_fallback: bool = True
    reasoning_effort: Literal["none", "low", "medium", "high"] | None = None
    response_format: Literal["json_object", "json_schema"] | None = None
    structured_schema: dict[str, Any] | None = None
    structured_schema_name: str = "structured_output"
    exclude_models: tuple[str, ...] = ()


@dataclass
class StructuredModelRequest:
    messages: list[dict[str, str]]
    system: str | None = None
    temperature: float = 0.3
    max_tokens: int = 4096
    inference: Any | None = None


@dataclass
class ModelResponse:
    content: str
    model: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    raw: Any = None


class ProviderResponseError(RuntimeError):
    """HTTP 200 body that is not a usable chat completion.

    SenseNova GLM-5.2 has returned 200 JSON without `choices`, which previously
    became an uncaught KeyError and aborted the fallback chain. This error is
    transient: the request should try the next configured model.
    """


@dataclass
class StreamChunk:
    content: str
    done: bool = False
    usage: dict[str, Any] | None = None
    model: str | None = None


MODEL_HEALTH_STALE_AFTER_SECONDS = 30.0
ModelHealthFreshness = Literal["fresh", "stale", "unknown"]


@dataclass
class ModelHealth:
    provider: str
    available: bool
    model: str = ""
    error: str | None = None
    models: list[str] = field(default_factory=list)
    model_metadata: list[dict] = field(default_factory=list)
    # These fields are populated by the router, which owns the probe/cache
    # boundary. Provider implementations need not maintain a second clock.
    observed_at: str | None = None
    age_seconds: float | None = None
    freshness: ModelHealthFreshness = "unknown"
    cached: bool = False
    stale_after_seconds: float = MODEL_HEALTH_STALE_AFTER_SECONDS
    # Fact-oriented provider state. ``available`` remains as a compatibility
    # alias for older callers; new consumers must use the independent fields.
    configured: bool = True
    enabled: bool = True
    reachable: bool | None = None
    verified: bool | None = None
    verified_at: str | None = None
    verification_identity: str | None = None
    verification_source: str | None = None
    verification_invalidated: bool = False
    verification_detail: str | None = None
    state: str = "unknown"
    reason_code: str = "no_observation"
    routing_roles: list[str] = field(default_factory=list)


def health_from_model_catalogue(
    provider: str,
    model: str,
    models: list[dict],
    *,
    allow_default: bool = False,
) -> ModelHealth:
    """Build honest health from a non-empty provider model catalogue."""
    slugs = [
        str(item["slug"])
        for item in models
        if isinstance(item, dict) and item.get("slug")
    ]
    if model not in slugs and not (allow_default and model == "default"):
        return ModelHealth(
            provider=provider,
            available=False,
            model=model,
            models=slugs,
            model_metadata=models,
            error="configured model not found in current catalogue",
            reason_code="provider_rejection",
        )
    return ModelHealth(
        provider=provider,
        available=True,
        model=model,
        models=slugs,
        model_metadata=models,
    )


class ModelProvider:
    """Base provider with transparent append-only physical-call measurement."""

    name: str = "base"

    def __init_subclass__(cls, **kwargs):
        import functools
        import time
        from datetime import datetime, timezone
        from uuid import uuid4

        super().__init_subclass__(**kwargs)
        original_complete = cls.__dict__.get("complete")
        if original_complete is not None and not getattr(original_complete, "_aletheia_cost_traced", False):

            @functools.wraps(original_complete)
            async def traced_complete(self, request: ModelRequest):
                grouped_request = _ACTIVE_STRUCTURED_REQUEST.get() or _ACTIVE_STREAM_REQUEST.get()
                cost_request = grouped_request or request
                inference = getattr(cost_request, "inference", None)
                call_id = getattr(inference, "logical_call_id", None) or getattr(
                    cost_request, "_aletheia_cost_call_id", None
                )
                if not call_id:
                    call_id = "llm_" + uuid4().hex[:16]
                    setattr(cost_request, "_aletheia_cost_call_id", call_id)
                if inference is not None:
                    attempt_index = inference.claim_attempt_index()
                else:
                    attempt_index = int(getattr(cost_request, "_aletheia_cost_attempt_index", 0))
                    setattr(
                        cost_request,
                        "_aletheia_cost_attempt_index",
                        attempt_index + 1,
                    )
                model = str(getattr(self, "model", "") or "unknown")
                requested_model = getattr(cost_request, "_aletheia_requested_model", None)
                if not requested_model:
                    requested_model = model
                    setattr(
                        cost_request,
                        "_aletheia_requested_model",
                        requested_model,
                    )
                provider_name = _configured_provider_name(self)
                if inference is not None:
                    inference.record_attempt_identity(provider_name, model)
                metadata = _cost_metadata(self, request=cost_request)
                if _ACTIVE_STRUCTURED_REQUEST.get() is not None:
                    metadata["structured"] = True
                if _ACTIVE_STREAM_REQUEST.get() is not None:
                    metadata["stream"] = True
                started_dt = datetime.now(timezone.utc)
                try:
                    from aletheia.economics.pricing_sync import capture_catalog_observation

                    pricing_observation = capture_catalog_observation()
                except Exception:
                    pricing_observation = None
                started_mono = time.perf_counter()
                try:
                    response = await original_complete(self, request)
                except BaseException as exc:
                    finished_dt = datetime.now(timezone.utc)
                    try:
                        await _record_llm_attempt_async(
                            call_id=call_id,
                            provider=provider_name,
                            model=model,
                            requested_model=requested_model,
                            attempt_index=attempt_index,
                            status="error",
                            started_at=started_dt.isoformat(),
                            finished_at=finished_dt.isoformat(),
                            latency_ms=(time.perf_counter() - started_mono) * 1000.0,
                            raw_usage=None,
                            error_type=type(exc).__name__,
                            metadata=metadata,
                            pricing_observation=pricing_observation,
                        )
                    except Exception:
                        pass
                    raise
                finished_dt = datetime.now(timezone.utc)
                raw_usage = getattr(response, "usage", None)
                raw = getattr(response, "raw", None)
                if not raw_usage:
                    if isinstance(raw, dict):
                        raw_usage = raw.get("usage")
                if isinstance(raw_usage, dict) and isinstance(raw, dict):
                    raw_usage = dict(raw_usage)
                    for key in ("provider_cost_usd", "total_cost_usd", "cost_usd"):
                        if key in raw and key not in raw_usage:
                            raw_usage[key] = raw[key]
                try:
                    await _record_llm_attempt_async(
                        call_id=call_id,
                        provider=provider_name,
                        model=str(getattr(response, "model", None) or model),
                        requested_model=requested_model,
                        attempt_index=attempt_index,
                        status="success",
                        started_at=started_dt.isoformat(),
                        finished_at=finished_dt.isoformat(),
                        latency_ms=(time.perf_counter() - started_mono) * 1000.0,
                        raw_usage=raw_usage,
                        metadata=metadata,
                        pricing_observation=pricing_observation,
                    )
                except Exception:
                    pass
                return response

            setattr(traced_complete, "_aletheia_cost_traced", True)
            cls.complete = traced_complete

        original_structured = cls.__dict__.get("structured")
        if original_structured is not None and not getattr(original_structured, "_aletheia_cost_grouped", False):

            @functools.wraps(original_structured)
            async def grouped_structured(self, request: StructuredModelRequest, schema: type):
                call_id = getattr(request, "_aletheia_cost_call_id", None)
                if not call_id:
                    call_id = "llm_structured_" + uuid4().hex[:16]
                    setattr(request, "_aletheia_cost_call_id", call_id)
                if not getattr(request, "_aletheia_requested_model", None):
                    setattr(
                        request,
                        "_aletheia_requested_model",
                        str(getattr(self, "model", "") or "unknown"),
                    )
                before_attempt = int(getattr(request, "_aletheia_cost_attempt_index", 0))
                token = _ACTIVE_STRUCTURED_REQUEST.set(request)
                started_dt = datetime.now(timezone.utc)
                try:
                    from aletheia.economics.pricing_sync import capture_catalog_observation

                    pricing_observation = capture_catalog_observation()
                except Exception:
                    pricing_observation = None
                started_mono = time.perf_counter()
                try:
                    result = await original_structured(self, request, schema)
                except BaseException as exc:
                    if int(getattr(request, "_aletheia_cost_attempt_index", 0)) == before_attempt:
                        setattr(
                            request,
                            "_aletheia_cost_attempt_index",
                            before_attempt + 1,
                        )
                        try:
                            await _record_llm_attempt_async(
                                call_id=call_id,
                                provider=_configured_provider_name(self),
                                model=str(getattr(self, "model", "") or "unknown"),
                                requested_model=getattr(request, "_aletheia_requested_model"),
                                attempt_index=before_attempt,
                                status="error",
                                started_at=started_dt.isoformat(),
                                finished_at=datetime.now(timezone.utc).isoformat(),
                                latency_ms=(time.perf_counter() - started_mono) * 1000.0,
                                raw_usage=None,
                                error_type=type(exc).__name__,
                                metadata={
                                    **_cost_metadata(self, mode="structured", request=request),
                                    "structured_direct": True,
                                },
                                pricing_observation=pricing_observation,
                            )
                        except Exception:
                            pass
                    raise
                else:
                    if int(getattr(request, "_aletheia_cost_attempt_index", 0)) == before_attempt:
                        setattr(
                            request,
                            "_aletheia_cost_attempt_index",
                            before_attempt + 1,
                        )
                        try:
                            await _record_llm_attempt_async(
                                call_id=call_id,
                                provider=_configured_provider_name(self),
                                model=str(getattr(self, "model", "") or "unknown"),
                                requested_model=getattr(request, "_aletheia_requested_model"),
                                attempt_index=before_attempt,
                                status="success",
                                started_at=started_dt.isoformat(),
                                finished_at=datetime.now(timezone.utc).isoformat(),
                                latency_ms=(time.perf_counter() - started_mono) * 1000.0,
                                raw_usage=None,
                                metadata={
                                    **_cost_metadata(self, mode="structured", request=request),
                                    "structured_direct": True,
                                },
                                pricing_observation=pricing_observation,
                            )
                        except Exception:
                            pass
                    return result
                finally:
                    _ACTIVE_STRUCTURED_REQUEST.reset(token)

            setattr(grouped_structured, "_aletheia_cost_grouped", True)
            cls.structured = grouped_structured

        original_stream = cls.__dict__.get("stream")
        if original_stream is not None and not getattr(original_stream, "_aletheia_cost_grouped", False):

            @functools.wraps(original_stream)
            async def grouped_stream(self, request: ModelRequest):
                call_id = getattr(request, "_aletheia_cost_call_id", None)
                if not call_id:
                    call_id = "llm_stream_" + uuid4().hex[:16]
                    setattr(request, "_aletheia_cost_call_id", call_id)
                before_attempt = int(getattr(request, "_aletheia_cost_attempt_index", 0))
                token = _ACTIVE_STREAM_REQUEST.set(request)
                started_dt = datetime.now(timezone.utc)
                try:
                    from aletheia.economics.pricing_sync import capture_catalog_observation

                    pricing_observation = capture_catalog_observation()
                except Exception:
                    pricing_observation = None
                started_mono = time.perf_counter()
                final_usage = None
                final_model = str(getattr(self, "model", "") or "unknown")
                try:
                    async for chunk in original_stream(self, request):
                        if getattr(chunk, "usage", None):
                            final_usage = chunk.usage
                        if getattr(chunk, "model", None):
                            final_model = chunk.model
                        yield chunk
                except BaseException as exc:
                    if int(getattr(request, "_aletheia_cost_attempt_index", 0)) == before_attempt:
                        try:
                            await _record_llm_attempt_async(
                                call_id=call_id,
                                provider=_configured_provider_name(self),
                                model=final_model,
                                requested_model=str(getattr(self, "model", "") or "unknown"),
                                attempt_index=before_attempt,
                                status="error",
                                started_at=started_dt.isoformat(),
                                finished_at=datetime.now(timezone.utc).isoformat(),
                                latency_ms=(time.perf_counter() - started_mono) * 1000.0,
                                raw_usage=final_usage,
                                error_type=type(exc).__name__,
                                metadata={
                                    **_cost_metadata(self, mode="stream", request=request),
                                    "stream_direct": True,
                                },
                                pricing_observation=pricing_observation,
                            )
                        except Exception:
                            pass
                    raise
                else:
                    if int(getattr(request, "_aletheia_cost_attempt_index", 0)) == before_attempt:
                        try:
                            await _record_llm_attempt_async(
                                call_id=call_id,
                                provider=_configured_provider_name(self),
                                model=final_model,
                                requested_model=str(getattr(self, "model", "") or "unknown"),
                                attempt_index=before_attempt,
                                status="success",
                                started_at=started_dt.isoformat(),
                                finished_at=datetime.now(timezone.utc).isoformat(),
                                latency_ms=(time.perf_counter() - started_mono) * 1000.0,
                                raw_usage=final_usage,
                                metadata={
                                    **_cost_metadata(self, mode="stream", request=request),
                                    "stream_direct": True,
                                },
                                pricing_observation=pricing_observation,
                            )
                        except Exception:
                            pass
                finally:
                    _ACTIVE_STREAM_REQUEST.reset(token)

            setattr(grouped_stream, "_aletheia_cost_grouped", True)
            cls.stream = grouped_stream

    def _get_http_client(self, factory, *, timeout: float = 300):
        """Return one managed HTTP client per provider and owning event loop.

        Inference work is consolidated onto the broker loop, while explicit
        candidate probes may run on the API loop. Keeping one connection pool
        per owning loop avoids cross-loop httpx locks without rebuilding TLS
        and TCP connections for every request.
        """

        loop_key = id(asyncio.get_running_loop())
        clients = getattr(self, "_http_clients", None)
        if clients is None:
            clients = {}
            self._http_clients = clients
        client = clients.get(loop_key)
        if client is None:
            client = factory(timeout=timeout)
            clients[loop_key] = client
        return client

    async def aclose(self) -> None:
        """Close the client owned by the current loop, if this is an HTTP provider."""

        clients = getattr(self, "_http_clients", None)
        if not clients:
            return
        client = clients.pop(id(asyncio.get_running_loop()), None)
        if client is not None:
            await client.aclose()

    async def complete(self, request: ModelRequest) -> ModelResponse:
        raise NotImplementedError

    async def structured(self, request: StructuredModelRequest, schema: type) -> Any:
        raise NotImplementedError

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
        """Stream response chunks. Default fallback: single chunk via complete()."""
        resp = await self.complete(request)
        yield StreamChunk(content=resp.content, done=True, usage=resp.usage, model=resp.model)

    async def list_models(self) -> list[dict]:
        """Return [{slug, display_name, ...}] of available models. Never raises."""
        return []

    async def healthcheck(self) -> ModelHealth:
        raise NotImplementedError
