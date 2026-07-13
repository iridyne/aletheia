"""Executable boundaries for the one admitted trading mainline."""

from __future__ import annotations

import ast
from pathlib import Path

from tests.specs.spec_meta import SpecClaim, spec_claim


ROOT = Path(__file__).resolve().parents[2]
SCOPE_PATH = ROOT / "docs" / "product" / "ACTIVE_RUNTIME_SCOPE.md"


def _scope() -> dict:
    from aletheia.knowledge.frontmatter import parse_frontmatter

    metadata, _ = parse_frontmatter(SCOPE_PATH)
    return metadata


def _tree(relative_path: str) -> ast.AST:
    return ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))


def _imports(relative_path: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(_tree(relative_path)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _call_names(relative_path: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_tree(relative_path)):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name:
            names.add(name)
    return names


@spec_claim(SpecClaim(
    "AR-S01", "active_runtime_scope", "frontmatter declaration", "L2a",
    "The active crypto paper mainline names exactly one decision, risk, execution, "
    "outcome, and Learning authority boundary.",
))
def test_active_runtime_scope_is_complete_and_narrow():
    scope = _scope()
    assert scope == {
        "schema_version": 1,
        "scope_id": "crypto-paper-mainline-v1",
        "status": "active",
        "asset_classes": ["crypto"],
        "market_types": ["spot", "perp"],
        "execution_modes": ["paper_internal"],
        "decision_authority": (
            "aletheia.agent_shell.trading_decision.evaluate_trading_opportunity"
        ),
        "risk_authority": "aletheia.risk.engine.evaluate_proposal",
        "execution_entrypoint": "aletheia.execution.gateway.submit_proposal",
        "outcome_entrypoint": (
            "aletheia.portfolio.outcome_closure.close_position_verified"
        ),
        "learning_measurement": "enabled",
        "learning_influence_default": "disabled",
        "learning_authority_gate": (
            "aletheia.learning.experiments.apply_numeric_treatment"
        ),
        "causal_chain": [
            "opportunity", "system_operator_decision", "proposal",
            "risk_decision", "order", "fill", "position", "genuine_outcome",
        ],
    }


@spec_claim(SpecClaim(
    "AR-S02", "active_runtime_scope", "scanner boundary", "L2a",
    "Scanner/observer production entrypoints do not call proposal, gateway, broker, "
    "or exchange order functions.",
))
def test_scanners_have_no_order_call():
    scanner_paths = [
        "aletheia/agents/market_observer.py",
        "aletheia/agents/paper_trader.py",
        "aletheia/agents/paper_trader_ranker.py",
        "aletheia/universe/service.py",
    ]
    forbidden = {
        "submit_proposal", "execute_existing_proposal", "create_paper_proposal",
        "place_order", "create_order", "execute_paper_order",
    }
    violations = {
        path: sorted(_call_names(path) & forbidden)
        for path in scanner_paths
        if _call_names(path) & forbidden
    }
    assert violations == {}


@spec_claim(SpecClaim(
    "AR-S03", "active_runtime_scope", "Learning boundary", "L2a",
    "Learning code has no direct exchange, execution, live-route, or Risk mutation import.",
))
def test_learning_has_no_execution_or_risk_authority():
    forbidden_prefixes = (
        "aletheia.exchanges", "aletheia.execution", "aletheia.server.routes.live",
        "aletheia.risk",
    )
    violations: dict[str, list[str]] = {}
    for path in sorted((ROOT / "aletheia" / "learning").glob("*.py")):
        rel = str(path.relative_to(ROOT))
        bad = sorted(
            module for module in _imports(rel)
            if module.startswith(forbidden_prefixes)
        )
        if bad:
            violations[rel] = bad
    assert violations == {}


@spec_claim(SpecClaim(
    "AR-S04", "active_runtime_scope", "operator and UI boundary", "L2a",
    "System Operator and frontend sources cannot call exchange order adapters directly.",
))
def test_operator_and_frontend_have_no_direct_exchange_order_path():
    operator_paths = sorted((ROOT / "aletheia" / "agent_shell").glob("*.py"))
    operator_violations = {
        str(path.relative_to(ROOT)): sorted(
            module for module in _imports(str(path.relative_to(ROOT)))
            if module.startswith("aletheia.exchanges")
        )
        for path in operator_paths
    }
    operator_violations = {
        path: modules for path, modules in operator_violations.items() if modules
    }
    assert operator_violations == {}

    forbidden_frontend_tokens = (
        "api.bitget.com", "www.okx.com/api/v5/trade", "api.binance.com/api/v3/order",
    )
    frontend_violations = []
    for path in sorted((ROOT / "frontend" / "src").rglob("*")):
        if path.suffix not in {".ts", ".tsx"}:
            continue
        text = path.read_text(encoding="utf-8")
        if any(token in text for token in forbidden_frontend_tokens):
            frontend_violations.append(str(path.relative_to(ROOT)))
    assert frontend_violations == []


@spec_claim(SpecClaim(
    "AR-S05", "active_runtime_scope", "risk and execution chokepoints", "L2a",
    "The paper wrapper enters submit_proposal and the gateway evaluates Risk before any broker.",
))
def test_native_proposals_enter_gateway_and_risk_before_execution():
    paper_tools = (ROOT / "aletheia" / "execution" / "paper_tools.py").read_text(
        encoding="utf-8"
    )
    assert "return submit_proposal(" in paper_tools

    gateway_path = "aletheia/execution/gateway.py"
    calls = _call_names(gateway_path)
    assert "evaluate_proposal" in calls
    assert {
        "execute_paper_order", "execute_demo_order", "execute_testnet_order",
    } & calls


@spec_claim(SpecClaim(
    "AR-S06", "active_runtime_scope", "Guard authority", "L2a",
    "The System Operator records Guard output as advisory and does not return early on a block verdict.",
))
def test_guard_chain_cannot_replace_risk_authority():
    path = ROOT / "aletheia" / "agent_shell" / "trading_decision.py"
    source = path.read_text(encoding="utf-8")
    start = source.index("async def submit_decision_as_proposal")
    end = source.index("\ndef _proposal_direction_overheated", start)
    body = source[start:end]
    guard_start = body.index("guard_verdicts = run_guards")
    proposal_start = body.index("create_paper_proposal(", guard_start)
    guard_section = body[guard_start:proposal_start]
    assert "guard_summary[\"blocks\"]" in guard_section
    assert "return None" not in guard_section


@spec_claim(SpecClaim(
    "AR-S07", "active_runtime_scope", "causal carrier", "L2a",
    "Native positions carry proposal, order, and opportunity links; runtime linkage is proved by the Golden Path.",
))
def test_position_carries_active_mainline_causal_ids():
    from aletheia.storage.models import Position

    assert {"proposal_id", "order_id", "opportunity_id"} <= set(Position.model_fields)


@spec_claim(SpecClaim(
    "AR-S08", "active_runtime_scope", "runtime session provenance", "L2a",
    "Each Gateway boot creates one runtime_sessions row (session under a runtime_id), "
    "and the seven core trading-fact entities carry the producing session's identity "
    "(runtime_session_id, producer_commit, behavior_fingerprint) as expand-only nullable "
    "fields. Positions additionally carry closed_* fields so the closing (outcome) session "
    "is distinguishable from the opening session. Full attribution is proved by the Golden Path.",
    status="implemented",
))
def test_core_entities_carry_runtime_session_provenance():
    from aletheia.storage.models import (
        Opportunity, PiDecision, TradeProposal, RiskDecision, Order, Fill, Position,
        RuntimeSession,
    )

    provenance = {"runtime_session_id", "producer_commit", "behavior_fingerprint"}
    for model in (Opportunity, PiDecision, TradeProposal, RiskDecision, Order, Fill, Position):
        assert provenance <= set(model.model_fields), (
            f"{model.__name__} missing provenance fields: {provenance - set(model.model_fields)}"
        )

    # Positions carry a distinct outcome-producer identity stamped at close.
    assert {"closed_runtime_session_id", "closed_producer_commit",
            "closed_behavior_fingerprint"} <= set(Position.model_fields)

    # The Runtime Session row records the identity every boot.
    assert {
        "session_id", "runtime_id", "boot_commit", "config_hash", "schema_version",
        "active_scope_id", "execution_mode", "started_at", "ended_at", "shutdown_clean",
    } <= set(RuntimeSession.model_fields)
