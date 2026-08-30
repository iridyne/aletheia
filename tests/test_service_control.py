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


def test_status_exposes_foreign_enabled_session_definition(monkeypatch):
    from aletheia.gateway import service_control

    monkeypatch.setattr(
        service_control.session_service,
        "status",
        lambda: _state(
            enabled=True,
            owned=False,
            config_valid=False,
            reason="ownership_conflict",
            backend="launchd",
        ),
    )
    monkeypatch.setattr(
        service_control.windows_machine_service,
        "status",
        lambda: _state(backend="windows_service"),
    )
    monkeypatch.setattr(
        service_control.managed_service,
        "status",
        lambda: {"managed": False, "platform": "launchd"},
    )

    result = service_control.status()

    assert result["ok"] is False
    assert result["reason"] == "ownership_conflict"
    assert result["registration"] == "persistent"
    assert result["installed"] is False
    assert result["enabled"] is True


def test_dual_persistent_backends_are_blocked(monkeypatch):
    from aletheia.gateway import service_control
    monkeypatch.setattr(service_control.session_service, "status", lambda: _state(installed=True))
    monkeypatch.setattr(service_control.windows_machine_service, "status", lambda: _state(installed=True))
    monkeypatch.setattr(service_control.managed_service, "status", lambda: {"managed": False})
    assert service_control.status()["reason"] == "backend_conflict"


def test_enable_installs_session_without_starting(monkeypatch):
    from aletheia.gateway import service_control
    monkeypatch.setattr(service_control.platform, "system", lambda: "Linux")
    monkeypatch.setattr(service_control, "status", lambda: {"reason": None, "backend": "session", "session": _state(), "machine": _state()})
    calls = []
    monkeypatch.setattr(service_control.session_service, "install", lambda **kwargs: calls.append("install") or {"ok": True})
    monkeypatch.setattr(service_control.session_service, "enable", lambda **kwargs: calls.append("enable") or {"ok": True, "enabled": True})
    result = service_control.enable()
    assert result["ok"] is True
    assert calls == ["install", "enable"]


def test_macos_enable_installs_replacement_before_removing_legacy_controller(monkeypatch):
    from aletheia.gateway import autostart, service_control

    events = []
    monkeypatch.setattr(service_control.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        service_control,
        "status",
        lambda: {
            "reason": None,
            "backend": "session",
            "session": _state(),
            "machine": _state(),
        },
    )
    monkeypatch.setattr(
        autostart,
        "_bootout_legacy_macos",
        lambda **kwargs: events.append("cleanup") or ["com.aletheia.gateway.watchdog"],
    )
    monkeypatch.setattr(autostart, "_legacy_launchd_present", lambda **kwargs: [])
    monkeypatch.setattr(
        service_control.session_service,
        "install",
        lambda **kwargs: events.append("install") or {"ok": True},
    )
    monkeypatch.setattr(
        service_control.session_service,
        "enable",
        lambda **kwargs: events.append("enable") or {"ok": True, "enabled": True},
    )

    assert service_control.enable()["ok"] is True
    assert events == ["install", "enable", "cleanup"]


def test_macos_enable_fails_closed_when_legacy_cleanup_is_denied(monkeypatch):
    from aletheia.gateway import autostart, service_control

    monkeypatch.setattr(service_control.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        service_control,
        "status",
        lambda: {
            "reason": None,
            "backend": "session",
            "session": _state(),
            "machine": _state(),
        },
    )

    events = []

    def deny_cleanup(**kwargs):
        events.append("cleanup")
        raise PermissionError("denied")

    monkeypatch.setattr(autostart, "_bootout_legacy_macos", deny_cleanup)
    monkeypatch.setattr(
        service_control.session_service,
        "install",
        lambda **kwargs: events.append("install") or {"ok": True},
    )
    monkeypatch.setattr(
        service_control.session_service,
        "enable",
        lambda **kwargs: events.append("enable") or {"ok": True, "enabled": True},
    )
    monkeypatch.setattr(
        service_control.session_service,
        "disable",
        lambda **kwargs: events.append("disable")
        or {"ok": True, "installed": True, "enabled": False},
    )

    result = service_control.enable()

    assert result["ok"] is False
    assert result["reason"] == "access_denied"
    assert events == ["install", "enable", "cleanup", "disable"]
    assert result["installed"] is True
    assert result["enabled"] is False


def test_macos_enable_rolls_back_when_partial_cleanup_preserves_old_owner(
    monkeypatch,
):
    from aletheia.gateway import autostart, service_control

    events = []
    monkeypatch.setattr(service_control.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        service_control,
        "status",
        lambda: {
            "reason": None,
            "backend": "session",
            "session": _state(),
            "machine": _state(),
        },
    )
    monkeypatch.setattr(
        service_control.session_service,
        "install",
        lambda **kwargs: events.append("install") or {"ok": True},
    )
    monkeypatch.setattr(
        service_control.session_service,
        "enable",
        lambda **kwargs: events.append("enable") or {"ok": True, "enabled": True},
    )
    monkeypatch.setattr(
        service_control.session_service,
        "disable",
        lambda **kwargs: events.append("disable") or {"ok": True, "enabled": False},
    )

    def partial_cleanup(**kwargs):
        events.append("cleanup")
        raise autostart.LegacyLaunchdMutationError(
            "access_denied",
            "second legacy owner remains",
            mutated=True,
        )

    monkeypatch.setattr(autostart, "_bootout_legacy_macos", partial_cleanup)

    result = service_control.enable()

    assert result["ok"] is False
    assert result["reason"] == "access_denied"
    assert result["legacy_mutated"] is True
    assert result["legacy_preserved"] is True
    assert events == ["install", "enable", "cleanup", "disable"]



def test_macos_enable_keeps_legacy_owner_when_replacement_enable_fails(monkeypatch):
    from aletheia.gateway import autostart, service_control

    events = []
    monkeypatch.setattr(service_control.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        service_control,
        "status",
        lambda: {
            "reason": None,
            "backend": "session",
            "session": _state(),
            "machine": _state(),
        },
    )
    monkeypatch.setattr(
        service_control.session_service,
        "install",
        lambda **kwargs: events.append("install") or {"ok": True},
    )
    monkeypatch.setattr(
        service_control.session_service,
        "enable",
        lambda **kwargs: events.append("enable")
        or {"ok": False, "reason": "plist_write_error"},
    )
    monkeypatch.setattr(
        autostart,
        "_bootout_legacy_macos",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not remove legacy")),
    )

    result = service_control.enable()

    assert result["ok"] is False
    assert result["reason"] == "plist_write_error"
    assert events == ["install", "enable"]


def test_macos_enable_keeps_replacement_after_legacy_mutation(monkeypatch):
    from aletheia.gateway import autostart, service_control

    events = []
    monkeypatch.setattr(service_control.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        service_control,
        "status",
        lambda: {
            "reason": None,
            "backend": "session",
            "session": _state(),
            "machine": _state(),
        },
    )
    monkeypatch.setattr(
        service_control.session_service,
        "install",
        lambda **kwargs: events.append("install") or {"ok": True},
    )
    monkeypatch.setattr(
        service_control.session_service,
        "enable",
        lambda **kwargs: events.append("enable") or {"ok": True, "enabled": True},
    )
    monkeypatch.setattr(
        service_control.session_service,
        "disable",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("must keep replacement")),
    )
    monkeypatch.setattr(
        autostart,
        "_bootout_legacy_macos",
        lambda **kwargs: events.append("cleanup") or ["legacy"],
    )
    monkeypatch.setattr(
        autostart,
        "_legacy_launchd_present",
        lambda **kwargs: (_ for _ in ()).throw(TimeoutError("probe failed")),
    )

    result = service_control.enable()

    assert result["ok"] is False
    assert result["legacy_mutated"] is True
    assert result["replacement_kept"] is True
    assert result["enabled"] is True
    assert events == ["install", "enable", "cleanup"]


def test_macos_enable_never_disables_preexisting_replacement(monkeypatch):
    from aletheia.gateway import autostart, service_control

    monkeypatch.setattr(service_control.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        service_control,
        "status",
        lambda: {
            "reason": None,
            "backend": "session",
            "session": _state(installed=True, enabled=True),
            "machine": _state(),
        },
    )
    monkeypatch.setattr(
        service_control.session_service,
        "enable",
        lambda **kwargs: {"ok": True, "enabled": True},
    )
    monkeypatch.setattr(
        service_control.session_service,
        "disable",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("must keep preexisting recovery")
        ),
    )
    monkeypatch.setattr(
        autostart,
        "_bootout_legacy_macos",
        lambda **kwargs: (_ for _ in ()).throw(PermissionError("denied")),
    )

    result = service_control.enable()

    assert result["ok"] is False
    assert result["legacy_mutated"] is False
    assert result["replacement_kept"] is True
    assert result["enabled"] is True

def test_macos_enable_keeps_legacy_owner_when_replacement_is_untrusted(monkeypatch):
    from aletheia.gateway import autostart, service_control

    monkeypatch.setattr(service_control.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        service_control,
        "status",
        lambda: {
            "reason": "ownership_conflict",
            "backend": "session",
            "session": _state(
                installed=True,
                owned=False,
                config_valid=False,
            ),
            "machine": _state(),
        },
    )
    monkeypatch.setattr(
        service_control.session_service,
        "install",
        lambda **kwargs: {"ok": False, "reason": "ownership_conflict"},
    )
    monkeypatch.setattr(
        autostart,
        "_bootout_legacy_macos",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not remove legacy")),
    )

    result = service_control.enable()

    assert result["ok"] is False
    assert result["reason"] == "ownership_conflict"


def test_macos_stop_preserves_legacy_when_runtime_stop_fails(monkeypatch):
    from aletheia.gateway import autostart, service_control

    monkeypatch.setattr(service_control.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        service_control,
        "status",
        lambda: {
            "reason": None,
            "backend": "session",
            "session": _state(),
            "machine": _state(),
        },
    )
    monkeypatch.setattr(
        service_control.managed_service,
        "stop",
        lambda: {"ok": False, "reason": "access_denied"},
    )
    monkeypatch.setattr(
        autostart,
        "_bootout_legacy_macos",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("must preserve legacy")),
    )

    result = service_control.stop()

    assert result["ok"] is False
    assert result["reason"] == "access_denied"


def test_macos_restart_migrates_recovery_but_stops_original_transient_owner(monkeypatch):
    from aletheia.gateway import autostart, service_control

    before = {
        "reason": None,
        "backend": "session",
        "session": _state(installed=True, enabled=False, running=False),
        "machine": _state(),
        "transient": {"managed": True, "running": True},
    }
    events = []
    monkeypatch.setattr(service_control.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(service_control, "status", lambda: before)
    monkeypatch.setattr(
        autostart,
        "_legacy_launchd_present",
        lambda **kwargs: ["com.aletheia.trading-os.gateway"],
    )
    monkeypatch.setattr(
        service_control,
        "enable",
        lambda **kwargs: events.append(("migrate", kwargs)) or {"ok": True},
    )
    monkeypatch.setattr(
        service_control.managed_service,
        "stop",
        lambda: events.append(("stop_transient", {})) or {"ok": True},
    )
    monkeypatch.setattr(
        service_control.session_service,
        "stop",
        lambda: events.append(("stop_replacement", {})) or {"ok": True},
    )
    monkeypatch.setattr(
        service_control,
        "_cleanup_legacy_macos",
        lambda **kwargs: events.append(("cleanup", kwargs)) or None,
    )

    result = service_control.stop(preserve_recovery=True)

    assert result["ok"] is True
    assert events == [
        ("migrate", {"source": "restart"}),
        ("stop_transient", {}),
        ("stop_replacement", {}),
    ]


def test_macos_stop_removes_legacy_controller_without_session_install(monkeypatch):
    from aletheia.gateway import autostart, service_control

    events = []
    monkeypatch.setattr(service_control.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        service_control,
        "status",
        lambda: {
            "reason": None,
            "backend": "session",
            "session": _state(),
            "machine": _state(),
        },
    )
    monkeypatch.setattr(
        autostart,
        "_bootout_legacy_macos",
        lambda **kwargs: events.append("cleanup") or ["com.aletheia.gateway.watchdog"],
    )
    monkeypatch.setattr(autostart, "_legacy_launchd_present", lambda **kwargs: [])
    monkeypatch.setattr(
        service_control.managed_service,
        "stop",
        lambda: events.append("stop") or {"ok": True},
    )

    assert service_control.stop(operator_stop=True)["ok"] is True
    assert events == ["stop", "cleanup"]


def test_gateway_enable_command_reaches_current_service_control(monkeypatch):
    from typer.testing import CliRunner

    from aletheia.cli.gateway import gateway_app
    from aletheia.gateway import service_control

    calls = []
    monkeypatch.setattr(
        service_control,
        "enable",
        lambda **kwargs: calls.append(kwargs) or {"ok": True},
    )

    result = CliRunner().invoke(gateway_app, ["enable"])

    assert result.exit_code == 0
    assert calls == [{"source": "cli"}]


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


def test_internal_stop_does_not_remove_legacy_recovery(monkeypatch):
    """Internal stops (readiness failure, update) must not destroy legacy recovery."""
    from aletheia.gateway import autostart, service_control

    monkeypatch.setattr(service_control.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        service_control,
        "status",
        lambda: {"reason": None, "backend": "session", "session": _state(), "machine": _state()},
    )
    monkeypatch.setattr(
        service_control.managed_service,
        "stop",
        lambda: {"ok": True},
    )
    monkeypatch.setattr(
        autostart,
        "_bootout_legacy_macos",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not remove legacy on internal stop")),
    )

    result = service_control.stop()  # no operator_stop=True
    assert result["ok"] is True


def test_normal_stop_selects_installed_persistent_owner_over_transient(monkeypatch):
    """A stale transient registration must not override an installed persistent session service."""
    from aletheia.gateway import service_control

    stopped = []
    monkeypatch.setattr(service_control.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        service_control,
        "status",
        lambda: {
            "reason": None,
            "backend": "session",
            "session": _state(installed=True, running=True),
            "machine": _state(),
            "transient": {"managed": True, "running": True},
        },
    )
    monkeypatch.setattr(
        service_control.session_service,
        "stop",
        lambda: stopped.append("session") or {"ok": True},
    )
    monkeypatch.setattr(
        service_control.managed_service,
        "stop",
        lambda: stopped.append("transient") or {"ok": True},
    )

    result = service_control.stop()
    assert result["ok"] is True
    assert result["transient_stopped"] is True
    assert stopped == ["session", "transient"]


def test_stop_rejects_untrusted_installed_session(monkeypatch):
    from aletheia.gateway import service_control

    session = _state(
        installed=True,
        owned=False,
        config_valid=False,
        reason="config_drift",
    )
    monkeypatch.setattr(
        service_control,
        "status",
        lambda: {
            "reason": "config_drift",
            "backend": "session",
            "session": session,
            "machine": _state(),
            "transient": {"managed": True},
        },
    )
    monkeypatch.setattr(
        service_control.session_service,
        "stop",
        lambda: (_ for _ in ()).throw(AssertionError("must not stop untrusted session")),
    )
    monkeypatch.setattr(
        service_control.managed_service,
        "stop",
        lambda: (_ for _ in ()).throw(AssertionError("must fail before mutation")),
    )

    result = service_control.stop()

    assert result["ok"] is False
    assert result["reason"] == "config_drift"


def test_stop_reports_persistent_runtime_stopped_when_transient_cleanup_fails(
    monkeypatch,
):
    from aletheia.gateway import service_control

    monkeypatch.setattr(
        service_control,
        "status",
        lambda: {
            "reason": None,
            "backend": "session",
            "session": _state(installed=True, running=True),
            "machine": _state(),
            "transient": {"managed": True},
        },
    )
    monkeypatch.setattr(
        service_control.session_service,
        "stop",
        lambda: {"ok": True, "runtime_stopped": True},
    )
    monkeypatch.setattr(
        service_control.managed_service,
        "stop",
        lambda: {"ok": False, "reason": "launchctl_error"},
    )

    result = service_control.stop()

    assert result["ok"] is False
    assert result["persistent_stopped"] is True
    assert result["runtime_stopped"] is True
    assert result["cleanup_target"] == "transient service registration"


def test_stop_does_not_infer_persistent_stop_from_global_pid_state(monkeypatch):
    from aletheia.gateway import service_control

    monkeypatch.setattr(
        service_control,
        "status",
        lambda: {
            "reason": None,
            "backend": "session",
            "session": _state(installed=True, enabled=False, running=True),
            "machine": _state(),
            "transient": {"managed": True, "running": True},
        },
    )
    monkeypatch.setattr(
        service_control.session_service,
        "stop",
        lambda: {"ok": True, "runtime_stopped": False},
    )
    monkeypatch.setattr(
        service_control.managed_service,
        "stop",
        lambda: {"ok": False, "reason": "launchctl_error"},
    )

    result = service_control.stop()

    assert result["ok"] is False
    assert result["persistent_stopped"] is True
    assert result["runtime_stopped"] is False


def test_stop_reports_direct_transient_cleanup_target(monkeypatch):
    from aletheia.gateway import service_control

    monkeypatch.setattr(
        service_control,
        "status",
        lambda: {
            "reason": None,
            "backend": "session",
            "session": _state(),
            "machine": _state(),
            "transient": {"managed": True, "running": True},
        },
    )
    monkeypatch.setattr(
        service_control.managed_service,
        "stop",
        lambda: {
            "ok": False,
            "reason": "launchctl_error",
            "runtime_stopped": True,
        },
    )

    result = service_control.stop()

    assert result["ok"] is False
    assert result["runtime_stopped"] is True
    assert result["cleanup_target"] == "transient service registration"


def test_foreign_enabled_only_session_blocks_stop_disable_and_uninstall(monkeypatch):
    """Enabled-only foreign session definitions must fail closed; no false success."""
    from aletheia.gateway import service_control

    foreign_session = _state(installed=False, enabled=True, owned=False, config_valid=False, reason="ownership_conflict")
    monkeypatch.setattr(
        service_control,
        "status",
        lambda: {
            "reason": "ownership_conflict",
            "backend": "session",
            "running": False,
            "session": foreign_session,
            "machine": _state(),
            "transient": {"managed": True},
        },
    )
    monkeypatch.setattr(
        service_control.session_service,
        "disable",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not mutate foreign definition")),
    )
    monkeypatch.setattr(
        service_control.session_service,
        "uninstall",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not mutate foreign definition")),
    )
    monkeypatch.setattr(
        service_control.managed_service,
        "stop",
        lambda: (_ for _ in ()).throw(AssertionError("must not stop before ownership validation")),
    )

    stop_result = service_control.stop()
    assert stop_result["ok"] is False
    assert stop_result["reason"] == "ownership_conflict"

    disable_result = service_control.disable()
    assert disable_result["ok"] is False
    assert disable_result["reason"] == "ownership_conflict"

    uninstall_result = service_control.uninstall()
    assert uninstall_result["ok"] is False
    assert uninstall_result["reason"] == "ownership_conflict"


def test_disable_cleans_verified_legacy_recovery_without_session_install(monkeypatch):
    from aletheia.gateway import service_control

    monkeypatch.setattr(
        service_control,
        "status",
        lambda: {
            "reason": None,
            "backend": "transient",
            "session": _state(installed=False, enabled=False),
            "machine": _state(),
            "transient": {"managed": True},
        },
    )
    sources = []
    monkeypatch.setattr(
        service_control,
        "_cleanup_legacy_macos",
        lambda *, source: sources.append(source),
    )

    result = service_control.disable(source="circuit_breaker")

    assert result["ok"] is True
    assert result["enabled"] is False
    assert sources == ["circuit_breaker"]


def test_disable_reports_persistent_owner_disabled_when_legacy_cleanup_fails(
    monkeypatch,
):
    from aletheia.gateway import service_control

    monkeypatch.setattr(
        service_control,
        "status",
        lambda: {
            "reason": None,
            "backend": "session",
            "session": _state(installed=True, enabled=True),
            "machine": _state(),
            "transient": {"managed": False},
        },
    )
    monkeypatch.setattr(
        service_control.session_service,
        "disable",
        lambda *, source: {"ok": True, "enabled": False},
    )
    monkeypatch.setattr(
        service_control,
        "_cleanup_legacy_macos",
        lambda *, source: {
            "ok": False,
            "reason": "legacy_launchd_cleanup_error",
            "legacy_mutated": False,
            "legacy_preserved": True,
        },
    )

    result = service_control.disable(source="circuit_breaker")

    assert result["ok"] is False
    assert result["enabled"] is False
    assert result["persistent_disabled"] is True
    assert result["cleanup_target"] == "legacy recovery"


def test_disable_does_not_claim_already_disabled_persistent_owner_was_mutated(
    monkeypatch,
):
    from aletheia.gateway import service_control

    monkeypatch.setattr(
        service_control,
        "status",
        lambda: {
            "reason": None,
            "backend": "session",
            "session": _state(installed=True, enabled=False),
            "machine": _state(),
            "transient": {"managed": False},
        },
    )
    monkeypatch.setattr(
        service_control.session_service,
        "disable",
        lambda *, source: {"ok": True, "enabled": False},
    )
    monkeypatch.setattr(
        service_control,
        "_cleanup_legacy_macos",
        lambda *, source: {
            "ok": False,
            "reason": "legacy_launchd_cleanup_error",
            "legacy_mutated": False,
            "legacy_preserved": True,
        },
    )

    result = service_control.disable(source="circuit_breaker")

    assert result["ok"] is False
    assert result["persistent_disabled"] is False
