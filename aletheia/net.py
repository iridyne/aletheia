"""Shared HTTP client factory with automatic proxy fallback.

All outbound HTTP connections in Aletheia go through these helpers so proxy
configuration from config.yaml is applied consistently regardless of how the
gateway process was started (terminal, watchdog, Task Scheduler).

Proxy fallback: when a configured proxy is TCP-reachable but returns HTTP-level
errors (502/503/407) or raises connection errors on actual requests, the
transport automatically retries the request via direct connection and
invalidates the proxy cache so subsequent calls also bypass the proxy until
the TTL expires and re-probes.

Usage:
    from aletheia.net import httpx_client, aiohttp_session

    async with httpx_client(timeout=30) as client:
        r = await client.get("https://example.com")

    session = aiohttp_session()   # pass to CCXTProvider
"""

from __future__ import annotations

import logging
import os
import socket
import time
from urllib.parse import urlparse

import aiohttp
import httpx
from aiohttp.resolver import ThreadedResolver

logger = logging.getLogger("aletheia.net")

# Reachability probe result cache: avoids a TCP connect on every aiohttp_session()
# call while still recovering within a minute if the proxy comes up later.
_proxy_reachable_cache: dict[str, tuple[bool, float]] = {}
_PROXY_CACHE_TTL = 60.0   # seconds


def _proxy_url() -> str | None:
    """Read proxy_url from config. Returns None if not configured."""
    try:
        from aletheia.config.loader import load_config
        return load_config().network.proxy_url or None
    except Exception:
        return None


def _is_proxy_reachable(url: str) -> bool:
    """TCP-connect to the proxy host:port. Returns False on any error.

    Result is cached for _PROXY_CACHE_TTL seconds so the scheduler doesn't
    pay a TCP round-trip on every job invocation.
    """
    cached = _proxy_reachable_cache.get(url)
    if cached is not None:
        ok, ts = cached
        if time.monotonic() - ts < _PROXY_CACHE_TTL:
            return ok

    try:
        parsed = urlparse(url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 1080
        with socket.create_connection((host, port), timeout=1.0):
            pass
        _proxy_reachable_cache[url] = (True, time.monotonic())
        return True
    except OSError:
        _proxy_reachable_cache[url] = (False, time.monotonic())
        logger.debug("Proxy %s unreachable — falling back to direct connection", url)
        return False


def invalidate_proxy_cache() -> None:
    """Mark the configured proxy as failed (HTTP-level) so subsequent calls bypass it.

    Called automatically by _ProxyFallbackTransport on proxy errors. Also clears
    env vars so aiohttp_session() callers immediately benefit.
    """
    url = _proxy_url()
    if url:
        _proxy_reachable_cache[url] = (False, time.monotonic())
        logger.info("Proxy %s marked failed (HTTP-level) — bypassing for %ds", url, int(_PROXY_CACHE_TTL))
    os.environ.pop("HTTP_PROXY", None)
    os.environ.pop("HTTPS_PROXY", None)


def _effective_proxy() -> str | None:
    """Return the proxy URL only if it is actually reachable; else None."""
    url = _proxy_url()
    if not url:
        return None
    if _is_proxy_reachable(url):
        return url
    return None


def apply_proxy_env() -> None:
    """Ensure HTTP_PROXY / HTTPS_PROXY env vars reflect the current config.

    Called at gateway startup and after config updates so that trust_env=True
    on all clients (including CCXT's aiohttp session) picks up the right proxy.
    Falls back to direct connection if the configured proxy is not reachable.
    """
    proxy = _effective_proxy()
    if proxy:
        os.environ["HTTP_PROXY"] = proxy
        os.environ["HTTPS_PROXY"] = proxy
    else:
        os.environ.pop("HTTP_PROXY", None)
        os.environ.pop("HTTPS_PROXY", None)


class _ProxyFallbackTransport(httpx.AsyncBaseTransport):
    """Transport that tries proxy first; on proxy-layer errors, retries direct.

    Catches HTTP 502/503/407 responses and connection-level proxy errors,
    invalidates the proxy cache, and transparently retries via direct connection.
    This makes proxy fallback invisible to all httpx_client() consumers.
    """

    _PROXY_ERROR_CODES = (502, 503, 407)

    def __init__(self, proxy_url: str) -> None:
        self._proxy_transport = httpx.AsyncHTTPTransport(proxy=proxy_url)
        self._direct_transport = httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        try:
            response = await self._proxy_transport.handle_async_request(request)
            if response.status_code in self._PROXY_ERROR_CODES:
                invalidate_proxy_cache()
                logger.debug("Proxy returned %d for %s — retrying direct", response.status_code, request.url)
                return await self._direct_transport.handle_async_request(request)
            return response
        except (httpx.ProxyError, httpx.ConnectError, httpx.ConnectTimeout) as e:
            invalidate_proxy_cache()
            logger.debug("Proxy error for %s (%s) — retrying direct", request.url, e)
            return await self._direct_transport.handle_async_request(request)

    async def aclose(self) -> None:
        await self._proxy_transport.aclose()
        await self._direct_transport.aclose()


def httpx_client(timeout: float = 30, **kwargs) -> httpx.AsyncClient:
    """Return an httpx.AsyncClient with automatic proxy fallback.

    When proxy is configured and reachable (TCP probe), uses _ProxyFallbackTransport
    which transparently retries via direct connection on proxy-layer errors.
    Callers use it identically to a plain httpx.AsyncClient — zero code changes needed.
    """
    proxy = _effective_proxy()
    if proxy:
        transport = _ProxyFallbackTransport(proxy)
        return httpx.AsyncClient(
            transport=transport,
            timeout=timeout,
            **kwargs,
        )
    return httpx.AsyncClient(
        timeout=timeout,
        **kwargs,
    )


def aiohttp_session(**kwargs) -> aiohttp.ClientSession:
    """Return an aiohttp.ClientSession with proxy from config.

    Sets HTTP_PROXY / HTTPS_PROXY env vars before creating the session so
    that trust_env=True picks them up. Windows-safe: uses ThreadedResolver
    (OS getaddrinfo) instead of c-ares AsyncResolver. Falls back to direct
    connection if the configured proxy is not reachable.
    """
    apply_proxy_env()
    connector = aiohttp.TCPConnector(resolver=ThreadedResolver())
    return aiohttp.ClientSession(
        connector=connector,
        trust_env=True,
        **kwargs,
    )
