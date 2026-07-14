"""Shared classification for exchange submission acknowledgement failures."""

from __future__ import annotations

import json

import ccxt
import httpx


class SubmissionResponseUnknownError(RuntimeError):
    """The venue may have accepted a request but its response is unusable."""


def is_ambiguous_submission_error(error: BaseException) -> bool:
    """Return True when exchange acceptance cannot be proven either way."""
    current: BaseException | None = error
    while current is not None:
        if isinstance(
            current,
            (
                SubmissionResponseUnknownError,
                ccxt.NetworkError,
                # CCXT uses BadResponse (including NullResponse) when a request
                # completed but its acknowledgement cannot be trusted. The venue
                # may already have accepted create_order, so a retry is unsafe.
                ccxt.BadResponse,
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

        # Preserve compatibility with adapter/transport implementations whose
        # concrete exception classes are not imported here. Traverse wrapper
        # causes as well, because CCXT clients deliberately sanitize exceptions
        # behind RuntimeError at the public adapter boundary.
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
