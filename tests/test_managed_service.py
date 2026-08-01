"""Contract tests for the gateway's on-demand OS-service ownership."""

from types import SimpleNamespace

import pytest


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
    # Simulate the macOS launchd domain without os.getuid() so this test is
    # hermetic on Windows (#224).
    monkeypatch.setattr(service, "_macos_domain", lambda: "gui/501")
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


@pytest.mark.parametrize(
    ("username", "domain", "computername", "fallback", "expected"),
    [
        ("operator", "CORP", "HOST", "fallback", r"CORP\operator"),
        ("operator", "HOST", "HOST", "fallback", r"HOST\operator"),
        ("operator", "WORKGROUP", "HOST", "fallback", r"HOST\operator"),
        ("operator", "workgroup", "HOST", "fallback", r"HOST\operator"),
        (None, None, None, "fallback", "fallback"),
        (r"CORP\operator", "IGNORED", "HOST", "fallback", r"CORP\operator"),
        ("operator@example.test", "IGNORED", "HOST", "fallback", "operator@example.test"),
    ],
)
def test_windows_current_user_resolves_a_mappable_account_authority(
    monkeypatch, username, domain, computername, fallback, expected,
):
    from aletheia.gateway import windows_identity

    for key, value in {
        "USERNAME": username,
        "USERDOMAIN": domain,
        "COMPUTERNAME": computername,
    }.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    monkeypatch.setattr(windows_identity.getpass, "getuser", lambda: fallback)

    assert windows_identity.current_windows_user() == expected


def test_windows_service_and_watchdog_share_the_principal_resolver():
    from aletheia.gateway import autostart, managed_service, windows_identity

    assert managed_service._current_user is windows_identity.current_windows_user
    assert autostart._current_user is windows_identity.current_windows_user


def test_windows_task_xml_escapes_the_resolved_principal(monkeypatch):
    from aletheia.gateway import managed_service as service

    monkeypatch.setattr(
        service,
        "_windows_program_and_arguments",
        lambda **_kwargs: (r"C:\Python\pythonw.exe", r"C:\app\_gateway_runner.py"),
    )
    monkeypatch.setattr(service, "_current_user", lambda: r"HOST&LAB\op<er")

    xml = service._build_windows_task_xml(no_browser=True)

    assert r"HOST&amp;LAB\op&lt;er" in xml
    assert r"HOST&LAB\op<er" not in xml


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
    monkeypatch.setattr(service, "_status_windows", lambda: {"managed": False})
    calls: list[list[str]] = []
    monkeypatch.setattr(service.subprocess, "run", lambda args, **_kwargs: calls.append(args) or _proc())

    assert service._start_windows(no_browser=True)["ok"] is True
    assert calls[0][:4] == ["schtasks", "/Create", "/TN", service.WINDOWS_TASK_NAME]
    assert calls[1] == ["schtasks", "/Run", "/TN", service.WINDOWS_TASK_NAME]

    assert service._stop_windows()["ok"] is True
    assert calls[2] == ["schtasks", "/End", "/TN", service.WINDOWS_TASK_NAME]
    assert calls[3] == ["schtasks", "/Delete", "/TN", service.WINDOWS_TASK_NAME, "/F"]


def test_windows_stop_is_idempotent_with_localized_not_found_bytes(monkeypatch):
    """schtasks emits console-code-page bytes; an absent task is still a clean stop."""
    from aletheia.gateway import console_text, managed_service as service

    monkeypatch.setattr(console_text, "console_encoding", lambda: "gbk")
    absent = "错误: 系统找不到指定的文件。\r\r\n".encode("gbk")
    monkeypatch.setattr(
        service.subprocess, "run",
        lambda args, **_kwargs: _proc(returncode=1, stderr=absent),
    )

    result = service._stop_windows()
    assert result["ok"] is True
    assert result["managed"] is False


def test_windows_task_xml_uses_s4u_not_interactive_token(monkeypatch):
    """LogonType must be S4U so the task runs without an active interactive session.

    InteractiveToken requires the user to hold Session 1+ (a live interactive desktop
    session). SSH-only users (SessionId=0) never have one, so the task scheduler
    launches the process and it exits immediately — silently — producing no pid file
    and no listening port.  S4U allows the task to run in a non-interactive context
    while still running as the user, not as SYSTEM.

    Regression for: [gateway][runtime] Managed Gateway start fails without registering
    or listening (introduced by commit 7dabe1e1 which correctly fixed the principal
    resolution from WORKGROUP\\user to COMPUTERNAME\\user but did not change the logon
    type, leaving the task able to be created but unable to execute under SSH).
    """
    from aletheia.gateway import managed_service as service

    monkeypatch.setattr(service, "_windows_program_and_arguments", lambda **_kwargs: (r"C:\Python\pythonw.exe", r"C:\app\_gateway_runner.py"))
    monkeypatch.setattr(service, "_current_user", lambda: r"CASI-AI\yixian")

    xml = service._build_windows_task_xml(no_browser=True)

    assert "<LogonType>S4U</LogonType>" in xml
    assert "<LogonType>InteractiveToken</LogonType>" not in xml


def test_windows_stop_keeps_failure_when_the_task_survives(monkeypatch):
    """An unrecognized delete failure is only forgiven when the task is really gone."""
    from aletheia.gateway import console_text, managed_service as service

    monkeypatch.setattr(console_text, "console_encoding", lambda: "cp932")
    unknown = "エラー: 不明なエラーが発生しました。".encode("cp932")

    def fake_run(args, **_kwargs):
        if "/Query" in args:
            return _proc(returncode=0, stdout="<Task />")
        return _proc(returncode=1, stderr=unknown)

    monkeypatch.setattr(service.subprocess, "run", fake_run)

    result = service._stop_windows()
    assert result["ok"] is False
    assert result["reason"] == "schtasks_error"
    assert "エラー" in result["detail"]


def test_windows_stop_reports_access_denied_without_deleting_task(monkeypatch):
    """A scheduler ownership denial must remain actionable, not become a generic error."""
    from aletheia.gateway import managed_service as service

    calls: list[list[str]] = []

    def fake_run(args, **_kwargs):
        calls.append(args)
        return _proc(returncode=1, stderr=b"ERROR: Access is denied.\r\n")

    monkeypatch.setattr(service.subprocess, "run", fake_run)

    result = service._stop_windows()

    assert result["ok"] is False
    assert result["managed"] is None
    assert result["reason"] == "access_denied"
    assert calls == [["schtasks", "/End", "/TN", service.WINDOWS_TASK_NAME]]


def test_windows_status_keeps_access_denied_as_unknown_ownership(monkeypatch):
    from aletheia.gateway import managed_service as service

    monkeypatch.setattr(
        service.subprocess, "run",
        lambda *args, **_kwargs: _proc(returncode=1, stderr=b"ERROR: Access is denied.\r\n"),
    )

    result = service._status_windows()

    assert result["managed"] is None
    assert result["reason"] == "access_denied"


def test_windows_start_refuses_to_replace_unverified_same_named_task(monkeypatch):
    from aletheia.gateway import managed_service as service

    monkeypatch.setattr(service, "_status_windows", lambda: {"managed": None, "reason": "ownership_conflict"})
    calls: list[list[str]] = []
    monkeypatch.setattr(service.subprocess, "run", lambda args, **_kwargs: calls.append(args) or _proc())

    result = service._start_windows(no_browser=True)

    assert result["ok"] is False
    assert result["reason"] == "ownership_conflict"
    assert calls == []


def test_linux_start_uses_notify_service_type(monkeypatch):
    """T8: _start_linux must use --service-type=notify so systemd waits for the
    sd_notify READY=1 signal before marking the unit active, rather than marking
    it active the moment the process starts.
    """
    from aletheia.gateway import managed_service as service

    monkeypatch.setattr(service, "_runner_command", lambda **_kwargs: ["/venv/bin/python", "/app/_gateway_runner.py", "--no-browser"])
    calls: list[list[str]] = []
    monkeypatch.setattr(service.subprocess, "run", lambda args, **_kwargs: calls.append(args) or _proc())

    result = service._start_linux(no_browser=True)

    assert result["ok"] is True
    command = calls[0]
    assert "--service-type=notify" in command
    assert "--service-type=exec" not in command
    assert "--property=TimeoutStartSec=120" in command
