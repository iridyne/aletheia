"""Every entrypoint binds the market-data factory without importing it from Core."""

from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("entrypoint", ["cli", "web", "collector"])
@pytest.mark.parametrize("missing", [None, "extensions.data.ccxt_provider", "broken_dependency"])
def test_market_data_factory_composition(entrypoint, missing):
    script = f"entrypoint = {entrypoint!r}\nmissing = {missing!r}\n" + '''
import asyncio
import importlib
import socket
import sys
from aletheia.data import providers
from aletheia.config import secrets, loader
import aletheia.logging_config
def forbidden(*args, **kwargs):
    raise AssertionError("composition must not query clients, credentials, or old bindings")
secrets.get_secret = forbidden
socket.socket.connect = forbidden
providers.configure_market_data_factory(forbidden)
attempted = []
if missing:
    class MissingAdapter:
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "extensions.data.ccxt_provider":
                attempted.append(fullname)
                raise ModuleNotFoundError("isolated missing dependency", name=missing)
    sys.meta_path.insert(0, MissingAdapter())
else:
    from extensions.data import ccxt_provider
    ccxt_provider.get_market_data_provider = forbidden
class StopBeforeRuntime(Exception):
    pass
def stop_before_runtime():
    raise StopBeforeRuntime
aletheia.logging_config.configure_logging = lambda: None
loader.bootstrap = stop_before_runtime
try:
    if entrypoint == "cli":
        from aletheia import main
        main._configure_runtime()
    elif entrypoint == "web":
        importlib.import_module("aletheia.server.app")
    else:
        from aletheia.data import collector_runner
        try:
            collector_runner.main()
        except StopBeforeRuntime:
            pass
except ModuleNotFoundError as exc:
    assert missing == "broken_dependency" and exc.name == missing, exc
else:
    assert missing != "broken_dependency", "transitive errors must propagate"
    if missing:
        assert providers._provider_factory is None
    else:
        assert providers._provider_factory is ccxt_provider.create_market_data_provider
if missing:
    assert attempted
'''
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
