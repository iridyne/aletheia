"""Base model provider abstraction."""

from collections.abc import AsyncIterator
import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

_ACTIVE_STRUCTURED_REQUEST: ContextVar[Any | None] = ContextVar(
    "aletheia_active_structured_cost_request", default=None
)
_ACTIVE_STREAM_REQUEST: ContextVar[Any | None] = ContextVar(
    "aletheia_active_stream_cost_request", default=None
)


def model_json_schema_payload(schema: Any) -> dict[str, Any]:
    """Return a JSON Schema payload from schema-like objects.

    Prefer this over hasattr-based fallbacks: callers must provide an object with an
    explicit `model_json_schema()` method (Pydantic models or lightweight shims).
    """

    schema_json = schema.model_json_schema()  # type: ignore[union-attr]
    if not isinstance(schema_json, dict):
        raise TypeError("model_json_schema() must return a dict")
    return schema_json


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
            "AnthropicNativeProvider": "anthropic_native",
            "OpenAINativeProvider": "openai_native",
            "CodexCLIProvider": "codex_cli",
            "ClaudeCLIProvider": "claude_cli",
        }.get(type(provider).__name__)
        provider_model = str(getattr(provider, "model", "") or "")
        provider_base_url = str(getattr(provider, "base_url", "") or "")
        candidates: list[tuple[str, bool]] = []
        for alias, pcfg in load_config().models.providers.items():
            configured_type = str(getattr(pcfg, "type", "") or alias)
            if expected_type and configured_type != expected_type:
                continue
            configured_base_url = str(getattr(pcfg, "base_url", "") or "")
            if (
                provider_base_url
                and configured_base_url
                and provider_base_url != configured_base_url
            ):
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


def _cost_metadata(
    provider: Any, *, mode: str | None = None, request: Any = None
) -> dict[str, Any]:
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
        observations = getattr(request, "_aletheia_would_block_observations", None)
        if observations:
            metadata["would_block"] = list(observations)
        guardrail_observations = getattr(request, "_aletheia_guardrail_observations", None)
        if guardrail_observations:
            metadata["prompt_guardrail"] = list(guardrail_observations)
    return metadata


@dataclass
class ModelRequest:
    messages: list[dict[str, str]]
    system: str | None = None
    temperature: float = 0.7
    max_tokens: int = 4096


@dataclass
class StructuredModelRequest:
    messages: list[dict[str, str]]
    system: str | None = None
    temperature: float = 0.3
    max_tokens: int = 4096


@dataclass
class ModelResponse:
    content: str
    model: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    raw: Any = None


@dataclass
class StreamChunk:
    content: str
    done: bool = False
    usage: dict[str, int] | None = None


@dataclass
class ModelHealth:
    provider: str
    available: bool
    model: str = ""
    error: str | None = None
    models: list[str] = field(default_factory=list)
    model_metadata: list[dict] = field(default_factory=list)


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
        if original_complete is not None and not getattr(
            original_complete, "_aletheia_cost_traced", False
        ):

            @functools.wraps(original_complete)
            async def traced_complete(self, request: ModelRequest):
                grouped_request = (
                    _ACTIVE_STRUCTURED_REQUEST.get()
                    or _ACTIVE_STREAM_REQUEST.get()
                )
                cost_request = grouped_request or request
                call_id = getattr(cost_request, "_aletheia_cost_call_id", None)
                if not call_id:
                    call_id = "llm_" + uuid4().hex[:16]
                    setattr(cost_request, "_aletheia_cost_call_id", call_id)
                attempt_index = int(
                    getattr(cost_request, "_aletheia_cost_attempt_index", 0)
                )
                setattr(
                    cost_request,
                    "_aletheia_cost_attempt_index",
                    attempt_index + 1,
                )
                model = str(getattr(self, "model", "") or "unknown")
                requested_model = getattr(
                    cost_request, "_aletheia_requested_model", None
                )
                if not requested_model:
                    requested_model = model
                    setattr(
                        cost_request,
                        "_aletheia_requested_model",
                        requested_model,
                    )
                metadata = _cost_metadata(self, request=cost_request)
                if _ACTIVE_STRUCTURED_REQUEST.get() is not None:
                    metadata["structured"] = True
                if _ACTIVE_STREAM_REQUEST.get() is not None:
                    metadata["stream"] = True
                provider_name = _configured_provider_name(self)
                started_dt = datetime.now(timezone.utc)
                started_mono = time.perf_counter()
                try:
                    response = await original_complete(self, request)
                except BaseException as exc:
                    finished_dt = datetime.now(timezone.utc)
                    try:
                        from aletheia.economics.tracing import record_llm_attempt

                        record_llm_attempt(
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
                        )
                    except Exception:
                        pass
                    raise
                finished_dt = datetime.now(timezone.utc)
                raw_usage = getattr(response, "usage", None)
                if not raw_usage:
                    raw = getattr(response, "raw", None)
                    if isinstance(raw, dict):
                        raw_usage = raw.get("usage")
                try:
                    from aletheia.economics.tracing import record_llm_attempt

                    record_llm_attempt(
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
                    )
                except Exception:
                    pass
                return response

            setattr(traced_complete, "_aletheia_cost_traced", True)
            cls.complete = traced_complete

        original_structured = cls.__dict__.get("structured")
        if original_structured is not None and not getattr(
            original_structured, "_aletheia_cost_grouped", False
        ):

            @functools.wraps(original_structured)
            async def grouped_structured(
                self, request: StructuredModelRequest, schema: type
            ):
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
                before_attempt = int(
                    getattr(request, "_aletheia_cost_attempt_index", 0)
                )
                token = _ACTIVE_STRUCTURED_REQUEST.set(request)
                started_dt = datetime.now(timezone.utc)
                started_mono = time.perf_counter()
                try:
                    result = await original_structured(self, request, schema)
                except BaseException as exc:
                    if int(
                        getattr(request, "_aletheia_cost_attempt_index", 0)
                    ) == before_attempt:
                        setattr(
                            request,
                            "_aletheia_cost_attempt_index",
                            before_attempt + 1,
                        )
                        try:
                            from aletheia.economics.tracing import record_llm_attempt

                            record_llm_attempt(
                                call_id=call_id,
                                provider=_configured_provider_name(self),
                                model=str(getattr(self, "model", "") or "unknown"),
                                requested_model=getattr(
                                    request, "_aletheia_requested_model"
                                ),
                                attempt_index=before_attempt,
                                status="error",
                                started_at=started_dt.isoformat(),
                                finished_at=datetime.now(timezone.utc).isoformat(),
                                latency_ms=(
                                    time.perf_counter() - started_mono
                                ) * 1000.0,
                                raw_usage=None,
                                error_type=type(exc).__name__,
                                metadata={
                                    **_cost_metadata(self, mode="structured", request=request),
                                    "structured_direct": True,
                                },
                            )
                        except Exception:
                            pass
                    raise
                else:
                    if int(
                        getattr(request, "_aletheia_cost_attempt_index", 0)
                    ) == before_attempt:
                        setattr(
                            request,
                            "_aletheia_cost_attempt_index",
                            before_attempt + 1,
                        )
                        try:
                            from aletheia.economics.tracing import record_llm_attempt

                            record_llm_attempt(
                                call_id=call_id,
                                provider=_configured_provider_name(self),
                                model=str(getattr(self, "model", "") or "unknown"),
                                requested_model=getattr(
                                    request, "_aletheia_requested_model"
                                ),
                                attempt_index=before_attempt,
                                status="success",
                                started_at=started_dt.isoformat(),
                                finished_at=datetime.now(timezone.utc).isoformat(),
                                latency_ms=(
                                    time.perf_counter() - started_mono
                                ) * 1000.0,
                                raw_usage=None,
                                metadata={
                                    **_cost_metadata(self, mode="structured", request=request),
                                    "structured_direct": True,
                                },
                            )
                        except Exception:
                            pass
                    return result
                finally:
                    _ACTIVE_STRUCTURED_REQUEST.reset(token)

            setattr(grouped_structured, "_aletheia_cost_grouped", True)
            cls.structured = grouped_structured

        original_stream = cls.__dict__.get("stream")
        if original_stream is not None and not getattr(
            original_stream, "_aletheia_cost_grouped", False
        ):

            @functools.wraps(original_stream)
            async def grouped_stream(self, request: ModelRequest):
                call_id = getattr(request, "_aletheia_cost_call_id", None)
                if not call_id:
                    call_id = "llm_stream_" + uuid4().hex[:16]
                    setattr(request, "_aletheia_cost_call_id", call_id)
                before_attempt = int(
                    getattr(request, "_aletheia_cost_attempt_index", 0)
                )
                token = _ACTIVE_STREAM_REQUEST.set(request)
                started_dt = datetime.now(timezone.utc)
                started_mono = time.perf_counter()
                final_usage = None
                try:
                    async for chunk in original_stream(self, request):
                        if getattr(chunk, "usage", None):
                            final_usage = chunk.usage
                        yield chunk
                except BaseException as exc:
                    if int(
                        getattr(request, "_aletheia_cost_attempt_index", 0)
                    ) == before_attempt:
                        try:
                            from aletheia.economics.tracing import record_llm_attempt

                            record_llm_attempt(
                                call_id=call_id,
                                provider=_configured_provider_name(self),
                                model=str(getattr(self, "model", "") or "unknown"),
                                requested_model=str(
                                    getattr(self, "model", "") or "unknown"
                                ),
                                attempt_index=before_attempt,
                                status="error",
                                started_at=started_dt.isoformat(),
                                finished_at=datetime.now(timezone.utc).isoformat(),
                                latency_ms=(
                                    time.perf_counter() - started_mono
                                ) * 1000.0,
                                raw_usage=final_usage,
                                error_type=type(exc).__name__,
                                metadata={
                                    **_cost_metadata(self, mode="stream", request=request),
                                    "stream_direct": True,
                                },
                            )
                        except Exception:
                            pass
                    raise
                else:
                    if int(
                        getattr(request, "_aletheia_cost_attempt_index", 0)
                    ) == before_attempt:
                        try:
                            from aletheia.economics.tracing import record_llm_attempt

                            record_llm_attempt(
                                call_id=call_id,
                                provider=_configured_provider_name(self),
                                model=str(getattr(self, "model", "") or "unknown"),
                                requested_model=str(
                                    getattr(self, "model", "") or "unknown"
                                ),
                                attempt_index=before_attempt,
                                status="success",
                                started_at=started_dt.isoformat(),
                                finished_at=datetime.now(timezone.utc).isoformat(),
                                latency_ms=(
                                    time.perf_counter() - started_mono
                                ) * 1000.0,
                                raw_usage=final_usage,
                                metadata={
                                    **_cost_metadata(self, mode="stream", request=request),
                                    "stream_direct": True,
                                },
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
        yield StreamChunk(content=resp.content, done=True, usage=resp.usage)

    async def list_models(self) -> list[dict]:
        """Return [{slug, display_name, ...}] of available models. Never raises."""
        return []

    async def healthcheck(self) -> ModelHealth:
        raise NotImplementedError
