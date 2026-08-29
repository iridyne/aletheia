"""Regression tests for the Gateway restart control response."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException


@pytest.mark.anyio
async def test_restart_without_supervisor_is_a_failed_control_operation(monkeypatch):
    import aletheia.server.routes.gateway as gateway_routes

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(_owns_pid=True)),
    )
    monkeypatch.delenv("ALETHEIA_DEV_RELOAD", raising=False)
    monkeypatch.setattr(gateway_routes, "is_preview_mode", lambda: False)
    monkeypatch.setattr(
        gateway_routes,
        "write_audit",
        lambda _event: pytest.fail("refused restart must not write an audit event"),
    )

    with pytest.raises(HTTPException) as caught:
        await gateway_routes.restart_gateway(request)

    assert caught.value.status_code == 409
    assert caught.value.detail == {
        "error": "gateway_restart_supervisor_unavailable",
        "reason_code": "supervisor_unavailable",
        "message": "No supervisor active — restart would kill the gateway permanently.",
    }
