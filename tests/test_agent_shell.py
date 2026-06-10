"""Tests for the Agent Shell (Pi Agent, Operator Layer Track B Stage 1)."""

import pytest
import sqlite3
from unittest.mock import patch, AsyncMock

from aletheia.agent_shell.registry import COMMANDS, get_command, FORBIDDEN_INTENTS
from aletheia.agent_shell.permissions import check_permission, allowed_commands
from aletheia.agent_shell.intent import _classify_keyword, CommandIntent
from aletheia.agent_shell.context import build_app_context, context_summary_text
from aletheia.agent_shell.synthesis import _format_fallback


# --- Registry tests ---

def test_registry_has_11_commands():
    assert len(COMMANDS) == 11


def test_registry_all_read_commands_require_observe():
    read_cmds = [c for c in COMMANDS.values() if c.risk_level == "read"]
    assert len(read_cmds) == 6
    for cmd in read_cmds:
        assert cmd.required_permission == "Observe"


def test_registry_operational_commands_require_paper():
    ops = [c for c in COMMANDS.values() if c.risk_level == "operational"]
    assert len(ops) == 2
    for cmd in ops:
        assert cmd.required_permission == "Paper"


def test_get_command_returns_none_for_unknown():
    assert get_command("nonexistent") is None


# --- Permission tests ---

def test_observe_allows_read_commands():
    allowed, reason = check_permission("get_system_status", "Observe")
    assert allowed is True
    assert reason is None


def test_observe_denies_pause():
    allowed, reason = check_permission("pause_agents", "Observe")
    assert allowed is False
    assert "Paper" in reason


def test_observe_denies_create_note():
    allowed, reason = check_permission("create_research_note", "Observe")
    assert allowed is False


def test_paper_allows_all_11():
    for cmd_name in COMMANDS:
        allowed, reason = check_permission(cmd_name, "Paper")
        assert allowed is True, f"{cmd_name} should be allowed in Paper mode"


def test_full_allows_all_11():
    for cmd_name in COMMANDS:
        allowed, reason = check_permission(cmd_name, "Full")
        assert allowed is True


def test_forbidden_always_denied():
    for intent in FORBIDDEN_INTENTS:
        allowed, reason = check_permission(intent, "Full")
        assert allowed is False
        assert "permanently forbidden" in reason


def test_allowed_commands_observe():
    cmds = allowed_commands("Observe")
    assert "get_system_status" in cmds
    assert "pause_agents" not in cmds
    assert len(cmds) == 6


def test_allowed_commands_paper():
    cmds = allowed_commands("Paper")
    assert len(cmds) == 11


# --- Intent keyword fallback tests ---

def test_keyword_status():
    intent = _classify_keyword("what is the system status?")
    assert intent.command == "get_system_status"


def test_keyword_pause():
    intent = _classify_keyword("pause everything")
    assert intent.command == "pause_agents"


def test_keyword_market():
    intent = _classify_keyword("show me BTC price")
    assert intent.command == "get_market_snapshot"


def test_keyword_forbidden():
    intent = _classify_keyword("enable live trading now")
    assert intent.command == "forbidden"


def test_keyword_unknown():
    intent = _classify_keyword("hello world how are you today")
    assert intent.command == "query"


def test_keyword_positions():
    intent = _classify_keyword("show my positions")
    assert intent.command == "get_positions"


def test_keyword_resume():
    intent = _classify_keyword("resume agents please")
    assert intent.command == "resume_agents"


# --- Synthesis fallback tests ---

def test_fallback_error():
    result = _format_fallback({"error": "something broke"})
    assert "something broke" in result


def test_fallback_message():
    result = _format_fallback({"message": "Agents paused."})
    assert result == "Agents paused."


def test_fallback_list():
    result = _format_fallback({"positions": [{"symbol": "BTC/USDT", "pnl": 5.0}]})
    assert "BTC/USDT" in result


# --- Context tests ---

@patch("aletheia.agent_shell.context.load_config")
@patch("aletheia.agent_runtime.health.compute_health",
       return_value={"healthy": True, "issues": []})
def test_build_context_system_section(mock_health, mock_cfg):
    from unittest.mock import MagicMock
    cfg = MagicMock()
    cfg.agent.permission_mode = "Paper"
    cfg.live.state = "live_disabled"
    cfg.runtime.paused = False
    cfg.runtime.kill_switch_active = False
    cfg.exchange.mode = "demo"
    mock_cfg.return_value = cfg

    ctx = build_app_context(["system"])

    assert "system" in ctx
    assert ctx["system"]["healthy"] is True
    assert ctx["system"]["permission_mode"] == "Paper"


def test_context_summary_text():
    ctx = {
        "system": {"healthy": True, "issues": [], "permission_mode": "Paper", "paused": False},
        "market": [{"symbol": "BTC/USDT", "price": 65000}],
        "agents": {"total_recent": 5, "failed_recent": 1},
        "trading": {"open_positions": [{"symbol": "SOL/USDT"}]},
    }
    text = context_summary_text(ctx)
    assert "healthy" in text
    assert "BTC/USDT" in text
    assert "5 recent runs" in text


# --- Runtime integration test (mocked LLM) ---

@pytest.mark.asyncio
async def test_runtime_handle_status_query():
    """Full cycle: 'what is system status' → keyword → get_system_status → response."""
    from unittest.mock import MagicMock

    db = MagicMock()
    cfg = MagicMock()
    cfg.agent.permission_mode = "Paper"
    cfg.live.state = "live_disabled"
    cfg.runtime.paused = False
    cfg.runtime.kill_switch_active = False
    cfg.runtime.scheduler_enabled = True
    cfg.exchange.mode = "demo"
    cfg.risk.max_daily_loss_pct = 1.0
    cfg.risk.max_demo_position_notional_usdt = 200.0
    cfg.risk.max_open_positions_demo = 6

    with patch("aletheia.agent_shell.sessions.get_db", return_value=db), \
         patch("aletheia.agent_shell.audit.get_db", return_value=db), \
         patch("aletheia.config.loader.load_config", return_value=cfg), \
         patch("aletheia.agent_runtime.health.compute_health",
               return_value={"healthy": True, "issues": []}), \
         patch("aletheia.storage.repositories._select_all", return_value=[]), \
         patch("aletheia.agents.helpers.llm_structured",
               new_callable=AsyncMock, return_value=(None, "unavailable")), \
         patch("aletheia.agents.helpers.llm_complete",
               new_callable=AsyncMock, return_value=(None, "unavailable")):

        from aletheia.agent_shell.runtime import AgentShellRuntime
        runtime = AgentShellRuntime()
        resp = await runtime.handle("what is the system status?")

    assert resp.intent == "get_system_status"
    assert "health" in resp.response.lower()
