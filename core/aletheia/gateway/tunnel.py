"""Cloudflare Tunnel lifecycle management.

Manages a cloudflared process that exposes the local gateway (127.0.0.1:8787)
via a Cloudflare Named Tunnel. The tunnel process is started/stopped alongside
the gateway and tracked via a pidfile at data/tunnel.pid.
"""

import logging
import json
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path

from aletheia.config.loader import edit_config, get_data_dir, load_config

logger = logging.getLogger("aletheia.tunnel")

_TUNNEL_PID_NAME = "tunnel.pid"
_TUNNEL_CONFIG_NAME = "tunnel.yml"
_DEFAULT_CLOUDFLARED_PATHS = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    / "Cloudflare.cloudflared_Microsoft.Winget.Source_8wekyb3d8bbwe" / "cloudflared.exe",
    Path("C:/Program Files/cloudflared/cloudflared.exe"),
    Path("C:/Program Files (x86)/cloudflared/cloudflared.exe"),
]


def _pid_path() -> Path:
    return get_data_dir() / _TUNNEL_PID_NAME


def _config_path() -> Path:
    return get_data_dir() / _TUNNEL_CONFIG_NAME


def find_cloudflared() -> str | None:
    """Locate cloudflared executable. Returns path or None."""
    cfg = load_config()
    if cfg.tunnel.cloudflared_path:
        p = Path(cfg.tunnel.cloudflared_path)
        if p.exists():
            return str(p)

    found = shutil.which("cloudflared")
    if found:
        return found

    for candidate in _DEFAULT_CLOUDFLARED_PATHS:
        if candidate.exists():
            return str(candidate)

    return None


def _write_tunnel_config(cfg) -> Path:
    """Generate data/tunnel.yml from config."""
    config_path = _config_path()
    creds = cfg.tunnel.credentials_path
    if not creds:
        home_cf = Path.home() / ".cloudflared" / f"{cfg.tunnel.tunnel_id}.json"
        creds = str(home_cf)

    lines = [
        f"tunnel: {cfg.tunnel.tunnel_id}",
        f"credentials-file: {creds}",
        "ingress:",
        f"  - hostname: {cfg.tunnel.hostname}",
        f"    service: http://127.0.0.1:{cfg.app.dashboard_port}",
        "  - service: http_status:404",
    ]
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return config_path


def is_tunnel_running() -> bool:
    """Check that the recorded process is alive and still owned by Aletheia."""
    record = _read_tunnel_record()
    if record is None:
        return False
    return _tunnel_process_owned(record) is True


def read_tunnel_pid() -> int | None:
    """Read the tunnel PID from the ownership record."""
    record = _read_tunnel_record()
    return int(record["pid"]) if record is not None else None


def _read_tunnel_record() -> dict | None:
    """Read the process ownership record.

    Legacy integer-only pidfiles are accepted for cleanup but are never treated
    as proof that the referenced process belongs to Aletheia.
    """
    p = _pid_path()
    if not p.exists():
        return None
    try:
        raw = p.read_text(encoding="utf-8").strip()
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = int(raw)
        if isinstance(value, int):
            return {"pid": value, "legacy": True}
        if not isinstance(value, dict) or not isinstance(value.get("pid"), int):
            return None
        return value
    except (TypeError, ValueError, OSError):
        return None


def _write_tunnel_record(*, pid: int, executable: str, config_path: Path) -> bool:
    record = {
        "version": 1,
        "pid": pid,
        "executable": str(executable),
        "config_path": str(config_path.resolve()),
    }
    _pid_path().write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
    return True


def _pid_alive(pid: int) -> bool:
    if platform.system() == "Windows":
        try:
            # Wait-based liveness: an exited process whose handle is still open
            # must read as dead, or a verified stop could never be observed.
            from aletheia.gateway._win32_proc import pid_alive_win32
            return pid_alive_win32(pid, ("",))
        except Exception as exc:
            logger.warning("tunnel: win32 pid probe unavailable for %s (%s)", pid, type(exc).__name__)
            return False
    try:
        # Reap it if it is our own exited child (route-started tunnel), so a
        # zombie is not mistaken for a live cloudflared.
        os.waitpid(pid, os.WNOHANG)
    except (ChildProcessError, OSError):
        logger.debug("tunnel: pid %s is not a reapable child of this process", pid)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _wait_pid_exit(pid: int, timeout: float) -> bool:
    """Poll until pid is observed dead; False if it is still alive at the deadline."""
    deadline = time.monotonic() + timeout
    while _pid_alive(pid):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)
    return True


def _process_command_line(pid: int) -> str | None:
    """Read the live command line used to prove the PID is our tunnel."""
    try:
        if platform.system() == "Windows":
            command = (
                f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine"
            )
            args = ["powershell", "-NoProfile", "-Command", command]
        else:
            # -ww: without it procps truncates to the terminal width (80 when
            # unknown), cutting off the --config path the ownership check needs.
            args = ["ps", "-ww", "-p", str(pid), "-o", "command="]
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=5,
        )
        value = proc.stdout.strip()
        return value if proc.returncode == 0 and value else None
    except (OSError, subprocess.SubprocessError):
        return None


def _tunnel_process_owned(record: dict) -> bool | None:
    """Verify that the live PID command is the configured cloudflared tunnel.

    None means the live command line could not be read: ownership is unproven
    but not disproven, so the record must be kept for a later retry (#1673).
    """
    if record.get("legacy"):
        return False
    pid = int(record["pid"])
    if not _pid_alive(pid):
        return False
    command_line = _process_command_line(pid)
    if not command_line:
        return None
    expected_exe = str(record.get("executable") or "")
    expected_config = str(record.get("config_path") or "")
    if platform.system() == "Windows":
        command_line = command_line.lower()
        expected_exe = expected_exe.lower()
        expected_config = expected_config.lower()
    return (
        expected_exe in command_line
        and "--config" in command_line
        and expected_config in command_line
    )


def start_tunnel() -> dict:
    """Start cloudflared tunnel process. Returns status dict."""
    cfg = load_config()

    if not cfg.tunnel.enabled:
        return {"ok": False, "reason": "tunnel not enabled in config"}

    if not cfg.tunnel.tunnel_id:
        return {"ok": False, "reason": "tunnel_id not configured"}

    if not cfg.tunnel.hostname:
        return {"ok": False, "reason": "hostname not configured"}

    if is_tunnel_running():
        pid = read_tunnel_pid()
        return {"ok": True, "status": "already_running", "pid": pid}

    exe = find_cloudflared()
    if not exe:
        return {"ok": False, "reason": "cloudflared not found"}

    config_file = _write_tunnel_config(cfg)
    args = [exe, "tunnel", "--protocol", "http2", "--config", str(config_file), "run"]

    if cfg.tunnel.metrics_port:
        args.extend(["--metrics", f"127.0.0.1:{cfg.tunnel.metrics_port}"])

    try:
        if platform.system() == "Windows":
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            DETACHED_PROCESS = 0x00000008
            CREATE_NO_WINDOW = 0x08000000
            proc = subprocess.Popen(
                args,
                creationflags=CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS | CREATE_NO_WINDOW,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            proc = subprocess.Popen(
                args,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        _write_tunnel_record(pid=proc.pid, executable=exe, config_path=config_file)

        # Verify cloudflared stays alive for a few seconds (catches config errors).
        # cloudflared logs to stderr and exits non-zero on bad config/creds within ~2s.
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                _pid_path().unlink(missing_ok=True)
                return {"ok": False, "reason": f"cloudflared exited immediately (code={proc.returncode})"}
            time.sleep(0.3)

        logger.info("Tunnel started (pid=%d, hostname=%s)", proc.pid, cfg.tunnel.hostname)
        return {"ok": True, "status": "started", "pid": proc.pid, "hostname": cfg.tunnel.hostname}

    except Exception as e:
        return {"ok": False, "reason": str(e)}


def stop_tunnel() -> dict:
    """Stop the tunnel process; report "stopped" only once it is observed dead."""
    record = _read_tunnel_record()
    if record is None:
        return {"ok": True, "status": "not_running"}
    pid = int(record["pid"])

    if not _pid_alive(pid):
        _pid_path().unlink(missing_ok=True)
        return {"ok": True, "status": "not_running"}
    owned = _tunnel_process_owned(record)
    if owned is None:
        logger.error("Refused to stop tunnel pid=%d: its command line is unreadable; record kept", pid)
        return {
            "ok": False,
            "status": "ownership_unverified",
            "pid": pid,
            "reason": "recorded PID is alive but its identity could not be read; retry",
        }
    if not owned:
        _pid_path().unlink(missing_ok=True)
        logger.error("Refused to stop unowned process referenced by tunnel pidfile (pid=%d)", pid)
        return {
            "ok": False,
            "status": "ownership_mismatch",
            "pid": pid,
            "reason": "recorded PID does not match the Aletheia cloudflared process",
        }

    try:
        if platform.system() == "Windows":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, timeout=10,
            )
        else:
            os.kill(pid, 15)  # SIGTERM
            if not _wait_pid_exit(pid, 2.0):
                os.kill(pid, 9)  # SIGKILL
    except Exception as e:
        logger.warning("Error stopping tunnel pid=%d: %s", pid, e)

    if not _wait_pid_exit(pid, 5.0):
        logger.error("Tunnel pid=%d is still alive after the stop signal; record kept", pid)
        return {
            "ok": False,
            "status": "stop_failed",
            "pid": pid,
            "reason": "cloudflared is still running after the stop signal",
        }
    _pid_path().unlink(missing_ok=True)
    logger.info("Tunnel stopped (pid=%d)", pid)
    return {"ok": True, "status": "stopped", "pid": pid}


def tunnel_status() -> dict:
    """Return current tunnel status."""
    cfg = load_config()
    record = _read_tunnel_record()
    running = is_tunnel_running()
    pid = int(record["pid"]) if record is not None else None
    ownership_mismatch = bool(record and _pid_alive(pid) and not running)

    return {
        "enabled": cfg.tunnel.enabled,
        "running": running,
        "pid": pid if running else None,
        "ownership_verified": running,
        "ownership_mismatch": ownership_mismatch,
        "tunnel_id": cfg.tunnel.tunnel_id or None,
        "hostname": cfg.tunnel.hostname or None,
        "cloudflared_installed": find_cloudflared() is not None,
    }


def setup_tunnel(tunnel_id: str, hostname: str, credentials_path: str = "") -> dict:
    """Configure tunnel settings (one-time setup)."""
    with edit_config() as cfg:
        cfg.tunnel.tunnel_id = tunnel_id
        cfg.tunnel.hostname = hostname
        if credentials_path:
            cfg.tunnel.credentials_path = credentials_path
        cfg.tunnel.enabled = True

        exe = find_cloudflared()
        if exe and not cfg.tunnel.cloudflared_path:
            cfg.tunnel.cloudflared_path = exe

    return {"ok": True, "tunnel_id": tunnel_id, "hostname": hostname}
