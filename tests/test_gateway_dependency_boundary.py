"""Core preparation diagnostics do not require a concrete Exchange package."""

from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


def test_gateway_imports_without_exchange_or_ccxt():
    script = '''
import sys
class AbsentExchange:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "aletheia.exchanges" or fullname.startswith("aletheia.exchanges.") or fullname == "ccxt" or fullname.startswith("ccxt."):
            raise ModuleNotFoundError("isolated absent Extension", name=fullname)
sys.meta_path.insert(0, AbsentExchange())
from aletheia.execution import gateway
assert callable(gateway.execute_existing_proposal)
'''
    result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("error_type,temporary", [("DDoSProtection", True), ("BadResponse", True), ("AuthenticationError", False)])
@pytest.mark.parametrize("allow_increase", [False, True])
def test_exchange_normalizes_dependency_failure_without_changing_quantity_gate(monkeypatch, error_type, temporary, allow_increase):
    import ccxt
    from aletheia import net
    from aletheia.execution import gateway, order_constraints as rules
    from aletheia.exchanges.ccxt_order_rules import read_order_constraints

    closed = []

    async def load():
        raise getattr(ccxt, error_type)("synthetic transport detail")

    async def close():
        closed.append(True)

    monkeypatch.setattr(net, "make_ccxt", lambda *args, **kwargs: (SimpleNamespace(load_markets=load, close=close), None))
    monkeypatch.setattr(rules, "_constraints_reader", read_order_constraints)
    monkeypatch.setattr(rules, "_cache", {})
    if temporary and not allow_increase:
        with pytest.raises(rules.ConstraintUnavailable) as raised:
            gateway._prepare_exchange_quantity("bitget_demo", "BTC/USDT", "spot", 0.001, 60000, allow_increase=False)
        assert raised.value.temporary is True
    else:
        quantity, reason = gateway._prepare_exchange_quantity("bitget_demo", "BTC/USDT", "spot", 0.001, 60000, allow_increase=allow_increase)
        assert quantity == 0.001
        assert "No order was sent" in reason
    assert closed == [True]
