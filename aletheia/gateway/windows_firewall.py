"""Windows Firewall onboarding for the LAN-facing dashboard front door.

The user-facing front door is the only port opened.  The backend worker remains
outside this rule even though it is supervised beside the front door.  Changes
are applied only by the UAC-elevated helper in ``windows_machine_service``.
"""

from __future__ import annotations

import base64
import json
import os
import platform
import subprocess
from pathlib import Path

from aletheia.config.loader import get_data_dir
from aletheia.gateway.console_text import decode_console
from aletheia.gateway.pidfile import _no_window_kwargs, _resolve_real_python

RULE_NAME = "AletheiaDashboardLanV1"
RULE_DISPLAY_NAME = "Aletheia Dashboard (local subnet)"
_STATE_VERSION = 1


def _state_path() -> Path:
    return get_data_dir() / "service" / "firewall-lan.json"


def listener_program() -> Path:
    """Return the executable used by supervised uvicorn children on Windows."""
    executable = Path(_resolve_real_python()[0]).resolve()
    if executable.name.lower() == "python.exe":
        windowed = executable.with_name("pythonw.exe")
        if windowed.is_file():
            return windowed
    return executable


def _program_candidates() -> set[str]:
    executable = listener_program()
    candidates = {os.path.normcase(str(executable.resolve()))}
    sibling_name = "python.exe" if executable.name.lower() == "pythonw.exe" else "pythonw.exe"
    sibling = executable.with_name(sibling_name)
    if sibling.is_file():
        candidates.add(os.path.normcase(str(sibling.resolve())))
    return candidates


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _ps_array(values) -> str:
    return "@(" + ",".join(_ps_quote(str(value)) for value in values) + ")"


def _powershell_process(script: str) -> subprocess.CompletedProcess:
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True,
        timeout=60,
        **_no_window_kwargs(),
    )
    proc.stdout = decode_console(proc.stdout)
    proc.stderr = decode_console(proc.stderr)
    return proc


def _run_powershell(script: str) -> None:
    proc = _powershell_process(script)
    if proc.returncode != 0:
        raise RuntimeError("windows_firewall_command_failed")


def _run_powershell_json(script: str):
    proc = _powershell_process(script)
    if proc.returncode != 0:
        raise RuntimeError("windows_firewall_query_failed")
    text = (proc.stdout or "").strip()
    return json.loads(text) if text else []


def _query_rules() -> list[dict]:
    programs = ",".join(_ps_quote(value) for value in sorted(_program_candidates()))
    script = f"""
$ErrorActionPreference = 'Stop'
$programs = @({programs})
$rules = @()
$owned = Get-NetFirewallRule -PolicyStore ActiveStore -Name {_ps_quote(RULE_NAME)} -ErrorAction SilentlyContinue
if ($owned) {{ $rules += @($owned) }}
$blocks = Get-NetFirewallRule -PolicyStore ActiveStore -Enabled True -Direction Inbound -Action Block -ErrorAction SilentlyContinue
foreach ($rule in @($blocks)) {{
    $app = $rule | Get-NetFirewallApplicationFilter
    if ($programs -contains ([string]$app.Program).ToLowerInvariant()) {{ $rules += $rule }}
}}
$rows = foreach ($rule in @($rules | Sort-Object -Property Name -Unique)) {{
    $port = $rule | Get-NetFirewallPortFilter
    $address = $rule | Get-NetFirewallAddressFilter
    $app = $rule | Get-NetFirewallApplicationFilter
    [ordered]@{{
        name = [string]$rule.Name
        display_name = [string]$rule.DisplayName
        action = [string]$rule.Action
        direction = [string]$rule.Direction
        enabled = [string]$rule.Enabled -eq 'True'
        profile = [string]$rule.Profile
        protocol = [string]$port.Protocol
        local_port = @($port.LocalPort | ForEach-Object {{ [string]$_ }})
        remote_address = @($address.RemoteAddress | ForEach-Object {{ [string]$_ }})
        program = [string]$app.Program
    }}
}}
@($rows) | ConvertTo-Json -Depth 5 -Compress
"""
    rows = _run_powershell_json(script)
    if isinstance(rows, dict):
        return [rows]
    return list(rows or [])


def _port_spec_covers(specs, port: int) -> bool:
    for raw in specs or []:
        for item in str(raw).split(","):
            item = item.strip()
            if not item:
                continue
            if item.lower() == "any":
                return True
            if item.isdigit() and int(item) == port:
                return True
            if "-" in item:
                start, _, end = item.partition("-")
                if start.isdigit() and end.isdigit() and int(start) <= port <= int(end):
                    return True
    return False


def _is_tcp(value) -> bool:
    return str(value).lower() in {"tcp", "6"}


def _same_program(value: str, candidates: set[str]) -> bool:
    if not value or value.lower() == "any":
        return False
    return os.path.normcase(os.path.abspath(value)) in candidates


def status(port: int) -> dict:
    if platform.system() != "Windows":
        return {
            "supported": False,
            "configured": False,
            "reason": f"unsupported_platform:{platform.system().lower()}",
            "port": port,
        }
    if not 1 <= int(port) <= 65535:
        return {"supported": True, "configured": False, "reason": "invalid_port", "port": port}
    try:
        rules = _query_rules()
        program = listener_program()
        candidates = _program_candidates()
    except Exception as exc:
        return {
            "supported": True,
            "configured": False,
            "reason": type(exc).__name__,
            "port": port,
        }

    owned = next((item for item in rules if item.get("name") == RULE_NAME), None)
    remote_addresses = {str(item).lower() for item in (owned or {}).get("remote_address", [])}
    profiles = {item.strip().lower() for item in str((owned or {}).get("profile", "")).split(",")}
    rule_valid = bool(
        owned
        and owned.get("enabled") is True
        and str(owned.get("action", "")).lower() == "allow"
        and str(owned.get("direction", "")).lower() == "inbound"
        and _is_tcp(owned.get("protocol"))
        and _port_spec_covers(owned.get("local_port"), port)
        and "localsubnet" in remote_addresses
        and {"private", "public"}.issubset(profiles)
        and _same_program(str(owned.get("program", "")), candidates)
    )
    conflicts = [
        item for item in rules
        if item.get("name") != RULE_NAME
        and item.get("enabled") is True
        and str(item.get("action", "")).lower() == "block"
        and str(item.get("direction", "")).lower() == "inbound"
        and _is_tcp(item.get("protocol"))
        and _port_spec_covers(item.get("local_port"), port)
        and _same_program(str(item.get("program", "")), candidates)
    ]
    return {
        "supported": True,
        "configured": rule_valid and not conflicts,
        "rule_present": owned is not None,
        "conflicting_block_rules": [str(item.get("name")) for item in conflicts],
        "rules": rules,
        "port": int(port),
        "program": str(program),
    }


def _ports_except(port: int) -> list[str]:
    if port == 1:
        return ["0", "2-65535"]
    if port == 65535:
        return ["0-65534"]
    return [f"0-{port - 1}", f"{port + 1}-65535"]


def _read_state() -> dict:
    try:
        payload = json.loads(_state_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return payload if payload.get("version") == _STATE_VERSION else {}


def _write_state(payload: dict) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def apply_enable_lan(port: int) -> dict:
    """Apply the one-port LocalSubnet rule from an already elevated process."""
    port = int(port)
    before = status(port)
    if not before.get("supported"):
        return {**before, "ok": False}
    if before.get("configured"):
        return {**before, "ok": True, "changed": False}

    conflicts_by_name = {
        str(item.get("name")): item
        for item in before.get("rules", [])
        if str(item.get("name")) in set(before.get("conflicting_block_rules", []))
    }
    manual = [
        name for name, item in conflicts_by_name.items()
        if [str(value).lower() for value in item.get("local_port", [])] != ["any"]
    ]
    if manual:
        return {
            **before,
            "ok": False,
            "reason": "conflicting_block_rule_requires_manual_action",
            "manual_rules": manual,
        }

    narrowed = [
        {"name": name, "local_port": item.get("local_port", ["Any"])}
        for name, item in conflicts_by_name.items()
    ]
    names = ",".join(_ps_quote(item["name"]) for item in narrowed)
    narrowed_port = _ports_except(port)
    program = listener_program()
    script = f"""
$ErrorActionPreference = 'Stop'
$changed = @()
try {{
    foreach ($name in @({names})) {{
        $rule = Get-NetFirewallRule -PolicyStore PersistentStore -Name $name -ErrorAction Stop
        $rule | Get-NetFirewallPortFilter | Set-NetFirewallPortFilter -LocalPort {_ps_array(narrowed_port)} | Out-Null
        $changed += $name
    }}
    Get-NetFirewallRule -PolicyStore PersistentStore -Name {_ps_quote(RULE_NAME)} -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule -PolicyStore PersistentStore -Name {_ps_quote(RULE_NAME)} -DisplayName {_ps_quote(RULE_DISPLAY_NAME)} -Direction Inbound -Action Allow -Enabled True -Profile Private,Public -Protocol TCP -LocalPort {port} -RemoteAddress LocalSubnet -Program {_ps_quote(str(program))} | Out-Null
}} catch {{
    Get-NetFirewallRule -PolicyStore PersistentStore -Name {_ps_quote(RULE_NAME)} -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    foreach ($name in $changed) {{
        Get-NetFirewallRule -PolicyStore PersistentStore -Name $name -ErrorAction SilentlyContinue | Get-NetFirewallPortFilter | Set-NetFirewallPortFilter -LocalPort 'Any' | Out-Null
    }}
    throw
}}
"""
    try:
        _run_powershell(script)
        _write_state({
            "version": _STATE_VERSION,
            "port": port,
            "program": str(program),
            "narrowed_rules": narrowed,
        })
        after = status(port)
    except Exception as exc:
        return {**before, "ok": False, "reason": type(exc).__name__}
    if not after.get("configured"):
        apply_disable_lan(port)
        return {**after, "ok": False, "reason": "firewall_verification_failed"}
    return {**after, "ok": True, "changed": True, "narrowed_rules": [item["name"] for item in narrowed]}


def apply_disable_lan(port: int) -> dict:
    """Remove Aletheia's rule and restore any block filters it narrowed."""
    state = _read_state()
    narrowed = state.get("narrowed_rules", []) if state.get("port") in {None, int(port)} else []
    restore_lines = []
    for item in narrowed:
        original = item.get("local_port", ["Any"]) or ["Any"]
        restore_lines.append(
            "Get-NetFirewallRule -PolicyStore PersistentStore -Name "
            f"{_ps_quote(str(item.get('name', '')))} -ErrorAction SilentlyContinue | "
            "Get-NetFirewallPortFilter | Set-NetFirewallPortFilter -LocalPort "
            f"{_ps_array(original)} | Out-Null"
        )
    script = "\n".join([
        "$ErrorActionPreference = 'Stop'",
        f"Get-NetFirewallRule -PolicyStore PersistentStore -Name {_ps_quote(RULE_NAME)} -ErrorAction SilentlyContinue | Remove-NetFirewallRule",
        *restore_lines,
    ])
    try:
        _run_powershell(script)
        _state_path().unlink(missing_ok=True)
    except Exception as exc:
        return {"supported": True, "configured": True, "ok": False, "reason": type(exc).__name__, "port": int(port)}
    return {
        "supported": True,
        "configured": False,
        "ok": True,
        "changed": bool(state),
        "port": int(port),
        "restored_rules": [str(item.get("name")) for item in narrowed],
    }
