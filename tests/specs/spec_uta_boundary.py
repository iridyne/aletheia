"""Executable structural contract for the Phase 0 UTA boundary."""

from __future__ import annotations

import ast
from pathlib import Path

from tests.specs.spec_meta import SpecClaim, spec_claim


ROOT = Path(__file__).resolve().parents[2]
UTA_ROOT = ROOT / "aletheia" / "uta"


@spec_claim(
    SpecClaim(
        "UTA-S01",
        "uta_boundary",
        "§1 one external-account contract",
        "L2b",
        "UTA V1 exposes the frozen complete read/write protocol without a public TradingVenue abstraction.",
    )
)
def test_uta_v1_protocol_surface_is_complete_and_singular():
    from aletheia.uta.contract import UTAAdapter, UTA_CONTRACT_VERSION

    assert UTA_CONTRACT_VERSION == 1
    expected = {
        "connect",
        "close",
        "health",
        "get_account",
        "get_positions",
        "get_open_orders",
        "get_order",
        "get_fills",
        "get_mass_status",
        "get_funding_ledger",
        "verify_position_exit",
        "probe_account_authority",
        "submit_order",
        "modify_order",
        "cancel_order",
    }
    assert expected <= set(UTAAdapter.__protocol_attrs__)
    assert not list((ROOT / "aletheia").rglob("*trading_venue*.py"))


@spec_claim(
    SpecClaim(
        "UTA-S02",
        "uta_boundary",
        "§2 storage and client isolation",
        "L2b",
        "UTA canonical contract and registry contain no Aletheia storage writes, concrete private clients, or live client singleton.",
    )
)
def test_uta_package_has_no_storage_or_concrete_client_side_channel():
    violations = []
    forbidden_imports = (
        "aletheia.storage",
        "aletheia.exchanges",
        "aletheia.execution",
        "ccxt",
    )
    for path in UTA_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if name.startswith(forbidden_imports):
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: {name}")
    assert violations == []


@spec_claim(
    SpecClaim(
        "UTA-S04",
        "uta_boundary",
        "§4 Phase 1 shadow isolation",
        "L2b",
        "The CCXT shadow adapter uses the managed factory and has no production consumer or mutation cutover.",
    )
)
def test_phase_one_shadow_uses_managed_ccxt_without_consumer_cutover():
    adapter = (UTA_ROOT / "ccxt_adapter.py").read_text(encoding="utf-8")
    assert "from aletheia.net import make_ccxt" in adapter
    assert "read_only_shadow_mutation_disabled" in adapter

    consumers = []
    for path in (ROOT / "aletheia").rglob("*.py"):
        if path.is_relative_to(UTA_ROOT):
            continue
        text = path.read_text(encoding="utf-8")
        if "CCXTUTAAdapter" in text:
            consumers.append(str(path.relative_to(ROOT)))
    assert consumers == []


@spec_claim(
    SpecClaim(
        "UTA-S03",
        "uta_boundary",
        "§3 migration evidence",
        "L2c",
        "The exact-head private-I/O consumer matrix is committed before any production cutover.",
    )
)
def test_exact_head_consumer_port_matrix_exists():
    matrix = ROOT / "docs" / "research" / "audits" / "uta_consumer_port_matrix_2026-08-27.md"
    text = matrix.read_text(encoding="utf-8")
    for consumer in (
        "Gateway entry",
        "Position close",
        "account sync",
        "Live unlock",
        "reconciliation",
        "funding settlement",
    ):
        assert consumer in text
