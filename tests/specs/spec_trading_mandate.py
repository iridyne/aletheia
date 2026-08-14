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
        "Trading Mandate is a versioned deterministic evidence contract and cannot be imported by native behavior authorities.",
    )
)
def test_trading_mandate_is_not_imported_by_behavior_authorities():
    owner_paths = [
        ROOT / "aletheia" / "agents" / "market_observer.py",
        ROOT / "aletheia" / "agents" / "paper_trader.py",
        ROOT / "aletheia" / "agents" / "paper_trader_ranker.py",
        ROOT / "aletheia" / "universe" / "service.py",
        ROOT / "aletheia" / "agent_shell" / "trading_decision.py",
        ROOT / "aletheia" / "risk" / "engine.py",
        ROOT / "aletheia" / "execution" / "gateway.py",
        ROOT / "aletheia" / "execution" / "paper_broker.py",
        ROOT / "aletheia" / "portfolio" / "position_manager.py",
        ROOT / "aletheia" / "server" / "routes" / "live.py",
    ]
    owner_paths.extend((ROOT / "aletheia" / "learning").glob("*.py"))
    forbidden = "aletheia.trading.trading_mandate"
    violations = {
        str(path.relative_to(ROOT)): sorted(
            module for module in _imports(path) if module == forbidden
        )
        for path in owner_paths
        if path.exists() and forbidden in _imports(path)
    }
    assert violations == {}


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