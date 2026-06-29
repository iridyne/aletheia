"""Spec: TS operator-shell <-> Python operator HTTP contract parity (2026-06-28).

TS schemas.ts interfaces and Python Pydantic models are hand-maintained (no codegen).
CONTRACT (request direction): every field a TS request payload declares MUST exist on
the Python model, else silently dropped on the wire. Asserts TS subset-of Python. L2a.
String-scanning parser (no regex) to stay robust across tooling.
"""
from __future__ import annotations

import ast
import pathlib

from tests.specs.spec_meta import SpecClaim, spec_claim


def _root():
    return pathlib.Path(__file__).resolve().parents[2]


_PAIRS = [
    ("OperatorHeartbeat", "OperatorHeartbeatIn"),
    ("OperatorTaskCreate", "OperatorTaskCreate"),
    ("OperatorTaskUpdate", "OperatorTaskUpdate"),
    ("OperatorMessageCreate", "OperatorMessageCreate"),
    ("OperatorSessionCreate", "OperatorSessionCreate"),
    ("OperatorEventCreate", "OperatorEventCreate"),
    ("OperatorArtifactCreate", "OperatorArtifactCreate"),
    ("OperatorContextQuery", "OperatorContextQueryRequest"),
]


def _ident(s):
    return bool(s) and all(c.isalnum() or c == "_" for c in s)


def _ts(src, name):
    marker = "export interface " + name
    i = src.find(marker)
    if i < 0:
        return None
    after = src[i + len(marker):i + len(marker) + 1]
    if after and (after.isalnum() or after == "_"):
        return None
    brace = src.find("{", i)
    end = src.find("
}", brace)
    if brace < 0 or end < 0:
        return None
    fields = set()
    for ln in src[brace + 1:end].splitlines():
        ln = ln.strip()
        if not ln or ln.startswith(("//", "*", "/*")):
            continue
        if ":" in ln:
            key = ln.split(":")[0].strip().rstrip("?")
            if _ident(key):
                fields.add(key)
    return fields


def _py(tree, name):
    cls = next((n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == name), None)
    if cls is None:
        return None
    fields = set()
    for n in cls.body:
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            fields.add(n.target.id)
    for b in cls.bases:
        if isinstance(b, ast.Name) and b.id != "BaseModel":
            inh = _py(tree, b.id)
            if inh:
                fields |= inh
    return fields


@spec_claim(SpecClaim("PI-S05", "operator_layer", "TS<->Python contract parity", "L2a",
    "every field a TS operator-shell request payload declares exists on its Python Pydantic model"))
def test_ts_request_fields_subset_of_python():
    r = _root()
    ts = (r / "operator-shell" / "src" / "schemas.ts").read_text(encoding="utf-8")
    py = (r / "aletheia" / "server" / "routes" / "operator.py").read_text(encoding="utf-8")
    tree = ast.parse(py)
    probs = []
    for tn, pn in _PAIRS:
        tf = _ts(ts, tn)
        pf = _py(tree, pn)
        if tf is None:
            probs.append("TS interface " + tn + " missing")
            continue
        if pf is None:
            probs.append("Python model " + pn + " missing")
            continue
        extra = tf - pf
        if extra:
            probs.append(tn + "->" + pn + ": TS extra " + str(sorted(extra)) + " not on Python")
    assert not probs, "operator TS<->Python contract drift: " + "; ".join(probs)
