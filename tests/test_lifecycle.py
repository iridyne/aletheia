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

    # These lifecycle tests do not register a real OS service or tunnel.
    from aletheia.gateway import lifecycle
    monkeypatch.setattr(lifecycle, "_auto_start_tunnel", lambda cfg: None)
    # Default: port-holder discovery returns empty — no real lsof/netstat (#233).
    # Tests that exercise the holder kill path override this in their own body.
    monkeypatch.setattr(lifecycle, "find_port_holders", lambda port: [])
    from aletheia.gateway import managed_service
    monkeypatch.setattr(managed_service, "start", lambda **_kwargs: {"ok": True})
    monkeypatch.setattr(managed_service, "stop", lambda: {"ok": True})


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
    with pytest.raises(typer.Exit):
        lifecycle.do_start(no_browser=True)
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

    from aletheia.gateway import managed_service
    started = []
    monkeypatch.setattr(managed_service, "start", lambda **kwargs: started.append(kwargs) or {"ok": True})
    monkeypatch.setattr(lifecycle, "_verify_started", lambda cfg: None)

    lifecycle.do_start(no_browser=True)
    assert killed == [9999]
    assert started == [{"no_browser": True}]


def test_do_start_refuses_when_orphan_kill_doesnt_free_port(tmp_path, monkeypatch, capsys):
    """Orphan found and killed, but port STILL held → refuse (not our process)."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import lifecycle

    monkeypatch.setattr(lifecycle, "is_running", lambda: False)
    monkeypatch.setattr(lifecycle, "find_gateway_orphans", lambda: [8888])
    monkeypatch.setattr(lifecycle, "kill_pid_tree", lambda pid: None)
    monkeypatch.setattr(lifecycle, "wait_port_free", lambda port, host, timeout=10.0: False)
    monkeypatch.setattr(lifecycle, "_is_headless", lambda: False)
    with pytest.raises(typer.Exit):
        lifecycle.do_start(no_browser=True)
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
    from aletheia.gateway import managed_service
    started = []
    monkeypatch.setattr(managed_service, "start", lambda **kwargs: started.append(kwargs) or {"ok": True})
    monkeypatch.setattr(lifecycle, "_verify_started", lambda cfg: None)

    lifecycle.do_start(no_browser=True)
    assert started == [{"no_browser": True}]  # transient hold tolerated


def test_do_start_proceeds_when_port_free(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import lifecycle
    monkeypatch.setattr(lifecycle, "is_running", lambda: False)
    monkeypatch.setattr(lifecycle, "wait_port_free", lambda port, host, timeout=10.0: True)
    from aletheia.gateway import managed_service
    started = []
    monkeypatch.setattr(managed_service, "start", lambda **kwargs: started.append(kwargs) or {"ok": True})
    monkeypatch.setattr(lifecycle, "_verify_started", lambda cfg: None)

    lifecycle.do_start(no_browser=True)
    assert started == [{"no_browser": True}]


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


# --- Managed-service readiness verification ---

def test_verify_started_returns_when_gateway_comes_up(tmp_path, monkeypatch):
    """pid file written + port listening → confirmed up, returns cleanly."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import lifecycle
    cfg = lifecycle.load_config()
    monkeypatch.setattr(lifecycle, "is_running", lambda: True)
    monkeypatch.setattr(lifecycle, "port_in_use", lambda port, host="127.0.0.1": True)
    monkeypatch.setattr(lifecycle, "_verify_api_ready_accessible", lambda cfg: True)
    monkeypatch.setattr(lifecycle, "_verify_webui_accessible", lambda cfg: True)

    lifecycle._verify_started(cfg, timeout=1.0)


def test_verify_started_leaves_process_alive_when_api_not_ready_at_timeout(tmp_path, monkeypatch):
    """A slow-starting process (alive + port up but /api/ready not yet 200) must not be killed.

    Previously the verifier killed the process and exited nonzero, which destroyed a
    gateway that was still running migrations or initializing the scheduler.  The new
    behaviour leaves it running and returns cleanly (slow-start audit event written).
    """
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import lifecycle
    cfg = lifecycle.load_config()
    monkeypatch.setattr(lifecycle, "_is_headless", lambda: True)
    monkeypatch.setattr(lifecycle, "is_running", lambda: True)
    monkeypatch.setattr(lifecycle, "port_in_use", lambda port, host="127.0.0.1": True)
    monkeypatch.setattr(lifecycle, "_verify_api_ready_accessible", lambda cfg: False)
    monkeypatch.setattr(lifecycle, "_verify_webui_accessible", lambda cfg: True)
    killed: list[int] = []
    monkeypatch.setattr(lifecycle, "kill_pid_tree", lambda pid: killed.append(pid))

    # Must return without raising — slow start is not a failure.
    lifecycle._verify_started(cfg, timeout=0.1, poll_interval=0.01)

    assert killed == [], "alive process must not be killed on readiness timeout"
    from aletheia.files.jsonl import read_jsonl
    assert any(e.get("event_type") == "gateway_slow_start" for e in read_jsonl("audit.jsonl"))


def test_verify_started_raises_when_gateway_never_comes_up(tmp_path, monkeypatch, capsys):
    """Manager registration without a live gateway exits nonzero and is audited."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import lifecycle
    cfg = lifecycle.load_config()
    monkeypatch.setattr(lifecycle, "_is_headless", lambda: False)
    monkeypatch.setattr(lifecycle, "is_running", lambda: False)
    monkeypatch.setattr(lifecycle, "port_in_use", lambda port, host="127.0.0.1": False)

    with pytest.raises(typer.Exit):
        lifecycle._verify_started(cfg, timeout=0.1, poll_interval=0.01)

    # The death is no longer silent: it is audited.
    from aletheia.files.jsonl import read_jsonl
    assert any(e.get("event_type") == "gateway_spawn_failed" for e in read_jsonl("audit.jsonl"))


def test_verify_started_raises_on_timeout_when_never_up(tmp_path, monkeypatch):
    """Managed service stays unready until the lifecycle timeout."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import lifecycle
    cfg = lifecycle.load_config()
    monkeypatch.setattr(lifecycle, "_is_headless", lambda: True)  # exercise headless log path
    monkeypatch.setattr(lifecycle, "is_running", lambda: False)
    monkeypatch.setattr(lifecycle, "port_in_use", lambda port, host="127.0.0.1": False)

    with pytest.raises(typer.Exit):
        lifecycle._verify_started(cfg, timeout=0.6, poll_interval=0.01)


def test_verify_started_leaves_process_alive_when_webui_missing_at_timeout(tmp_path, monkeypatch):
    """API up + WebUI missing at timeout must not kill the process.

    Previously the verifier killed the process on this path.  The new behaviour
    leaves it running so the operator can diagnose, and the self-heal mechanism
    can detect and restart a gateway whose dashboard_static is genuinely absent.
    The gateway's own 503 page (served by the frontdoor's dashboard_unavailable
    handler) informs the user while recovery is in progress.
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

    # Must return without raising — WebUI missing is handled by self-heal, not CLI kill.
    lifecycle._verify_started(cfg, timeout=0.1)

    assert killed == [], "process must not be killed when only WebUI is missing"
    assert cleared == []
    from aletheia.files.jsonl import read_jsonl
    assert any(e.get("event_type") == "gateway_slow_start" for e in read_jsonl("audit.jsonl"))


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
        lifecycle._verify_started(lifecycle.load_config(), timeout=1.0)
    err = capsys.readouterr().err
    assert "ImportError boom" in err


def test_verify_started_fast_path_via_ready_beacon(tmp_path, monkeypatch):
    """A valid ready beacon lets _verify_started skip the tcp-port check and go
    straight to HTTP verification, confirming success without waiting for the normal
    polling deadline.
    """
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import lifecycle
    cfg = lifecycle.load_config()
    monkeypatch.setattr(lifecycle, "_is_headless", lambda: True)

    # Simulate supervisor having emitted the beacon for pid 9999.
    import json as _json
    beacon_path = tmp_path / "gateway.ready"
    beacon_path.write_text(_json.dumps({"pid": 9999, "ts": "2026-01-01T00:00:00+00:00"}), encoding="utf-8")
    monkeypatch.setattr(lifecycle, "read_pid", lambda: 9999)

    # port_in_use and is_running should not matter because beacon is present.
    monkeypatch.setattr(lifecycle, "is_running", lambda: False)
    monkeypatch.setattr(lifecycle, "port_in_use", lambda port, host="127.0.0.1": False)
    monkeypatch.setattr(lifecycle, "_verify_api_ready_accessible", lambda cfg: True)
    monkeypatch.setattr(lifecycle, "_verify_webui_accessible", lambda cfg: True)

    # Should return immediately without killing anything.
    killed: list[int] = []
    monkeypatch.setattr(lifecycle, "kill_pid_tree", lambda pid: killed.append(pid))
    lifecycle._verify_started(cfg, timeout=2.0, poll_interval=0.01)
    assert killed == []


def test_verify_started_beacon_ignored_when_pid_mismatch(tmp_path, monkeypatch):
    """A beacon with a stale PID is rejected; normal polling applies."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import lifecycle
    cfg = lifecycle.load_config()
    monkeypatch.setattr(lifecycle, "_is_headless", lambda: True)

    import json as _json
    beacon_path = tmp_path / "gateway.ready"
    beacon_path.write_text(_json.dumps({"pid": 1111, "ts": "2026-01-01T00:00:00+00:00"}), encoding="utf-8")
    # Live pid differs from beacon pid → beacon must be rejected.
    monkeypatch.setattr(lifecycle, "read_pid", lambda: 2222)
    monkeypatch.setattr(lifecycle, "is_running", lambda: False)
    monkeypatch.setattr(lifecycle, "port_in_use", lambda port, host="127.0.0.1": False)

    import typer
    with pytest.raises(typer.Exit):
        lifecycle._verify_started(cfg, timeout=0.1, poll_interval=0.01)


def test_circuit_break_auto_resets_after_stability_window(tmp_path, monkeypatch):
    """After 30+ min of stability, do_start auto-clears the circuit-break file and
    re-enables the watchdog without operator intervention.
    """
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import lifecycle

    # Write a circuit-break state file timestamped 31 minutes ago.
    import json as _json
    import datetime as _dt
    old_ts = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=31)).isoformat()
    cb_path = tmp_path / "circuit_break.json"
    cb_path.write_text(_json.dumps({"tripped_at": old_ts, "source": "circuit_breaker"}), encoding="utf-8")

    autostart_enabled: list[str] = []

    from aletheia.gateway import autostart as _autostart_mod
    monkeypatch.setattr(_autostart_mod, "is_supported_platform", lambda: True)
    monkeypatch.setattr(_autostart_mod, "enable", lambda source="api": autostart_enabled.append(source) or {"ok": True})

    # No new spawn failures in audit log → reset should proceed.
    lifecycle._check_circuit_break_auto_reset()

    assert not cb_path.exists(), "circuit_break.json should have been deleted after auto-reset"
    assert "auto_reset" in autostart_enabled, "watchdog should be re-enabled after auto-reset"

    from aletheia.files.jsonl import read_jsonl
    assert any(e.get("event_type") == "gateway_circuit_breaker_reset" for e in read_jsonl("audit.jsonl"))


def test_circuit_break_does_not_reset_within_window(tmp_path, monkeypatch):
    """Within the 30-min protection window the circuit-break file must not be removed."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import lifecycle

    import json as _json
    import datetime as _dt
    recent_ts = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=5)).isoformat()
    cb_path = tmp_path / "circuit_break.json"
    cb_path.write_text(_json.dumps({"tripped_at": recent_ts, "source": "circuit_breaker"}), encoding="utf-8")

    lifecycle._check_circuit_break_auto_reset()

    assert cb_path.exists(), "circuit_break.json must remain during the protection window"


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


def test_do_start_background_stops_registration_when_readiness_fails(tmp_path, monkeypatch):
    """A failed readiness probe removes the just-created OS service definition."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import lifecycle
    monkeypatch.setattr(lifecycle, "is_running", lambda: False)
    monkeypatch.setattr(lifecycle, "wait_port_free", lambda port, host, timeout=10.0: True)
    monkeypatch.setattr(lifecycle, "_is_headless", lambda: True)
    from aletheia.gateway import managed_service
    stopped = []
    monkeypatch.setattr(managed_service, "start", lambda **_kwargs: {"ok": True})
    monkeypatch.setattr(managed_service, "stop", lambda: stopped.append(True) or {"ok": True})
    monkeypatch.setattr(lifecycle, "_verify_started", lambda *_args, **_kwargs: (_ for _ in ()).throw(typer.Exit(1)))

    with pytest.raises(typer.Exit):
        lifecycle.do_start(no_browser=True)
    assert stopped == [True]


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
