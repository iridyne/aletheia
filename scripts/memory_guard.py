#!/usr/bin/env python3
"""Memory guard — lightweight monitor for the aletheia service group.

Reads /proc directly (no psutil dependency). When system memory exceeds
configurable thresholds, emits structured alerts to the audit log and,
at the critical threshold, asks the aletheia gateway to restart so the
supervisor respawns a fresh process group before OOM kills the box.

Run from cron every 2 minutes:

    */2 * * * * /home/ubuntu/aletheia/.venv/bin/python /home/ubuntu/aletheia/scripts/memory_guard.py >> /home/ubuntu/.aletheia/logs/memory_guard.log 2>&1
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# --- Configuration -----------------------------------------------------------
# Thresholds as a fraction of total memory (0.0–1.0).
WARN_LEVEL = 0.75   # emit a warning audit event
CRITICAL_LEVEL = 0.85  # trigger a gateway restart via supervisor
# Process name patterns that count toward "aletheia" memory.
ALETHEIA_PATTERNS = (
    "aletheia._gateway_runner",
    "aletheia.data.collector_runner",
    "uvicorn aletheia",
)
# Path to the aletheia data dir (audit log sink).
DATA_DIR = Path(os.environ.get("ALETHEIA_DATA_DIR", Path.home() / ".aletheia"))
LOG_DIR = DATA_DIR / "logs"
AUDIT_LOG = LOG_DIR / "audit.jsonl"
RESTART_LOCK = DATA_DIR / ".memory_guard_restart"
# Don't restart more than once per RESTART_COOLDOWN_SEC even if memory stays high.
RESTART_COOLDOWN_SEC = 600  # 10 minutes


def read_meminfo() -> dict[str, int]:
    """Parse /proc/meminfo into a dict of kB values."""
    info: dict[str, int] = {}
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                m = re.match(r"(\w+):\s+(\d+)\s+kB", line)
                if m:
                    info[m.group(1)] = int(m.group(2))
    except OSError:
        pass
    return info


def get_memory_state() -> dict:
    """Return a snapshot of current memory usage."""
    info = read_meminfo()
    total = info.get("MemTotal", 0)
    available = info.get("MemAvailable", 0)
    free = info.get("MemFree", 0)
    buffers = info.get("Buffers", 0)
    cached = info.get("Cached", 0)
    swap_total = info.get("SwapTotal", 0)
    swap_free = info.get("SwapFree", 0)
    if total == 0:
        return {"ok": False}
    used = total - available
    return {
        "ok": True,
        "total_kb": total,
        "available_kb": available,
        "used_kb": used,
        "free_kb": free,
        "buffers_kb": buffers,
        "cached_kb": cached,
        "swap_total_kb": swap_total,
        "swap_used_kb": swap_total - swap_free,
        "used_fraction": used / total,
        "swap_fraction": (swap_total - swap_free) / swap_total if swap_total else 0.0,
    }


def get_aletheia_processes() -> list[dict]:
    """Find aletheia-related processes via /proc. No psutil needed."""
    procs: list[dict] = []
    pid_dirs = (d for d in os.listdir("/proc") if d.isdigit())
    for pid_str in sorted(pid_dirs):
        pid = int(pid_str)
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode(
                "utf-8", errors="replace"
            ).strip()
        except OSError:
            continue
        if not cmdline:
            continue
        if any(pat in cmdline for pat in ALETHEIA_PATTERNS):
            try:
                status = Path(f"/proc/{pid}/status").read_text(encoding="utf-8", errors="replace")
                rss_match = re.search(r"VmRSS:\s+(\d+)\s+kB", status)
                name_match = re.search(r"Name:\s+(.+)", status)
                rss_kb = int(rss_match.group(1)) if rss_match else 0
                name = name_match.group(1).strip() if name_match else "?"
                procs.append({
                    "pid": pid,
                    "name": name,
                    "rss_kb": rss_kb,
                    "cmdline": cmdline[:120],
                })
            except OSError:
                continue
    return procs


def write_audit(event: dict) -> None:
    """Append a structured event to the audit log."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        event["observed_at"] = datetime.now(timezone.utc).isoformat()
        with AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"memory_guard: failed to write audit: {e}", file=sys.stderr)


def request_restart(reason: str) -> None:
    """Restart the aletheia service via systemd.

    Uses nohup so the restart (which kills this process group) does not
    leave a defunct cron child. The beacon file records the event for audit
    and enforces the cooldown.
    """
    try:
        RESTART_LOCK.write_text(
            json.dumps({
                "requested_at": datetime.now(timezone.utc).isoformat(),
                "reason": reason,
            }),
            encoding="utf-8",
        )
        subprocess.Popen(
            ["sudo", "systemctl", "restart", "aletheia.service"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        print(f"memory_guard: restart triggered ({reason})")
    except OSError as e:
        print(f"memory_guard: failed to request restart: {e}", file=sys.stderr)


def recent_restart_cooldown_active() -> bool:
    """True if a restart was requested within the cooldown window."""
    try:
        if not RESTART_LOCK.exists():
            return False
        payload = json.loads(RESTART_LOCK.read_text(encoding="utf-8"))
        requested_at = datetime.fromisoformat(payload["requested_at"])
        age = (datetime.now(timezone.utc) - requested_at).total_seconds()
        return age < RESTART_COOLDOWN_SEC
    except (OSError, ValueError, KeyError):
        return False


def main() -> int:
    state = get_memory_state()
    if not state["ok"]:
        print("memory_guard: could not read /proc/meminfo", file=sys.stderr)
        return 1

    used_frac = state["used_fraction"]
    procs = get_aletheia_processes()
    aletheia_rss_kb = sum(p["rss_kb"] for p in procs)

    # Human-readable summary
    total_mb = state["total_kb"] / 1024
    used_mb = state["used_kb"] / 1024
    avail_mb = state["available_kb"] / 1024
    aletheia_mb = aletheia_rss_kb / 1024
    print(
        f"memory_guard: sys {used_mb:.0f}/{total_mb:.0f} MB ({used_frac:.0%}), "
        f"aletheia {aletheia_mb:.0f} MB ({len(procs)} procs)"
    )

    # Threshold logic
    if used_frac >= CRITICAL_LEVEL:
        write_audit({
            "actor": "system",
            "source": "memory_guard",
            "event_type": "infra_event",
            "entity_id": "memory_pressure_critical",
            "severity": "critical",
            "message": (
                f"System memory at {used_frac:.0%} — "
                f"critical threshold {CRITICAL_LEVEL:.0%}. "
                f"aletheia using {aletheia_mb:.0f} MB across {len(procs)} processes. "
                "Requesting gateway restart to release memory before OOM."
            ),
            "memory": {
                "used_fraction": round(used_frac, 4),
                "used_mb": round(used_mb, 1),
                "total_mb": round(total_mb, 1),
                "aletheia_mb": round(aletheia_mb, 1),
                "process_count": len(procs),
            },
        })
        if not recent_restart_cooldown_active():
            request_restart(f"memory_critical_{used_frac:.0%}")
        else:
            print("memory_guard: restart cooldown active, skipping")
        return 2  # non-zero but distinct from error

    if used_frac >= WARN_LEVEL:
        write_audit({
            "actor": "system",
            "source": "memory_guard",
            "event_type": "infra_event",
            "entity_id": "memory_pressure_warning",
            "severity": "warning",
            "message": (
                f"System memory at {used_frac:.0%} — "
                f"warning threshold {WARN_LEVEL:.0%}. "
                f"aletheia using {aletheia_mb:.0f} MB across {len(procs)} processes."
            ),
            "memory": {
                "used_fraction": round(used_frac, 4),
                "used_mb": round(used_mb, 1),
                "total_mb": round(total_mb, 1),
                "aletheia_mb": round(aletheia_mb, 1),
                "process_count": len(procs),
            },
        })
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
