"""Absent optional implementations must not break UTA contracts or other venues."""

from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("current,retired", [
    ("extensions.exchanges.composition", "aletheia.exchanges.composition"),
    ("extensions.exchanges.credential_routes", "aletheia.exchanges.credential_routes"),
    ("extensions.exchanges.ccxt.order_rules", "aletheia.exchanges.ccxt_order_rules"),
    ("extensions.exchanges.ccxt.submission_errors", "aletheia.exchanges.submission_errors"),
])
def test_exchange_compartment_has_no_core_forwarding_path(current, retired):
    import importlib

    root = Path(__file__).resolve().parents[2]
    module = importlib.import_module(current)
    assert Path(module.__file__) == root / (current.replace(".", "/") + ".py")
    assert not any((root / "aletheia/exchanges").rglob("*.py"))
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(retired)


@pytest.mark.parametrize("target,blocked,action", [
    ("aletheia.trust.live_unlock", ("aletheia.config.secrets", "aletheia.exchanges", "extensions.exchanges", "ccxt"),
     "import asyncio\nfrom aletheia.config.schema import AletheiaConfig\ncfg = AletheiaConfig()\ncfg.exchange.name = 'bitget'\ncfg.exchange.mode = 'live'\nassert asyncio.run(module.run_live_preflight(cfg))['reason_code'] == 'exchange_adapter_unavailable'"),
    ("aletheia.server.routes.bitget", ("aletheia.config.secrets", "aletheia.exchanges", "extensions.exchanges", "ccxt"),
     "assert not {'set_exchange_credentials', 'test_candidate_credentials', 'get_secret', 'set_secret'} & vars(module).keys()"),
    ("aletheia.server.app", ("extensions.exchanges.bitget",),
     "paths = {route.path for route in module.app.routes}\nassert '/api/uta/state' in paths\nassert '/api/bitget/test-connection' in paths\nassert '/api/bitget/test-candidate' in paths\nassert '/api/bitget/credentials-status' in paths"),
    ("aletheia.server.app", ("extensions.exchanges.credential_routes",),
     "paths = {route.path for route in module.app.routes}\nassert '/api/uta/state' in paths\nassert '/api/bitget/test-connection' in paths\nassert '/api/bitget/test-candidate' not in paths\nassert '/api/bitget/credentials-status' not in paths"),
    ("extensions.exchanges.composition", ("extensions.exchanges.binance.profile", "extensions.exchanges.okx.profile"),
     "from aletheia.config.schema import ExchangeConfig\nmodule.get_secret = lambda key: 'synthetic-test-value' if key.startswith('bitget.demo.') else None\nmodule.get_credential_generation = lambda scope: 0\ncfg = SimpleNamespace(exchange=ExchangeConfig())\nassert [i.execution_mode for i in module.build_uta_registry(cfg).configured_identities()] == ['bitget_demo']\nassert module.build_uta_registry(cfg, execution_modes=('okx_demo',)).configured_identities() == ()"),
    ("aletheia.uta.manager", ("aletheia.config.secrets", "extensions.exchanges", "aletheia.uta.profiles"),
     "assert module.build_uta_registry().configured_identities() == ()\nimport asyncio\nstate = asyncio.run(module.get_unified_state(SimpleNamespace(), execution_modes=('bitget_demo',)))\nassert state.total_equity_usdt is None\nassert state.errors == ['bitget_demo:account_not_configured']"),
    ("aletheia.uta.models", ("extensions.exchanges", "aletheia.uta.profiles"), ""),
    ("extensions.exchanges.ccxt.adapter", ("extensions.exchanges.binance", "extensions.exchanges.okx", "extensions.exchanges.bitget"), ""),
    ("extensions.exchanges.composition", ("extensions.exchanges.bitget",),
     "from aletheia.config.schema import ExchangeConfig\ncfg = SimpleNamespace(exchange=ExchangeConfig())\nassert module.build_uta_registry(cfg, execution_modes=('bitget_demo',)).configured_identities() == ()"),
    ("extensions.exchanges.composition", ("extensions.exchanges.binance.profile", "extensions.exchanges.okx.profile"),
     "assert module.funding_history_retention_contract('bitget_demo', cfg=SimpleNamespace(exchange=SimpleNamespace())) == (90, '1')"),
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
            raise ModuleNotFoundError('unavailable optional implementation: ' + fullname, name=fullname)
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


@pytest.mark.parametrize("venue", ["bitget", "okx", "binance"])
def test_venue_profile_has_one_extension_implementation(venue):
    import importlib

    profile = importlib.import_module(f"extensions.exchanges.{venue}.profile")
    root = Path(__file__).resolve().parents[2]
    assert Path(profile.__file__) == root / f"extensions/exchanges/{venue}/profile.py"
    assert not (root / f"aletheia/uta/profiles/{venue}.py").exists()
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(f"aletheia.uta.profiles.{venue}")


@pytest.mark.parametrize("name", ["client", "native_protection"])
def test_native_bitget_has_one_extension_implementation(name):
    import importlib

    module = importlib.import_module(f"extensions.exchanges.bitget.{name}")
    root = Path(__file__).resolve().parents[2]
    assert Path(module.__file__) == root / f"extensions/exchanges/bitget/{name}.py"
    assert not any((root / "aletheia/exchanges/bitget").glob("*.py"))
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(f"aletheia.exchanges.bitget.{name}")


@pytest.mark.parametrize("error_type", ["ImportError", "ModuleNotFoundError"])
def test_credential_route_internal_import_defect_is_not_optional_absence(error_type):
    script = f"""
import builtins
original = builtins.__import__
def importing(name, *args, **kwargs):
    if name == 'extensions.exchanges.credential_routes':
        raise {error_type}('broken internal import', name='aletheia.trust.missing_internal')
    return original(name, *args, **kwargs)
builtins.__import__ = importing
try:
    import aletheia.server.app
except {error_type} as exc:
    assert exc.name == 'aletheia.trust.missing_internal'
else:
    raise AssertionError('internal import defect was hidden')
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[2],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr


def test_generic_ccxt_has_one_physical_implementation_outside_core():
    import ast
    import importlib
    from extensions.exchanges.ccxt import adapter, profile

    root = Path(__file__).resolve().parents[2]
    assert not (root / "aletheia/uta/ccxt_adapter.py").exists()
    assert not (root / "aletheia/uta/profiles/base.py").exists()
    for retired in ("aletheia.uta.ccxt_adapter", "aletheia.uta.profiles.base"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(retired)
    assert adapter.CCXTUTAAdapter.__module__ == "extensions.exchanges.ccxt.adapter"
    assert profile.CCXTProfile.__module__ == "extensions.exchanges.ccxt.profile"
    for module in (adapter, profile):
        tree = ast.parse(Path(module.__file__).read_text())
        imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        imports += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
        assert not any(name and name.startswith(("aletheia.uta.profiles", "aletheia.exchanges.bitget", "aletheia.exchanges.okx", "aletheia.exchanges.binance", "extensions.exchanges.bitget", "extensions.exchanges.okx", "extensions.exchanges.binance")) for name in imports)
