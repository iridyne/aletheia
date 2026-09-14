"""Optional Telegram HTTP work belongs to its adapter, not Core routes."""

from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("missing", ["aletheia.telegram", "aletheia.telegram.routes"])
def test_core_routes_survive_absent_telegram(missing):
    script = f"missing = {missing!r}\n" + '''
import sys
class MissingTelegram:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == missing:
            raise ModuleNotFoundError("optional Telegram absent", name=fullname)
sys.meta_path.insert(0, MissingTelegram())
from aletheia.server.app import app
paths = app.openapi()["paths"]
assert "/api/telegram/health" not in paths
assert "/api/settings" in paths
assert "aletheia.server.routes.telegram" not in sys.modules
'''
    result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
