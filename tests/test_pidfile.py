"""Tests for gateway PID liveness detection (guide §6.1, §32 #3).

Regression: on Windows, os.kill(pid, 0) does NOT probe liveness (it terminates),
so is_running() must use a real liveness check. We test the platform-agnostic
contract by stubbing the low-level probe.
"""


def setup_data(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    tmp_path.mkdir(parents=True, exist_ok=True)


def test_is_running_false_when_no_pidfile(tmp_path, monkeypatch):
    setup_data(tmp_path, monkeypatch)
    from aletheia.gateway import pidfile
    assert pidfile.is_running() is False


def test_is_running_true_when_pid_alive(tmp_path, monkeypatch):
    setup_data(tmp_path, monkeypatch)
    from aletheia.gateway import pidfile
    pidfile.pid_path().write_text("4242", encoding="utf-8")
    monkeypatch.setattr(pidfile, "_pid_alive", lambda pid: True)
    assert pidfile.is_running() is True
    # pidfile preserved while alive
    assert pidfile.read_pid() == 4242


def test_is_running_false_and_clears_when_pid_dead(tmp_path, monkeypatch):
    setup_data(tmp_path, monkeypatch)
    from aletheia.gateway import pidfile
    pidfile.pid_path().write_text("4242", encoding="utf-8")
    monkeypatch.setattr(pidfile, "_pid_alive", lambda pid: False)
    assert pidfile.is_running() is False
    # stale pidfile cleared
    assert pidfile.read_pid() is None


def test_pid_alive_for_current_process(tmp_path, monkeypatch):
    """The real _pid_alive must report THIS process (definitely alive) as alive."""
    import os
    from aletheia.gateway import pidfile
    assert pidfile._pid_alive(os.getpid()) is True


def test_pid_alive_for_bogus_pid(tmp_path, monkeypatch):
    from aletheia.gateway import pidfile
    # An almost-certainly-unused high PID should read as not alive.
    assert pidfile._pid_alive(2_000_000_000) is False


# --- Process-tree teardown + port utilities (gateway restart leak fix) ---

def test_port_in_use_false_when_free():
    from aletheia.gateway import pidfile
    # A very high, almost-certainly-unbound port should read as free.
    assert pidfile.port_in_use(59321) is False


def test_port_in_use_normalizes_wildcard_host(monkeypatch):
    from aletheia.gateway import pidfile
    seen = {}

    class FakeSock:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def settimeout(self, t): pass
        def connect_ex(self, addr):
            seen["host"] = addr[0]
            return 1  # free

    monkeypatch.setattr(pidfile.socket, "socket", lambda *a, **k: FakeSock())
    # A wildcard bind address must be probed via loopback, not connected-to directly
    # (connect to 0.0.0.0 is invalid on Windows and misleading elsewhere).
    pidfile.port_in_use(8787, "0.0.0.0")
    assert seen["host"] == "127.0.0.1"


def test_wait_port_free_true_when_already_free(monkeypatch):
    from aletheia.gateway import pidfile
    monkeypatch.setattr(pidfile, "port_in_use", lambda port, host="127.0.0.1": False)
    assert pidfile.wait_port_free(8787, "127.0.0.1", timeout=1.0) is True


def test_wait_port_free_times_out_when_stuck(monkeypatch):
    from aletheia.gateway import pidfile
    monkeypatch.setattr(pidfile, "port_in_use", lambda port, host="127.0.0.1": True)
    monkeypatch.setattr(pidfile.time, "sleep", lambda s: None)  # no real waiting
    assert pidfile.wait_port_free(8787, "127.0.0.1", timeout=1.0) is False


def test_kill_pid_tree_windows_uses_T_and_F(monkeypatch):
    from aletheia.gateway import pidfile
    monkeypatch.setattr(pidfile.platform, "system", lambda: "Windows")
    calls = {}
    def fake_run(argv, **kwargs):
        calls["argv"] = argv
        class R: returncode = 0
        return R()
    monkeypatch.setattr(pidfile.subprocess, "run", fake_run)
    pidfile.kill_pid_tree(4242)
    assert "/T" in calls["argv"] and "/F" in calls["argv"]
    assert "4242" in calls["argv"]


def test_kill_pid_tree_posix_uses_killpg(monkeypatch):
    from aletheia.gateway import pidfile
    monkeypatch.setattr(pidfile.platform, "system", lambda: "Linux")
    killed = {}
    def fake_killpg(pid, sig):
        killed["pgid"] = pid
        killed["sig"] = sig
    # os.killpg/getpgid don't exist on Windows where this test runs → raising=False.
    monkeypatch.setattr(pidfile.os, "killpg", fake_killpg, raising=False)
    monkeypatch.setattr(pidfile.os, "getpgid", lambda pid: pid, raising=False)
    pidfile.kill_pid_tree(4242)
    assert killed["pgid"] == 4242


def test_kill_pid_tree_never_raises(monkeypatch):
    from aletheia.gateway import pidfile
    monkeypatch.setattr(pidfile.platform, "system", lambda: "Windows")
    def boom(*a, **k):
        raise OSError("taskkill exploded")
    monkeypatch.setattr(pidfile.subprocess, "run", boom)
    # must swallow and not raise
    pidfile.kill_pid_tree(4242)


def test_find_gateway_orphans_matches_inner_and_supervisor(monkeypatch):
    from aletheia.gateway import pidfile
    # Must match BOTH the inner uvicorn AND the supervisor (_gateway_runner), so a
    # still-alive supervisor can't respawn an inner we just killed. Foreign/unrelated
    # processes are never matched.
    procs = [
        (101, "python.exe -m uvicorn aletheia.server.app:app --host 127.0.0.1 --port 8787"),
        (150, "python.exe C:\\proj\\aletheia\\_gateway_runner.py --no-browser"),  # supervisor
        (202, "python.exe -m uvicorn someones.other.app:app --port 9000"),  # foreign
        (303, "node server.js"),  # unrelated
    ]
    monkeypatch.setattr(pidfile, "_list_processes", lambda: procs)
    orphans = pidfile.find_gateway_orphans()
    assert set(orphans) == {101, 150}  # inner + supervisor, nothing foreign


def test_find_gateway_orphans_supervisors_first(monkeypatch):
    """Supervisors must be ordered before inner servers so killing them first
    prevents a respawn of the inner we're about to kill."""
    from aletheia.gateway import pidfile
    procs = [
        (101, "python.exe -m uvicorn aletheia.server.app:app --port 8787"),
        (150, "python.exe /x/_gateway_runner.py"),  # supervisor
    ]
    monkeypatch.setattr(pidfile, "_list_processes", lambda: procs)
    orphans = pidfile.find_gateway_orphans()
    assert orphans.index(150) < orphans.index(101)  # supervisor before inner


def test_find_gateway_orphans_empty_on_error(monkeypatch):
    from aletheia.gateway import pidfile
    def boom():
        raise RuntimeError("ps failed")
    monkeypatch.setattr(pidfile, "_list_processes", boom)
    assert pidfile.find_gateway_orphans() == []
