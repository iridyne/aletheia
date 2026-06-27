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
