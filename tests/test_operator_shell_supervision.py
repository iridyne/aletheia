import sqlite3
from pathlib import Path


def setup_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.sqlite import reset_db

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()


def test_build_operator_shell_command_uses_local_tsx_runtime(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)

    from aletheia.config.loader import load_config
    from aletheia.gateway import operator_shell

    root = tmp_path / "operator-shell"
    preflight = root / "node_modules" / "tsx" / "dist" / "preflight.cjs"
    loader = root / "node_modules" / "tsx" / "dist" / "loader.mjs"
    entry = root / "src" / "index.ts"
    preflight.parent.mkdir(parents=True, exist_ok=True)
    entry.parent.mkdir(parents=True, exist_ok=True)
    preflight.write_text("// tsx preflight", encoding="utf-8")
    loader.write_text("// tsx loader", encoding="utf-8")
    entry.write_text("console.log('ok')", encoding="utf-8")

    monkeypatch.setattr(operator_shell, "operator_shell_root", lambda: root)

    cmd = operator_shell.build_operator_shell_command(load_config())

    assert cmd is not None
    assert cmd[0] == "node"
    assert cmd[1] == "--require"
    assert cmd[2] == str(preflight)
    assert cmd[3] == "--import"
    assert cmd[4] == loader.as_uri()
    assert cmd[5] == str(entry)


def _make_operator_shell_fixture(tmp_path, monkeypatch):
    from aletheia.gateway import operator_shell

    root = tmp_path / "operator-shell"
    preflight = root / "node_modules" / "tsx" / "dist" / "preflight.cjs"
    loader = root / "node_modules" / "tsx" / "dist" / "loader.mjs"
    entry = root / "src" / "index.ts"
    preflight.parent.mkdir(parents=True, exist_ok=True)
    entry.parent.mkdir(parents=True, exist_ok=True)
    preflight.write_text("// tsx preflight", encoding="utf-8")
    loader.write_text("// tsx loader", encoding="utf-8")
    entry.write_text("console.log('ok')", encoding="utf-8")
    monkeypatch.setattr(operator_shell, "operator_shell_root", lambda: root)
    return operator_shell


def test_spawn_operator_shell_adopts_existing_pid_without_spawning(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)

    from aletheia.config.loader import load_config

    operator_shell = _make_operator_shell_fixture(tmp_path, monkeypatch)
    operator_shell.operator_shell_pid_path().write_text("1234\n", encoding="utf-8")

    monkeypatch.setattr(operator_shell, "_pid_exists", lambda pid: pid == 1234)
    monkeypatch.setattr(operator_shell, "_operator_shell_process_matches", lambda pid, cmd: True)

    def fail_popen(*args, **kwargs):  # noqa: ARG001
        raise AssertionError("spawn_operator_shell should adopt the pidfile process")

    monkeypatch.setattr(operator_shell.subprocess, "Popen", fail_popen)

    proc = operator_shell.spawn_operator_shell(load_config())

    assert proc is not None
    assert proc.pid == 1234
    assert proc.external is True


def test_spawn_operator_shell_replaces_stale_pidfile(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)

    from aletheia.config.loader import load_config

    operator_shell = _make_operator_shell_fixture(tmp_path, monkeypatch)
    pid_path = operator_shell.operator_shell_pid_path()
    pid_path.write_text("1234\n", encoding="utf-8")
    monkeypatch.setattr(operator_shell, "_pid_exists", lambda pid: False)

    class FakeProc:
        pid = 5678
        external = False

        def poll(self):
            return None

    monkeypatch.setattr(operator_shell.subprocess, "Popen", lambda *args, **kwargs: FakeProc())

    proc = operator_shell.spawn_operator_shell(load_config())

    assert proc is not None
    assert proc.pid == 5678
    assert pid_path.read_text("utf-8").strip() == "5678"


def test_operator_shell_process_roots_include_relative_tsx_parent(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)

    operator_shell = _make_operator_shell_fixture(tmp_path, monkeypatch)
    root = operator_shell.operator_shell_root()
    child_cmd = (
        f'"C:/Program Files/nodejs/node.exe" --require '
        f'{root}/node_modules/tsx/dist/preflight.cjs --import '
        f'file:///{root}/node_modules/tsx/dist/loader.mjs src/index.ts'
    )

    roots = operator_shell._operator_shell_process_roots(
        [
            (100, 1, "node_modules/tsx/dist/cli.mjs src/index.ts"),
            (101, 100, child_cmd),
        ]
    )

    assert roots == [100]


def test_spawn_operator_shell_kills_unmanaged_duplicates_before_spawning(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)

    from aletheia.config.loader import load_config

    operator_shell = _make_operator_shell_fixture(tmp_path, monkeypatch)
    killed: list[int] = []

    class FakeProc:
        pid = 5678
        external = False

        def poll(self):
            return None

    monkeypatch.setattr(operator_shell, "_list_operator_shell_processes", lambda: [(1234, 1, "")])
    monkeypatch.setattr(operator_shell, "_operator_shell_process_roots", lambda processes: [1234])
    monkeypatch.setattr(operator_shell.subprocess, "Popen", lambda *args, **kwargs: FakeProc())
    monkeypatch.setattr(
        "aletheia.gateway.pidfile.kill_pid_tree",
        lambda pid: killed.append(pid),
    )

    proc = operator_shell.spawn_operator_shell(load_config())

    assert proc is not None
    assert proc.pid == 5678
    assert killed == [1234]
    assert operator_shell.operator_shell_pid_path().read_text("utf-8").strip() == "5678"


def test_operator_shell_monitor_stop_does_not_terminate_external_process(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)

    from aletheia.config.loader import load_config
    from aletheia.gateway.operator_shell import OperatorShellMonitor

    calls = {"terminate": 0, "kill": 0}

    class ExternalProc:
        external = True
        pid = 1234

        def terminate(self):
            calls["terminate"] += 1

        def wait(self, timeout=None):  # noqa: ARG002
            return None

        def kill(self):
            calls["kill"] += 1

    monitor = OperatorShellMonitor(load_config())
    monitor.proc = ExternalProc()

    monitor.stop()

    assert calls == {"terminate": 0, "kill": 0}
    assert monitor.proc is None


def test_operator_shell_monitor_degrades_after_restart_budget(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)

    from aletheia.config.loader import load_config, save_config
    from aletheia.gateway.operator_shell import OperatorShellMonitor

    cfg = load_config()
    cfg.operator.max_restart_failures = 3
    cfg.operator.restart_after_sec = 90
    save_config(cfg)

    now = {"value": 1000.0}
    events = []
    spawned = {"count": 0}

    class FakeProc:
        def poll(self):
            return 1

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return 1

        def kill(self):
            return None

    def fake_spawn(_cfg):
        spawned["count"] += 1
        return FakeProc()

    monitor = OperatorShellMonitor(
        cfg,
        spawn_fn=fake_spawn,
        now_fn=lambda: now["value"],
        record_event=lambda kind, payload: events.append((kind, payload)),
    )
    monkeypatch.setattr("aletheia.gateway.operator_shell._gateway_api_ready", lambda cfg: True)

    monitor.tick_once()
    monitor.tick_once()
    monitor.tick_once()
    monitor.tick_once()
    monitor.tick_once()
    monitor.tick_once()

    assert spawned["count"] == 3
    assert monitor.degraded_until > now["value"]
    assert any(kind == "operator_shell_degraded" for kind, _ in events)


def test_operator_shell_monitor_records_offline_when_spawn_unavailable(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)

    from aletheia.agent_shell.operator_store import get_latest_heartbeat
    from aletheia.config.loader import load_config, save_config
    from aletheia.gateway.operator_shell import OperatorShellMonitor

    cfg = load_config()
    cfg.operator.max_restart_failures = 1
    cfg.operator.restart_after_sec = 90
    save_config(cfg)

    now = {"value": 1000.0}
    events = []
    monkeypatch.setattr("aletheia.gateway.operator_shell._gateway_api_ready", lambda cfg: True)
    monkeypatch.setattr("aletheia.gateway.operator_shell._post_operator_heartbeat", lambda cfg, payload: False)

    monitor = OperatorShellMonitor(
        cfg,
        spawn_fn=lambda _cfg: None,
        now_fn=lambda: now["value"],
        record_event=lambda kind, payload: events.append((kind, payload)),
    )

    monitor.tick_once()

    heartbeat = get_latest_heartbeat()
    assert heartbeat is not None
    assert heartbeat["status"] == "offline"
    assert heartbeat["mode"] == "Degraded"
    assert "command remained unavailable" in heartbeat["last_error"]
    assert monitor.degraded_until == now["value"] + cfg.operator.restart_after_sec
    assert any(kind == "operator_shell_degraded" for kind, _ in events)


def test_operator_shell_monitor_records_offline_when_spawn_raises(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)

    from aletheia.agent_shell.operator_store import get_latest_heartbeat
    from aletheia.config.loader import load_config, save_config
    from aletheia.gateway.operator_shell import OperatorShellMonitor

    cfg = load_config()
    cfg.operator.max_restart_failures = 1
    save_config(cfg)

    monkeypatch.setattr("aletheia.gateway.operator_shell._gateway_api_ready", lambda cfg: True)
    monkeypatch.setattr("aletheia.gateway.operator_shell._post_operator_heartbeat", lambda cfg, payload: False)

    def raise_spawn(_cfg):
        raise RuntimeError("spawn failed")

    monitor = OperatorShellMonitor(cfg, spawn_fn=raise_spawn, now_fn=lambda: 1000.0)

    monitor.tick_once()

    heartbeat = get_latest_heartbeat()
    assert heartbeat is not None
    assert heartbeat["status"] == "offline"
    assert heartbeat["mode"] == "Degraded"
    assert "spawn failed" in heartbeat["last_error"]


def test_operator_shell_monitor_refreshes_offline_status_during_cooldown(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)

    from aletheia.agent_shell.operator_store import get_latest_heartbeat
    from aletheia.config.loader import load_config, save_config
    from aletheia.gateway.operator_shell import OperatorShellMonitor

    cfg = load_config()
    cfg.operator.heartbeat_interval_sec = 10
    save_config(cfg)
    monkeypatch.setattr("aletheia.gateway.operator_shell._post_operator_heartbeat", lambda cfg, payload: False)

    now = {"value": 1000.0}
    monitor = OperatorShellMonitor(cfg, now_fn=lambda: now["value"])
    monitor.degraded_until = 1200.0

    monitor.tick_once()
    first = get_latest_heartbeat()
    assert first is not None
    assert first["status"] == "offline"
    assert "restart cooldown" in first["last_error"]

    now["value"] += 5
    monitor.tick_once()
    throttled = get_latest_heartbeat()
    assert throttled is not None
    assert throttled["id"] == first["id"]

    now["value"] += 6
    monitor.tick_once()
    refreshed = get_latest_heartbeat()
    assert refreshed is not None
    assert refreshed["id"] != first["id"]
    assert refreshed["status"] == "offline"


def test_operator_shell_monitor_waits_for_gateway_ready_before_spawn(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)

    from aletheia.config.loader import load_config
    from aletheia.gateway.operator_shell import OperatorShellMonitor

    spawned = {"count": 0}

    def fake_spawn(_cfg):
        spawned["count"] += 1
        raise AssertionError("operator shell should not spawn before gateway API is ready")

    monkeypatch.setattr("aletheia.gateway.operator_shell._gateway_api_ready", lambda cfg: False)

    monitor = OperatorShellMonitor(load_config(), spawn_fn=fake_spawn)
    monitor.tick_once()

    assert spawned["count"] == 0
    assert monitor.proc is None


def test_operator_shell_monitor_marks_alive_stale_heartbeat_degraded_without_restart(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)

    from aletheia.agent_shell.operator_store import get_latest_heartbeat
    from aletheia.config.loader import load_config, save_config
    from aletheia.gateway.operator_shell import OperatorShellMonitor

    cfg = load_config()
    cfg.operator.heartbeat_stale_after_sec = 45
    save_config(cfg)

    now = {"value": 1000.0}
    killed: list[int] = []
    events = []

    class FakeProc:
        pid = 2468

        def poll(self):
            return None

    monkeypatch.setattr("aletheia.gateway.operator_shell._gateway_api_ready", lambda cfg: True)
    monkeypatch.setattr("aletheia.gateway.operator_shell._latest_operator_heartbeat_age_sec", lambda: 120.0)
    monkeypatch.setattr("aletheia.gateway.operator_shell._post_operator_heartbeat", lambda cfg, payload: False)
    monkeypatch.setattr("aletheia.gateway.pidfile.kill_pid_tree", lambda pid: killed.append(pid))

    monitor = OperatorShellMonitor(
        cfg,
        now_fn=lambda: now["value"],
        record_event=lambda kind, payload: events.append((kind, payload)),
    )
    monitor.proc = FakeProc()
    monitor._proc_started_at = now["value"] - 120.0

    monitor.tick_once()

    assert killed == []
    assert monitor.proc is not None
    heartbeat = get_latest_heartbeat()
    assert heartbeat is not None
    assert heartbeat["status"] == "online"
    assert heartbeat["mode"] == "Degraded"
    assert "internal heartbeat is delayed" in heartbeat["last_error"]
    assert events == []


def test_gateway_supervisor_starts_and_stops_operator_monitor(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)

    from aletheia import _gateway_runner as runner

    calls = {"start": 0, "stop": 0}

    class FakeMonitor:
        def __init__(self, cfg):
            self.cfg = cfg

        def start(self):
            calls["start"] += 1

        def stop(self):
            calls["stop"] += 1

    import aletheia.gateway.operator_shell as operator_shell

    monkeypatch.setattr(operator_shell, "OperatorShellMonitor", FakeMonitor)
    monkeypatch.setattr(runner, "_sleep", lambda s: None)
    monkeypatch.setattr(runner, "_cleanup_before_spawn", lambda cfg: None)

    result = runner.run_supervised(spawn_fn=lambda: 0)

    assert result["gave_up"] is False
    assert calls["start"] == 1
    assert calls["stop"] == 1
