"""Research composition preserves Core registry ownership and explicit absence."""

from pathlib import Path
import subprocess
import sys

import pytest


def test_old_advisory_modules_are_removed():
    root = Path(__file__).resolve().parents[1]
    for name in ("research_agent", "risk_critic"):
        assert not (root / "aletheia" / "agents" / f"{name}.py").exists()


@pytest.mark.parametrize("entrypoint", ["cli", "web"])
@pytest.mark.parametrize("missing", [None, "extensions", "extensions.research", "broken_dependency"])
def test_advisory_agent_composition(entrypoint, missing):
    script = f"entrypoint = {entrypoint!r}\nmissing = {missing!r}\n" + '''
import importlib
import socket
import sys
from aletheia.agents import get_agent_fn, register_agent
from aletheia.config import secrets

assert not any(name.startswith("extensions.research") for name in sys.modules)

def forbidden(*args, **kwargs):
    raise AssertionError("composition must not execute agents, network or credentials")

secrets.get_secret = forbidden
socket.socket.connect = forbidden
for name in ("research_agent", "risk_critic"):
    register_agent(name)(forbidden)

attempted = []
class MissingResearch:
    def find_spec(self, fullname, path=None, target=None):
        target_name = "extensions.research" if missing == "broken_dependency" else missing
        if fullname == target_name:
            attempted.append(fullname)
            raise ModuleNotFoundError("isolated missing dependency", name=missing)

sys.meta_path.insert(0, MissingResearch())
try:
    if entrypoint == "cli":
        from aletheia.main import _configure_runtime
        _configure_runtime()
        # Cached module imports must still replace a stale registration.
        register_agent("research_agent")(forbidden)
        _configure_runtime()
    else:
        importlib.import_module("aletheia.server.app")
except ModuleNotFoundError as exc:
    assert missing == "broken_dependency" and exc.name == missing, exc
else:
    assert missing != "broken_dependency", "internal import defects must propagate"
    for name in ("research_agent", "risk_critic"):
        if missing:
            try:
                get_agent_fn(name)
            except ValueError:
                pass
            else:
                raise AssertionError("missing Research retained an executable binding")
        else:
            fn = get_agent_fn(name)
            assert fn.__module__ == "extensions.research." + name
            assert fn is not forbidden
    assert callable(get_agent_fn("portfolio_agent")), "Core snapshot agent remains available"
    from aletheia.server.routes.data import get_agents_catalog
    catalog = {row["name"]: row for row in get_agents_catalog()["agents"]}
    for name in ("research_agent", "risk_critic"):
        if missing:
            assert catalog[name]["runnable"] is False
            assert catalog[name]["blocked_reason"] == "agent_unavailable"
        else:
            assert catalog[name].get("runnable", True)
if missing:
    assert attempted, "missing-package path must be exercised"
'''
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
