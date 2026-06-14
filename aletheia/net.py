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

import os
import httpx
import aiohttp
from aiohttp.resolver import ThreadedResolver


def _proxy_url() -> str | None:
    """Read proxy_url from config. Returns None if not configured."""
    try:
        from aletheia.config.loader import load_config
        return load_config().network.proxy_url or None
    except Exception:
        return None


def apply_proxy_env() -> None:
    """Ensure HTTP_PROXY / HTTPS_PROXY env vars reflect the current config.

    Called at gateway startup and after config updates so that trust_env=True
    on all clients (including CCXT's aiohttp session) picks up the right proxy.
    """
    proxy = _proxy_url()
    if proxy:
        os.environ["HTTP_PROXY"] = proxy
        os.environ["HTTPS_PROXY"] = proxy
    else:
        os.environ.pop("HTTP_PROXY", None)
        os.environ.pop("HTTPS_PROXY", None)


def httpx_client(timeout: float = 30, **kwargs) -> httpx.AsyncClient:
    """Return an httpx.AsyncClient with proxy from config.

    Drop-in replacement for ``httpx.AsyncClient(timeout=X)``. Use as a
    context manager or pass kwargs through unchanged.
    """
    proxy = _proxy_url()
    return httpx.AsyncClient(
        proxy=proxy,
        timeout=timeout,
        **kwargs,
    )


def aiohttp_session(**kwargs) -> aiohttp.ClientSession:
    """Return an aiohttp.ClientSession with proxy from config.

    Sets HTTP_PROXY / HTTPS_PROXY env vars before creating the session so
    that trust_env=True picks them up. Windows-safe: uses ThreadedResolver
    (OS getaddrinfo) instead of c-ares AsyncResolver.
    """
    apply_proxy_env()
    connector = aiohttp.TCPConnector(resolver=ThreadedResolver())
    return aiohttp.ClientSession(
        connector=connector,
        trust_env=True,
        **kwargs,
    )
