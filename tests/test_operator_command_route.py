from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from aletheia.server.app import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_operator_command_uses_existing_agent_shell_runtime():
    fake_response = {
        "intent": "get_system_status",
        "risk_level": "read",
        "response": "System healthy",
        "action": None,
        "needs_confirmation": False,
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch(
            "aletheia.agent_shell.runtime.AgentShellRuntime.handle",
            new=AsyncMock(return_value=fake_response),
        ) as mocked:
            resp = await client.post("/api/operator/command", json={"input": "status"})

    assert resp.status_code == 200
    assert resp.json()["response"] == "System healthy"
    mocked.assert_awaited_once_with("status")
