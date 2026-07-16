"""Async-safe AgentRun identity for cross-cutting runtime measurement."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from collections.abc import Iterator

_CURRENT_AGENT_RUN_ID: ContextVar[str | None] = ContextVar(
    "aletheia_current_agent_run_id", default=None
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
