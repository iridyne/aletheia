"""Tests for Windows Task Scheduler autostart + supervisor watchdog (roadmap §30.3).

Governance: human-triggered only, never agent-callable, runs the SAME gated
`gateway start` a human runs (live stays disabled, RunLevel LeastPrivilege).
schtasks is fully mocked here (no real task is created). Verifies argv + Task XML
correctness, UTF-16 encoding, three honest states, access-denied honesty,
non-Windows no-op, schtasks-missing graceful degradation, and audit on
human-triggered enable/disable.
"""

import sqlite3
from pathlib import Path


def setup_env(tmp_path, monkeypatch):
    """Point DATA_DIR/CONFIG_PATH at tmp and init the DB so write_audit works."""
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    from aletheia.storage.migrations import run_migrations
    sq.reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()


class _FakeProc:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


class _Recorder:
    """Stand-in for subprocess.run that records argv and captures any /XML file."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.calls = []
        self.xml_bytes = None

    def __call__(self, args, **kwargs):
        self.calls.append((list(args), kwargs))
        if "/XML" in args:
            xml_path = args[args.index("/XML") + 1]
            try:
                self.xml_bytes = Path(xml_path).read_bytes()
            except OSError:
                pass
        return _FakeProc(stdout=self.stdout, returncode=self.returncode, stderr=self.stderr)


def _force_windows(monkeypatch, autostart, python=r"C:\venv\Scripts\pythonw.exe"):
    monkeypatch.setattr(autostart.platform, "system", lambda: "Windows")
    monkeypatch.setattr(autostart, "_python_for_task", lambda: python)


# --- 1. enable builds correct argv + Task XML ---

def test_enable_builds_correct_argv_and_xml(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import autostart
    _force_windows(monkeypatch, autostart)
    rec = _Recorder(returncode=0, stdout="SUCCESS")
    monkeypatch.setattr(autostart.subprocess, "run", rec)

    result = autostart.enable(source="cli")
    assert result["ok"] is True
    assert result["enabled"] is True

    args = rec.calls[0][0]
    assert args[0] == "schtasks"
    assert "/Create" in args and "/XML" in args and "/F" in args
    assert autostart.TASK_NAME in args  # the /TN value

    assert rec.xml_bytes is not None
    assert rec.xml_bytes[:2] == b"\xff\xfe"  # UTF-16 LE BOM (schtasks requirement)
    xml = rec.xml_bytes.decode("utf-16")
    assert 'xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task"' in xml
    assert "<LogonTrigger>" in xml
    assert "<Interval>PT5M</Interval>" in xml
    assert "<StopAtDurationEnd>false</StopAtDurationEnd>" in xml
    assert "<LogonType>InteractiveToken</LogonType>" in xml
    assert "<RunLevel>LeastPrivilege</RunLevel>" in xml
    assert "<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>" in xml
    # Arguments must contain the gateway start command (either direct -m or -c bootstrap).
    assert "gateway" in xml and "start" in xml and "--no-browser" in xml
    assert "pythonw.exe" in xml
    # Window suppression: Task Scheduler must mark the task hidden so no console
    # flashes on the LogonTrigger fire (and PT5M watchdog ticks).
    assert "<Hidden>true</Hidden>" in xml
    # schtasks /Create itself must not flash a console window when called from a
    # detached parent (e.g. the inner uvicorn API route serving /api/gateway/autostart).
    assert rec.calls[0][1].get("creationflags", 0) & 0x08000000 == 0x08000000


# --- 2. python resolution: prefer pythonw sibling, fall back to sys.executable ---

def test_task_xml_marks_hidden_inside_settings(monkeypatch):
    """<Hidden>true</Hidden> must live inside the <Settings>...</Settings> block —
    not just somewhere in the document — to be honored by Task Scheduler."""
    from aletheia.gateway import autostart
    _force_windows(monkeypatch, autostart)
    xml = autostart._build_task_xml()
    settings = xml.split("<Settings>", 1)[1].split("</Settings>", 1)[0]
    assert "<Hidden>true</Hidden>" in settings


def test_python_for_task_prefers_pythonw(monkeypatch):
    from aletheia.gateway import autostart
    monkeypatch.setattr(autostart.sys, "executable", r"C:\venv\Scripts\python.exe")
    monkeypatch.setattr(autostart, "_exists", lambda p: str(p).endswith("pythonw.exe"))
    assert autostart._python_for_task().endswith("pythonw.exe")

    monkeypatch.setattr(autostart, "_exists", lambda p: False)
    assert autostart._python_for_task() == r"C:\venv\Scripts\python.exe"


def test_task_arguments_bootstrap_makes_venv_importable(monkeypatch):
    """When the task runs the base pythonw (≠ venv shim), the -c bootstrap MUST
    make the venv importable via site.addsitedir — NOT via __PYVENV_LAUNCHER__,
    which site.py reads at interpreter startup (before -c) so setting it inside
    the -c string is too late and leaves the base python venv-blind (the bug that
    made the watchdog exit 1 on every tick → could never resurrect a dead gateway).
    """
    from aletheia.gateway import autostart
    # venv shim as sys.executable; base pythonw as the resolved task interpreter.
    monkeypatch.setattr(autostart.sys, "executable", r"C:\proj\.venv\Scripts\python.exe")
    monkeypatch.setattr(autostart, "_exists", lambda p: str(p).endswith("pyvenv.cfg"))
    monkeypatch.setattr(autostart, "_python_for_task", lambda: r"C:\base\python\pythonw.exe")

    args = autostart._task_arguments()

    # (a) Uses site.addsitedir pointing at the venv's site-packages (effective now).
    assert "site.addsitedir(" in args
    assert r"\.venv\Lib\site-packages" in args
    # (b) Still imports + runs the gateway CLI.
    assert "from aletheia.main import app" in args
    assert "gateway" in args and "start" in args and "--no-browser" in args
    # (c) Must NOT rely on __PYVENV_LAUNCHER__ (the too-late mechanism we removed).
    assert "__PYVENV_LAUNCHER__" not in args


# --- 3. enable audits autostart_enabled (actor=human) ---

def test_enable_audits_human(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import autostart
    _force_windows(monkeypatch, autostart)
    monkeypatch.setattr(autostart.subprocess, "run", _Recorder(returncode=0, stdout="SUCCESS"))

    autostart.enable(source="cli")
    from aletheia.files.jsonl import read_jsonl
    events = [e for e in read_jsonl("audit.jsonl") if e.get("event_type") == "autostart_enabled"]
    assert events
    assert events[0]["actor"] == "human"
    assert events[0]["source"] == "cli"
    assert events[0]["entity_type"] == "gateway_autostart"


# --- 4. enable access-denied → honest dict + audit, never crashes ---

def test_enable_access_denied_graceful(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import autostart
    _force_windows(monkeypatch, autostart)
    monkeypatch.setattr(autostart.subprocess, "run",
                        _Recorder(returncode=1, stderr="ERROR: Access is denied."))

    result = autostart.enable(source="api")
    assert result["ok"] is False
    assert result["enabled"] is False
    assert result["reason"] == "access_denied"
    assert "denied" in result["detail"].lower()

    from aletheia.files.jsonl import read_jsonl
    assert any(e.get("event_type") == "autostart_enable_failed" for e in read_jsonl("audit.jsonl"))


# --- 5. disable argv + audit ---

def test_disable_argv_and_audit(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import autostart
    _force_windows(monkeypatch, autostart)
    rec = _Recorder(returncode=0, stdout="SUCCESS")
    monkeypatch.setattr(autostart.subprocess, "run", rec)

    result = autostart.disable(source="cli")
    assert result["ok"] is True
    args = rec.calls[0][0]
    assert args[0] == "schtasks"
    assert "/Delete" in args and "/TN" in args and "/F" in args
    assert autostart.TASK_NAME in args
    # schtasks /Delete must not flash a console window from API-route detached caller.
    assert rec.calls[0][1].get("creationflags", 0) & 0x08000000 == 0x08000000

    from aletheia.files.jsonl import read_jsonl
    assert any(e.get("event_type") == "autostart_disabled" for e in read_jsonl("audit.jsonl"))


# --- 6. disable when task absent → idempotent success ---

def test_disable_not_found_is_idempotent(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import autostart
    _force_windows(monkeypatch, autostart)
    monkeypatch.setattr(autostart.subprocess, "run",
                        _Recorder(returncode=1, stderr="ERROR: The system cannot find the file specified."))

    result = autostart.disable(source="api")
    assert result["ok"] is True  # absence is success, not an error


# --- 7. status: task installed → enabled True ---

def test_status_installed(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import autostart
    monkeypatch.setattr(autostart.platform, "system", lambda: "Windows")
    stdout = (
        "HostName:      DESKTOP\r\n"
        "TaskName:      \\Aletheia Trading OS Gateway\r\n"
        "Status:        Ready\r\n"
        "Schedule Type: At logon time\r\n"
    )
    rec = _Recorder(returncode=0, stdout=stdout)
    monkeypatch.setattr(autostart.subprocess, "run", rec)

    result = autostart.status()
    assert result["supported"] is True
    assert result["enabled"] is True
    assert result["task_name"] == autostart.TASK_NAME
    assert result["trigger"]  # best-effort trigger summary present
    # schtasks /Query must not flash a console window from API-route detached caller.
    assert rec.calls[0][1].get("creationflags", 0) & 0x08000000 == 0x08000000


# --- 8. status: task not installed → enabled False (disabled) ---

def test_status_not_installed(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import autostart
    monkeypatch.setattr(autostart.platform, "system", lambda: "Windows")
    monkeypatch.setattr(autostart.subprocess, "run",
                        _Recorder(returncode=1, stderr="ERROR: The system cannot find the file specified."))

    result = autostart.status()
    assert result["supported"] is True
    assert result["enabled"] is False
    assert result.get("reason") != "access_denied"


# --- 9. status: access denied → enabled None, never reported as enabled ---

def test_status_access_denied_is_honest(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import autostart
    monkeypatch.setattr(autostart.platform, "system", lambda: "Windows")
    monkeypatch.setattr(autostart.subprocess, "run",
                        _Recorder(returncode=1, stderr="ERROR: Access is denied."))

    result = autostart.status()
    assert result["supported"] is True
    assert result["enabled"] is None  # unknown — must NOT claim enabled or cleanly disabled
    assert result["reason"] == "access_denied"


# --- 10. non-Windows → all three are honest no-ops, never shell out ---

def test_non_windows_no_op(monkeypatch):
    from aletheia.gateway import autostart
    monkeypatch.setattr(autostart.platform, "system", lambda: "Linux")
    called = []
    monkeypatch.setattr(autostart.subprocess, "run", lambda *a, **k: called.append(1))

    s = autostart.status()
    e = autostart.enable(source="api")
    d = autostart.disable(source="api")
    assert s["supported"] is False
    assert e["supported"] is False and e["ok"] is False
    assert d["supported"] is False and d["ok"] is False
    assert called == []  # never invokes schtasks off-Windows


# --- 11. schtasks binary missing → graceful supported:false ---

def test_schtasks_missing_graceful(monkeypatch):
    from aletheia.gateway import autostart
    monkeypatch.setattr(autostart.platform, "system", lambda: "Windows")

    def boom(*a, **k):
        raise FileNotFoundError("schtasks")
    monkeypatch.setattr(autostart.subprocess, "run", boom)

    s = autostart.status()
    assert s["supported"] is False
    assert s.get("reason") == "schtasks_unavailable"
    # enable/disable also degrade without raising
    assert autostart.enable(source="api")["ok"] is False
    assert autostart.disable(source="api")["ok"] is False


# --- 12-15. API routes ---

async def _client():
    from httpx import AsyncClient, ASGITransport
    from aletheia.server.app import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_api_get_autostart_honest_passthrough(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import autostart
    monkeypatch.setattr(autostart, "status",
                        lambda: {"supported": True, "enabled": False, "task_name": autostart.TASK_NAME})
    async with await _client() as c:
        resp = await c.get("/api/gateway/autostart")
    assert resp.status_code == 200
    body = resp.json()
    assert body["supported"] is True
    assert body["enabled"] is False


async def test_api_post_enable_audits(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import autostart
    monkeypatch.setattr(autostart.platform, "system", lambda: "Windows")
    monkeypatch.setattr(autostart, "_python_for_task", lambda: r"C:\venv\Scripts\pythonw.exe")
    monkeypatch.setattr(autostart.subprocess, "run", _Recorder(returncode=0, stdout="SUCCESS"))
    async with await _client() as c:
        resp = await c.post("/api/gateway/autostart", json={"enabled": True})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True
    from aletheia.files.jsonl import read_jsonl
    evts = [e for e in read_jsonl("audit.jsonl") if e.get("event_type") == "autostart_enabled"]
    assert evts and evts[0]["source"] == "api"


async def test_api_post_disable_audits(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import autostart
    monkeypatch.setattr(autostart.platform, "system", lambda: "Windows")
    monkeypatch.setattr(autostart.subprocess, "run", _Recorder(returncode=0, stdout="SUCCESS"))
    async with await _client() as c:
        resp = await c.post("/api/gateway/autostart", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False
    from aletheia.files.jsonl import read_jsonl
    assert any(e.get("event_type") == "autostart_disabled" for e in read_jsonl("audit.jsonl"))


async def test_api_non_windows_supported_false(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import autostart
    monkeypatch.setattr(autostart.platform, "system", lambda: "Darwin")
    async with await _client() as c:
        resp = await c.get("/api/gateway/autostart")
    assert resp.status_code == 200
    assert resp.json()["supported"] is False


# --- 18. Governance guard: autostart must never be agent-callable ---

def test_autostart_not_agent_callable(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime.tool_registry import init_tool_registry
    from aletheia.agent_runtime import permissions
    init_tool_registry()
    forbidden = {"enable_autostart", "disable_autostart", "autostart",
                 "schtasks", "register_task", "create_task"}
    assert forbidden.isdisjoint(permissions.TOOL_REGISTRY.keys())

    import inspect
    from aletheia.agent_runtime import tool_registry
    src = inspect.getsource(tool_registry)
    assert "aletheia.gateway.autostart" not in src
