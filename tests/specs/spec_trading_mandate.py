"""Executable boundary claims for the observe-only Trading Mandate contract."""

from __future__ import annotations

import ast
from pathlib import Path

from tests.specs.spec_meta import SpecClaim, spec_claim

ROOT = Path(__file__).resolve().parents[2]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules




@spec_claim(
    SpecClaim(
        "TM-S01",
        "trading_mandate_contract",
        "observe-only authority boundary",
        "L2a",
        "Trading Mandate is a versioned deterministic evidence contract and cannot "
        "be imported by native behavior authorities.",
        status="implemented",
    )
)
def test_trading_mandate_is_not_imported_by_behavior_authorities():
    """Restored after #2945 removed it.

    That refactor deleted this claim because its module list was hardcoded and
    several entries (paper_trader, paper_broker) no longer existed — but the
    boundary it guarded is still real, and removing the claim left
    trading_mandate_contract with no L2a coverage at all. The authority set is now
    resolved from modules that exist, so a renamed authority fails loudly instead
    of silently dropping out of the check.
    """
    authorities = (
        "aletheia.risk.engine",
        "aletheia.execution.gateway",
        "aletheia.execution.coordinator",
        "aletheia.portfolio.position_manager",
        "aletheia.universe.service",
        "aletheia.trading.entry_plan_compiler",
        "aletheia.agent_shell.entry_planner",
    )
    forbidden = "aletheia.trading.trading_mandate"
    violations: dict[str, list[str]] = {}
    missing: list[str] = []
    for dotted in authorities:
        path = ROOT / "core" / Path(*dotted.split(".")).with_suffix(".py")
        if not path.exists():
            missing.append(dotted)
            continue
        offending = sorted(m for m in _imports(path) if m == forbidden)
        if offending:
            violations[dotted] = offending
    assert not missing, (
        "these behavior authorities no longer resolve, so the boundary is being "
        f"checked against fewer modules than declared: {missing}"
    )
    assert not violations, (
        "observe-only Trading Mandate evidence must not be imported by a behavior "
        f"authority: {violations}"
    )


@spec_claim(
    SpecClaim(
        "TM-S02",
        "trading_mandate_contract",
        "fit result behavior flag",
        "L2b",
        "Every mandate fit result explicitly records behavior_changed=false.",
    )
)
def test_fit_contract_is_observation_only():
    from aletheia.trading.trading_mandate import (
        TradingMandate,
        assess_trading_mandate_fit,
    )

    mandate = TradingMandate(
        contract_version="trading_mandate/v1",
        mandate_id="spec-mandate",
        mandate_version=1,
        status="active",
        enforcement_mode="observe_only",
        market_scope={
            "allowed_asset_classes": ["crypto"],
            "allowed_physical_market_types": ["spot", "perp"],
        },
        edge_scope={},
    )
    result = assess_trading_mandate_fit(
        mandate=mandate,
        opportunity={"id": "opportunity"},
        decision={"id": "decision"},
        proposal={"id": "proposal", "market_type": "spot"},
    )
    assert result["behavior_changed"] is False