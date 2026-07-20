"""Async-safe identities for cross-cutting runtime measurement."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any

_CURRENT_AGENT_RUN_ID: ContextVar[str | None] = ContextVar(
    "aletheia_current_agent_run_id", default=None
)
_CURRENT_ECONOMIC_ATTRIBUTION: ContextVar[dict[str, Any] | None] = ContextVar(
    "aletheia_current_economic_attribution", default=None
)


def current_agent_run_id() -> str | None:
    return _CURRENT_AGENT_RUN_ID.get()


def set_current_agent_run_id(run_id: str | None) -> Token:
    return _CURRENT_AGENT_RUN_ID.set(run_id)


def reset_current_agent_run_id(token: Token) -> None:
    _CURRENT_AGENT_RUN_ID.reset(token)


@contextmanager
def bind_agent_run(run_id: str) -> Iterator[None]:
    token = set_current_agent_run_id(run_id)
    try:
        yield
    finally:
        reset_current_agent_run_id(token)


def current_economic_attribution() -> dict[str, Any]:
    """Return a defensive copy of the current explicit accounting identity."""
    return dict(_CURRENT_ECONOMIC_ATTRIBUTION.get() or {})


def set_current_economic_attribution(values: dict[str, Any] | None) -> Token:
    """Bind one logical evaluation identity without inventing missing entities."""
    cleaned = {
        str(key): value
        for key, value in (values or {}).items()
        if value not in (None, "")
    }
    return _CURRENT_ECONOMIC_ATTRIBUTION.set(cleaned or None)


def reset_current_economic_attribution(token: Token) -> None:
    _CURRENT_ECONOMIC_ATTRIBUTION.reset(token)


@contextmanager
def bind_economic_attribution(**values: Any) -> Iterator[None]:
    token = set_current_economic_attribution(values)
    try:
        yield
    finally:
        reset_current_economic_attribution(token)
