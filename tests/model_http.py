"""In-memory HTTP boundary for real model SDK adapter tests."""

import json
from contextlib import contextmanager
from unittest.mock import patch

import httpx


def mock_model_probe(monkeypatch):
    """Replace only model side effects; keep Settings verification/persistence real."""
    from aletheia.model_provider.base import ModelHealth, ModelResponse
    from aletheia.model_provider.openai_compatible import OpenAICompatibleProvider
    from aletheia.model_provider.anthropic_compatible import AnthropicCompatibleProvider

    async def healthy(provider):
        return ModelHealth(provider=provider.name, available=True, model=provider.model, models=[provider.model])

    async def complete(_provider_name, provider, _request, **_kwargs):
        return ModelResponse(content="OK", model=provider.model, usage={})

    monkeypatch.setattr(OpenAICompatibleProvider, "healthcheck", healthy)
    monkeypatch.setattr(AnthropicCompatibleProvider, "healthcheck", healthy)
    monkeypatch.setattr("aletheia.model_provider.broker.brokered_complete", complete)


@contextmanager
def model_http(*, provider="openai_compatible", post=None, get=None):
    async def handle(request):
        if request.method == "POST" and post is not None:
            response = await post(
                str(request.url), json=json.loads(request.content), headers=request.headers,
            )
        elif request.method == "GET" and get is not None:
            response = await get(str(request.url), headers=request.headers)
        else:
            raise AssertionError(f"Unexpected model request: {request.method} {request.url}")
        return httpx.Response(response.status_code, json=response.json(), request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle), timeout=300)
    with patch(f"aletheia.model_provider.{provider}.httpx_client", return_value=client):
        yield client
