"""Helpers for sync test doubles that stand in for coroutine runners.

Production broker helpers accept an already-created coroutine. A sync test double
that returns or raises without consuming that object leaks it, and Python reports the
leak later against an unrelated test. These helpers close the coroutine deterministically
before returning the configured result.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any


def closing_coro_runner(*results: Any) -> Callable[[Coroutine[Any, Any, Any]], Any]:
    """Build a sync runner that closes each coroutine and returns queued results.

    An exception instance in ``results`` is raised after the coroutine is closed. An
    unexpected extra call fails loudly so the test cannot silently accept a changed
    number of exchange operations.
    """

    queued = iter(results)

    def _run(coro: Coroutine[Any, Any, Any]) -> Any:
        close = getattr(coro, "close", None)
        if not callable(close):
            raise TypeError(f"expected a closeable coroutine, got {type(coro).__name__}")
        close()

        try:
            result = next(queued)
        except StopIteration as exc:
            raise AssertionError("coroutine runner called more times than expected") from exc
        if isinstance(result, BaseException):
            raise result
        return result

    return _run
