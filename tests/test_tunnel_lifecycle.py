import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
import asyncio
import threading

import pytest

from aletheia.gateway import tunnel


def _config(tmp_path: Path):
    return SimpleNamespace(
        tunnel=SimpleNamespace(
            enabled=True,
            tunnel_id="tunnel-1",
            hostname="aletheia.example.test",
            cloudflared_path="",
            credentials_path=str(tmp_path / "credentials.json"),
            metrics_port=0,
        ),
        app=SimpleNamespace(dashboard_port=8787),
    )


def test_legacy_pidfile_never_proves_process_ownership(tmp_path, monkeypatch):
    pidfile = tmp_path / "tunnel.pid"
    pidfile.write_text("4242", encoding="utf-8")
    killed: list[tuple[int, int]] = []

    monkeypatch.setattr(tunnel, "_pid_path", lambda: pidfile)
    monkeypatch.setattr(tunnel, "_pid_alive", lambda pid: pid == 4242)
    monkeypatch.setattr(tunnel.os, "kill", lambda pid, signal: killed.append((pid, signal)))

    assert tunnel.is_tunnel_running() is False
    result = tunnel.stop_tunnel()

    assert result["ok"] is False
    assert result["status"] == "ownership_mismatch"
    assert killed == []
    assert not pidfile.exists()


def test_reused_pid_is_not_reported_or_terminated(tmp_path, monkeypatch):
    pidfile = tmp_path / "tunnel.pid"
    pidfile.write_text(
        '{"config_path":"/data/tunnel.yml","executable":"/usr/bin/cloudflared",'
        '"pid":4242,"version":1}',
        encoding="utf-8",
    )
    killed: list[tuple[int, int]] = []

    monkeypatch.setattr(tunnel, "_pid_path", lambda: pidfile)
    monkeypatch.setattr(tunnel, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(tunnel, "_process_command_line", lambda pid: "/usr/bin/unrelated")
    monkeypatch.setattr(tunnel.os, "kill", lambda pid, signal: killed.append((pid, signal)))

    assert tunnel.is_tunnel_running() is False
    result = tunnel.stop_tunnel()

    assert result["status"] == "ownership_mismatch"
    assert killed == []


def test_start_status_stop_acceptance_uses_verified_process_identity(tmp_path, monkeypatch):
    pidfile = tmp_path / "tunnel.pid"
    config_path = tmp_path / "tunnel.yml"
    cfg = _config(tmp_path)
    command = (
        f"/usr/bin/cloudflared tunnel --protocol http2 --config {config_path} run"
    )
    killed: list[tuple[int, int]] = []

    class FakeProcess:
        pid = 4242
        returncode = None

        def poll(self):
            return None

        def terminate(self):
            raise AssertionError("verified process should not be terminated during start")

    monkeypatch.setattr(tunnel, "_pid_path", lambda: pidfile)
    monkeypatch.setattr(tunnel, "_config_path", lambda: config_path)
    monkeypatch.setattr(tunnel, "load_config", lambda: cfg)
    monkeypatch.setattr(tunnel, "find_cloudflared", lambda: "/usr/bin/cloudflared")
    monkeypatch.setattr(tunnel.platform, "system", lambda: "Linux")
    monkeypatch.setattr(tunnel.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(tunnel, "_process_command_line", lambda pid: command)
    monkeypatch.setattr(tunnel, "_pid_alive", lambda pid: not killed)  # dies on SIGTERM
    monkeypatch.setattr(tunnel.time, "monotonic", iter([0.0, 4.0, 5.0, 6.0, 7.0]).__next__)
    monkeypatch.setattr(tunnel.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(tunnel.os, "kill", lambda pid, signal: killed.append((pid, signal)))

    started = tunnel.start_tunnel()
    assert started == {
        "ok": True,
        "status": "started",
        "pid": 4242,
        "hostname": "aletheia.example.test",
    }
    assert tunnel.tunnel_status()["ownership_verified"] is True

    stopped = tunnel.stop_tunnel()
    assert stopped["ok"] is True
    assert stopped["status"] == "stopped"
    assert killed[0] == (4242, 15)
    assert not pidfile.exists()


def test_tunnel_api_runs_blocking_lifecycle_off_event_loop(monkeypatch):
    from aletheia.server.routes import settings

    entered = threading.Event()
    release = threading.Event()

    def blocking_start():
        entered.set()
        release.wait(timeout=2)
        return {"ok": True, "status": "started"}

    monkeypatch.setattr(tunnel, "start_tunnel", blocking_start)

    async def exercise():
        task = asyncio.create_task(settings.start_tunnel_api())
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(0.001)
        assert entered.is_set()
        loop_remained_responsive = True
        await asyncio.sleep(0)
        release.set()
        result = await task
        return loop_remained_responsive, result

    responsive, result = asyncio.run(exercise())
    assert responsive is True
    assert result["status"] == "started"


# --- #1673: "stopped" only once the process is observed dead; keep unproven records ---


def _owned_record(tmp_path, monkeypatch, pid=4242):
    pidfile = tmp_path / "tunnel.pid"
    config_path = tmp_path / "tunnel.yml"
    pidfile.write_text(json.dumps({
        "version": 1, "pid": pid, "executable": "/usr/bin/cloudflared",
        "config_path": str(config_path),
    }), encoding="utf-8")
    monkeypatch.setattr(tunnel, "_pid_path", lambda: pidfile)
    monkeypatch.setattr(tunnel.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        tunnel, "_process_command_line",
        lambda _pid: f"/usr/bin/cloudflared tunnel --protocol http2 --config {config_path} run",
    )
    clock = {"t": 0.0}
    monkeypatch.setattr(tunnel.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(tunnel.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    return pidfile


def test_stop_reports_failure_and_keeps_record_when_signal_raises(tmp_path, monkeypatch):
    pidfile = _owned_record(tmp_path, monkeypatch)
    monkeypatch.setattr(tunnel, "_pid_alive", lambda pid: True)

    def deny(pid, sig):
        raise PermissionError("operation not permitted")

    monkeypatch.setattr(tunnel.os, "kill", deny)
    result = tunnel.stop_tunnel()
    assert result["ok"] is False and result["status"] == "stop_failed"
    assert pidfile.exists()


def test_stop_reports_failure_when_process_survives_sigkill(tmp_path, monkeypatch):
    pidfile = _owned_record(tmp_path, monkeypatch)
    sent: list[int] = []
    monkeypatch.setattr(tunnel, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(tunnel.os, "kill", lambda pid, sig: sent.append(sig))
    result = tunnel.stop_tunnel()
    assert sent == [15, 9]
    assert result["status"] == "stop_failed"
    assert pidfile.exists()


def test_stop_reports_failure_when_windows_taskkill_fails(tmp_path, monkeypatch):
    pidfile = _owned_record(tmp_path, monkeypatch)
    monkeypatch.setattr(tunnel.platform, "system", lambda: "Windows")
    monkeypatch.setattr(tunnel, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(
        tunnel.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 128, b"", b"Access is denied."),
    )
    result = tunnel.stop_tunnel()
    assert result["status"] == "stop_failed"
    assert pidfile.exists()


def test_unreadable_command_line_keeps_record_and_retry_stops(tmp_path, monkeypatch):
    pidfile = _owned_record(tmp_path, monkeypatch)
    readable = tunnel._process_command_line
    sent: list[int] = []
    monkeypatch.setattr(tunnel, "_pid_alive", lambda pid: not sent)
    monkeypatch.setattr(tunnel.os, "kill", lambda pid, sig: sent.append(sig))
    monkeypatch.setattr(tunnel, "_process_command_line", lambda pid: None)

    first = tunnel.stop_tunnel()
    assert first["ok"] is False and first["status"] == "ownership_unverified"
    assert sent == [] and pidfile.exists()

    monkeypatch.setattr(tunnel, "_process_command_line", readable)
    second = tunnel.stop_tunnel()
    assert second == {"ok": True, "status": "stopped", "pid": 4242}
    assert sent == [15] and not pidfile.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX ps-based identity probe")
def test_stop_verifies_a_real_child_process_exit(tmp_path, monkeypatch):
    config_path = tmp_path / "tunnel.yml"
    proc = subprocess.Popen([
        sys.executable, "-c", "import time; time.sleep(60)",
        "tunnel", "--config", str(config_path), "run",
    ])
    try:
        pidfile = tmp_path / "tunnel.pid"
        monkeypatch.setattr(tunnel, "_pid_path", lambda: pidfile)
        tunnel._write_tunnel_record(pid=proc.pid, executable=sys.executable, config_path=config_path)
        assert tunnel.is_tunnel_running() is True
        result = tunnel.stop_tunnel()
        assert result["status"] == "stopped"
        assert not pidfile.exists()
    finally:
        proc.kill()
        proc.wait()


def test_gateway_stop_attempts_recorded_tunnel_and_surfaces_failure(monkeypatch, caplog):
    from aletheia.gateway import lifecycle

    monkeypatch.setattr(tunnel, "stop_tunnel", lambda: {"ok": False, "status": "stop_failed", "reason": "still running"})
    monkeypatch.setattr(tunnel, "is_tunnel_running", lambda: False)  # identity unreadable
    with caplog.at_level("WARNING", logger="aletheia.gateway.lifecycle"):
        lifecycle._auto_stop_tunnel()
    assert "tunnel stop not verified (stop_failed)" in caplog.text


def test_tunnel_stop_api_returns_conflict_when_stop_unverified(monkeypatch):
    from fastapi import HTTPException
    from aletheia.server.routes import settings

    monkeypatch.setattr(tunnel, "stop_tunnel", lambda: {"ok": False, "status": "stop_failed", "reason": "still running"})
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(settings.stop_tunnel_api())
    assert excinfo.value.status_code == 409
