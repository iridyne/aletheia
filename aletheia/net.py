"""Shared HTTP client factory with automatic proxy fallback.

All outbound HTTP connections in Aletheia go through these helpers so proxy
configuration from config.yaml is applied consistently regardless of how the
gateway process was started (terminal, watchdog, Task Scheduler).

Proxy fallback: when a configured proxy is TCP-reachable but returns HTTP-level
errors (502/503/407) or raises connection errors on actual requests, the
transport automatically retries the request via direct connection and
invalidates the proxy cache so subsequent calls also bypass the proxy until
the TTL expires and re-probes.

Request retry: transient network errors (timeouts, connection errors, 5xx)
are retried with exponential backoff before surfacing the failure.

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
import asyncio
from urllib.parse import urlparse

import aiohttp
import httpx
from aiohttp.resolver import ThreadedResolver

logger = logging.getLogger("aletheia.net")


def _redact_url(url):
    """Mask embedded credentials before logging a URL: scheme://user:pass@host -> scheme://***@host."""
    if not url:
        return str(url)
    try:
        p = urlparse(str(url))
        if p.username or p.password:
            host = p.hostname or ""
            if p.port:
                host = f"{host}:{p.port}"
            return f"{p.scheme}://***@{host}{p.path}"
    except Exception:  # noqa: BLE001 — never let log redaction raise
        return "***"
    return url


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
    exception and includes truncated response body for diagnostics.

    Shared by every model provider so a 401/403 from any LLM backend cannot leak
    the api key through the raised exception. Keeps httpx.HTTPStatusError as the
    type (callers/fallback read ``exc.response.status_code``).
    """
    status = getattr(resp, "status_code", None)
    # Success: 2xx/3xx (matches raise_for_status, which only raises on 4xx/5xx).
    # Fall back to is_success when status_code is unavailable.
    if status is not None:
        if status < 400:
            return
    elif getattr(resp, "is_success", True):
        return
    try:
        for h in _SECRET_HEADERS:
            resp.request.headers.pop(h, None)
    except Exception:
        pass
    try:
        url = str(resp.url)
    except Exception:
        url = "?"
    # Include response body so callers see the upstream error detail (e.g. LiteLLM
    # "model not found", Databricks "invalid token") rather than just the status code.
    # Response bodies don't contain credentials; truncate to 500 chars to avoid noise.
    body_detail = ""
    try:
        try:
            data = resp.json()
            if isinstance(data, dict):
                err = data.get("error") or {}
                body_detail = (
                    (err.get("message") if isinstance(err, dict) else str(err))
                    or data.get("detail")
                    or data.get("message")
                    or str(data)
                )
            else:
                body_detail = str(data)
        except Exception:
            body_detail = resp.text
        body_detail = (body_detail or "").strip()[:500]
    except Exception:
        pass
    msg = f"HTTP {status} from {url}" + (f": {body_detail}" if body_detail else "")
    raise httpx.HTTPStatusError(msg, request=getattr(resp, "request", None), response=resp)

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


def _enforce() -> bool:
    """Read the fail-closed policy (config.network.enforce). False on any error.

    When True, outbound traffic MUST traverse the proxy: an unreachable proxy
    fails the request rather than silently connecting direct (no real-IP leak).
    """
    try:
        from aletheia.config.loader import load_config
        return bool(load_config().network.enforce)
    except Exception:
        return False


def _is_proxy_reachable(url: str) -> bool:
    """TCP-connect to the proxy host:port. Returns False on any error.

    Result is cached for _PROXY_CACHE_TTL seconds so the scheduler doesn't
    pay a TCP round-trip on every job invocation.

    If called from a running asyncio event loop:
    - With a fresh cache: returns the cached value immediately (stale OK) and
      schedules a background refresh — never blocks the event loop.
    - With NO cache at all (cold start): blocks on the probe via executor so
      the first request uses the correct path. A 1s TCP probe is negligible
      vs. the cost of sending traffic direct when a proxy is required.
    """
    import asyncio

    cached = _proxy_reachable_cache.get(url)
    if cached is not None and (time.monotonic() - cached[1] < _PROXY_CACHE_TTL):
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
        # No cache at all — synchronously probe via thread pool so cold-start
        # requests don't silently go direct when the proxy is required.
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(_probe_proxy_sync, url).result(timeout=2)

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
        logger.debug("Proxy %s unreachable — falling back to direct connection", _redact_url(url))
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
        logger.info("Proxy %s marked failed (HTTP-level) — bypassing for %ds", _redact_url(url), int(_PROXY_CACHE_TTL))


def _effective_proxy() -> str | None:
    """Return the proxy URL to use, or None to connect directly.

    Enforce mode (fail-closed): always return the configured proxy, even when the
    reachability probe reports it DOWN — callers must never silently go direct.
    A dead proxy then surfaces as a request error, which the exchange connectivity
    monitor (also on this client) turns into an auto-pause, and which httpx does
    NOT retry-direct in enforce mode.

    Non-enforce (fail-open): return the proxy only when it is reachable, else None
    so httpx_client falls back to a direct connection (availability first).
    """
    url = _proxy_url()
    if not url:
        return None
    if _enforce():
        return url
    if _is_proxy_reachable(url):
        return url
    return None


def _no_proxy_value() -> str:
    """Compose the NO_PROXY bypass list: loopback (always) + config.network.no_proxy.

    Loopback is ALWAYS bypassed so localhost gateway/probe calls never route
    through the proxy — the root cause of the localhost-probe 502 trap.
    """
    bypass = ["127.0.0.1", "localhost", "::1"]
    try:
        from aletheia.config.loader import load_config
        extra = load_config().network.no_proxy or ""
        bypass += [h.strip() for h in extra.split(",") if h.strip()]
    except Exception:
        pass  # intentional — config unreadable at early startup: loopback bypass still applies
    seen: set[str] = set()
    ordered: list[str] = []
    for h in bypass:
        if h not in seen:
            seen.add(h)
            ordered.append(h)
    return ",".join(ordered)


def apply_proxy_env() -> None:
    """Ensure proxy env vars are set for trust_env=True clients (aiohttp, urllib).

    Priority: existing env vars (shell/system) > config.network.proxy_url > none.
    Never clears an existing HTTP(S)_PROXY — it represents the user's actual network
    path (e.g. Clash/v2ray on a different port than config records). ALWAYS
    (re)writes NO_PROXY so loopback/internal traffic stays DIRECT even when a proxy
    is active, and sets ALL_PROXY so SOCKS-aware clients pick the same proxy up.
    """
    existing = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    if not existing:
        proxy = _proxy_url()
        if proxy:
            os.environ["HTTP_PROXY"] = proxy
            os.environ["HTTPS_PROXY"] = proxy
            os.environ["ALL_PROXY"] = proxy
    # NO_PROXY is always refreshed (loopback + config), independent of the proxy.
    no_proxy_val = _no_proxy_value()
    os.environ["NO_PROXY"] = no_proxy_val
    os.environ["no_proxy"] = no_proxy_val


# Transient error types worth retrying at the transport level.
# These are all network-layer or server-overload issues — never 4xx client errors.
_TRANSIENT_EXCEPTIONS = (
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.WriteError,
    httpx.RemoteProtocolError,
    httpx.NetworkError,
)
_TRANSIENT_STATUS_CODES = (500, 502, 503, 504, 507, 508, 520, 522, 524, 429)


class _RetryingTransport(httpx.AsyncBaseTransport):
    """Transport wrapper that retries transient failures with exponential backoff.

    Wraps an inner transport and retries on:
      - ConnectTimeout, ReadTimeout, ConnectError, etc.
      - HTTP 5xx status codes + 429 (rate limit)

    Retry count and backoff are configurable via httpx_client(retry=...).
    Default: 2 retries (3 total attempts) with 0.5s initial backoff.
    """

    def __init__(
        self,
        inner: httpx.AsyncBaseTransport,
        max_retries: int = 2,
        initial_backoff: float = 0.5,
    ) -> None:
        self._inner = inner
        self._max_retries = max_retries
        self._initial_backoff = initial_backoff

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        last_exc: BaseException | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._inner.handle_async_request(request)
                if response.status_code in _TRANSIENT_STATUS_CODES and attempt < self._max_retries:
                    # Consume the response body so the connection can be reused
                    try:
                        await response.aread()
                    except Exception:
                        pass
                    wait = self._initial_backoff * (2 ** attempt)
                    logger.debug(
                        "HTTP %d from %s — retrying in %.1fs (attempt %d/%d)",
                        response.status_code, request.url, wait,
                        attempt + 1, self._max_retries + 1,
                    )
                    await asyncio.sleep(wait)
                    continue
                return response
            except _TRANSIENT_EXCEPTIONS as e:
                last_exc = e
                if attempt < self._max_retries:
                    wait = self._initial_backoff * (2 ** attempt)
                    logger.debug(
                        "Transient %s for %s — retrying in %.1fs (attempt %d/%d)",
                        type(e).__name__, request.url, wait,
                        attempt + 1, self._max_retries + 1,
                    )
                    await asyncio.sleep(wait)
                    continue
                raise
        assert last_exc is not None
        raise last_exc

    async def aclose(self) -> None:
        await self._inner.aclose()


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
                try:
                    await response.aread()
                except Exception:
                    pass
                finally:
                    await response.aclose()
                return await self._direct_transport.handle_async_request(request)
            return response
        except (httpx.ProxyError, httpx.ConnectError, httpx.ConnectTimeout) as e:
            invalidate_proxy_cache()
            logger.debug("Proxy error for %s (%s) — retrying direct", request.url, e)
            return await self._direct_transport.handle_async_request(request)

    async def aclose(self) -> None:
        await self._proxy_transport.aclose()
        await self._direct_transport.aclose()


def httpx_client(
    timeout: float = 30,
    *,
    retry: int | None = None,
    **kwargs,
) -> httpx.AsyncClient:
    """Return an httpx.AsyncClient wired to the managed proxy path.

    - enforce=True: a proxy-only client (fail-closed). A proxy-layer error
      propagates — it is NEVER retried over a direct connection, so the real IP
      cannot leak. (socks5 requires the ``socksio`` extra: ``httpx[socks]``.)
    - enforce=False + reachable proxy: _ProxyFallbackTransport transparently
      retries direct on proxy-layer errors (availability first).
    - no proxy configured (or non-enforce + unreachable): a direct client.

    retry: number of retries for transient network errors (default 2).
           Set to 0 to disable retry entirely. Uses exponential backoff.

    Callers use it identically to a plain httpx.AsyncClient — zero changes needed.
    """
    max_retries = 2 if retry is None else retry
    proxy = _effective_proxy()
    if proxy:
        if _enforce():
            # Enforce mode: proxy-only, but still retry transient errors on the proxy path
            if max_retries > 0:
                transport = _RetryingTransport(
                    httpx.AsyncHTTPTransport(proxy=proxy),
                    max_retries=max_retries,
                )
                return httpx.AsyncClient(
                    transport=transport,
                    timeout=timeout,
                    **kwargs,
                )
            return httpx.AsyncClient(proxy=proxy, timeout=timeout, **kwargs)
        transport: httpx.AsyncBaseTransport = _ProxyFallbackTransport(proxy)
        if max_retries > 0:
            transport = _RetryingTransport(transport, max_retries=max_retries)
        return httpx.AsyncClient(
            transport=transport,
            timeout=timeout,
            **kwargs,
        )
    if max_retries > 0:
        transport = _RetryingTransport(httpx.AsyncHTTPTransport(), max_retries=max_retries)
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
    """Return an aiohttp.ClientSession wired to the managed proxy path.

    HTTP(S) proxy: env-based (trust_env=True). apply_proxy_env() sets
    HTTP(S)_PROXY + ALL_PROXY from config and always refreshes NO_PROXY so
    loopback/internal traffic stays direct. Existing shell/system env proxy wins.

    SOCKS proxy: aiohttp has no native SOCKS support, so we route through an
    aiohttp_socks ProxyConnector (remote DNS via socks5h avoids the c-ares issue).
    Requires the ``aiohttp_socks`` package — imported lazily so it's only needed
    when the user actually selects the socks5 protocol.

    Note: aiohttp/ccxt do NOT transparently fall back to direct like httpx does
    (aiohttp has no fallback transport). Under enforce=True this is exactly the
    fail-closed behaviour we want; under enforce=False an unreachable proxy still
    surfaces as an error that the exchange fallback / connectivity monitor handles.
    """
    timeout = kwargs.pop("timeout", None)
    if timeout is None:
        timeout = aiohttp.ClientTimeout(total=15, connect=8)

    proxy = _effective_proxy()
    if proxy and proxy.lower().startswith("socks"):
        try:
            from aiohttp_socks import ProxyConnector
        except ImportError as e:  # pragma: no cover — dep only needed for socks
            raise RuntimeError(
                "SOCKS proxy configured (proxy_protocol=socks5) but 'aiohttp_socks' "
                "is not installed. Run `uv add aiohttp_socks`, or switch the proxy "
                "protocol to http in Settings → Network."
            ) from e
        connector = ProxyConnector.from_url(proxy)
        return aiohttp.ClientSession(connector=connector, timeout=timeout, **kwargs)

    # HTTP proxy (or direct) via env.
    apply_proxy_env()
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


def make_ccxt(name: str, **options):
    """Build a ccxt.async_support exchange with the managed proxy session injected.

    This is the ONLY sanctioned way to construct an exchange client (spec
    NET-S03/S04): it guarantees the aiohttp session carries the configured proxy,
    so exchange REST traffic can never silently bypass the managed egress path.
    Caller keyword args (apiKey/secret/password/options/...) are merged over the
    defaults (timeout + enableRateLimit + session).

    Returns ``(exchange, session)``. The caller OWNS the session and must close it
    (ccxt will not, since we inject our own — ``own_session=False`` semantics).
    Post-construction tweaks like ``exchange.set_sandbox_mode(True)`` are the
    caller's responsibility.
    """
    import ccxt.async_support as ccxt

    if not hasattr(ccxt, name):
        raise ValueError(f"unknown ccxt exchange: {name}")
    session = aiohttp_session()
    opts = {**ccxt_timeout_options(), "enableRateLimit": True, "session": session}
    opts.update(options)
    exchange = getattr(ccxt, name)(opts)
    return exchange, session


def build_urllib_opener():
    """Return a urllib opener routing external requests through the managed proxy.

    For the few SYNC callers (webhook POST, polymarket urllib fallback) that can't
    use the async httpx_client. Honours enforce / reachability via _effective_proxy;
    loopback stays direct. SOCKS is not supported by urllib without PySocks, so a
    socks5 proxy degrades this opener to direct (only affects the best-effort
    webhook — all primary egress uses httpx/aiohttp which DO support socks).
    """
    import urllib.request

    proxy = _effective_proxy()
    if proxy and not proxy.lower().startswith("socks"):
        handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    else:
        # No proxy / non-enforce-unreachable / socks → explicit no-env-proxy handler
        # so we never accidentally route through a stale env proxy.
        handler = urllib.request.ProxyHandler({})
    return urllib.request.build_opener(handler)
