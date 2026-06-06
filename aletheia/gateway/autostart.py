"""Windows Task Scheduler autostart + supervisor watchdog (guide roadmap §30.3).

The gateway already detaches a background process and a supervisor respawns the
inner server on crash — but nothing restarts the supervisor itself after a
reboot, power loss, OOM kill, or manual kill. This module closes that gap on
Windows by registering a per-user scheduled task that runs the SAME gated
`gateway start` a human runs:

  - ONLOGON trigger  → starts the gateway when the user logs on (boot autostart).
  - Repeat every 5m  → because `do_start` is idempotent (no-op when already
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
from pathlib import Path

from aletheia.config.loader import _PROJECT_ROOT
from aletheia.files.jsonl import write_audit
from aletheia.gateway.pidfile import _no_window_kwargs

TASK_NAME = "Aletheia Trading OS Gateway"

# How often the task re-checks the gateway (and resurrects a dead supervisor).
WATCHDOG_INTERVAL = "PT5M"

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
    """Build the task arguments, injecting __PYVENV_LAUNCHER__ if needed.

    When using the real pythonw.exe (resolved through pyvenv.cfg), it needs
    __PYVENV_LAUNCHER__ set to find the venv's site-packages. We pass a -c
    bootstrap that sets the env var, activates the venv path, and then runs
    the gateway CLI.
    """
    exe = sys.executable
    venv_dir = Path(exe).resolve().parent.parent
    pyvenv_cfg = venv_dir / "pyvenv.cfg"
    real_python = _python_for_task()

    # If we resolved to a real python (different from venv shim), need bootstrap.
    if _exists(pyvenv_cfg) and str(Path(exe).resolve()) != str(Path(real_python).resolve()):
        venv_python = str(Path(exe).resolve())
        project_root = str(_PROJECT_ROOT)
        # Inline bootstrap: set __PYVENV_LAUNCHER__ so site.py finds the venv,
        # then import and run the gateway CLI.
        return (
            f'-c "import os,sys;'
            f"os.environ['__PYVENV_LAUNCHER__']=r'{venv_python}';"
            f"os.chdir(r'{project_root}');"
            f"from aletheia.main import app;"
            f"sys.argv=['task','gateway','start','--no-browser'];"
            f'app()"'
        )
    return "-m aletheia.main gateway start --no-browser"


def _build_task_xml() -> str:
    """Build the Task Scheduler definition XML.

    LogonTrigger + indefinite 5-minute repetition (omitting <Duration> with
    StopAtDurationEnd=false means repeat forever). InteractiveToken + the omission
    of /RU SYSTEM keeps creation non-elevated; LeastPrivilege keeps the started
    gateway unprivileged. MultipleInstancesPolicy=IgnoreNew guards the only
    realistic double-fire (logon coinciding with the first repetition tick).
    """
    user = _xml_escape(_current_user())
    command = _xml_escape(_python_for_task())
    arguments = _xml_escape(_task_arguments())
    workdir = _xml_escape(str(_PROJECT_ROOT))
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        f'<Task version="1.2" xmlns="{_XML_NS}">\n'
        "  <RegistrationInfo>\n"
        f"    <Author>{user}</Author>\n"
        "    <Description>Aletheia Trading OS gateway autostart (on logon) plus a "
        "5-minute watchdog that restarts the supervisor if it dies.</Description>\n"
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
        # Hidden: Task Scheduler suppresses the task's window. Combined with
        # pythonw.exe and the spawn-time CREATE_NO_WINDOW guards, this prevents
        # any Windows Terminal flash on logon or the 5-minute watchdog tick.
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
            capture_output=True, text=True, timeout=30,
            **_no_window_kwargs(),
        )
    except FileNotFoundError:
        return {"supported": False, "enabled": False, "ok": False,
                "reason": "schtasks_unavailable"}
    except Exception as e:  # subprocess timeout / OS errors — stay honest
        return {"supported": True, "enabled": False, "ok": False,
                "reason": "schtasks_error", "detail": str(e)[:200]}
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    if proc.returncode == 0:
        write_audit({
            "actor": "human", "source": source, "event_type": "autostart_enabled",
            "entity_type": "gateway_autostart", "entity_id": TASK_NAME,
            "message": f"Autostart scheduled task created (logon + {WATCHDOG_INTERVAL} watchdog).",
        })
        return {"supported": True, "enabled": True, "ok": True}

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
            capture_output=True, text=True, timeout=30,
            **_no_window_kwargs(),
        )
    except FileNotFoundError:
        return {"supported": False, "enabled": False, "ok": False,
                "reason": "schtasks_unavailable"}
    except Exception as e:
        return {"supported": True, "enabled": True, "ok": False,
                "reason": "schtasks_error", "detail": str(e)[:200]}

    reason = _classify_stderr(proc.stderr) if proc.returncode != 0 else None
    # "not found" means there is nothing to disable — already in the desired state.
    if proc.returncode == 0 or reason == "not_found":
        write_audit({
            "actor": "human", "source": source, "event_type": "autostart_disabled",
            "entity_type": "gateway_autostart", "entity_id": TASK_NAME,
            "message": "Autostart scheduled task removed.",
        })
        return {"supported": True, "enabled": False, "ok": True}

    return {"supported": True, "enabled": True, "ok": False,
            "reason": reason, "detail": (proc.stderr or "").strip()[:200]}


def _parse_trigger(stdout: str) -> str:
    """Best-effort one-line trigger summary from `schtasks /Query /FO LIST /V`."""
    for line in (stdout or "").splitlines():
        if line.strip().lower().startswith("schedule type"):
            return line.split(":", 1)[1].strip()
    return ""


def status() -> dict:
    """Live read of whether the autostart task exists. Read-only; never audits.

    Returns enabled=True/False when known, or enabled=None with reason when the
    state cannot be determined (e.g. access denied) — so the UI never shows a
    reassuring state that is not backed by a real query (UI honesty).
    """
    if not _is_windows():
        return {"supported": False, "enabled": False, "task_name": TASK_NAME,
                "reason": "not_windows"}

    try:
        proc = subprocess.run(
            ["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST", "/V"],
            capture_output=True, text=True, timeout=30,
            **_no_window_kwargs(),
        )
    except FileNotFoundError:
        return {"supported": False, "enabled": False, "task_name": TASK_NAME,
                "reason": "schtasks_unavailable"}
    except Exception as e:
        return {"supported": True, "enabled": None, "task_name": TASK_NAME,
                "reason": "schtasks_error", "detail": str(e)[:200]}

    if proc.returncode == 0:
        return {"supported": True, "enabled": True, "task_name": TASK_NAME,
                "trigger": _parse_trigger(proc.stdout), "interval": WATCHDOG_INTERVAL}

    reason = _classify_stderr(proc.stderr)
    if reason == "not_found":
        return {"supported": True, "enabled": False, "task_name": TASK_NAME}
    # Access denied or any other failure: state is unknown — do NOT claim a status.
    return {"supported": True, "enabled": None, "task_name": TASK_NAME,
            "reason": reason, "detail": (proc.stderr or "").strip()[:200]}
