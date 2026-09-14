"""Core Persona fails closed without concrete Learning safety queries."""

from pathlib import Path
import subprocess
import sys

import pytest


def test_persona_core_works_without_learning():
    script = '''
import sys
from types import SimpleNamespace
attempted = []
class MissingLearning:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "aletheia.learning" or fullname.startswith("aletheia.learning."):
            attempted.append(fullname)
            raise AssertionError("Core imported concrete Learning: " + fullname)
sys.meta_path.insert(0, MissingLearning())
from aletheia.trust.learning_safety import learning_influence_allowed, learning_refreeze_reason
from aletheia.trading import persona, persona_rollout, sizing_policy
cfg = SimpleNamespace(agent=SimpleNamespace(autonomous_learning=True), runtime=SimpleNamespace(paused=False))
assert learning_influence_allowed() is False
assert learning_refreeze_reason() == "learning_integrity_state_unavailable"
assert sizing_policy.learned_appetite_adjustment("bull", cfg) == 0.0
assert persona_rollout.rollout_runtime_safe(cfg) is False
assert not attempted, attempted
'''
    result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_learning_safety_preserves_reasons_and_rejects_invalid_evidence(monkeypatch):
    from aletheia.trust import learning_safety as safety

    for reason in (None, "runtime_paused", "autonomous_learning_disabled", "reconciliation_integrity_incident:fixture"):
        monkeypatch.setattr(safety, "_influence_gate", lambda: reason)
        monkeypatch.setattr(safety, "_refreeze_reason", lambda: reason)
        assert safety.learning_influence_allowed() is (reason is None)
        assert safety.learning_refreeze_reason() == reason
    for value in (False, True, 0, {}, ""):
        monkeypatch.setattr(safety, "_influence_gate", lambda: value)
        monkeypatch.setattr(safety, "_refreeze_reason", lambda: value)
        assert safety.learning_influence_allowed() is False
        assert safety.learning_refreeze_reason() == "learning_integrity_state_unavailable"
    def unavailable():
        raise RuntimeError("query unavailable")
    monkeypatch.setattr(safety, "_influence_gate", unavailable)
    monkeypatch.setattr(safety, "_refreeze_reason", unavailable)
    assert safety.learning_influence_allowed() is False
    assert safety.learning_refreeze_reason() == "learning_integrity_state_unavailable"


@pytest.mark.parametrize("entrypoint", ["web", "cli"])
@pytest.mark.parametrize("available", [False, True])
def test_bootstrap_binds_learning_safety_without_querying(entrypoint, available):
    script = f"available = {available!r}\n" + '''
import sys
class MissingControl:
    def find_spec(self, fullname, path=None, target=None):
        if not available and fullname == "aletheia.learning.control":
            raise ModuleNotFoundError("Learning control absent", name=fullname)
sys.meta_path.insert(0, MissingControl())
from aletheia.trust import learning_safety as safety
def forbidden():
    raise AssertionError("bootstrap queried Learning safety")
if available:
    from aletheia.learning import control
    control.learning_influence_gate_reason = forbidden
    control.global_learning_refreeze_reason = forbidden
assert safety._influence_gate is None and safety._refreeze_reason is None
'''
    script += '\nfrom aletheia.server.app import app\n' if entrypoint == "web" else '\nfrom aletheia import main\nmain._configure_runtime()\n'
    script += '\nassert safety._influence_gate is (forbidden if available else None)\nassert safety._refreeze_reason is (forbidden if available else None)\n'
    result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
