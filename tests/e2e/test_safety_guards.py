"""Executable contracts for E2E external-I/O guards."""

import pytest


@pytest.mark.asyncio
async def test_e2e_llm_guard_blocks_provider_resolution():
    from aletheia.agents.helpers import llm_complete

    content, error = await llm_complete(
        "E2E safety guard",
        "provider resolution must be blocked",
        max_tokens=1,
    )

    assert content is None
