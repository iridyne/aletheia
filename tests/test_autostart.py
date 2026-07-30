"""Tests for Windows Task Scheduler and macOS launchd gateway watchdogs.

Governance: human-triggered only, never agent-callable, runs the SAME gated
`gateway start` a human runs (live stays disabled, RunLevel LeastPrivilege).
schtasks is fully mocked here (no real task is created). Verifies argv + Task XML
correctness, UTF-16 encoding, three honest states, access-denied honesty,
non-Windows no-op, schtasks-missing graceful degradation, and audit on
human-triggered enable/disable.
"""

import os
import sqlite3
import plistlib
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
        if "/XML" in args and "/Create" in args:
            idx = args.index("/XML") + 1
            if idx < len(args):
                try:
                    self.xml_bytes = Path(args[idx]).read_bytes()
                except OSError:
                    pass
        return _FakeProc(stdout=self.stdout, returncode=self.returncode, stderr=self.stderr)


def _force_windows(monkeypatch, autostart, python=r"C:\venv\Scripts\pythonw.exe"):
    monkeypatch.setattr(autostart.platform, "system", lambda: "Windows")
    monkeypatch.setattr(autostart, "_python_for_task", lambda: python)


def _force_macos(monkeypatch, autostart, tmp_path):
    launch_agents = tmp_path / "Library" / "LaunchAgents"
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(autostart.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(autostart, "_launch_agents_dir", lambda: launch_agents)
    monkeypatch.setattr(autostart, "_PROJECT_ROOT", project_root)
    monkeypatch.setattr(autostart.sys, "executable", str(project_root / ".venv/bin/python"))
    monkeypatch.setattr(autostart.os, "getuid", lambda: 501, raising=False)
    return launch_agents, project_root


# --- 1. enable builds correct argv + Task XML ---

def test_enable_builds_correct_argv_and_xml(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import autostart
    _force_windows(monkeypatch, autostart)
    rec = _Recorder(returncode=0, stdout="SUCCESS")
    monkeypatch.setattr(autostart.subprocess, "run", rec)
    monkeypatch.setattr(autostart, "_verify_after_prime", lambda **kw: {"verified": True})

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
    assert "<TimeTrigger>" in xml
    assert "<Interval>PT1M</Interval>" in xml
    assert "<StopAtDurationEnd>false</StopAtDurationEnd>" in xml
    assert "<LogonType>InteractiveToken</LogonType>" in xml
    assert "<RunLevel>LeastPrivilege</RunLevel>" in xml
    assert "<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>" in xml
    assert "gateway" in xml and "start" in xml and "--no-browser" in xml
    assert "pythonw.exe" in xml
    assert "<Hidden>true</Hidden>" in xml
    # schtasks /Create itself must not flash a console window when called from a
    # detached parent (for example an inner uvicorn worker).
    assert rec.calls[0][1].get("creationflags", 0) & 0x08000000 == 0x08000000
    assert any("/Run" in call[0] for call in rec.calls)
    assert result["primed"] is True


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
    # repr() escapes backslashes, so single \ in path becomes \\ in the generated args string.
    assert r"\\.venv\\Lib\\site-packages" in args
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
    monkeypatch.setattr(autostart, "_verify_after_prime", lambda **kw: {"verified": True})

    autostart.enable(source="cli")
    from aletheia.files.jsonl import read_jsonl
    events = [e for e in read_jsonl("audit.jsonl") if e.get("event_type") == "autostart_enabled"]
    assert events
    assert events[0]["actor"] == "human"
    assert events[0]["source"] == "cli"
    assert events[0]["entity_type"] == "gateway_autostart"


def test_enable_still_succeeds_when_prime_run_fails(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import autostart
    _force_windows(monkeypatch, autostart)

    class PrimeFails(_Recorder):
        def __call__(self, args, **kwargs):
            self.calls.append((list(args), kwargs))
            if "/XML" in args:
                xml_path = args[args.index("/XML") + 1]
                self.xml_bytes = Path(xml_path).read_bytes()
            if "/Run" in args:
                return _FakeProc(returncode=1, stderr="ERROR: cannot run")
            return _FakeProc(returncode=0, stdout="SUCCESS")

    rec = PrimeFails()
    monkeypatch.setattr(autostart.subprocess, "run", rec)

    result = autostart.enable(source="cli")

    assert result["ok"] is True
    assert result["enabled"] is True
    assert result["primed"] is False
    assert result["prime_reason"] == "schtasks_error"


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


# --- 6b. localized schtasks output must survive decoding and stay idempotent ---

def _force_console_code_page(monkeypatch, encoding):
    """Pin the console code page so byte-level decoding is host-independent."""
    from aletheia.gateway import console_text
    monkeypatch.setattr(console_text, "console_encoding", lambda: encoding)


def test_decode_console_recovers_code_page_bytes(monkeypatch):
    """schtasks writes the console code page, not Python's locale encoding.

    Decoding cp936 bytes as UTF-8 produced mojibake, which hid the localized
    "not found" wording from the classifier.
    """
    from aletheia.gateway.console_text import decode_console
    _force_console_code_page(monkeypatch, "gbk")

    assert decode_console("错误: 系统找不到指定的文件。".encode("gbk")) == "错误: 系统找不到指定的文件。"
    # UTF-8 is tried first, so a tool that does emit UTF-8 is not mojibake'd.
    assert decode_console("已经是文本".encode("utf-8")) == "已经是文本"
    assert decode_console("UTF-16 输出".encode("utf-16")) == "UTF-16 输出"
    assert decode_console("passthrough") == "passthrough"
    assert decode_console(None) == ""


def test_disable_localized_not_found_bytes_is_idempotent(tmp_path, monkeypatch):
    """Simplified-Chinese Windows: absence must still read as success."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import autostart
    _force_windows(monkeypatch, autostart)
    _force_console_code_page(monkeypatch, "gbk")
    rec = _Recorder(returncode=1, stderr="错误: 系统找不到指定的文件。\r\r\n".encode("gbk"))
    monkeypatch.setattr(autostart.subprocess, "run", rec)

    result = autostart.disable(source="cli")
    assert result["ok"] is True
    assert result["enabled"] is False
    # The wording is recognized directly — no extra existence query needed.
    assert len(rec.calls) == 1


def test_disable_unrecognized_failure_wording_probes_real_existence(tmp_path, monkeypatch):
    """An unknown locale's wording is resolved by the /Query exit code, not text."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import autostart
    _force_windows(monkeypatch, autostart)
    _force_console_code_page(monkeypatch, "cp932")
    # Japanese "the system cannot find the file specified", which no marker matches.
    japanese = "エラー: 指定されたファイルが見つかりません。".encode("cp932")
    calls = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        return _FakeProc(returncode=1, stderr=japanese)

    monkeypatch.setattr(autostart.subprocess, "run", fake_run)
    result = autostart.disable(source="cli")
    assert result["ok"] is True
    assert result["enabled"] is False
    assert any("/Query" in call for call in calls)  # existence proved, not guessed


def test_disable_keeps_failure_when_task_still_exists(tmp_path, monkeypatch):
    """The existence probe must not launder a real failure into success."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import autostart
    _force_windows(monkeypatch, autostart)
    _force_console_code_page(monkeypatch, "cp932")
    japanese = "エラー: 不明なエラーが発生しました。".encode("cp932")

    def fake_run(args, **kwargs):
        if "/Query" in args:
            return _FakeProc(returncode=0, stdout="<Task />")  # task is still registered
        return _FakeProc(returncode=1, stderr=japanese)

    monkeypatch.setattr(autostart.subprocess, "run", fake_run)
    result = autostart.disable(source="cli")
    assert result["ok"] is False
    assert result["reason"] == "schtasks_error"
    assert "エラー" in result["detail"]  # readable, not mojibake


# --- 7. status: task installed → enabled True (XML-based) ---

def test_status_installed(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import autostart
    monkeypatch.setattr(autostart.platform, "system", lambda: "Windows")

    xml_output = (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        f'<Task xmlns="{autostart._XML_NS}">\n'
        '  <Settings><Enabled>true</Enabled></Settings>\n'
        '  <Triggers><LogonTrigger><Repetition><Interval>PT2M</Interval></Repetition></LogonTrigger></Triggers>\n'
        '  <Actions><Exec><Command>pythonw.exe</Command><Arguments>-m aletheia.main gateway start</Arguments></Exec></Actions>\n'
        '</Task>'
    )
    list_output = (
        "HostName:      DESKTOP\r\n"
        "TaskName:      \\Aletheia Trading OS Gateway\r\n"
        "Status:        Ready\r\n"
        "Schedule Type: At logon time\r\n"
        "Last Run Time: 2026/6/12 14:31:00\r\n"
        "Last Result:   0\r\n"
    )

    call_count = [0]
    def fake_run(args, **kwargs):
        call_count[0] += 1
        if "/XML" in args:
            return _FakeProc(returncode=0, stdout=xml_output)
        return _FakeProc(returncode=0, stdout=list_output)

    monkeypatch.setattr(autostart.subprocess, "run", fake_run)

    result = autostart.status()
    assert result["supported"] is True
    assert result["enabled"] is True
    assert result["task_name"] == autostart.TASK_NAME
    assert result["interval"] == "PT2M"
    assert result["last_run_time"] == "2026/6/12 14:31:00"
    assert result["last_result"] == "0"
    assert result["primed"] is True
    assert call_count[0] == 2  # XML query + LIST query


def test_status_installed_but_never_run_is_not_primed(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import autostart
    monkeypatch.setattr(autostart.platform, "system", lambda: "Windows")

    xml_output = (
        f'<Task xmlns="{autostart._XML_NS}">'
        '<Settings><Enabled>true</Enabled></Settings>'
        '</Task>'
    )
    list_output = (
        "TaskName:      \\Aletheia Trading OS Gateway\r\n"
        "Status:        Ready\r\n"
        "Last Run Time: 1999/11/30 0:00:00\r\n"
        "Last Result:   267011\r\n"
    )

    def fake_run(args, **kwargs):
        if "/XML" in args:
            return _FakeProc(returncode=0, stdout=xml_output)
        return _FakeProc(returncode=0, stdout=list_output)

    monkeypatch.setattr(autostart.subprocess, "run", fake_run)

    result = autostart.status()
    assert result["enabled"] is True
    assert result["primed"] is False
    assert result["last_result"] == "267011"


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


# --- 10. unsupported OS → all three are honest no-ops, never shell out ---

def test_unsupported_linux_no_op(monkeypatch):
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


# --- 12. XML status parsing ---

def test_parse_xml_status():
    from aletheia.gateway.autostart import _parse_xml_status, _XML_NS
    xml = (
        f'<Task xmlns="{_XML_NS}">'
        '<Settings><Enabled>true</Enabled></Settings>'
        '<Triggers><LogonTrigger><Repetition><Interval>PT2M</Interval></Repetition></LogonTrigger></Triggers>'
        '<Actions><Exec><Command>C:\\python\\pythonw.exe</Command>'
        '<Arguments>-m aletheia.main gateway start --no-browser</Arguments></Exec></Actions>'
        '</Task>'
    )
    result = _parse_xml_status(xml)
    assert result["task_enabled"] is True
    assert result["interval"] == "PT2M"
    assert "pythonw.exe" in result["command"]
    assert "--no-browser" in result["arguments"]


def test_parse_xml_status_invalid_xml():
    from aletheia.gateway.autostart import _parse_xml_status
    assert _parse_xml_status("not xml at all") == {}


def test_parse_xml_status_disabled():
    from aletheia.gateway.autostart import _parse_xml_status, _XML_NS
    xml = f'<Task xmlns="{_XML_NS}"><Settings><Enabled>false</Enabled></Settings></Task>'
    result = _parse_xml_status(xml)
    assert result["task_enabled"] is False


# --- 13. Watchdog interval is 1 minute (minimum allowed by Task Scheduler) ---

def test_watchdog_interval_is_1_minute():
    from aletheia.gateway.autostart import WATCHDOG_INTERVAL
    assert WATCHDOG_INTERVAL == "PT1M"


def test_xml_contains_1m_interval(monkeypatch):
    from aletheia.gateway import autostart
    _force_windows(monkeypatch, autostart)
    xml = autostart._build_task_xml()
    assert "<Interval>PT1M</Interval>" in xml


# --- 14. write_watchdog_tick ---

def test_write_watchdog_tick(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway.autostart import write_watchdog_tick
    from aletheia.files.jsonl import read_jsonl

    write_watchdog_tick("started", exit_code=0, duration_ms=1200)
    write_watchdog_tick("already_running", exit_code=0, duration_ms=50)

    entries = list(read_jsonl("watchdog.jsonl"))
    assert len(entries) == 2
    assert entries[0]["action"] == "started"
    assert entries[1]["action"] == "already_running"


# --- 15. verify_after_prime ---

def test_verify_after_prime_success(monkeypatch):
    from aletheia.gateway import autostart
    monkeypatch.setattr("aletheia.gateway.pidfile.is_running", lambda: True)
    monkeypatch.setattr("aletheia.gateway.pidfile.port_in_use", lambda p, h: True)

    from aletheia.config.loader import load_config
    monkeypatch.setattr(autostart, "_verify_after_prime", lambda **kw: {"verified": True})
    result = autostart._verify_after_prime(timeout=1.0)
    assert result["verified"] is True


def test_verify_after_prime_timeout(monkeypatch):
    from aletheia.gateway import autostart
    monkeypatch.setattr("aletheia.gateway.pidfile.is_running", lambda: False)
    monkeypatch.setattr("aletheia.gateway.pidfile.port_in_use", lambda p, h: False)

    from unittest.mock import patch
    with patch.object(autostart, "time") as mock_time:
        mock_time.monotonic = lambda: 100.0  # always past deadline
        mock_time.sleep = lambda s: None

    result = autostart._verify_after_prime(timeout=0.1)
    assert result["verified"] is False
    assert "verify_detail" in result


# --- 16. macOS launchd support ---

def test_macos_plist_uses_safe_periodic_gated_start(tmp_path, monkeypatch):
    from aletheia.gateway import autostart

    _, project_root = _force_macos(monkeypatch, autostart, tmp_path)
    payload = autostart._build_launchd_plist()

    assert payload["Label"] == autostart.LAUNCHD_LABEL
    assert payload["ProgramArguments"] == [
        str(project_root / ".venv/bin/python"),
        "-m", "aletheia.main", "gateway", "start", "--no-browser",
    ]
    assert payload["WorkingDirectory"] == str(project_root)
    assert payload["RunAtLoad"] is True
    assert payload["StartInterval"] == 60
    assert payload["AbandonProcessGroup"] is True
    assert "KeepAlive" not in payload
    assert "EnvironmentVariables" not in payload
    assert "api_key" not in plistlib.dumps(payload).decode("utf-8").lower()


def test_macos_enable_writes_plist_bootstraps_kickstarts_and_audits(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import autostart
    from aletheia.files.jsonl import read_jsonl

    launch_agents, _ = _force_macos(monkeypatch, autostart, tmp_path)
    calls: list[list[str]] = []

    def run(args, **kwargs):
        calls.append(list(args))
        if args[1] == "print":
            return _FakeProc(
                stdout="state = not running\nruns = 1\nlast exit code = 0\n",
                returncode=0,
            )
        if args[1] == "bootout":
            return _FakeProc(stderr="Could not find service", returncode=113)
        return _FakeProc(returncode=0)

    monkeypatch.setattr(autostart.subprocess, "run", run)
    monkeypatch.setattr(autostart, "_verify_after_prime", lambda **kw: {"verified": True})

    result = autostart.enable(source="cli")

    plist_path = launch_agents / f"{autostart.LAUNCHD_LABEL}.plist"
    assert plist_path.exists()
    if os.name != "nt":
        assert plist_path.stat().st_mode & 0o777 == 0o644
    assert plistlib.loads(plist_path.read_bytes())["Label"] == autostart.LAUNCHD_LABEL
    assert any(call[1] == "bootstrap" for call in calls)
    assert any(call[1] == "kickstart" for call in calls)
    assert result["ok"] is True and result["enabled"] is True
    assert result["platform"] == "launchd"
    assert result["primed"] is True and result["verified"] is True
    assert any(e.get("event_type") == "autostart_enabled" for e in read_jsonl("audit.jsonl"))


def test_macos_status_installed_but_not_loaded_is_honest(tmp_path, monkeypatch):
    from aletheia.gateway import autostart

    launch_agents, _ = _force_macos(monkeypatch, autostart, tmp_path)
    launch_agents.mkdir(parents=True)
    plist_path = launch_agents / f"{autostart.LAUNCHD_LABEL}.plist"
    plist_path.write_bytes(plistlib.dumps(autostart._build_launchd_plist()))
    monkeypatch.setattr(
        autostart.subprocess,
        "run",
        lambda *args, **kwargs: _FakeProc(returncode=113, stderr="Could not find service"),
    )

    result = autostart.status()

    assert result["supported"] is True
    assert result["enabled"] is False
    assert result["installed"] is True
    assert result["reason"] == "not_loaded"


def test_macos_disable_boots_out_and_removes_plist(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import autostart
    from aletheia.files.jsonl import read_jsonl

    launch_agents, _ = _force_macos(monkeypatch, autostart, tmp_path)
    launch_agents.mkdir(parents=True)
    plist_path = launch_agents / f"{autostart.LAUNCHD_LABEL}.plist"
    plist_path.write_bytes(plistlib.dumps(autostart._build_launchd_plist()))
    calls: list[list[str]] = []

    def run(args, **kwargs):
        calls.append(list(args))
        return _FakeProc(returncode=0)

    monkeypatch.setattr(autostart.subprocess, "run", run)

    result = autostart.disable(source="api")

    assert result == {
        "supported": True, "enabled": False, "ok": True, "platform": "launchd"
    }
    assert not plist_path.exists()
    assert any(call[1] == "bootout" for call in calls)
    assert any(e.get("event_type") == "autostart_disabled" for e in read_jsonl("audit.jsonl"))


def test_macos_status_loaded_with_stale_path_reports_drift(tmp_path, monkeypatch):
    from aletheia.gateway import autostart

    launch_agents, _ = _force_macos(monkeypatch, autostart, tmp_path)
    launch_agents.mkdir(parents=True)
    payload = autostart._build_launchd_plist()
    payload["WorkingDirectory"] = "/stale/project"
    (launch_agents / f"{autostart.LAUNCHD_LABEL}.plist").write_bytes(
        plistlib.dumps(payload)
    )
    monkeypatch.setattr(
        autostart.subprocess,
        "run",
        lambda *args, **kwargs: _FakeProc(
            stdout="state = not running\nruns = 2\nlast exit code = 0\n",
            returncode=0,
        ),
    )

    result = autostart.status()

    assert result["enabled"] is True
    assert result["config_valid"] is False
    assert result["reason"] == "config_drift"


def test_macos_enable_bootstrap_failure_is_honest_and_audited(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import autostart
    from aletheia.files.jsonl import read_jsonl

    _force_macos(monkeypatch, autostart, tmp_path)

    def run(args, **kwargs):
        if args[1] == "bootout":
            return _FakeProc(returncode=113, stderr="Could not find service")
        if args[1] == "bootstrap":
            return _FakeProc(returncode=5, stderr="Bootstrap failed: input/output error")
        raise AssertionError(args)

    monkeypatch.setattr(autostart.subprocess, "run", run)

    result = autostart.enable(source="api")

    assert result["ok"] is False
    assert result["enabled"] is False
    assert result["installed"] is False
    assert not autostart._launchd_plist_path().exists()
    assert result["reason"] == "launchctl_error"
    assert any(
        event.get("event_type") == "autostart_enable_failed"
        for event in read_jsonl("audit.jsonl")
    )


def test_parse_launchctl_status_short_lived_success_is_primed():
    from aletheia.gateway.autostart import _parse_launchctl_status

    parsed = _parse_launchctl_status(
        "state = not running\nruns = 12\nlast exit code = 0\npid = 0\n"
    )

    assert parsed == {
        "status": "not running",
        "runs": 12,
        "last_result": "0",
        "pid": 0,
        "primed": True,
    }


# --- 19. macOS legacy label migration ---

def test_macos_enable_boots_out_legacy_labels(tmp_path, monkeypatch):
    """enable() must bootout and remove every known legacy label before installing."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import autostart

    launch_agents, _ = _force_macos(monkeypatch, autostart, tmp_path)
    launch_agents.mkdir(parents=True)

    # Plant a legacy plist on disk.
    legacy_label = autostart._LEGACY_LAUNCHD_LABELS[0]
    legacy_plist = launch_agents / f"{legacy_label}.plist"
    import plistlib as pl
    legacy_plist.write_bytes(pl.dumps({"Label": legacy_label, "ProgramArguments": ["/old/uv"]}))

    calls: list[list[str]] = []

    def run(args, **kwargs):
        calls.append(list(args))
        cmd = args[1] if len(args) > 1 else ""
        if cmd == "print":
            return _FakeProc(stdout="state = not running\nruns = 1\nlast exit code = 0\n", returncode=0)
        if cmd == "bootout":
            return _FakeProc(returncode=0)
        return _FakeProc(returncode=0)

    monkeypatch.setattr(autostart.subprocess, "run", run)
    monkeypatch.setattr(autostart, "_verify_after_prime", lambda **kw: {"verified": True})

    result = autostart.enable(source="cli")

    assert result["ok"] is True
    booted_targets = [c[2] for c in calls if c[1] == "bootout"]
    assert any(legacy_label in t for t in booted_targets), f"legacy bootout not called; calls={calls}"
    assert not legacy_plist.exists(), "legacy plist must be removed"


def test_macos_enable_legacy_absent_is_ok(tmp_path, monkeypatch):
    """enable() succeeds cleanly when legacy labels are already absent."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import autostart

    _force_macos(monkeypatch, autostart, tmp_path)

    def run(args, **kwargs):
        cmd = args[1] if len(args) > 1 else ""
        if cmd == "print":
            return _FakeProc(stdout="state = not running\nruns = 1\nlast exit code = 0\n", returncode=0)
        if cmd == "bootout":
            # legacy labels return not_found; current label also not found initially
            return _FakeProc(returncode=113, stderr="Could not find service")
        return _FakeProc(returncode=0)

    monkeypatch.setattr(autostart.subprocess, "run", run)
    monkeypatch.setattr(autostart, "_verify_after_prime", lambda **kw: {"verified": True})

    result = autostart.enable(source="cli")
    assert result["ok"] is True


def test_macos_enable_legacy_permission_denied_is_honest(tmp_path, monkeypatch):
    """If removing a legacy label is access-denied, enable() returns ok=False honestly."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import autostart

    _force_macos(monkeypatch, autostart, tmp_path)
    legacy_label = autostart._LEGACY_LAUNCHD_LABELS[0]

    def run(args, **kwargs):
        cmd = args[1] if len(args) > 1 else ""
        if cmd == "bootout" and legacy_label in args[-1]:
            return _FakeProc(returncode=1, stderr="Operation not permitted")
        return _FakeProc(returncode=0)

    monkeypatch.setattr(autostart.subprocess, "run", run)

    result = autostart.enable(source="cli")
    assert result["ok"] is False
    assert result["reason"] == "access_denied"


def test_macos_enable_idempotent_reenable(tmp_path, monkeypatch):
    """Calling enable() twice in a row must succeed both times (idempotent)."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import autostart

    _force_macos(monkeypatch, autostart, tmp_path)

    def run(args, **kwargs):
        cmd = args[1] if len(args) > 1 else ""
        if cmd == "print":
            return _FakeProc(stdout="state = not running\nruns = 1\nlast exit code = 0\n", returncode=0)
        if cmd == "bootout":
            return _FakeProc(returncode=113, stderr="Could not find service")
        return _FakeProc(returncode=0)

    monkeypatch.setattr(autostart.subprocess, "run", run)
    monkeypatch.setattr(autostart, "_verify_after_prime", lambda **kw: {"verified": True})

    r1 = autostart.enable(source="cli")
    r2 = autostart.enable(source="cli")
    assert r1["ok"] is True
    assert r2["ok"] is True


def test_macos_disable_also_removes_legacy_labels(tmp_path, monkeypatch):
    """disable() must also bootout legacy labels so no stale watchdog remains."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import autostart

    launch_agents, _ = _force_macos(monkeypatch, autostart, tmp_path)
    launch_agents.mkdir(parents=True)

    legacy_label = autostart._LEGACY_LAUNCHD_LABELS[0]
    legacy_plist = launch_agents / f"{legacy_label}.plist"
    import plistlib as pl
    legacy_plist.write_bytes(pl.dumps({"Label": legacy_label}))

    calls: list[list[str]] = []

    def run(args, **kwargs):
        calls.append(list(args))
        return _FakeProc(returncode=0)

    monkeypatch.setattr(autostart.subprocess, "run", run)

    result = autostart.disable(source="api")

    assert result["ok"] is True
    booted_targets = [c[2] for c in calls if len(c) > 2 and c[1] == "bootout"]
    assert any(legacy_label in t for t in booted_targets), f"legacy bootout missing; calls={calls}"
    assert not legacy_plist.exists(), "legacy plist must be removed on disable"


# --- 20. Governance guard: autostart must never be agent-callable ---

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
