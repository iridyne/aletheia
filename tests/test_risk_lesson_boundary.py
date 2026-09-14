"""Core keeps static rules and failure policy when optional Learning is absent."""

import builtins
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from aletheia.risk import engine
from aletheia.storage import repositories as repo
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import PSLesson


@pytest.mark.parametrize("source_type", ["manual", "owner", "human"])
def test_static_rules_survive_missing_learning(monkeypatch, source_type):
    run_migrations()
    lesson = PSLesson(
        source_type=source_type, source_id="owner-rule", category="risk",
        severity="high", summary="static rule", status="applied",
        recommendation_json='{"action":"block_symbol","symbol":"BTC/USDT"}',
    )
    repo._insert("ps_lessons", lesson.model_dump())
    proposal = SimpleNamespace(id="proposal", symbol="BTC/USDT", direction="long")
    original_import = builtins.__import__

    def no_learning(name, *args, **kwargs):
        if name.startswith("aletheia.learning"):
            raise ModuleNotFoundError("optional Learning absent", name=name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_learning)
    assert engine._check_lesson_constraints(proposal) == [
        f"lesson_block_symbol:{lesson.lesson_id}:BTC/USDT",
    ]


def test_optional_treatment_keeps_core_authorization_and_error_rules(monkeypatch):
    run_migrations()
    lesson = PSLesson(
        source_type="automatic", source_id="automatic-rule", category="risk",
        severity="high", summary="advisory", status="applied",
        recommendation_json='{"action":"block_symbol","symbol":"BTC/USDT"}',
    )
    repo._insert("ps_lessons", lesson.model_dump())
    proposal = SimpleNamespace(id="proposal", symbol="BTC/USDT", direction="long")
    monkeypatch.setattr(engine, "_lesson_treatment", None)
    assert engine._check_lesson_constraints(proposal) == ["lesson_check_error:internal_failure"]
    monkeypatch.setattr(engine, "_lesson_treatment", lambda *_: False)
    assert engine._check_lesson_constraints(proposal) == []
    monkeypatch.setattr(engine, "_lesson_treatment", lambda *_: True)
    assert engine._check_lesson_constraints(proposal) == [
        f"lesson_block_symbol:{lesson.lesson_id}:BTC/USDT",
    ]
    proposal.id = ""
    assert engine._check_lesson_constraints(proposal) == ["lesson_check_error:internal_failure"]


@pytest.mark.parametrize("entrypoint", ["web", "cli"])
@pytest.mark.parametrize("available", [False, True])
def test_bootstrap_binds_optional_treatment_without_loading_experiments(entrypoint, available):
    script = f"available = {available!r}\n" + '''
import sys
class MissingTreatment:
    def find_spec(self, fullname, path=None, target=None):
        if not available and fullname == "aletheia.learning.risk_evidence":
            raise ModuleNotFoundError("optional treatment absent", name=fullname)
sys.meta_path.insert(0, MissingTreatment())
from aletheia.risk import engine
assert engine._lesson_treatment is None
'''
    script += '\nfrom aletheia.server.app import app\n' if entrypoint == "web" else '\nfrom aletheia import main\nmain._configure_runtime()\n'
    script += '''
if available:
    from aletheia.learning.risk_evidence import authorize_lesson_treatment
    assert engine._lesson_treatment is authorize_lesson_treatment
else:
    assert engine._lesson_treatment is None
assert "aletheia.learning.experiments" not in sys.modules
'''
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
