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


def test_llm_tool_uses_completed_logical_inference_duration(tmp_path, monkeypatch):
    conn = _setup_db(tmp_path, monkeypatch)
    from aletheia.economics.schema import ensure_economics_schema

    db = ensure_economics_schema()
    db.execute(
        "INSERT INTO agent_runs (id,agent_name,permission_mode,started_at) VALUES (?,?,?,?)",
        ("llm-duration-run", "test_agent", "Observe", "2026-08-24T00:00:00+00:00"),
    )
    db.execute(
        """INSERT INTO llm_inference_calls (
            logical_call_id,schema_version,caller,prompt_template_id,prompt_version,
            prompt_static_sha256,rendered_prompt_sha256,context_adapter_id,tool_policy_id,
            allowed_tools_sha256,temperature,max_tokens,allow_model_fallback,status,
            attempt_count,retry_count,started_at,finished_at,metadata_json,created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "logical-duration", "test", "test_agent", "template", "v1", "a", "b",
            "none", "none", "c", 0.0, 1, 1, "success", 1, 0,
            "2026-08-24T00:00:00+00:00", "2026-08-24T00:00:01.250000+00:00",
            "{}", "2026-08-24T00:00:00+00:00",
        ),
    )
    db.commit()

    from aletheia.agents.helpers import _release_run_counter, log_tool

    log_tool(
        "llm-duration-run",
        "llm_complete",
        "observe",
        True,
        inference_call_id="logical-duration",
    )
    _release_run_counter("llm-duration-run")

    assert conn.execute(
        "SELECT duration_ms FROM agent_tool_calls WHERE run_id='llm-duration-run'"
    ).fetchone()[0] == 1250


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
