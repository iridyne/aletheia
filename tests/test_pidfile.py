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
    """The real _pid_alive must report THIS process (definitely alive) as alive.
    We monkeypatch the markers check to accept 'python' which will be present in
    the command line of any Python test runner."""
    import os
    import platform
    from aletheia.gateway import pidfile
    if platform.system() == "Windows":
        from aletheia.gateway import _win32_proc
        orig = _win32_proc.pid_alive_win32
        # Accept 'python' as marker so the test process matches.
        monkeypatch.setattr(
            pidfile, "_pid_alive",
            lambda pid: _win32_proc.pid_alive_win32(pid, ("python",))
        )
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
    assert pidfile.wait_port_free(8787, "127.0.0.1", timeout=0.01) is False


def test_find_port_holders_posix_queries_listeners_only(monkeypatch):
    """Connected API clients must never be classified as killable port owners."""
    from types import SimpleNamespace
    from aletheia.gateway import pidfile

    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        return SimpleNamespace(stdout="4242\n")

    monkeypatch.setattr(pidfile.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(pidfile.subprocess, "run", fake_run)
    monkeypatch.setattr(pidfile.os, "getpid", lambda: 9999)

    assert pidfile.find_port_holders(8787) == [4242]
    assert seen["command"] == [
        "lsof", "-nP", "-t", "-sTCP:LISTEN", "-iTCP:8787",
    ]


def test_find_port_holders_windows_tolerates_localized_output(monkeypatch):
    """Netstat decoding must not discard otherwise parseable listener rows."""
    from types import SimpleNamespace
    from aletheia.gateway import pidfile

    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs
        return SimpleNamespace(
            stdout="  TCP    0.0.0.0:8787    0.0.0.0:0    LISTENING    4242\n"
        )

    monkeypatch.setattr(pidfile.platform, "system", lambda: "Windows")
    monkeypatch.setattr(pidfile.subprocess, "run", fake_run)
    monkeypatch.setattr(pidfile.os, "getpid", lambda: 9999)

    assert pidfile.find_port_holders(8787) == [4242]
    assert seen["command"] == ["netstat", "-ano"]
    assert seen["kwargs"]["text"] is True
    assert seen["kwargs"]["errors"] == "replace"


def test_kill_pid_tree_windows_uses_ctypes_not_subprocess(monkeypatch):
    """On Windows, kill_pid_tree uses pure ctypes (no taskkill spawn), so no
    console window flash during orphan cleanup or gateway stop."""
    import platform as _platform
    if _platform.system() != "Windows":
        return  # ctypes module only available on Windows
    from aletheia.gateway import pidfile
    # Verify no subprocess.run is called during kill_pid_tree.
    def boom(*a, **k):
        raise AssertionError("subprocess.run should NOT be called for kill_pid_tree on Windows")
    monkeypatch.setattr(pidfile.subprocess, "run", boom)
    # Bogus PID — should attempt termination without spawning any subprocess.
    pidfile.kill_pid_tree(2_000_000_000)  # should not raise


# --- _no_window_kwargs helper + Win32 console suppression splats ---

def test_no_window_kwargs_helper_windows(monkeypatch):
    from aletheia.gateway import pidfile
    monkeypatch.setattr(pidfile.platform, "system", lambda: "Windows")
    assert pidfile._no_window_kwargs() == {"creationflags": 0x08000000}


def test_no_window_kwargs_helper_posix(monkeypatch):
    from aletheia.gateway import pidfile
    monkeypatch.setattr(pidfile.platform, "system", lambda: "Linux")
    assert pidfile._no_window_kwargs() == {}


def test_pid_alive_windows_uses_ctypes_not_subprocess(monkeypatch):
    """On Windows, _pid_alive uses pure ctypes (no subprocess spawn), so no
    wmic/tasklist flash on the PT5M watchdog tick."""
    import platform as _platform
    if _platform.system() != "Windows":
        return  # ctypes module only available on Windows
    from aletheia.gateway import pidfile
    # Verify no subprocess.run is called during pid_alive.
    def boom(*a, **k):
        raise AssertionError("subprocess.run should NOT be called for pid_alive on Windows")
    monkeypatch.setattr(pidfile.subprocess, "run", boom)
    # Bogus PID — should return False without spawning any subprocess.
    assert pidfile._pid_alive(2_000_000_000) is False


def test_list_processes_windows_uses_ctypes_not_subprocess(monkeypatch):
    """On Windows, _list_processes uses pure ctypes (no powershell spawn), so no
    console window flash on orphan discovery."""
    import platform as _platform
    if _platform.system() != "Windows":
        return  # ctypes module only available on Windows
    from aletheia.gateway import pidfile
    # Verify no subprocess.run is called during _list_processes.
    def boom(*a, **k):
        raise AssertionError("subprocess.run should NOT be called for _list_processes on Windows")
    monkeypatch.setattr(pidfile.subprocess, "run", boom)
    procs = pidfile._list_processes()
    # Should return a non-empty list (at least this test process).
    assert len(procs) > 0
    assert all(isinstance(p, tuple) and len(p) == 2 for p in procs)


def test_list_processes_posix_no_creationflags(monkeypatch):
    from aletheia.gateway import pidfile
    monkeypatch.setattr(pidfile.platform, "system", lambda: "Linux")
    calls = {}
    def fake_run(argv, **kwargs):
        calls["argv"] = argv
        calls["kwargs"] = kwargs
        class R: stdout = ""  # noqa: E701
        return R()
    monkeypatch.setattr(pidfile.subprocess, "run", fake_run)
    pidfile._list_processes()
    assert calls["argv"][0] == "ps"
    assert "creationflags" not in calls["kwargs"]


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


# --- _resolve_real_python: venv graft for BOTH spawn-launch contexts ---------
#
# Regression for the silent "autostart watchdog never revives the gateway" bug:
# the watchdog runs the BASE pythonw.exe and grafts the venv at runtime via
# site.addsitedir. _resolve_real_python must still hand spawned children a
# venv graft (__PYVENV_LAUNCHER__), or every 5-minute tick spawns a venv-blind
# supervisor that dies with `ModuleNotFoundError: No module named 'aletheia'`
# (launcher exits 1 before the gateway comes up).

def _force_windows(monkeypatch):
    from aletheia.gateway import pidfile
    monkeypatch.setattr(pidfile.platform, "system", lambda: "Windows")


def _make_fake_venv(root):
    """Create a minimal venv layout: pyvenv.cfg + Lib/site-packages + Scripts/python.exe."""
    venv = root / "venv"
    (venv / "Lib" / "site-packages").mkdir(parents=True)
    (venv / "Scripts").mkdir(parents=True)
    (venv / "Scripts" / "python.exe").write_text("", encoding="utf-8")
    (venv / "pyvenv.cfg").write_text("home = C:\base\n", encoding="utf-8")
    return venv


def test_resolve_real_python_case_b_base_interp_grafts_venv_from_syspath(tmp_path, monkeypatch):
    """Case B (watchdog): sys.executable is the BASE interpreter (no sibling
    pyvenv.cfg) but a venv site-packages is on sys.path → graft via that venv's
    shim so the spawned child reactivates the venv at its own startup."""
    import sys
    from aletheia.gateway import pidfile
    _force_windows(monkeypatch)

    base = tmp_path / "basepy"
    base.mkdir()
    base_exe = base / "python.exe"   # parent.parent = tmp_path, no pyvenv.cfg there
    base_exe.write_text("", encoding="utf-8")
    venv = _make_fake_venv(tmp_path)

    monkeypatch.setattr(sys, "executable", str(base_exe))
    monkeypatch.setattr(sys, "path", [str(venv / "Lib" / "site-packages")])

    real_python, env_patch = pidfile._resolve_real_python()
    assert env_patch.get("__PYVENV_LAUNCHER__") == str(venv / "Scripts" / "python.exe")


def test_resolve_real_python_case_b_no_venv_on_syspath_returns_empty_patch(tmp_path, monkeypatch):
    """Base interpreter with no venv anywhere on sys.path → empty patch (the
    pre-existing honest behavior, preserved)."""
    import sys
    from aletheia.gateway import pidfile
    _force_windows(monkeypatch)

    base = tmp_path / "basepy"
    base.mkdir()
    base_exe = base / "python.exe"
    base_exe.write_text("", encoding="utf-8")

    monkeypatch.setattr(sys, "executable", str(base_exe))
    monkeypatch.setattr(sys, "path", [str(tmp_path / "nowhere")])

    real_python, env_patch = pidfile._resolve_real_python()
    assert env_patch == {}


def test_resolve_real_python_case_a_venv_shim_resolves_to_base(tmp_path, monkeypatch):
    """Case A (manual / uv run): sys.executable IS the venv shim → resolve to
    base python via pyvenv.cfg 'home' and graft via the shim itself."""
    import sys
    from aletheia.gateway import pidfile
    _force_windows(monkeypatch)

    # Real base python the shim resolves to.
    realpy_dir = tmp_path / "realpy"
    realpy_dir.mkdir()
    (realpy_dir / "python.exe").write_text("", encoding="utf-8")

    # Venv whose shim is sys.executable; pyvenv.cfg home points at realpy_dir.
    venv = tmp_path / "venv"
    (venv / "Scripts").mkdir(parents=True)
    shim = venv / "Scripts" / "python.exe"
    shim.write_text("", encoding="utf-8")
    (venv / "pyvenv.cfg").write_text(f"home = {realpy_dir}\n", encoding="utf-8")

    monkeypatch.setattr(sys, "executable", str(shim))

    real_python, env_patch = pidfile._resolve_real_python()
    assert real_python == str(realpy_dir / "python.exe")
    assert env_patch.get("__PYVENV_LAUNCHER__") == str(shim.resolve())
