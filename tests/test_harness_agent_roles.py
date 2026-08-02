from aletheia.economics.agent_roles import AgentRole, role_for_logical_agent


def test_role_taxonomy_preserves_system_operator_compatibility():
    assert role_for_logical_agent("system_operator") is AgentRole.SYSTEM_OPERATOR
    assert role_for_logical_agent("pi_trading_decision") is AgentRole.SYSTEM_OPERATOR


def test_role_taxonomy_classifies_existing_worker_and_evaluator_names():
    assert role_for_logical_agent("market_observer") is AgentRole.WORKER
    assert role_for_logical_agent("research_worker") is AgentRole.WORKER
    assert role_for_logical_agent("risk_critic") is AgentRole.EVALUATOR
    assert role_for_logical_agent("orchestrator") is AgentRole.COORDINATOR


def test_unknown_attribution_is_never_invented():
    assert role_for_logical_agent("future_agent") is AgentRole.UNKNOWN
    assert role_for_logical_agent(None) is AgentRole.UNKNOWN
