"""Shared classification for exchange submission acknowledgement failures."""

from __future__ import annotations

import ccxt
from aletheia.execution.errors import is_transient_dependency_error


class SubmissionResponseUnknownError(RuntimeError):
    """The venue may have accepted a request but its response is unusable."""


class ClosePositionAbsentError(RuntimeError):
    """The venue rejected a close because it reported no matching exposure.

    This proves only that the new close was rejected. It does not prove how the
    position disappeared and must never be treated as fill evidence.
    """


def is_ambiguous_submission_error(error: BaseException) -> bool:
    """Normalize concrete acknowledgement failures at the Exchange boundary."""
    if is_transient_dependency_error(error):
        return True
    current: BaseException | None = error
    while current is not None:
        if isinstance(current, (SubmissionResponseUnknownError, ccxt.NetworkError, ccxt.BadResponse)):
            return True
        current = current.__cause__ or current.__context__
    return False
