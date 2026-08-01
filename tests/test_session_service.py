from types import SimpleNamespace


def _proc(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_windows_definition_is_persistent_but_disabled_and_has_no_watchdog(monkeypatch):
    from aletheia.gateway import session_service
    monkeypatch.setattr(session_service._managed, "_current_user", lambda: r"HOST\user")
    monkeypatch.setattr(session_service, "_windows_program_and_arguments", lambda: (r"C:\Aletheia\aletheia.exe", "__packaged_gateway_runner --no-browser"))
    xml = session_service._build_windows_task_xml(enabled=False)
    assert "<LogonTrigger>" in xml
    assert "<Enabled>false</Enabled>" in xml
    assert "<Repetition>" not in xml
    assert "<TimeTrigger>" not in xml
    assert "<RunLevel>LeastPrivilege</RunLevel>" in xml


def test_windows_install_falls_back_only_after_s4u_access_denied(monkeypatch):
    from aletheia.gateway import session_service
    monkeypatch.setattr(session_service, "_platform", lambda: "Windows")
    monkeypatch.setattr(session_service, "_status_windows", lambda: session_service._result(backend="task_scheduler", owned=True, config_valid=True))
    calls = []
    def run(args, **kwargs):
        if "/Create" in args:
            calls.append(args)
            return _proc(1, stderr="ERROR: Access is denied.") if len(calls) == 1 else _proc(0)
        return _proc(1, stderr="not found")
    monkeypatch.setattr(session_service._managed, "_run_schtasks", run)
    result = session_service.install()
    assert result["ok"] is True
    assert result["logon_type"] == "InteractiveToken"
    assert len(calls) == 2


def test_windows_export_normalization_preserves_owned_task(monkeypatch):
    from aletheia.gateway import session_service

    sid = "S-1-5-21-111-222-333-1001"
    monkeypatch.setattr(session_service._managed, "_current_user", lambda: r"HOST\user")
    monkeypatch.setattr(session_service, "_windows_current_user_ids", lambda: {r"host\user", sid.casefold()})
    monkeypatch.setattr(
        session_service,
        "_windows_program_and_arguments",
        lambda: (r"C:\Aletheia\aletheia.exe", "__packaged_gateway_runner --no-browser"),
    )
    exported = (
        session_service._build_windows_task_xml(enabled=False)
        .replace(r"HOST\user", sid)
        .replace("<LogonType>S4U</LogonType>", "<LogonType>InteractiveToken</LogonType>")
        .replace("<RunLevel>LeastPrivilege</RunLevel>", "")
    )
    parsed = session_service._parse_windows_task(exported)
    assert parsed["owned"] is True
    assert parsed["config_valid"] is True


def test_windows_export_omitted_enabled_setting_means_enabled(monkeypatch):
    from aletheia.gateway import session_service

    monkeypatch.setattr(session_service._managed, "_current_user", lambda: r"HOST\user")
    monkeypatch.setattr(session_service, "_windows_current_user_ids", lambda: {r"host\user"})
    monkeypatch.setattr(
        session_service,
        "_windows_program_and_arguments",
        lambda: (r"C:\Aletheia\aletheia.exe", "__packaged_gateway_runner --no-browser"),
    )
    exported = session_service._build_windows_task_xml(enabled=True).replace(
        "<Enabled>true</Enabled><Hidden>true</Hidden>", "<Hidden>true</Hidden>"
    )
    parsed = session_service._parse_windows_task(exported)
    assert parsed["enabled"] is True
    assert parsed["config_valid"] is True


def test_windows_export_normalization_rejects_foreign_sid(monkeypatch):
    from aletheia.gateway import session_service

    monkeypatch.setattr(session_service._managed, "_current_user", lambda: r"HOST\user")
    monkeypatch.setattr(session_service, "_windows_current_user_ids", lambda: {r"host\user", "s-1-5-21-111-222-333-1001"})
    monkeypatch.setattr(
        session_service,
        "_windows_program_and_arguments",
        lambda: (r"C:\Aletheia\aletheia.exe", "__packaged_gateway_runner --no-browser"),
    )
    exported = session_service._build_windows_task_xml().replace(
        r"HOST\user", "S-1-5-21-999-888-777-1001"
    )
    assert session_service._parse_windows_task(exported)["owned"] is False


def test_windows_enable_does_not_start(monkeypatch):
    from aletheia.gateway import session_service
    monkeypatch.setattr(session_service, "_status_windows", lambda: session_service._result(backend="task_scheduler", installed=True, enabled=False, owned=True, config_valid=True))
    calls = []
    monkeypatch.setattr(session_service._managed, "_run_schtasks", lambda args, **kwargs: calls.append(args) or _proc(0))
    result = session_service._change_windows(True)
    assert result["ok"] is True and result["enabled"] is True
    assert any("/ENABLE" in call for call in calls)
    assert not any("/Run" in call for call in calls)


def test_windows_stop_retains_definition(monkeypatch):
    from aletheia.gateway import session_service
    monkeypatch.setattr(session_service, "_status_windows", lambda: session_service._result(backend="task_scheduler", installed=True, enabled=True, running=True, owned=True, config_valid=True))
    calls = []
    monkeypatch.setattr(session_service._managed, "_run_schtasks", lambda args, **kwargs: calls.append(args) or _proc(0))
    result = session_service._stop_windows()
    assert result["ok"] is True and result["enabled"] is True
    assert any("/End" in call for call in calls)
    assert not any("/Delete" in call for call in calls)


def test_windows_unknown_locale_absence_uses_persistent_task_probe(monkeypatch):
    from aletheia.gateway import session_service
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return _proc(1, stderr="unrecognized localized error")

    monkeypatch.setattr(session_service._managed, "_run_schtasks", run)
    result = session_service._status_windows()
    assert result["installed"] is False
    assert result.get("reason") is None
    assert calls[0][-1] == "/XML"
    assert calls[1][-1] == session_service.WINDOWS_TASK_NAME


def test_macos_enablement_is_separate_from_running_state(tmp_path, monkeypatch):
    from aletheia.gateway import session_service
    canonical = tmp_path / "service.plist"
    enabled = tmp_path / "LaunchAgents" / "service.plist"
    monkeypatch.setattr(session_service, "_macos_canonical_path", lambda: canonical)
    monkeypatch.setattr(session_service, "_macos_enabled_path", lambda: enabled)
    monkeypatch.setattr(session_service, "_build_macos_plist", lambda: {"Label": session_service.MACOS_LABEL, "ProgramArguments": ["aletheia"], "RunAtLoad": True})
    monkeypatch.setattr(session_service, "_status_macos", lambda: session_service._result(backend="launchd", installed=canonical.exists(), enabled=enabled.exists(), running=False, owned=True, config_valid=True))
    assert session_service._install_macos()["ok"] is True
    assert session_service._enable_macos(True)["ok"] is True
    assert enabled.exists()
    assert session_service._enable_macos(False)["ok"] is True
    assert canonical.exists() and not enabled.exists()


def test_linux_unit_uses_native_on_failure_semantics(monkeypatch):
    from aletheia.gateway import session_service
    monkeypatch.setattr(session_service._managed, "_runner_command", lambda **kwargs: ["/opt/aletheia", "__packaged_gateway_runner", "--no-browser"])
    unit = session_service._build_systemd_unit()
    assert "Type=notify" in unit
    assert "Restart=on-failure" in unit
    assert "WantedBy=default.target" in unit
    assert "--no-browser" in unit
