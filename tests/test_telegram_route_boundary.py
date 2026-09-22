"""Optional Telegram HTTP work belongs to its adapter, not Core routes."""

from pathlib import Path
import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("missing", ["extensions", "extensions.telegram", "extensions.telegram.routes"])
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


@pytest.mark.parametrize("entrypoint", ["cli", "web"])
@pytest.mark.parametrize("missing", [None, "extensions", "extensions.telegram", "extensions.telegram.delivery", "broken_dependency"])
def test_telegram_delivery_composition(entrypoint, missing, tmp_path):
    script = f"entrypoint = {entrypoint!r}\nmissing = {missing!r}\n" + '''
import sys
import socket
from aletheia.config import loader, secrets
loader._load_dotenv = lambda: None
def forbidden(*args, **kwargs):
    raise AssertionError("composition must not read credentials or perform I/O")
secrets.get_secret = forbidden
socket.socket.connect = forbidden
from aletheia import notifications
notifications.configure_telegram_sender(forbidden)
attempts = []
class MissingTelegram:
    def find_spec(self, fullname, path=None, target=None):
        target_name = "extensions.telegram.delivery" if missing == "broken_dependency" else missing
        if fullname == target_name:
            attempts.append(fullname)
            raise ModuleNotFoundError("optional adapter absent", name=missing)
sys.meta_path.insert(0, MissingTelegram())
try:
    if entrypoint == "cli":
        from aletheia.main import _configure_runtime
        _configure_runtime()
    else:
        import aletheia.server.app
except ModuleNotFoundError as exc:
    assert missing == "broken_dependency" and exc.name == missing
else:
    assert missing != "broken_dependency"
    if missing is None:
        from extensions.telegram.delivery import send_telegram_alert
        assert notifications._telegram_sender is send_telegram_alert
    else:
        assert notifications._telegram_sender is None
    import aletheia.files.jsonl as audit
    events = []
    webhook = []
    audit.write_audit = events.append
    notifications._send_webhook = lambda *args, **kwargs: webhook.append(kwargs)
    notifications.notify_operator("test", "local record", windows=False, error_log=False)
    assert len(events) == 1 and events[0]["event_type"] == "operator_notification"
    assert len(webhook) == 1
if missing is not None:
    assert attempts
assert "aletheia.telegram" not in sys.modules
'''
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "ALETHEIA_DATA_DIR": str(tmp_path)},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
