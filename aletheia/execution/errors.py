"""Shared execution-path exceptions."""

from __future__ import annotations


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


class LeverageSetupError(RuntimeError):
    """Leverage configuration failed BEFORE any order was submitted.

    The exchange never received a create-order call.  Callers must NOT mark
    the local Order as submission_unknown — there is no ambiguous exposure.
    (#1792)
    """
