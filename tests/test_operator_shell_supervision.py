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
    cli = root / "node_modules" / "tsx" / "dist" / "cli.mjs"
    entry = root / "src" / "index.ts"
    cli.parent.mkdir(parents=True, exist_ok=True)
    entry.parent.mkdir(parents=True, exist_ok=True)
    cli.write_text("// tsx", encoding="utf-8")
    entry.write_text("console.log('ok')", encoding="utf-8")

    monkeypatch.setattr(operator_shell, "operator_shell_root", lambda: root)

    cmd = operator_shell.build_operator_shell_command(load_config())

    assert cmd is not None
    assert cmd[0] == "node"
    assert cmd[1] == str(cli)
    assert cmd[2] == str(entry)


def _make_operator_shell_fixture(tmp_path, monkeypatch):
    from aletheia.gateway import operator_shell

    root = tmp_path / "operator-shell"
    cli = root / "node_modules" / "tsx" / "dist" / "cli.mjs"
    entry = root / "src" / "index.ts"
    cli.parent.mkdir(parents=True, exist_ok=True)
    entry.parent.mkdir(parents=True, exist_ok=True)
    cli.write_text("// tsx", encoding="utf-8")
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

    monitor.tick_once()
    monitor.tick_once()
    monitor.tick_once()
    monitor.tick_once()
    monitor.tick_once()
    monitor.tick_once()

    assert spawned["count"] == 3
    assert monitor.degraded_until > now["value"]
    assert any(kind == "operator_shell_degraded" for kind, _ in events)


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

    result = runner.run_supervised(spawn_fn=lambda: 0)

    assert result["gave_up"] is False
    assert calls["start"] == 1
    assert calls["stop"] == 1
