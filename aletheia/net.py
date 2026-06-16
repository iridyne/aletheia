"""Shared HTTP client factory.

All outbound HTTP connections in Aletheia go through these helpers so proxy
configuration from config.yaml is applied consistently regardless of how the
gateway process was started (terminal, watchdog, Task Scheduler).

Usage:
    from aletheia.net import httpx_client, aiohttp_session

    async with httpx_client(timeout=30) as client:
        r = await client.get("https://example.com")

    session = aiohttp_session()   # pass to CCXTProvider
"""

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


def httpx_client(timeout: float = 30, **kwargs) -> httpx.AsyncClient:
    """Return an httpx.AsyncClient with proxy from config.

    Drop-in replacement for ``httpx.AsyncClient(timeout=X)``. Use as a
    context manager or pass kwargs through unchanged. Uses direct connection
    if the configured proxy is not reachable.
    """
    proxy = _effective_proxy()
    return httpx.AsyncClient(
        proxy=proxy,
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
