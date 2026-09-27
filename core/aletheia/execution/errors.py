"""Shared execution-path exceptions."""

from __future__ import annotations

import json

import httpx

from aletheia.execution.order_constraints import ConstraintUnavailable
from aletheia.uta.models import ReadCompleteness


class PostSubmitDBError(RuntimeError):
    """Order submitted to the exchange OK, but a subsequent local DB write
    (order/fill/position persistence) failed.

    Carries ``exchange_order_id`` so the gateway can record it for
    reconciliation — the exchange-side order exists even though local
    persistence is incomplete. Replaces the prior
    ``RuntimeError(...)`` + ``# type: ignore[attr-defined]`` attribute hack
    in the broker post-submit paths (B4).
    """

    def __init__(self, message: str, exchange_order_id: str | None = None) -> None:
        super().__init__(message)
        self.exchange_order_id = exchange_order_id


class SubmissionUnknownError(RuntimeError):
    """The transport failed while submitting, so acceptance is unknowable.

    This is deliberately distinct from an exchange/API rejection.  Callers must
    preserve it as retry-suppressing unresolved state and reconcile by the stable
    client id instead of submitting the same economic intent again.
    """

    def __init__(
        self,
        message: str = "exchange submission acknowledgement unknown",
        *,
        exchange_order_id: str | None = None,
        client_oid: str | None = None,
    ) -> None:
        super().__init__(message)
        self.exchange_order_id = exchange_order_id
        self.client_oid = client_oid


class IncompleteReadError(RuntimeError):
    """A UTA read could not prove ``ReadCompleteness.COMPLETE``.

    Raised at the reconciliation projection boundary in place of a bare
    ``RuntimeError(reason_code)``. The bare form conflated two different
    semantics behind one opaque type name: a *structural* PARTIAL (the venue
    profile cannot serve the requested scope, e.g.
    ``private_order_history_start_required``) and a *transient* failure
    (network, rate limit, auth flake). Callers logged only
    ``type(exc).__name__`` — literally ``"RuntimeError"`` — so the
    ``reason_code``, which is the entire diagnostic payload, never reached
    ``errors.log``.

    Subclasses ``RuntimeError`` deliberately: every existing
    ``except Exception`` handler around a projection read keeps its current
    fail-closed behaviour unchanged, while handlers that want to dispatch on
    the failure kind can now read ``completeness`` and ``reason_code``
    programmatically. This is a subclass *narrowing*, not a compatibility
    shim: there is exactly one live implementation of "UTA read incompleteness"
    and nothing reads the old bare-``RuntimeError`` spelling.

    Carrying ``completeness`` separately from ``reason_code`` is what lets a
    caller distinguish structural from transient without string-matching:
    ``UNSUPPORTED``/``PARTIAL`` are venue-profile facts that will recur for
    every identical request, whereas ``UNAVAILABLE`` is typically retryable.
    Nothing in this class decides retry policy — it only preserves the
    evidence a policy would need.
    """

    def __init__(
        self,
        reason_code: str | None,
        endpoint: str,
        execution_mode: str,
        market_type: str | None = None,
        symbol: str | None = None,
        *,
        completeness: ReadCompleteness | None = None,
    ) -> None:
        self.reason_code: str = reason_code or f"uta_{endpoint}_incomplete"
        self.endpoint = endpoint
        self.execution_mode = execution_mode
        self.market_type = market_type
        self.symbol = symbol
        self.completeness = completeness
        super().__init__(self.reason_code)

    def __str__(self) -> str:
        return (
            f"{self.endpoint}({self.execution_mode}/{self.market_type or '*'}/"
            f"{self.symbol or '*'}) incomplete: {self.reason_code}"
        )


def is_transient_dependency_error(error: BaseException) -> bool:
    """Classify preparation dependency failures without importing Exchange code."""
    current: BaseException | None = error
    while current is not None:
        if isinstance(current, ConstraintUnavailable) and current.temporary:
            return True
        if isinstance(
            current,
            (
                httpx.RequestError,
                json.JSONDecodeError,
                UnicodeError,
                TimeoutError,
                ConnectionError,
                BrokenPipeError,
            ),
        ):
            return True
        if isinstance(current, httpx.HTTPStatusError):
            response = current.response
            if response is not None and response.status_code >= 500:
                return True

        # Preserve generic transport classification through sanitized wrappers.
        name = type(current).__name__.lower()
        if any(
            marker in name
            for marker in (
                "timeout",
                "connection",
                "transport",
                "network",
                "readerror",
                "writeerror",
                "protocolerror",
                "brokenpipe",
                "eoferror",
            )
        ):
            return True
        current = current.__cause__ or current.__context__
    return False
