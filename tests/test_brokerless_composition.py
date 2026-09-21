"""Missing Exchange must not stop Core composition or retain old bindings."""

from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("entrypoint", ["cli", "web"])
@pytest.mark.parametrize("missing", [
    "extensions.exchanges.composition", "broken_dependency",
    "extensions", "extensions.exchanges", "extensions.exchanges.ccxt",
    "extensions.exchanges.ccxt.adapter", "extensions.exchanges.ccxt.profile",
])
def test_brokerless_composition(entrypoint, missing):
    script = f"entrypoint = {entrypoint!r}\nmissing = {missing!r}\n" + '''
import asyncio
import importlib
import socket
import sys
from aletheia.uta import manager
from aletheia.execution import order_constraints
from aletheia.config import secrets

def forbidden(*args, **kwargs):
    raise AssertionError("composition must not query credentials, clients, or old bindings")
secrets.get_secret = forbidden
socket.socket.connect = forbidden
order_constraints.configure_order_constraints_reader(forbidden)
manager.configure_uta_provider(
    registry_factory=forbidden, retention_contract=forbidden,
    credential_revision_reader=forbidden, credential_presence_reader=forbidden,
    native_protection_reader_factory=forbidden, native_protection_modes=("bitget_demo",),
)
attempted = []
class MissingExchange:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == (missing if missing != "broken_dependency" else "extensions.exchanges.composition"):
            attempted.append(fullname)
            raise ModuleNotFoundError("isolated missing dependency", name=missing)
sys.meta_path.insert(0, MissingExchange())

class StopBeforeRuntime(Exception):
    pass
def stop_before_runtime():
    raise StopBeforeRuntime
async def start_web():
    server = importlib.import_module("aletheia.server.app")
    import aletheia.logging_config
    aletheia.logging_config.configure_logging = lambda: None
    server.bootstrap = stop_before_runtime
    try:
        async with server.lifespan(server.app):
            raise AssertionError("runtime startup is not authorized")
    except StopBeforeRuntime:
        pass
try:
    if entrypoint == "cli":
        from aletheia import main
        main._configure_runtime()
    else:
        asyncio.run(start_web())
except ModuleNotFoundError as exc:
    assert missing == "broken_dependency" and exc.name == missing, exc
else:
    assert missing != "broken_dependency", "unrelated import errors must propagate"
    assert manager.build_uta_registry().configured_identities() == ()
    assert manager.read_credential_presence("bitget_demo") is None
    assert manager.native_protection_execution_modes() == ()
    assert manager._retention_contract is None
    assert manager._credential_revision_reader is None
    assert manager._native_protection_reader_factory is None
    assert order_constraints._constraints_reader is None
    try:
        manager.create_uta_adapter("bitget_demo")
    except KeyError:
        pass
    else:
        raise AssertionError("absent Exchange must not create an adapter")
assert attempted, "test must exercise the missing dependency"
'''
    result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
