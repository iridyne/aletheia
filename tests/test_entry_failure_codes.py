"""An Entry Plan's terminal reason is a countable code, never a sentence.

On the Fedora runtime 72 plans failed with one fixed sentence ("Executable venue
order rules are unavailable...") and 84 more with only an exception class, so the
question "below the minimum size, or symbol not listed, or rules unreadable?" had
no answer in the database.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from aletheia.execution import gateway
from aletheia.execution.order_constraints import ConstraintUnavailable, normalize_quantity

GATEWAY = Path(gateway.__file__)
CODE = re.compile(r"[a-z][a-z0-9_]{0,95}(:[a-z][a-z0-9_]{0,95})?")


def _reason_arguments(call: ast.Call) -> list[ast.expr]:
    name = call.func.id if isinstance(call.func, ast.Name) else None
    if name == "_fail_entry_plan":
        return [call.args[1]]
    if name == "_reject_for_exchange_constraints":
        return [kw.value for kw in call.keywords if kw.arg == "code"]
    return []


def test_every_entry_plan_failure_site_passes_a_bounded_code():
    """A new call site with free text (an f-string of a venue message, a sentence) fails here."""
    allowed_names = {"reason", "reason_code", "failure_reason", "plan_reason", "code", "constraint_error"}
    seen = 0
    for node in ast.walk(ast.parse(GATEWAY.read_text())):
        if not isinstance(node, ast.Call):
            continue
        for arg in _reason_arguments(node):
            seen += 1
            if isinstance(arg, ast.Constant):
                assert isinstance(arg.value, str) and CODE.fullmatch(arg.value), f"line {node.lineno}: {arg.value!r} is not a bounded code"
            else:
                assert isinstance(arg, ast.Name) and arg.id in allowed_names, (
                    f"line {node.lineno}: {ast.unparse(arg)} must be a literal code or a vetted variable"
                )
    assert seen >= 10  # the scan found the real call sites, not an empty set


@pytest.mark.parametrize("free_text", [
    "Executable venue order rules are unavailable.",
    "BTC/USDT (usdt_futures) not available on bitget_demo",
    "UPPER_CASE", "", "a" * 200,
])
def test_free_text_is_never_stored_as_a_plan_reason(monkeypatch, free_text):
    from typing import cast

    from aletheia.storage.models import TradeProposal

    recorded = []
    from aletheia.storage import repositories as repo

    monkeypatch.setattr(repo, "get_entry_plan", lambda _id: {"status": "active"})
    monkeypatch.setattr(repo, "transition_entry_plan", lambda *a, **kw: recorded.append(kw["reason"]) or True)
    gateway._fail_entry_plan(cast(TradeProposal, type("P", (), {"entry_plan_id": "plan-1"})()), free_text)
    assert recorded == ["entry_failure_code_invalid"]


def test_venue_rule_failures_carry_distinguishable_codes():
    """Below-minimum, unreadable rules and an unlisted symbol must not collapse into one sentence."""
    from extensions.exchanges.ccxt.order_rules import _find_market

    with pytest.raises(ConstraintUnavailable) as unlisted:
        _find_market(type("X", (), {"markets": {}})(), "NOPE/USDT", "spot")
    assert unlisted.value.reason_code == "symbol_not_active_on_venue"

    from tests.test_order_constraints import _market
    from extensions.exchanges.ccxt.order_rules import constraints_from_market

    constraints = constraints_from_market(
        venue="bitget", symbol="BTC/USDT", market_type="spot", market=_market(), precision_mode="TICK_SIZE",
    )
    with pytest.raises(ConstraintUnavailable) as small:
        normalize_quantity(1e-12, 60000, constraints, allow_increase=False)
    assert small.value.reason_code == "order_quantity_below_exchange_minimum"
    assert unlisted.value.reason_code != small.value.reason_code
