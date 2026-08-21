"""Persistent, user-scoped Gateway service backends.

This module owns installed session definitions.  It is separate from the
transient definitions in :mod:`aletheia.gateway.managed_service` so existing
``gateway start`` remains usable before an operator installs a service.
"""

from __future__ import annotations

import os
import platform
import plistlib
import re
import shlex
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from aletheia.config.loader import _PROJECT_ROOT, get_data_dir
from aletheia.gateway import managed_service as _managed
from aletheia.gateway.pidfile import is_running
from aletheia.gateway.windows_identity import current_windows_user_ids as _windows_current_user_ids

WINDOWS_TASK_NAME = "Aletheia Trading OS Gateway Service"
MACOS_LABEL = "com.aletheia.trading-os.gateway.service"
SYSTEMD_UNIT = "aletheia-gateway.service"
_XML_NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"


def _platform() -> str:
    return platform.system()


def _result(**values) -> dict:
    base = {
        "scope": "session",
        "supported": True,
        "installed": False,
        "enabled": False,
        "running": False,
        "owned": None,
        "config_valid": None,
        "recovery_scope": "none",
    }
    base.update(values)
    return base


def _audit(event_type: str, message: str, *, source: str) -> None:
    try:
        from aletheia.files.jsonl import write_audit

        write_audit({
            "actor": "human",
            "source": source,
            "event_type": event_type,
            "entity_type": "gateway_service",
            "entity_id": "session",
            "message": message,
        })
    except Exception:
        pass


def _xml_escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _windows_program_and_arguments() -> tuple[str, str]:
    return _managed._windows_program_and_arguments(no_browser=True)


def _build_windows_task_xml(*, logon_type: str = "S4U", enabled: bool = False) -> str:
    if logon_type not in {"S4U", "InteractiveToken"}:
        raise ValueError(f"unsupported Windows task logon type: {logon_type}")
    command, arguments = _windows_program_and_arguments()
    user = _xml_escape(_managed._current_user())
    enabled_text = "true" if enabled else "false"
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        f'<Task version="1.2" xmlns="{_XML_NS}">\n'
        "  <RegistrationInfo><Description>Aletheia persistent user Gateway service.</Description></RegistrationInfo>\n"
        "  <Triggers><LogonTrigger><Enabled>true</Enabled>"
        f"<UserId>{user}</UserId></LogonTrigger></Triggers>\n"
        "  <Principals><Principal id=\"Author\">"
        f"<UserId>{user}</UserId><LogonType>{logon_type}</LogonType>"
        "<RunLevel>LeastPrivilege</RunLevel></Principal></Principals>\n"
        "  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>"
        "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>"
        "<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>"
        "<AllowStartOnDemand>true</AllowStartOnDemand><StartWhenAvailable>true</StartWhenAvailable>"
        f"<ExecutionTimeLimit>PT0S</ExecutionTimeLimit><Enabled>{enabled_text}</Enabled><Hidden>true</Hidden></Settings>\n"
        "  <Actions Context=\"Author\"><Exec>"
        f"<Command>{_xml_escape(command)}</Command>"
        f"<Arguments>{_xml_escape(arguments)}</Arguments>"
        f"<WorkingDirectory>{_xml_escape(str(_PROJECT_ROOT))}</WorkingDirectory>"
        "</Exec></Actions>\n"
        "</Task>\n"
    )


def _parse_windows_task(xml: str) -> dict:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return {}
    ns = {"t": _XML_NS}

    def value(path: str) -> str:
        node = root.find(path, ns)
        return (node.text or "").strip() if node is not None else ""

    command, arguments = _windows_program_and_arguments()
    enabled_value = value(".//t:Settings/t:Enabled").casefold()
    actual = {
        "command": value(".//t:Actions/t:Exec/t:Command"),
        "arguments": value(".//t:Actions/t:Exec/t:Arguments"),
        "working_directory": value(".//t:Actions/t:Exec/t:WorkingDirectory"),
        "user": value(".//t:Principals/t:Principal/t:UserId"),
        "logon_type": value(".//t:Principals/t:Principal/t:LogonType"),
        "run_level": value(".//t:Principals/t:Principal/t:RunLevel"),
        # Task Scheduler omits Enabled after /ENABLE because true is the schema default.
        "enabled": enabled_value in {"", "true", "1"},
        "enabled_value_valid": enabled_value in {"", "true", "false", "1", "0"},
        "has_logon_trigger": root.find(".//t:Triggers/t:LogonTrigger", ns) is not None,
        "has_repetition": root.find(".//t:Repetition", ns) is not None,
        "has_time_trigger": root.find(".//t:Triggers/t:TimeTrigger", ns) is not None,
    }
    actual["owned"] = (
        actual["command"] == command
        and actual["arguments"] == arguments
        and actual["working_directory"] == str(_PROJECT_ROOT)
        and actual["user"].casefold() in _windows_current_user_ids()
        and actual["logon_type"] in {"S4U", "InteractiveToken"}
        and actual["run_level"] in {"", "LeastPrivilege"}
    )
    actual["config_valid"] = (
        actual["owned"]
        and actual["enabled_value_valid"]
        and actual["has_logon_trigger"]
        and not actual["has_repetition"]
        and not actual["has_time_trigger"]
    )
    return actual


def _windows_task_exists() -> bool | None:
    """Probe this persistent task by exit code, independent of localized text."""
    try:
        proc = _managed._run_schtasks(["schtasks", "/Query", "/TN", WINDOWS_TASK_NAME])
    except Exception:
        return None
    if proc.returncode == 0:
        return True
    if _managed._is_access_denied(proc.stderr):
        return None
    return False


def _status_windows() -> dict:
    try:
        proc = _managed._run_schtasks(["schtasks", "/Query", "/TN", WINDOWS_TASK_NAME, "/XML"])
    except FileNotFoundError:
        return _result(supported=False, backend="task_scheduler", reason="schtasks_unavailable")
    except Exception as exc:
        return _result(backend="task_scheduler", enabled=None, reason="schtasks_error", detail=type(exc).__name__)
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip()[:200]
        if _managed._is_access_denied(proc.stderr):
            return _result(backend="task_scheduler", enabled=None, owned=None, reason="access_denied", detail=detail)
        if _managed._is_not_found(proc.stderr) or _windows_task_exists() is False:
            return _result(backend="task_scheduler", owned=True, config_valid=True)
        return _result(backend="task_scheduler", enabled=None, reason="schtasks_error", detail=detail)
    parsed = _parse_windows_task(proc.stdout)
    if not parsed or not parsed.get("owned"):
        return _result(
            backend="task_scheduler", installed=True, enabled=None, owned=False,
            config_valid=False, reason="ownership_conflict",
        )
    return _result(
        backend="task_scheduler",
        installed=True,
        enabled=parsed["enabled"],
        running=is_running(),
        owned=True,
        config_valid=parsed["config_valid"],
        logon_type=parsed["logon_type"],
        recovery_scope="login",
        **({"reason": "config_drift"} if not parsed["config_valid"] else {}),
    )


def _create_windows(*, enabled: bool) -> dict:
    selected = "S4U"
    for logon_type in ("S4U", "InteractiveToken"):
        temp_path: str | None = None
        try:
            xml = _build_windows_task_xml(logon_type=logon_type, enabled=enabled)
            with tempfile.NamedTemporaryFile(mode="wb", suffix=".xml", delete=False) as handle:
                handle.write(xml.encode("utf-16"))
                temp_path = handle.name
            proc = _managed._run_schtasks(
                ["schtasks", "/Create", "/TN", WINDOWS_TASK_NAME, "/XML", temp_path, "/F"]
            )
        except FileNotFoundError:
            return _result(supported=False, backend="task_scheduler", reason="schtasks_unavailable", ok=False)
        except Exception as exc:
            return _result(backend="task_scheduler", reason="schtasks_error", detail=type(exc).__name__, ok=False)
        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
        if proc.returncode == 0:
            selected = logon_type
            return _result(
                backend="task_scheduler", installed=True, enabled=enabled, owned=True,
                config_valid=True, recovery_scope="login", logon_type=selected, ok=True,
            )
        if logon_type != "S4U" or not _managed._is_access_denied(proc.stderr):
            reason = "access_denied" if _managed._is_access_denied(proc.stderr) else "schtasks_error"
            return _result(
                backend="task_scheduler", reason=reason,
                detail=(proc.stderr or "").strip()[:200], ok=False,
            )
    return _result(backend="task_scheduler", reason="schtasks_error", ok=False)


def _change_windows(enabled: bool) -> dict:
    current = _status_windows()
    if not current.get("installed"):
        if not enabled:
            return {**current, "ok": True}
        created = _create_windows(enabled=False)
        if not created.get("ok"):
            return created
    elif current.get("owned") is not True:
        return {**current, "ok": False}
    try:
        proc = _managed._run_schtasks([
            "schtasks", "/Change", "/TN", WINDOWS_TASK_NAME,
            "/ENABLE" if enabled else "/DISABLE",
        ])
    except Exception as exc:
        return {**current, "ok": False, "reason": "schtasks_error", "detail": type(exc).__name__}
    if proc.returncode != 0:
        reason = "access_denied" if _managed._is_access_denied(proc.stderr) else "schtasks_error"
        return {**current, "ok": False, "reason": reason, "detail": (proc.stderr or "").strip()[:200]}
    return {**current, "installed": True, "enabled": enabled, "owned": True, "ok": True, "recovery_scope": "login"}


def _start_windows() -> dict:
    current = _status_windows()
    if not current.get("installed"):
        return {**current, "ok": False, "reason": "not_installed"}
    if current.get("owned") is not True:
        return {**current, "ok": False}
    if current.get("running"):
        return {**current, "ok": True, "already_running": True}
    restore_disabled = not current.get("enabled")
    if restore_disabled:
        changed = _change_windows(True)
        if not changed.get("ok"):
            return changed
    try:
        started = _managed._run_schtasks(["schtasks", "/Run", "/TN", WINDOWS_TASK_NAME])
        restored = _change_windows(False) if restore_disabled else {"ok": True}
    except Exception as exc:
        return {**current, "ok": False, "reason": "schtasks_error", "detail": type(exc).__name__}
    if started.returncode != 0:
        reason = "access_denied" if _managed._is_access_denied(started.stderr) else "schtasks_error"
        return {**current, "ok": False, "reason": reason, "detail": (started.stderr or "").strip()[:200]}
    if not restored.get("ok"):
        return {**current, "ok": False, "reason": "partial_transition", "detail": "started but enablement could not be restored"}
    return {**current, "ok": True, "running": True}


def _stop_windows() -> dict:
    current = _status_windows()
    if not current.get("installed"):
        return {**current, "ok": True}
    if current.get("owned") is not True:
        return {**current, "ok": False}
    try:
        proc = _managed._run_schtasks(["schtasks", "/End", "/TN", WINDOWS_TASK_NAME])
    except Exception as exc:
        return {**current, "ok": False, "reason": "schtasks_error", "detail": type(exc).__name__}
    if proc.returncode != 0 and not _managed._is_not_found(proc.stderr):
        reason = "access_denied" if _managed._is_access_denied(proc.stderr) else "schtasks_error"
        return {**current, "ok": False, "reason": reason, "detail": (proc.stderr or "").strip()[:200]}
    return {**current, "ok": True, "running": False}


def _uninstall_windows() -> dict:
    current = _status_windows()
    if not current.get("installed"):
        return {**current, "ok": True}
    if current.get("owned") is not True:
        return {**current, "ok": False}
    try:
        proc = _managed._run_schtasks(["schtasks", "/Delete", "/TN", WINDOWS_TASK_NAME, "/F"])
    except Exception as exc:
        return {**current, "ok": False, "reason": "schtasks_error", "detail": type(exc).__name__}
    if proc.returncode != 0 and not _managed._is_not_found(proc.stderr):
        reason = "access_denied" if _managed._is_access_denied(proc.stderr) else "schtasks_error"
        return {**current, "ok": False, "reason": reason, "detail": (proc.stderr or "").strip()[:200]}
    return _result(backend="task_scheduler", owned=True, config_valid=True, ok=True)


def _macos_canonical_path() -> Path:
    return get_data_dir() / "service" / f"{MACOS_LABEL}.plist"


def _macos_enabled_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{MACOS_LABEL}.plist"


def _macos_domain() -> str:
    return f"gui/{os.getuid()}"


def _macos_target() -> str:
    return f"{_macos_domain()}/{MACOS_LABEL}"


def _build_macos_plist() -> dict:
    log_dir = get_data_dir() / "logs"
    return {
        "Label": MACOS_LABEL,
        "ProgramArguments": _managed._runner_command(no_browser=True),
        "WorkingDirectory": str(_PROJECT_ROOT),
        "RunAtLoad": True,
        "ProcessType": "Background",
        "StandardOutPath": str(log_dir / "gateway-service.log"),
        "StandardErrorPath": str(log_dir / "gateway-service-errors.log"),
    }


def _runner_commands_match(actual: object, expected: object) -> bool:
    if actual == expected:
        return True
    if not isinstance(actual, list) or not isinstance(expected, list):
        return False
    if len(actual) != len(expected) or actual[1:] != expected[1:]:
        return False
    if not all(isinstance(part, str) for part in (*actual, *expected)):
        return False
    actual_python, expected_python = Path(actual[0]), Path(expected[0])
    if actual_python.parent != expected_python.parent:
        return False
    if not actual_python.name.startswith("python") or not expected_python.name.startswith("python"):
        return False
    try:
        return actual_python.samefile(expected_python)
    except OSError:
        return False


def _write_plist(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=False)
    with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
        handle.write(data)
        temp = Path(handle.name)
    os.chmod(temp, 0o600)
    os.replace(temp, path)


def _macos_payload_valid(payload: dict) -> bool:
    expected = _build_macos_plist()
    return (
        _runner_commands_match(
            payload.get("ProgramArguments"), expected["ProgramArguments"]
        )
        and {key: value for key, value in payload.items() if key != "ProgramArguments"}
        == {key: value for key, value in expected.items() if key != "ProgramArguments"}
        and "KeepAlive" not in payload
        and "StartInterval" not in payload
        and "EnvironmentVariables" not in payload
    )


def _status_macos() -> dict:
    canonical = _macos_canonical_path()
    enabled_path = _macos_enabled_path()
    if not canonical.exists():
        return _result(backend="launchd", owned=True, config_valid=True)
    try:
        payload = plistlib.loads(canonical.read_bytes())
        valid = _macos_payload_valid(payload)
        if enabled_path.exists():
            valid = valid and _macos_payload_valid(
                plistlib.loads(enabled_path.read_bytes())
            )
    except Exception:
        valid = False
    loaded = False
    try:
        loaded = _managed._launchctl(["print", _macos_target()]).returncode == 0
    except Exception:
        pass
    return _result(
        backend="launchd", installed=True, enabled=enabled_path.exists(), running=is_running(),
        owned=valid, config_valid=valid, recovery_scope="login",
        loaded=loaded, **({"reason": "config_drift"} if not valid else {}),
    )


def _install_macos() -> dict:
    current = _status_macos()
    if current.get("installed") and current.get("owned") is not True:
        return {**current, "ok": False, "reason": "ownership_conflict"}
    try:
        _write_plist(_macos_canonical_path(), _build_macos_plist())
    except Exception as exc:
        return {**current, "ok": False, "reason": "plist_write_error", "detail": type(exc).__name__}
    return _result(backend="launchd", installed=True, owned=True, config_valid=True, recovery_scope="login", ok=True)


def _enable_macos(enabled: bool) -> dict:
    current = _status_macos()
    if not current.get("installed"):
        installed = _install_macos()
        if not installed.get("ok"):
            return installed
    elif current.get("owned") is not True:
        return {**current, "ok": False, "reason": "ownership_conflict"}
    try:
        if enabled:
            _write_plist(_macos_enabled_path(), _build_macos_plist())
        else:
            _macos_enabled_path().unlink(missing_ok=True)
    except Exception as exc:
        return {**current, "ok": False, "reason": "plist_write_error", "detail": type(exc).__name__}
    return {**_status_macos(), "enabled": enabled, "ok": True}


def _start_macos() -> dict:
    current = _status_macos()
    if not current.get("installed"):
        return {**current, "ok": False, "reason": "not_installed"}
    if current.get("owned") is not True:
        return {**current, "ok": False, "reason": "ownership_conflict"}
    source = _macos_enabled_path() if current.get("enabled") else _macos_canonical_path()
    try:
        old = _managed._launchctl(["bootout", _macos_target()])
        if old.returncode != 0 and not _managed._is_not_found(old.stderr):
            return {**current, "ok": False, "reason": "launchctl_error", "detail": (old.stderr or "")[:200]}
        loaded = _managed._launchctl(["bootstrap", _macos_domain(), str(source)])
        if loaded.returncode != 0:
            return {**current, "ok": False, "reason": "launchctl_error", "detail": (loaded.stderr or "")[:200]}
        started = _managed._launchctl(["kickstart", _macos_target()])
        if started.returncode != 0:
            return {**current, "ok": False, "reason": "launchctl_error", "detail": (started.stderr or "")[:200]}
    except Exception as exc:
        return {**current, "ok": False, "reason": "launchctl_error", "detail": type(exc).__name__}
    return {**current, "running": True, "ok": True}


def _stop_macos() -> dict:
    current = _status_macos()
    if not current.get("installed"):
        return {**current, "ok": True}
    try:
        proc = _managed._launchctl(["bootout", _macos_target()])
    except Exception as exc:
        return {**current, "ok": False, "reason": "launchctl_error", "detail": type(exc).__name__}
    if proc.returncode != 0 and not _managed._is_not_found(proc.stderr):
        return {**current, "ok": False, "reason": "launchctl_error", "detail": (proc.stderr or "")[:200]}
    return {**current, "running": False, "ok": True}


def _uninstall_macos() -> dict:
    current = _status_macos()
    if not current.get("installed"):
        return {**current, "ok": True}
    if current.get("owned") is not True:
        return {**current, "ok": False, "reason": "ownership_conflict"}
    stopped = _stop_macos()
    if not stopped.get("ok"):
        return stopped
    try:
        _macos_enabled_path().unlink(missing_ok=True)
        _macos_canonical_path().unlink(missing_ok=True)
    except Exception as exc:
        return {**current, "ok": False, "reason": "plist_remove_error", "detail": type(exc).__name__}
    return _result(backend="launchd", owned=True, config_valid=True, ok=True)


def _systemd_unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / SYSTEMD_UNIT


def _gateway_memory_limits() -> tuple[str, str]:
    """Return (MemoryMax, MemoryHigh) systemd values scaled to machine RAM.

    Ratios:
      ≤2 GB → 60% / 50%   (tiny host, gateway needs headroom)
      2-4 GB → 50% / 40%  (paper runtime baseline)
      >4 GB  → 40% / 33%  (workstation, leave room for other services)

    Falls back to conservative fixed values if psutil is unavailable.
    """
    try:
        import psutil
        total = psutil.virtual_memory().total  # bytes
        total_mb = total // (1024 * 1024)
        if total_mb <= 2048:
            max_mb = int(total_mb * 0.60)
            high_mb = int(total_mb * 0.50)
        elif total_mb <= 4096:
            max_mb = int(total_mb * 0.50)
            high_mb = int(total_mb * 0.40)
        else:
            max_mb = int(total_mb * 0.40)
            high_mb = int(total_mb * 0.33)
        return f"{max_mb}M", f"{high_mb}M"
    except Exception:
        return "1500M", "1200M"


def _build_systemd_unit() -> str:
    command = shlex.join(_managed._runner_command(no_browser=True))
    mem_max, mem_high = _gateway_memory_limits()
    return (
        "[Unit]\nDescription=Aletheia Gateway user service\nAfter=network-online.target\n\n"
        "[Service]\nType=notify\n"
        f"WorkingDirectory={_PROJECT_ROOT}\nExecStart={command}\n"
        "Restart=on-failure\nRestartSec=60\nTimeoutStartSec=120\n"
        f"MemoryMax={mem_max}\nMemoryHigh={mem_high}\n"
        "StandardOutput=journal\nStandardError=journal\n\n"
        "[Install]\nWantedBy=default.target\n"
    )


def _systemd_unit_valid(actual: str, expected: str) -> bool:
    if actual == expected:
        return True
    prefix = "ExecStart="
    actual_exec = [line for line in actual.splitlines() if line.startswith(prefix)]
    expected_exec = [line for line in expected.splitlines() if line.startswith(prefix)]
    if len(actual_exec) != 1 or len(expected_exec) != 1:
        return False
    # Normalize MemoryMax/MemoryHigh too — these vary by machine RAM
    def _normalize(text: str, exec_line: str) -> str:
        normalized = text.replace(exec_line, f"{prefix}<runner>", 1)
        for mem_prefix in ("MemoryMax=", "MemoryHigh="):
            lines = [ln for ln in normalized.splitlines() if ln.startswith(mem_prefix)]
            if lines:
                normalized = normalized.replace(lines[0], f"{mem_prefix}<auto>", 1)
        return normalized
    if _normalize(actual, actual_exec[0]) != _normalize(expected, expected_exec[0]):
        return False
    try:
        return _runner_commands_match(
            shlex.split(actual_exec[0][len(prefix):]),
            shlex.split(expected_exec[0][len(prefix):]),
        )
    except ValueError:
        return False


def _systemctl(args: list[str]):
    return subprocess.run(
        ["systemctl", "--user", *args], capture_output=True, text=True,
        errors="replace", timeout=30,
    )


def _status_linux() -> dict:
    path = _systemd_unit_path()
    if not path.exists():
        return _result(backend="systemd", owned=True, config_valid=True)
    try:
        valid = _systemd_unit_valid(
            path.read_text(encoding="utf-8"), _build_systemd_unit()
        )
    except OSError:
        valid = False
    try:
        enabled_proc = _systemctl(["is-enabled", SYSTEMD_UNIT])
        enabled = enabled_proc.returncode == 0 and enabled_proc.stdout.strip() == "enabled"
        show = _systemctl(["show", SYSTEMD_UNIT, "--property=ActiveState,MainPID"])
        fields = dict(line.split("=", 1) for line in show.stdout.splitlines() if "=" in line)
        running = fields.get("ActiveState") in {"active", "activating"}
        pid = int(fields.get("MainPID", "0") or 0) or None
    except FileNotFoundError:
        return _result(
            supported=False, backend="systemd", installed=True, enabled=None,
            owned=valid, config_valid=valid, reason="systemd_unavailable",
        )
    except Exception as exc:
        return _result(
            backend="systemd", installed=True, enabled=None, owned=valid,
            config_valid=valid, reason="systemd_error", detail=type(exc).__name__,
        )
    return _result(
        backend="systemd", installed=True, enabled=enabled, running=running, pid=pid,
        owned=valid, config_valid=valid, recovery_scope="failure" if enabled else "none",
        **({"reason": "config_drift"} if not valid else {}),
    )


def _install_linux() -> dict:
    current = _status_linux()
    if current.get("installed") and current.get("owned") is not True:
        return {**current, "ok": False, "reason": "ownership_conflict"}
    path = _systemd_unit_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(_build_systemd_unit(), encoding="utf-8")
        os.replace(temp, path)
        proc = _systemctl(["daemon-reload"])
    except FileNotFoundError:
        return _result(supported=False, backend="systemd", reason="systemd_unavailable", ok=False)
    except Exception as exc:
        return {**current, "ok": False, "reason": "systemd_error", "detail": type(exc).__name__}
    if proc.returncode != 0:
        return {**current, "ok": False, "reason": "systemd_error", "detail": (proc.stderr or "")[:200]}
    return _result(backend="systemd", installed=True, owned=True, config_valid=True, ok=True)


def _change_linux(enabled: bool) -> dict:
    current = _status_linux()
    if not current.get("installed"):
        installed = _install_linux()
        if not installed.get("ok"):
            return installed
    elif current.get("owned") is not True:
        return {**current, "ok": False, "reason": "ownership_conflict"}
    try:
        proc = _systemctl(["enable" if enabled else "disable", SYSTEMD_UNIT])
    except Exception as exc:
        return {**current, "ok": False, "reason": "systemd_error", "detail": type(exc).__name__}
    if proc.returncode != 0:
        return {**current, "ok": False, "reason": "systemd_error", "detail": (proc.stderr or "")[:200]}
    return {**_status_linux(), "enabled": enabled, "ok": True}


def _run_linux(action: str) -> dict:
    current = _status_linux()
    if not current.get("installed"):
        return {**current, "ok": action == "stop", **({"reason": "not_installed"} if action == "start" else {})}
    if current.get("owned") is not True:
        return {**current, "ok": False, "reason": "ownership_conflict"}
    try:
        proc = _systemctl([action, SYSTEMD_UNIT])
    except Exception as exc:
        return {**current, "ok": False, "reason": "systemd_error", "detail": type(exc).__name__}
    if proc.returncode != 0:
        return {**current, "ok": False, "reason": "systemd_error", "detail": (proc.stderr or "")[:200]}
    return {**current, "running": action == "start", "ok": True}


def _uninstall_linux() -> dict:
    current = _status_linux()
    if not current.get("installed"):
        return {**current, "ok": True}
    if current.get("owned") is not True:
        return {**current, "ok": False, "reason": "ownership_conflict"}
    disabled = _change_linux(False)
    if not disabled.get("ok"):
        return disabled
    try:
        _systemd_unit_path().unlink(missing_ok=True)
        _systemctl(["daemon-reload"])
        _systemctl(["reset-failed", SYSTEMD_UNIT])
    except Exception as exc:
        return {**current, "ok": False, "reason": "systemd_error", "detail": type(exc).__name__}
    return _result(backend="systemd", owned=True, config_valid=True, ok=True)


def status() -> dict:
    if _platform() == "Windows":
        return _status_windows()
    if _platform() == "Darwin":
        return _status_macos()
    if _platform() == "Linux":
        return _status_linux()
    return _result(supported=False, reason=f"unsupported_platform:{_platform().lower()}")


def install(*, source: str = "cli") -> dict:
    current = status()
    if current.get("installed") and current.get("owned") is not True:
        return {**current, "ok": False, "reason": "ownership_conflict"}
    if _platform() == "Windows":
        result = _create_windows(enabled=False)
    elif _platform() == "Darwin":
        result = _install_macos()
    elif _platform() == "Linux":
        result = _install_linux()
    else:
        result = {**current, "ok": False}
    if result.get("ok"):
        _audit("gateway_service_installed", "Persistent session service installed (disabled).", source=source)
    return result


def enable(*, source: str = "cli") -> dict:
    if _platform() == "Windows":
        result = _change_windows(True)
    elif _platform() == "Darwin":
        result = _enable_macos(True)
    elif _platform() == "Linux":
        result = _change_linux(True)
    else:
        result = {**status(), "ok": False}
    if result.get("ok"):
        _audit("gateway_service_enabled", "Session service automatic activation enabled.", source=source)
    return result


def disable(*, source: str = "cli") -> dict:
    if _platform() == "Windows":
        result = _change_windows(False)
    elif _platform() == "Darwin":
        result = _enable_macos(False)
    elif _platform() == "Linux":
        result = _change_linux(False)
    else:
        result = {**status(), "ok": False}
    if result.get("ok"):
        _audit("gateway_service_disabled", "Session service automatic activation disabled.", source=source)
    return result


def start() -> dict:
    if _platform() == "Windows":
        return _start_windows()
    if _platform() == "Darwin":
        return _start_macos()
    if _platform() == "Linux":
        return _run_linux("start")
    return {**status(), "ok": False}


def stop() -> dict:
    if _platform() == "Windows":
        return _stop_windows()
    if _platform() == "Darwin":
        return _stop_macos()
    if _platform() == "Linux":
        return _run_linux("stop")
    return {**status(), "ok": False}


def uninstall(*, source: str = "cli") -> dict:
    if _platform() == "Windows":
        result = _uninstall_windows()
    elif _platform() == "Darwin":
        result = _uninstall_macos()
    elif _platform() == "Linux":
        result = _uninstall_linux()
    else:
        result = {**status(), "ok": False}
    if result.get("ok"):
        _audit("gateway_service_uninstalled", "Persistent session service uninstalled; runtime data preserved.", source=source)
    return result
