def _state(**kwargs):
    base = {"installed": False, "enabled": False, "running": False, "owned": True, "config_valid": True}
    base.update(kwargs)
    return base


def test_status_keeps_installed_enabled_and_running_independent(monkeypatch):
    from aletheia.gateway import service_control
    monkeypatch.setattr(service_control.session_service, "status", lambda: _state(installed=True, enabled=True, running=False, backend="task_scheduler", recovery_scope="login"))
    monkeypatch.setattr(service_control.windows_machine_service, "status", lambda: _state(backend="windows_service"))
    monkeypatch.setattr(service_control.managed_service, "status", lambda: {"managed": False, "platform": "task_scheduler"})
    result = service_control.status()
    assert result["registration"] == "persistent"
    assert result["installed"] is True
    assert result["enabled"] is True
    assert result["runtime"] == "stopped"


def test_dual_persistent_backends_are_blocked(monkeypatch):
    from aletheia.gateway import service_control
    monkeypatch.setattr(service_control.session_service, "status", lambda: _state(installed=True))
    monkeypatch.setattr(service_control.windows_machine_service, "status", lambda: _state(installed=True))
    monkeypatch.setattr(service_control.managed_service, "status", lambda: {"managed": False})
    assert service_control.status()["reason"] == "backend_conflict"


def test_enable_installs_session_without_starting(monkeypatch):
    from aletheia.gateway import service_control
    monkeypatch.setattr(service_control, "status", lambda: {"reason": None, "backend": "session", "session": _state(), "machine": _state()})
    calls = []
    monkeypatch.setattr(service_control.session_service, "install", lambda **kwargs: calls.append("install") or {"ok": True})
    monkeypatch.setattr(service_control.session_service, "enable", lambda **kwargs: calls.append("enable") or {"ok": True, "enabled": True})
    result = service_control.enable()
    assert result["ok"] is True
    assert calls == ["install", "enable"]


def test_uninstall_refuses_running_service(monkeypatch):
    from aletheia.gateway import service_control
    monkeypatch.setattr(service_control, "status", lambda: {"backend": "session", "running": True})
    assert service_control.uninstall()["reason"] == "service_running"


def test_start_uses_transient_manager_when_nothing_is_installed(monkeypatch):
    from aletheia.gateway import service_control
    monkeypatch.setattr(service_control, "status", lambda: {"reason": None, "backend": "session", "session": _state(), "machine": _state()})
    calls = []
    monkeypatch.setattr(service_control.managed_service, "start", lambda **kwargs: calls.append(kwargs) or {"ok": True})
    assert service_control.start(no_browser=True)["ok"] is True
    assert calls == [{"no_browser": True}]
