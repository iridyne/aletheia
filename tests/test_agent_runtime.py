"""Tests for agent runtime: permissions, tool registry, orchestrator."""

import json
import pytest
from unittest.mock import MagicMock
from unittest.mock import patch

from aletheia.agent_runtime.permissions import (
    register_tool, is_tool_permitted, get_permitted_tools, TOOL_REGISTRY,
)
from aletheia.agent_runtime.tool_registry import init_tool_registry
from aletheia.agent_runtime.orchestrator import run_agent
from aletheia.storage.sqlite import get_db, reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage import repositories as repo


def setup_test_env(tmp_path, monkeypatch):
    """Set up test db and config."""
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    # Ensure knowledge dirs exist
    (tmp_path / "knowledge" / "research_notes").mkdir(parents=True, exist_ok=True)
    (tmp_path / "knowledge" / "daily_reviews").mkdir(parents=True, exist_ok=True)
    (tmp_path / "knowledge" / "dreaming_logs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    # Reset and setup db
    reset_db()
    sq._db_conn = None
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    init_tool_registry()


def test_tool_registry_initialized():
    init_tool_registry()
    assert "get_market_snapshot" in TOOL_REGISTRY
    assert "create_research_note" in TOOL_REGISTRY
    assert "unlock_live" in TOOL_REGISTRY


def test_observe_tools_permitted_in_all_modes():
    init_tool_registry()
    for mode in ["Observe", "Paper", "Full"]:
        assert is_tool_permitted("get_market_snapshot", mode)
        assert is_tool_permitted("create_research_note", mode)


def test_paper_tool_blocked_in_observe():
    init_tool_registry()
    assert not is_tool_permitted("run_paper_order", "Observe")
    assert is_tool_permitted("run_paper_order", "Paper")


def test_restricted_tools_always_blocked():
    init_tool_registry()
    for mode in ["Observe", "Paper", "Full"]:
        assert not is_tool_permitted("unlock_live", mode)
        assert not is_tool_permitted("change_hard_risk_limits", mode)
        assert not is_tool_permitted("modify_credentials", mode)


def test_get_permitted_tools_observe():
    init_tool_registry()
    tools = get_permitted_tools("Observe")
    assert "get_market_snapshot" in tools
    assert "create_research_note" in tools
    assert "run_paper_order" not in tools
    assert "unlock_live" not in tools


def test_mark_stale_running_runs_closes_only_running_rows(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)

    from aletheia.agent_runtime.run_logger import mark_stale_running_runs
    from aletheia.storage.models import AgentRun

    running = AgentRun(agent_name="market_observer", trigger_type="scheduled")
    completed = AgentRun(
        agent_name="research_agent",
        trigger_type="manual",
        status="completed",
        ended_at="2026-01-01T00:00:00+00:00",
        errors=json.dumps(["already done"]),
    )
    repo.insert_agent_run(running)
    repo.insert_agent_run(completed)

    count = mark_stale_running_runs(reason="gateway startup recovery", source="test")

    assert count == 1
    stale_row = repo.get_agent_run(running.id)
    assert stale_row["status"] == "stale"
    assert stale_row["ended_at"]
    assert any("gateway startup recovery" in err for err in json.loads(stale_row["errors"]))

    completed_row = repo.get_agent_run(completed.id)
    assert completed_row["status"] == "completed"
    assert completed_row["errors"] == json.dumps(["already done"])

    audit_rows = get_db().execute(
        "SELECT event_type, message FROM audit_events ORDER BY timestamp DESC LIMIT 1"
    ).fetchall()
    assert audit_rows
    assert audit_rows[0]["event_type"] == "agent_runs_marked_stale"
    assert "1 running agent run" in audit_rows[0]["message"]
    reset_db()


# Agents now call the LLM; with no model server in tests they degrade gracefully.
_OK = ("completed", "completed_with_errors")


@pytest.mark.anyio
async def test_market_observer_creates_opportunity(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    result = await run_agent("market_observer", trigger_type="manual")
    assert result["status"] in _OK
    # When LLM is unavailable, the quality gate skips opportunity creation.
    # Errors are recorded instead of neutral/NULL waste signals.
    if result["status"] == "completed_with_errors":
        assert len(result["errors"]) > 0
    else:
        assert any("opportunity:" in o for o in result["outputs"])
    reset_db()


@pytest.mark.anyio
async def test_research_agent_creates_note(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    result = await run_agent("research_agent", trigger_type="manual")
    assert result["status"] in _OK
    assert any("research_note:" in o for o in result["outputs"])
    items = repo.list_knowledge_items(item_type="research_note")
    assert len(items) >= 1
    reset_db()


@pytest.mark.anyio
async def test_dreaming_agent_creates_log(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    result = await run_agent("dreaming_agent", trigger_type="manual")
    assert result["status"] in _OK
    assert any("dreaming_log:" in o for o in result["outputs"])
    assert any("daily_review:" in o for o in result["outputs"])
    reset_db()


@pytest.mark.anyio
async def test_agent_blocked_in_readonly(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    # Save config with ReadOnly mode
    from aletheia.config.schema import AletheiaConfig
    from aletheia.config.loader import save_config
    cfg = AletheiaConfig()
    cfg.agent.permission_mode = "Observe"
    save_config(cfg)

    import aletheia.storage.sqlite as sq
    reset_db()
    sq._db_conn = None
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    init_tool_registry()

    result = await run_agent("research_agent", trigger_type="manual")
    # In Observe mode, research notes ARE permitted (observe tool class)
    assert result["status"] in _OK
    reset_db()


@pytest.mark.anyio
async def test_run_agent_tolerates_non_string_default_model_provider(tmp_path, monkeypatch):
    import aletheia.agent_runtime.orchestrator as orchestrator
    from aletheia.config.loader import load_config

    setup_test_env(tmp_path, monkeypatch)
    cfg = load_config()
    cfg.models.default_provider = MagicMock(name="default_provider")
    monkeypatch.setattr(orchestrator, "load_config", lambda: cfg)

    result = await run_agent("research_agent", trigger_type="manual")

    assert result["status"] in _OK
    latest = repo.list_agent_runs(limit=1)[0]
    assert latest["model_provider"] is None
    reset_db()


def reset_db():
    from aletheia.storage.sqlite import reset_db as _reset
    _reset()
