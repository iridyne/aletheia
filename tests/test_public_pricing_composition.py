"""Public Data composition must not fetch, use credentials, or mask internal defects."""

from pathlib import Path
import subprocess
import sys

import pytest


def test_public_fetcher_preserves_http_acquisition(monkeypatch):
    import httpx
    from extensions.data.public_pricing import fetch_price_map
    from aletheia.economics.pricing_sync import LITELLM_PRICE_MAP_URL

    calls = []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(200, content=b'{"model": {}}', request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx, "get", get)
    assert fetch_price_map() == ({"model": {}}, b'{"model": {}}')
    assert calls == [(LITELLM_PRICE_MAP_URL, {"timeout": 5, "follow_redirects": True})]


@pytest.mark.parametrize("entrypoint", ["cli", "web"])
@pytest.mark.parametrize("missing", [None, "extensions.data", "extensions.data.public_pricing", "broken_dependency"])
def test_public_pricing_composition(entrypoint, missing, tmp_path):
    script = f"entrypoint={entrypoint!r}\nmissing={missing!r}\n" + '''
import importlib
import socket
import sys
from aletheia.config import secrets
from aletheia.economics import pricing_sync

def forbidden(*args, **kwargs):
    raise AssertionError("composition must not fetch or read credentials")
socket.socket.connect = forbidden
secrets.get_secret = forbidden
pricing_sync.configure_pricing_fetcher(forbidden)
class MissingData:
    def find_spec(self, fullname, path=None, target=None):
        target_name = "extensions.data.public_pricing" if missing == "broken_dependency" else missing
        if fullname == target_name:
            raise ModuleNotFoundError("test", name=missing)
sys.meta_path.insert(0, MissingData())
try:
    if entrypoint == "cli":
        from aletheia.main import _configure_runtime
        _configure_runtime()
        pricing_sync.configure_pricing_fetcher(forbidden)
        _configure_runtime()
    else:
        importlib.import_module("aletheia.server.app")
except ModuleNotFoundError as exc:
    assert missing == "broken_dependency" and exc.name == missing
else:
    assert missing != "broken_dependency"
    if missing:
        assert pricing_sync._catalog_fetcher is None
        try:
            pricing_sync.refresh_pricing()
        except RuntimeError as exc:
            assert str(exc) == "pricing_catalog_unavailable"
        else:
            raise AssertionError("missing Data silently refreshed")
    else:
        assert pricing_sync._catalog_fetcher.__module__ == "extensions.data.public_pricing"
'''
    import os
    result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
                            env={**os.environ, "ALETHEIA_DATA_DIR": str(tmp_path)}, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
