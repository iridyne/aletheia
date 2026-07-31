"""Spec: Audit Chain Contract (audit_chain_contract.md)

Hash-chain append-only JSONL audit log. Trust-boundary subsystem.
Claims: AU-S01 .. AU-S06.
"""

import ast
import hashlib
import json
from pathlib import Path

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent


def _reset_audit_hash_cache():
    """Reset the in-process hash cache so each test starts clean."""
    import aletheia.files.jsonl as _m
    _m._last_audit_hash = None


@pytest.fixture()
def audit_tmp(tmp_path, monkeypatch):
    """Redirect write_audit / _log_dir to a fresh tmp directory."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    import aletheia.files.jsonl as _m
    monkeypatch.setattr(_m, "_log_dir", lambda: log_dir)
    _reset_audit_hash_cache()
    yield log_dir
    _reset_audit_hash_cache()


# ---------------------------------------------------------------------------
# AU-S01  L2a — append-only file open mode (AST scan)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "AU-S01", "audit_chain_contract", "§3 append-only", "L2a",
        "write_audit opens audit.jsonl only with mode='a' — never 'w' (AST scan jsonl.py)",
    ),
)
def test_write_audit_opens_append_only():
    """AST-scan jsonl.py: every open() call uses mode 'a', never 'w'."""
    src = _REPO_ROOT / "aletheia" / "files" / "jsonl.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))

    truncating_opens: list[str] = []
    for node in ast.walk(tree):
        # Catch both open(...) and Path.open(...)
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Identify open() or .open() calls
        is_open = (
            (isinstance(func, ast.Name) and func.id == "open")
            or (isinstance(func, ast.Attribute) and func.attr == "open")
        )
        if not is_open:
            continue
        # Collect the mode argument: positional arg[1] or keyword "mode"
        mode_value: str | None = None
        if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
            mode_value = node.args[1].value
        else:
            for kw in node.keywords:
                if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                    mode_value = kw.value.value
                    break

        if mode_value is not None and "w" in str(mode_value) and "a" not in str(mode_value):
            truncating_opens.append(
                f"line {node.lineno}: open(mode={mode_value!r})"
            )

    assert truncating_opens == [], (
        "jsonl.py contains open() calls with truncating mode — "
        "this would destroy the audit log chain:\n"
        + "\n".join(truncating_opens)
    )


# ---------------------------------------------------------------------------
# AU-S02  L2b — _prev_hash chain integrity (2-record write)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "AU-S02", "audit_chain_contract", "§3 hash chain", "L2b",
        "record[1]._prev_hash == sha256(raw_line_0); 64-char hex",
    ),
)
def test_hash_chain_two_records(audit_tmp):
    """Write 2 records, verify line[1]._prev_hash == sha256(line[0] as stored)."""
    from aletheia.files.jsonl import write_audit

    write_audit({"event_type": "test_event_1", "detail": "alpha"})
    write_audit({"event_type": "test_event_2", "detail": "beta"})

    audit_path = audit_tmp / "audit.jsonl"
    assert audit_path.exists(), "audit.jsonl was not created"

    lines = [l.strip() for l in audit_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 2, f"Expected 2 records, got {len(lines)}"

    raw_line0 = lines[0]
    rec1 = json.loads(lines[1])

    prev_hash = rec1.get("_prev_hash")
    assert prev_hash is not None, "record[1] missing _prev_hash"
    assert len(prev_hash) == 64, f"_prev_hash must be 64 hex chars, got {len(prev_hash)}"
    assert all(c in "0123456789abcdef" for c in prev_hash.lower()), (
        f"_prev_hash is not valid lowercase hex: {prev_hash}"
    )

    expected = hashlib.sha256(raw_line0.encode("utf-8")).hexdigest()
    assert prev_hash == expected, (
        f"Hash chain broken: record[1]._prev_hash={prev_hash!r} "
        f"but sha256(line[0])={expected!r}"
    )


# ---------------------------------------------------------------------------
# AU-S03  L2b — verify_audit_chain on a valid 5-record file
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "AU-S03", "audit_chain_contract", "§4 verify", "L2b",
        "verify_audit_chain() on 5 valid records returns valid=True, errors empty",
    ),
)
def test_verify_audit_chain_valid(audit_tmp):
    """Write 5 records then call verify_audit_chain(); expect valid=True, breaks==0."""
    from aletheia.files.jsonl import verify_audit_chain, write_audit

    for i in range(5):
        write_audit({"event_type": "test_seq", "seq": i})

    result = verify_audit_chain()

    assert result.get("valid") is True, (
        f"verify_audit_chain() reported invalid after clean writes: {result}"
    )
    errors = result.get("errors", [])
    assert errors == [], f"Unexpected errors from verify_audit_chain(): {errors}"
    assert result.get("records", 0) == 5, (
        f"Expected 5 records, got {result.get('records')}"
    )


# ---------------------------------------------------------------------------
# AU-S04  L2b — guard: append_jsonl("audit.jsonl", …) raises ValueError
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "AU-S04", "audit_chain_contract", "§8 guard", "L2b",
        "append_jsonl('audit.jsonl', {...}) raises ValueError — direct bypass forbidden",
        status="implemented",
    ),
)
def test_direct_append_audit_jsonl_raises(audit_tmp):
    """Calling append_jsonl('audit.jsonl', …) must raise ValueError."""
    from aletheia.files.jsonl import append_jsonl

    with pytest.raises(ValueError, match="write_audit"):
        append_jsonl("audit.jsonl", {"event_type": "bypass_attempt"})


# ---------------------------------------------------------------------------
# AU-S05  L2c — trust-boundary route handlers call write_audit (AST scan)
# ---------------------------------------------------------------------------

_TRUST_BOUNDARY_ROUTES = [
    ("routes/live.py", ["live_unlock", "live_lock"]),
    ("routes/risk.py", ["set_kill_switch"]),
]

# Modules that are known to call write_audit internally on behalf of a handler.
# These are acceptable delegation paths for AU-S05.
_AUDIT_DELEGATION_MODULES = {
    "kill_switch",          # aletheia.risk.kill_switch.activate/deactivate
}


def _handler_calls_write_audit_or_delegates(func_node: ast.FunctionDef) -> bool:
    """Return True if the handler directly calls write_audit() or delegates
    to a known audit-delegating module (e.g. kill_switch.activate)."""
    for node in ast.walk(func_node):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Direct call: write_audit(...)
        if isinstance(func, ast.Name) and func.id == "write_audit":
            return True
        # Attribute call: obj.write_audit(...)
        if isinstance(func, ast.Attribute) and func.attr == "write_audit":
            return True
        # Delegation: kill_switch.activate(...) / kill_switch.deactivate(...)
        if isinstance(func, ast.Attribute):
            obj = func.value
            if isinstance(obj, ast.Name) and obj.id in _AUDIT_DELEGATION_MODULES:
                return True
    return False


@spec_claim(
    SpecClaim(
        "AU-S05", "audit_chain_contract", "§6 invariant", "L2c",
        "Every trust-boundary route handler calls write_audit (directly or via delegation)",
    ),
)
def test_trust_boundary_handlers_audit():
    """AST-scan routes/live.py and routes/risk.py:
    each listed handler must call write_audit or a known audit-delegating function.
    """
    routes_root = _REPO_ROOT / "aletheia" / "server"
    missing_audit: list[str] = []

    for rel_path, handler_names in _TRUST_BOUNDARY_ROUTES:
        route_file = routes_root / rel_path
        if not route_file.exists():
            pytest.skip(f"Trust-boundary route file not found: {rel_path}")

        tree = ast.parse(route_file.read_text(encoding="utf-8"))
        func_map = {
            n.name: n
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

        for handler in handler_names:
            if handler not in func_map:
                missing_audit.append(f"{rel_path}::{handler} — function not found in AST")
                continue
            if not _handler_calls_write_audit_or_delegates(func_map[handler]):
                missing_audit.append(
                    f"{rel_path}::{handler} — does not call write_audit or a "
                    "known audit-delegating module"
                )

    assert missing_audit == [], (
        "Trust-boundary handlers without audit trail:\n"
        + "\n".join(f"  - {m}" for m in missing_audit)
    )


# ---------------------------------------------------------------------------
# AU-S06  L2c — transactional authority may suppress only the SQLite mirror
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "AU-S06", "audit_chain_contract", "§3 transactional mirror", "L2c",
        "mirror_db=False keeps the append-only hash-chain write but does not "
        "duplicate a SQLite AuditEvent already committed by the caller",
        status="implemented",
    ),
)
def test_transactional_authority_can_skip_only_sqlite_mirror(
    audit_tmp, monkeypatch
):
    import aletheia.files.jsonl as jsonl_mod

    mirrored: list[str] = []
    monkeypatch.setattr(
        jsonl_mod,
        "_write_audit_row",
        lambda event: mirrored.append(str(event.get("event_type"))),
    )

    jsonl_mod.write_audit(
        {"event_type": "already_committed", "detail": "transactional"},
        mirror_db=False,
    )
    jsonl_mod.write_audit(
        {"event_type": "default_mirror", "detail": "ordinary"}
    )

    records = [
        json.loads(line)
        for line in (audit_tmp / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [row["event_type"] for row in records] == [
        "already_committed",
        "default_mirror",
    ]
    assert mirrored == ["default_mirror"]
    assert records[1]["_prev_hash"] == hashlib.sha256(
        json.dumps(records[0], default=str, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


@spec_claim(
    SpecClaim(
        "AU-C04",
        "audit_chain_contract",
        "§6.3 durable lifecycle audit mirror",
        "L2c",
        "A stable audit dedup id makes retries physically idempotent and the writer reports "
        "whether the JSONL append succeeded.",
        status="implemented",
    ),
)
def test_audit_writer_reports_failure_and_deduplicates_retry(
    audit_tmp,
    monkeypatch,
):
    import aletheia.files.jsonl as jsonl_mod

    event = {
        "id": "learning-lifecycle:event-1",
        "actor": "system",
        "source": "learning_lifecycle",
        "event_type": "learning_lifecycle_transition",
    }
    assert jsonl_mod.write_audit(
        dict(event), mirror_db=False, dedup_id=event["id"],
    ) is True
    assert jsonl_mod.write_audit(
        dict(event), mirror_db=False, dedup_id=event["id"],
    ) is True

    records = [
        json.loads(line)
        for line in (audit_tmp / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [row["id"] for row in records] == [event["id"]]

    monkeypatch.setattr(
        jsonl_mod,
        "_append_jsonl_line",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    assert jsonl_mod.write_audit(
        {**event, "id": "learning-lifecycle:event-2"},
        mirror_db=False,
        dedup_id="learning-lifecycle:event-2",
    ) is False


def test_audit_batch_scans_chain_once(audit_tmp, monkeypatch):
    import aletheia.files.jsonl as jsonl_mod

    scans = 0
    real_scan = jsonl_mod._audit_record_ids

    def tracked_scan(path, dedup_ids):
        nonlocal scans
        scans += 1
        return real_scan(path, dedup_ids)

    monkeypatch.setattr(jsonl_mod, "_audit_record_ids", tracked_scan)
    events = [
        {
            "id": f"learning-lifecycle:batch-{index}",
            "actor": "system",
            "source": "learning_lifecycle",
            "event_type": "learning_lifecycle_transition",
        }
        for index in range(3)
    ]

    assert jsonl_mod.write_audits_deduplicated(events) == {
        event["id"] for event in events
    }
    assert scans == 1


def test_audit_retry_repairs_partial_tail_by_appending_only(audit_tmp, monkeypatch):
    import aletheia.files.jsonl as jsonl_mod

    first = {
        "id": "learning-lifecycle:complete",
        "event_type": "learning_lifecycle_transition",
    }
    retry = {
        "id": "learning-lifecycle:retry",
        "event_type": "learning_lifecycle_transition",
        "message": "审计恢复",
    }
    assert jsonl_mod.write_audits_deduplicated([first]) == {first["id"]}
    real_append = jsonl_mod._append_jsonl_line

    def partial_append(path, line):
        encoded = line.encode("utf-8")
        split_at = encoded.index("审".encode("utf-8")) + 1
        with path.open("ab") as stream:
            stream.write(encoded[:split_at])
        raise OSError("disk full after partial write")

    monkeypatch.setattr(jsonl_mod, "_append_jsonl_line", partial_append)
    assert jsonl_mod.write_audits_deduplicated([retry]) == set()
    monkeypatch.setattr(jsonl_mod, "_append_jsonl_line", real_append)

    assert jsonl_mod.write_audits_deduplicated([retry]) == {retry["id"]}
    records = [
        json.loads(line)
        for line in (audit_tmp / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [record["id"] for record in records] == [first["id"], retry["id"]]
    assert records[1]["_prev_hash"] == hashlib.sha256(
        json.dumps(records[0], default=str, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def test_audit_batch_partial_retry_excludes_already_complete_candidate(
    audit_tmp, monkeypatch,
):
    import aletheia.files.jsonl as jsonl_mod

    events = [
        {
            "id": f"learning-lifecycle:batch-partial-{index}",
            "event_type": "learning_lifecycle_transition",
            "timestamp": "2026-07-31T00:00:00+00:00",
        }
        for index in range(2)
    ]
    real_append = jsonl_mod._append_jsonl_line
    writes = 0

    def fail_second_append(path, line):
        nonlocal writes
        writes += 1
        if writes == 1:
            real_append(path, line)
            return
        with path.open("ab") as stream:
            stream.write(line[:10].encode("utf-8"))
        raise OSError("crash during second event")

    monkeypatch.setattr(jsonl_mod, "_append_jsonl_line", fail_second_append)
    assert jsonl_mod.write_audits_deduplicated(events) == {events[0]["id"]}
    monkeypatch.setattr(jsonl_mod, "_append_jsonl_line", real_append)

    assert jsonl_mod.write_audits_deduplicated(events) == {
        event["id"] for event in events
    }
    records = [
        json.loads(line)
        for line in (audit_tmp / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [record["id"] for record in records] == [
        event["id"] for event in events
    ]


def test_audit_retry_preserves_complete_record_missing_final_newline(audit_tmp):
    import aletheia.files.jsonl as jsonl_mod

    event = {
        "id": "learning-lifecycle:complete-no-newline",
        "event_type": "learning_lifecycle_transition",
        "timestamp": "2026-07-31T00:00:00+00:00",
        "_prev_hash": "0" * 64,
    }
    encoded = json.dumps(event, default=str, ensure_ascii=False)
    (audit_tmp / "audit.jsonl").write_text(encoded, encoding="utf-8")

    assert jsonl_mod.write_audits_deduplicated([dict(event)]) == {event["id"]}
    assert (audit_tmp / "audit.jsonl").read_text(encoding="utf-8") == encoded + "\n"


def test_ordinary_audit_does_not_guess_identity_of_partial_tail(audit_tmp):
    import aletheia.files.jsonl as jsonl_mod

    partial = b'{"actor": "human", "source": "dashboard"'
    (audit_tmp / "audit.jsonl").write_bytes(partial)
    event = {
        "actor": "human",
        "source": "dashboard",
        "event_type": "different_event",
    }

    assert jsonl_mod.write_audit(event, mirror_db=False) is False
    assert (audit_tmp / "audit.jsonl").read_bytes() == partial


def test_audit_dedup_ignores_non_string_historical_id(audit_tmp):
    import aletheia.files.jsonl as jsonl_mod

    (audit_tmp / "audit.jsonl").write_text('{"id": []}\n', encoding="utf-8")
    event = {
        "id": "learning-lifecycle:valid",
        "event_type": "learning_lifecycle_transition",
    }

    assert jsonl_mod.write_audits_deduplicated([event]) == {event["id"]}
