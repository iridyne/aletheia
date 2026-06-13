"""Tests for the Agent Shell (Pi Agent, Operator Layer Track B Stage 1)."""

import json
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from aletheia.agent_shell.registry import COMMANDS, get_command, FORBIDDEN_INTENTS
from aletheia.agent_shell.permissions import check_permission, allowed_commands
from aletheia.agent_shell.intent import _classify_keyword, CommandIntent
from aletheia.agent_shell.context import build_app_context, context_summary_text, sections_for_profile
from aletheia.agent_shell.synthesis import _format_fallback


def _init_agent_shell_db(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.sqlite import reset_db

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    return conn


# --- Registry tests ---

def test_registry_has_32_commands():
    assert len(COMMANDS) == 38


def test_registry_all_read_commands_require_observe():
    read_cmds = [c for c in COMMANDS.values() if c.risk_level == "read"]
    assert len(read_cmds) == 10
    for cmd in read_cmds:
        assert cmd.required_permission == "Observe"


def test_registry_operational_commands_require_paper():
    ops = [c for c in COMMANDS.values() if c.risk_level == "operational"]
    assert len(ops) == 15
    observe_ops = {c.name for c in ops if c.required_permission == "Observe"}
    paper_ops = {c.name for c in ops if c.required_permission == "Paper"}
    assert observe_ops == {
        "run_market_scan",
        "rerun_failed_agent_task",
        "propose_command_center_behavior_change",
    }
    assert paper_ops == {
        "pause_agents",
        "resume_agents",
        "set_symbol_observe_only",
        "create_paper_experiment",
        "reconcile_positions",
        "pause_paper_strategy",
        "propose_strategy_promotion",
        "propose_prompt_change",
        "propose_schedule_change",
        "propose_live_limited_enablement",
        "propose_risk_boundary_change",
        "propose_extension_enablement",
    }


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


def test_paper_allows_all_15():
    for cmd_name in COMMANDS:
        allowed, reason = check_permission(cmd_name, "Paper")
        assert allowed is True, f"{cmd_name} should be allowed in Paper mode"


def test_full_allows_all_15():
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
    assert "create_review_report" in cmds
    assert "create_diagnostic_report" in cmds
    assert len(cmds) == 16


def test_allowed_commands_paper():
    cmds = allowed_commands("Paper")
    assert len(cmds) == 38


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


def test_keyword_operator_status():
    intent = _classify_keyword("show the operator heartbeat")
    assert intent.command == "get_operator_status"


def test_keyword_strategy_lifecycle():
    intent = _classify_keyword("show the strategy lifecycle")
    assert intent.command == "get_strategy_lifecycle"


def test_keyword_live_candidate_status_is_not_forbidden():
    intent = _classify_keyword("show the live candidate strategy status")
    assert intent.command == "get_strategy_lifecycle"


def test_keyword_failed_tasks():
    intent = _classify_keyword("review failed tasks")
    assert intent.command == "get_failed_tasks"


def test_keyword_recommendations():
    intent = _classify_keyword("what recommendations do you have")
    assert intent.command == "get_recommendations"


def test_keyword_suggestions_map_to_recommendations():
    intent = _classify_keyword("what suggestions do you have")
    assert intent.command == "get_recommendations"


def test_keyword_improvement_proposal():
    intent = _classify_keyword("create an improvement proposal")
    assert intent.command == "create_improvement_proposal"


def test_keyword_dreaming_log():
    intent = _classify_keyword("write a dreaming log")
    assert intent.command == "create_dreaming_log"


def test_keyword_strategy_draft():
    intent = _classify_keyword("create a strategy draft")
    assert intent.command == "create_strategy_draft"


def test_keyword_mark_task_done():
    intent = _classify_keyword("mark task done")
    assert intent.command == "mark_task_done"


def test_keyword_run_market_scan():
    intent = _classify_keyword("run market scan")
    assert intent.command == "run_market_scan"


def test_keyword_rerun_failed_task():
    intent = _classify_keyword("rerun failed task")
    assert intent.command == "rerun_failed_agent_task"


def test_keyword_symbol_observe_only():
    intent = _classify_keyword("set BTC/USDT observe only")
    assert intent.command == "set_symbol_observe_only"


def test_keyword_create_paper_experiment():
    intent = _classify_keyword("create paper experiment")
    assert intent.command == "create_paper_experiment"


def test_keyword_create_review_report():
    intent = _classify_keyword("create a risk review report")
    assert intent.command == "create_review_report"


def test_keyword_create_diagnostic_report():
    intent = _classify_keyword("create a system health diagnostic report")
    assert intent.command == "create_diagnostic_report"


def test_keyword_propose_strategy_promotion():
    intent = _classify_keyword("propose strategy promotion")
    assert intent.command == "propose_strategy_promotion"


def test_keyword_propose_prompt_change():
    intent = _classify_keyword("propose prompt change")
    assert intent.command == "propose_prompt_change"


def test_keyword_propose_schedule_change():
    intent = _classify_keyword("propose schedule change")
    assert intent.command == "propose_schedule_change"


def test_keyword_propose_live_limited_enablement():
    intent = _classify_keyword("propose live limited enablement")
    assert intent.command == "propose_live_limited_enablement"


def test_keyword_propose_risk_boundary_change():
    intent = _classify_keyword("propose risk boundary change")
    assert intent.command == "propose_risk_boundary_change"


def test_keyword_propose_command_center_behavior_change():
    intent = _classify_keyword("propose command center behavior change")
    assert intent.command == "propose_command_center_behavior_change"


def test_keyword_archive_low_value_notification():
    intent = _classify_keyword("archive this recommendation")
    assert intent.command == "archive_low_value_notification"


def test_keyword_mark_strategy_under_review():
    intent = _classify_keyword("mark strategy under review")
    assert intent.command == "mark_strategy_under_review"


def test_keyword_pause_paper_strategy():
    intent = _classify_keyword("pause paper strategy")
    assert intent.command == "pause_paper_strategy"


def test_keyword_resume():
    intent = _classify_keyword("resume agents please")
    assert intent.command == "resume_agents"


@pytest.mark.asyncio
async def test_resume_agents_blocks_when_llm_unavailable(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    import aletheia.config.loader as loader
    from aletheia.agent_runtime import resume_gate
    from aletheia.agent_shell.commands import resume_agents

    cfg = loader.load_config()
    cfg.runtime.paused = True
    loader.save_config(cfg)
    monkeypatch.setattr(
        resume_gate,
        "resume_if_llm_available",
        AsyncMock(
            return_value={
                "allowed": False,
                "reason": "llm_unavailable",
                "checked": 1,
                "results": [{"provider": "anthropic_compatible", "usable": False, "error": "429"}],
            }
        ),
    )

    result = await resume_agents({})

    assert result["error"].startswith("Resume blocked")
    assert result["resume_gate"]["reason"] == "llm_unavailable"
    assert loader.load_config().runtime.paused is True


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


@pytest.mark.asyncio
async def test_runtime_reads_operator_status_from_heartbeat(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    import aletheia.config.loader as loader
    from aletheia.agent_shell.operator_store import create_task, record_heartbeat

    cfg = loader.load_config()
    cfg.agent.permission_mode = "Observe"
    cfg.operator.permission_mode = "ReadOnly"
    cfg.runtime.paused = False
    loader.save_config(cfg)

    task_id = create_task(
        task_type="diagnose_system_health",
        status="running",
        priority="high",
        created_by="operator_loop",
        assigned_to="operator",
        input_data={"issue": "market_data_stale:15m"},
    )
    record_heartbeat(
        agent_id="operator",
        status="online",
        mode="Working",
        permission_mode="ReadOnly",
        is_busy=True,
        current_task_id=task_id,
        current_intent="investigating market_data_stale:15m",
        queue_size=2,
        last_error=None,
        model_provider="pi-shell",
        session_id="operator-main",
    )

    with patch(
        "aletheia.agent_runtime.health.compute_health",
        return_value={"healthy": True, "issues": []},
    ), patch(
        "aletheia.agents.helpers.llm_structured",
        new_callable=AsyncMock,
        return_value=(None, "unavailable"),
    ), patch(
        "aletheia.agents.helpers.llm_complete",
        new_callable=AsyncMock,
        return_value=(None, "unavailable"),
    ):
        from aletheia.agent_shell.runtime import AgentShellRuntime

        runtime = AgentShellRuntime()
        resp = await runtime.handle("show the operator heartbeat")

    assert resp.intent == "get_operator_status"
    assert "Working" in resp.response
    assert "queue" in resp.response.lower()


@pytest.mark.asyncio
async def test_runtime_handles_what_is_the_system_doing_with_operator_evidence(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.operator_store import create_task, record_heartbeat
    from aletheia.storage.models import AgentRun, Position
    from aletheia.storage.repositories import insert_agent_run, insert_position
    import aletheia.config.loader as loader

    cfg = loader.load_config()
    cfg.agent.permission_mode = "Observe"
    cfg.operator.permission_mode = "ReadOnly"
    cfg.live.state = "live_disabled"
    cfg.runtime.paused = False
    cfg.runtime.kill_switch_active = False
    cfg.runtime.scheduler_enabled = True
    cfg.exchange.mode = "demo"
    loader.save_config(cfg)

    insert_agent_run(AgentRun(agent_name="market_observer", status="completed"))
    insert_agent_run(AgentRun(agent_name="paper_trader", status="failed", errors='["network timeout"]'))
    insert_position(
        Position(
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            quantity=0.01,
            entry_price=65000,
            execution_mode="paper_internal",
        )
    )
    task_id = create_task(
        task_type="diagnose_system_health",
        status="running",
        priority="high",
        created_by="operator_loop",
        assigned_to="operator",
        input_data={"issue": "market_data_stale:15m"},
    )
    record_heartbeat(
        agent_id="operator",
        status="online",
        mode="Working",
        permission_mode="ReadOnly",
        is_busy=True,
        current_task_id=task_id,
        current_intent="investigating market_data_stale:15m",
        queue_size=1,
        last_error=None,
        model_provider="pi-shell",
        session_id="operator-main",
    )

    with patch(
        "aletheia.agent_runtime.health.compute_health",
        return_value={"healthy": False, "issues": ["market_data_stale:15m"]},
    ), patch(
        "aletheia.agents.helpers.llm_structured",
        new_callable=AsyncMock,
        return_value=(None, "unavailable"),
    ), patch(
        "aletheia.agents.helpers.llm_complete",
        new_callable=AsyncMock,
        return_value=(None, "unavailable"),
    ):
        from aletheia.agent_shell.runtime import AgentShellRuntime

        runtime = AgentShellRuntime()
        resp = await runtime.handle("what is the system doing?")

    assert resp.intent == "get_system_status"
    assert "market_data_stale:15m" in resp.response
    assert "Working" in resp.response
    assert "1 failed recent agent run" in resp.response
    assert "1 open position" in resp.response


@pytest.mark.asyncio
async def test_runtime_explains_why_proposal_failed_from_risk_decision(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    import aletheia.config.loader as loader
    from aletheia.storage.models import RiskDecision, TradeProposal
    from aletheia.storage.repositories import insert_risk_decision, insert_trade_proposal

    cfg = loader.load_config()
    cfg.agent.permission_mode = "Observe"
    cfg.operator.permission_mode = "ReadOnly"
    loader.save_config(cfg)

    proposal = TradeProposal(
        symbol="ETH/USDT",
        market_type="spot",
        direction="short",
        status="rejected",
        agent_rationale="Funding reversal thesis.",
    )
    insert_trade_proposal(proposal)
    insert_risk_decision(
        RiskDecision(
            proposal_id=proposal.id,
            approved=False,
            risk_score=0.91,
            violations='["max_daily_loss", "permission_mode_insufficient:Observe"]',
            message="Daily loss limit breached.",
        )
    )

    with patch(
        "aletheia.agent_runtime.health.compute_health",
        return_value={"healthy": True, "issues": []},
    ), patch(
        "aletheia.agents.helpers.llm_structured",
        new_callable=AsyncMock,
        return_value=(None, "unavailable"),
    ), patch(
        "aletheia.agents.helpers.llm_complete",
        new_callable=AsyncMock,
        return_value=(None, "unavailable"),
    ):
        from aletheia.agent_shell.runtime import AgentShellRuntime

        runtime = AgentShellRuntime()
        resp = await runtime.handle("why did this proposal fail?")

    assert resp.intent == "explain_proposal"
    assert "ETH/USDT" in resp.response
    assert "Daily loss limit breached." in resp.response
    assert "max_daily_loss" in resp.response
    assert "risk score 0.91" in resp.response


@pytest.mark.asyncio
async def test_runtime_reads_backend_recommendations(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    import aletheia.config.loader as loader
    from aletheia.agent_shell.operator_store import create_task

    cfg = loader.load_config()
    cfg.runtime.paused = True
    loader.save_config(cfg)

    create_task(
        task_type="diagnose_system_health",
        status="failed",
        priority="high",
        created_by="operator_loop",
        assigned_to="operator",
        input_data={"issue": "market_data_stale:45m"},
        error="market_data_stale:45m",
    )

    with patch(
        "aletheia.agent_runtime.health.compute_health",
        return_value={"healthy": True, "issues": []},
    ), patch(
        "aletheia.agents.helpers.llm_structured",
        new_callable=AsyncMock,
        return_value=(None, "unavailable"),
    ), patch(
        "aletheia.agents.helpers.llm_complete",
        new_callable=AsyncMock,
        return_value=(None, "unavailable"),
    ):
        from aletheia.agent_shell.runtime import AgentShellRuntime

        runtime = AgentShellRuntime()
        resp = await runtime.handle("what recommendations do you have?")

    assert resp.intent == "get_recommendations"
    assert "recommendation" in resp.response.lower()


@pytest.mark.asyncio
async def test_runtime_uses_session_kind_context_profile_for_selected_branch(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.intent import CommandIntent
    from aletheia.agent_shell.operator_store import create_session_record
    from aletheia.agent_shell.runtime import AgentShellRuntime

    create_session_record(
        session_id="strategy-session-context",
        user_input="Investigate strategy drift.",
        kind="strategy_session",
        parent_session_id="operator-main",
        title="Strategy Drift Review",
    )

    captured_sections: list[list[str]] = []

    def fake_build_app_context(sections=None):
        captured_sections.append(list(sections or []))
        return {"system": {"healthy": True}}

    with patch(
        "aletheia.agent_shell.context.build_app_context",
        side_effect=fake_build_app_context,
    ), patch(
        "aletheia.agent_shell.context.context_summary_text",
        return_value="mock strategy summary",
    ), patch(
        "aletheia.agent_shell.intent.classify_intent",
        new_callable=AsyncMock,
        return_value=CommandIntent(command="forbidden", parameters={}, confidence=1.0),
    ):
        runtime = AgentShellRuntime()
        resp = await runtime.handle(
            "continue the current strategy branch",
            session_id="strategy-session-context",
        )

    assert resp.intent == "forbidden"
    assert captured_sections == [sections_for_profile("trading_context")]


@pytest.mark.asyncio
async def test_runtime_includes_recent_session_context_and_trace_in_branch_summary(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.intent import CommandIntent
    from aletheia.agent_shell.operator_store import append_message, create_session_record
    from aletheia.agent_shell.runtime import AgentShellRuntime

    create_session_record(
        session_id="diagnostic-session-history",
        user_input="Investigate stale market data.",
        kind="diagnostic_session",
        parent_session_id="operator-main",
        title="Stale Market Diagnostic",
    )
    append_message(
        session_id="diagnostic-session-history",
        role="assistant",
        message_type="context",
        content={"text": "Diagnostic branch context: market_data_stale:45m affects promotion safety."},
    )
    append_message(
        session_id="diagnostic-session-history",
        role="assistant",
        message_type="response",
        content={"text": "Previous diagnostic result: keep the gateway restart todo visible."},
    )

    captured_summary: dict[str, str] = {}

    async def fake_classify_intent(text, summary):
        captured_summary["value"] = summary
        return CommandIntent(command="query", parameters={}, confidence=0.9)

    with patch(
        "aletheia.agent_runtime.health.compute_health",
        return_value={"healthy": True, "issues": []},
    ), patch(
        "aletheia.agents.helpers.llm_complete",
        new_callable=AsyncMock,
        return_value=(None, "unavailable"),
    ), patch(
        "aletheia.agent_shell.intent.classify_intent",
        side_effect=fake_classify_intent,
    ):
        runtime = AgentShellRuntime()
        resp = await runtime.handle(
            "what should this branch do next?",
            permission_mode="ReadOnly",
            permission_scope="operator",
            session_id="diagnostic-session-history",
        )

    assert resp.intent == "query"
    assert "Diagnostic branch context: market_data_stale:45m affects promotion safety." in captured_summary["value"]
    assert "Previous diagnostic result: keep the gateway restart todo visible." in captured_summary["value"]


@pytest.mark.asyncio
async def test_runtime_includes_parent_branch_trace_for_nested_session_summary(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.intent import CommandIntent
    from aletheia.agent_shell.operator_store import append_message, create_session_record
    from aletheia.agent_shell.runtime import AgentShellRuntime

    create_session_record(
        session_id="strategy-parent-session",
        user_input="Investigate Momentum root.",
        kind="strategy_session",
        parent_session_id="operator-main",
        title="Momentum Root",
    )
    create_session_record(
        session_id="review-child-session",
        user_input="Review nested drawdown branch.",
        kind="review_session",
        parent_session_id="strategy-parent-session",
        title="Nested Drawdown Review",
    )
    append_message(
        session_id="strategy-parent-session",
        role="assistant",
        message_type="context",
        content={"text": "Parent branch context: Momentum root still owns the promotion backlog."},
    )
    append_message(
        session_id="strategy-parent-session",
        role="assistant",
        message_type="response",
        content={"text": "Parent branch result: resolve promotion blockers before widening experiments."},
    )

    captured_summary: dict[str, str] = {}

    async def fake_classify_intent(text, summary):
        captured_summary["value"] = summary
        return CommandIntent(command="query", parameters={}, confidence=0.9)

    with patch(
        "aletheia.agent_runtime.health.compute_health",
        return_value={"healthy": True, "issues": []},
    ), patch(
        "aletheia.agents.helpers.llm_complete",
        new_callable=AsyncMock,
        return_value=(None, "unavailable"),
    ), patch(
        "aletheia.agent_shell.intent.classify_intent",
        side_effect=fake_classify_intent,
    ):
        runtime = AgentShellRuntime()
        resp = await runtime.handle(
            "what does this nested branch depend on?",
            permission_mode="ReadOnly",
            permission_scope="operator",
            session_id="review-child-session",
        )

    assert resp.intent == "query"
    assert "Parent branch context: Momentum root still owns the promotion backlog." in captured_summary["value"]
    assert "Parent branch result: resolve promotion blockers before widening experiments." in captured_summary["value"]


@pytest.mark.asyncio
async def test_runtime_dispatches_enabled_extension_to_target_command(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    import aletheia.config.loader as loader
    from aletheia.agent_shell.operator_store import create_extension_record
    from aletheia.storage.models import MarketSnapshot
    from aletheia.storage.repositories import insert_market_snapshot

    cfg = loader.load_config()
    cfg.agent.permission_mode = "Observe"
    cfg.operator.permission_mode = "ReadOnly"
    loader.save_config(cfg)

    insert_market_snapshot(MarketSnapshot(symbol="BTC/USDT", market_type="spot", price=65000))
    create_extension_record(
        session_id="operator-main",
        extension_name="Summarize Market",
        slash_command="/summarize-market",
        description="Summarize the latest market snapshot via slash command.",
        target_command="get_market_snapshot",
        manifest={
            "slash_command": "/summarize-market",
            "target_command": "get_market_snapshot",
            "description": "Summarize the latest market snapshot via slash command.",
            "default_parameters": {"symbol": "BTC/USDT"},
            "capabilities": ["operator_command_proxy"],
            "sandbox_only": True,
            "requires_human_approval": True,
        },
        status="enabled",
        created_by="operator",
        enabled_by="human-review",
    )

    with patch(
        "aletheia.agent_runtime.health.compute_health",
        return_value={"healthy": True, "issues": []},
    ), patch(
        "aletheia.agents.helpers.llm_structured",
        new_callable=AsyncMock,
        return_value=(None, "unavailable"),
    ), patch(
        "aletheia.agents.helpers.llm_complete",
        new_callable=AsyncMock,
        return_value=(None, "unavailable"),
    ):
        from aletheia.agent_shell.runtime import AgentShellRuntime

        runtime = AgentShellRuntime()
        resp = await runtime.handle("/summarize-market")

    assert resp.intent == "get_market_snapshot"
    assert "BTC/USDT" in resp.response


@pytest.mark.asyncio
async def test_runtime_reuses_explicit_session_id_for_operator_turn(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    import aletheia.config.loader as loader
    from aletheia.agent_shell.operator_store import create_session_record, list_commands, list_sessions

    cfg = loader.load_config()
    cfg.agent.permission_mode = "Observe"
    cfg.operator.permission_mode = "ReadOnly"
    loader.save_config(cfg)

    create_session_record(
        session_id="strategy-session-1",
        user_input="review strategy",
        kind="strategy_session",
        parent_session_id="operator-main",
        title="Strategy Review",
    )

    with patch(
        "aletheia.agent_runtime.health.compute_health",
        return_value={"healthy": True, "issues": []},
    ), patch(
        "aletheia.agents.helpers.llm_structured",
        new_callable=AsyncMock,
        return_value=(None, "unavailable"),
    ), patch(
        "aletheia.agents.helpers.llm_complete",
        new_callable=AsyncMock,
        return_value=(None, "unavailable"),
    ):
        from aletheia.agent_shell.runtime import AgentShellRuntime

        runtime = AgentShellRuntime()
        resp = await runtime.handle(
            "show the strategy lifecycle",
            permission_mode="ReadOnly",
            permission_scope="operator",
            session_id="strategy-session-1",
        )

    assert resp.intent == "get_strategy_lifecycle"
    commands = list_commands("strategy-session-1")
    assert len(commands) == 1
    sessions = list_sessions(limit=10)
    assert [session["id"] for session in sessions].count("strategy-session-1") == 1
    assert len(sessions) == 2  # operator-main placeholder + the explicit child session


@pytest.mark.asyncio
async def test_runtime_maps_extension_text_tail_into_target_parameters(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    import aletheia.config.loader as loader
    from aletheia.agent_shell.operator_store import create_extension_record
    from aletheia.storage.repositories import list_knowledge_items

    cfg = loader.load_config()
    cfg.agent.permission_mode = "Paper"
    cfg.operator.permission_mode = "Research"
    loader.save_config(cfg)

    create_extension_record(
        session_id="operator-main",
        extension_name="Quick Research Note",
        slash_command="/create-research-note",
        description="Create a research note from a slash command tail.",
        target_command="create_research_note",
        manifest={
            "slash_command": "/create-research-note",
            "target_command": "create_research_note",
            "description": "Create a research note from a slash command tail.",
            "default_parameters": {"title": "Quick Note"},
            "text_parameter": "content",
            "capabilities": ["operator_command_proxy"],
            "sandbox_only": True,
            "requires_human_approval": True,
        },
        status="enabled",
        created_by="operator",
        enabled_by="human-review",
    )

    with patch(
        "aletheia.agent_runtime.health.compute_health",
        return_value={"healthy": True, "issues": []},
    ), patch(
        "aletheia.agents.helpers.llm_structured",
        new_callable=AsyncMock,
        return_value=(None, "unavailable"),
    ), patch(
        "aletheia.agents.helpers.llm_complete",
        new_callable=AsyncMock,
        return_value=(None, "unavailable"),
    ):
        from aletheia.agent_shell.runtime import AgentShellRuntime

        runtime = AgentShellRuntime()
        resp = await runtime.handle("/create-research-note Review BTC basis dislocation before Asia open.")

    assert resp.intent == "create_research_note"
    notes = list_knowledge_items(item_type="research_note", limit=10)
    assert len(notes) == 1
    assert notes[0]["title"] == "Quick Note"


@pytest.mark.asyncio
async def test_runtime_rejects_extension_that_is_not_enabled(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    import aletheia.config.loader as loader
    from aletheia.agent_shell.operator_store import create_extension_record

    cfg = loader.load_config()
    cfg.agent.permission_mode = "Observe"
    cfg.operator.permission_mode = "ReadOnly"
    loader.save_config(cfg)

    create_extension_record(
        session_id="operator-main",
        extension_name="Explain Proposal",
        slash_command="/explain-proposal",
        description="Explain the latest proposal via slash command.",
        target_command="explain_proposal",
        manifest={
            "slash_command": "/explain-proposal",
            "target_command": "explain_proposal",
            "description": "Explain the latest proposal via slash command.",
            "capabilities": ["operator_command_proxy"],
            "sandbox_only": True,
            "requires_human_approval": True,
        },
        status="proposed",
        created_by="operator",
    )

    with patch(
        "aletheia.agent_runtime.health.compute_health",
        return_value={"healthy": True, "issues": []},
    ), patch(
        "aletheia.agents.helpers.llm_structured",
        new_callable=AsyncMock,
        return_value=(None, "unavailable"),
    ), patch(
        "aletheia.agents.helpers.llm_complete",
        new_callable=AsyncMock,
        return_value=(None, "unavailable"),
    ):
        from aletheia.agent_shell.runtime import AgentShellRuntime

        runtime = AgentShellRuntime()
        resp = await runtime.handle("/explain-proposal")

    assert resp.intent == "extension_not_enabled"
    assert "not enabled" in resp.response.lower()


@pytest.mark.asyncio
async def test_runtime_explains_what_failed_with_specific_failures(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    import aletheia.config.loader as loader
    from aletheia.agent_shell.operator_store import create_task
    from aletheia.storage.models import AgentRun
    from aletheia.storage.repositories import insert_agent_run

    cfg = loader.load_config()
    cfg.agent.permission_mode = "Observe"
    cfg.operator.permission_mode = "ReadOnly"
    loader.save_config(cfg)

    create_task(
        task_type="diagnose_system_health",
        status="failed",
        priority="high",
        created_by="operator_loop",
        assigned_to="operator",
        input_data={"issue": "market_data_stale:45m"},
        error="market_data_stale:45m",
    )
    insert_agent_run(
        AgentRun(
            agent_name="paper_trader",
            status="failed",
            errors='["exchange timeout"]',
        )
    )

    with patch(
        "aletheia.agent_runtime.health.compute_health",
        return_value={"healthy": True, "issues": []},
    ), patch(
        "aletheia.agents.helpers.llm_structured",
        new_callable=AsyncMock,
        return_value=(None, "unavailable"),
    ), patch(
        "aletheia.agents.helpers.llm_complete",
        new_callable=AsyncMock,
        return_value=(None, "unavailable"),
    ):
        from aletheia.agent_shell.runtime import AgentShellRuntime

        runtime = AgentShellRuntime()
        resp = await runtime.handle("what failed?")

    assert resp.intent == "get_failed_tasks"
    assert "diagnose_system_health" in resp.response
    assert "paper_trader" in resp.response


@pytest.mark.asyncio
async def test_create_daily_review_handler_preserves_item_type(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.commands import create_daily_review
    from aletheia.storage.repositories import list_knowledge_items

    result = await create_daily_review({"title": "Daily Review", "content": "Today was stable."})

    assert result["created"] is True
    items = list_knowledge_items(item_type="daily_review")
    assert len(items) == 1
    assert items[0]["item_type"] == "daily_review"
    assert items[0]["title"] == "Daily Review"


@pytest.mark.asyncio
async def test_create_dreaming_log_handler_preserves_item_type(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.commands import create_dreaming_log
    from aletheia.storage.repositories import list_knowledge_items

    result = await create_dreaming_log({"title": "Dreaming Log", "content": "Pattern synthesis."})

    assert result["created"] is True
    items = list_knowledge_items(item_type="dreaming_log")
    assert len(items) == 1
    assert items[0]["item_type"] == "dreaming_log"
    assert items[0]["title"] == "Dreaming Log"


@pytest.mark.asyncio
async def test_create_strategy_draft_creates_draft_and_strategy_memory(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.commands import create_strategy_draft
    from aletheia.storage.repositories import get_strategy, list_knowledge_items

    result = await create_strategy_draft(
        {
            "title": "Mean Reversion Draft",
            "content": "Fade intraday overextension with tight invalidation.",
            "parameters": {"symbol": "BTCUSDT", "timeframe": "15m"},
        }
    )

    assert result["created"] is True
    strategy = get_strategy(result["id"])
    assert strategy is not None
    assert strategy["state"] == "draft"
    memories = list_knowledge_items(item_type="strategy_memory")
    assert len(memories) == 1
    assert memories[0]["title"] == "Mean Reversion Draft"


@pytest.mark.asyncio
async def test_mark_task_done_completes_operator_task(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.commands import mark_task_done
    from aletheia.agent_shell.operator_store import create_task, get_task

    task_id = create_task(
        task_type="diagnose_system_health",
        status="running",
        priority="high",
        created_by="operator_loop",
        assigned_to="operator",
        input_data={"issue": "market_data_stale:15m"},
    )

    result = await mark_task_done({"task_id": task_id, "summary": "issue reviewed"})

    assert result["completed"] is True
    task = get_task(task_id)
    assert task is not None
    assert task["status"] == "completed"
    assert task["output_data"]["summary"] == "issue reviewed"


@pytest.mark.asyncio
async def test_run_market_scan_triggers_market_observer(monkeypatch):
    from aletheia.agent_shell.commands import run_market_scan

    async def fake_run_agent(agent_name: str, trigger_type: str = "scheduled"):
        assert agent_name == "market_observer"
        assert trigger_type == "manual"
        return {"run_id": "run-scan-1", "status": "completed", "outputs": ["scan complete"], "errors": []}

    monkeypatch.setattr("aletheia.agent_shell.commands.run_agent", fake_run_agent, raising=False)

    result = await run_market_scan({})

    assert result["run"]["agent_name"] == "market_observer"
    assert result["run"]["run_id"] == "run-scan-1"


@pytest.mark.asyncio
async def test_rerun_failed_agent_task_uses_latest_failed_run(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.commands import rerun_failed_agent_task
    from aletheia.storage.models import AgentRun
    from aletheia.storage.repositories import insert_agent_run

    insert_agent_run(AgentRun(agent_name="paper_trader", status="failed", errors='["timeout"]'))
    insert_agent_run(AgentRun(agent_name="market_observer", status="completed"))

    async def fake_run_agent(agent_name: str, trigger_type: str = "scheduled"):
        assert agent_name == "paper_trader"
        assert trigger_type == "manual"
        return {"run_id": "run-rerun-1", "status": "completed_with_errors", "outputs": [], "errors": ["still flaky"]}

    monkeypatch.setattr("aletheia.agent_shell.commands.run_agent", fake_run_agent, raising=False)

    result = await rerun_failed_agent_task({})

    assert result["rerun"]["agent_name"] == "paper_trader"
    assert result["rerun"]["new_run_id"] == "run-rerun-1"


@pytest.mark.asyncio
async def test_set_symbol_observe_only_removes_trading_but_keeps_observation(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    import aletheia.config.loader as loader
    from aletheia.agent_shell.commands import set_symbol_observe_only

    cfg = loader.load_config()
    cfg.risk.symbol_filter_mode = "whitelist"
    cfg.risk.symbol_list = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    cfg.exchange.symbol_list = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    cfg.market_data.observed_symbols = ["BTC/USDT", "ETH/USDT"]
    cfg.market_data.observed_instruments = []
    loader.save_config(cfg)

    result = await set_symbol_observe_only({"symbol": "BTC/USDT"})

    assert result["updated"] is True
    updated = loader.load_config()
    assert "BTCUSDT" not in updated.exchange.symbol_list
    assert "BTCUSDT" not in updated.risk.symbol_list
    assert "BTC/USDT" in updated.market_data.observed_symbols


@pytest.mark.asyncio
async def test_create_paper_experiment_advances_to_paper_candidate(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.commands import create_paper_experiment
    from aletheia.storage.repositories import get_strategy

    result = await create_paper_experiment(
        {
            "title": "Momentum Experiment",
            "content": "Probe breakout continuation with paper-only sizing.",
            "parameters": {"symbol": "ETHUSDT", "timeframe": "1h"},
        }
    )

    assert result["created"] is True
    strategy = get_strategy(result["id"])
    assert strategy is not None
    assert strategy["state"] == "paper_candidate"


@pytest.mark.asyncio
async def test_archive_low_value_notification_creates_artifact_and_filters_recommendations(
    tmp_path, monkeypatch
):
    _init_agent_shell_db(tmp_path, monkeypatch)

    import aletheia.config.loader as loader
    from aletheia.agent_shell.commands import archive_low_value_notification
    from aletheia.agent_shell.operator_recommendations import build_recommendations
    from aletheia.agent_shell.operator_store import list_artifacts

    cfg = loader.load_config()
    cfg.runtime.paused = True
    loader.save_config(cfg)

    before = build_recommendations(limit=10)
    assert any(item["id"] == "gateway-paused" for item in before)

    result = await archive_low_value_notification({"recommendation_id": "gateway-paused"})

    assert result["archived"] is True
    artifacts = list_artifacts(session_id="operator-main")
    assert artifacts[0]["artifact_type"] == "notification_archive"
    assert artifacts[0]["content"]["recommendation_id"] == "gateway-paused"

    after = build_recommendations(limit=10)
    assert all(item["id"] != "gateway-paused" for item in after)


@pytest.mark.asyncio
async def test_mark_strategy_under_review_updates_strategy_record(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.commands import mark_strategy_under_review
    from aletheia.storage.repositories import get_strategy
    from aletheia.trading.strategy_lifecycle import create_strategy

    strategy = create_strategy("Review Candidate", "needs operator review")

    result = await mark_strategy_under_review(
        {
            "strategy_id": strategy.id,
            "review_notes": "paper drawdown looks unstable",
        }
    )

    assert result["updated"] is True
    record = get_strategy(strategy.id)
    assert record is not None
    assert record["under_review"] == 1
    assert record["review_notes"] == "paper drawdown looks unstable"


@pytest.mark.asyncio
async def test_create_review_report_writes_markdown_and_artifact(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.commands import create_review_report
    from aletheia.agent_shell.operator_store import list_artifacts

    result = await create_review_report(
        {
            "report_type": "risk_review",
            "title": "Risk Review Report",
            "content": "# Risk Review\n\n- rejected decisions: 2\n- proposal candidates: 1\n",
            "summary": "Summarized recent risk rejections for operator follow-up.",
        }
    )

    assert result["created"] is True
    assert result["report_type"] == "risk_review"
    report_path = Path(result["path"])
    assert report_path.exists()
    assert report_path.read_text(encoding="utf-8").startswith("# Risk Review Report")

    artifacts = list_artifacts(session_id="operator-main")
    assert artifacts[0]["artifact_type"] == "review_report"
    assert artifacts[0]["content"]["report_type"] == "risk_review"
    assert artifacts[0]["content"]["file_path"] == str(report_path)
    assert "rejected decisions" in artifacts[0]["content"]["body"]


@pytest.mark.asyncio
async def test_create_diagnostic_report_writes_markdown_and_artifact(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.commands import create_diagnostic_report
    from aletheia.agent_shell.operator_store import list_artifacts

    result = await create_diagnostic_report(
        {
            "report_type": "system_health",
            "title": "System Health Diagnostic Report",
            "content": "# System Health\n\n- status: degraded\n- issue: market_data_stale:45m\n",
            "summary": "Captured degraded gateway health for operator diagnosis.",
        }
    )

    assert result["created"] is True
    assert result["report_type"] == "system_health"
    report_path = Path(result["path"])
    assert report_path.exists()
    assert "market_data_stale:45m" in report_path.read_text(encoding="utf-8")

    artifacts = list_artifacts(session_id="operator-main")
    assert artifacts[0]["artifact_type"] == "diagnostic_report"
    assert artifacts[0]["content"]["report_type"] == "system_health"
    assert artifacts[0]["content"]["file_path"] == str(report_path)
    assert "degraded gateway health" in artifacts[0]["content"]["summary"].lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("report_type", "title", "summary"),
    [
        ("worker_health", "Worker Health Diagnostic Report", "Captured degraded worker health for operator diagnosis."),
        ("model_runtime_health", "Model Runtime Health Diagnostic Report", "Captured degraded model/runtime health for operator diagnosis."),
        ("data_source_health", "Data Source Health Diagnostic Report", "Captured degraded data-source health for operator diagnosis."),
    ],
)
async def test_create_diagnostic_report_supports_specialized_health_report_types(
    tmp_path,
    monkeypatch,
    report_type,
    title,
    summary,
):
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.commands import create_diagnostic_report
    from aletheia.agent_shell.operator_store import list_artifacts

    result = await create_diagnostic_report(
        {
            "report_type": report_type,
            "title": title,
            "content": f"# {title}\n\n- issue: test\n",
            "summary": summary,
        }
    )

    assert result["created"] is True
    assert result["report_type"] == report_type
    report_path = Path(result["path"])
    assert report_path.exists()
    assert title in report_path.read_text(encoding="utf-8")

    artifacts = list_artifacts(session_id="operator-main")
    assert artifacts[0]["artifact_type"] == "diagnostic_report"
    assert artifacts[0]["content"]["report_type"] == report_type
    assert artifacts[0]["content"]["file_path"] == str(report_path)
    assert summary == artifacts[0]["content"]["summary"]


@pytest.mark.asyncio
async def test_pause_paper_strategy_marks_strategy_paused_and_reviews_open_opportunities(
    tmp_path, monkeypatch
):
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.commands import pause_paper_strategy
    from aletheia.storage.models import Opportunity
    from aletheia.storage.repositories import get_opportunity, get_strategy, insert_opportunity
    from aletheia.trading.strategy_lifecycle import create_strategy, promote_strategy

    strategy = create_strategy("Paused Strategy", "pause this strategy")
    for target in ("research_note", "strategy_hypothesis", "paper_candidate", "paper_running"):
        promote_strategy(strategy.id, target)

    opp = Opportunity(
        symbol="BTC/USDT",
        market="spot",
        direction_bias="long",
        thesis="linked to active strategy",
        confidence="medium",
        source_agent="market_observer",
        linked_strategy_id=strategy.id,
    )
    insert_opportunity(opp)

    result = await pause_paper_strategy(
        {
            "strategy_id": strategy.id,
            "reason": "risk review pending",
        }
    )

    assert result["paused"] is True
    record = get_strategy(strategy.id)
    assert record is not None
    assert record["paper_trading_paused"] == 1
    assert record["paper_pause_reason"] == "risk review pending"
    updated_opp = get_opportunity(opp.id)
    assert updated_opp is not None
    assert updated_opp["status"] == "reviewed"


@pytest.mark.asyncio
async def test_propose_strategy_promotion_creates_artifact_with_gate_preview(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.commands import propose_strategy_promotion
    from aletheia.agent_shell.operator_store import list_artifacts
    from aletheia.trading.strategy_lifecycle import create_strategy, promote_strategy

    strategy = create_strategy("Promotion Candidate", "paper strategy")
    for target in ("research_note", "strategy_hypothesis", "paper_candidate", "paper_running"):
        promote_strategy(strategy.id, target)

    result = await propose_strategy_promotion(
        {
            "strategy_id": strategy.id,
            "target_state": "live_candidate",
            "rationale": "paper evidence looks promising",
        }
    )

    assert result["proposed"] is True
    artifacts = list_artifacts(session_id="operator-main")
    assert artifacts[0]["artifact_type"] == "strategy_promotion_proposal"
    assert artifacts[0]["content"]["target_state"] == "live_candidate"
    assert "gate_preview" in artifacts[0]["content"]


@pytest.mark.asyncio
async def test_propose_prompt_change_creates_artifact(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.commands import propose_prompt_change
    from aletheia.agent_shell.operator_store import list_artifacts

    result = await propose_prompt_change(
        {
            "target": "paper_trader.system_prompt",
            "content": "Tighten rejection reasoning.",
            "rationale": "Reduce vague trade explanations.",
        }
    )

    assert result["proposed"] is True
    artifacts = list_artifacts(session_id="operator-main")
    assert artifacts[0]["artifact_type"] == "prompt_change_proposal"
    assert artifacts[0]["content"]["target"] == "paper_trader.system_prompt"


@pytest.mark.asyncio
async def test_propose_schedule_change_creates_artifact(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.commands import propose_schedule_change
    from aletheia.agent_shell.operator_store import list_artifacts

    result = await propose_schedule_change(
        {
            "agent_name": "market_observer",
            "requested_schedule": "every 10 minutes",
            "rationale": "faster scan cadence during active sessions",
        }
    )

    assert result["proposed"] is True
    artifacts = list_artifacts(session_id="operator-main")
    assert artifacts[0]["artifact_type"] == "schedule_change_proposal"
    assert artifacts[0]["content"]["agent_name"] == "market_observer"


@pytest.mark.asyncio
async def test_propose_live_limited_enablement_creates_artifact(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.commands import propose_live_limited_enablement
    from aletheia.agent_shell.operator_store import list_artifacts
    from aletheia.trading.strategy_lifecycle import create_strategy

    strategy = create_strategy("Live Limited Candidate", "candidate")

    result = await propose_live_limited_enablement(
        {
            "strategy_id": strategy.id,
            "rationale": "Prepare explicit human review for live-limited admission.",
        }
    )

    assert result["proposed"] is True
    artifacts = list_artifacts(session_id="operator-main")
    assert artifacts[0]["artifact_type"] == "live_limited_enablement_proposal"
    assert artifacts[0]["content"]["strategy_id"] == strategy.id


@pytest.mark.asyncio
async def test_propose_risk_boundary_change_creates_artifact(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.commands import propose_risk_boundary_change
    from aletheia.agent_shell.operator_store import list_artifacts

    result = await propose_risk_boundary_change(
        {
            "changes": {"max_demo_position_notional_usdt": 300.0},
            "rationale": "Paper sizing is too restrictive for current symbol set.",
        }
    )

    assert result["proposed"] is True
    artifacts = list_artifacts(session_id="operator-main")
    assert artifacts[0]["artifact_type"] == "risk_boundary_change_proposal"
    assert artifacts[0]["content"]["changes"]["max_demo_position_notional_usdt"] == 300.0


@pytest.mark.asyncio
async def test_propose_command_center_behavior_change_creates_artifact(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.commands import propose_command_center_behavior_change
    from aletheia.agent_shell.operator_store import list_artifacts

    result = await propose_command_center_behavior_change(
        {
            "target_command": "pause_agents",
            "requested_behavior": "Show an inline permission gate before retrying pause_agents from a denied mode.",
            "rationale": "Users keep retrying the same denied control without a better affordance.",
            "evidence": {
                "rejection_count": 2,
                "sample_inputs": ["pause everything", "pause agents now"],
            },
        }
    )

    assert result["proposed"] is True
    artifacts = list_artifacts(session_id="operator-main")
    assert artifacts[0]["artifact_type"] == "command_center_behavior_proposal"
    assert artifacts[0]["content"]["target_command"] == "pause_agents"


@pytest.mark.asyncio
async def test_draft_extension_manifest_persists_registry_and_manifest_file(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.commands import draft_extension_manifest
    from aletheia.agent_shell.operator_store import list_extensions

    result = await draft_extension_manifest(
        {
            "extension_name": "Summarize Market",
            "slash_command": "/summarize-market",
            "description": "Summarize the latest market snapshot via a slash command.",
            "target_command": "get_market_snapshot",
            "default_parameters": {"symbol": "BTC/USDT"},
            "capabilities": ["operator_command_proxy"],
        }
    )

    assert result["created"] is True
    extensions = list_extensions()
    assert len(extensions) == 1
    assert extensions[0]["status"] == "draft"
    assert extensions[0]["slash_command"] == "/summarize-market"

    manifest_files = list((tmp_path / "agent_shell" / "extensions").glob("*.json"))
    assert len(manifest_files) == 1
    manifest = json.loads(manifest_files[0].read_text(encoding="utf-8"))
    assert manifest["slash_command"] == "/summarize-market"
    assert manifest["target_command"] == "get_market_snapshot"


@pytest.mark.asyncio
async def test_test_extension_sandbox_records_failed_run_and_scratch_report(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.commands import draft_extension_manifest, test_extension_sandbox
    from aletheia.agent_shell.operator_store import get_extension, list_extension_runs

    draft = await draft_extension_manifest(
        {
            "extension_name": "Leaky Extension",
            "slash_command": "/leaky-extension",
            "description": "An unsafe extension that incorrectly requests credential access.",
            "target_command": "get_market_snapshot",
            "capabilities": ["credentials_access"],
        }
    )

    result = await test_extension_sandbox({"extension_id": draft["extension_id"]})

    assert result["passed"] is False
    assert result["status"] == "failed"
    assert "credentials access" in result["summary"].lower()
    scratch_path = tmp_path / "agent_shell" / "scratch"
    scratch_files = list(scratch_path.glob("*.md"))
    assert len(scratch_files) == 1
    scratch_body = scratch_files[0].read_text(encoding="utf-8").lower()
    assert "sandbox test" in scratch_body
    assert "credentials access" in scratch_body

    extension = get_extension(draft["extension_id"])
    assert extension is not None
    assert extension["status"] == "sandbox_failed"
    runs = list_extension_runs(extension_id=draft["extension_id"])
    assert len(runs) == 1
    assert runs[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_propose_extension_enablement_requires_passed_sandbox_and_creates_artifact(tmp_path, monkeypatch):
    _init_agent_shell_db(tmp_path, monkeypatch)

    from aletheia.agent_shell.commands import (
        draft_extension_manifest,
        propose_extension_enablement,
        test_extension_sandbox,
    )
    from aletheia.agent_shell.operator_store import get_extension, list_artifacts

    draft = await draft_extension_manifest(
        {
            "extension_name": "Explain Proposal",
            "slash_command": "/explain-proposal",
            "description": "Open a structured explanation flow for the latest proposal.",
            "target_command": "explain_proposal",
            "capabilities": ["operator_command_proxy"],
        }
    )

    sandbox = await test_extension_sandbox({"extension_id": draft["extension_id"]})
    assert sandbox["passed"] is True

    result = await propose_extension_enablement(
        {
            "extension_id": draft["extension_id"],
            "rationale": "This shortcut is stable enough for explicit human review.",
        }
    )

    assert result["proposed"] is True
    artifacts = list_artifacts(session_id="operator-main")
    assert artifacts[0]["artifact_type"] == "extension_enablement_proposal"
    assert artifacts[0]["content"]["extension_id"] == draft["extension_id"]
    assert artifacts[0]["content"]["latest_sandbox_status"] == "passed"

    extension = get_extension(draft["extension_id"])
    assert extension is not None
    assert extension["status"] == "proposed"
