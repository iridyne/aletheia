"""Real SDK, synthetic HTTP only: model transport authority boundaries."""

import asyncio
import json
import logging

import httpx
import pytest
from openai import AsyncOpenAI

from aletheia.model_provider.base import ModelRequest
from aletheia.model_provider.openai_compatible import OpenAICompatibleProvider


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol,path", [("chat_completions", "/chat/completions"), ("responses", "/responses")])
@pytest.mark.parametrize("api_key", [None, "configured-test-key"])
async def test_sdk_uses_only_explicit_endpoint_credential_and_preserves_raw_usage(
    monkeypatch, caplog, protocol, path, api_key,
):
    for name in ("API_KEY", "ADMIN_KEY", "ORG_ID", "PROJECT_ID", "WEBHOOK_SECRET"):
        monkeypatch.setenv(f"OPENAI_{name}", "ambient-test-secret")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://unselected.invalid")
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", "Authorization: Bearer ambient-test-secret\nX-Ambient: forbidden")
    seen = []
    raw = {
        "choices": [{"message": {"content": [{"type": "text", "text": "ok"}]}}],
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
        "model": "actual-model",
        "usage": {"input_tokens": 17, "output_tokens": 3, "provider_cost_usd": 0.002},
    }

    def handle(request):
        seen.append(request)
        return httpx.Response(200, json=raw)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), timeout=23) as transport:
        monkeypatch.setattr("aletheia.model_provider.openai_compatible.httpx_client", lambda **_: transport)
        provider = OpenAICompatibleProvider("https://selected.test/v1", "manual-model", protocol, api_key)
        sdk = provider._sdk_client()
        assert isinstance(sdk, AsyncOpenAI)
        assert sdk.max_retries == 0
        assert sdk.timeout.read == 23
        with caplog.at_level(logging.DEBUG):
            response = await provider.complete(ModelRequest(messages=[{"role": "user", "content": "private-test-prompt"}]))
        assert response.content == "ok"
        assert response.model == "actual-model"
        assert response.usage == raw["usage"]
        assert response.raw == raw
        assert len(seen) == 1
        assert str(seen[0].url) == f"https://selected.test/v1{path}"
        assert json.loads(seen[0].content)["model"] == "manual-model"
        assert seen[0].headers.get("authorization") == (f"Bearer {api_key}" if api_key else None)
        for header in ("openai-organization", "openai-project", "x-ambient"):
            assert header not in seen[0].headers
        assert "private-test-prompt" not in caplog.text
        assert "ambient-test-secret" not in caplog.text
        await provider.aclose()
        assert transport.is_closed


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 422, 429, 500, 503])
async def test_sdk_status_failure_is_scrubbed_and_never_implicitly_retried(monkeypatch, status):
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(status, json={"error": {"message": "rejected"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as transport:
        monkeypatch.setattr("aletheia.model_provider.openai_compatible.httpx_client", lambda **_: transport)
        provider = OpenAICompatibleProvider("https://model.test/v1", "m", api_key="synthetic-key")
        with pytest.raises(httpx.HTTPStatusError) as caught:
            await provider.complete(ModelRequest(messages=[]))
        assert len(seen) == 1
        assert caught.value.response.status_code == status
        assert "authorization" not in caught.value.request.headers
        assert "authorization" not in caught.value.response.request.headers
        assert "synthetic-key" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ReadTimeout, asyncio.CancelledError])
async def test_sdk_transport_failure_retains_fallback_or_cancellation_without_retry(monkeypatch, error):
    calls = 0

    def handle(request):
        nonlocal calls
        calls += 1
        raise error("synthetic-sensitive-detail")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as transport:
        monkeypatch.setattr("aletheia.model_provider.openai_compatible.httpx_client", lambda **_: transport)
        provider = OpenAICompatibleProvider("https://model.test/v1", "m", api_key="synthetic-key")
        with pytest.raises(error) as caught:
            await provider.complete(ModelRequest(messages=[]))
        assert calls == 1
        if error is not asyncio.CancelledError:
            assert "synthetic-sensitive-detail" not in str(caught.value)
            assert "authorization" not in caught.value.__context__.request.headers


@pytest.mark.asyncio
@pytest.mark.parametrize("catalogue", [["m1", {"id": "m2"}], {"data": [{"id": "m1"}, "m2"]}])
async def test_sdk_catalogue_keeps_gateway_shapes_without_pagination(monkeypatch, catalogue):
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200, json=catalogue)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as transport:
        monkeypatch.setattr("aletheia.model_provider.openai_compatible.httpx_client", lambda **_: transport)
        provider = OpenAICompatibleProvider("https://model.test/v1", "manual")
        assert [model["slug"] for model in await provider.list_models()] == ["m1", "m2"]
        assert len(seen) == 1
        assert seen[0].url.path == "/v1/models"
