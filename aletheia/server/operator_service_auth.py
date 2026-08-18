"""Least-privilege authentication for the local supervised Operator Shell."""

from __future__ import annotations

from aletheia.operator_service_identity import (
    OPERATOR_SERVICE_HEADER,
    is_valid_operator_service_token,
)

# Read-only state actually consumed by operator-shell/src/backend_client.ts.
_OPERATOR_SERVICE_READ_PATHS = frozenset(
    {
        "/api/status",
        "/api/health",
        "/api/operator/status",
        "/api/operator/events",
        "/api/operator/sessions",
        "/api/operator/commands",
        "/api/operator/tasks",
        "/api/operator/extensions",
        "/api/dreaming/summary",
        "/api/reviews/summary",
        "/api/strategies/summary",
        "/api/risk/summary",
        "/api/portfolio",
        "/api/market/portfolio-history",
        "/api/agent-runs",
    }
)

# One bounded specialist-delegation mutation used by OperatorLoop.
_OPERATOR_SERVICE_MUTATION_PATHS = frozenset(
    {
        ("POST", "/api/agent-runs/start"),
    }
)

# Internal bridge methods currently implemented by server/routes/operator.py.
_OPERATOR_INTERNAL_METHODS = frozenset({"GET", "POST", "PATCH"})
_OPERATOR_INTERNAL_PREFIX = "/api/operator/internal/"
_OPERATOR_SESSION_DETAIL_PREFIX = "/api/operator/sessions/"


def operator_service_route_allowed(method: str, path: str) -> bool:
    normalized_method = method.upper()
    if path.startswith(_OPERATOR_INTERNAL_PREFIX):
        return normalized_method in _OPERATOR_INTERNAL_METHODS
    if normalized_method == "GET":
        return path in _OPERATOR_SERVICE_READ_PATHS or path.startswith(
            _OPERATOR_SESSION_DETAIL_PREFIX
        )
    return (normalized_method, path) in _OPERATOR_SERVICE_MUTATION_PATHS


def operator_service_request_authenticated(request) -> bool:
    """Authenticate the service credential only on the declared Operator surface."""
    path = request.url.path
    if not operator_service_route_allowed(request.method, path):
        return False

    presented = request.headers.get(OPERATOR_SERVICE_HEADER, "")
    return bool(presented) and is_valid_operator_service_token(presented)
