"""Spec: Audit Chain Contract (audit_chain_contract.md)

Hash-chain append-only JSONL audit log. Trust-boundary subsystem.
Claims: AU-S01 .. AU-S05.
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
