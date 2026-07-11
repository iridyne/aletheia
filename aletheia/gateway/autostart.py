"""OS-level autostart + supervisor watchdog for Windows and macOS.

The gateway already detaches a background process and a supervisor respawns the
inner server on crash — but nothing restarts the supervisor itself after a
reboot, power loss, OOM kill, or manual kill. This module closes that gap on
Windows Task Scheduler and a macOS user LaunchAgent both run the SAME gated
`gateway start` a human runs:

  - ONLOGON trigger  → starts the gateway when the user logs on (boot autostart).
  - Repeat every 1m  → because `do_start` is idempotent (no-op when already
    running), each tick resurrects a dead supervisor. This is the OS-level
    watchdog over the supervisor.

Governance:
  - Human-triggered only, never agent-callable (not in the tool registry).
  - Runs `gateway start --no-browser` with RunLevel=LeastPrivilege — live stays
    disabled/gated exactly as in a manual start. No risk/credential/live change.
  - UI honesty: status() always shells a live `schtasks /Query`; we never persist
    an "enabled" flag that could lie if the user deletes the task via the GUI.
  - Never raises into callers; degrades to honest dicts on every error.
"""

import getpass
import os
import platform
import plistlib
import re
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path, PureWindowsPath

from aletheia.config.loader import _PROJECT_ROOT
from aletheia.files.jsonl import write_audit
from aletheia.gateway.pidfile import _no_window_kwargs

TASK_NAME = "Aletheia Trading OS Gateway"
LAUNCHD_LABEL = "com.aletheia.trading-os.gateway"
LAUNCHD_INTERVAL_SEC = 60

# How often the task re-checks the gateway (and resurrects a dead supervisor).
# Windows Task Scheduler enforces a minimum repetition interval of PT1M;
# PT30S is rejected with "Interval value out of range" on registration.
WATCHDOG_INTERVAL = "PT1M"

_XML_NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"


def _exists(path) -> bool:
    """Indirection over Path.exists so tests can stub filesystem checks."""
    try:
        return Path(path).exists()
    except OSError:
        return False


def _python_for_task() -> str:
    """Return the real GUI-subsystem pythonw.exe for the task, bypassing shims.

    uv-created venv shims are Console-subsystem executables — Task Scheduler
    launching them allocates a console (Win11 Windows Terminal flash). We resolve
    through pyvenv.cfg to the base installation's pythonw.exe (GUI subsystem,
    PE subsystem=2), which never allocates a console.
    """
    exe = sys.executable
    venv_dir = Path(exe).resolve().parent.parent
    pyvenv_cfg = venv_dir / "pyvenv.cfg"

    if _exists(pyvenv_cfg):
        try:
            for line in Path(pyvenv_cfg).read_text(encoding="utf-8").splitlines():
                if line.lower().startswith("home"):
                    _, _, val = line.partition("=")
                    home = val.strip()
                    real_pyw = Path(home) / "pythonw.exe"
                    if _exists(real_pyw):
                        return str(real_pyw)
                    break
        except (OSError, ValueError):
            pass

    # Fallback: try sibling pythonw.exe (non-uv venvs or system install).
    try:
        pyw = Path(exe).with_name("pythonw.exe")
        if _exists(pyw):
            return str(pyw)
    except (ValueError, OSError):
        pass
    return exe


def _current_user() -> str:
    """Best-effort DOMAIN\\USERNAME for the task principal/trigger."""
    domain = os.environ.get("USERDOMAIN")
    user = os.environ.get("USERNAME") or getpass.getuser()
    return f"{domain}\\{user}" if domain else user


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def _task_arguments() -> str:
    """Build the task arguments so the base pythonw.exe can see the venv's deps.

    The task runs the base GUI-subsystem pythonw.exe (no Win11 console flash;
    the venv's own pythonw is a Console-subsystem trampoline that WOULD flash).
    But the base interpreter has no venv context, so we must make the venv's
    site-packages importable from inside the -c bootstrap.

    Mechanism: `site.addsitedir(<venv site-packages>)` — it takes effect the
    instant it runs and also processes any `.pth` files, so the subsequent
    `import aletheia.main` finds typer/fastapi/etc.
    """
    exe = sys.executable
    # Tests may emulate Windows while executing on Linux. Choose the path flavour
    # from the executable value so a Windows venv is never reinterpreted as a
    # POSIX relative path.
    path_type = PureWindowsPath if "\\" in exe or (len(exe) > 1 and exe[1] == ":") else Path
    venv_dir = path_type(exe).parent.parent
    pyvenv_cfg = venv_dir / "pyvenv.cfg"
    real_python = _python_for_task()

    # If we resolved to a real python (different from venv shim), need bootstrap.
    if _exists(pyvenv_cfg) and str(path_type(exe)) != str(path_type(real_python)):
        venv_site = str(venv_dir / "Lib" / "site-packages")
        project_root = str(_PROJECT_ROOT)
        # Use repr() for path strings to handle single quotes, backslashes, etc.
        return (
            f'-c "import os,sys,site;'
            f"site.addsitedir({repr(venv_site)});"
            f"os.chdir({repr(project_root)});"
            f"from aletheia.main import app;"
            f"sys.argv=['task','gateway','start','--no-browser'];"
            f'app()"'
        )
    return "-m aletheia.main gateway start --no-browser"


def _build_task_xml() -> str:
    """Build the Task Scheduler definition XML.

    LogonTrigger + indefinite 2-minute repetition (omitting <Duration> with
    StopAtDurationEnd=false means repeat forever). InteractiveToken + the omission
    of /RU SYSTEM keeps creation non-elevated; LeastPrivilege keeps the started
    gateway unprivileged. MultipleInstancesPolicy=IgnoreNew guards the only
    realistic double-fire (logon coinciding with the first repetition tick).
    """
    user = _xml_escape(_current_user())
    command = _xml_escape(_python_for_task())
    arguments = _xml_escape(_task_arguments())
    workdir = _xml_escape(str(_PROJECT_ROOT))
    start_boundary = (datetime.now() + timedelta(minutes=1)).replace(microsecond=0).isoformat()
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        f'<Task version="1.2" xmlns="{_XML_NS}">\n'
        "  <RegistrationInfo>\n"
        f"    <Author>{user}</Author>\n"
        "    <Description>Aletheia Trading OS gateway autostart (on logon) plus a "
        "1-minute watchdog that restarts the supervisor if it dies.</Description>\n"
        "  </RegistrationInfo>\n"
        "  <Triggers>\n"
        "    <LogonTrigger>\n"
        "      <Enabled>true</Enabled>\n"
        f"      <UserId>{user}</UserId>\n"
        "      <Repetition>\n"
        f"        <Interval>{WATCHDOG_INTERVAL}</Interval>\n"
        "        <StopAtDurationEnd>false</StopAtDurationEnd>\n"
        "      </Repetition>\n"
        "    </LogonTrigger>\n"
        "    <TimeTrigger>\n"
        "      <Enabled>true</Enabled>\n"
        f"      <StartBoundary>{start_boundary}</StartBoundary>\n"
        "      <Repetition>\n"
        f"        <Interval>{WATCHDOG_INTERVAL}</Interval>\n"
        "        <StopAtDurationEnd>false</StopAtDurationEnd>\n"
        "      </Repetition>\n"
        "    </TimeTrigger>\n"
        "  </Triggers>\n"
        "  <Principals>\n"
        '    <Principal id="Author">\n'
        f"      <UserId>{user}</UserId>\n"
        "      <LogonType>InteractiveToken</LogonType>\n"
        "      <RunLevel>LeastPrivilege</RunLevel>\n"
        "    </Principal>\n"
        "  </Principals>\n"
        "  <Settings>\n"
        "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n"
        "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n"
        "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n"
        "    <StartWhenAvailable>true</StartWhenAvailable>\n"
        "    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>\n"
        "    <Enabled>true</Enabled>\n"
        "    <Hidden>true</Hidden>\n"
        "  </Settings>\n"
        '  <Actions Context="Author">\n'
        "    <Exec>\n"
        f"      <Command>{command}</Command>\n"
        f"      <Arguments>{arguments}</Arguments>\n"
        f"      <WorkingDirectory>{workdir}</WorkingDirectory>\n"
        "    </Exec>\n"
        "  </Actions>\n"
        "</Task>\n"
    )


def _is_windows() -> bool:
    return platform.system() == "Windows"


def _is_macos() -> bool:
    return platform.system() == "Darwin"


def is_supported_platform() -> bool:
    return _is_windows() or _is_macos()


def _not_supported(reason: str = "unsupported_platform") -> dict:
    return {"supported": False, "enabled": False, "ok": False, "reason": reason}


def _launch_agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def _launchd_plist_path() -> Path:
    return _launch_agents_dir() / f"{LAUNCHD_LABEL}.plist"


def _launchd_domain() -> str:
    return f"gui/{os.getuid()}"


def _launchd_target() -> str:
    return f"{_launchd_domain()}/{LAUNCHD_LABEL}"


def _build_launchd_plist() -> dict:
    """Build a secret-free periodic LaunchAgent definition.

    `gateway start` is intentionally short-lived and idempotent, so StartInterval
    is the correct watchdog primitive. KeepAlive would relaunch the successful
    command in a tight loop. AbandonProcessGroup preserves the detached supervisor
    spawned by the gated lifecycle command after the watchdog invocation exits.
    """
    log_dir = _PROJECT_ROOT / "data" / "logs"
    return {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": [
            sys.executable,
            "-m",
            "aletheia.main",
            "gateway",
            "start",
            "--no-browser",
        ],
        "WorkingDirectory": str(_PROJECT_ROOT),
        "RunAtLoad": True,
        "StartInterval": LAUNCHD_INTERVAL_SEC,
        "AbandonProcessGroup": True,
        "ProcessType": "Background",
        "ThrottleInterval": 10,
        "StandardOutPath": str(log_dir / "launchd-watchdog.log"),
        "StandardErrorPath": str(log_dir / "launchd-watchdog-errors.log"),
    }


def _validate_launchd_plist(payload: dict) -> bool:
    expected_args = [
        sys.executable,
        "-m",
        "aletheia.main",
        "gateway",
        "start",
        "--no-browser",
    ]
    return (
        payload.get("Label") == LAUNCHD_LABEL
        and payload.get("ProgramArguments") == expected_args
        and payload.get("WorkingDirectory") == str(_PROJECT_ROOT)
        and payload.get("RunAtLoad") is True
        and payload.get("StartInterval") == LAUNCHD_INTERVAL_SEC
        and payload.get("AbandonProcessGroup") is True
        and "EnvironmentVariables" not in payload
        and "KeepAlive" not in payload
        and Path(sys.executable).is_absolute()
        and Path(_PROJECT_ROOT).is_absolute()
    )


def _write_launchd_plist() -> Path:
    """Atomically install a validated user LaunchAgent plist with mode 0644."""
    payload = _build_launchd_plist()
    if not _validate_launchd_plist(payload):
        raise ValueError("invalid launchd plist payload")
    data = plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=False)
    # Validate the serialized form too; a partial or malformed plist must never
    # replace the last working registration.
    if not _validate_launchd_plist(plistlib.loads(data)):
        raise ValueError("launchd plist serialization failed validation")

    target = _launchd_plist_path()
    (_PROJECT_ROOT / "data" / "logs").mkdir(parents=True, exist_ok=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=target.parent, prefix=f".{LAUNCHD_LABEL}.",
            suffix=".tmp", delete=False,
        ) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            tmp = handle.name
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
        tmp = None
        return target
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _launchctl(args: list[str]):
    return subprocess.run(
        ["launchctl", *args],
        capture_output=True,
        timeout=30,
        text=True,
        errors="replace",
    )


def _classify_launchctl_error(stderr: str) -> str:
    low = (stderr or "").lower()
    if any(marker in low for marker in (
        "could not find service", "service not found", "no such process", "not found",
    )):
        return "not_found"
    if any(marker in low for marker in (
        "operation not permitted", "permission denied", "not privileged", "access denied",
    )):
        return "access_denied"
    return "launchctl_error"


def _parse_launchctl_status(output: str) -> dict:
    """Parse stable `launchctl print` keys for a short-lived periodic job."""
    result: dict = {}
    patterns = {
        "status": (r"^\s*state\s*=\s*(.+?)\s*$", str),
        "runs": (r"^\s*runs\s*=\s*(\d+)\s*$", int),
        "last_result": (r"^\s*last exit code\s*=\s*(-?\d+)\s*$", str),
        "pid": (r"^\s*pid\s*=\s*(\d+)\s*$", int),
    }
    for line in (output or "").splitlines():
        for key, (pattern, cast) in patterns.items():
            match = re.match(pattern, line, flags=re.IGNORECASE)
            if match:
                result[key] = cast(match.group(1).strip())
                break
    runs = result.get("runs")
    last_result = result.get("last_result")
    if runs is None:
        result["primed"] = None
    elif runs < 1:
        result["primed"] = False
    elif last_result is None and result.get("status") == "running":
        result["primed"] = True
    else:
        result["primed"] = last_result == "0"
    return result


def _classify_stderr(stderr: str) -> str:
    low = (stderr or "").lower()
    if "access is denied" in low or "denied" in low or "拒绝" in stderr:
        return "access_denied"
    if ("cannot find" in low or "does not exist" in low or "no mapping" in low
            or "找不到" in stderr or "不存在" in stderr):
        return "not_found"
    return "schtasks_error"


def _schtasks_text_kwargs() -> dict:
    """Decode localized schtasks output without surfacing codec noise."""
    return {"text": True, "errors": "replace"}


def _verify_after_prime(timeout: float = 30.0) -> dict:
    """Wait up to timeout seconds for the gateway to come alive after priming.

    Checks pidfile existence + port liveness. Returns verification result dict.
    """
    from aletheia.config.loader import load_config
    from aletheia.gateway.pidfile import is_running, port_in_use

    cfg = load_config()
    port = cfg.app.dashboard_port
    host = cfg.app.bind_host
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        if is_running() and port_in_use(port, host):
            return {"verified": True}
        time.sleep(2.0)

    running = is_running()
    port_up = port_in_use(port, host)
    return {
        "verified": False,
        "verify_detail": f"pid_alive={running}, port_up={port_up} after {timeout}s",
    }


def _status_macos() -> dict:
    path = _launchd_plist_path()
    installed = path.exists()
    config_valid: bool | None = None
    if installed:
        try:
            config_valid = _validate_launchd_plist(plistlib.loads(path.read_bytes()))
        except (OSError, ValueError, plistlib.InvalidFileException):
            config_valid = False

    try:
        proc = _launchctl(["print", _launchd_target()])
    except FileNotFoundError:
        return {
            "supported": False,
            "enabled": False,
            "installed": installed,
            "task_name": LAUNCHD_LABEL,
            "platform": "launchd",
            "reason": "launchctl_unavailable",
        }
    except Exception as exc:
        return {
            "supported": True,
            "enabled": None,
            "installed": installed,
            "task_name": LAUNCHD_LABEL,
            "platform": "launchd",
            "reason": "launchctl_error",
            "detail": type(exc).__name__,
        }

    if proc.returncode != 0:
        reason = _classify_launchctl_error(proc.stderr)
        if reason == "not_found":
            return {
                "supported": True,
                "enabled": False,
                "installed": installed,
                "config_valid": config_valid,
                "task_name": LAUNCHD_LABEL,
                "platform": "launchd",
                "interval": f"{LAUNCHD_INTERVAL_SEC}s",
                "reason": "not_loaded" if installed else "not_installed",
            }
        return {
            "supported": True,
            "enabled": None,
            "installed": installed,
            "config_valid": config_valid,
            "task_name": LAUNCHD_LABEL,
            "platform": "launchd",
            "reason": reason,
            "detail": (proc.stderr or "").strip()[:200],
        }

    runtime = _parse_launchctl_status(proc.stdout)
    reason = None
    if not installed:
        reason = "plist_missing"
    elif config_valid is False:
        reason = "config_drift"
    return {
        "supported": True,
        "enabled": True,
        "installed": installed,
        "config_valid": config_valid,
        "task_name": LAUNCHD_LABEL,
        "platform": "launchd",
        "interval": f"{LAUNCHD_INTERVAL_SEC}s",
        "reason": reason,
        **runtime,
    }


def _enable_macos(source: str) -> dict:
    try:
        path = _write_launchd_plist()
    except Exception as exc:
        write_audit({
            "actor": "human", "source": source,
            "event_type": "autostart_enable_failed",
            "entity_type": "gateway_autostart", "entity_id": LAUNCHD_LABEL,
            "message": f"macOS autostart plist install failed: {type(exc).__name__}",
        })
        return {
            "supported": True, "enabled": False, "ok": False,
            "platform": "launchd", "task_name": LAUNCHD_LABEL,
            "reason": "plist_install_error", "detail": type(exc).__name__,
        }

    # Replace an already loaded definition so path/interpreter drift is repaired.
    try:
        old = _launchctl(["bootout", _launchd_target()])
        if old.returncode != 0:
            reason = _classify_launchctl_error(old.stderr)
            if reason == "access_denied":
                return {
                    "supported": True, "enabled": None, "ok": False,
                    "installed": True, "platform": "launchd",
                    "task_name": LAUNCHD_LABEL, "reason": reason,
                    "detail": (old.stderr or "").strip()[:200],
                }
    except FileNotFoundError:
        return {
            "supported": False, "enabled": False, "ok": False,
            "installed": True, "platform": "launchd",
            "task_name": LAUNCHD_LABEL, "reason": "launchctl_unavailable",
        }
    except Exception as exc:
        return {
            "supported": True, "enabled": None, "ok": False,
            "installed": True, "platform": "launchd",
            "task_name": LAUNCHD_LABEL, "reason": "launchctl_error",
            "detail": type(exc).__name__,
        }

    try:
        loaded = _launchctl(["bootstrap", _launchd_domain(), str(path)])
    except FileNotFoundError:
        return {
            "supported": False, "enabled": False, "ok": False,
            "installed": True, "platform": "launchd",
            "task_name": LAUNCHD_LABEL, "reason": "launchctl_unavailable",
        }
    except Exception as exc:
        loaded = None
        load_error = type(exc).__name__
    else:
        load_error = ""

    if loaded is None or loaded.returncode != 0:
        reason = (
            "launchctl_error" if loaded is None
            else _classify_launchctl_error(loaded.stderr)
        )
        detail = load_error if loaded is None else (loaded.stderr or "").strip()[:200]
        write_audit({
            "actor": "human", "source": source,
            "event_type": "autostart_enable_failed",
            "entity_type": "gateway_autostart", "entity_id": LAUNCHD_LABEL,
            "message": f"macOS LaunchAgent bootstrap failed: {reason}",
        })
        return {
            "supported": True, "enabled": False, "ok": False,
            "installed": True, "platform": "launchd",
            "task_name": LAUNCHD_LABEL, "reason": reason, "detail": detail,
        }

    try:
        prime = _launchctl(["kickstart", "-k", _launchd_target()])
        primed = prime.returncode == 0
        prime_reason = None if primed else _classify_launchctl_error(prime.stderr)
    except Exception as exc:
        primed = False
        prime_reason = "launchctl_unavailable" if isinstance(exc, FileNotFoundError) else "launchctl_error"

    live = _status_macos()
    if live.get("enabled") is not True:
        write_audit({
            "actor": "human", "source": source,
            "event_type": "autostart_enable_failed",
            "entity_type": "gateway_autostart", "entity_id": LAUNCHD_LABEL,
            "message": "macOS LaunchAgent bootstrap returned success but live query failed.",
        })
        return {**live, "ok": False, "reason": live.get("reason") or "verification_failed"}

    verify = _verify_after_prime(timeout=30.0) if primed else {}
    write_audit({
        "actor": "human", "source": source, "event_type": "autostart_enabled",
        "entity_type": "gateway_autostart", "entity_id": LAUNCHD_LABEL,
        "message": (
            "macOS LaunchAgent installed (RunAtLoad + "
            f"{LAUNCHD_INTERVAL_SEC}s watchdog)."
        ),
    })
    return {
        **live,
        "ok": True,
        "primed": live.get("primed") if live.get("primed") is not None else primed,
        "prime_reason": prime_reason,
        **verify,
    }


def _disable_macos(source: str) -> dict:
    path = _launchd_plist_path()
    try:
        proc = _launchctl(["bootout", _launchd_target()])
    except FileNotFoundError:
        return {
            "supported": False, "enabled": path.exists(), "ok": False,
            "platform": "launchd", "reason": "launchctl_unavailable",
        }
    except Exception as exc:
        return {
            "supported": True, "enabled": None, "ok": False,
            "platform": "launchd", "reason": "launchctl_error",
            "detail": type(exc).__name__,
        }

    if proc.returncode != 0:
        reason = _classify_launchctl_error(proc.stderr)
        if reason != "not_found":
            return {
                "supported": True, "enabled": None, "ok": False,
                "platform": "launchd", "reason": reason,
                "detail": (proc.stderr or "").strip()[:200],
            }
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        return {
            "supported": True, "enabled": False, "ok": False,
            "platform": "launchd", "reason": "plist_remove_error",
            "detail": type(exc).__name__,
        }

    write_audit({
        "actor": "human", "source": source, "event_type": "autostart_disabled",
        "entity_type": "gateway_autostart", "entity_id": LAUNCHD_LABEL,
        "message": "macOS LaunchAgent removed.",
    })
    return {"supported": True, "enabled": False, "ok": True, "platform": "launchd"}


def enable(source: str = "api") -> dict:
    """Register (or replace) the autostart+watchdog scheduled task. Never raises."""
    if _is_macos():
        return _enable_macos(source)
    if not _is_windows():
        return _not_supported(f"unsupported_platform:{platform.system().lower()}")

    xml = _build_task_xml()
    tmp = None
    try:
        # schtasks /XML requires UTF-16 (the 'utf-16' codec emits the BOM).
        with tempfile.NamedTemporaryFile(
            mode="wb", suffix=".xml", delete=False
        ) as f:
            f.write(xml.encode("utf-16"))
            tmp = f.name
        proc = subprocess.run(
            ["schtasks", "/Create", "/TN", TASK_NAME, "/XML", tmp, "/F"],
            capture_output=True, timeout=30,
            **_schtasks_text_kwargs(),
            **_no_window_kwargs(),
        )
    except FileNotFoundError:
        return {"supported": False, "enabled": False, "ok": False,
                "reason": "schtasks_unavailable"}
    except Exception as e:
        return {"supported": True, "enabled": False, "ok": False,
                "reason": "schtasks_error", "detail": str(e)[:200]}
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    if proc.returncode == 0:
        prime = _prime_task()
        # Verify the task can actually start the gateway (catch config/env bugs early).
        verify = {}
        if prime.get("primed"):
            verify = _verify_after_prime(timeout=30.0)
        write_audit({
            "actor": "human", "source": source, "event_type": "autostart_enabled",
            "entity_type": "gateway_autostart", "entity_id": TASK_NAME,
            "message": f"Autostart scheduled task created (logon + {WATCHDOG_INTERVAL} watchdog).",
        })
        return {"supported": True, "enabled": True, "ok": True, **prime, **verify}

    reason = _classify_stderr(proc.stderr)
    write_audit({
        "actor": "human", "source": source, "event_type": "autostart_enable_failed",
        "entity_type": "gateway_autostart", "entity_id": TASK_NAME,
        "message": f"Autostart enable failed: {reason}",
    })
    return {"supported": True, "enabled": False, "ok": False,
            "reason": reason, "detail": (proc.stderr or "").strip()[:200]}


def disable(source: str = "api") -> dict:
    """Delete the scheduled task. Absence is treated as success. Never raises."""
    if _is_macos():
        return _disable_macos(source)
    if not _is_windows():
        return _not_supported(f"unsupported_platform:{platform.system().lower()}")

    try:
        proc = subprocess.run(
            ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
            capture_output=True, timeout=30,
            **_schtasks_text_kwargs(),
            **_no_window_kwargs(),
        )
    except FileNotFoundError:
        return {"supported": False, "enabled": False, "ok": False,
                "reason": "schtasks_unavailable"}
    except Exception as e:
        return {"supported": True, "enabled": True, "ok": False,
                "reason": "schtasks_error", "detail": str(e)[:200]}

    reason = _classify_stderr(proc.stderr) if proc.returncode != 0 else None
    if proc.returncode == 0 or reason == "not_found":
        write_audit({
            "actor": "human", "source": source, "event_type": "autostart_disabled",
            "entity_type": "gateway_autostart", "entity_id": TASK_NAME,
            "message": "Autostart scheduled task removed.",
        })
        return {"supported": True, "enabled": False, "ok": True}

    return {"supported": True, "enabled": True, "ok": False,
            "reason": reason, "detail": (proc.stderr or "").strip()[:200]}


def _prime_task() -> dict:
    """Best-effort immediate run so the current login session is protected too."""
    try:
        proc = subprocess.run(
            ["schtasks", "/Run", "/TN", TASK_NAME],
            capture_output=True,
            timeout=30,
            **_schtasks_text_kwargs(),
            **_no_window_kwargs(),
        )
    except FileNotFoundError:
        return {"primed": False, "prime_reason": "schtasks_unavailable"}
    except Exception as e:
        return {"primed": False, "prime_reason": "schtasks_error", "prime_detail": str(e)[:200]}
    if proc.returncode == 0:
        return {"primed": True}
    return {
        "primed": False,
        "prime_reason": _classify_stderr(proc.stderr),
        "prime_detail": (proc.stderr or "").strip()[:200],
    }


def _parse_xml_status(xml_str: str) -> dict:
    """Parse the structured XML output from `schtasks /Query /XML`.

    Uses xml.etree.ElementTree for locale-independent field extraction.
    The XML has namespace http://schemas.microsoft.com/windows/2004/02/mit/task.
    """
    ns = {"t": _XML_NS}
    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError:
        return {}

    result: dict = {}

    # Enabled state from <Settings><Enabled>
    enabled_el = root.find(".//t:Settings/t:Enabled", ns)
    if enabled_el is not None:
        result["task_enabled"] = (enabled_el.text or "").lower() == "true"

    # Interval from repetition
    interval_el = root.find(".//t:Triggers//t:Repetition/t:Interval", ns)
    if interval_el is not None:
        result["interval"] = interval_el.text

    # Command being run
    cmd_el = root.find(".//t:Actions/t:Exec/t:Command", ns)
    if cmd_el is not None:
        result["command"] = cmd_el.text

    args_el = root.find(".//t:Actions/t:Exec/t:Arguments", ns)
    if args_el is not None:
        result["arguments"] = args_el.text

    return result


def _parse_runtime_status(verbose_stdout: str) -> dict:
    """Parse Last Run Time / Last Result from verbose LIST output.

    These fields are NOT in the XML export — only in /FO LIST /V output.
    We still need this for primed detection, but we minimize reliance on it.
    """
    result: dict = {}
    for line in (verbose_stdout or "").splitlines():
        stripped = line.strip()
        # Match on colon-separated key/value regardless of locale label.
        # Last Run Time is always the timestamp line after "Last Run Time" or
        # locale equivalent; Last Result is always a numeric/hex value.
        if ":" in stripped:
            key_part, _, val_part = stripped.partition(":")
            val = val_part.strip()
            key_lower = key_part.strip().lower()
            if "last run" in key_lower and "time" in key_lower:
                result["last_run_time"] = val
            elif "last result" in key_lower or "last_result" in key_lower:
                result["last_result"] = val
            elif "status" == key_lower or key_lower.endswith("status"):
                if "status" not in result:
                    result["status"] = val
    return result


def _task_primed(*, last_run_time: str, last_result: str) -> bool | None:
    if not last_run_time and not last_result:
        return None
    never_run_markers = ("1999/11/30", "11/30/1999", "N/A", "Never")
    if not last_run_time or any(last_run_time.startswith(marker) for marker in never_run_markers):
        return False
    return last_result in {"", "0", "0x0"}


def status() -> dict:
    """Live read of whether the autostart task exists. Read-only; never audits.

    Uses two complementary queries:
    1. `schtasks /Query /TN ... /XML` for structured config (locale-independent)
    2. `schtasks /Query /TN ... /FO LIST /V` for runtime fields (Last Run/Result)

    Returns enabled=True/False when known, or enabled=None with reason when the
    state cannot be determined — so the UI never shows a reassuring state that is
    not backed by a real query (UI honesty).
    """
    if _is_macos():
        return _status_macos()
    if not _is_windows():
        return {"supported": False, "enabled": False, "task_name": TASK_NAME,
                "reason": f"unsupported_platform:{platform.system().lower()}"}

    # Query 1: XML (structured, locale-independent config)
    try:
        xml_proc = subprocess.run(
            ["schtasks", "/Query", "/TN", TASK_NAME, "/XML"],
            capture_output=True, timeout=30,
            **_schtasks_text_kwargs(),
            **_no_window_kwargs(),
        )
    except FileNotFoundError:
        return {"supported": False, "enabled": False, "task_name": TASK_NAME,
                "reason": "schtasks_unavailable"}
    except Exception as e:
        return {"supported": True, "enabled": None, "task_name": TASK_NAME,
                "reason": "schtasks_error", "detail": str(e)[:200]}

    if xml_proc.returncode != 0:
        reason = _classify_stderr(xml_proc.stderr)
        if reason == "not_found":
            return {"supported": True, "enabled": False, "task_name": TASK_NAME}
        return {"supported": True, "enabled": None, "task_name": TASK_NAME,
                "reason": reason, "detail": (xml_proc.stderr or "").strip()[:200]}

    xml_info = _parse_xml_status(xml_proc.stdout)

    # Query 2: LIST /V (runtime state — Last Run Time, Last Result)
    runtime_info: dict = {}
    try:
        list_proc = subprocess.run(
            ["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST", "/V"],
            capture_output=True, timeout=30,
            **_schtasks_text_kwargs(),
            **_no_window_kwargs(),
        )
        if list_proc.returncode == 0:
            runtime_info = _parse_runtime_status(list_proc.stdout)
    except Exception:
        pass

    last_run_time = runtime_info.get("last_run_time", "")
    last_result = runtime_info.get("last_result", "")

    return {
        "supported": True,
        "enabled": True,
        "task_name": TASK_NAME,
        "interval": xml_info.get("interval", WATCHDOG_INTERVAL),
        "task_enabled": xml_info.get("task_enabled", True),
        "status": runtime_info.get("status", ""),
        "last_run_time": last_run_time,
        "last_result": last_result,
        "primed": _task_primed(last_run_time=last_run_time, last_result=last_result),
    }


def write_watchdog_tick(action: str, exit_code: int = 0, duration_ms: int = 0) -> None:
    """Record a watchdog tick to data/logs/watchdog.jsonl for diagnostics."""
    from datetime import timezone as tz
    from aletheia.files.jsonl import append_jsonl

    append_jsonl("watchdog.jsonl", {
        "timestamp": datetime.now(tz.utc).isoformat(),
        "action": action,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
    })
