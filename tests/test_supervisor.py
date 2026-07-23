"""Tests for the gateway supervisor (Component 5 — process liveness).

The supervisor respawns the inner server on crash with backoff, and gives up after
too many restarts in a window. We inject a fake spawn so no real port is bound.
Pidfile semantics must be preserved (written once, read_pid/is_running still work).
"""

import sqlite3

import pytest

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
    monkeypatch.setattr(runner, "_cleanup_before_spawn", lambda cfg: None)
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
    monkeypatch.setattr(runner, "_cleanup_before_spawn", lambda cfg: None)
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
    monkeypatch.setattr(runner, "_cleanup_before_spawn", lambda cfg: None)
    runner.run_supervised(spawn_fn=lambda: 0)
    assert written["n"] == 1  # supervisor writes pid exactly once


def test_run_supervised_default_uses_frontdoor_and_internal_backend_port(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia import _gateway_runner as runner
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.operator.enabled = False
    save_config(cfg)

    class FakeFrontdoor:
        pid = 1234

        def poll(self):
            return None

    captured: dict = {}
    monkeypatch.setattr(runner, "write_pid", lambda: None)
    monkeypatch.setattr(runner, "_cleanup_before_spawn", lambda cfg: None)
    monkeypatch.setattr(runner, "_cleanup_backend_before_spawn", lambda cfg, backend_port: captured.setdefault("cleanup_port", backend_port))
    def fake_ensure_frontdoor(cfg, backend_port, proc):  # noqa: ARG001
        captured["frontdoor_port"] = backend_port
        return FakeFrontdoor()

    monkeypatch.setattr(runner, "_ensure_frontdoor_running", fake_ensure_frontdoor)
    monkeypatch.setattr(runner, "_stop_vite_watch", lambda proc: None)
    monkeypatch.setattr(runner, "kill_pid_tree", lambda pid: captured.setdefault("killed_frontdoor", pid))

    def fake_spawn_server(cfg, *, port=None, frontdoor_holder=None, collector_holder=None):
        captured["backend_port"] = port
        captured["frontdoor_holder"] = frontdoor_holder
        captured["frontdoor_proc"] = frontdoor_holder[0] if frontdoor_holder else None
        return 0

    monkeypatch.setattr(runner, "_spawn_server", fake_spawn_server)

    result = runner.run_supervised()

    expected_backend_port = cfg.app.dashboard_port + 1
    assert result["gave_up"] is False
    assert captured["cleanup_port"] == expected_backend_port
    assert captured["frontdoor_port"] == expected_backend_port
    assert captured["backend_port"] == expected_backend_port
    # _spawn_server receives the live frontdoor via the mutable holder.
    assert isinstance(captured["frontdoor_proc"], FakeFrontdoor)
    assert captured["killed_frontdoor"] == 1234


def test_spawn_frontdoor_uses_dashboard_port_and_backend_url(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia import _gateway_runner as runner
    from aletheia.config.loader import load_config

    captured = {}

    class FakeProc:
        pid = 2468

        def poll(self):
            return None

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return FakeProc()

    monkeypatch.setattr(runner, "_resolve_real_python", lambda: ("python-real", {"PYTHON_PATCH": "1"}))
    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)

    cfg = load_config()
    proc = runner._spawn_frontdoor(cfg, backend_port=9876)

    assert proc.pid == 2468
    assert captured["cmd"][:4] == ["python-real", "-m", "uvicorn", "aletheia.gateway.frontdoor:app"]
    assert "--port" in captured["cmd"]
    assert str(cfg.app.dashboard_port) in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("--log-level") + 1] == "info"
    env = captured["kwargs"]["env"]
    assert env["ALETHEIA_BACKEND_PORT"] == "9876"
    assert env["ALETHEIA_BACKEND_BASE_URL"] == "http://127.0.0.1:9876"
    assert env["ALETHEIA_FRONTDOOR_ROUTE_PATH"].endswith("frontdoor-route.json")


def test_spawn_server_can_bind_internal_backend_port(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia import _gateway_runner as runner
    from aletheia.config.loader import load_config

    captured = {}

    class FakeProc:
        pid = 1357

        def poll(self):
            return 0

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return FakeProc()

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(runner, "_resolve_real_python", lambda: ("python-real", {}))

    code = runner._spawn_server(load_config(), port=9876)

    assert code == 0
    assert captured["cmd"][:4] == ["python-real", "-m", "uvicorn", "aletheia.server.app:app"]
    assert "--port" in captured["cmd"]
    assert "9876" in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("--log-level") + 1] == "debug"
    assert captured["kwargs"]["env"]["ALETHEIA_RUNTIME_PORT"] == "9876"
    from aletheia.gateway.frontdoor_route import read_frontdoor_route

    route = read_frontdoor_route()
    assert route is not None
    assert route["backend_base_url"] == "http://127.0.0.1:9876"
    assert route["backend_pid"] == 1357


def test_inner_server_is_restarted_when_port_never_listens(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia import _gateway_runner as runner
    from aletheia.config.loader import load_config

    now = {"value": 0.0}
    killed: list[int] = []

    class FakeProc:
        pid = 12345

        def poll(self):
            return None

    monkeypatch.setattr(runner.time, "monotonic", lambda: now["value"])
    monkeypatch.setattr(runner, "_sleep", lambda seconds: now.__setitem__("value", now["value"] + seconds))
    monkeypatch.setattr(runner, "_SERVER_PORT_STARTUP_GRACE_SEC", 2.0)
    monkeypatch.setattr(runner, "_SERVER_PORT_CHECK_INTERVAL_SEC", 1.0)
    monkeypatch.setattr(runner, "port_in_use", lambda port, host: False)
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: FakeProc())
    monkeypatch.setattr(runner, "kill_pid_tree", lambda pid: killed.append(pid))

    code = runner._spawn_server(load_config())

    assert code == 1
    assert killed == [12345]


def test_inner_server_is_restarted_when_port_is_lost_after_start(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia import _gateway_runner as runner
    from aletheia.config.loader import load_config

    now = {"value": 0.0}
    killed: list[int] = []
    port_checks = {"count": 0}

    class FakeProc:
        pid = 23456

        def poll(self):
            return None

    def fake_port_in_use(port, host):  # noqa: ARG001
        port_checks["count"] += 1
        return port_checks["count"] == 1

    monkeypatch.setattr(runner.time, "monotonic", lambda: now["value"])
    monkeypatch.setattr(runner, "_sleep", lambda seconds: now.__setitem__("value", now["value"] + seconds))
    monkeypatch.setattr(runner, "_SERVER_PORT_LOST_GRACE_SEC", 2.0)
    monkeypatch.setattr(runner, "_SERVER_PORT_CHECK_INTERVAL_SEC", 1.0)
    monkeypatch.setattr(runner, "port_in_use", fake_port_in_use)
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: FakeProc())
    monkeypatch.setattr(runner, "kill_pid_tree", lambda pid: killed.append(pid))

    code = runner._spawn_server(load_config())

    assert code == 1
    assert killed == [23456]


def test_frontdoor_unhealthy_does_not_kill_backend(tmp_path, monkeypatch):
    """A frontdoor fault must restart ONLY the frontdoor — never the backend.

    Regression (§23→§24 cascade): the monitor loop used to kill_pid_tree(backend)
    and return 1 when the frontdoor was dead/port-lost, tearing down the backend
    for a frontdoor hiccup. Now it calls _ensure_frontdoor_running and keeps the
    backend running.
    """
    setup_env(tmp_path, monkeypatch)
    from aletheia import _gateway_runner as runner
    from aletheia.config.loader import load_config

    cfg = load_config()
    backend_port = cfg.app.dashboard_port + 1
    killed: list[int] = []
    ensured: list = []

    class FakeBackend:
        pid = 9001

        def poll(self):
            return None  # backend stays alive throughout

    class FakeOldFrontdoor:
        pid = 8001

        def poll(self):
            return 1  # dead → triggers the frontdoor health branch

    class FakeNewFrontdoor:
        pid = 8002

        def poll(self):
            return None

    def fake_port_in_use(port, host):  # noqa: ARG001
        # Dashboard (frontdoor) port reported NOT listening → frontdoor_port_lost;
        # backend port always listening so the backend liveness check passes.
        return port != cfg.app.dashboard_port

    # Break out of the otherwise-infinite monitor loop on the 2nd frontdoor
    # restart — by then we've proven the loop CONTINUED (did not return 1) after
    # the first frontdoor fault, which is the whole point of the decoupling.
    def fake_ensure_frontdoor(cfg, port, proc):  # noqa: ARG001
        ensured.append(getattr(proc, "pid", None))
        if len(ensured) >= 2:
            raise KeyboardInterrupt  # clean escape hatch for the test
        return FakeNewFrontdoor()

    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **kw: FakeBackend())
    monkeypatch.setattr(runner, "kill_pid_tree", lambda pid: killed.append(pid))
    monkeypatch.setattr(runner, "port_in_use", fake_port_in_use)
    monkeypatch.setattr(runner, "_ensure_frontdoor_running", fake_ensure_frontdoor)
    monkeypatch.setattr(runner, "_sleep", lambda s: None)
    monkeypatch.setattr(runner, "_resolve_real_python", lambda: ("python", {}))
    monkeypatch.setattr(runner, "_publish_frontdoor_route", lambda *a, **kw: None)

    holder = [FakeOldFrontdoor()]
    with pytest.raises(KeyboardInterrupt):
        runner._spawn_server(cfg, port=backend_port, frontdoor_holder=holder)

    # The backend was NEVER kill-signalled for the frontdoor fault (the §23→§24
    # cascade). The old code did kill_pid_tree(9001)+return 1 on frontdoor death.
    assert 9001 not in killed
    # The frontdoor health branch fired (loop continued past the first fault and
    # called _ensure_frontdoor_running again on the next iteration).
    assert len(ensured) >= 2
    assert ensured[0] == 8001  # first restart handed the dead old frontdoor


# --- Inner-uvicorn console suppression (no Windows Terminal pop-up) ---

def test_inner_uvicorn_uses_create_no_window_on_windows(tmp_path, monkeypatch):
    """On Windows, the inner uvicorn Popen must pass CREATE_NO_WINDOW so the OS
    does not allocate a console (which Win11 would render via Windows Terminal)."""
    setup_env(tmp_path, monkeypatch)
    from aletheia import _gateway_runner as runner
    from aletheia.config.loader import load_config

    captured = {}

    class FakeProc:
        def poll(self):
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
        def poll(self):
            return 0

    def fake_popen(cmd, **kwargs):
        captured["kwargs"] = kwargs
        return FakeProc()

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(runner.platform, "system", lambda: "Linux", raising=False)

    runner._spawn_server(load_config())

    assert "creationflags" not in captured["kwargs"]
