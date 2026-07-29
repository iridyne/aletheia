from pathlib import Path
from types import SimpleNamespace
import asyncio
import threading

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
    monkeypatch.setattr(tunnel, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(tunnel.time, "monotonic", iter([0.0, 4.0]).__next__)
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
