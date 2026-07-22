"""Contract tests for the gateway's on-demand OS-service ownership."""

from types import SimpleNamespace


def _proc(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_macos_job_is_on_demand_and_runs_runner_in_foreground(tmp_path, monkeypatch):
    from aletheia.gateway import managed_service as service

    monkeypatch.setattr(service, "get_data_dir", lambda: tmp_path)
    monkeypatch.setattr(
        service, "_runner_command", lambda **_kwargs: ["/venv/bin/python", "/app/_gateway_runner.py", "--no-browser"],
    )

    payload = service._build_macos_plist(no_browser=True)

    assert payload["ProgramArguments"][-2:] == ["/app/_gateway_runner.py", "--no-browser"]
    assert {"KeepAlive", "RunAtLoad", "StartInterval"}.isdisjoint(payload)
    assert service._validate_macos_plist(payload, no_browser=True)


def test_macos_start_and_stop_register_only_for_the_running_session(tmp_path, monkeypatch):
    from aletheia.gateway import managed_service as service

    monkeypatch.setattr(service, "get_data_dir", lambda: tmp_path)
    monkeypatch.setattr(service, "_runner_command", lambda **_kwargs: ["/venv/bin/python", "/app/_gateway_runner.py"])
    calls: list[list[str]] = []
    def launchctl(args):
        calls.append(args)
        return _proc(returncode=113, stderr="Could not find service") if args[0] == "bootout" else _proc()

    monkeypatch.setattr(service, "_launchctl", launchctl)

    assert service._start_macos(no_browser=True)["ok"] is True
    assert ["bootstrap", service._macos_domain(), str(service._macos_plist_path())] in calls
    assert ["kickstart", service._macos_target()] in calls
    assert service._macos_plist_path().exists()

    assert service._stop_macos()["ok"] is True
    assert ["bootout", service._macos_target()] == calls[-1]
    assert not service._macos_plist_path().exists()


def test_linux_uses_a_non_restarting_user_transient_service(monkeypatch):
    from aletheia.gateway import managed_service as service

    monkeypatch.setattr(service, "_runner_command", lambda **_kwargs: ["/venv/bin/python", "/app/_gateway_runner.py", "--no-browser"])
    calls: list[list[str]] = []
    monkeypatch.setattr(service.subprocess, "run", lambda args, **_kwargs: calls.append(args) or _proc())

    result = service._start_linux(no_browser=True)

    assert result["ok"] is True
    command = calls[0]
    assert command[:4] == ["systemd-run", "--user", "--unit", service.SYSTEMD_UNIT]
    assert "--property=Restart=no" in command
    assert command[-3:] == ["/venv/bin/python", "/app/_gateway_runner.py", "--no-browser"]
    assert "tmux" not in command and "screen" not in command


def test_linux_stop_stops_the_managed_unit(monkeypatch):
    from aletheia.gateway import managed_service as service

    calls: list[list[str]] = []
    monkeypatch.setattr(service, "_systemctl", lambda args: calls.append(args) or _proc())

    assert service._stop_linux()["ok"] is True
    assert calls == [["stop", service.SYSTEMD_UNIT], ["reset-failed", service.SYSTEMD_UNIT]]


def test_windows_task_is_demand_only_without_a_schedule(monkeypatch):
    from aletheia.gateway import managed_service as service

    monkeypatch.setattr(service, "_windows_program_and_arguments", lambda **_kwargs: (r"C:\\Python\\pythonw.exe", 'C:\\app\\_gateway_runner.py --no-browser'))
    monkeypatch.setattr(service, "_current_user", lambda: "DOMAIN\\operator")

    xml = service._build_windows_task_xml(no_browser=True)

    assert "<Triggers" not in xml
    assert "<AllowStartOnDemand>true</AllowStartOnDemand>" in xml
    assert "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>" in xml
    assert "_gateway_runner.py --no-browser" in xml


def test_windows_program_arguments_checks_windows_venv_config_as_filesystem_path(monkeypatch):
    """Windows-style paths must not call ``exists`` on ``PureWindowsPath``."""
    from aletheia.gateway import managed_service as service

    monkeypatch.setattr(service.sys, "executable", r"C:\\venv\\Scripts\\python.exe")
    monkeypatch.setattr(service, "_windows_python", lambda: r"C:\\Python\\pythonw.exe")
    monkeypatch.setattr(service.Path, "exists", lambda _path: True)
    monkeypatch.setattr(
        service,
        "_runner_command",
        lambda **_kwargs: [r"C:\\venv\\Scripts\\python.exe", r"C:\\app\\_gateway_runner.py", "--no-browser"],
    )

    program, arguments = service._windows_program_and_arguments(no_browser=True)

    assert program == r"C:\\Python\\pythonw.exe"
    assert "site.addsitedir" in arguments


def test_windows_start_then_stop_runs_and_removes_the_task(monkeypatch):
    from aletheia.gateway import managed_service as service

    monkeypatch.setattr(service, "_build_windows_task_xml", lambda **_kwargs: "<Task />")
    calls: list[list[str]] = []
    monkeypatch.setattr(service.subprocess, "run", lambda args, **_kwargs: calls.append(args) or _proc())

    assert service._start_windows(no_browser=True)["ok"] is True
    assert calls[0][:4] == ["schtasks", "/Create", "/TN", service.WINDOWS_TASK_NAME]
    assert calls[1] == ["schtasks", "/Run", "/TN", service.WINDOWS_TASK_NAME]

    assert service._stop_windows()["ok"] is True
    assert calls[2] == ["schtasks", "/End", "/TN", service.WINDOWS_TASK_NAME]
    assert calls[3] == ["schtasks", "/Delete", "/TN", service.WINDOWS_TASK_NAME, "/F"]
