"""Command Registry — defines all commands the Agent Shell can execute."""

from dataclasses import dataclass


@dataclass(frozen=True)
class CommandDef:
    name: str
    risk_class: str  # "read" | "low_risk_write" | "controlled_operational" | "proposal_only"
    worker_permission_required: str  # "Observe" | "Paper" | "Full"
    operator_permission_required: str  # "ReadOnly" | "Research" | "PaperAuto" | "AllowEditing" | "Auto"
    description: str
    requires_confirmation: bool = False
    requires_proposal: bool = False
    audit_event_type: str = ""
    backend_handler: str = ""

    @property
    def risk_level(self) -> str:
        """Backward-compatible alias used by existing runtime responses/tests."""
        mapping = {
            "read": "read",
            "low_risk_write": "low_write",
            "controlled_operational": "operational",
            "proposal_only": "operational",
        }
        return mapping.get(self.risk_class, self.risk_class)

    @property
    def required_permission(self) -> str:
        """Backward-compatible worker permission alias."""
        return self.worker_permission_required


COMMANDS: dict[str, CommandDef] = {
    "get_system_status": CommandDef(
        name="get_system_status",
        risk_class="read",
        worker_permission_required="Observe",
        operator_permission_required="ReadOnly",
        description="Show gateway health, permission mode, live state, and config summary",
        audit_event_type="operator_get_system_status",
        backend_handler="get_system_status",
    ),
    "get_operator_status": CommandDef(
        name="get_operator_status",
        risk_class="read",
        worker_permission_required="Observe",
        operator_permission_required="ReadOnly",
        description="Show operator heartbeat, mode, queue, and latest task context",
        audit_event_type="operator_get_operator_status",
        backend_handler="get_operator_status",
    ),
    "get_agent_activity": CommandDef(
        name="get_agent_activity",
        risk_class="read",
        worker_permission_required="Observe",
        operator_permission_required="ReadOnly",
        description="Show recent agent runs with status and timing",
        audit_event_type="operator_get_agent_activity",
        backend_handler="get_agent_activity",
    ),
    "get_market_snapshot": CommandDef(
        name="get_market_snapshot",
        risk_class="read",
        worker_permission_required="Observe",
        operator_permission_required="ReadOnly",
        description="Show latest market prices for observed symbols",
        audit_event_type="operator_get_market_snapshot",
        backend_handler="get_market_snapshot",
    ),
    "get_positions": CommandDef(
        name="get_positions",
        risk_class="read",
        worker_permission_required="Observe",
        operator_permission_required="ReadOnly",
        description="Show open positions with PnL",
        audit_event_type="operator_get_positions",
        backend_handler="get_positions",
    ),
    "explain_agent_run": CommandDef(
        name="explain_agent_run",
        risk_class="read",
        worker_permission_required="Observe",
        operator_permission_required="ReadOnly",
        description="Explain what a specific agent run did (steps, tools, output)",
        audit_event_type="operator_explain_agent_run",
        backend_handler="explain_agent_run",
    ),
    "explain_proposal": CommandDef(
        name="explain_proposal",
        risk_class="read",
        worker_permission_required="Observe",
        operator_permission_required="ReadOnly",
        description="Explain a trade proposal and its risk decision",
        audit_event_type="operator_explain_proposal",
        backend_handler="explain_proposal",
    ),
    "get_strategy_lifecycle": CommandDef(
        name="get_strategy_lifecycle",
        risk_class="read",
        worker_permission_required="Observe",
        operator_permission_required="ReadOnly",
        description="Inspect strategy state and promotion-gate readiness",
        audit_event_type="operator_get_strategy_lifecycle",
        backend_handler="get_strategy_lifecycle",
    ),
    "get_failed_tasks": CommandDef(
        name="get_failed_tasks",
        risk_class="read",
        worker_permission_required="Observe",
        operator_permission_required="ReadOnly",
        description="Show failed operator tasks and failed recent agent runs",
        audit_event_type="operator_get_failed_tasks",
        backend_handler="get_failed_tasks",
    ),
    "get_recommendations": CommandDef(
        name="get_recommendations",
        risk_class="read",
        worker_permission_required="Observe",
        operator_permission_required="ReadOnly",
        description="Show current backend operator recommendations",
        audit_event_type="operator_get_recommendations",
        backend_handler="get_recommendations",
    ),
    "pause_agents": CommandDef(
        name="pause_agents",
        risk_class="controlled_operational",
        worker_permission_required="Paper",
        operator_permission_required="PaperAuto",
        description="Pause all scheduled agent jobs",
        requires_confirmation=True,
        audit_event_type="operator_pause_agents",
        backend_handler="pause_agents",
    ),
    "resume_agents": CommandDef(
        name="resume_agents",
        risk_class="controlled_operational",
        worker_permission_required="Paper",
        operator_permission_required="PaperAuto",
        description="Resume paused agent jobs",
        requires_confirmation=True,
        audit_event_type="operator_resume_agents",
        backend_handler="resume_agents",
    ),
    "create_research_note": CommandDef(
        name="create_research_note",
        risk_class="low_risk_write",
        worker_permission_required="Paper",
        operator_permission_required="Research",
        description="Create a research note in the knowledge base",
        audit_event_type="operator_create_research_note",
        backend_handler="create_research_note",
    ),
    "create_strategy_draft": CommandDef(
        name="create_strategy_draft",
        risk_class="low_risk_write",
        worker_permission_required="Paper",
        operator_permission_required="Research",
        description="Create a draft strategy and optional strategy memory note",
        audit_event_type="operator_create_strategy_draft",
        backend_handler="create_strategy_draft",
    ),
    "create_dreaming_log": CommandDef(
        name="create_dreaming_log",
        risk_class="low_risk_write",
        worker_permission_required="Paper",
        operator_permission_required="Research",
        description="Create a dreaming log entry in the knowledge base",
        audit_event_type="operator_create_dreaming_log",
        backend_handler="create_dreaming_log",
    ),
    "create_diagnostic_todo": CommandDef(
        name="create_diagnostic_todo",
        risk_class="low_risk_write",
        worker_permission_required="Paper",
        operator_permission_required="Research",
        description="Create a diagnostic todo item for a system issue",
        audit_event_type="operator_create_diagnostic_todo",
        backend_handler="create_diagnostic_todo",
    ),
    "mark_task_done": CommandDef(
        name="mark_task_done",
        risk_class="low_risk_write",
        worker_permission_required="Paper",
        operator_permission_required="Research",
        description="Mark an operator task completed with a short summary",
        audit_event_type="operator_mark_task_done",
        backend_handler="mark_task_done",
    ),
    "create_improvement_proposal": CommandDef(
        name="create_improvement_proposal",
        risk_class="low_risk_write",
        worker_permission_required="Paper",
        operator_permission_required="AllowEditing",
        description="Create a system improvement proposal",
        audit_event_type="operator_create_improvement_proposal",
        backend_handler="create_improvement_proposal",
    ),
    "draft_extension_manifest": CommandDef(
        name="draft_extension_manifest",
        risk_class="low_risk_write",
        worker_permission_required="Paper",
        operator_permission_required="AllowEditing",
        description="Draft a typed extension manifest for sandbox review",
        audit_event_type="operator_draft_extension_manifest",
        backend_handler="draft_extension_manifest",
    ),
    "test_extension_sandbox": CommandDef(
        name="test_extension_sandbox",
        risk_class="low_risk_write",
        worker_permission_required="Paper",
        operator_permission_required="AllowEditing",
        description="Run a sandbox-only validation pass for a drafted extension",
        audit_event_type="operator_test_extension_sandbox",
        backend_handler="test_extension_sandbox",
    ),
    "create_daily_review": CommandDef(
        name="create_daily_review",
        risk_class="low_risk_write",
        worker_permission_required="Paper",
        operator_permission_required="Research",
        description="Create a daily review entry in the knowledge base",
        audit_event_type="operator_create_daily_review",
        backend_handler="create_daily_review",
    ),
    "create_review_report": CommandDef(
        name="create_review_report",
        risk_class="low_risk_write",
        worker_permission_required="Observe",
        operator_permission_required="Research",
        description="Create a non-trading review report under data/reports",
        audit_event_type="operator_create_review_report",
        backend_handler="create_review_report",
    ),
    "create_diagnostic_report": CommandDef(
        name="create_diagnostic_report",
        risk_class="low_risk_write",
        worker_permission_required="Observe",
        operator_permission_required="Research",
        description="Create a diagnostic report for operator observability follow-up",
        audit_event_type="operator_create_diagnostic_report",
        backend_handler="create_diagnostic_report",
    ),
    "archive_low_value_notification": CommandDef(
        name="archive_low_value_notification",
        risk_class="low_risk_write",
        worker_permission_required="Observe",
        operator_permission_required="Research",
        description="Archive a low-value recommendation so it stops cluttering the operator queue",
        audit_event_type="operator_archive_low_value_notification",
        backend_handler="archive_low_value_notification",
    ),
    "mark_strategy_under_review": CommandDef(
        name="mark_strategy_under_review",
        risk_class="low_risk_write",
        worker_permission_required="Paper",
        operator_permission_required="Research",
        description="Mark a strategy under operator review with a structured review note",
        audit_event_type="operator_mark_strategy_under_review",
        backend_handler="mark_strategy_under_review",
    ),
    "run_market_scan": CommandDef(
        name="run_market_scan",
        risk_class="controlled_operational",
        worker_permission_required="Observe",
        operator_permission_required="PaperAuto",
        description="Trigger a manual market observer scan",
        requires_confirmation=True,
        audit_event_type="operator_run_market_scan",
        backend_handler="run_market_scan",
    ),
    "rerun_failed_agent_task": CommandDef(
        name="rerun_failed_agent_task",
        risk_class="controlled_operational",
        worker_permission_required="Observe",
        operator_permission_required="PaperAuto",
        description="Rerun the latest failed agent task or a specified failed run",
        requires_confirmation=True,
        audit_event_type="operator_rerun_failed_agent_task",
        backend_handler="rerun_failed_agent_task",
    ),
    "set_symbol_observe_only": CommandDef(
        name="set_symbol_observe_only",
        risk_class="controlled_operational",
        worker_permission_required="Paper",
        operator_permission_required="PaperAuto",
        description="Remove a symbol from tradable lists while keeping market observation on",
        requires_confirmation=True,
        audit_event_type="operator_set_symbol_observe_only",
        backend_handler="set_symbol_observe_only",
    ),
    "create_paper_experiment": CommandDef(
        name="create_paper_experiment",
        risk_class="controlled_operational",
        worker_permission_required="Paper",
        operator_permission_required="PaperAuto",
        description="Create a new paper experiment strategy and advance it to paper_candidate",
        requires_confirmation=True,
        audit_event_type="operator_create_paper_experiment",
        backend_handler="create_paper_experiment",
    ),
    "reconcile_positions": CommandDef(
        name="reconcile_positions",
        risk_class="controlled_operational",
        worker_permission_required="Paper",
        operator_permission_required="PaperAuto",
        description="Trigger position reconciliation: classify, auto-adopt externals, close orphans (demo)",
        requires_confirmation=True,
        audit_event_type="operator_reconcile_positions",
        backend_handler="reconcile_positions",
    ),
    "pause_paper_strategy": CommandDef(
        name="pause_paper_strategy",
        risk_class="controlled_operational",
        worker_permission_required="Paper",
        operator_permission_required="PaperAuto",
        description="Pause a paper strategy and clear any still-open linked opportunities from execution",
        requires_confirmation=True,
        audit_event_type="operator_pause_paper_strategy",
        backend_handler="pause_paper_strategy",
    ),
    "propose_strategy_promotion": CommandDef(
        name="propose_strategy_promotion",
        risk_class="proposal_only",
        worker_permission_required="Paper",
        operator_permission_required="AllowEditing",
        description="Create a proposal artifact for promoting a strategy lifecycle state",
        requires_proposal=True,
        audit_event_type="operator_propose_strategy_promotion",
        backend_handler="propose_strategy_promotion",
    ),
    "propose_prompt_change": CommandDef(
        name="propose_prompt_change",
        risk_class="proposal_only",
        worker_permission_required="Paper",
        operator_permission_required="AllowEditing",
        description="Create a proposal artifact for changing a prompt or playbook draft",
        requires_proposal=True,
        audit_event_type="operator_propose_prompt_change",
        backend_handler="propose_prompt_change",
    ),
    "propose_schedule_change": CommandDef(
        name="propose_schedule_change",
        risk_class="proposal_only",
        worker_permission_required="Paper",
        operator_permission_required="AllowEditing",
        description="Create a proposal artifact for changing an agent schedule",
        requires_proposal=True,
        audit_event_type="operator_propose_schedule_change",
        backend_handler="propose_schedule_change",
    ),
    "propose_live_limited_enablement": CommandDef(
        name="propose_live_limited_enablement",
        risk_class="proposal_only",
        worker_permission_required="Paper",
        operator_permission_required="AllowEditing",
        description="Create a proposal artifact for human review of live-limited enablement",
        requires_proposal=True,
        audit_event_type="operator_propose_live_limited_enablement",
        backend_handler="propose_live_limited_enablement",
    ),
    "propose_risk_boundary_change": CommandDef(
        name="propose_risk_boundary_change",
        risk_class="proposal_only",
        worker_permission_required="Paper",
        operator_permission_required="AllowEditing",
        description="Create a proposal artifact for changing risk boundaries",
        requires_proposal=True,
        audit_event_type="operator_propose_risk_boundary_change",
        backend_handler="propose_risk_boundary_change",
    ),
    "propose_command_center_behavior_change": CommandDef(
        name="propose_command_center_behavior_change",
        risk_class="proposal_only",
        worker_permission_required="Observe",
        operator_permission_required="AllowEditing",
        description="Create a proposal artifact for changing Command Center behavior",
        requires_proposal=True,
        audit_event_type="operator_propose_command_center_behavior_change",
        backend_handler="propose_command_center_behavior_change",
    ),
    "propose_extension_enablement": CommandDef(
        name="propose_extension_enablement",
        risk_class="proposal_only",
        worker_permission_required="Paper",
        operator_permission_required="AllowEditing",
        description="Create a proposal artifact requesting human approval to enable an extension",
        requires_proposal=True,
        audit_event_type="operator_propose_extension_enablement",
        backend_handler="propose_extension_enablement",
    ),
    "evaluate_trading_opportunity": CommandDef(
        name="evaluate_trading_opportunity",
        risk_class="controlled_operational",
        worker_permission_required="Paper",
        operator_permission_required="PaperAuto",
        description="PI agent evaluates current market state and submits a paper proposal if conviction >= 55",
        requires_confirmation=False,
        audit_event_type="operator_evaluate_trading_opportunity",
        backend_handler="evaluate_trading_opportunity",
    ),
    "review_portfolio_state": CommandDef(
        name="review_portfolio_state",
        risk_class="controlled_operational",
        worker_permission_required="Paper",
        operator_permission_required="PaperAuto",
        description="PI agent reviews open positions for overexposure and optionally closes / reduces",
        requires_confirmation=False,
        audit_event_type="operator_review_portfolio_state",
        backend_handler="review_portfolio_state",
    ),
}

FORBIDDEN_INTENTS = frozenset([
    "enable_live", "modify_credentials", "bypass_risk",
    "place_live_order", "modify_execution_gateway", "delete_knowledge",
    "change_risk_limits",
    # Defense-in-depth double-lock for the two Level-3 operator actions that
    # previously had NO permission-layer block (operator_loop._LEVEL_3_ACTIONS
    # classified them L3, but check_permission never rejected them). A Level-3
    # classification alone is not a hard wall — these must also be permanently
    # forbidden intents so no permission_mode can ever reach them. (CR-RE/OL-C03,
    # BACKEND_ISSUES §15.1, 2026-06-23.)
    "deactivate_kill_switch", "modify_permissions",
    # PI Constitution invariant (PCI-G01, 2026-06-30): PI must NEVER modify its
    # own system prompt base layer. propose_prompt_change exists as a
    # proposal_only command (human reviews it), but any lower-risk command
    # (create_research_note, create_dreaming_log, etc.) that attempts to
    # overwrite prompt files must be hard-blocked here. The command gate alone is
    # insufficient because low-risk commands bypass command-level validation.
    "modify_own_prompt",
])


def get_command(name: str) -> CommandDef | None:
    return COMMANDS.get(name)


def list_command_names() -> list[str]:
    return list(COMMANDS.keys())
