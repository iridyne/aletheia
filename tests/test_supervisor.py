"""Tests for the gateway supervisor (Component 5 — process liveness).

The supervisor respawns the inner server on crash with backoff, and gives up after
too many restarts in a window. We inject a fake spawn so no real port is bound.
Pidfile semantics must be preserved (written once, read_pid/is_running still work).
"""

import sqlite3

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations


def setup_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()


def test_supervisor_respawns_on_crash_then_gives_up(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia import _gateway_runner as runner

    # Configure a small restart budget for a fast test.
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.health.supervisor_max_restarts = 3
    cfg.health.supervisor_restart_window_sec = 600
    cfg.health.supervisor_backoff_cap_sec = 0  # no real sleeping in test
    save_config(cfg)

    spawns = {"n": 0}

    def fake_spawn():
        spawns["n"] += 1
        return 1  # always exits nonzero (crash)

    monkeypatch.setattr(runner, "_sleep", lambda s: None)  # don't actually sleep
    result = runner.run_supervised(spawn_fn=fake_spawn)

    # gives up after max_restarts attempts
    assert result["gave_up"] is True
    assert spawns["n"] == 3

    from aletheia.files.jsonl import read_jsonl
    assert any(e.get("event_type") == "gateway_supervisor_gave_up" for e in read_jsonl("audit.jsonl"))


def test_supervisor_stops_cleanly_on_zero_exit(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia import _gateway_runner as runner

    def fake_spawn():
        return 0  # clean shutdown -> supervisor exits without respawning

    monkeypatch.setattr(runner, "_sleep", lambda s: None)
    result = runner.run_supervised(spawn_fn=fake_spawn)
    assert result["gave_up"] is False
    assert result["restarts"] == 0


def test_supervisor_writes_pid_once(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia import _gateway_runner as runner
    from aletheia.gateway import pidfile

    written = {"n": 0}
    monkeypatch.setattr(runner, "write_pid", lambda: written.__setitem__("n", written["n"] + 1))
    monkeypatch.setattr(runner, "_sleep", lambda s: None)
    runner.run_supervised(spawn_fn=lambda: 0)
    assert written["n"] == 1  # supervisor writes pid exactly once


# --- Inner-uvicorn console suppression (no Windows Terminal pop-up) ---

def test_inner_uvicorn_uses_create_no_window_on_windows(tmp_path, monkeypatch):
    """On Windows, the inner uvicorn Popen must pass CREATE_NO_WINDOW so the OS
    does not allocate a console (which Win11 would render via Windows Terminal)."""
    setup_env(tmp_path, monkeypatch)
    from aletheia import _gateway_runner as runner
    from aletheia.config.loader import load_config

    captured = {}

    class FakeProc:
        def wait(self):
            return 0

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return FakeProc()

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(runner.platform, "system", lambda: "Windows", raising=False)

    runner._spawn_server(load_config())

    flags = captured["kwargs"].get("creationflags", 0)
    assert flags & 0x08000000 == 0x08000000  # CREATE_NO_WINDOW


def test_inner_uvicorn_no_creationflags_on_posix(tmp_path, monkeypatch):
    """POSIX path stays byte-identical: no creationflags kwarg."""
    setup_env(tmp_path, monkeypatch)
    from aletheia import _gateway_runner as runner
    from aletheia.config.loader import load_config

    captured = {}

    class FakeProc:
        def wait(self):
            return 0

    def fake_popen(cmd, **kwargs):
        captured["kwargs"] = kwargs
        return FakeProc()

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(runner.platform, "system", lambda: "Linux", raising=False)

    runner._spawn_server(load_config())

    assert "creationflags" not in captured["kwargs"]
