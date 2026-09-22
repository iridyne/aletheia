"""Shared execution-path exceptions."""

from __future__ import annotations

import json

import httpx

from aletheia.execution.order_constraints import ConstraintUnavailable


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
