"""AST guard: reconciliation handlers that wrap a UTA projection read MUST log
the exception's ``reason_code`` alongside ``type(exc).__name__``.

Invariant (``INV-EX-1``, ``docs/design/contracts/execution_contract.md``): a UTA
read that cannot prove ``ReadCompleteness.COMPLETE`` is raised as
``IncompleteReadError`` (``core/aletheia/execution/errors.py``), never a bare
``RuntimeError``, and every ``logger.warning`` inside an ``except … as exc:``
handler that wraps such a read MUST include that exception's ``reason_code``.
The bare form reduced the entire diagnostic payload to the literal string
``"RuntimeError"`` in ``errors.log``, so the venue-reported reason never reached
operators.

Scope narrowing: this guard inspects only ``except Exception`` handlers whose
``try`` body directly reads a projection boundary — the six
``_UTAReconciliationProjection`` read methods or the two multibroker evidence
helpers — not every handler in the module. Materialization handlers catch
``MaterializationIntegrityError`` / ``ValueError`` around local-only work and
never wrap a projection read; including them would flag unrelated code and make
the guard noisy without protecting the invariant.

Why body descent stops at a nested ``ast.Try``: a handler's ``try`` body can
itself contain an inner ``try`` (for example a ``transition_order`` recovery
block). A nested ``Try`` owns its own handlers, so a call inside it is not
guarded by the outer handler; descending into it would misattribute the site.
The body is therefore walked by hand, and child ``Try`` nodes are not entered.

Why the anti-vacuity test exists: an AST guard that discovers zero call sites
passes trivially and protects nothing.
``test_guard_covers_every_projection_read_site`` pins the discovered in-scope
handler count so a refactor that renames or deletes the projection methods —
silently disarming the guard — fails immediately instead of going green.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RECONCILIATION = ROOT / "core" / "aletheia" / "execution" / "reconciliation.py"

PROJECTION_METHODS = {
    "get_open_orders",
    "get_fills",
    "get_order_fills",
    "get_order_status",
    "get_order_detail_by_client_oid",
    "get_funding_ledger",
}
PROJECTION_HELPERS = {"_fetch_multibroker_evidence", "_fetch_multibroker_funding"}

EXPECTED_IN_SCOPE_HANDLERS = 10


def _is_projection_call(node: ast.AST) -> bool:
    """True when ``node`` reads a projection boundary.

    ``client.get_fills(...)`` resolves as an ``Attribute`` with
    ``.attr ∈ PROJECTION_METHODS``; ``_fetch_multibroker_evidence(...)``
    resolves as a ``Name`` with ``.id ∈ PROJECTION_HELPERS``.
    """
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr in PROJECTION_METHODS
    if isinstance(func, ast.Name):
        return func.id in PROJECTION_HELPERS
    return False


def _body_calls(body: list[ast.stmt]) -> list[ast.Call]:
    """Calls reachable in a ``try`` body without descending into nested ``Try``.

    Hand-rolled walk (not ``ast.walk``) so a child ``Try`` can be skipped: it
    owns its own handlers and must not be attributed to this one.
    """
    calls: list[ast.Call] = []
    stack: list[ast.AST] = list(body)
    while stack:
        node = stack.pop()
        if isinstance(node, ast.Try):
            continue
        if isinstance(node, ast.Call):
            calls.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return calls


def _wraps_projection_read(node: ast.Try) -> bool:
    return any(_is_projection_call(call) for call in _body_calls(node.body))


def _is_exception_handler(handler: ast.ExceptHandler) -> bool:
    return isinstance(handler.type, ast.Name) and handler.type.id == "Exception"


def _warning_has_reason_code(call: ast.Call) -> bool:
    """True when a ``logger.warning`` call references an exception ``reason_code``.

    Accepts either ``<name>.reason_code`` (an ``Attribute``) or
    ``getattr(<name>, "reason_code", …)`` (a ``Call``).
    """
    values = [*call.args, *(kw.value for kw in call.keywords)]
    for value in values:
        if isinstance(value, ast.Attribute) and value.attr == "reason_code":
            return True
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "getattr"
            and len(value.args) >= 2
            and isinstance(value.args[1], ast.Constant)
            and value.args[1].value == "reason_code"
        ):
            return True
    return False


def _warning_calls(handler: ast.ExceptHandler) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(handler)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "warning"
    ]


def _logs_reason_code(handler: ast.ExceptHandler) -> bool:
    """True only when every ``logger.warning`` in the handler logs a reason code.

    A handler with no warning at all is treated as non-compliant: a bare
    ``except Exception:`` (no ``as name``) cannot carry the reason code, so it
    must surface as an offender rather than pass silently.
    """
    warnings = _warning_calls(handler)
    if not warnings:
        return False
    return all(_warning_has_reason_code(call) for call in warnings)


def _in_scope_handlers(tree: ast.Module) -> list[ast.ExceptHandler]:
    handlers: list[ast.ExceptHandler] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        if not _wraps_projection_read(node):
            continue
        handlers.extend(
            handler for handler in node.handlers if _is_exception_handler(handler)
        )
    return handlers


def _offending_handlers(tree: ast.Module) -> list[tuple[int, str]]:
    """In-scope handlers whose warning omits the exception's ``reason_code``."""
    offenders: list[tuple[int, str]] = []
    for handler in _in_scope_handlers(tree):
        if not _logs_reason_code(handler):
            offenders.append((handler.lineno, "Exception"))
    return offenders


def _parse_reconciliation() -> ast.Module:
    source = RECONCILIATION.read_text(encoding="utf-8")
    return ast.parse(source, filename=str(RECONCILIATION))


def test_projection_read_warnings_log_the_reason_code() -> None:
    offenders = _offending_handlers(_parse_reconciliation())
    assert not offenders, (
        "every except Exception handler wrapping a UTA projection read must log "
        "the exception's reason_code alongside type(exc).__name__; offending "
        "handler(s) at "
        + ", ".join(f"line {lineno}" for lineno, _ in offenders)
    )


def test_guard_covers_every_projection_read_site() -> None:
    """Anti-vacuity: the guard must discover the real projection-read handlers."""
    handlers = _in_scope_handlers(_parse_reconciliation())
    assert len(handlers) == EXPECTED_IN_SCOPE_HANDLERS, (
        "expected "
        f"{EXPECTED_IN_SCOPE_HANDLERS} except-Exception handlers wrapping a "
        f"projection read, found {len(handlers)}: "
        + ", ".join(f"line {handler.lineno}" for handler in handlers)
    )


def test_guard_rejects_a_warning_without_the_reason_code() -> None:
    source = (
        "def positive_missing():\n"  # line 1
        "    try:\n"  # line 2
        "        client.get_fills()\n"  # line 3
        "    except Exception as exc:\n"  # line 4
        "        logger.warning('failed for %s', type(exc).__name__)\n"
        "def negative_attribute():\n"
        "    try:\n"
        "        client.get_open_orders()\n"
        "    except Exception as exc:\n"
        "        logger.warning('failed for %s (%s: %s)',"
        " type(exc).__name__, exc.reason_code)\n"
        "def negative_getattr():\n"
        "    try:\n"
        "        _fetch_multibroker_evidence()\n"
        "    except Exception as exc:\n"
        "        logger.warning('failed for %s (%s: %s)', type(exc).__name__,"
        " getattr(exc, 'reason_code', 'no_reason_code'))\n"
        "def out_of_scope():\n"
        "    try:\n"
        "        materialize_bitget_entry()\n"
        "    except Exception as exc:\n"
        "        logger.warning('failed for %s', type(exc).__name__)\n"
    )
    tree = ast.parse(source)
    offenders = _offending_handlers(tree)
    assert [lineno for lineno, _ in offenders] == [4]
