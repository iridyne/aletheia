"""Windows Task Scheduler autostart + supervisor watchdog.

The gateway already detaches a background process and a supervisor respawns the
inner server on crash — but nothing restarts the supervisor itself after a
reboot, power loss, OOM kill, or manual kill. This module closes that gap on
Windows by registering a per-user scheduled task that runs the SAME gated
`gateway start` a human runs:

  - ONLOGON trigger  → starts the gateway when the user logs on (boot autostart).
  - Repeat every 2m  → because `do_start` is idempotent (no-op when already
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
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

from aletheia.config.loader import _PROJECT_ROOT
from aletheia.files.jsonl import write_audit
from aletheia.gateway.pidfile import _no_window_kwargs

TASK_NAME = "Aletheia Trading OS Gateway"

# How often the task re-checks the gateway (and resurrects a dead supervisor).
WATCHDOG_INTERVAL = "PT30S"

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
    venv_dir = Path(exe).resolve().parent.parent
    pyvenv_cfg = venv_dir / "pyvenv.cfg"
    real_python = _python_for_task()

    # If we resolved to a real python (different from venv shim), need bootstrap.
    if _exists(pyvenv_cfg) and str(Path(exe).resolve()) != str(Path(real_python).resolve()):
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
        "30-second watchdog that restarts the supervisor if it dies.</Description>\n"
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


def _not_supported(reason: str = "not_windows") -> dict:
    return {"supported": False, "enabled": False, "ok": False, "reason": reason}


def _classify_stderr(stderr: str) -> str:
    low = (stderr or "").lower()
    if "access is denied" in low or "denied" in low:
        return "access_denied"
    if "cannot find" in low or "does not exist" in low or "no mapping" in low:
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


def enable(source: str = "api") -> dict:
    """Register (or replace) the autostart+watchdog scheduled task. Never raises."""
    if not _is_windows():
        return _not_supported()

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
    if not _is_windows():
        return _not_supported()

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
    if not _is_windows():
        return {"supported": False, "enabled": False, "task_name": TASK_NAME,
                "reason": "not_windows"}

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
