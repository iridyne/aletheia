"""OAuth 2.0 PKCE engine — shared by OpenAI and Claude subscription providers.

Implements the core OAuth primitives without provider-specific knowledge:
  - PKCE code_verifier / code_challenge generation (S256)
  - State parameter generation
  - Authorization URL construction
  - Token exchange (authorization_code grant)
  - Token refresh (refresh_token grant)
  - Local callback HTTP server for receiving authorization codes
"""

import asyncio
import hashlib
import logging
import os
import secrets
import time
from base64 import urlsafe_b64encode
from dataclasses import dataclass, field
from urllib.parse import urlencode

import httpx
from aiohttp import web

logger = logging.getLogger("aletheia.model_provider.oauth")

# ─── PKCE ────────────────────────────────────────────────────────────────────


def generate_pkce() -> tuple[str, str]:
    """Generate (code_verifier, code_challenge) for S256 PKCE."""
    verifier_bytes = secrets.token_bytes(32)
    code_verifier = urlsafe_b64encode(verifier_bytes).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


def generate_state() -> str:
    """Generate a random 32-byte hex state parameter."""
    return secrets.token_hex(32)


# ─── OAuth config dataclass ──────────────────────────────────────────────────


@dataclass
class OAuthConfig:
    client_id: str
    auth_url: str
    token_url: str
    redirect_uri: str
    scopes: str
    extra_params: dict[str, str] = field(default_factory=dict)
    token_request_format: str = "form"  # "form" or "json"


# ─── URL construction ────────────────────────────────────────────────────────


def build_auth_url(config: OAuthConfig, code_challenge: str, state: str) -> str:
    """Construct the full authorization URL with PKCE and state."""
    params = {
        "client_id": config.client_id,
        "response_type": "code",
        "redirect_uri": config.redirect_uri,
        "scope": config.scopes,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    params.update(config.extra_params)
    return f"{config.auth_url}?{urlencode(params)}"


# ─── Token exchange ──────────────────────────────────────────────────────────


async def exchange_code(
    config: OAuthConfig, code: str, code_verifier: str, state: str | None = None
) -> dict:
    """Exchange authorization code for tokens. Returns raw token response dict."""
    body: dict = {
        "grant_type": "authorization_code",
        "client_id": config.client_id,
        "code": code,
        "redirect_uri": config.redirect_uri,
        "code_verifier": code_verifier,
    }
    if state:
        body["state"] = state

    headers: dict = {}
    if config.token_request_format == "json":
        headers["Content-Type"] = "application/json"
    else:
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    async with httpx.AsyncClient(timeout=30) as client:
        if config.token_request_format == "json":
            resp = await client.post(config.token_url, json=body, headers=headers)
        else:
            resp = await client.post(config.token_url, data=body, headers=headers)
        resp.raise_for_status()
        return resp.json()


async def refresh_tokens(config: OAuthConfig, refresh_token: str) -> dict:
    """Refresh tokens using a refresh_token grant. Returns raw response dict."""
    body: dict = {
        "grant_type": "refresh_token",
        "client_id": config.client_id,
        "refresh_token": refresh_token,
    }

    headers: dict = {}
    if config.token_request_format == "json":
        headers["Content-Type"] = "application/json"
    else:
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    async with httpx.AsyncClient(timeout=30) as client:
        if config.token_request_format == "json":
            resp = await client.post(config.token_url, json=body, headers=headers)
        else:
            resp = await client.post(config.token_url, data=body, headers=headers)
        resp.raise_for_status()
        return resp.json()


async def token_exchange_rfc8693(
    token_url: str, client_id: str, id_token: str, requested_token: str
) -> dict:
    """RFC 8693 Token Exchange — convert id_token into a different token type."""
    body = {
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
        "client_id": client_id,
        "subject_token": id_token,
        "subject_token_type": "urn:ietf:params:oauth:token-type:id_token",
        "requested_token": requested_token,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            token_url, data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        return resp.json()


# ─── Local callback server ───────────────────────────────────────────────────


_SUCCESS_HTML = """<!DOCTYPE html><html><head><title>Authorization Complete</title>
<style>body{font-family:system-ui;display:flex;justify-content:center;align-items:center;
height:100vh;margin:0;background:#1a1a2e;color:#e0e0e0}
.card{text-align:center;padding:2rem;border-radius:8px;background:#16213e}
</style></head><body><div class="card"><h2>Authorization Complete</h2>
<p>You can close this tab and return to Aletheia.</p></div></body></html>"""

_ERROR_HTML = """<!DOCTYPE html><html><head><title>Authorization Failed</title>
<style>body{font-family:system-ui;display:flex;justify-content:center;align-items:center;
height:100vh;margin:0;background:#1a1a2e;color:#e0e0e0}
.card{text-align:center;padding:2rem;border-radius:8px;background:#16213e}
.err{color:#ff6b6b}</style></head><body><div class="card"><h2 class="err">Authorization Failed</h2>
<p>{error}</p></div></body></html>"""


class CallbackServer:
    """Ephemeral localhost HTTP server that receives an OAuth callback."""

    def __init__(self, port: int = 1455, path: str = "/auth/callback"):
        self.port = port
        self.path = path
        self._result: asyncio.Future | None = None
        self._runner: web.AppRunner | None = None

    async def start(self, expected_state: str) -> None:
        """Start serving. Call await_code() to get the authorization code."""
        self._result = asyncio.get_event_loop().create_future()
        self._expected_state = expected_state

        app = web.Application()
        app.router.add_get(self.path, self._handle_callback)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", self.port)
        await site.start()
        logger.info("OAuth callback server listening on 127.0.0.1:%d%s", self.port, self.path)

    async def _handle_callback(self, request: web.Request) -> web.Response:
        """Handle the OAuth redirect callback."""
        code = request.query.get("code")
        state = request.query.get("state")
        error = request.query.get("error")

        if error:
            desc = request.query.get("error_description", error)
            if self._result and not self._result.done():
                self._result.set_exception(RuntimeError(f"OAuth error: {desc}"))
            return web.Response(
                text=_ERROR_HTML.format(error=desc),
                content_type="text/html",
            )

        if not code:
            if self._result and not self._result.done():
                self._result.set_exception(RuntimeError("No code in callback"))
            return web.Response(
                text=_ERROR_HTML.format(error="No authorization code received"),
                content_type="text/html",
            )

        if state != self._expected_state:
            if self._result and not self._result.done():
                self._result.set_exception(RuntimeError("State mismatch — possible CSRF"))
            return web.Response(
                text=_ERROR_HTML.format(error="State parameter mismatch"),
                content_type="text/html",
            )

        if self._result and not self._result.done():
            self._result.set_result(code)

        return web.Response(text=_SUCCESS_HTML, content_type="text/html")

    async def await_code(self, timeout: float = 300) -> str:
        """Wait for the authorization code. Raises on timeout or error."""
        if not self._result:
            raise RuntimeError("Server not started")
        try:
            return await asyncio.wait_for(self._result, timeout=timeout)
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Shutdown the callback server."""
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
            logger.info("OAuth callback server stopped")
