"""Base model provider abstraction."""

from collections.abc import AsyncIterator
import asyncio
from dataclasses import dataclass, field
from typing import Any


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
    """Base class for model providers."""

    name: str = "base"

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
