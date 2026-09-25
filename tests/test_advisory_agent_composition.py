"""Research composition preserves Core registry ownership and explicit absence."""

from pathlib import Path
import subprocess
import sys

import pytest


def test_old_advisory_modules_are_removed():
    root = Path(__file__).resolve().parents[1]
    for name in ("research_agent", "risk_critic", "equity_observer", "market_observer", "post_trade_reviewer", "prediction_observer", "divergence_scanner", "dreaming_agent", "lesson_reviewer", "research_worker"):
        assert not (root / "core" / "aletheia" / "agents" / f"{name}.py").exists()


@pytest.mark.parametrize("entrypoint", ["cli", "web"])
@pytest.mark.parametrize("missing", [
    None, "extensions", "extensions.research", "extensions.data",
    "extensions.data.ccxt_provider", "extensions.data.polymarket_provider", "broken_dependency", "broken_data_dependency",
    "broken_nightly_dependency", "extensions.research.dreaming_agent",
])
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
advisory = ("research_agent", "risk_critic", "equity_observer", "post_trade_reviewer", "dreaming_agent", "lesson_reviewer")
pipeline = ("prediction_observer", "divergence_scanner")
names = advisory + pipeline
broken = missing in {"broken_dependency", "broken_data_dependency", "broken_nightly_dependency", "extensions.research.dreaming_agent"}
unavailable = names if missing in {"extensions", "extensions.research"} else pipeline if missing else ()
for name in names:
    register_agent(name)(forbidden)
from aletheia.agent_runtime import scheduler
scheduler.configure_research_producers(extract_lessons=forbidden, enqueue_run=forbidden)

attempted = []
class MissingResearch:
    def find_spec(self, fullname, path=None, target=None):
        target_name = "extensions.research" if missing == "broken_dependency" else missing
        if missing == "broken_data_dependency":
            target_name = "extensions.data.ccxt_provider"
        if missing == "broken_nightly_dependency":
            target_name = "extensions.research.dreaming_agent"
        if fullname == target_name:
            attempted.append(fullname)
            raise ModuleNotFoundError("isolated missing dependency", name=missing)

sys.meta_path.insert(0, MissingResearch())
try:
    if entrypoint == "cli":
        from aletheia.main import _configure_runtime
        _configure_runtime()
        # Cached module imports must still replace a stale registration.
        for name in names:
            register_agent(name)(forbidden)
        _configure_runtime()
    else:
        importlib.import_module("aletheia.server.app")
except ModuleNotFoundError as exc:
    assert broken and exc.name == missing, exc
else:
    assert not broken, "internal import defects must propagate"
    if missing in {"extensions", "extensions.research"}:
        assert scheduler._extract_research_lessons is None
        assert scheduler._enqueue_research_run is None
    else:
        assert scheduler._extract_research_lessons.__module__ == "extensions.research.dreaming_agent"
        assert scheduler.require_research_enqueue().__module__ == "extensions.research.research_worker"
    for name in names:
        if name in unavailable:
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
    from aletheia.server.routes import data
    from aletheia.config.schema import AletheiaConfig
    cfg = AletheiaConfig()
    cfg.market_data.prediction_enabled = True
    cfg.market_data.observed_prediction_markets = ["crypto"]
    data.load_config = lambda: cfg
    catalog = {row["name"]: row for row in data.get_agents_catalog()["agents"]}
    for name in names:
        if name in unavailable:
            assert catalog[name]["runnable"] is False
            assert catalog[name]["blocked_reason"] == "agent_unavailable"
            from aletheia.agent_runtime import job_queue
            manager = job_queue.AgentJobManager()
            manager.start = forbidden
            job_queue.queue_run = forbidden
            try:
                manager.enqueue(name)
            except ValueError:
                pass
            else:
                raise AssertionError("missing Research must not create an AgentRun")
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
