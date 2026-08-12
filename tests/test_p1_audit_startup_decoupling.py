"""P1 regression: Gateway startup must not block when .audit.lock is held or slow.

Tests:
1. Ordinary write_audit() waits for lock release instead of dropping a
   safety-relevant record under contention.
2. init_scheduler()'s audit mirror is dispatched to a background thread and does
   not block the function's return even when the audit write itself is slow.
"""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_logs(tmp_path, monkeypatch):
    """Redirect audit writes to a temp dir and reset module-level state."""
    import aletheia.files.jsonl as jmod

    monkeypatch.setattr(jmod, "_last_audit_hash", None)
    monkeypatch.setattr(jmod, "_log_dir", lambda: tmp_path)
    yield


# ---------------------------------------------------------------------------
# write_audit with a contested lock
# ---------------------------------------------------------------------------

def _hold_audit_lock(tmp_path: Path, held_event: threading.Event, release_event: threading.Event) -> None:
    """Hold .audit.lock until release_event is set (simulates slow recovery scan)."""
    import fcntl

    lock_path = tmp_path / ".audit.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lf:
        lf.write(b"\0")
        lf.flush()
        lf.seek(0)
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        held_event.set()
        release_event.wait(timeout=10)
        fcntl.flock(lf.fileno(), fcntl.LOCK_UN)






@pytest.mark.skipif(
    __import__("os").name == "nt", reason="POSIX-only lock test"
)
def test_write_audit_waits_for_lock_and_preserves_record(tmp_path):
    """Contention may delay an audit write, but must not discard it."""
    import aletheia.files.jsonl as jmod

    held = threading.Event()
    release = threading.Event()
    holder = threading.Thread(
        target=_hold_audit_lock, args=(tmp_path, held, release), daemon=True
    )
    holder.start()
    assert held.wait(timeout=5)
    releaser = threading.Timer(0.2, release.set)
    releaser.start()

    start = time.monotonic()
    result = jmod.write_audit(
        {"event_type": "startup_test", "timestamp": "2026-01-01T00:00:00"}
    )
    elapsed = time.monotonic() - start

    holder.join(timeout=2)
    releaser.cancel()

    assert elapsed >= 0.15
    assert result is True
    assert jmod.verify_audit_chain()["records"] == 1


def test_write_audit_succeeds_normally_without_contention(tmp_path):
    """write_audit must still work correctly when the lock is uncontested."""
    from aletheia.files.jsonl import write_audit, verify_audit_chain

    ok = write_audit({"event_type": "gateway_started", "timestamp": "2026-01-01T00:00:00"})
    assert ok is True

    result = verify_audit_chain()
    assert result["valid"] is True
    assert result["records"] == 1


# ---------------------------------------------------------------------------
# init_scheduler does not block on audit mirror
# ---------------------------------------------------------------------------

def test_init_scheduler_audit_mirror_is_background(tmp_path, monkeypatch):
    """init_scheduler() must return without waiting for the audit chain scan."""
    import aletheia.agent_runtime.scheduler as sched_mod

    # Track whether _mirror_universe_scanner_recovery_audits was called
    mirror_called = threading.Event()
    mirror_may_proceed = threading.Event()

    original_mirror = sched_mod._mirror_universe_scanner_recovery_audits

    def _slow_mirror():
        mirror_called.set()
        mirror_may_proceed.wait(timeout=5)
        return True

    monkeypatch.setattr(sched_mod, "_mirror_universe_scanner_recovery_audits", _slow_mirror)
    # Pretend startup recovery is done so the mirror branch is entered
    monkeypatch.setattr(sched_mod, "_universe_scanner_startup_recovery_done", True)
    monkeypatch.setattr(sched_mod, "_universe_scanner_audit_mirror_done", False)
    monkeypatch.setattr(sched_mod, "_universe_scanner_startup_cutoff", "2026-01-01T00:00:00Z")
    # Don't actually start the full scheduler
    monkeypatch.setattr(sched_mod, "_scheduler", object())  # truthy → early return

    start = time.monotonic()
    sched_mod.init_scheduler()
    elapsed = time.monotonic() - start

    # init_scheduler must return well before the slow mirror finishes
    assert elapsed < 1.0, (
        f"init_scheduler blocked for {elapsed:.2f}s — audit mirror is not background"
    )

    # Let the mirror thread finish cleanly
    mirror_may_proceed.set()
    # Give the daemon thread a moment to exit
    time.sleep(0.1)

    monkeypatch.setattr(sched_mod, "_mirror_universe_scanner_recovery_audits", original_mirror)
