"""One truthful control surface over transient, session, and machine services."""

from __future__ import annotations

import platform

from aletheia.gateway import managed_service, session_service, windows_machine_service

SESSION_BACKEND = "session"
WINDOWS_SERVICE_BACKEND = "windows-service"


def _cleanup_legacy_macos(*, source: str = "cli") -> dict | None:
    if platform.system() != "Darwin":
        return None
    from aletheia.gateway import autostart
    try:
        removed = autostart._bootout_legacy_macos(
            labels=autostart._SESSION_SERVICE_LEGACY_LAUNCHD_LABELS,
            source=source,
        )
    except autostart.LegacyLaunchdMutationError as exc:
        return {
            "ok": False,
            "reason": exc.reason,
            "detail": exc.detail,
            "legacy_mutated": exc.mutated,
            "legacy_preserved": True,
        }
    except PermissionError:
        return {
            "ok": False,
            "reason": "access_denied",
            "detail": "legacy_launchd_cleanup",
            "legacy_mutated": False,
            "legacy_preserved": True,
        }
    except autostart.LegacyLaunchdOwnershipError:
        return {
            "ok": False,
            "reason": "ownership_conflict",
            "detail": "legacy_launchd_definition",
            "legacy_mutated": False,
            "legacy_preserved": True,
        }
    except Exception as exc:
        return {
            "ok": False,
            "reason": "legacy_launchd_cleanup_error",
            "detail": type(exc).__name__,
            "legacy_mutated": False,
            "legacy_preserved": True,
        }
    try:
        remaining = autostart._legacy_launchd_present(
            labels=autostart._SESSION_SERVICE_LEGACY_LAUNCHD_LABELS,
        )
    except Exception as exc:
        return {
            "ok": False,
            "reason": "legacy_launchd_cleanup_error",
            "detail": type(exc).__name__,
            "legacy_mutated": bool(removed),
            "legacy_preserved": False,
        }
    if remaining:
        return {
            "ok": False,
            "reason": "legacy_launchd_cleanup_incomplete",
            "legacy_labels": remaining,
            "legacy_mutated": bool(removed),
            "legacy_preserved": True,
        }
    return None


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
    elif session.get("installed") or session.get("enabled"):
        selected = session
        backend = SESSION_BACKEND
    else:
        selected = transient
        backend = SESSION_BACKEND
    running = bool(selected.get("running"))
    persistent = bool(selected.get("installed") or selected.get("enabled"))
    registration = (
        "persistent" if persistent else
        "transient" if transient.get("managed") else "absent"
    )
    return {
        "ok": selected.get("reason") not in {"ownership_conflict", "access_denied", "config_drift"},
        "backend": backend,
        "scope": "machine" if backend == WINDOWS_SERVICE_BACKEND else "session",
        "registration": registration,
        "installed": bool(selected.get("installed")),
        "enabled": selected.get("enabled", False),
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
    session = current.get("session", {})
    if backend == SESSION_BACKEND and not session.get("installed") and session.get("enabled"):
        return {**current, **session, "ok": False, "reason": "ownership_conflict"}
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
    session = current["session"]
    was_enabled = session.get("enabled") is True
    if (
        not session.get("installed")
        or session.get("owned") is not True
        or session.get("config_valid") is not True
    ):
        installed = session_service.install(source=source)
        if not installed.get("ok"):
            return installed
    enabled = session_service.enable(source=source)
    if not enabled.get("ok"):
        return enabled
    cleanup_failure = _cleanup_legacy_macos(source=source)
    if not cleanup_failure:
        return enabled
    if was_enabled or cleanup_failure.get("legacy_preserved") is not True:
        return {
            **enabled,
            **cleanup_failure,
            "replacement_kept": True,
        }
    rollback = session_service.disable(source=source)
    if not rollback.get("ok"):
        return {
            **enabled,
            **cleanup_failure,
            "reason": "legacy_launchd_cleanup_rollback_error",
            "rollback_reason": rollback.get("reason"),
            "replacement_kept": True,
        }
    return {**rollback, **cleanup_failure}


def disable(*, elevate: bool = True, source: str = "cli") -> dict:
    current = status()
    if current.get("reason") == "backend_conflict":
        return current
    if current.get("backend") == WINDOWS_SERVICE_BACKEND and current.get("installed"):
        return windows_machine_service.disable(elevate=elevate)
    session = current["session"]
    if not session.get("installed"):
        if session.get("enabled"):
            return {**current, **session, "ok": False, "reason": "ownership_conflict"}
        disabled = {**current, "ok": True, "enabled": False}
    else:
        disabled = session_service.disable(source=source)
        if not disabled.get("ok"):
            return disabled
    cleanup_failure = _cleanup_legacy_macos(source=source)
    if cleanup_failure:
        return {
            **disabled,
            **cleanup_failure,
            "cleanup_target": "legacy recovery",
        }
    return disabled


def start(*, no_browser: bool = True, elevate: bool = True) -> dict:
    current = status()
    if current.get("reason") == "backend_conflict":
        return current
    if current.get("backend") == WINDOWS_SERVICE_BACKEND and current.get("installed"):
        return windows_machine_service.start(elevate=elevate)
    if current["session"].get("installed"):
        return session_service.start()
    return managed_service.start(no_browser=no_browser)


def stop(*, elevate: bool = True, preserve_recovery: bool = False, operator_stop: bool = False) -> dict:
    current = status()
    if current.get("reason") == "backend_conflict":
        return current
    session = current.get("session", {})
    if (session.get("installed") or session.get("enabled")) and (
        session.get("owned") is not True
        or session.get("config_valid") is not True
    ):
        return {
            **current,
            "ok": False,
            "reason": session.get("reason") or "ownership_conflict",
        }
    migrated_recovery = False
    if preserve_recovery and platform.system() == "Darwin":
        from aletheia.gateway import autostart
        try:
            legacy = autostart._legacy_launchd_present(
                labels=autostart._SESSION_SERVICE_LEGACY_LAUNCHD_LABELS,
            )
        except Exception as exc:
            return {
                **current,
                "ok": False,
                "reason": "legacy_launchd_cleanup_error",
                "detail": type(exc).__name__,
            }
        if legacy:
            migrated = enable(source="restart")
            if not migrated.get("ok"):
                return migrated
            migrated_recovery = True
    transient = current.get("transient", {})
    transient_owner = (
        transient.get("managed") is True and transient.get("running") is True
    )
    if current.get("backend") == WINDOWS_SERVICE_BACKEND and current.get("installed"):
        stopped = windows_machine_service.stop(elevate=elevate)
    elif migrated_recovery and transient_owner:
        # Only prefer the pre-migration transient owner on the macOS legacy migration
        # path: the transient was the proven runtime, and we just bootstrapped a
        # persistent replacement.  Normal stops must select the installed persistent
        # owner first so a stale transient registration cannot override it.
        stopped = managed_service.stop()
    elif current["session"].get("installed"):
        stopped = session_service.stop()
        if stopped.get("ok") and transient_owner:
            transient_stopped = managed_service.stop()
            if not transient_stopped.get("ok"):
                return {
                    **transient_stopped,
                    "persistent_stopped": True,
                    "runtime_stopped": (
                        stopped.get("runtime_stopped") is True
                        or transient_stopped.get("runtime_stopped") is True
                    ),
                    "cleanup_target": "transient service registration",
                }
            stopped = {**stopped, "transient_stopped": True}
    else:
        stopped = managed_service.stop()
        if (
            not stopped.get("ok")
            and stopped.get("runtime_stopped") is True
            and not stopped.get("cleanup_target")
        ):
            stopped = {
                **stopped,
                "cleanup_target": "transient service registration",
            }
    if not stopped.get("ok"):
        return stopped
    if migrated_recovery and transient_owner:
        replacement_stopped = session_service.stop()
        if not replacement_stopped.get("ok"):
            return {
                **replacement_stopped,
                "transient_stopped": True,
                "runtime_stopped": False,
            }
        stopped = replacement_stopped
    if operator_stop:
        # Destructive legacy cleanup only for explicit operator stop.  Internal
        # readiness-failure stops and all restart paths must not remove recovery.
        cleanup_failure = _cleanup_legacy_macos(source="cli")
        if cleanup_failure:
            return {
                **stopped,
                **cleanup_failure,
                "runtime_stopped": True,
                "cleanup_target": "legacy recovery",
            }
    return stopped
