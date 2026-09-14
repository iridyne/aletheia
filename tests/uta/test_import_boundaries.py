"""Absent optional implementations must not break UTA contracts or other venues."""

from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("target,blocked,action", [
    ("aletheia.uta.models", ("aletheia.uta.ccxt_adapter", "aletheia.uta.profiles"), ""),
    ("aletheia.uta.ccxt_adapter", ("aletheia.uta.profiles.binance", "aletheia.uta.profiles.okx", "aletheia.uta.profiles.bitget"), ""),
    ("aletheia.uta.manager", ("aletheia.uta.profiles.binance", "aletheia.uta.profiles.okx"),
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
