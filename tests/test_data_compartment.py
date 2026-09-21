"""Concrete public Data is absent-safe at the existing composition roots."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


def test_public_data_has_one_physical_implementation():
    root = Path(__file__).resolve().parents[1]
    for relative in (
        "ccxt_provider.py", "sources/__init__.py", "sources/chain.py",
        "sources/coingecko.py", "sources/binance_public.py",
        "collectors/microstructure.py", "collectors/derivatives.py",
        "collectors/volume_scanner.py",
    ):
        assert not (root / "aletheia/data" / relative).exists()
        assert (root / "extensions/data" / relative).is_file()


@pytest.mark.parametrize("entrypoint", ["cli", "web", "collector"])
@pytest.mark.parametrize("missing", [
    "extensions", "extensions.data", "extensions.data.ccxt_provider", "broken_dependency",
])
def test_data_absence_clears_bindings_without_starting_runtime(entrypoint, missing, tmp_path):
    script = f"entrypoint = {entrypoint!r}\nmissing = {missing!r}\n" + '''
import asyncio
import importlib
import socket
import sys
from aletheia.config import secrets
from aletheia.data import providers

def forbidden(*args, **kwargs):
    raise AssertionError("composition must not read secrets, open clients or use stale bindings")
secrets.get_secret = forbidden
socket.socket.connect = forbidden
providers.configure_listing_reader(forbidden)
providers.configure_market_data_factory(forbidden)
attempted = []
class MissingData:
    def find_spec(self, fullname, path=None, target=None):
        target_name = "extensions.data.ccxt_provider" if missing == "broken_dependency" else missing
        if fullname == target_name:
            attempted.append(fullname)
            raise ModuleNotFoundError("isolated missing dependency", name=missing)
sys.meta_path.insert(0, MissingData())

class StopBeforeRuntime(Exception):
    pass
def stop_before_runtime():
    raise StopBeforeRuntime
try:
    if entrypoint == "cli":
        from aletheia import main
        main._configure_runtime()
    elif entrypoint == "web":
        importlib.import_module("aletheia.server.app")  # no lifespan / Gateway startup
    else:
        import aletheia.logging_config
        from aletheia.config import loader
        from aletheia.data import collector_runner
        aletheia.logging_config.configure_logging = lambda: None
        loader.bootstrap = stop_before_runtime
        try:
            collector_runner.main()
        except StopBeforeRuntime:
            pass
except ModuleNotFoundError as exc:
    assert missing == "broken_dependency" and exc.name == missing, exc
else:
    assert missing != "broken_dependency", "internal dependency defects must propagate"
    assert providers._listing_reader is None
    assert providers._provider_factory is None
    try:
        providers.get_market_data_provider("okx", exact_venue=True)
    except RuntimeError as exc:
        assert str(exc) == "market data provider unavailable"
    else:
        raise AssertionError("absent Data must remain unavailable")
assert attempted, "test must reach the missing adapter or parent"
'''
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "ALETHEIA_DATA_DIR": str(tmp_path)},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
