from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aletheia.agent_shell.intent import CommandIntent
from aletheia.agent_shell.permissions import allowed_commands, check_permission
from aletheia.agent_shell.registry import COMMANDS


def test_registry_commands_have_operator_metadata():
    for cmd in COMMANDS.values():
        assert hasattr(cmd, "risk_class")
        assert hasattr(cmd, "worker_permission_required")
        assert hasattr(cmd, "operator_permission_required")
        assert hasattr(cmd, "requires_confirmation")
        assert hasattr(cmd, "requires_proposal")
        assert hasattr(cmd, "audit_event_type")
        assert hasattr(cmd, "backend_handler")


def test_worker_permissions_preserve_existing_modes():
    allowed, reason = check_permission("pause_agents", "Observe")
    assert allowed is False
    assert "Paper" in reason

    allowed, reason = check_permission("create_improvement_proposal", "Paper")
    assert allowed is True
    assert reason is None


def test_operator_permissions_use_canonical_ladder():
    allowed, reason = check_permission(
        "create_research_note",
        "ReadOnly",
        permission_scope="operator",
    )
    assert allowed is False
    assert "Research" in reason

    allowed, reason = check_permission(
        "create_research_note",
        "Research",
        permission_scope="operator",
    )
    assert allowed is True
    assert reason is None

    allowed, reason = check_permission(
        "pause_agents",
        "Research",
        permission_scope="operator",
    )
    assert allowed is False
    assert "PaperAuto" in reason

    allowed, reason = check_permission(
        "pause_agents",
        "PaperAuto",
        permission_scope="operator",
    )
    assert allowed is True
    assert reason is None

    allowed, reason = check_permission(
        "create_improvement_proposal",
        "PaperAuto",
        permission_scope="operator",
    )
    assert allowed is False
    assert "AllowEditing" in reason

    allowed, reason = check_permission(
        "create_improvement_proposal",
        "AllowEditing",
        permission_scope="operator",
    )
    assert allowed is True
    assert reason is None

    allowed, reason = check_permission(
        "create_strategy_draft",
        "Research",
        permission_scope="operator",
    )
    assert allowed is True
    assert reason is None

    allowed, reason = check_permission(
        "run_market_scan",
        "Research",
        permission_scope="operator",
    )
    assert allowed is False
    assert "PaperAuto" in reason

    allowed, reason = check_permission(
        "run_market_scan",
        "PaperAuto",
        permission_scope="operator",
    )
    assert allowed is True
    assert reason is None

    allowed, reason = check_permission(
        "propose_prompt_change",
        "PaperAuto",
        permission_scope="operator",
    )
    assert allowed is False
    assert "AllowEditing" in reason

    allowed, reason = check_permission(
        "propose_prompt_change",
        "AllowEditing",
        permission_scope="operator",
    )
    assert allowed is True
    assert reason is None

    operator_cmds = allowed_commands("Auto", permission_scope="operator")
    assert set(COMMANDS).issubset(set(operator_cmds))


@pytest.mark.asyncio
async def test_runtime_operator_surface_respects_operator_permission_mode():
    cfg = MagicMock()
    cfg.agent.permission_mode = "Observe"
    cfg.operator.permission_mode = "ReadOnly"
    cfg.live.state = "live_disabled"
    cfg.runtime.paused = False
    cfg.runtime.kill_switch_active = False
    cfg.runtime.scheduler_enabled = True
    cfg.exchange.mode = "demo"
    cfg.risk.max_daily_loss_pct = 1.0
    cfg.risk.max_demo_position_notional_usdt = 200.0
    cfg.risk.max_open_positions_demo = 6

    with patch("aletheia.agent_shell.sessions.create_session", return_value=("session-1", 0.0)), \
         patch("aletheia.agent_shell.sessions.close_session"), \
         patch("aletheia.agent_shell.audit.record_command"), \
         patch("aletheia.agent_shell.operator_store.get_session", return_value=None), \
         patch("aletheia.agent_shell.context.extend_summary_with_session_trace", return_value="summary"), \
         patch("aletheia.config.loader.load_config", return_value=cfg), \
         patch("aletheia.agent_shell.context.build_app_context", return_value={}), \
         patch("aletheia.agent_shell.context.context_summary_text", return_value="summary"), \
         patch(
             "aletheia.agent_shell.intent.classify_intent",
             new=AsyncMock(
                 return_value=CommandIntent(
                     command="create_research_note",
                     parameters={"title": "Test", "content": "Body"},
                     confidence=0.9,
                 )
             ),
         ):
        from aletheia.agent_shell.runtime import AgentShellRuntime

        runtime = AgentShellRuntime()
        resp = await runtime.handle(
            "write a research note",
            permission_mode="ReadOnly",
            permission_scope="operator",
        )

    assert resp.intent == "create_research_note"
    assert "Research" in resp.response


@pytest.mark.asyncio
async def test_runtime_operator_surface_is_still_bounded_by_worker_global_safety():
    cfg = MagicMock()
    cfg.agent.permission_mode = "Observe"
    cfg.operator.permission_mode = "Research"
    cfg.live.state = "live_disabled"
    cfg.runtime.paused = False
    cfg.runtime.kill_switch_active = False
    cfg.runtime.scheduler_enabled = True
    cfg.exchange.mode = "demo"
    cfg.risk.max_daily_loss_pct = 1.0
    cfg.risk.max_demo_position_notional_usdt = 200.0
    cfg.risk.max_open_positions_demo = 6

    with patch("aletheia.agent_shell.sessions.create_session", return_value=("session-1", 0.0)), \
         patch("aletheia.agent_shell.sessions.close_session"), \
         patch("aletheia.agent_shell.audit.record_command"), \
         patch("aletheia.agent_shell.operator_store.get_session", return_value=None), \
         patch("aletheia.agent_shell.context.extend_summary_with_session_trace", return_value="summary"), \
         patch("aletheia.config.loader.load_config", return_value=cfg), \
         patch("aletheia.agent_shell.context.build_app_context", return_value={}), \
         patch("aletheia.agent_shell.context.context_summary_text", return_value="summary"), \
         patch(
             "aletheia.agent_shell.intent.classify_intent",
             new=AsyncMock(
                 return_value=CommandIntent(
                     command="create_research_note",
                     parameters={"title": "Test", "content": "Body"},
                     confidence=0.9,
                 )
             ),
         ):
        from aletheia.agent_shell.runtime import AgentShellRuntime

        runtime = AgentShellRuntime()
        resp = await runtime.handle(
            "write a research note",
            permission_mode="Research",
            permission_scope="operator",
        )

    assert resp.intent == "create_research_note"
    assert "worker/global safety" in resp.response
    assert "Observe" in resp.response
    assert "Paper" in resp.response


@pytest.mark.asyncio
async def test_runtime_operator_surface_executes_only_when_operator_and_worker_allow():
    cfg = MagicMock()
    cfg.agent.permission_mode = "Paper"
    cfg.operator.permission_mode = "Research"
    cfg.live.state = "live_disabled"
    cfg.runtime.paused = False
    cfg.runtime.kill_switch_active = False
    cfg.runtime.scheduler_enabled = True
    cfg.exchange.mode = "demo"
    cfg.risk.max_daily_loss_pct = 1.0
    cfg.risk.max_demo_position_notional_usdt = 200.0
    cfg.risk.max_open_positions_demo = 6
    mocked_handler = AsyncMock(return_value={"created": True, "id": "note-1", "title": "Test"})

    with patch("aletheia.agent_shell.sessions.create_session", return_value=("session-1", 0.0)), \
         patch("aletheia.agent_shell.sessions.close_session"), \
         patch("aletheia.agent_shell.audit.record_command"), \
         patch("aletheia.agent_shell.operator_store.get_session", return_value=None), \
         patch("aletheia.agent_shell.context.extend_summary_with_session_trace", return_value="summary"), \
         patch("aletheia.config.loader.load_config", return_value=cfg), \
         patch("aletheia.agent_shell.context.build_app_context", return_value={}), \
         patch("aletheia.agent_shell.context.context_summary_text", return_value="summary"), \
         patch(
             "aletheia.agent_shell.intent.classify_intent",
             new=AsyncMock(
                 return_value=CommandIntent(
                     command="create_research_note",
                     parameters={"title": "Test", "content": "Body"},
                     confidence=0.9,
                 )
             ),
         ), \
         patch(
             "aletheia.agent_shell.commands.HANDLERS",
             {"create_research_note": mocked_handler},
         ), \
         patch(
             "aletheia.agent_shell.synthesis.synthesize_answer",
             new=AsyncMock(return_value="Research note created."),
         ):
        from aletheia.agent_shell.runtime import AgentShellRuntime

        runtime = AgentShellRuntime()
        resp = await runtime.handle(
            "write a research note",
            permission_mode="Research",
            permission_scope="operator",
        )

    assert resp.intent == "create_research_note"
    assert resp.response == "Research note created."
    mocked_handler.assert_awaited_once()
