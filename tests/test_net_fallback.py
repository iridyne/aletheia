"""Tests for proxy fallback transport in aletheia.net."""
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock

import httpx
import pytest

from aletheia.net import (
    _ProxyFallbackTransport,
    httpx_client,
    invalidate_proxy_cache,
    _proxy_reachable_cache,
)


@pytest.fixture(autouse=True)
def clear_cache():
    _proxy_reachable_cache.clear()
    yield
    _proxy_reachable_cache.clear()


class TestProxyFallbackTransport:
    @pytest.mark.asyncio
    async def test_normal_request_goes_through_proxy(self):
        transport = _ProxyFallbackTransport("http://127.0.0.1:7897")
        mock_response = httpx.Response(200)

        with patch.object(
            transport._proxy_transport, "handle_async_request",
            new_callable=AsyncMock, return_value=mock_response,
        ) as mock_proxy:
            request = httpx.Request("GET", "https://example.com")
            resp = await transport.handle_async_request(request)
            assert resp.status_code == 200
            mock_proxy.assert_called_once()

    @pytest.mark.asyncio
    async def test_proxy_502_falls_back_to_direct(self):
        transport = _ProxyFallbackTransport("http://127.0.0.1:7897")
        proxy_response = httpx.Response(502)
        direct_response = httpx.Response(200)

        with patch.object(
            transport._proxy_transport, "handle_async_request",
            new_callable=AsyncMock, return_value=proxy_response,
        ), patch.object(
            transport._direct_transport, "handle_async_request",
            new_callable=AsyncMock, return_value=direct_response,
        ) as mock_direct:
            request = httpx.Request("GET", "https://example.com")
            resp = await transport.handle_async_request(request)
            assert resp.status_code == 200
            mock_direct.assert_called_once()

    @pytest.mark.asyncio
    async def test_proxy_503_falls_back_to_direct(self):
        transport = _ProxyFallbackTransport("http://127.0.0.1:7897")
        proxy_response = httpx.Response(503)
        direct_response = httpx.Response(200)

        with patch.object(
            transport._proxy_transport, "handle_async_request",
            new_callable=AsyncMock, return_value=proxy_response,
        ), patch.object(
            transport._direct_transport, "handle_async_request",
            new_callable=AsyncMock, return_value=direct_response,
        ):
            request = httpx.Request("GET", "https://example.com")
            resp = await transport.handle_async_request(request)
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_proxy_connect_error_falls_back_to_direct(self):
        transport = _ProxyFallbackTransport("http://127.0.0.1:7897")
        direct_response = httpx.Response(200)

        with patch.object(
            transport._proxy_transport, "handle_async_request",
            new_callable=AsyncMock, side_effect=httpx.ConnectError("refused"),
        ), patch.object(
            transport._direct_transport, "handle_async_request",
            new_callable=AsyncMock, return_value=direct_response,
        ):
            request = httpx.Request("GET", "https://example.com")
            resp = await transport.handle_async_request(request)
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_proxy_error_invalidates_cache(self):
        _proxy_reachable_cache["http://127.0.0.1:7897"] = (True, 999999999.0)
        transport = _ProxyFallbackTransport("http://127.0.0.1:7897")
        proxy_response = httpx.Response(502)
        direct_response = httpx.Response(200)

        with patch.object(
            transport._proxy_transport, "handle_async_request",
            new_callable=AsyncMock, return_value=proxy_response,
        ), patch.object(
            transport._direct_transport, "handle_async_request",
            new_callable=AsyncMock, return_value=direct_response,
        ), patch("aletheia.net._proxy_url", return_value="http://127.0.0.1:7897"):
            request = httpx.Request("GET", "https://example.com")
            await transport.handle_async_request(request)

        cached = _proxy_reachable_cache.get("http://127.0.0.1:7897")
        assert cached is not None
        assert cached[0] is False

    @pytest.mark.asyncio
    async def test_non_proxy_error_propagates(self):
        transport = _ProxyFallbackTransport("http://127.0.0.1:7897")

        with patch.object(
            transport._proxy_transport, "handle_async_request",
            new_callable=AsyncMock, side_effect=httpx.ReadTimeout("timeout"),
        ):
            request = httpx.Request("GET", "https://example.com")
            with pytest.raises(httpx.ReadTimeout):
                await transport.handle_async_request(request)


class TestHttpxClientWithFallback:
    def test_returns_client_with_transport_when_proxy_available(self):
        with patch("aletheia.net._effective_proxy", return_value="http://127.0.0.1:7897"):
            client = httpx_client(timeout=10)
            assert client._transport is not None
            assert isinstance(client._transport, _ProxyFallbackTransport)

    def test_returns_plain_client_when_no_proxy(self):
        with patch("aletheia.net._effective_proxy", return_value=None):
            client = httpx_client(timeout=10)
            assert not isinstance(client._transport, _ProxyFallbackTransport)


class TestInvalidateProxyCache:
    def test_invalidate_clears_cache_and_env(self):
        import os
        os.environ["HTTP_PROXY"] = "http://test:1234"
        os.environ["HTTPS_PROXY"] = "http://test:1234"
        _proxy_reachable_cache["http://test:1234"] = (True, 999999999.0)

        with patch("aletheia.net._proxy_url", return_value="http://test:1234"):
            invalidate_proxy_cache()

        assert _proxy_reachable_cache["http://test:1234"][0] is False
        assert "HTTP_PROXY" not in os.environ
        assert "HTTPS_PROXY" not in os.environ
