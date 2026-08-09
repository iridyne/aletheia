#!/usr/bin/env python3
"""Stability check — run this to decide if backend is stable enough for frontend work.

Usage:
    uv run python scripts/stability_check.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

DATA_DIR = Path(os.environ.get("ALETHEIA_DATA_DIR", Path.home() / ".aletheia"))
LOG_DIR = DATA_DIR / "logs"


def check_process_restarts(hours: int = 24) -> dict:
    """Count how many times the gateway restarted in the last N hours."""
    log_file = LOG_DIR / "errors.log"
    if not log_file.exists():
        return {"status": "unknown", "count": 0}

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()[:19]
    count = 0
    try:
        with log_file.open() as f:
            for line in f:
                if "Scheduler started" in line and line >= cutoff:
                    count += 1
    except OSError:
        pass

    return {
        "status": "critical" if count > 2 else "warning" if count > 0 else "healthy",
        "count": count,
        "threshold": 2,
        "window_hours": hours,
    }


def check_sqlite_lock_timeouts(hours: int = 24) -> dict:
    """Count SQLite lock timeout events."""
    log_file = LOG_DIR / "errors.log"
    if not log_file.exists():
        return {"status": "unknown", "count": 0}

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()[:19]
    cross_proc = 0
    write_lock = 0
    try:
        with log_file.open() as f:
            for line in f:
                if line < cutoff:
                    continue
                if "sqlite_cross_process_write_lock_timeout" in line:
                    cross_proc += 1
                if "sqlite_write_lock_timeout" in line:
                    write_lock += 1
    except OSError:
        pass

    total = cross_proc + write_lock
    return {
        "status": "critical" if total > 5 else "warning" if total > 0 else "healthy",
        "count": total,
        "cross_process": cross_proc,
        "write_lock": write_lock,
        "threshold": 5,
        "window_hours": hours,
    }


def check_memory() -> dict:
    """Check current system memory usage."""
    try:
        with open("/proc/meminfo") as f:
            info = {}
            for line in f:
                if ":" in line:
                    key, val = line.split(":", 1)
                    info[key.strip()] = int(val.split()[0])

        total = info.get("MemTotal", 0)
        available = info.get("MemAvailable", 0)
        used_pct = ((total - available) / total * 100) if total > 0 else 0

        swap_total = info.get("SwapTotal", 0)
        swap_free = info.get("SwapFree", 0)
        swap_used = (swap_total - swap_free) / 1024  # MB

        return {
            "status": "critical" if used_pct > 85 else "warning" if used_pct > 70 else "healthy",
            "used_pct": round(used_pct, 1),
            "swap_used_mb": round(swap_used, 0),
            "threshold_pct": 85,
        }
    except OSError:
        return {"status": "unknown", "used_pct": 0, "swap_used_mb": 0}


def check_job_failure_rate(hours: int = 6) -> dict:
    """Check scheduler job failure rate."""
    log_file = LOG_DIR / "errors.log"
    if not log_file.exists():
        return {"status": "unknown", "failure_rate": 0}

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()[:19]
    failures = 0
    total = 0
    try:
        with log_file.open() as f:
            for line in f:
                if line < cutoff:
                    continue
                if "failed" in line and "consecutive" in line:
                    failures += 1
                if "job" in line and ("completed" in line or "success" in line):
                    total += 1
    except OSError:
        pass

    total += failures  # include failures in total
    rate = (failures / total * 100) if total > 0 else 0
    return {
        "status": "critical" if rate > 10 else "warning" if rate > 5 else "healthy",
        "failure_rate": round(rate, 1),
        "failures": failures,
        "total": total,
        "threshold_pct": 10,
        "window_hours": hours,
    }


def check_error_rate(hours: int = 1) -> dict:
    """Count ERROR level logs per hour."""
    log_file = LOG_DIR / "errors.log"
    if not log_file.exists():
        return {"status": "unknown", "count": 0}

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()[:19]
    count = 0
    try:
        with log_file.open() as f:
            for line in f:
                if line < cutoff:
                    continue
                if " ERROR " in line:
                    count += 1
    except OSError:
        pass

    return {
        "status": "critical" if count > 10 else "warning" if count > 5 else "healthy",
        "count": count,
        "threshold": 10,
        "window_hours": hours,
    }


def check_data_quality(hours: int = 6) -> dict:
    """Check if new features have enough data to be useful."""
    results = {}

    # Opportunities created
    log_file = LOG_DIR / "errors.log"
    if log_file.exists():
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()[:19]
        opp_count = 0
        try:
            with log_file.open() as f:
                for line in f:
                    if line < cutoff:
                        continue
                    if "opportunity" in line.lower() and "created" in line.lower():
                        opp_count += 1
        except OSError:
            pass
        results["opportunities_per_hour"] = round(opp_count / max(hours, 1), 1)

    # SQLite DB size
    db_file = DATA_DIR / "app.db"
    if db_file.exists():
        size_mb = db_file.stat().st_size / (1024 * 1024)
        results["db_size_mb"] = round(size_mb, 1)

    results["status"] = "healthy" if results.get("opportunities_per_hour", 0) > 10 else "warning"
    return results


def main() -> int:
    """Run all stability checks and print report."""
    print("=" * 60)
    print("  Aletheia Backend Stability Report")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 60)
    print()

    checks = {
        "Process Restarts (24h)": check_process_restarts(24),
        "SQLite Lock Timeouts (24h)": check_sqlite_lock_timeouts(24),
        "System Memory": check_memory(),
        "Job Failure Rate (6h)": check_job_failure_rate(6),
        "Error Rate (1h)": check_error_rate(1),
        "Data Quality": check_data_quality(6),
    }

    all_healthy = True
    any_critical = False

    for name, result in checks.items():
        status = result.get("status", "unknown")
        icon = {"healthy": "🟢", "warning": "🟡", "critical": "🔴", "unknown": "⚪"}.get(status, "?")

        if status == "critical":
            any_critical = True
        if status != "healthy":
            all_healthy = False

        print(f"{icon} {name}: {status.upper()}")
        # Print key metrics
        if "count" in result:
            print(f"   count: {result['count']}")
        if "used_pct" in result:
            print(f"   memory used: {result['used_pct']}%")
        if "swap_used_mb" in result:
            print(f"   swap used: {result['swap_used_mb']:.0f} MB")
        if "failure_rate" in result:
            print(f"   failure rate: {result['failure_rate']}%")
        if "cross_process" in result:
            print(f"   cross-process: {result['cross_process']}, write-lock: {result['write_lock']}")
        print()

    print("-" * 60)
    if any_critical:
        print("🔴 VERDICT: UNSTABLE — Fix critical issues before adding frontend")
    elif not all_healthy:
        print("🟡 VERDICT: DEGRADED — Monitor for 2-3 more days before adding frontend")
    else:
        print("🟢 VERDICT: STABLE — Safe to add frontend/API work")
    print("-" * 60)

    return 1 if any_critical else 0


if __name__ == "__main__":
    sys.exit(main())
