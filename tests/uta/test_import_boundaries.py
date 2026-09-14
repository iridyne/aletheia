"""Absent optional implementations must not break UTA contracts or other venues."""

from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("target,blocked,action", [
    ("aletheia.exchanges.composition", ("aletheia.uta.profiles.binance", "aletheia.uta.profiles.okx"),
     "from aletheia.config.schema import ExchangeConfig\nmodule.get_secret = lambda key: 'synthetic-test-value' if key.startswith('bitget.demo.') else None\nmodule.get_credential_generation = lambda scope: 0\ncfg = SimpleNamespace(exchange=ExchangeConfig())\nassert [i.execution_mode for i in module.build_uta_registry(cfg).configured_identities()] == ['bitget_demo']\nassert module.build_uta_registry(cfg, execution_modes=('okx_demo',)).configured_identities() == ()"),
    ("aletheia.uta.manager", ("aletheia.config.secrets", "aletheia.uta.ccxt_adapter", "aletheia.uta.profiles"),
     "assert module.build_uta_registry().configured_identities() == ()\nimport asyncio\nstate = asyncio.run(module.get_unified_state(SimpleNamespace(), execution_modes=('bitget_demo',)))\nassert state.total_equity_usdt is None\nassert state.errors == ['bitget_demo:account_not_configured']"),
    ("aletheia.uta.models", ("aletheia.uta.ccxt_adapter", "aletheia.uta.profiles"), ""),
    ("aletheia.uta.ccxt_adapter", ("aletheia.uta.profiles.binance", "aletheia.uta.profiles.okx", "aletheia.uta.profiles.bitget"), ""),
    ("aletheia.exchanges.composition", ("aletheia.uta.profiles.binance", "aletheia.uta.profiles.okx"),
     "assert module.funding_history_retention_contract('bitget_demo', cfg=SimpleNamespace(exchange=SimpleNamespace(bitget_profile='classic_v2'))) == (90, '1')"),
])
def test_uta_import_does_not_require_unselected_implementations(target, blocked, action):
    script = f"""
import importlib
import sys
from types import SimpleNamespace
blocked = {blocked!r}
class RejectOptional:
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + '.') for name in blocked):
            raise ImportError('unavailable optional implementation: ' + fullname)
sys.meta_path.insert(0, RejectOptional())
module = importlib.import_module({target!r})
{action}
assert not any(name == prefix or name.startswith(prefix + '.') for name in sys.modules for prefix in blocked)
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[2],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
