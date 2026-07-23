"""Safety test — E2E LLM guard blocks provider resolution (#240).

The autouse _mock_llm fixture patches get_provider() to raise, ensuring no
E2E test accidentally reaches a configured model. This file verifies the
guard is active and that llm_complete returns a clean unavailable result.
"""

from __future__ import annotations

import pytest


async def test_guard_blocks_real_llm():
    """#240: llm_complete must return (None, error) under the E2E guard.

    The _mock_llm autouse fixture patches get_provider() to raise. When
    llm_complete() catches that exception it must return (None, error) and
    the error must mention the blocked provider resolution.
    """
    from aletheia.agents.helpers import llm_complete

    content, error = await llm_complete("system", "user")

    assert content is None, "guarded llm_complete must return None content"
    assert error is not None, "guarded llm_complete must return an error string"
    assert "llm_unavailable" in error, (
        f"error must signal unavailability; got: {error!r}"
    )
    assert "E2E guard" in error or "blocked" in error.lower(), (
        f"error must reference the guard or blocked state; got: {error!r}"
    )


async def test_guard_does_not_block_direct_llm_mock(monkeypatch):
    """#240: tests that inject their own llm_complete mock are not affected by the guard."""
    from aletheia import agents

    calls = []

    async def _fake_llm_complete(system, user, **kwargs):
        calls.append((system, user))
        return "fake response", None

    monkeypatch.setattr(agents.helpers, "llm_complete", _fake_llm_complete)

    result = await agents.helpers.llm_complete("sys", "msg")
    assert result == ("fake response", None)
    assert len(calls) == 1
