"""Tests for gateway lifecycle stop/start process-tree hygiene.

Regression: `gateway restart` used to leave an orphaned uvicorn holding the port
(taskkill without /T), so do_stop must tree-kill + wait for the port to free, and
do_start must refuse to spawn when the port is held but no gateway is tracked.

All process/port interaction is monkeypatched — no real ports or kills here.
"""

import sqlite3

import pytest
import typer

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

    # do_start exercises port/spawn hygiene, NOT the build-artifact preflight.
    # dashboard_static is now a gitignored build artifact (d728aa4), so
    # check_static_files() fails in a fresh test env (no `npm run build`). Bypass
    # preflight with an empty (passing) result so these tests stay focused on
    # process/port logic, not the presence of built front-end assets.
    from aletheia.gateway import preflight
    monkeypatch.setattr(preflight, "run_preflight_checks", lambda: preflight.PreflightResult())


def test_do_stop_clears_pid_only_after_port_freed(tmp_path, monkeypatch, capsys):
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import lifecycle
    from aletheia.gateway import pidfile

    # Exercise the console path so stdout/stderr messages are emitted (the headless
    # guard suppresses them; tests must pin it like the _echo_safe tests do).
    monkeypatch.setattr(lifecycle, "_is_headless", lambda: False)
    pidfile.pid_path().write_text("4242", encoding="utf-8")
    monkeypatch.setattr(lifecycle, "is_running", lambda: True)
    killed = []
    monkeypatch.setattr(lifecycle, "kill_pid_tree", lambda pid: killed.append(pid))
    monkeypatch.setattr(lifecycle, "find_gateway_orphans", lambda: [])
    # Record whether the pidfile still existed at the moment wait_port_free ran:
    # the pid must NOT be cleared before we've confirmed the port is free (TOCTOU).
    pid_present_during_wait = {}
    def fake_wait(port, host, timeout=10.0):
        pid_present_during_wait["v"] = pidfile.pid_path().exists()
        return True
    monkeypatch.setattr(lifecycle, "wait_port_free", fake_wait)

    lifecycle.do_stop()

    assert 4242 in killed
    assert pid_present_during_wait["v"] is True   # pid still tracked while waiting
    assert pidfile.read_pid() is None             # cleared only after port freed
    assert "stopped" in capsys.readouterr().out.lower()

    from aletheia.files.jsonl import read_jsonl
    assert any(e.get("event_type") == "gateway_stopped" for e in read_jsonl("audit.jsonl"))


def test_do_stop_cleans_our_orphans(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import lifecycle
    from aletheia.gateway import pidfile

    # No tracked supervisor, but an orphan holds the port.
    monkeypatch.setattr(lifecycle, "is_running", lambda: False)
    killed = []
    monkeypatch.setattr(lifecycle, "kill_pid_tree", lambda pid: killed.append(pid))
    monkeypatch.setattr(lifecycle, "find_gateway_orphans", lambda: [999])
    monkeypatch.setattr(lifecycle, "wait_port_free", lambda port, host, timeout=10.0: True)

    lifecycle.do_stop()
    assert killed == [999]  # orphan cleaned even with no pidfile


def test_do_stop_reports_when_port_stuck(tmp_path, monkeypatch, capsys):
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import lifecycle
    from aletheia.gateway import pidfile

    monkeypatch.setattr(lifecycle, "_is_headless", lambda: False)
    pidfile.pid_path().write_text("4242", encoding="utf-8")
    monkeypatch.setattr(lifecycle, "is_running", lambda: True)
    monkeypatch.setattr(lifecycle, "kill_pid_tree", lambda pid: None)
    monkeypatch.setattr(lifecycle, "find_gateway_orphans", lambda: [])
    monkeypatch.setattr(lifecycle, "wait_port_free", lambda port, host, timeout=10.0: False)

    lifecycle.do_stop()
    err = capsys.readouterr().err
    assert "still in use" in err.lower()


def test_do_stop_not_running_message(tmp_path, monkeypatch, capsys):
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import lifecycle
    monkeypatch.setattr(lifecycle, "_is_headless", lambda: False)
    monkeypatch.setattr(lifecycle, "is_running", lambda: False)
    monkeypatch.setattr(lifecycle, "find_gateway_orphans", lambda: [])
    # Nothing tracked AND port genuinely free → "not running".
    monkeypatch.setattr(lifecycle, "port_in_use", lambda port, host="127.0.0.1": False)
    monkeypatch.setattr(lifecycle, "wait_port_free", lambda port, host, timeout=10.0: True)

    lifecycle.do_stop()
    out = capsys.readouterr().out
    assert "not running" in out.lower()


def test_do_start_refuses_when_port_held_by_foreign_process(tmp_path, monkeypatch, capsys):
    """Port held, no orphans found → foreign process → refuse."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import lifecycle
    monkeypatch.setattr(lifecycle, "is_running", lambda: False)
    monkeypatch.setattr(lifecycle, "wait_port_free", lambda port, host, timeout=10.0: False)
    monkeypatch.setattr(lifecycle, "find_gateway_orphans", lambda: [])
    monkeypatch.setattr(lifecycle, "_is_headless", lambda: False)
    spawned = []
    monkeypatch.setattr(lifecycle, "_spawn_background", lambda nb: spawned.append(True))

    with pytest.raises(typer.Exit):
        lifecycle.do_start(no_browser=True)
    assert spawned == []
    err = capsys.readouterr().err
    assert "foreign" in err.lower()


def test_do_start_self_heals_orphan_then_proceeds(tmp_path, monkeypatch):
    """Port held by an Aletheia orphan → auto-kill → port frees → spawn succeeds."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import lifecycle

    monkeypatch.setattr(lifecycle, "is_running", lambda: False)
    monkeypatch.setattr(lifecycle, "find_gateway_orphans", lambda: [9999])
    killed = []
    monkeypatch.setattr(lifecycle, "kill_pid_tree", lambda pid: killed.append(pid))

    # First call (timeout=3.0) returns False (port held); second call (timeout=5.0)
    # after orphan kill returns True (port freed).
    call_count = {"n": 0}
    def staged_wait(port, host, timeout=10.0):
        call_count["n"] += 1
        return call_count["n"] > 1  # False first time, True second time
    monkeypatch.setattr(lifecycle, "wait_port_free", staged_wait)

    spawned = []
    monkeypatch.setattr(lifecycle, "_spawn_background", lambda nb: spawned.append(True))
    monkeypatch.setattr(lifecycle, "_verify_started", lambda proc, cfg: None)

    lifecycle.do_start(no_browser=True)
    assert killed == [9999]
    assert spawned == [True]


def test_do_start_refuses_when_orphan_kill_doesnt_free_port(tmp_path, monkeypatch, capsys):
    """Orphan found and killed, but port STILL held → refuse (not our process)."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import lifecycle

    monkeypatch.setattr(lifecycle, "is_running", lambda: False)
    monkeypatch.setattr(lifecycle, "find_gateway_orphans", lambda: [8888])
    monkeypatch.setattr(lifecycle, "kill_pid_tree", lambda pid: None)
    monkeypatch.setattr(lifecycle, "wait_port_free", lambda port, host, timeout=10.0: False)
    monkeypatch.setattr(lifecycle, "_is_headless", lambda: False)
    spawned = []
    monkeypatch.setattr(lifecycle, "_spawn_background", lambda nb: spawned.append(True))

    with pytest.raises(typer.Exit):
        lifecycle.do_start(no_browser=True)
    assert spawned == []
    err = capsys.readouterr().err
    assert "still in use" in err.lower()


def test_do_start_proceeds_after_transient_port_clears(tmp_path, monkeypatch):
    """Restart race: the port is briefly held (TIME_WAIT/lingering socket) right after
    a stop, then clears within the grace window → do_start must proceed, not refuse."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import lifecycle
    monkeypatch.setattr(lifecycle, "is_running", lambda: False)
    # Port frees within the grace window.
    monkeypatch.setattr(lifecycle, "wait_port_free", lambda port, host, timeout=10.0: True)
    spawned = []
    monkeypatch.setattr(lifecycle, "_spawn_background", lambda nb: spawned.append(True))
    monkeypatch.setattr(lifecycle, "_verify_started", lambda proc, cfg: None)

    lifecycle.do_start(no_browser=True)
    assert spawned == [True]  # transient hold tolerated


def test_do_start_proceeds_when_port_free(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import lifecycle
    monkeypatch.setattr(lifecycle, "is_running", lambda: False)
    monkeypatch.setattr(lifecycle, "wait_port_free", lambda port, host, timeout=10.0: True)
    spawned = []
    monkeypatch.setattr(lifecycle, "_spawn_background", lambda nb: spawned.append(True))
    monkeypatch.setattr(lifecycle, "_verify_started", lambda proc, cfg: None)

    lifecycle.do_start(no_browser=True)
    assert spawned == [True]


def test_do_start_foreground_guard_refuses_when_port_taken(tmp_path, monkeypatch, capsys):
    """The port guard must also protect the foreground path (write_pid + _run_server)."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import lifecycle
    monkeypatch.setattr(lifecycle, "is_running", lambda: False)
    monkeypatch.setattr(lifecycle, "wait_port_free", lambda port, host, timeout=10.0: False)
    monkeypatch.setattr(lifecycle, "find_gateway_orphans", lambda: [])
    monkeypatch.setattr(lifecycle, "_is_headless", lambda: False)
    ran = []
    monkeypatch.setattr(lifecycle, "_run_server", lambda cfg: ran.append(True))
    wrote = []
    monkeypatch.setattr(lifecycle, "write_pid", lambda: wrote.append(True))

    with pytest.raises(typer.Exit):
        lifecycle.do_start(no_browser=True, foreground=True)
    assert ran == [] and wrote == []  # guard fired before write_pid / _run_server


# --- Background spawn console suppression (no Windows Terminal pop-up) ---

def test_spawn_background_uses_create_no_window_on_windows(tmp_path, monkeypatch):
    """Win11 needs CREATE_NO_WINDOW alongside DETACHED_PROCESS to forbid console
    allocation; without it Windows Terminal pops up for the supervisor child."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import lifecycle

    captured = {}

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        class P: pass
        return P()

    monkeypatch.setattr(lifecycle.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(lifecycle.platform, "system", lambda: "Windows", raising=False)

    lifecycle._spawn_background(no_browser=True)

    flags = captured["kwargs"].get("creationflags", 0)
    assert flags & 0x08000000 == 0x08000000  # CREATE_NO_WINDOW
    assert flags & 0x00000008 == 0x00000008  # DETACHED_PROCESS still set
    assert flags & 0x00000200 == 0x00000200  # CREATE_NEW_PROCESS_GROUP still set


def test_spawn_background_posix_unchanged(tmp_path, monkeypatch):
    """POSIX path: no creationflags, start_new_session=True preserved."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import lifecycle

    captured = {}

    def fake_popen(args, **kwargs):
        captured["kwargs"] = kwargs
        class P: pass
        return P()

    monkeypatch.setattr(lifecycle.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(lifecycle.platform, "system", lambda: "Linux", raising=False)

    lifecycle._spawn_background(no_browser=True)

    assert "creationflags" not in captured["kwargs"]
    assert captured["kwargs"].get("start_new_session") is True


# --- Headless detection + safe echo ---

def test_is_headless_false_on_posix(monkeypatch):
    from aletheia.gateway import lifecycle
    monkeypatch.setattr(lifecycle.platform, "system", lambda: "Linux")
    assert lifecycle._is_headless() is False


def test_is_headless_true_for_pythonw(monkeypatch):
    from aletheia.gateway import lifecycle
    monkeypatch.setattr(lifecycle.platform, "system", lambda: "Windows")
    monkeypatch.setattr(lifecycle.sys, "executable", r"C:\Python\pythonw.exe")
    assert lifecycle._is_headless() is True


def test_echo_safe_suppresses_in_headless(tmp_path, monkeypatch, capsys):
    from aletheia.gateway import lifecycle
    monkeypatch.setattr(lifecycle, "_is_headless", lambda: True)
    lifecycle._echo_safe("should not appear on stderr")
    out = capsys.readouterr()
    assert out.err == ""


def test_echo_safe_writes_stderr_when_not_headless(tmp_path, monkeypatch, capsys):
    from aletheia.gateway import lifecycle
    monkeypatch.setattr(lifecycle, "_is_headless", lambda: False)
    lifecycle._echo_safe("visible error")
    out = capsys.readouterr()
    assert "visible error" in out.err


# --- Spawn verification: the watchdog's success signal must be honest ---
#
# Regression for the 13.5h silent-death zombie: _spawn_background returns exit 0
# the instant it forks, even when the supervisor immediately dies, so the PT5M
# watchdog recorded LastResult=0x0 ("success") on every tick while the gateway
# stayed dead. _verify_started turns that signal honest.

class _FakeProc:
    """Stand-in for a Popen launcher handle. poll() returns `code` (None = alive)."""
    def __init__(self, code=None):
        self._code = code
        self.returncode = code

    def poll(self):
        return self._code


def test_verify_started_returns_when_gateway_comes_up(tmp_path, monkeypatch):
    """pid file written + port listening → confirmed up, returns cleanly."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import lifecycle
    cfg = lifecycle.load_config()
    monkeypatch.setattr(lifecycle, "is_running", lambda: True)
    monkeypatch.setattr(lifecycle, "port_in_use", lambda port, host="127.0.0.1": True)
    monkeypatch.setattr(lifecycle, "_verify_api_ready_accessible", lambda cfg: True)
    monkeypatch.setattr(lifecycle, "_verify_webui_accessible", lambda cfg: True)

    # Launcher still alive (poll None); gateway up → no raise.
    lifecycle._verify_started(_FakeProc(code=None), cfg, timeout=1.0)


def test_verify_started_accepts_replacement_after_launcher_sigterm(tmp_path, monkeypatch):
    """A newer restart may replace the launcher before its gateway binds."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import lifecycle
    cfg = lifecycle.load_config()
    running = iter([False, False, True])
    monkeypatch.setattr(lifecycle, "is_running", lambda: next(running, True))
    monkeypatch.setattr(lifecycle, "port_in_use", lambda port, host="127.0.0.1": True)
    monkeypatch.setattr(lifecycle, "_verify_api_ready_accessible", lambda cfg: True)
    monkeypatch.setattr(lifecycle, "_verify_webui_accessible", lambda cfg: True)

    lifecycle._verify_started(_FakeProc(code=-15), cfg, timeout=0.2, poll_interval=0.01)


def test_verify_started_waits_for_api_ready_before_success(tmp_path, monkeypatch):
    """A listening socket alone is not readiness: require the cheap API probe."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import lifecycle
    cfg = lifecycle.load_config()
    monkeypatch.setattr(lifecycle, "_is_headless", lambda: True)
    monkeypatch.setattr(lifecycle, "is_running", lambda: True)
    monkeypatch.setattr(lifecycle, "port_in_use", lambda port, host="127.0.0.1": True)
    monkeypatch.setattr(lifecycle, "_verify_api_ready_accessible", lambda cfg: False)
    monkeypatch.setattr(lifecycle, "_verify_webui_accessible", lambda cfg: True)

    with pytest.raises(typer.Exit):
        lifecycle._verify_started(_FakeProc(code=None), cfg, timeout=0.1, poll_interval=0.01)


def test_verify_started_raises_when_launcher_dies_immediately(tmp_path, monkeypatch, capsys):
    """Launcher exits non-zero before gateway is up → Exit(1) + spawn-failed audit."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import lifecycle
    cfg = lifecycle.load_config()
    monkeypatch.setattr(lifecycle, "_is_headless", lambda: False)
    monkeypatch.setattr(lifecycle, "is_running", lambda: False)
    monkeypatch.setattr(lifecycle, "port_in_use", lambda port, host="127.0.0.1": False)

    with pytest.raises(typer.Exit):
        lifecycle._verify_started(_FakeProc(code=1), cfg, timeout=2.0)

    # The death is no longer silent: it is audited.
    from aletheia.files.jsonl import read_jsonl
    assert any(e.get("event_type") == "gateway_spawn_failed" for e in read_jsonl("audit.jsonl"))


def test_verify_started_raises_on_timeout_when_never_up(tmp_path, monkeypatch):
    """Launcher stays alive but gateway never binds the port → Exit(1) on timeout."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import lifecycle
    cfg = lifecycle.load_config()
    monkeypatch.setattr(lifecycle, "_is_headless", lambda: True)  # exercise headless log path
    monkeypatch.setattr(lifecycle, "is_running", lambda: False)
    monkeypatch.setattr(lifecycle, "port_in_use", lambda port, host="127.0.0.1": False)

    with pytest.raises(typer.Exit):
        lifecycle._verify_started(_FakeProc(code=None), cfg, timeout=0.6, poll_interval=0.01)


def test_verify_started_kills_api_only_gateway_when_webui_missing(tmp_path, monkeypatch):
    """API-only startup is not success: kill it so watchdog can retry after build.

    Regression: if dashboard_static/index.html disappeared during frontend build,
    uvicorn could bind /api while "/" stayed 404. The old verifier returned
    success, leaving users with an unreachable UI forever.
    """
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import lifecycle
    cfg = lifecycle.load_config()
    monkeypatch.setattr(lifecycle, "_is_headless", lambda: True)
    monkeypatch.setattr(lifecycle, "is_running", lambda: True)
    monkeypatch.setattr(lifecycle, "port_in_use", lambda port, host="127.0.0.1": True)
    monkeypatch.setattr(lifecycle, "_verify_api_ready_accessible", lambda cfg: True)
    monkeypatch.setattr(lifecycle, "_verify_webui_accessible", lambda cfg: False)
    monkeypatch.setattr(lifecycle, "read_pid", lambda: 4242)
    killed: list[int] = []
    monkeypatch.setattr(lifecycle, "kill_pid_tree", lambda pid: killed.append(pid))
    cleared: list[bool] = []
    monkeypatch.setattr(lifecycle, "clear_pid", lambda: cleared.append(True))
    monkeypatch.setattr(lifecycle, "wait_port_free", lambda port, host, timeout=10.0: True)

    with pytest.raises(typer.Exit):
        lifecycle._verify_started(_FakeProc(code=None), cfg, timeout=0.1)

    assert killed == [4242]
    assert cleared == [True]


def test_verify_started_surfaces_errors_log_tail(tmp_path, monkeypatch, capsys):
    """On failure, the inner uvicorn's errors.log tail is surfaced to the operator."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import lifecycle
    from aletheia.gateway.logs import errors_log_path
    monkeypatch.setattr(lifecycle, "_is_headless", lambda: False)
    monkeypatch.setattr(lifecycle, "is_running", lambda: False)
    monkeypatch.setattr(lifecycle, "port_in_use", lambda port, host="127.0.0.1": False)
    errors_log_path().write_text("Traceback: ImportError boom\n", encoding="utf-8")

    with pytest.raises(typer.Exit):
        lifecycle._verify_started(_FakeProc(code=1), cfg=lifecycle.load_config(), timeout=1.0)
    err = capsys.readouterr().err
    assert "ImportError boom" in err


def test_do_logs_shows_all_operational_logs_redacted(tmp_path, monkeypatch, capsys):
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import lifecycle
    from aletheia.gateway.logs import errors_log_path, log_path

    log_dir = tmp_path / "logs"
    log_path().write_text("gateway alive\n", encoding="utf-8")
    errors_log_path().write_text("api_key=SECRET123\n", encoding="utf-8")
    (log_dir / "frontdoor-errors.log").write_text("frontdoor failed\n", encoding="utf-8")

    lifecycle.do_logs(lines=5)

    out = capsys.readouterr().out
    assert "== gateway.log" in out
    assert "== errors.log" in out
    assert "== frontdoor-errors.log" in out
    assert "gateway alive" in out
    assert "frontdoor failed" in out
    assert "SECRET123" not in out
    assert "[REDACTED]" in out


def test_do_start_background_raises_when_spawn_dies(tmp_path, monkeypatch):
    """End-to-end: do_start background path now propagates a dead spawn as Exit(1),
    instead of silently returning 0 (the false-green the watchdog used to record)."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import lifecycle
    monkeypatch.setattr(lifecycle, "is_running", lambda: False)
    monkeypatch.setattr(lifecycle, "wait_port_free", lambda port, host, timeout=10.0: True)
    monkeypatch.setattr(lifecycle, "_is_headless", lambda: True)
    monkeypatch.setattr(lifecycle, "_spawn_background", lambda nb: _FakeProc(code=1))
    # Gateway never comes up.
    monkeypatch.setattr(lifecycle, "port_in_use", lambda port, host="127.0.0.1": False)

    with pytest.raises(typer.Exit):
        lifecycle.do_start(no_browser=True)


def test_do_resume_prefers_running_gateway_api(tmp_path, monkeypatch):
    """When the gateway is running, CLI resume must wake the in-process scheduler."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import lifecycle
    from aletheia.agent_runtime import resume_gate

    monkeypatch.setattr(lifecycle, "_is_headless", lambda: False)
    monkeypatch.setattr(lifecycle, "is_running", lambda: True)
    monkeypatch.setattr(lifecycle, "port_in_use", lambda port, host="127.0.0.1": True)
    calls = []
    monkeypatch.setattr(lifecycle, "_post_gateway_resume_api", lambda cfg: calls.append("api") or {"paused": False})
    monkeypatch.setattr(
        resume_gate,
        "resume_if_llm_available_sync",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("local gate should not run")),
    )

    lifecycle.do_resume()

    assert calls == ["api"]
