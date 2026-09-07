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