"""Core consumers fail closed without concrete Learning safety/registry queries."""

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
from aletheia.trust.behavior_authority import authorize_candidate_block
cfg = SimpleNamespace(agent=SimpleNamespace(autonomous_learning=True), runtime=SimpleNamespace(paused=False))
assert learning_influence_allowed() is False
assert learning_refreeze_reason() == "learning_integrity_state_unavailable"
assert sizing_policy.learned_appetite_adjustment("bull", cfg) == 0.0
assert persona_rollout.rollout_runtime_safe(cfg) is False
assert authorize_candidate_block(source="manual_guard") is True
assert authorize_candidate_block(source="seed_guard") is True
assert authorize_candidate_block(source="learned_guard", prospective_authority=True, learning_path_id="ranker_learned_guard") is False
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
        if not available and fullname in {"aletheia.learning.control", "aletheia.learning.registry"}:
            raise ModuleNotFoundError("Learning control absent", name=fullname)
sys.meta_path.insert(0, MissingControl())
from aletheia.trust import learning_safety as safety
from aletheia.trust import behavior_authority as behavior
def forbidden(*args):
    raise AssertionError("bootstrap queried Learning safety")
if available:
    from aletheia.learning import control
    from aletheia.learning import registry
    control.learning_influence_gate_reason = forbidden
    control.global_learning_refreeze_reason = forbidden
    registry.candidate_filter_path_declared = forbidden
assert safety._influence_gate is None and safety._refreeze_reason is None
assert behavior._candidate_filter_path_resolver is None
'''
    script += '\nfrom aletheia.server.app import app\n' if entrypoint == "web" else '\nfrom aletheia import main\nmain._configure_runtime()\n'
    script += '\nassert safety._influence_gate is (forbidden if available else None)\nassert safety._refreeze_reason is (forbidden if available else None)\n'
    script += '\nassert behavior._candidate_filter_path_resolver is (forbidden if available else None)\n'
    result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_registry_evidence_cannot_override_core_source_or_path(monkeypatch):
    from unittest.mock import Mock
    from aletheia.trust import behavior_authority as behavior

    resolver = Mock(return_value=True)
    monkeypatch.setattr(behavior, "_candidate_filter_path_resolver", resolver)
    assert behavior.authorize_candidate_block(source="learned_guard") is False
    assert behavior.authorize_candidate_block(source="unknown", prospective_authority=True) is False
    assert behavior.authorize_candidate_block(source="learned_guard", prospective_authority=True, learning_path_id="ranker_factor_filter") is False
    resolver.assert_not_called()
    kwargs = dict(source="learned_guard", prospective_authority=True, learning_path_id="ranker_learned_guard")
    assert behavior.authorize_candidate_block(**kwargs) is True
    resolver.assert_called_once_with("ranker_learned_guard")
    for invalid in (False, None, 1, "true", {}):
        resolver.return_value = invalid
        assert behavior.authorize_candidate_block(**kwargs) is False
    resolver.side_effect = RuntimeError("registry unavailable")
    assert behavior.authorize_candidate_block(**kwargs) is False


def test_real_registry_only_declares_filter_capability():
    from aletheia.learning.registry import candidate_filter_path_declared

    assert candidate_filter_path_declared("ranker_learned_guard") is True
    assert candidate_filter_path_declared("ranker_factor_filter") is True
    assert candidate_filter_path_declared("risk_lesson_constraint") is False
    with pytest.raises(KeyError):
        candidate_filter_path_declared("unknown")
