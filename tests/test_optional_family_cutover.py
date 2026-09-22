"""Remaining optional families cannot own Core boot or canonical history."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


def test_families_have_one_physical_owner_and_core_facts_remain():
    root = Path(__file__).resolve().parents[1]
    for family in ("knowledge", "learning", "counterfactual", "observation_lab", "policies", "sync"):
        assert not (root / "aletheia" / family).exists()
        assert (root / "extensions" / family / "__init__.py").is_file()
    for name in ("filter_evidence", "outcome_integrity", "outcome_semantics"):
        assert (root / "aletheia" / "analytics" / (name + ".py")).is_file()
        assert not (root / "extensions" / "analytics" / (name + ".py")).exists()


@pytest.mark.parametrize("entrypoint", ["cli", "web"])
@pytest.mark.parametrize("missing", ["knowledge", "learning", "counterfactual", "observation_lab", "policies", "sync", "all"])
def test_core_composition_without_optional_family(tmp_path, entrypoint, missing):
    script = f"entrypoint={entrypoint!r}\nmissing={missing!r}\n" + '''
import importlib
import socket
import sys
import threading
families = ("knowledge", "learning", "counterfactual", "observation_lab", "policies", "sync") if missing == "all" else (missing,)
blocked = tuple("extensions." + name for name in families)
class MissingOptional:
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + ".") for name in blocked):
            raise ModuleNotFoundError("optional family absent", name=fullname)
sys.meta_path.insert(0, MissingOptional())
def forbidden(*args, **kwargs):
    raise AssertionError("composition cannot use network, credentials, listeners, or background work")
socket.socket.connect = socket.socket.bind = forbidden
threading.Thread.start = forbidden
from aletheia.config import secrets
secrets.get_secret = forbidden
if entrypoint == "cli":
    from aletheia.main import _configure_runtime
    _configure_runtime()
else:
    importlib.import_module("aletheia.server.app")
from aletheia.agents import get_agent_fn
assert callable(get_agent_fn("portfolio_agent"))
assert callable(get_agent_fn("equity_observer"))
assert callable(get_agent_fn("market_observer"))
if missing in ("knowledge", "all"):
    for name in ("research_agent", "risk_critic", "post_trade_reviewer", "dreaming_agent", "lesson_reviewer"):
        try:
            get_agent_fn(name)
        except ValueError:
            pass
        else:
            raise AssertionError("Knowledge-dependent agent remained executable: " + name)
from aletheia.agent_runtime import scheduler
from aletheia.analytics import family_scorecard
if missing in ("counterfactual", "all"):
    assert scheduler._resolve_stale_shadows is None
    assert family_scorecard._read_shadow_cohort is None
if missing in ("knowledge", "all"):
    assert scheduler._expire_learned_guards is None
if missing in ("learning", "all"):
    assert scheduler._learning_daily_catchup_due is None
assert not any(any(name == prefix or name.startswith(prefix + ".") for prefix in blocked) for name in sys.modules)
'''
    result = subprocess.run([sys.executable, "-c", script],
                            cwd=Path(__file__).resolve().parents[1],
                            env={**os.environ, "ALETHEIA_DATA_DIR": str(tmp_path), "ALETHEIA_RUNTIME_MODE": "test"},
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_canonical_trade_stats_survive_absent_shadow_reader(monkeypatch):
    from aletheia.analytics import family_scorecard, outcome_integrity, trade_stats
    monkeypatch.setattr(family_scorecard, "_read_shadow_cohort", None)
    monkeypatch.setattr(outcome_integrity, "eligible_strategy_outcomes", lambda **kwargs: [{
        "id": "canonical", "realized_pnl": 5.0, "entry_price": 100.0, "quantity": 1.0,
        "opened_at": "2026-09-22T00:00:00Z", "closed_at": "2026-09-22T01:00:00Z",
    }])
    scorecard = family_scorecard.compute_family_scorecard_with_epoch()
    assert scorecard["families"][0]["trade_count"] == 1
    assert scorecard["families"][0]["expectancy_pct"] == 5.0
    assert scorecard["families"][0]["shadow_count"] is None
    assert scorecard["shadow_epoch_status"] == "unavailable"
    assert scorecard["shadow_epoch_error"] == "counterfactual_extension_unavailable"
    result = trade_stats.build_trade_stats()
    assert result is not None


def test_optional_job_absence_is_failure_not_success(monkeypatch):
    from types import SimpleNamespace
    from aletheia.agent_runtime import scheduler
    failures = []
    monkeypatch.setattr(scheduler, "_resolve_stale_shadows", None)
    monkeypatch.setattr(scheduler, "_expire_learned_guards", None)
    monkeypatch.setattr(scheduler, "_learning_daily_catchup_due", None)
    monkeypatch.setattr(scheduler, "load_config", lambda: SimpleNamespace(runtime=SimpleNamespace(paused=False)))
    monkeypatch.setattr(scheduler, "_record_job_failure", lambda job, reason: failures.append((job, reason)))
    scheduler._run_shadow_resolver()
    scheduler._run_guard_promotion()
    scheduler._run_learning_startup_catchup()
    assert failures == [
        ("shadow_resolver", "counterfactual_extension_unavailable"),
        ("guard_promotion", "knowledge_extension_unavailable"),
        ("learning_startup_catchup", "learning_control_unavailable"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["create_research_note", "create_dreaming_log", "create_diagnostic_todo", "create_improvement_proposal", "create_daily_review"])
@pytest.mark.parametrize("missing", ["extensions", "extensions.knowledge", "extensions.knowledge.manager", "broken_dependency"])
async def test_explicit_knowledge_command_absence_is_honest(monkeypatch, command, missing):
    import builtins
    from aletheia.agent_shell import commands
    original = builtins.__import__

    def deny(name, *args, **kwargs):
        if name == "extensions.knowledge.manager":
            raise ModuleNotFoundError("isolated missing dependency", name=missing)
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", deny)
    call = getattr(commands, command)
    if missing == "broken_dependency":
        with pytest.raises(ModuleNotFoundError, match="isolated missing"):
            await call({"title": "bounded test", "content": "bounded test content"})
    else:
        assert await call({"title": "bounded test", "content": "bounded test content"}) == {"error": "knowledge_extension_unavailable"}
