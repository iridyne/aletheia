"""Base model provider abstraction."""

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
class ModelHealth:
    provider: str
    available: bool
    model: str = ""
    error: str | None = None
    models: list[str] = field(default_factory=list)


class ModelProvider:
    """Base class for model providers."""

    name: str = "base"

    async def complete(self, request: ModelRequest) -> ModelResponse:
        raise NotImplementedError

    async def structured(self, request: StructuredModelRequest, schema: type) -> Any:
        raise NotImplementedError

    async def healthcheck(self) -> ModelHealth:
        raise NotImplementedError
