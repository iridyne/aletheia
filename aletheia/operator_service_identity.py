"""Install-scoped identity for the supervised Operator Shell.

This credential is deliberately separate from remote owner authentication.  It is
stored through Aletheia's existing encrypted secret store and is only accepted by
the backend on a small Operator-service route allowlist.

Token model
-----------
The live shell gets a durable token that is rotated on every respawn and persisted
to the secret store for cross-restart recovery.  Shadow / one-shot shells get an
ephemeral per-process token that is issued in-memory and revoked when the process
exits.  Both kinds live in an in-memory active set so the auth layer only needs to
call is_valid_operator_service_token() — no single-value assumption.
"""

from __future__ import annotations

import hmac
import secrets
import threading

from aletheia.config.secrets import _file_get, set_secret

OPERATOR_SERVICE_SECRET_KEY = "operator_service_token"
OPERATOR_SERVICE_ENV = "ALETHEIA_OPERATOR_SERVICE_TOKEN"
OPERATOR_SERVICE_HEADER = "X-Aletheia-Operator-Service"

_lock = threading.Lock()
_active_session_tokens: set[str] = set()
_live_shell_token: str | None = None


def _load_persisted() -> str | None:
    # Use _file_get, not get_secret: the encrypted file is the authoritative store for
    # this machine-generated credential. get_secret() would prefer a potentially stale
    # OS keyring copy when a prior set_password() write failed silently (issue #1104).
    token = _file_get(OPERATOR_SERVICE_SECRET_KEY)
    if token is None:
        return None
    token = token.strip()
    return token or None


def _ensure_live_token_loaded() -> None:
    # Must be called under _lock.
    global _live_shell_token
    if _live_shell_token is None:
        persisted = _load_persisted()
        if persisted:
            _live_shell_token = persisted
            _active_session_tokens.add(persisted)


def current_operator_service_token() -> str | None:
    """Return the persisted live-shell token (for supervisor heartbeat use)."""
    return _load_persisted()


def is_valid_operator_service_token(token: str) -> bool:
    """Return True if *token* is currently authorized for operator-service use."""
    with _lock:
        _ensure_live_token_loaded()
        return any(hmac.compare_digest(token, t) for t in _active_session_tokens)


def ensure_operator_service_token() -> str:
    """Return the durable service token, creating it when absent."""
    with _lock:
        _ensure_live_token_loaded()
        if _live_shell_token:
            return _live_shell_token
    return rotate_operator_service_token()


def rotate_operator_service_token() -> str:
    """Replace the live-shell token without affecting active shadow tokens."""
    global _live_shell_token
    new_token = secrets.token_urlsafe(32)
    with _lock:
        _ensure_live_token_loaded()
        if _live_shell_token is not None:
            _active_session_tokens.discard(_live_shell_token)
        set_secret(OPERATOR_SERVICE_SECRET_KEY, new_token)
        _live_shell_token = new_token
        _active_session_tokens.add(new_token)
    return new_token


def issue_session_token() -> str:
    """Issue an ephemeral per-process credential for a shadow / one-shot shell."""
    token = secrets.token_urlsafe(32)
    with _lock:
        _active_session_tokens.add(token)
    return token


def revoke_session_token(token: str) -> None:
    """Revoke a per-process credential when its process exits."""
    with _lock:
        _active_session_tokens.discard(token)
