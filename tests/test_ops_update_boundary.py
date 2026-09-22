"""Core identity and command registration do not load operational writers."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("entrypoint", ["cli", "web"])
def test_core_identity_and_command_registration_without_ops(tmp_path, entrypoint):
    script = '''
import importlib
import importlib.abc
import socket
import sys
import threading

def forbidden(*args, **kwargs):
    raise AssertionError("cold composition must not access network, credentials or background work")

socket.socket.connect = socket.socket.bind = forbidden
socket.getaddrinfo = forbidden
threading.Thread.start = forbidden
from aletheia.config import secrets
secrets.get_secret = forbidden

class WithoutOps(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "ops" or fullname.startswith("ops."):
            raise ModuleNotFoundError(fullname, name=fullname)

sys.meta_path.insert(0, WithoutOps())
for name in (
    "aletheia.update", "aletheia.update.packaged", "aletheia.package_integrity",
    "aletheia.update.git_ops", "aletheia.cli.update_cmd",
    "aletheia.server.routes.update", "aletheia.server.routes.ui",
):
    importlib.import_module(name)
if sys.argv[1] == "cli":
    from aletheia.main import _configure_runtime
    _configure_runtime()
else:
    importlib.import_module("aletheia.server.app")
assert not any(name == "ops" or name.startswith("ops.") for name in sys.modules)
from aletheia.update import git_ops, packaged
assert not hasattr(git_ops, "pull")
assert not hasattr(packaged, "request_update")
'''
    env = {**os.environ, "ALETHEIA_DATA_DIR": str(tmp_path), "ALETHEIA_RUNTIME_MODE": "test"}
    result = subprocess.run(
        [sys.executable, "-c", script, entrypoint], cwd=Path(__file__).resolve().parents[1],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_packaged_integrity_uses_single_core_error_and_actual_ops_owner(tmp_path):
    from aletheia import package_integrity
    from aletheia.update import packaged as identity
    from ops.update import packaged, packaged_commands

    assert packaged.PackagedUpdateError is identity.PackagedUpdateError
    with pytest.raises(identity.PackagedUpdateError):
        package_integrity.validate_release_dir(tmp_path, expected_release_id="test", expected_sha="test")
    assert packaged_commands.PackagedUpdateError is identity.PackagedUpdateError
    original = packaged._verified_feed
    feed = {"release_id": "test"}
    with packaged_commands._pin_verified_feed(feed):
        assert packaged._verified_feed() is feed
    assert packaged._verified_feed is original
