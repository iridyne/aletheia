"""OpenAI subscription OAuth — ChatGPT Plus/Pro → API key via PKCE + RFC 8693.

Uses the official Codex CLI public client registration (same client_id as
openai/codex). Flow:
  1. PKCE authorize → user logs in with ChatGPT account
  2. Callback on localhost:1455 receives authorization code
  3. Code → token exchange → id_token + access_token + refresh_token
  4. id_token → RFC 8693 Token Exchange → OpenAI API key
  5. API key stored; refresh_token used to renew before expiry

We store NO raw tokens in config — only encrypted secret refs.
"""

import asyncio
import logging
import time

from aletheia.files.jsonl import write_audit
from aletheia.model_provider.oauth import (
    OAuthConfig, CallbackServer,
    generate_pkce, generate_state, build_auth_url,
    exchange_code, refresh_tokens, token_exchange_rfc8693,
)

logger = logging.getLogger("aletheia.model_provider.openai_oauth")

# ─── OAuth configuration (official Codex CLI public client) ──────────────────

OPENAI_OAUTH_CONFIG = OAuthConfig(
    client_id="app_EMoamEEZ73f0CkXaXp7hrann",
    auth_url="https://auth.openai.com/oauth/authorize",
    token_url="https://auth.openai.com/oauth/token",
    redirect_uri="http://localhost:1455/auth/callback",
    scopes="openid profile email offline_access",
    extra_params={
        "codex_cli_simplified_flow": "true",
        "id_token_add_organizations": "true",
    },
    token_request_format="form",
)

_CALLBACK_PORT = 1455
_CALLBACK_PATH = "/auth/callback"
_AUTH_TIMEOUT = 300  # 5 min for user to complete browser auth


# ─── Session state machine ───────────────────────────────────────────────────


class OpenAILoginSession:
    def __init__(self):
        self.state = "idle"  # idle | awaiting | completed | failed
        self.auth_url: str | None = None
        self.error: str | None = None
        self.api_key: str | None = None
        self.refresh_token: str | None = None
        self.id_token: str | None = None
        self.expires_at: int | None = None
        self._code_verifier: str | None = None
        self._expected_state: str | None = None
        self._server: CallbackServer | None = None
        self._task: asyncio.Task | None = None

    def public(self) -> dict:
        return {
            "state": self.state,
            "auth_url": self.auth_url,
            "error": self.error,
        }


_session = OpenAILoginSession()
_lock = asyncio.Lock()


async def start_login() -> dict:
    """Begin OpenAI OAuth flow. Returns {state, auth_url} for frontend to open."""
    global _session
    async with _lock:
        if _session.state == "awaiting":
            return _session.public()

        _session = OpenAILoginSession()
        code_verifier, code_challenge = generate_pkce()
        state = generate_state()

        _session._code_verifier = code_verifier
        _session._expected_state = state
        _session.auth_url = build_auth_url(OPENAI_OAUTH_CONFIG, code_challenge, state)
        _session.state = "awaiting"

        # Start callback server + background exchange task
        server = CallbackServer(port=_CALLBACK_PORT, path=_CALLBACK_PATH)
        _session._server = server
        try:
            await server.start(expected_state=state)
        except Exception as e:
            _session.state = "failed"
            _session.error = f"callback_server_failed: {e}"
            return _session.public()

        _session._task = asyncio.create_task(_await_and_exchange())

        write_audit({
            "actor": "human", "source": "dashboard",
            "event_type": "openai_oauth_started", "entity_type": "model_provider",
            "entity_id": "openai_native", "message": "OpenAI OAuth login started",
        })
        return _session.public()


async def _await_and_exchange() -> None:
    """Background: wait for callback code, then do token + key exchange."""
    try:
        server = _session._server
        if server is None:
            raise RuntimeError("callback server not initialized")
        code = await server.await_code(timeout=_AUTH_TIMEOUT)

        # Step 1: code → tokens
        tokens = await exchange_code(
            OPENAI_OAUTH_CONFIG, code, _session._code_verifier
        )
        id_token = tokens.get("id_token")
        refresh_token = tokens.get("refresh_token")
        access_token = tokens.get("access_token")
        expires_in = tokens.get("expires_in", 3600)

        if not id_token:
            _session.state = "failed"
            _session.error = "no_id_token"
            return

        # Step 2: id_token → API key (RFC 8693 Token Exchange)
        key_resp = await token_exchange_rfc8693(
            token_url=OPENAI_OAUTH_CONFIG.token_url,
            client_id=OPENAI_OAUTH_CONFIG.client_id,
            id_token=id_token,
            requested_token="openai-api-key",
        )
        api_key = key_resp.get("access_token")
        if not api_key:
            _session.state = "failed"
            _session.error = "token_exchange_no_key"
            return

        _session.api_key = api_key
        _session.refresh_token = refresh_token
        _session.id_token = id_token
        _session.expires_at = int(time.time() * 1000) + (expires_in * 1000)
        _session.state = "completed"
        _session.error = None

        write_audit({
            "actor": "human", "source": "dashboard",
            "event_type": "openai_oauth_completed", "entity_type": "model_provider",
            "entity_id": "openai_native", "message": "OpenAI OAuth login completed",
        })

    except asyncio.TimeoutError:
        _session.state = "failed"
        _session.error = "timeout"
    except Exception as e:
        _session.state = "failed"
        _session.error = str(e)[:200]
        logger.warning("OpenAI OAuth exchange failed: %s", e)


def get_status() -> dict:
    """Return current login session state (never tokens)."""
    return _session.public()


async def cancel() -> dict:
    """Cancel an in-progress login."""
    if _session._server:
        await _session._server.stop()
    if _session._task and not _session._task.done():
        _session._task.cancel()
    _session.state = "idle"
    return _session.public()


def get_tokens() -> dict | None:
    """Return tokens after successful login. Called by route to persist."""
    if _session.state != "completed":
        return None
    return {
        "api_key": _session.api_key,
        "refresh_token": _session.refresh_token,
        "id_token": _session.id_token,
        "expires_at": _session.expires_at,
    }


# ─── Token refresh ───────────────────────────────────────────────────────────


async def refresh_openai_tokens(refresh_token_value: str) -> dict:
    """Refresh OpenAI tokens. Returns {id_token, access_token, refresh_token, expires_at}."""
    tokens = await refresh_tokens(OPENAI_OAUTH_CONFIG, refresh_token_value)
    id_token = tokens.get("id_token")
    new_refresh = tokens.get("refresh_token", refresh_token_value)
    expires_in = tokens.get("expires_in", 3600)

    result: dict = {
        "refresh_token": new_refresh,
        "expires_at": int(time.time() * 1000) + (expires_in * 1000),
    }

    # Re-derive API key from refreshed id_token
    if id_token:
        key_resp = await token_exchange_rfc8693(
            token_url=OPENAI_OAUTH_CONFIG.token_url,
            client_id=OPENAI_OAUTH_CONFIG.client_id,
            id_token=id_token,
            requested_token="openai-api-key",
        )
        result["api_key"] = key_resp.get("access_token")
        result["id_token"] = id_token

    return result
