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


def test_do_stop_tree_kills_and_waits(tmp_path, monkeypatch, capsys):
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import lifecycle
    from aletheia.gateway import pidfile

    pidfile.pid_path().write_text("4242", encoding="utf-8")
    monkeypatch.setattr(lifecycle, "is_running", lambda: True)
    killed = []
    monkeypatch.setattr(lifecycle, "kill_pid_tree", lambda pid: killed.append(pid))
    monkeypatch.setattr(lifecycle, "find_gateway_orphans", lambda port: [])
    monkeypatch.setattr(lifecycle, "wait_port_free", lambda port, host, timeout=10.0: True)

    lifecycle.do_stop()

    assert 4242 in killed                 # supervisor tree-killed
    assert pidfile.read_pid() is None     # pidfile cleared
    out = capsys.readouterr().out
    assert "stopped" in out.lower()

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
    monkeypatch.setattr(lifecycle, "find_gateway_orphans", lambda port: [999])
    monkeypatch.setattr(lifecycle, "wait_port_free", lambda port, host, timeout=10.0: True)

    lifecycle.do_stop()
    assert killed == [999]  # orphan cleaned even with no pidfile


def test_do_stop_reports_when_port_stuck(tmp_path, monkeypatch, capsys):
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import lifecycle
    from aletheia.gateway import pidfile

    pidfile.pid_path().write_text("4242", encoding="utf-8")
    monkeypatch.setattr(lifecycle, "is_running", lambda: True)
    monkeypatch.setattr(lifecycle, "kill_pid_tree", lambda pid: None)
    monkeypatch.setattr(lifecycle, "find_gateway_orphans", lambda port: [])
    monkeypatch.setattr(lifecycle, "wait_port_free", lambda port, host, timeout=10.0: False)

    lifecycle.do_stop()
    err = capsys.readouterr().err
    assert "still in use" in err.lower()


def test_do_stop_not_running_message(tmp_path, monkeypatch, capsys):
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import lifecycle
    monkeypatch.setattr(lifecycle, "is_running", lambda: False)
    monkeypatch.setattr(lifecycle, "find_gateway_orphans", lambda port: [])
    # Nothing tracked AND port genuinely free → "not running".
    monkeypatch.setattr(lifecycle, "port_in_use", lambda port, host="127.0.0.1": False)
    monkeypatch.setattr(lifecycle, "wait_port_free", lambda port, host, timeout=10.0: True)

    lifecycle.do_stop()
    out = capsys.readouterr().out
    assert "not running" in out.lower()


def test_do_start_refuses_when_port_taken_and_untracked(tmp_path, monkeypatch, capsys):
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import lifecycle
    monkeypatch.setattr(lifecycle, "is_running", lambda: False)
    monkeypatch.setattr(lifecycle, "port_in_use", lambda port, host="127.0.0.1": True)
    spawned = []
    monkeypatch.setattr(lifecycle, "_spawn_background", lambda nb: spawned.append(True))

    with pytest.raises(typer.Exit):
        lifecycle.do_start(no_browser=True)
    assert spawned == []  # never spawned a doomed supervisor
    err = capsys.readouterr().err
    assert "in use" in err.lower()


def test_do_start_proceeds_when_port_free(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.gateway import lifecycle
    monkeypatch.setattr(lifecycle, "is_running", lambda: False)
    monkeypatch.setattr(lifecycle, "port_in_use", lambda port, host="127.0.0.1": False)
    spawned = []
    monkeypatch.setattr(lifecycle, "_spawn_background", lambda nb: spawned.append(True))

    lifecycle.do_start(no_browser=True)
    assert spawned == [True]
