"""Executable boundaries for the one admitted trading mainline."""

from __future__ import annotations

import ast
from pathlib import Path

from tests.specs.spec_meta import SpecClaim, spec_claim


ROOT = Path(__file__).resolve().parents[2]


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


@spec_claim(SpecClaim(
    "AR-S03", "active_runtime_scope", "Learning boundary", "L2a",
    "Learning code has no direct exchange, execution, live-route, or Risk mutation import.",
))
def test_learning_has_no_execution_or_risk_authority():
    forbidden_prefixes = (
        "extensions.exchanges", "aletheia.execution", "aletheia.server.routes.live",
        "aletheia.risk",
    )
    violations: dict[str, list[str]] = {}
    for path in sorted((ROOT / "core" / "aletheia" / "learning").glob("*.py")):
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
    operator_paths = sorted((ROOT / "core" / "aletheia" / "agent_shell").glob("*.py"))
    operator_violations = {
        str(path.relative_to(ROOT)): sorted(
            module for module in _imports(str(path.relative_to(ROOT)))
            if module.startswith("extensions.exchanges")
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
