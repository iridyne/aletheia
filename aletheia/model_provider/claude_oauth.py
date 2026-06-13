"""Claude subscription OAuth — Claude Max/Pro → access_token via PKCE.

Uses the same client_id as Claude Code CLI. Flow:
  1. PKCE authorize → user logs in with Claude account in browser
  2. Redirect to console.anthropic.com/oauth/code/callback → page shows code
  3. User copies code back to Aletheia UI
  4. Code → token exchange → access_token + refresh_token
  5. access_token used directly as Bearer for api.anthropic.com

No local callback server needed — the redirect goes to Anthropic's hosted
page which displays the code for manual copy.
"""

import asyncio
import logging
import time

from aletheia.files.jsonl import write_audit
from aletheia.model_provider.oauth import (
    OAuthConfig,
    generate_pkce, generate_state, build_auth_url,
    exchange_code, refresh_tokens,
)

logger = logging.getLogger("aletheia.model_provider.claude_oauth")

# ─── OAuth configuration (Claude Code CLI public client) ─────────────────────

CLAUDE_OAUTH_CONFIG = OAuthConfig(
    client_id="9d1c250a-e61b-44d9-88ed-5944d1962f5e",
    auth_url="https://claude.ai/oauth/authorize",
    token_url="https://platform.claude.com/v1/oauth/token",
    redirect_uri="https://console.anthropic.com/oauth/code/callback",
    scopes="org:create_api_key user:profile user:inference",
    extra_params={"code": "true"},
    token_request_format="json",
)

_STATE_TTL_SEC = 600  # 10 minutes


# ─── Session state machine ───────────────────────────────────────────────────


class ClaudeLoginSession:
    def __init__(self):
        self.state = "idle"  # idle | awaiting_code | completed | failed
        self.auth_url: str | None = None
        self.error: str | None = None
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.expires_at: int | None = None
        self._code_verifier: str | None = None
        self._expected_state: str | None = None
        self._created_at: float = 0

    def public(self) -> dict:
        return {
            "state": self.state,
            "auth_url": self.auth_url,
            "error": self.error,
        }


_session = ClaudeLoginSession()
_lock = asyncio.Lock()


async def start_login() -> dict:
    """Begin Claude OAuth flow. Returns {state, auth_url} for frontend."""
    global _session
    async with _lock:
        if _session.state == "awaiting_code":
            # Check if expired
            if time.time() - _session._created_at > _STATE_TTL_SEC:
                _session.state = "failed"
                _session.error = "expired"
            else:
                return _session.public()

        _session = ClaudeLoginSession()
        code_verifier, code_challenge = generate_pkce()
        state = generate_state()

        _session._code_verifier = code_verifier
        _session._expected_state = state
        _session._created_at = time.time()
        _session.auth_url = build_auth_url(CLAUDE_OAUTH_CONFIG, code_challenge, state)
        _session.state = "awaiting_code"

        write_audit({
            "actor": "human", "source": "dashboard",
            "event_type": "claude_oauth_started", "entity_type": "model_provider",
            "entity_id": "anthropic_native", "message": "Claude OAuth login started",
        })
        return _session.public()


async def complete_login(code: str) -> dict:
    """Complete Claude OAuth by exchanging the user-provided code for tokens."""
    async with _lock:
        if _session.state != "awaiting_code":
            return {"error": "no_pending_flow", **_session.public()}

        if time.time() - _session._created_at > _STATE_TTL_SEC:
            _session.state = "failed"
            _session.error = "expired"
            return _session.public()

        # Clean code (may have URL fragments appended)
        clean_code = code.split("#")[0].split("&")[0].strip()

        try:
            tokens = await exchange_code(
                CLAUDE_OAUTH_CONFIG,
                clean_code,
                _session._code_verifier,
                state=_session._expected_state,
            )
        except Exception as e:
            _session.state = "failed"
            _session.error = f"exchange_failed: {str(e)[:150]}"
            logger.warning("Claude OAuth code exchange failed: %s", e)
            return _session.public()

        access_token = tokens.get("access_token")
        if not access_token:
            _session.state = "failed"
            _session.error = "no_access_token"
            return _session.public()

        refresh_token = tokens.get("refresh_token")
        expires_in = tokens.get("expires_in", 3600)

        _session.access_token = access_token
        _session.refresh_token = refresh_token
        _session.expires_at = int(time.time() * 1000) + (expires_in * 1000)
        _session.state = "completed"
        _session.error = None

        write_audit({
            "actor": "human", "source": "dashboard",
            "event_type": "claude_oauth_completed", "entity_type": "model_provider",
            "entity_id": "anthropic_native", "message": "Claude OAuth login completed",
        })
        return _session.public()


def get_status() -> dict:
    """Return current login session state (never tokens)."""
    return _session.public()


def cancel() -> dict:
    """Cancel an in-progress login."""
    global _session
    _session = ClaudeLoginSession()
    return _session.public()


def get_tokens() -> dict | None:
    """Return tokens after successful login. Called by route to persist."""
    if _session.state != "completed":
        return None
    return {
        "access_token": _session.access_token,
        "refresh_token": _session.refresh_token,
        "expires_at": _session.expires_at,
    }


# ─── Token refresh ───────────────────────────────────────────────────────────


async def refresh_claude_tokens(refresh_token_value: str) -> dict:
    """Refresh Claude tokens. Returns {access_token, refresh_token, expires_at}."""
    tokens = await refresh_tokens(CLAUDE_OAUTH_CONFIG, refresh_token_value)
    access_token = tokens.get("access_token")
    new_refresh = tokens.get("refresh_token", refresh_token_value)
    expires_in = tokens.get("expires_in", 3600)

    return {
        "access_token": access_token,
        "refresh_token": new_refresh,
        "expires_at": int(time.time() * 1000) + (expires_in * 1000),
    }
