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

# Request headers that carry credentials. httpx's HTTPStatusError keeps a reference
# to the originating Request (directly and via response.request), whose headers hold
# the bearer token / api key. A full-traceback log or any access to
# exc.request.headers would leak it. raise_for_status_without_secrets() scrubs these
# in place before raising (the request is single-use) — error-leak 2.7.
_SECRET_HEADERS = (
    "authorization", "x-api-key", "api-key", "cookie",
    "x-goog-api-key", "openai-organization", "openai-project",
)


def raise_for_status_without_secrets(resp: "httpx.Response") -> None:
    """Like ``resp.raise_for_status()`` but strips credential headers from the
    exception and uses a message naming only status + URL (no headers/body).

    Shared by every model provider so a 401/403 from any LLM backend cannot leak
    the api key through the raised exception. Keeps httpx.HTTPStatusError as the
    type (callers/fallback read ``exc.response.status_code``).
    """
    if resp.is_success:
        return
    try:
        for h in _SECRET_HEADERS:
            resp.request.headers.pop(h, None)
    except Exception:
        pass
    msg = f"HTTP {resp.status_code} from {resp.url}"
    raise httpx.HTTPStatusError(msg, request=resp.request, response=resp)

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

    If called from a running asyncio event loop, returns the cached value
    immediately (stale OK) and schedules a background refresh — never blocks
    the event loop with a synchronous TCP connect.
    """
    import asyncio

    cached = _proxy_reachable_cache.get(url)
    cache_fresh = cached is not None and (time.monotonic() - cached[1] < _PROXY_CACHE_TTL)

    if cache_fresh:
        return cached[0]

    # If we're on an event loop thread, return stale cache or False and refresh async
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None:
        if cached is not None:
            # Schedule background refresh, return stale value now
            loop.run_in_executor(None, _probe_proxy_sync, url)
            return cached[0]
        # No cache at all — return False (assume direct) and kick off probe
        loop.run_in_executor(None, _probe_proxy_sync, url)
        return False

    # Not on event loop — safe to block
    return _probe_proxy_sync(url)


def _probe_proxy_sync(url: str) -> bool:
    """Blocking TCP probe. Safe to call from worker threads."""
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
    """Mark the configured proxy as failed (HTTP-level) so subsequent httpx calls bypass it.

    Called automatically by _ProxyFallbackTransport on proxy errors. Only
    invalidates the in-memory cache — does NOT clear env vars, since those may
    point to a working proxy port that differs from config (e.g. Clash on 7897
    while config records stale 7890).
    """
    url = _proxy_url()
    if url:
        _proxy_reachable_cache[url] = (False, time.monotonic())
        logger.info("Proxy %s marked failed (HTTP-level) — bypassing for %ds", url, int(_PROXY_CACHE_TTL))


def _effective_proxy() -> str | None:
    """Return the proxy URL only if it is actually reachable; else None."""
    url = _proxy_url()
    if not url:
        return None
    if _is_proxy_reachable(url):
        return url
    return None


def apply_proxy_env() -> None:
    """Ensure HTTP_PROXY / HTTPS_PROXY env vars are set for trust_env=True clients.

    Priority: existing env vars (shell/system) > config.network.proxy_url > none.
    Never clears env vars that were already present — they represent the user's
    actual network path (e.g. Clash/v2ray on a different port than config records).
    """
    existing = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    if existing:
        # Env already set (by shell profile, system, or prior call) — don't touch.
        return
    proxy = _proxy_url()
    if proxy:
        os.environ["HTTP_PROXY"] = proxy
        os.environ["HTTPS_PROXY"] = proxy


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

    Respects existing HTTP_PROXY/HTTPS_PROXY env vars (e.g. set by the system
    or shell profile). Only falls back to config.network.proxy_url when no env
    var is present. Never clears env vars — the scheduler's fresh event loop
    previously caused apply_proxy_env() to race and clear valid proxy settings.
    """
    if not os.environ.get("HTTPS_PROXY") and not os.environ.get("HTTP_PROXY"):
        proxy = _proxy_url()
        if proxy:
            os.environ["HTTP_PROXY"] = proxy
            os.environ["HTTPS_PROXY"] = proxy
    timeout = kwargs.pop("timeout", None)
    if timeout is None:
        timeout = aiohttp.ClientTimeout(total=15, connect=8)
    connector = aiohttp.TCPConnector(resolver=ThreadedResolver())
    return aiohttp.ClientSession(
        connector=connector,
        timeout=timeout,
        trust_env=True,
        **kwargs,
    )


def ccxt_timeout_options() -> dict:
    """Return CCXT-compatible timeout options dict (milliseconds).

    Apply to any ccxt exchange constructor: ``ccxt.okx({**ccxt_timeout_options(), ...})``
    """
    return {"timeout": 15000}
