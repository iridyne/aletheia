"""Spec: current Persistence Contract and dormant recovery boundary.

The current Contract covers durable stores and deterministic order identity.
Recovery components exist but are not wired into Gateway startup; PR-C01 keeps
that absence explicit while the future design lives in the Gateway Recovery
Proposal.

Contract: persistence_recovery_contract
Claim prefix: PR
"""

import ast
import asyncio
import sqlite3
from pathlib import Path

from tests.specs.spec_meta import SpecClaim, spec_claim

_ROOT = Path(__file__).parents[2]


# ---------------------------------------------------------------------------
# L2a — Structural: the primitives exist
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PR-S01", "persistence_recovery_contract",
        "Failure semantics — dormant recovery component", "L2a",
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
        "PR-S02", "persistence_recovery_contract",
        "Invariants — deterministic order identity", "L2a",
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
        "PR-S03", "persistence_recovery_contract",
        "Durable state and consumers", "L2a",
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
        "PR-S04", "persistence_recovery_contract",
        "Entry points — deterministic adapter identity", "L2b",
        "bitget broker submits orders with a deterministic client_oid so the "
        "identity reaches the supported adapter boundary",
    ),
)
def test_bitget_broker_uses_client_oid():
    src = (_ROOT / "aletheia" / "execution" / "bitget_broker.py").read_text(encoding="utf-8")
    assert "client_oid=" in src, (
        "bitget_broker.py must pass client_oid for idempotent submission (persistence §3)"
    )


# ---------------------------------------------------------------------------
# L2c — Honesty tripwire: recovery FSM is NOT wired into startup (documented gap)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PR-C01", "persistence_recovery_contract",
        "Failure semantics — recovery is not a current guarantee", "L2c",
        "Current boundary: gateway/lifecycle.py does NOT yet drive the recovery "
        "FSM (no RECOVERY_MODE / GatewayRecoveryService reference). When this is wired, "
        "this test FAILS — that is the signal to verify the Proposal and promote "
        "proved behavior into the current Contract.",
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
        "Verify gateway_recovery.md, add executable recovery acceptance, and update "
        "the current persistence Contract before claiming the safety net is active."
    )


def test_gateway_status_does_not_invent_dormant_recovery_state():
    from aletheia.server.routes.persistence import get_gateway_status

    assert asyncio.run(get_gateway_status()) == {
        "status": "not_available",
        "freeze_reason": None,
    }


# ---------------------------------------------------------------------------
# PR-S05 / PR-S06 — EventStore idempotency correctness (#247)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PR-S05", "persistence_recovery_contract",
        "Durable state — EventStore single append", "L2b",
        "Repeating append() with the same idempotency_key must: return the original "
        "persisted event_id (not a new phantom), leave exactly one SQLite row, and write "
        "exactly one JSONL line.  Non-idempotency IntegrityError must propagate (#247).",
        status="implemented",
    ),
)
def test_event_store_idempotency_single_append(tmp_path, monkeypatch):
    """#247: duplicate idempotency_key → return existing event, one DB row, one JSONL line."""
    from tests.test_entry_logic import setup_env
    setup_env(tmp_path, monkeypatch)

    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations

    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "ev_idem.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    sq._db_conn = conn
    run_migrations()

    monkeypatch.setattr(
        "aletheia.storage.event_store.get_data_dir", lambda: tmp_path
    )

    from aletheia.storage.event_store import EventStore, make_event

    store = EventStore()
    ev = make_event(
        "test_event", "order", "order-1",
        payload={"x": 1},
        idempotency_key="idem-key-1",
    )

    first = store.append(ev)
    second = store.append(
        make_event("test_event", "order", "order-1",
                   payload={"x": 99},
                   idempotency_key="idem-key-1")
    )

    assert first.event_id == second.event_id, (
        "both calls must return the same event_id on idempotency conflict"
    )
    rows = conn.execute(
        "SELECT COUNT(*) FROM ps_event_log WHERE idempotency_key='idem-key-1'"
    ).fetchone()[0]
    assert rows == 1, f"expected 1 DB row, got {rows}"

    jsonl_files = list((tmp_path / "events").glob("event_log_*.jsonl"))
    assert jsonl_files, "no JSONL file written"
    lines = [ln for f in jsonl_files for ln in f.read_text().splitlines() if ln.strip()]
    idem_lines = [ln for ln in lines if "idem-key-1" in ln]
    assert len(idem_lines) == 1, (
        f"expected exactly 1 JSONL line for idempotency_key, got {len(idem_lines)}"
    )


@spec_claim(
    SpecClaim(
        "PR-S06", "persistence_recovery_contract",
        "Durable state — EventStore batch append and error visibility", "L2b",
        "append_many() with duplicate idempotency_keys must return existing events and "
        "write JSONL only for newly inserted rows.  A PK clash without an idempotency_key "
        "must raise IntegrityError and must not write a JSONL mirror entry (#247).",
        status="implemented",
    ),
)
def test_event_store_idempotency_batch_and_pk_error(tmp_path, monkeypatch):
    """#247: append_many dedup + raw PK clash propagates IntegrityError without JSONL."""
    from tests.test_entry_logic import setup_env
    setup_env(tmp_path, monkeypatch)

    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations

    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "ev_batch.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    sq._db_conn = conn
    run_migrations()

    monkeypatch.setattr(
        "aletheia.storage.event_store.get_data_dir", lambda: tmp_path
    )

    from aletheia.storage.event_store import EventStore, make_event
    from aletheia.storage.models import new_id

    store = EventStore()

    # -- Batch idempotency: first insert ev_a, then batch with ev_a (dup) + ev_b (new) --
    ev_a = make_event("t", "agg", "a1", idempotency_key="batch-idem-a")
    first_a = store.append(ev_a)

    ev_b = make_event("t", "agg", "b1", idempotency_key="batch-idem-b")
    results = store.append_many([
        make_event("t", "agg", "a1", idempotency_key="batch-idem-a"),
        ev_b,
    ])
    assert results[0].event_id == first_a.event_id, (
        "batch result[0] must resolve to the originally inserted event_id"
    )
    assert results[1].event_id == ev_b.event_id, (
        "batch result[1] must be the newly inserted event"
    )
    count_a = conn.execute(
        "SELECT COUNT(*) FROM ps_event_log WHERE idempotency_key='batch-idem-a'"
    ).fetchone()[0]
    assert count_a == 1, f"expected 1 row for batch-idem-a, got {count_a}"

    jsonl_files = list((tmp_path / "events").glob("event_log_*.jsonl"))
    lines = [ln for f in jsonl_files for ln in f.read_text().splitlines() if ln.strip()]
    a_lines = [ln for ln in lines if "batch-idem-a" in ln]
    b_lines = [ln for ln in lines if "batch-idem-b" in ln]
    assert len(a_lines) == 1, f"expected 1 JSONL line for batch-idem-a, got {len(a_lines)}"
    assert len(b_lines) == 1, f"expected 1 JSONL line for batch-idem-b, got {len(b_lines)}"

    # -- PK clash without idempotency_key must raise IntegrityError, no JSONL written --
    fixed_id = new_id()
    pk_ev = make_event("t", "agg", "pk1")
    pk_ev.event_id = fixed_id
    store.append(pk_ev)
    lines_before = [ln for f in jsonl_files for ln in f.read_text().splitlines() if ln.strip()]

    pk_ev2 = make_event("t", "agg", "pk1")
    pk_ev2.event_id = fixed_id
    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        store.append(pk_ev2)

    lines_after = [ln for f in jsonl_files for ln in f.read_text().splitlines() if ln.strip()]
    assert len(lines_after) == len(lines_before), (
        "IntegrityError on PK clash must not write an extra JSONL line"
    )
