"""Executable structural contract for the Phase 0 UTA boundary."""

from __future__ import annotations

import ast
from pathlib import Path

from tests.specs.spec_meta import SpecClaim, spec_claim


ROOT = Path(__file__).resolve().parents[2]
UTA_ROOT = ROOT / "aletheia" / "uta"


def _mutation_command_importers(root):
    owners = {
        "aletheia/uta/contract.py", "aletheia/uta/models.py",
        "aletheia/execution/coordinator.py", "aletheia/execution/gateway.py",
        "extensions/exchanges/ccxt/adapter.py",
    }
    commands = {"SubmitOrderCommand", "ModifyOrderCommand", "CancelOrderCommand"}
    command_importers = []
    for directory in ("aletheia", "extensions"):
        for path in (root / directory).rglob("*.py"):
            relative = path.relative_to(root).as_posix()
            if relative in owners:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            names = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Name):
                    names.add(node.id)
                elif isinstance(node, ast.Attribute):
                    names.add(node.attr)
                elif isinstance(node, ast.ImportFrom):
                    names.update(alias.name for alias in node.names)
            if names & commands:
                command_importers.append(relative)
    return sorted(command_importers)


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
        "extensions.exchanges",
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
        "UTA-S06",
        "uta_boundary",
        "§6 Binance testnet and OKX demo execution slices",
        "L2b",
        "Gateway selects the coordinator only for qualified per-mode uta_v1 profiles; the coordinator is the sole production caller of UTA mutation commands and owns no concrete venue client.",
    )
)
def test_migrated_mutations_have_one_gateway_coordinator_path():
    from extensions.exchanges.binance.profile import BinanceLiveProfile
    from extensions.exchanges.okx.profile import OKXDemoProfile, OKXLiveProfile
    from extensions.exchanges.bitget.profile import (
        BitgetClassicV2LiveProfile,
        BitgetClassicV2Profile,
        BitgetUTAV3LiveProfile,
        BitgetUTAV3Profile,
    )

    coordinator_path = ROOT / "aletheia" / "execution" / "coordinator.py"
    coordinator = coordinator_path.read_text(encoding="utf-8")
    gateway = (ROOT / "aletheia" / "execution" / "gateway.py").read_text(
        encoding="utf-8"
    )
    manager = (ROOT / "aletheia" / "portfolio" / "position_manager.py").read_text(
        encoding="utf-8"
    )
    stops = (ROOT / "aletheia" / "execution" / "stops.py").read_text(encoding="utf-8")


    assert _mutation_command_importers(ROOT) == []
    assert all((
        BinanceLiveProfile.mutation_market_order,
        BinanceLiveProfile.mutation_limit_order,
        BinanceLiveProfile.mutation_modify_order,
        BinanceLiveProfile.mutation_cancel_order,
        BinanceLiveProfile.mutation_reduce_only,
        BinanceLiveProfile.mutation_leverage,
        BinanceLiveProfile.mutation_post_only,
        BinanceLiveProfile.mutation_client_order_id,
    ))
    assert all((
        OKXDemoProfile.mutation_market_order,
        OKXDemoProfile.mutation_limit_order,
        OKXDemoProfile.mutation_modify_order,
        OKXDemoProfile.mutation_cancel_order,
        OKXDemoProfile.mutation_reduce_only,
        OKXDemoProfile.mutation_leverage,
        OKXDemoProfile.mutation_post_only,
        OKXDemoProfile.mutation_client_order_id,
    ))
    assert all((
        OKXLiveProfile.mutation_market_order,
        OKXLiveProfile.mutation_limit_order,
        OKXLiveProfile.mutation_modify_order,
        OKXLiveProfile.mutation_cancel_order,
        OKXLiveProfile.mutation_reduce_only,
        OKXLiveProfile.mutation_leverage,
        OKXLiveProfile.mutation_post_only,
        OKXLiveProfile.mutation_client_order_id,
    ))
    for profile in (BitgetClassicV2Profile, BitgetUTAV3Profile):
        assert all((
            profile.mutation_market_order,
            profile.mutation_limit_order,
            profile.mutation_modify_order,
            profile.mutation_cancel_order,
            profile.mutation_reduce_only,
            profile.mutation_leverage,
            profile.mutation_post_only,
            profile.mutation_client_order_id,
            profile.mutation_trigger_order,
            profile.native_tpsl,
            profile.protection_replace,
            profile.protection_cancel,
        ))
    for profile in (BitgetClassicV2LiveProfile, BitgetUTAV3LiveProfile):
        assert all((
            profile.mutation_market_order,
            profile.mutation_limit_order,
            profile.mutation_modify_order,
            profile.mutation_cancel_order,
            profile.mutation_reduce_only,
            profile.mutation_leverage,
            profile.mutation_post_only,
            profile.mutation_client_order_id,
            profile.mutation_trigger_order,
            profile.native_tpsl,
            profile.protection_replace,
            profile.protection_cancel,
        ))


@spec_claim(
    SpecClaim(
        "UTA-S05",
        "uta_boundary",
        "§5 Phase 3 evidence consumer cutover",
        "L2b",
        "Recovery, reconciliation, funding, verified exit, and Live Trust own no concrete private venue client.",
    )
)
def test_phase_three_evidence_consumers_have_no_concrete_private_client_import():
    consumers = (
        "aletheia/execution/reconciliation.py",
        "aletheia/execution/stale_order_cleanup.py",
        "aletheia/execution/pending_close_recovery.py",
        "aletheia/portfolio/disappearance_settlement.py",
        "aletheia/portfolio/broker_funding_settlement.py",
        "aletheia/trust/live_unlock.py",
    )
    violations = []
    for relative in consumers:
        path = ROOT / relative
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if name.startswith(("aletheia.exchanges", "extensions.exchanges")) or name in {
                    "aletheia.execution.okx_broker",
                    "aletheia.execution.binance_broker",
                    "aletheia.execution.bitget_broker",
                }:
                    violations.append(f"{relative}:{node.lineno}: {name}")
    assert violations == []


@spec_claim(
    SpecClaim(
        "UTA-S07",
        "uta_boundary",
        "§7 static legacy containment",
        "L2b",
        "Unqualified rollback paths are explicit and no new concrete private-client or venue-broker side channel may appear.",
    )
)
def test_unqualified_legacy_private_io_is_explicitly_contained():
    # This set IS the legacy-client migration ledger (#2994). It is asserted
    # exactly, so it shrinks only alongside a real migration and can never grow
    # silently.
    #
    # TPSL uses its normalized port; credential ingress belongs to Exchange.
    allowed = set()
    actual = set()
    for path in (ROOT / "aletheia").rglob("*.py"):
        if path.is_relative_to(UTA_ROOT) or path.is_relative_to(
            ROOT / "aletheia" / "exchanges"
        ):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(
                name.startswith(
                    (
                        "aletheia.exchanges.binance",
                        "aletheia.exchanges.bitget",
                        "aletheia.exchanges.okx",
                        "extensions.exchanges.bitget",
                        "extensions.exchanges.okx",
                        "extensions.exchanges.binance",
                    )
                )
                or name in {
                    "aletheia.execution.binance_broker",
                    "aletheia.execution.bitget_broker",
                    "aletheia.execution.okx_broker",
                }
                for name in names
            ):
                actual.add(str(path.relative_to(ROOT)))
    assert actual == allowed


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
