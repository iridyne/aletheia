"""Task-local economic attribution context.

The context never changes model or trading behaviour. It only associates physical
runtime cost facts with the AgentRun that caused them when such an identity exists.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

_CURRENT_AGENT_RUN_ID: ContextVar[str | None] = ContextVar(
    "aletheia_economic_agent_run_id", default=None
)


def current_agent_run_id() -> str | None:
    return _CURRENT_AGENT_RUN_ID.get()


@contextmanager
def bind_agent_run(run_id: str | None) -> Iterator[None]:
    token = _CURRENT_AGENT_RUN_ID.set(run_id)
    try:
        yield
    finally:
        _CURRENT_AGENT_RUN_ID.reset(token)
