"""Regression tests for the Gateway restart control response."""

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

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


# --- #2845: every dev-reload effect path uses the shared env AND live_disabled gate ---


@contextmanager
def _live_enabled_after_boot(monkeypatch, sentinel_writes: list):
    """Boot had ALETHEIA_DEV_RELOAD=1 + live_disabled; live is now live_limited.

    Scopes the fake config to the call under test, so no
    module first imported later (e.g. by fixture teardown) binds the fake.
    """
    import aletheia.config.loader as loader
    import aletheia.server.dev_reload as dev_reload

    monkeypatch.setenv("ALETHEIA_DEV_RELOAD", "1")
    monkeypatch.setattr(dev_reload, "touch_reload_sentinel", lambda reason="": sentinel_writes.append(reason))
    with monkeypatch.context() as scoped:
        scoped.setattr(loader, "load_config", lambda: SimpleNamespace(live=SimpleNamespace(state="live_limited")))
        yield


@pytest.mark.anyio
async def test_restart_route_refuses_dev_reload_once_live_is_enabled(monkeypatch):
    import aletheia.server.routes.gateway as gateway_routes

    sentinel: list = []
    monkeypatch.setattr(gateway_routes, "is_preview_mode", lambda: False)
    monkeypatch.setattr(gateway_routes, "write_audit", lambda _e: None)
    monkeypatch.setattr(
        gateway_routes, "asyncio",
        SimpleNamespace(create_task=lambda coro: (coro.close(), pytest.fail("must not schedule os._exit"))),
    )
    request: Any = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(_owns_pid=False)))

    with _live_enabled_after_boot(monkeypatch, sentinel), pytest.raises(HTTPException) as caught:
        await gateway_routes.restart_gateway(request)

    assert caught.value.status_code == 409
    detail: Any = caught.value.detail
    assert detail["reason_code"] == "dev_reload_blocked"
    assert sentinel == []


def test_self_heal_restart_never_reloads_once_live_is_enabled(monkeypatch):
    from aletheia.agent_runtime import health

    sentinel: list = []
    audits: list = []
    monkeypatch.setattr(health, "write_audit", audits.append)
    monkeypatch.setattr(health, "_send_alert", lambda _msg: None)
    monkeypatch.setattr(health, "_os_exit", lambda code: pytest.fail(f"os._exit({code})"))

    with _live_enabled_after_boot(monkeypatch, sentinel):
        health._trigger_restart("code_drift")

    assert sentinel == []
    assert [a["event_type"] for a in audits] == ["self_heal_restart_blocked"]


@pytest.mark.parametrize("config_outcome", ["live_limited", "raises"])
def test_dev_watcher_guard_fails_closed(monkeypatch, config_outcome):
    import aletheia.config.loader as loader
    from scripts.dev import watch_backend

    monkeypatch.setenv("ALETHEIA_DEV_RELOAD", "1")

    def boom():
        raise OSError("config unreadable")

    fake = boom if config_outcome == "raises" else (lambda: SimpleNamespace(live=SimpleNamespace(state="live_limited")))
    with monkeypatch.context() as scoped:
        scoped.setattr(loader, "load_config", fake)
        assert watch_backend.check_live_guard() is False
