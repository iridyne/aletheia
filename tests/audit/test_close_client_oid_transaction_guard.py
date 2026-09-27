"""AST guard: coordinator._claim_close's next_close_client_oid call MUST be
inside a `with transaction(immediate=True):` writer lock.

Fedora runs a single-process uvicorn today; the helper's read-then-compute is
safe only under the immediate-transaction writer lock. A future refactor that
moves the call out of that lock re-introduces a race window; this guard fails
loudly the moment that happens rather than waiting for a multi-worker
deployment to expose it silently.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COORDINATOR = ROOT / "core" / "aletheia" / "execution" / "coordinator.py"
FUNCTION_NAME = "_claim_close"
GUARDED_CALL = "next_close_client_oid"


def _call_name(call: ast.Call) -> str | None:
    """Resolve the called name for a bare ``name(...)`` or ``obj.name(...)``."""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _is_immediate_transaction(context_expr: ast.expr) -> bool:
    """True for ``transaction(immediate=True)`` / ``sqlite.transaction(immediate=True)``.

    Only the function name and the ``immediate=True`` keyword matter; the
    receiver (bare name, module, or bound db handle) is intentionally ignored.
    """
    if not isinstance(context_expr, ast.Call):
        return False
    if _call_name(context_expr) != "transaction":
        return False
    return any(
        keyword.arg == "immediate"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value is True
        for keyword in context_expr.keywords
    )


def _parent_map(root: ast.AST) -> dict[ast.AST, ast.AST]:
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(root):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return parents


def _enclosing_with(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> ast.With | None:
    current = parents.get(node)
    while current is not None:
        if isinstance(current, ast.With):
            return current
        current = parents.get(current)
    return None


def _claim_close(tree: ast.Module) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == FUNCTION_NAME
        ):
            return node
    raise AssertionError(f"{FUNCTION_NAME} not found in {COORDINATOR}")


def _unguarded_calls(tree: ast.Module) -> list[ast.Call]:
    """Every guarded-allocation call in _claim_close not under an immediate txn."""
    function = _claim_close(tree)
    parents = _parent_map(function)
    offenders: list[ast.Call] = []
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name is None or not name.endswith(GUARDED_CALL):
            continue
        with_node = _enclosing_with(node, parents)
        if with_node is None or not any(
            _is_immediate_transaction(item.context_expr) for item in with_node.items
        ):
            offenders.append(node)
    return offenders


def test_claim_close_allocates_client_oid_inside_immediate_transaction() -> None:
    tree = ast.parse(COORDINATOR.read_text(encoding="utf-8"), filename=str(COORDINATOR))
    offenders = _unguarded_calls(tree)
    assert not offenders, (
        "coordinator._claim_close must call next_close_client_oid only inside a "
        "`with transaction(immediate=True):` writer lock; offending call(s) at "
        + ", ".join(f"line {node.lineno}" for node in offenders)
    )


def test_guard_detects_a_call_moved_outside_the_writer_lock() -> None:
    """Self-check: the AST rule actually flags an unwrapped allocation."""
    source = (
        "def _claim_close(position_id, reason, close_reasoning):\n"
        "    client_order_id = next_close_client_oid(position_id)\n"
        "    with transaction(immediate=True):\n"
        "        insert_order(client_order_id)\n"
    )
    offenders = _unguarded_calls(ast.parse(source))
    assert [node.lineno for node in offenders] == [2]
