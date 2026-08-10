"""Focused duration instrumentation regressions for agent audit records."""

import ast
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


_NO_EXECUTION_LOGS = {
    ("market_observer.py", "reuse_terminal_opportunity"),
    ("paper_trader.py", "create_paper_proposal"),
    ("portfolio_agent.py", "get_positions"),
}


def _setup_db(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    sq.reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(tmp_path / "test.db", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    return conn


def test_elapsed_ms_uses_monotonic_clock(monkeypatch):
    import aletheia.agents.helpers as helpers

    monkeypatch.setattr(helpers.time, "monotonic", lambda: 100.125)
    assert helpers.elapsed_ms(100.0) == 125


def test_registered_agent_tool_producers_pass_duration_for_actual_tools():
    agent_dir = Path(__file__).parents[1] / "aletheia" / "agents"
    missing: list[str] = []
    for path in agent_dir.glob("*.py"):
        source = path.read_text()
        if "@register_agent" not in source or "log_tool(" not in source:
            continue
        for node in ast.walk(ast.parse(source, filename=str(path))):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id != "log_tool" or len(node.args) < 2:
                continue
            tool_name = node.args[1]
            if not isinstance(tool_name, ast.Constant) or not isinstance(tool_name.value, str):
                continue
            if tool_name.value.startswith("llm_") or (path.name, tool_name.value) in _NO_EXECUTION_LOGS:
                continue
            if not any(keyword.arg == "duration_ms" for keyword in node.keywords):
                missing.append(f"{path.name}:{node.lineno}:{tool_name.value}")
    assert not missing, "missing duration_ms: " + ", ".join(missing)


def test_tool_duration_is_persisted_without_backfilling_legacy_rows(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    conn.execute(
        "INSERT INTO agent_runs (id, agent_name, permission_mode, started_at) "
        "VALUES ('duration-run', 'test_agent', 'Observe', datetime('now'))"
    )
    conn.commit()

    from aletheia.agents.helpers import _release_run_counter, log_tool

    log_tool("duration-run", "timed_tool", "observe", True, duration_ms=125)
    log_tool("duration-run", "legacy_tool", "observe", True)
    _release_run_counter("duration-run")

    rows = conn.execute(
        "SELECT tool_name, duration_ms FROM agent_tool_calls "
        "WHERE run_id='duration-run' ORDER BY rowid"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("timed_tool", 125),
        ("legacy_tool", None),
    ]


@pytest.mark.asyncio
async def test_command_duration_uses_monotonic_clock_and_keeps_legacy_null(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    ticks = iter((100.0, 100.250))
    monkeypatch.setattr(
        "aletheia.agent_shell.executor.time",
        SimpleNamespace(time=time.time, monotonic=lambda: next(ticks)),
    )

    from aletheia.agent_shell.audit import record_command
    from aletheia.agent_shell.executor import execute_typed_command

    result = await execute_typed_command(
        "unknown_command",
        session_id="duration-session",
    )
    assert result.permitted is False
    record_command("duration-session", "legacy_command", {}, False, "legacy")

    rows = conn.execute(
        "SELECT command, duration_ms FROM agent_shell_commands "
        "WHERE session_id='duration-session' ORDER BY rowid"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("unknown_command", 250),
        ("legacy_command", None),
    ]
