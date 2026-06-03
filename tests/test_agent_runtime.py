"""Tests for agent runtime: permissions, tool registry, orchestrator."""

import pytest
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


def test_read_tools_permitted_in_all_modes():
    init_tool_registry()
    for mode in ["ReadOnly", "Research", "PaperAuto", "AllowEditing", "Auto"]:
        assert is_tool_permitted("get_market_snapshot", mode)


def test_research_tool_blocked_in_readonly():
    init_tool_registry()
    assert not is_tool_permitted("create_research_note", "ReadOnly")
    assert is_tool_permitted("create_research_note", "Research")


def test_paper_tool_blocked_in_research():
    init_tool_registry()
    assert not is_tool_permitted("run_paper_order", "Research")
    assert is_tool_permitted("run_paper_order", "PaperAuto")


def test_restricted_tools_always_blocked():
    init_tool_registry()
    for mode in ["ReadOnly", "Research", "PaperAuto", "AllowEditing", "Auto"]:
        assert not is_tool_permitted("unlock_live", mode)
        assert not is_tool_permitted("change_hard_risk_limits", mode)
        assert not is_tool_permitted("modify_credentials", mode)


def test_get_permitted_tools_research():
    init_tool_registry()
    tools = get_permitted_tools("Research")
    assert "get_market_snapshot" in tools
    assert "create_research_note" in tools
    assert "run_paper_order" not in tools
    assert "unlock_live" not in tools


# Agents now call the LLM; with no model server in tests they degrade gracefully
# (status "completed_with_errors") but MUST still produce their artifact.
_OK = ("completed", "completed_with_errors")


@pytest.mark.anyio
async def test_market_observer_creates_opportunity(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    result = await run_agent("market_observer", trigger_type="manual")
    assert result["status"] in _OK
    assert any("opportunity:" in o for o in result["outputs"])
    opps = repo.list_opportunities()
    assert len(opps) >= 1
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
    cfg.agent.permission_mode = "ReadOnly"
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
    # Research agent can't create notes in ReadOnly
    assert "research_note:" not in str(result["outputs"])
    reset_db()


def reset_db():
    from aletheia.storage.sqlite import reset_db as _reset
    _reset()
