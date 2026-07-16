"""Base model provider abstraction."""

from collections.abc import AsyncIterator
import asyncio
from dataclasses import dataclass, field
from typing import Any


def _configured_provider_name(provider: Any) -> str:
    """Resolve the user-facing provider alias once, without changing routing.

    Router instances do not currently carry their config key. Exact cost pricing
    must distinguish two endpoints that share the same provider implementation,
    so we recover the unique matching alias from the immutable provider config and
    cache it on the instance. Fallback clones inherit the cached attribute.
    """
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
            if provider_base_url and configured_base_url and provider_base_url != configured_base_url:
                continue
            configured_model = str(getattr(pcfg, "model", "") or "")
            candidates.append((alias, configured_model == provider_model))
        exact = [alias for alias, model_match in candidates if model_match]
        resolved = exact[0] if len(exact) == 1 else candidates[0][0] if len(candidates) == 1 else fallback
    except Exception:
        resolved = fallback
    try:
        setattr(provider, "_aletheia_provider_name", resolved)
    except Exception:
        pass
    return resolved


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
    """Base class for model providers.

    Physical provider calls are wrapped once at subclass-definition time. The
    wrapper is measurement-only: it records append-only cost facts and always
    returns or raises exactly what the provider implementation returned or raised.
    Measurement failures are swallowed by the tracing function and can never
    change model routing, retries, fallback, or trading behavior.
    """

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
                call_id = getattr(request, "_aletheia_cost_call_id", None)
                if not call_id:
                    call_id = "llm_" + uuid4().hex[:16]
                    setattr(request, "_aletheia_cost_call_id", call_id)
                attempt_index = int(getattr(request, "_aletheia_cost_attempt_index", 0))
                setattr(request, "_aletheia_cost_attempt_index", attempt_index + 1)
                model = str(getattr(self, "model", "") or "unknown")
                requested_model = getattr(request, "_aletheia_requested_model", None)
                if not requested_model:
                    requested_model = model
                    setattr(request, "_aletheia_requested_model", requested_model)
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
                            metadata={"provider_class": type(self).__name__},
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
                        metadata={"provider_class": type(self).__name__},
                    )
                except Exception:
                    pass
                return response

            traced_complete._aletheia_cost_traced = True
            cls.complete = traced_complete

        original_stream = cls.__dict__.get("stream")
        if original_stream is not None and not getattr(original_stream, "_aletheia_cost_traced", False):
            @functools.wraps(original_stream)
            async def traced_stream(self, request: ModelRequest):
                call_id = "llm_stream_" + uuid4().hex[:16]
                model = str(getattr(self, "model", "") or "unknown")
                provider_name = _configured_provider_name(self)
                started_dt = datetime.now(timezone.utc)
                started_mono = time.perf_counter()
                final_usage = None
                try:
                    async for chunk in original_stream(self, request):
                        if getattr(chunk, "usage", None):
                            final_usage = chunk.usage
                        yield chunk
                except BaseException as exc:
                    finished_dt = datetime.now(timezone.utc)
                    try:
                        from aletheia.economics.tracing import record_llm_attempt

                        record_llm_attempt(
                            call_id=call_id,
                            provider=provider_name,
                            model=model,
                            requested_model=model,
                            attempt_index=0,
                            status="error",
                            started_at=started_dt.isoformat(),
                            finished_at=finished_dt.isoformat(),
                            latency_ms=(time.perf_counter() - started_mono) * 1000.0,
                            raw_usage=final_usage,
                            error_type=type(exc).__name__,
                            metadata={"provider_class": type(self).__name__, "stream": True},
                        )
                    except Exception:
                        pass
                    raise
                finished_dt = datetime.now(timezone.utc)
                try:
                    from aletheia.economics.tracing import record_llm_attempt

                    record_llm_attempt(
                        call_id=call_id,
                        provider=provider_name,
                        model=model,
                        requested_model=model,
                        attempt_index=0,
                        status="success",
                        started_at=started_dt.isoformat(),
                        finished_at=finished_dt.isoformat(),
                        latency_ms=(time.perf_counter() - started_mono) * 1000.0,
                        raw_usage=final_usage,
                        metadata={"provider_class": type(self).__name__, "stream": True},
                    )
                except Exception:
                    pass

            traced_stream._aletheia_cost_traced = True
            cls.stream = traced_stream

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
