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
