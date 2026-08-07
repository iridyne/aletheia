"""Tests for the gateway supervisor (Component 5 — process liveness).

The supervisor respawns the inner server on crash with backoff, and gives up after
too many restarts in a window. We inject a fake spawn so no real port is bound.
Pidfile semantics must be preserved (written once, read_pid/is_running still work).
"""

import sqlite3

import pytest

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations


class _FakeNotifySocket:
    def __init__(self):
        self.address = None
        self.payload = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def connect(self, address):
        self.address = address

    def sendall(self, payload):
        self.payload = payload


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


def test_sd_notify_uses_standard_library_for_abstract_systemd_socket(monkeypatch):
    from aletheia import _gateway_runner as runner

    notifier = _FakeNotifySocket()
    socket_args = {}

    def fake_socket(family, kind):
        socket_args.update(family=family, kind=kind)
        return notifier

    monkeypatch.setenv("NOTIFY_SOCKET", "@aletheia-ready")
    monkeypatch.setattr(runner.socket, "socket", fake_socket)

    runner._emit_sd_notify()

    assert socket_args == {
        "family": runner.socket.AF_UNIX,
        "kind": runner.socket.SOCK_DGRAM,
    }
    assert notifier.address == "\0aletheia-ready"
    assert notifier.payload == b"READY=1"


def test_sd_notify_without_systemd_environment_is_a_noop(monkeypatch):
    from aletheia import _gateway_runner as runner

    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    monkeypatch.setattr(
        runner.socket,
        "socket",
        lambda *_args: pytest.fail("socket must not be opened without NOTIFY_SOCKET"),
    )

    runner._emit_sd_notify()


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
    monkeypatch.setattr(runner, "_spawn_collector_process", lambda _cfg: None)
    monkeypatch.setattr(runner, "_stop_vite_watch", lambda proc: None)
    monkeypatch.setattr(runner, "kill_pid_tree", lambda pid: captured.setdefault("killed_frontdoor", pid))

    def fake_spawn_server(
        cfg, *, port=None, frontdoor_holder=None, collector_holder=None, backend_holder=None,
    ):
        captured["backend_port"] = port
        captured["frontdoor_holder"] = frontdoor_holder
        captured["frontdoor_proc"] = frontdoor_holder[0] if frontdoor_holder else None
        captured["collector_holder"] = collector_holder
        captured["collector_proc"] = collector_holder[0] if collector_holder else None
        captured["backend_holder"] = backend_holder
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
    assert isinstance(captured["collector_holder"], list)
    assert captured["collector_proc"] is None  # collector waits for backend readiness
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
    assert captured["cmd"] == ["python-real", "-m", "aletheia.gateway.frontdoor_runner"]
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


def test_collector_starts_only_after_backend_listens(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia import _gateway_runner as runner
    from aletheia.config.loader import load_config

    class FakeBackend:
        pid = 12345

        def poll(self):
            return None

    holder = [None]
    started = []
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: FakeBackend())
    monkeypatch.setattr(runner, "port_in_use", lambda *args: True)
    monkeypatch.setattr(runner, "_spawn_collector_process", lambda cfg: started.append(cfg) or object())
    monkeypatch.setattr(runner, "_sleep", lambda _seconds: (_ for _ in ()).throw(KeyboardInterrupt))

    with pytest.raises(KeyboardInterrupt):
        runner._spawn_server(load_config(), collector_holder=holder)

    assert len(started) == 1
    assert holder[0] is not None


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


# --- SIGTERM/SIGINT controlled shutdown (issue #355) -------------------------
#
# A managed stop (launchd/systemd) delivers SIGTERM to the supervisor. The
# default POSIX disposition kills it without unwinding Python frames, so the
# finally-block teardown never runs and the collector/frontdoor/backend children
# are reparented to PID 1 (orphaned). The supervisor must instead catch the
# signal, route it through the single idempotent cleanup path, remove its pid
# file, and record a durable system-actor event.


def test_sigterm_triggers_controlled_shutdown_and_records_system_event(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    import os
    import signal
    import time

    from aletheia import _gateway_runner as runner
    from aletheia.gateway import pidfile

    monkeypatch.setattr(runner, "_cleanup_before_spawn", lambda cfg: None)

    def fake_spawn():
        # Stand in for the blocking server lifetime, then take a managed SIGTERM.
        # Our installed handler re-arms SIG_IGN and raises KeyboardInterrupt, so
        # this returns via the supervisor's cleanup path rather than exit code 0.
        # Guard: only self-signal once the supervisor's handler is installed —
        # otherwise the default disposition would kill the test runner instead of
        # failing this assertion.
        assert callable(signal.getsignal(signal.SIGTERM)), "SIGTERM handler not installed"
        os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(2)  # interrupted by the pending SIGTERM
        return 0  # unreachable

    result = runner.run_supervised(spawn_fn=fake_spawn)

    # Unwound cleanly through the finally (no traceback escapes run_supervised).
    assert result.get("stopped_by_signal") is True

    # PID file removed so status/stop don't act on a stale live pid.
    assert pidfile.read_pid() is None
    assert not pidfile.pid_path().exists()

    # Durable event, system actor (never a fabricated human), names the signal.
    from aletheia.files.jsonl import read_jsonl
    events = [
        e for e in read_jsonl("audit.jsonl")
        if e.get("event_type") == "gateway_signal_shutdown"
    ]
    assert len(events) == 1
    assert events[0]["actor"] == "system"
    assert "SIGTERM" in events[0]["message"]


def test_repeated_shutdown_signals_are_idempotent(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    import os
    import signal
    import time

    from aletheia import _gateway_runner as runner
    from aletheia.gateway import pidfile

    monkeypatch.setattr(runner, "_cleanup_before_spawn", lambda cfg: None)

    # A second SIGTERM arrives mid-cleanup (signals are already neutralized by
    # then). It must be dropped: no crash, no duplicate event.
    def noisy_stop_vite(proc):  # runs inside the finally, after SIG_IGN is set
        os.kill(os.getpid(), signal.SIGTERM)

    monkeypatch.setattr(runner, "_stop_vite_watch", noisy_stop_vite)

    def fake_spawn():
        assert callable(signal.getsignal(signal.SIGTERM)), "SIGTERM handler not installed"
        os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(2)
        return 0

    result = runner.run_supervised(spawn_fn=fake_spawn)
    assert result.get("stopped_by_signal") is True

    from aletheia.files.jsonl import read_jsonl
    events = [
        e for e in read_jsonl("audit.jsonl")
        if e.get("event_type") == "gateway_signal_shutdown"
    ]
    assert len(events) == 1  # re-delivered SIGTERM added nothing
    assert pidfile.read_pid() is None


def test_abnormal_restart_path_records_no_signal_event(tmp_path, monkeypatch):
    """A crash/give-up (no signal) must not fabricate a signal-shutdown event."""
    setup_env(tmp_path, monkeypatch)
    from aletheia import _gateway_runner as runner
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.health.supervisor_max_restarts = 2
    cfg.health.supervisor_restart_window_sec = 600
    cfg.health.supervisor_backoff_cap_sec = 0
    save_config(cfg)

    monkeypatch.setattr(runner, "_sleep", lambda s: None)
    monkeypatch.setattr(runner, "_cleanup_before_spawn", lambda cfg: None)

    result = runner.run_supervised(spawn_fn=lambda: 1)
    assert result["gave_up"] is True
    assert "stopped_by_signal" not in result

    from aletheia.files.jsonl import read_jsonl
    assert not any(
        e.get("event_type") == "gateway_signal_shutdown"
        for e in read_jsonl("audit.jsonl")
    )


# --- Acceptance: subprocess proves every owned descendant exits on SIGTERM ---

_SUBPROCESS_DRIVER = r'''
import json
import os
import subprocess
import sys
from pathlib import Path

DATA = Path(os.environ["ALETHEIA_DATA_DIR"])
PIDS = DATA / "children.json"

from aletheia import _gateway_runner as runner
from aletheia.config.loader import load_config, ensure_data_dirs
import aletheia.gateway.operator_shell as opshell

ensure_data_dirs()

children = {}


def _sleeper():
    # A benign, long-lived owned child standing in for a real subprocess.
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])


def _maybe_dump():
    if {"frontdoor", "collector", "operator", "backend"} <= set(children):
        PIDS.write_text(json.dumps(children))


def fake_frontdoor(cfg, backend_port):
    p = _sleeper(); children["frontdoor"] = p.pid; _maybe_dump(); return p


def fake_ensure_frontdoor(cfg, backend_port, proc):
    if proc is not None and proc.poll() is None:
        return proc
    return fake_frontdoor(cfg, backend_port)


def fake_collector(cfg):
    p = _sleeper(); children["collector"] = p.pid; _maybe_dump(); return p


class FakeOperator:
    def __init__(self, cfg):
        self._p = None

    def start(self):
        self._p = _sleeper(); children["operator"] = self._p.pid; _maybe_dump()

    def stop(self):
        if self._p is not None:
            try:
                runner.kill_pid_tree(self._p.pid)
            except Exception:
                pass


def fake_spawn_server(cfg, *, port=None, frontdoor_holder=None, collector_holder=None, backend_holder=None):
    p = _sleeper(); children["backend"] = p.pid
    if backend_holder is not None:
        backend_holder[0] = p
    if collector_holder is not None and collector_holder[0] is None:
        collector_holder[0] = fake_collector(cfg)
    _maybe_dump()
    return p.wait()  # blocks until SIGTERM unwinds the supervisor


runner.bootstrap = load_config
runner._cleanup_before_spawn = lambda cfg: None
runner._cleanup_backend_before_spawn = lambda cfg, backend_port: None
runner._publish_frontdoor_route = lambda *a, **k: None
runner._ensure_frontdoor_running = fake_ensure_frontdoor
runner._spawn_frontdoor = fake_frontdoor
runner._spawn_collector_process = fake_collector
runner._spawn_server = fake_spawn_server
runner._start_vite_build_watch = lambda: None
opshell.OperatorShellMonitor = FakeOperator

runner.run_supervised()
'''


def _pid_alive(pid: int) -> bool:
    import os
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _wait_all_dead(pids, timeout: float) -> list[int]:
    import time
    pids = list(pids)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not any(_pid_alive(p) for p in pids):
            return []
        time.sleep(0.1)
    return [p for p in pids if _pid_alive(p)]


@pytest.mark.skipif(__import__("platform").system() == "Windows", reason="POSIX signal/process-group teardown")
def test_sigterm_tears_down_all_owned_descendants_subprocess(tmp_path):
    import json
    import os
    import signal
    import subprocess
    import sys
    import time
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    driver = tmp_path / "driver.py"
    driver.write_text(_SUBPROCESS_DRIVER)
    driver_log = (data_dir / "driver.log").open("w")

    env = dict(os.environ)
    env["ALETHEIA_DATA_DIR"] = str(data_dir)
    env.pop("ALETHEIA_COLLECTOR_PROCESS", None)

    # start_new_session=True → the supervisor leads its own session/process group,
    # exactly like the launchd/systemd-managed gateway. kill_pid_tree then reaches
    # only its own descendants, never the test runner or sibling worktrees.
    proc = subprocess.Popen(
        [sys.executable, str(driver)],
        cwd=str(repo_root), env=env,
        stdout=driver_log, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    children: dict = {}
    try:
        pids_file = data_dir / "children.json"
        deadline = time.time() + 30
        while time.time() < deadline and not pids_file.exists():
            if proc.poll() is not None:
                driver_log.flush()
                raise AssertionError(
                    "supervisor exited early:\n" + (data_dir / "driver.log").read_text()
                )
            time.sleep(0.1)
        assert pids_file.exists(), "supervisor never reported its children"
        children = json.loads(pids_file.read_text())
        assert set(children) == {"frontdoor", "collector", "operator", "backend"}
        for pid in children.values():
            assert _pid_alive(pid)

        pid_file = data_dir / "gateway.pid"
        assert pid_file.exists()
        assert int(pid_file.read_text().strip()) == proc.pid

        # Managed stop: SIGTERM the SUPERVISOR ONLY (not the group). Without the
        # fix its default disposition would kill it and orphan the children.
        os.kill(proc.pid, signal.SIGTERM)
        proc.wait(timeout=30)

        orphans = _wait_all_dead(children.values(), timeout=15)
        assert not orphans, f"owned descendants orphaned after SIGTERM: {orphans}"

        # PID file removed / unambiguously stale after shutdown.
        assert not pid_file.exists()

        # Durable, system-actor signal-shutdown event.
        audit = data_dir / "logs" / "audit.jsonl"
        assert audit.exists()
        sig_events = [
            json.loads(line)
            for line in audit.read_text().splitlines()
            if line.strip() and '"gateway_signal_shutdown"' in line
        ]
        assert len(sig_events) == 1
        assert sig_events[0]["actor"] == "system"
    finally:
        driver_log.close()
        # Safety net: never leak the supervisor or its children if we failed above.
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            pass
        for pid in children.values():
            try:
                os.kill(pid, signal.SIGKILL)
            except Exception:
                pass
