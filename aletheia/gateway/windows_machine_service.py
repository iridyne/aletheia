"""Optional Windows Service backend with UAC-owned installation transactions."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from aletheia.config.loader import get_data_dir
from aletheia.gateway.console_text import decode_console
from aletheia.gateway.pidfile import _no_window_kwargs
from aletheia.packaged_process import is_frozen_runtime

SERVICE_ID = "AletheiaGateway"
SERVICE_NAME = "Aletheia Gateway"
WINSW_VERSION = "2.12.0"
WINSW_URL = "https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW-x64.exe"
WINSW_SHA256 = "05b82d46ad331cc16bdc00de5c6332c1ef818df8ceefcd49c726553209b3a0da"


def _program_data() -> Path:
    return Path(os.environ.get("ProgramData", r"C:\ProgramData"))


def machine_root() -> Path:
    return _program_data() / "Aletheia"


def service_root() -> Path:
    return machine_root() / "service"


def machine_data_dir() -> Path:
    return machine_root() / "runtime"


def wrapper_path() -> Path:
    return service_root() / "AletheiaGatewayService.exe"


def config_path() -> Path:
    return service_root() / "AletheiaGatewayService.xml"


def _result(**values) -> dict:
    base = {
        "scope": "machine",
        "backend": "windows_service",
        "supported": platform.system() == "Windows",
        "installed": False,
        "enabled": False,
        "running": False,
        "owned": None,
        "config_valid": None,
        "elevation_required": True,
        "recovery_scope": "boot_and_failure",
        "data_dir": str(machine_data_dir()),
    }
    base.update(values)
    return base


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _runtime_is_service_safe(executable: Path | None = None) -> bool:
    executable = (executable or Path(sys.executable)).resolve()
    if not is_frozen_runtime() or executable.suffix.lower() != ".exe":
        return False
    allowed_roots = [
        Path(value).resolve()
        for value in (
            os.environ.get("ProgramFiles"),
            os.environ.get("ProgramW6432"),
            os.environ.get("ProgramData"),
        )
        if value
    ]
    return any(executable == root or root in executable.parents for root in allowed_roots)


def _build_winsw_xml(*, executable: Path | None = None) -> str:
    executable = (executable or Path(sys.executable)).resolve()
    root = ET.Element("service")
    for tag, text in (
        ("id", SERVICE_ID),
        ("name", SERVICE_NAME),
        ("description", "Aletheia machine Gateway service"),
        ("executable", str(executable)),
        ("arguments", "__packaged_gateway_runner --no-browser"),
        ("workingdirectory", str(executable.parent)),
        ("stoptimeout", "30 sec"),
        ("resetfailure", "1 hour"),
        ("logpath", str(machine_data_dir() / "logs")),
    ):
        ET.SubElement(root, tag).text = text
    ET.SubElement(root, "env", {"name": "ALETHEIA_DATA_DIR", "value": str(machine_data_dir())})
    account = ET.SubElement(root, "serviceaccount")
    ET.SubElement(account, "username").text = r"NT AUTHORITY\LocalService"
    ET.SubElement(root, "onfailure", {"action": "restart", "delay": "60 sec"})
    ET.SubElement(root, "onfailure", {"action": "restart", "delay": "60 sec"})
    ET.SubElement(root, "onfailure", {"action": "none"})
    log = ET.SubElement(root, "log", {"mode": "roll-by-size"})
    ET.SubElement(log, "sizeThreshold").text = "10485760"
    ET.SubElement(log, "keepFiles").text = "5"
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def _run(args: list[str], *, timeout: float = 60.0):
    proc = subprocess.run(args, capture_output=True, timeout=timeout, **_no_window_kwargs())
    proc.stdout = decode_console(proc.stdout)
    proc.stderr = decode_console(proc.stderr)
    return proc


def _service_query() -> tuple[bool | None, bool, bool | None, str]:
    try:
        query = _run(["sc.exe", "query", SERVICE_ID])
    except FileNotFoundError:
        return None, False, None, "sc_unavailable"
    except Exception as exc:
        return None, False, None, type(exc).__name__
    combined = f"{query.stdout}\n{query.stderr}"
    if query.returncode != 0:
        if "1060" in combined or "does not exist" in combined.lower() or "不存在" in combined:
            return False, False, False, ""
        if "denied" in combined.lower() or "拒绝" in combined:
            return None, False, None, "access_denied"
        return None, False, None, combined.strip()[:200]
    state_match = re.search(r"STATE\s*:\s*(\d+)", combined, flags=re.IGNORECASE)
    running = bool(state_match and int(state_match.group(1)) in {2, 4})
    qc = _run(["sc.exe", "qc", SERVICE_ID])
    qc_text = f"{qc.stdout}\n{qc.stderr}"
    start_match = re.search(r"START_TYPE\s*:\s*(\d+)", qc_text, flags=re.IGNORECASE)
    enabled = None if not start_match else int(start_match.group(1)) == 2
    owned = str(wrapper_path()).lower() in qc_text.lower()
    return True, running, enabled, "" if owned else "ownership_conflict"


def status() -> dict:
    if platform.system() != "Windows":
        return _result(supported=False, reason=f"unsupported_platform:{platform.system().lower()}")
    exists, running, enabled, detail = _service_query()
    if exists is False:
        partial = wrapper_path().exists() or config_path().exists()
        return _result(
            installed=False, owned=not partial, config_valid=not partial,
            reason="partial_install" if partial else ("packaged_runtime_required" if not _runtime_is_service_safe() else None),
            install_supported=_runtime_is_service_safe(),
        )
    if exists is None:
        return _result(installed=None, enabled=None, running=False, reason=detail or "service_query_error")
    try:
        config_valid = (
            wrapper_path().is_file()
            and _sha256(wrapper_path()) == WINSW_SHA256
            and config_path().read_text(encoding="utf-8") == _build_winsw_xml()
        )
    except Exception:
        config_valid = False
    return _result(
        installed=True, enabled=enabled, running=running,
        owned=detail != "ownership_conflict", config_valid=config_valid,
        reason=(detail or ("config_drift" if not config_valid else None)),
        install_supported=_runtime_is_service_safe(),
        credentials_provisioned=(machine_data_dir() / "config.yaml").exists(),
    )


def _cached_wrapper() -> Path:
    return get_data_dir() / "service" / "cache" / f"WinSW-x64-{WINSW_VERSION}.exe"


def _download_wrapper() -> Path:
    target = _cached_wrapper()
    if target.is_file() and _sha256(target) == WINSW_SHA256:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".download")
    request = urllib.request.Request(WINSW_URL, headers={"User-Agent": "Aletheia-Service-Installer/1"})
    with urllib.request.urlopen(request, timeout=60) as response, temp.open("wb") as handle:
        shutil.copyfileobj(response, handle)
    if _sha256(temp) != WINSW_SHA256:
        temp.unlink(missing_ok=True)
        raise ValueError("WinSW checksum mismatch")
    os.replace(temp, target)
    return target


def _request_dir() -> Path:
    return get_data_dir() / "service" / "requests"


def _write_request(
    operation: str,
    *,
    wrapper: Path | None = None,
    arguments: dict | None = None,
) -> Path:
    directory = _request_dir()
    directory.mkdir(parents=True, exist_ok=True)
    request_path = directory / f"{uuid.uuid4().hex}.json"
    payload = {
        "version": 1,
        "operation": operation,
        "nonce": uuid.uuid4().hex,
        "wrapper": str(wrapper.resolve()) if wrapper else None,
        "executable": str(Path(sys.executable).resolve()),
        "arguments": arguments or {},
    }
    request_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return request_path


def _elevated_launcher() -> tuple[Path, list[str]]:
    """Resolve a CLI entrypoint that works for packaged and source runtimes."""
    executable = Path(sys.executable).resolve()
    if is_frozen_runtime():
        return executable, []
    cli = executable.with_name("aletheia.exe")
    if cli.is_file():
        return cli.resolve(), []
    return executable, ["-c", "from aletheia.main import app; app()"]


def _shell_execute_elevated(request_path: Path, *, timeout_ms: int = 300000) -> dict:
    if platform.system() != "Windows":
        return {"ok": False, "reason": "elevation_unavailable"}
    from ctypes import wintypes

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG),
            ("hwnd", wintypes.HWND), ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE), ("lpIDList", wintypes.LPVOID),
            ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD), ("hIcon", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    executable, prefix = _elevated_launcher()
    params = subprocess.list2cmdline([*prefix,
        "gateway", "service", "elevated-apply", "--request", str(request_path),
    ])
    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x00000040
    info.lpVerb = "runas"
    info.lpFile = str(executable)
    info.lpParameters = params
    info.lpDirectory = str(executable.parent)
    info.nShow = 0
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)):
        error = ctypes.get_last_error()
        return {"ok": False, "reason": "elevation_cancelled" if error == 1223 else "elevation_unavailable", "winerror": error}
    wait = ctypes.windll.kernel32.WaitForSingleObject(info.hProcess, timeout_ms)
    if wait == 0x00000102:
        return {"ok": False, "reason": "elevation_timeout"}
    exit_code = wintypes.DWORD()
    ctypes.windll.kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(exit_code))
    ctypes.windll.kernel32.CloseHandle(info.hProcess)
    result_path = request_path.with_suffix(".result.json")
    if not result_path.is_file():
        return {"ok": False, "reason": "elevated_result_missing", "exit_code": int(exit_code.value)}
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except Exception:
        return {"ok": False, "reason": "elevated_result_invalid", "exit_code": int(exit_code.value)}
    finally:
        result_path.unlink(missing_ok=True)
        request_path.unlink(missing_ok=True)
    return result


def _elevated(
    operation: str,
    *,
    wrapper: Path | None = None,
    arguments: dict | None = None,
    elevate: bool = True,
) -> dict:
    if not elevate:
        return _result(ok=False, reason="elevation_required")
    request_path = _write_request(operation, wrapper=wrapper, arguments=arguments)
    return {**_result(), **_shell_execute_elevated(request_path)}


def _apply_firewall_operation(payload: dict) -> dict:
    arguments = payload.get("arguments")
    if not isinstance(arguments, dict) or set(arguments) != {"port"}:
        return {"ok": False, "reason": "invalid_elevation_request"}
    port = arguments.get("port")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        return {"ok": False, "reason": "invalid_elevation_request"}
    from aletheia.gateway import windows_firewall
    if payload.get("operation") == "firewall_enable_lan":
        from aletheia.config.loader import load_config
        cfg = load_config()
        if port != cfg.app.dashboard_port or cfg.app.bind_host not in {"0.0.0.0", "::"}:
            return {"ok": False, "reason": "invalid_elevation_request"}
        return windows_firewall.apply_enable_lan(port)
    if payload.get("operation") == "firewall_disable_lan":
        return windows_firewall.apply_disable_lan(port)
    return {"ok": False, "reason": "invalid_elevation_request"}


def _apply_acls() -> None:
    showsid = _run(["sc.exe", "showsid", SERVICE_ID])
    match = re.search(r"S-1-5-80-(?:\d+-){4}\d+", f"{showsid.stdout}\n{showsid.stderr}")
    if not match:
        raise RuntimeError("service SID unavailable")
    service_sid = f"*{match.group(0)}"
    for path, permission in ((service_root(), "RX"), (machine_data_dir(), "M")):
        proc = _run([
            "icacls.exe", str(path), "/inheritance:r", "/grant:r",
            "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F",
            f"{service_sid}:(OI)(CI){permission}",
        ])
        if proc.returncode != 0:
            raise RuntimeError("ACL application failed")


def _apply_install(payload: dict) -> dict:
    source = Path(payload.get("wrapper") or "").resolve()
    executable = Path(payload.get("executable") or "").resolve()
    if source.name != f"WinSW-x64-{WINSW_VERSION}.exe" or not source.is_file() or _sha256(source) != WINSW_SHA256:
        return _result(ok=False, reason="wrapper_verification_failed")
    if executable != Path(sys.executable).resolve() or not _runtime_is_service_safe(executable):
        return _result(ok=False, reason="packaged_runtime_required")
    service_root().mkdir(parents=True, exist_ok=True)
    (machine_data_dir() / "logs").mkdir(parents=True, exist_ok=True)
    installed_wrapper = wrapper_path()
    installed_config = config_path()
    try:
        shutil.copy2(source, installed_wrapper)
        if _sha256(installed_wrapper) != WINSW_SHA256:
            raise RuntimeError("installed wrapper checksum mismatch")
        installed_config.write_text(_build_winsw_xml(executable=executable), encoding="utf-8")
        proc = _run([str(installed_wrapper), "install"])
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or "WinSW install failed")[:200])
        sid = _run(["sc.exe", "sidtype", SERVICE_ID, "unrestricted"])
        if sid.returncode != 0:
            raise RuntimeError("service SID configuration failed")
        _apply_acls()
    except Exception as exc:
        try:
            _run([str(installed_wrapper), "uninstall"], timeout=30)
        except Exception:
            pass
        installed_wrapper.unlink(missing_ok=True)
        installed_config.unlink(missing_ok=True)
        return _result(ok=False, reason="partial_install", detail=str(exc)[:200])
    return _result(ok=True, installed=True, enabled=False, owned=True, config_valid=True, credentials_provisioned=False)


def _apply_simple(operation: str) -> dict:
    if not wrapper_path().is_file() or _sha256(wrapper_path()) != WINSW_SHA256:
        return _result(ok=False, reason="wrapper_verification_failed")
    current = status()
    if current.get("owned") is False:
        return {**current, "ok": False, "reason": "ownership_conflict"}
    if operation == "uninstall" and current.get("running"):
        return {**current, "ok": False, "reason": "service_running"}
    commands = {
        "start": [str(wrapper_path()), "start"],
        "stop": [str(wrapper_path()), "stop"],
        "enable": ["sc.exe", "config", SERVICE_ID, "start=", "auto"],
        "disable": ["sc.exe", "config", SERVICE_ID, "start=", "demand"],
        "uninstall": [str(wrapper_path()), "uninstall"],
    }
    proc = _run(commands[operation])
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:200]
        return {**current, "ok": False, "reason": "access_denied" if "denied" in detail.lower() else "service_command_failed", "detail": detail}
    if operation == "uninstall":
        wrapper_path().unlink(missing_ok=True)
        config_path().unlink(missing_ok=True)
        return _result(ok=True, owned=True, config_valid=True)
    return {**current, "ok": True, "running": operation == "start" or (current.get("running") and operation not in {"stop"}), "enabled": operation == "enable" or (current.get("enabled") and operation not in {"disable"})}


def apply_elevated_request(request_path: Path) -> dict:
    result_path = request_path.with_suffix(".result.json")
    result: dict
    try:
        if platform.system() != "Windows" or not ctypes.windll.shell32.IsUserAnAdmin():
            result = _result(ok=False, reason="elevation_required")
        else:
            payload = json.loads(request_path.read_text(encoding="utf-8"))
            if payload.get("version") != 1 or not re.fullmatch(r"[0-9a-f]{32}", payload.get("nonce", "")):
                result = _result(ok=False, reason="invalid_elevation_request")
            elif payload.get("operation") == "install":
                result = _apply_install(payload)
            elif payload.get("operation") in {"start", "stop", "enable", "disable", "uninstall"}:
                result = _apply_simple(payload["operation"])
            elif payload.get("operation") in {"firewall_enable_lan", "firewall_disable_lan"}:
                result = _apply_firewall_operation(payload)
            else:
                result = _result(ok=False, reason="invalid_elevation_request")
    except Exception as exc:
        result = _result(ok=False, reason="elevated_apply_failed", detail=type(exc).__name__)
    result_path.write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
    return result


def install(*, elevate: bool = True) -> dict:
    if platform.system() != "Windows":
        return _result(supported=False, ok=False, reason=f"unsupported_platform:{platform.system().lower()}")
    if platform.machine().lower() not in {"amd64", "x86_64"}:
        return _result(ok=False, reason="unsupported_architecture")
    if not _runtime_is_service_safe():
        return _result(ok=False, reason="packaged_runtime_required")
    current = status()
    if current.get("installed"):
        return {**current, "ok": current.get("owned") is True and current.get("config_valid") is True}
    try:
        asset = _download_wrapper()
    except Exception as exc:
        return _result(ok=False, reason="wrapper_download_failed", detail=str(exc)[:200])
    return _elevated("install", wrapper=asset, elevate=elevate)


def start(*, elevate: bool = True) -> dict:
    return _elevated("start", elevate=elevate)


def stop(*, elevate: bool = True) -> dict:
    return _elevated("stop", elevate=elevate)


def enable(*, elevate: bool = True) -> dict:
    return _elevated("enable", elevate=elevate)


def disable(*, elevate: bool = True) -> dict:
    return _elevated("disable", elevate=elevate)


def uninstall(*, elevate: bool = True) -> dict:
    current = status()
    if current.get("running"):
        return {**current, "ok": False, "reason": "service_running"}
    return _elevated("uninstall", elevate=elevate)


def firewall_enable_lan(port: int, *, elevate: bool = True) -> dict:
    if platform.system() != "Windows":
        return {"ok": False, "supported": False, "reason": f"unsupported_platform:{platform.system().lower()}"}
    if not elevate:
        return {"ok": False, "supported": True, "reason": "elevation_required", "port": int(port)}
    return _elevated("firewall_enable_lan", arguments={"port": int(port)}, elevate=True)


def firewall_disable_lan(port: int, *, elevate: bool = True) -> dict:
    if platform.system() != "Windows":
        return {"ok": False, "supported": False, "reason": f"unsupported_platform:{platform.system().lower()}"}
    if not elevate:
        return {"ok": False, "supported": True, "reason": "elevation_required", "port": int(port)}
    return _elevated("firewall_disable_lan", arguments={"port": int(port)}, elevate=True)
