"""Spec: Persistence / Recovery Contract (persistence_recovery_contract.md)

A code-ahead-of-wiring domain: the persistence PRIMITIVES (event/artifact stores,
deterministic client_order_id, the recovery state machine class) are implemented, but
the recovery FSM is NOT yet wired into gateway startup. These claims lock the implemented
surface AND document the wiring gap honestly (PR-C01 is a self-correcting tripwire).

Contract: persistence_recovery_contract
Claim prefix: PR
"""

import ast
from pathlib import Path

from tests.specs.spec_meta import SpecClaim, spec_claim

_ROOT = Path(__file__).parents[2]


# ---------------------------------------------------------------------------
# L2a — Structural: the primitives exist
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PR-S01", "persistence_recovery_contract", "§4 recovery FSM", "L2a",
        "GatewayStatus enum carries the recovery states (STARTING/RECOVERY_MODE/RUNNING/"
        "SAFE_FREEZE) and GatewayStateMachine + FreezeBlockedError exist",
    ),
)
def test_gateway_state_machine_surface():
    from aletheia.gateway.state_machine import (
        GatewayStatus, GatewayStateMachine, FreezeBlockedError,
    )

    names = {s.name for s in GatewayStatus}
    for required in ("STARTING", "RECOVERY_MODE", "RUNNING", "SAFE_FREEZE"):
        assert required in names, f"GatewayStatus missing {required} (persistence §4)"
    assert isinstance(GatewayStateMachine, type)
    assert issubclass(FreezeBlockedError, Exception)


@spec_claim(
    SpecClaim(
        "PR-S02", "persistence_recovery_contract", "§3 idempotency", "L2a",
        "Deterministic order identity generators make_client_order_id + "
        "make_idempotency_key exist (§1.2 — orders must be idempotent across restart)",
    ),
)
def test_idempotency_generators_exist():
    from aletheia.gateway import client_order_id as coid

    assert callable(coid.make_client_order_id)
    assert callable(coid.make_idempotency_key)


@spec_claim(
    SpecClaim(
        "PR-S03", "persistence_recovery_contract", "§2 stores", "L2a",
        "Append-only EventStore and ArtifactStore are implemented with accessors "
        "(get_event_store / get_artifact_store) — the non-memory sources of truth",
    ),
)
def test_event_and_artifact_stores_exist():
    from aletheia.storage.event_store import EventStore, get_event_store
    from aletheia.storage.artifact_store import ArtifactStore, get_artifact_store

    assert callable(getattr(EventStore, "append", None))
    assert callable(get_event_store)
    assert isinstance(ArtifactStore, type)
    assert callable(get_artifact_store)


# ---------------------------------------------------------------------------
# L2b — Behavioral: idempotency key is actually used at the broker boundary
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PR-S04", "persistence_recovery_contract", "§3 idempotency wired", "L2b",
        "bitget broker submits orders with a deterministic client_oid (idempotency key "
        "reaches the exchange boundary so restart cannot double-submit)",
    ),
)
def test_bitget_broker_uses_client_oid():
    src = (_ROOT / "aletheia" / "execution" / "bitget_broker.py").read_text(encoding="utf-8")
    assert "client_oid=" in src, (
        "bitget_broker.py must pass client_oid for idempotent submission (persistence §3)"
    )


@spec_claim(
    SpecClaim(
        "PR-S05", "persistence_recovery_contract", "§5 import integrity", "L2b",
        "Import checksum verification is an authoritative pre-write gate: any reported "
        "integrity error returns before database or artifact processing",
    ),
)
def test_import_checksum_gate_precedes_all_mutation_paths():
    src = (_ROOT / "aletheia" / "sync" / "importer.py").read_text(encoding="utf-8")
    verify_pos = src.index("self._verify_checksums")
    gate_pos = src.index("if result.errors:", verify_pos)
    db_pos = src.index("self._import_db_tables", verify_pos)
    artifact_pos = src.index("self._import_artifacts", verify_pos)
    gate_block = src[gate_pos:db_pos]

    assert verify_pos < gate_pos < db_pos < artifact_pos
    assert "return result" in gate_block


@spec_claim(
    SpecClaim(
        "PR-S06", "persistence_recovery_contract", "§5 import table authority", "L2b",
        "JSONL import is limited to the explicit exporter table surface and preflights "
        "the whole bundle before database mutation",
    ),
)
def test_import_table_authority_is_explicit_and_precedes_mutation():
    from aletheia.sync.importer import _IMPORTABLE_TABLES

    expected = {
        "ps_execution_intents",
        "ps_fills",
        "ps_gateway_sessions",
        "ps_lessons",
        "ps_order_intents",
        "ps_orders",
        "ps_positions",
        "ps_skills",
        "serenity_claims",
        "serenity_evidence",
        "serenity_opportunities",
        "serenity_themes",
        "trade_proposals",
    }
    assert _IMPORTABLE_TABLES == expected
    assert {"settings", "risk_decisions", "knowledge_items", "audit_events"}.isdisjoint(
        _IMPORTABLE_TABLES
    )

    src = (_ROOT / "aletheia" / "sync" / "importer.py").read_text(encoding="utf-8")
    validation_pos = src.index("self._validate_db_payloads")
    gate_pos = src.index("if result.errors:", validation_pos)
    mutation_pos = src.index("self._import_db_tables", validation_pos)
    assert validation_pos < gate_pos < mutation_pos
    assert "return result" in src[gate_pos:mutation_pos]


# ---------------------------------------------------------------------------
# L2c — Honesty tripwire: recovery FSM is NOT wired into startup (documented gap)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PR-C01", "persistence_recovery_contract", "§6 wiring gap (tripwire)", "L2c",
        "Documented AS-BUILT gap: gateway/lifecycle.py does NOT yet drive the recovery "
        "FSM (no RECOVERY_MODE / GatewayRecoveryService reference). When this is wired, "
        "this test FAILS — that is the signal to flip persistence §1.3/§1.4/§6 to "
        "'enforced' and re-scope as a live prerequisite.",
        status="implemented",
    ),
)
def test_recovery_fsm_not_yet_wired_into_lifecycle():
    """Self-correcting gap marker. Passing = gap still open (gateway does not enter
    recovery on startup). Failing = someone wired it → update the contract."""
    lifecycle = _ROOT / "aletheia" / "gateway" / "lifecycle.py"
    if not lifecycle.exists():
        return  # lifecycle moved — re-confirm wiring manually
    text = lifecycle.read_text(encoding="utf-8")
    wired_markers = ("RECOVERY_MODE", "GatewayRecoveryService", "GatewayStateMachine")
    found = [m for m in wired_markers if m in text]
    assert not found, (
        f"gateway/lifecycle.py now references {found} — the recovery FSM appears WIRED. "
        "Update persistence_recovery_contract §1.3/§1.4/§4/§6 to 'enforced' and remove "
        "this tripwire (the safety net is now active)."
    )
