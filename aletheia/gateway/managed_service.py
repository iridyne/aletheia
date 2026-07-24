"""On-demand, user-level ownership of the gateway supervisor.

This is deliberately separate from :mod:`aletheia.gateway.autostart`: an
on-demand managed service is created by ``gateway start`` and removed by
``gateway stop``.  It has no login trigger, interval, KeepAlive/Restart policy,
or credentials.  The operating system owns the foreground supervisor while it
is running, so closing the terminal that issued the CLI command cannot end the
gateway's service lifetime.
"""

from __future__ import annotations

import getpass
import os
import platform
import plistlib
import re
import subprocess
import sys
import tempfile
from pathlib import Path, PureWindowsPath

from aletheia.config.loader import _PROJECT_ROOT, get_data_dir
from aletheia.gateway.pidfile import _no_window_kwargs, _resolve_real_python, is_running
from aletheia.packaged_process import gateway_runner_command, is_frozen_runtime

MACOS_LABEL = "com.aletheia.trading-os.gateway.session"
SYSTEMD_UNIT = "aletheia-gateway-session.service"
WINDOWS_TASK_NAME = "Aletheia Trading OS Gateway Session"
_XML_NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"


def _is_macos() -> bool:
    return platform.system() == "Darwin"


def _is_linux() -> bool:
    return platform.system() == "Linux"


def _is_windows() -> bool:
    return platform.system() == "Windows"


def _service_dir() -> Path:
    path = get_data_dir() / "service"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _macos_plist_path() -> Path:
    return _service_dir() / f"{MACOS_LABEL}.plist"


def _macos_domain() -> str:
    return f"gui/{os.getuid()}"


def _macos_target() -> str:
    return f"{_macos_domain()}/{MACOS_LABEL}"


def _runner_command(*, no_browser: bool) -> list[str]:
    script = Path(__file__).resolve().parent.parent / "_gateway_runner.py"
    real_python, _ = _resolve_real_python()
    return gateway_runner_command(
        script=script,
        real_python=real_python,
        no_browser=no_browser,
    )


def _xml_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _current_user() -> str:
    domain = os.environ.get("USERDOMAIN")
    user = os.environ.get("USERNAME") or getpass.getuser()
    return f"{domain}\\{user}" if domain else user


def _build_macos_plist(*, no_browser: bool) -> dict:
    """Return a launch-on-demand job definition with no restart trigger."""
    log_dir = get_data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return {
        "Label": MACOS_LABEL,
        "ProgramArguments": _runner_command(no_browser=no_browser),
        "WorkingDirectory": str(_PROJECT_ROOT),
        "ProcessType": "Background",
        "StandardOutPath": str(log_dir / "gateway-service.log"),
        "StandardErrorPath": str(log_dir / "gateway-service-errors.log"),
    }


def _validate_macos_plist(payload: dict, *, no_browser: bool) -> bool:
    command = _runner_command(no_browser=no_browser)
    return (
        payload.get("Label") == MACOS_LABEL
        and payload.get("ProgramArguments") == command
        and payload.get("WorkingDirectory") == str(_PROJECT_ROOT)
        and "KeepAlive" not in payload
        and "RunAtLoad" not in payload
        and "StartInterval" not in payload
        and "EnvironmentVariables" not in payload
    )


def _write_macos_plist(*, no_browser: bool) -> Path:
    payload = _build_macos_plist(no_browser=no_browser)
    if not _validate_macos_plist(payload, no_browser=no_browser):
        raise ValueError("invalid on-demand launchd plist")
    data = plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=False)
    if not _validate_macos_plist(plistlib.loads(data), no_browser=no_browser):
        raise ValueError("invalid serialized on-demand launchd plist")
    target = _macos_plist_path()
    temp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=target.parent, prefix=f".{MACOS_LABEL}.", suffix=".tmp", delete=False,
        ) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = handle.name
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, target)
        temp_path = None
        return target
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def _launchctl(args: list[str]):
    return subprocess.run(
        ["launchctl", *args], capture_output=True, text=True, errors="replace", timeout=30,
    )


def _is_not_found(detail: str) -> bool:
    low = (detail or "").lower()
    return any(marker in low for marker in (
        "not found", "no such process", "could not find service",
        "does not exist", "cannot find the file",
    ))


def _parse_launchctl(output: str) -> dict:
    result: dict = {}
    for line in (output or "").splitlines():
        state = re.match(r"^\s*state\s*=\s*(.+?)\s*$", line, flags=re.IGNORECASE)
        pid = re.match(r"^\s*pid\s*=\s*(\d+)\s*$", line, flags=re.IGNORECASE)
        if state:
            result["state"] = state.group(1).strip()
        if pid:
            result["pid"] = int(pid.group(1))
    return result


def _status_macos() -> dict:
    path = _macos_plist_path()
    try:
        proc = _launchctl(["print", _macos_target()])
    except FileNotFoundError:
        return {"supported": False, "managed": False, "platform": "launchd", "reason": "launchctl_unavailable"}
    except Exception as exc:
        return {"supported": True, "managed": None, "platform": "launchd", "reason": "launchctl_error", "detail": type(exc).__name__}
    if proc.returncode != 0:
        if _is_not_found(proc.stderr):
            return {"supported": True, "managed": False, "platform": "launchd", "registered": path.exists()}
        return {"supported": True, "managed": None, "platform": "launchd", "reason": "launchctl_error", "detail": (proc.stderr or "").strip()[:200]}
    return {
        "supported": True,
        "managed": True,
        "platform": "launchd",
        "registered": path.exists(),
        "running": is_running(),
        **_parse_launchctl(proc.stdout),
    }


def _is_bootstrap_eio(stderr: str) -> bool:
    """Return True if stderr is the transient EIO race from a recent bootout."""
    return "bootstrap failed: 5" in (stderr or "").lower()


def _start_macos(*, no_browser: bool) -> dict:
    import time

    path = None
    try:
        path = _write_macos_plist(no_browser=no_browser)
        old = _launchctl(["bootout", _macos_target()])
        if old.returncode != 0 and not _is_not_found(old.stderr):
            path.unlink(missing_ok=True)
            return {"supported": True, "managed": None, "ok": False, "platform": "launchd", "reason": "launchctl_error", "detail": (old.stderr or "").strip()[:200]}
        # launchctl bootout is async on macOS: launchd may still be cleaning up
        # the prior service entry when bootstrap is called immediately after a
        # restart. Retry up to 3 times on the transient EIO (error 5) before
        # giving up — each retry waits 1 s for the prior bootout to settle.
        _bootstrap_attempts = 3
        loaded = None
        for _attempt in range(_bootstrap_attempts):
            loaded = _launchctl(["bootstrap", _macos_domain(), str(path)])
            if loaded.returncode == 0:
                break
            if _attempt < _bootstrap_attempts - 1 and _is_bootstrap_eio(loaded.stderr):
                time.sleep(1.0)
            else:
                break
        if loaded.returncode != 0:
            path.unlink(missing_ok=True)
            return {"supported": True, "managed": False, "ok": False, "platform": "launchd", "reason": "launchctl_error", "detail": (loaded.stderr or "").strip()[:200]}
        started = _launchctl(["kickstart", _macos_target()])
        if started.returncode != 0:
            return {"supported": True, "managed": True, "ok": False, "platform": "launchd", "reason": "launchctl_error", "detail": (started.stderr or "").strip()[:200]}
    except FileNotFoundError:
        if path is not None:
            path.unlink(missing_ok=True)
        return {"supported": False, "managed": False, "ok": False, "platform": "launchd", "reason": "launchctl_unavailable"}
    except Exception as exc:
        if path is not None:
            path.unlink(missing_ok=True)
        return {"supported": True, "managed": None, "ok": False, "platform": "launchd", "reason": "launchctl_error", "detail": type(exc).__name__}
    return {"supported": True, "managed": True, "ok": True, "platform": "launchd"}


def _stop_macos() -> dict:
    path = _macos_plist_path()
    try:
        proc = _launchctl(["bootout", _macos_target()])
    except FileNotFoundError:
        return {"supported": False, "managed": False, "ok": False, "platform": "launchd", "reason": "launchctl_unavailable"}
    except Exception as exc:
        return {"supported": True, "managed": None, "ok": False, "platform": "launchd", "reason": "launchctl_error", "detail": type(exc).__name__}
    if proc.returncode != 0 and not _is_not_found(proc.stderr):
        return {"supported": True, "managed": None, "ok": False, "platform": "launchd", "reason": "launchctl_error", "detail": (proc.stderr or "").strip()[:200]}
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        return {"supported": True, "managed": False, "ok": False, "platform": "launchd", "reason": "plist_remove_error", "detail": type(exc).__name__}
    return {"supported": True, "managed": False, "ok": True, "platform": "launchd"}


def _systemctl(args: list[str]):
    return subprocess.run(
        ["systemctl", "--user", *args], capture_output=True, text=True, errors="replace", timeout=30,
    )


def _status_linux() -> dict:
    try:
        proc = _systemctl(["show", SYSTEMD_UNIT, "--property=LoadState,ActiveState,MainPID"])
    except FileNotFoundError:
        return {"supported": False, "managed": False, "platform": "systemd", "reason": "systemd_unavailable"}
    except Exception as exc:
        return {"supported": True, "managed": None, "platform": "systemd", "reason": "systemd_error", "detail": type(exc).__name__}
    fields = dict(line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line)
    if proc.returncode != 0 or fields.get("LoadState") == "not-found":
        return {"supported": True, "managed": False, "platform": "systemd"}
    pid = int(fields.get("MainPID", "0") or 0)
    return {
        "supported": True,
        "managed": True,
        "platform": "systemd",
        "running": fields.get("ActiveState") in {"active", "activating"},
        "pid": pid or None,
        "state": fields.get("ActiveState"),
    }


def _start_linux(*, no_browser: bool) -> dict:
    command = [
        "systemd-run", "--user", "--unit", SYSTEMD_UNIT, "--service-type=exec",
        "--property=Restart=no", "--property=StandardInput=null",
        "--property=StandardOutput=journal", "--property=StandardError=journal", "--quiet",
        *_runner_command(no_browser=no_browser),
    ]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, errors="replace", timeout=30)
    except FileNotFoundError:
        return {"supported": False, "managed": False, "ok": False, "platform": "systemd", "reason": "systemd_unavailable"}
    except Exception as exc:
        return {"supported": True, "managed": None, "ok": False, "platform": "systemd", "reason": "systemd_error", "detail": type(exc).__name__}
    if proc.returncode != 0:
        return {"supported": True, "managed": False, "ok": False, "platform": "systemd", "reason": "systemd_error", "detail": (proc.stderr or "").strip()[:200]}
    return {"supported": True, "managed": True, "ok": True, "platform": "systemd"}


def _stop_linux() -> dict:
    try:
        stopped = _systemctl(["stop", SYSTEMD_UNIT])
        if stopped.returncode != 0 and "not loaded" not in (stopped.stderr or "").lower():
            return {"supported": True, "managed": None, "ok": False, "platform": "systemd", "reason": "systemd_error", "detail": (stopped.stderr or "").strip()[:200]}
        _systemctl(["reset-failed", SYSTEMD_UNIT])
    except FileNotFoundError:
        return {"supported": False, "managed": False, "ok": False, "platform": "systemd", "reason": "systemd_unavailable"}
    except Exception as exc:
        return {"supported": True, "managed": None, "ok": False, "platform": "systemd", "reason": "systemd_error", "detail": type(exc).__name__}
    return {"supported": True, "managed": False, "ok": True, "platform": "systemd"}


def _windows_python() -> str:
    exe = Path(sys.executable)
    venv_dir = exe.resolve().parent.parent
    cfg = venv_dir / "pyvenv.cfg"
    if cfg.exists():
        try:
            for line in cfg.read_text(encoding="utf-8").splitlines():
                if line.lower().startswith("home"):
                    _, _, home = line.partition("=")
                    candidate = Path(home.strip()) / "pythonw.exe"
                    if candidate.exists():
                        return str(candidate)
        except OSError:
            pass
    candidate = exe.with_name("pythonw.exe")
    return str(candidate if candidate.exists() else exe)


def _windows_program_and_arguments(*, no_browser: bool) -> tuple[str, str]:
    command = _runner_command(no_browser=no_browser)
    if is_frozen_runtime():
        return command[0], subprocess.list2cmdline(command[1:])
    exe = Path(sys.executable)
    path_type = PureWindowsPath if "\\" in str(exe) or (len(str(exe)) > 1 and str(exe)[1] == ":") else Path
    venv_dir = path_type(str(exe)).parent.parent
    venv_cfg = Path(str(venv_dir / "pyvenv.cfg"))
    if venv_cfg.exists() and str(path_type(_windows_python())) != str(path_type(str(exe))):
        source = (
            "import os,runpy,site,sys;"
            f"site.addsitedir({str(venv_dir / 'Lib' / 'site-packages')!r});"
            f"os.chdir({str(_PROJECT_ROOT)!r});"
            f"sys.argv={[command[1], *command[2:]]!r};"
            f"runpy.run_path({command[1]!r}, run_name='__main__')"
        )
        return _windows_python(), subprocess.list2cmdline(["-c", source])
    return command[0], subprocess.list2cmdline(command[1:])


def _build_windows_task_xml(*, no_browser: bool) -> str:
    program, arguments = _windows_program_and_arguments(no_browser=no_browser)
    user = _xml_escape(_current_user())
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        f'<Task version="1.2" xmlns="{_XML_NS}">\n'
        "  <RegistrationInfo><Description>Aletheia gateway on-demand user service.</Description></RegistrationInfo>\n"
        "  <Principals><Principal id=\"Author\">\n"
        f"    <UserId>{user}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel>\n"
        "  </Principal></Principals>\n"
        "  <Settings>\n"
        "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><AllowStartOnDemand>true</AllowStartOnDemand>\n"
        "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n"
        "    <StartWhenAvailable>false</StartWhenAvailable><ExecutionTimeLimit>PT0S</ExecutionTimeLimit><Enabled>true</Enabled><Hidden>true</Hidden>\n"
        "  </Settings>\n"
        "  <Actions Context=\"Author\"><Exec>\n"
        f"    <Command>{_xml_escape(program)}</Command><Arguments>{_xml_escape(arguments)}</Arguments><WorkingDirectory>{_xml_escape(str(_PROJECT_ROOT))}</WorkingDirectory>\n"
        "  </Exec></Actions>\n"
        "</Task>\n"
    )


def _status_windows() -> dict:
    try:
        proc = subprocess.run(
            ["schtasks", "/Query", "/TN", WINDOWS_TASK_NAME, "/XML"], capture_output=True, timeout=30,
            text=True, errors="replace", **_no_window_kwargs(),
        )
    except FileNotFoundError:
        return {"supported": False, "managed": False, "platform": "task_scheduler", "reason": "schtasks_unavailable"}
    except Exception as exc:
        return {"supported": True, "managed": None, "platform": "task_scheduler", "reason": "schtasks_error", "detail": type(exc).__name__}
    if proc.returncode != 0:
        return {"supported": True, "managed": False, "platform": "task_scheduler"}
    return {"supported": True, "managed": True, "platform": "task_scheduler", "running": is_running()}


def _start_windows(*, no_browser: bool) -> dict:
    xml = _build_windows_task_xml(no_browser=no_browser)
    temp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".xml", delete=False) as handle:
            handle.write(xml.encode("utf-16"))
            temp_path = handle.name
        created = subprocess.run(
            ["schtasks", "/Create", "/TN", WINDOWS_TASK_NAME, "/XML", temp_path, "/F"], capture_output=True, timeout=30,
            text=True, errors="replace", **_no_window_kwargs(),
        )
        if created.returncode != 0:
            return {"supported": True, "managed": False, "ok": False, "platform": "task_scheduler", "reason": "schtasks_error", "detail": (created.stderr or "").strip()[:200]}
        started = subprocess.run(
            ["schtasks", "/Run", "/TN", WINDOWS_TASK_NAME], capture_output=True, timeout=30,
            text=True, errors="replace", **_no_window_kwargs(),
        )
        if started.returncode != 0:
            return {"supported": True, "managed": True, "ok": False, "platform": "task_scheduler", "reason": "schtasks_error", "detail": (started.stderr or "").strip()[:200]}
    except FileNotFoundError:
        return {"supported": False, "managed": False, "ok": False, "platform": "task_scheduler", "reason": "schtasks_unavailable"}
    except Exception as exc:
        return {"supported": True, "managed": None, "ok": False, "platform": "task_scheduler", "reason": "schtasks_error", "detail": type(exc).__name__}
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
    return {"supported": True, "managed": True, "ok": True, "platform": "task_scheduler"}


def _stop_windows() -> dict:
    try:
        ended = subprocess.run(
            ["schtasks", "/End", "/TN", WINDOWS_TASK_NAME], capture_output=True, timeout=30,
            text=True, errors="replace", **_no_window_kwargs(),
        )
        deleted = subprocess.run(
            ["schtasks", "/Delete", "/TN", WINDOWS_TASK_NAME, "/F"], capture_output=True, timeout=30,
            text=True, errors="replace", **_no_window_kwargs(),
        )
    except FileNotFoundError:
        return {"supported": False, "managed": False, "ok": False, "platform": "task_scheduler", "reason": "schtasks_unavailable"}
    except Exception as exc:
        return {"supported": True, "managed": None, "ok": False, "platform": "task_scheduler", "reason": "schtasks_error", "detail": type(exc).__name__}
    if deleted.returncode != 0 and not _is_not_found(deleted.stderr):
        return {"supported": True, "managed": None, "ok": False, "platform": "task_scheduler", "reason": "schtasks_error", "detail": (deleted.stderr or "").strip()[:200]}
    return {"supported": True, "managed": False, "ok": True, "platform": "task_scheduler", "end_requested": ended.returncode == 0}


def status() -> dict:
    """Return the live ownership state without claiming a platform fallback."""
    if _is_macos():
        return _status_macos()
    if _is_linux():
        return _status_linux()
    if _is_windows():
        return _status_windows()
    return {"supported": False, "managed": False, "reason": f"unsupported_platform:{platform.system().lower()}"}


def start(*, no_browser: bool) -> dict:
    """Create and start exactly one non-restarting user service."""
    if _is_macos():
        return _start_macos(no_browser=no_browser)
    if _is_linux():
        return _start_linux(no_browser=no_browser)
    if _is_windows():
        return _start_windows(no_browser=no_browser)
    return {"supported": False, "managed": False, "ok": False, "reason": f"unsupported_platform:{platform.system().lower()}"}


def stop() -> dict:
    """Stop and unregister the on-demand user service; never enable a watchdog."""
    if _is_macos():
        return _stop_macos()
    if _is_linux():
        return _stop_linux()
    if _is_windows():
        return _stop_windows()
    return {"supported": False, "managed": False, "ok": False, "reason": f"unsupported_platform:{platform.system().lower()}"}
