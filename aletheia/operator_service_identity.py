"""Install-scoped identity for the supervised Operator Shell.

This credential is deliberately separate from remote owner authentication.  It is
stored through Aletheia's existing encrypted secret store and is only accepted by
the backend on a small Operator-service route allowlist.
"""

from __future__ import annotations

import secrets

from aletheia.config.secrets import get_secret, set_secret

OPERATOR_SERVICE_SECRET_KEY = "operator_service_token"
OPERATOR_SERVICE_ENV = "ALETHEIA_OPERATOR_SERVICE_TOKEN"
OPERATOR_SERVICE_HEADER = "X-Aletheia-Operator-Service"


def current_operator_service_token() -> str | None:
    token = get_secret(OPERATOR_SERVICE_SECRET_KEY)
    if token is None:
        return None
    token = token.strip()
    return token or None


def ensure_operator_service_token() -> str:
    """Return the durable service token, creating it when absent."""
    current = current_operator_service_token()
    if current:
        return current
    return rotate_operator_service_token()


def rotate_operator_service_token() -> str:
    """Replace the service token without exposing it through config or logs."""
    token = secrets.token_urlsafe(32)
    set_secret(OPERATOR_SERVICE_SECRET_KEY, token)
    return token
