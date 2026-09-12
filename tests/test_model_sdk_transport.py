"""Real SDK, synthetic HTTP only: model transport authority boundaries."""

import asyncio
import json
import logging

import httpx
import pytest
from openai import AsyncOpenAI
from anthropic import AsyncAnthropic

from aletheia.model_provider.base import ModelRequest
from aletheia.model_provider.anthropic_compatible import AnthropicCompatibleProvider
from aletheia.model_provider.openai_compatible import OpenAICompatibleProvider


@pytest.fixture(params=[
    (OpenAICompatibleProvider, {"protocol": "chat_completions"}, AsyncOpenAI, "/chat/completions", "authorization"),
    (OpenAICompatibleProvider, {"protocol": "responses"}, AsyncOpenAI, "/responses", "authorization"),
    (AnthropicCompatibleProvider, {"auth_scheme": "x-api-key"}, AsyncAnthropic, "/v1/messages", "x-api-key"),
    (AnthropicCompatibleProvider, {"auth_scheme": "bearer"}, AsyncAnthropic, "/v1/messages", "authorization"),
], ids=["chat", "responses", "anthropic-key", "anthropic-bearer"])
def endpoint(request, monkeypatch):
    provider_type, kwargs, sdk_type, path, auth_header = request.param

    def connect(transport, api_key=None):
        monkeypatch.setattr(f"{provider_type.__module__}.httpx_client", lambda **_: transport)
        return provider_type("https://selected.test/gateway", "manual-model", api_key=api_key, **kwargs)

    return connect, sdk_type, path, auth_header


@pytest.mark.asyncio
@pytest.mark.parametrize("api_key", [None, "configured-test-key"])
async def test_sdk_uses_only_explicit_endpoint_credential_and_preserves_raw_usage(
    monkeypatch, caplog, endpoint, api_key,
):
    connect, sdk_type, path, auth_header = endpoint
    for name in ("API_KEY", "ADMIN_KEY", "ORG_ID", "PROJECT_ID", "WEBHOOK_SECRET"):
        monkeypatch.setenv(f"OPENAI_{name}", "ambient-test-secret")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://unselected.invalid")
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", "Authorization: Bearer ambient-test-secret\nX-Ambient: forbidden")
    for name in ("API_KEY", "AUTH_TOKEN", "WEBHOOK_SIGNING_KEY", "IDENTITY_TOKEN"):
        monkeypatch.setenv(f"ANTHROPIC_{name}", "ambient-test-secret")
    monkeypatch.setenv("ANTHROPIC_PROFILE", "unselected-profile")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://unselected.invalid")
    monkeypatch.setenv("ANTHROPIC_CUSTOM_HEADERS", "X-Api-Key: ambient-test-secret\nX-Ambient: forbidden")
    def forbidden_discovery(**_):
        raise AssertionError("SDK credential discovery must not run")
    monkeypatch.setattr("anthropic._client.default_credentials", forbidden_discovery)
    seen = []
    raw = {
        "choices": [{"message": {"content": [{"type": "text", "text": "ok"}]}}],
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
        "content": [{"type": "text", "text": "ok"}],
        "model": "actual-model",
        "usage": {"input_tokens": 17, "output_tokens": 3, "provider_cost_usd": 0.002},
    }

    def handle(request):
        seen.append(request)
        return httpx.Response(200, json=raw)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), timeout=23) as transport:
        provider = connect(transport, api_key)
        sdk = provider._sdk_client()
        assert isinstance(sdk, sdk_type)
        assert sdk.max_retries == 0
        assert sdk.timeout.read == 23
        with caplog.at_level(logging.DEBUG):
            response = await provider.complete(ModelRequest(messages=[{"role": "user", "content": "private-test-prompt"}]))
        assert response.content == "ok"
        assert response.model == "actual-model"
        assert response.usage == raw["usage"]
        assert response.raw == raw
        assert len(seen) == 1
        assert seen[0].extensions["timeout"]["read"] == 23
        assert seen[0].headers["user-agent"].startswith(f"{sdk_type.__name__}/Python ")
        assert str(seen[0].url) == f"https://selected.test/gateway{path}"
        payload = json.loads(seen[0].content)
        assert payload["model"] == "manual-model"
        expected_key = f"Bearer {api_key}" if auth_header == "authorization" and api_key else api_key
        assert seen[0].headers.get(auth_header) == expected_key
        other_header = "x-api-key" if auth_header == "authorization" else "authorization"
        assert other_header not in seen[0].headers
        if sdk_type is AsyncAnthropic:
            assert sdk.credentials is None
            assert seen[0].headers["anthropic-version"] == "2023-06-01"
            assert ("temperature" in payload) == (auth_header == "x-api-key")
        for header in ("openai-organization", "openai-project", "x-ambient"):
            assert header not in seen[0].headers
        assert "private-test-prompt" not in caplog.text
        assert "ambient-test-secret" not in caplog.text
        await provider.aclose()
        assert transport.is_closed


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 422, 429, 500, 503])
async def test_sdk_status_failure_is_scrubbed_and_never_implicitly_retried(endpoint, status):
    connect, _, _, _ = endpoint
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(status, json={"error": {"message": "rejected"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as transport:
        provider = connect(transport, "synthetic-key")
        with pytest.raises(httpx.HTTPStatusError) as caught:
            await provider.complete(ModelRequest(messages=[]))
        assert len(seen) == 1
        assert caught.value.response.status_code == status
        assert "authorization" not in caught.value.request.headers
        assert "authorization" not in caught.value.response.request.headers
        assert "x-api-key" not in caught.value.request.headers
        assert "synthetic-key" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ReadTimeout, asyncio.CancelledError])
async def test_sdk_transport_failure_retains_fallback_or_cancellation_without_retry(endpoint, error):
    connect, _, _, _ = endpoint
    calls = 0

    def handle(request):
        nonlocal calls
        calls += 1
        raise error("synthetic-sensitive-detail")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as transport:
        provider = connect(transport, "synthetic-key")
        with pytest.raises(error) as caught:
            await provider.complete(ModelRequest(messages=[]))
        assert calls == 1
        if error is not asyncio.CancelledError:
            assert "synthetic-sensitive-detail" not in str(caught.value)
            assert "authorization" not in caught.value.__context__.request.headers
            assert "x-api-key" not in caught.value.__context__.request.headers


@pytest.mark.asyncio
@pytest.mark.parametrize("catalogue", [["m1", {"id": "m2"}], {"data": [{"id": "m1"}, "m2"]}])
async def test_sdk_catalogue_keeps_gateway_shapes_without_pagination(endpoint, catalogue):
    connect, sdk_type, _, _ = endpoint
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200, json=catalogue)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as transport:
        provider = connect(transport)
        assert [model["slug"] for model in await provider.list_models()] == ["m1", "m2"]
        assert len(seen) == 1
        assert seen[0].url.path == "/gateway" + ("/v1/models" if sdk_type is AsyncAnthropic else "/models")
