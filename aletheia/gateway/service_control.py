"""One truthful control surface over transient, session, and machine services."""

from __future__ import annotations

import platform

from aletheia.gateway import managed_service, session_service, windows_machine_service

SESSION_BACKEND = "session"
WINDOWS_SERVICE_BACKEND = "windows-service"


def status() -> dict:
    session = session_service.status()
    machine = windows_machine_service.status()
    transient = managed_service.status()
    installed = [
        name for name, item in ((SESSION_BACKEND, session), (WINDOWS_SERVICE_BACKEND, machine))
        if item.get("installed")
    ]
    if len(installed) > 1:
        return {
            "ok": False, "reason": "backend_conflict", "backend": None,
            "installed": True, "enabled": None, "runtime": "unknown",
            "owned": False, "config_valid": False,
            "session": session, "machine": machine, "transient": transient,
        }
    if machine.get("installed"):
        selected = machine
        backend = WINDOWS_SERVICE_BACKEND
    elif session.get("installed"):
        selected = session
        backend = SESSION_BACKEND
    else:
        selected = transient
        backend = SESSION_BACKEND
    running = bool(selected.get("running"))
    registration = (
        "persistent" if selected.get("installed") else
        "transient" if transient.get("managed") else "absent"
    )
    return {
        "ok": selected.get("reason") not in {"ownership_conflict", "access_denied", "config_drift"},
        "backend": backend,
        "scope": "machine" if backend == WINDOWS_SERVICE_BACKEND else "session",
        "registration": registration,
        "installed": bool(selected.get("installed")),
        "enabled": selected.get("enabled", False) if selected.get("installed") else False,
        "runtime": "running" if running else "stopped",
        "running": running,
        "owned": selected.get("owned", transient.get("managed") is not None),
        "config_valid": selected.get("config_valid", True),
        "recovery_scope": selected.get("recovery_scope", "none"),
        "reason": selected.get("reason"),
        "detail": selected.get("detail"),
        "platform": selected.get("backend") or selected.get("platform"),
        "data_dir": selected.get("data_dir"),
        "session": session,
        "machine": machine,
        "transient": transient,
    }


def is_machine_selected() -> bool:
    current = status()
    return current.get("backend") == WINDOWS_SERVICE_BACKEND and current.get("installed") is True


def install(*, backend: str = SESSION_BACKEND, elevate: bool = True, source: str = "cli") -> dict:
    current = status()
    if current.get("running"):
        return {**current, "ok": False, "reason": "service_running"}
    if backend not in {SESSION_BACKEND, WINDOWS_SERVICE_BACKEND}:
        return {**current, "ok": False, "reason": "unsupported_backend"}
    other_installed = (
        current["machine"].get("installed") if backend == SESSION_BACKEND
        else current["session"].get("installed")
    )
    if other_installed:
        return {**current, "ok": False, "reason": "backend_conflict"}
    if backend == WINDOWS_SERVICE_BACKEND:
        if platform.system() != "Windows":
            return {**current, "ok": False, "reason": "unsupported_backend"}
        return windows_machine_service.install(elevate=elevate)
    return session_service.install(source=source)


def uninstall(*, backend: str | None = None, elevate: bool = True, source: str = "cli") -> dict:
    current = status()
    backend = backend or current.get("backend")
    if current.get("running"):
        return {**current, "ok": False, "reason": "service_running"}
    if backend == WINDOWS_SERVICE_BACKEND:
        return windows_machine_service.uninstall(elevate=elevate)
    if backend == SESSION_BACKEND:
        return session_service.uninstall(source=source)
    return {**current, "ok": False, "reason": "unsupported_backend"}


def enable(*, elevate: bool = True, source: str = "cli") -> dict:
    current = status()
    if current.get("reason") == "backend_conflict":
        return current
    if current.get("backend") == WINDOWS_SERVICE_BACKEND and current.get("installed"):
        return windows_machine_service.enable(elevate=elevate)
    if not current["session"].get("installed"):
        installed = session_service.install(source=source)
        if not installed.get("ok"):
            return installed
    return session_service.enable(source=source)


def disable(*, elevate: bool = True, source: str = "cli") -> dict:
    current = status()
    if current.get("reason") == "backend_conflict":
        return current
    if current.get("backend") == WINDOWS_SERVICE_BACKEND and current.get("installed"):
        return windows_machine_service.disable(elevate=elevate)
    if not current["session"].get("installed"):
        return {**current, "ok": True, "enabled": False}
    return session_service.disable(source=source)


def start(*, no_browser: bool = True, elevate: bool = True) -> dict:
    current = status()
    if current.get("reason") == "backend_conflict":
        return current
    if current.get("backend") == WINDOWS_SERVICE_BACKEND and current.get("installed"):
        return windows_machine_service.start(elevate=elevate)
    if current["session"].get("installed"):
        return session_service.start()
    return managed_service.start(no_browser=no_browser)


def stop(*, elevate: bool = True) -> dict:
    current = status()
    if current.get("reason") == "backend_conflict":
        return current
    if current.get("backend") == WINDOWS_SERVICE_BACKEND and current.get("installed"):
        return windows_machine_service.stop(elevate=elevate)
    if current["session"].get("installed"):
        return session_service.stop()
    return managed_service.stop()
