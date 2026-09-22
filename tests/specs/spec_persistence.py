"""Spec: current Persistence Contract and retired recovery boundary.

The current Contract covers durable stores and deterministic order identity.
The legacy PS recovery producer is retired; historical tables/readers remain.
PR-S01 / PR-C01 prevent its write graph and startup authority from returning.

Contract: persistence_recovery_contract
Claim prefix: PR
"""

import ast
import asyncio
import re
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
        "Invariants — retired PS recovery graph", "L2a",
        "The retired PS Gateway writer, recovery, exchange, session and lock modules "
        "are absent; archive imports cannot recreate their state or Core trading authority.",
    ),
)
def test_legacy_gateway_write_graph_is_removed():
    from extensions.sync.importer import _IMPORT_ALLOWED_TABLES

    assert _IMPORT_ALLOWED_TABLES == {
        "serenity_themes", "serenity_evidence", "serenity_claims",
        "serenity_opportunities", "ps_lessons", "ps_skills",
    }
    retired = {"state_machine", "state_store", "recovery", "reconciliation", "session", "locks", "exchange_adapter"}
    assert not [name for name in retired if (_ROOT / "aletheia" / "gateway" / f"{name}.py").exists()]
    tables = {"ps_execution_intents", "ps_order_intents", "ps_orders", "ps_fills", "ps_positions",
              "ps_gateway_sessions", "ps_gateway_locks", "ps_reconciliation_reports"}
    writes = re.compile(r"\b(?:INSERT\s+(?:OR\s+\w+\s+)?INTO|UPDATE|DELETE\s+FROM)\s+(?:"
                        + "|".join(sorted(tables)) + r")\b", re.IGNORECASE)
    violations = []
    for path in (_ROOT / "aletheia").rglob("*.py"):
        relative = path.relative_to(_ROOT / "aletheia").as_posix()
        if relative == "storage/migrations.py":
            continue  # Historical schema migration is not runtime authority.
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and writes.search(node.value):
                violations.append(f"{relative}:{node.lineno}")
            if isinstance(node, ast.Call):
                name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                if name in {"_insert", "_update"} and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value in tables:
                    violations.append(f"{relative}:{node.lineno}")
    assert not violations, violations


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



# ---------------------------------------------------------------------------
# L2c — Legacy history cannot regain current startup authority
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PR-C01", "persistence_recovery_contract",
        "Invariants — Legacy is not startup authority", "L2c",
        "Startup and process lifecycle do not import the retired PS state owners or "
        "use historical PS tables as current admission evidence.",
        status="implemented",
    ),
)
def test_legacy_recovery_does_not_control_startup():
    for relative in ("gateway/lifecycle.py", "agent_runtime/startup_behavior_gate.py"):
        text = (_ROOT / "aletheia" / relative).read_text(encoding="utf-8")
        assert not any(marker in text for marker in (
            "GatewayRecoveryService", "GatewayStateMachine", "GatewayStateStore",
            "ps_order_intents", "ps_orders", "ps_positions",
        )), relative


def test_gateway_status_does_not_invent_dormant_recovery_state():
    from aletheia.server.routes.persistence import get_gateway_status

    assert get_gateway_status() == {
        "status": "not_available",
        "freeze_reason": None,
    }


def test_retired_gateway_history_remains_readable_without_lifecycle_writes():
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import PSGatewaySession, PSReconciliationReport
    from aletheia.server.routes.persistence import list_gateway_sessions, list_reconciliation_reports

    session = PSGatewaySession(gateway_id="historical", mode="paper", status="SAFE_FREEZE")
    report = PSReconciliationReport(gateway_session_id=session.gateway_session_id, status="frozen")
    repo._insert("ps_gateway_sessions", session.model_dump())
    repo._insert("ps_reconciliation_reports", report.model_dump())
    assert list_gateway_sessions(limit=10) == [session.model_dump()]
    assert list_reconciliation_reports(limit=10) == [report.model_dump()]
    assert repo.get_db().execute("SELECT count(*) FROM ps_orders").fetchone()[0] == 0
    assert repo.get_db().execute("SELECT count(*) FROM ps_fills").fetchone()[0] == 0
    assert repo.get_db().execute("SELECT count(*) FROM ps_positions").fetchone()[0] == 0


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
    from concurrent.futures import ThreadPoolExecutor

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
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                store.append,
                make_event(
                    "test_event",
                    "order",
                    "order-1",
                    payload={"x": value},
                    idempotency_key="idem-key-1",
                ),
            )
            for value in (1, 99)
        ]
        first, second = [future.result() for future in futures]

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


def test_event_store_reconciles_failed_jsonl_mirror(tmp_path, monkeypatch):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    monkeypatch.setattr("aletheia.storage.event_store.get_data_dir", lambda: tmp_path)

    from aletheia.storage.event_store import EventStore, make_event
    from aletheia.storage.sqlite import get_db

    store = EventStore()
    write_jsonl = store._write_jsonl
    monkeypatch.setattr(store, "_write_jsonl", lambda _event: (_ for _ in ()).throw(OSError()))
    event = store.append(make_event("mirror_test", "test", "one"))

    row = get_db().execute(
        "SELECT jsonl_mirror_status, jsonl_attempt_count, jsonl_next_attempt_at, "
        "jsonl_last_error_class FROM ps_event_log WHERE event_id=?",
        (event.event_id,),
    ).fetchone()
    assert tuple(row[:2]) == ("failed", 1)
    assert row[2] is not None
    assert row[3] == "OSError"

    get_db().execute(
        "UPDATE ps_event_log SET jsonl_next_attempt_at='2000-01-01T00:00:00+00:00' "
        "WHERE event_id=?",
        (event.event_id,),
    )
    get_db().commit()
    monkeypatch.setattr(store, "_write_jsonl", write_jsonl)
    # The mirror file is derived from the event's own created_at
    # (`event_store._jsonl_path`), so the corrupt lines must be seeded into THAT
    # month's file. A hardcoded month silently stopped exercising the claim once
    # the real clock moved past it: the corrupt bytes went to one file while
    # reconcile appended to another, and the glob then read whichever came first.
    from aletheia.storage.event_store import _jsonl_path

    path = _jsonl_path(event.created_at)
    assert path.parent == tmp_path / "events"
    path.parent.mkdir(parents=True, exist_ok=True)
    # An unparseable line must not defeat dedup on the following reconcile pass.
    path.write_bytes(b"null\n\xff\n")

    assert store.reconcile_jsonl() == {"attempted": 1, "mirrored": 1, "failed": 0}
    assert store.reconcile_jsonl() == {"attempted": 0, "mirrored": 0, "failed": 0}
    assert path.read_bytes().count(event.event_id.encode()) == 1
    # No sibling month file may also carry the event.
    assert [
        item.name for item in (tmp_path / "events").glob("event_log_*.jsonl")
    ] == [path.name]


def test_event_store_jsonl_file_lock_prevents_cross_process_duplicate(
    tmp_path, monkeypatch
):
    import json
    import os
    import subprocess
    import sys
    import time

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    monkeypatch.setattr("aletheia.storage.event_store.get_data_dir", lambda: tmp_path)

    from aletheia.files.jsonl import _exclusive_file_lock
    from aletheia.storage.event_store import (
        EventStore,
        _jsonl_lock_path,
        _jsonl_path,
        make_event,
    )

    store = EventStore()
    event = store._prepare(
        make_event(
            "mirror_race",
            "test",
            "same",
            payload={"source": "parent"},
            idempotency_key="mirror-race",
        )
    )
    path = _jsonl_path(event.created_at)
    path.parent.mkdir(parents=True, exist_ok=True)
    event_path = tmp_path / "event.json"
    marker_path = tmp_path / "child-ready"
    line = json.dumps(event.model_dump(), ensure_ascii=False)
    event_path.write_text(line, encoding="utf-8")

    child = (
        "import json, pathlib, sys\n"
        "from aletheia.storage.event_store import EventStore\n"
        "from aletheia.storage.models import PSEvent\n"
        "event_path = pathlib.Path(sys.argv[1])\n"
        "marker_path = pathlib.Path(sys.argv[2])\n"
        "event = PSEvent(**json.loads(event_path.read_text()))\n"
        "marker_path.write_text('ready')\n"
        "EventStore()._write_jsonl(event)\n"
    )

    env = os.environ.copy()
    env["ALETHEIA_DATA_DIR"] = str(tmp_path)
    with _exclusive_file_lock(_jsonl_lock_path(path)):
        proc = subprocess.Popen(
            [sys.executable, "-c", child, str(event_path), str(marker_path)],
            cwd=_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 5
        while not marker_path.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert marker_path.exists(), "child did not reach mirror attempt"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")

    stdout, stderr = proc.communicate(timeout=10)
    assert proc.returncode == 0, stdout + stderr
    records = [json.loads(raw) for raw in path.read_text().splitlines() if raw.strip()]
    assert [record["event_id"] for record in records].count(event.event_id) == 1


def test_event_store_jsonl_same_id_different_payload_stays_failed(
    tmp_path, monkeypatch
):
    import json

    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    monkeypatch.setattr("aletheia.storage.event_store.get_data_dir", lambda: tmp_path)

    from aletheia.storage.event_store import EventStore, _jsonl_path, make_event
    from aletheia.storage.sqlite import get_db

    store = EventStore()
    event = store._prepare(
        make_event("mirror_mismatch", "test", "same", payload={"version": 1})
    )
    persisted, inserted = store._write_db(event)
    assert inserted

    bad_record = persisted.model_dump()
    bad_record["payload_hash"] = "different"
    path = _jsonl_path(persisted.created_at)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(bad_record, ensure_ascii=False) + "\n", encoding="utf-8")

    assert store._attempt_jsonl_mirror(persisted) is False
    row = get_db().execute(
        "SELECT jsonl_mirror_status, jsonl_last_error_class "
        "FROM ps_event_log WHERE event_id=?",
        (persisted.event_id,),
    ).fetchone()
    assert tuple(row) == ("failed", "EventJsonlMirrorPayloadMismatch")
    records = [json.loads(raw) for raw in path.read_text().splitlines() if raw.strip()]
    assert [record["event_id"] for record in records].count(persisted.event_id) == 1


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
