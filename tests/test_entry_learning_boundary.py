"""Core Entry contracts remain usable without optional Learning implementation."""

import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest


def test_entry_contracts_do_not_import_concrete_learning():
    from aletheia.trading import entry_analysis

    tree = ast.parse(Path(entry_analysis.__file__).read_text())
    assert not any(
        isinstance(node, ast.ImportFrom) and (node.module or "").startswith("aletheia.learning")
        for node in ast.walk(tree)
    )


def test_absent_learning_returns_valid_empty_context_without_authority(monkeypatch):
    from aletheia.trading import entry_learning

    monkeypatch.setattr(entry_learning, "_context_loader", None)
    result = entry_learning.resolve_entry_learning_context(
        generation={"id": "generation"}, model_provider="selected", profiles=(),
        as_of_at="2026-09-01T00:00:00+00:00", mechanism="reversal",
        direction="long", market_type="perp", policy="candidate_selection_utility_v1",
        policy_epoch="epoch",
    )
    assert result.status == "unavailable"
    assert result.blocking_reasons == ("entry_learning_extension_unavailable",)
    assert result.experiment_identity is None and result.source_fingerprint is None
    assert result.metrics["intent_to_treat_net_utility_pct"] is None

    # A producer cannot bypass Core validators with model_copy or expose a raw
    # observational summary as an authorized treatment context.
    for status in ("observational", "experimental"):
        forged = result.model_copy(update={"status": status, "source_fingerprint": "facts", "blocking_reasons": ()})
        monkeypatch.setattr(entry_learning, "_context_loader", lambda **_: forged)
        with pytest.raises(ValueError):
            entry_learning.resolve_entry_learning_context(
                generation={}, model_provider="selected", profiles=(),
                as_of_at=result.as_of_at, mechanism="reversal", direction="long",
                market_type="perp", policy="candidate_selection_utility_v1", policy_epoch="epoch",
            )


def test_entry_schema_is_unchanged_by_contract_extraction():
    from aletheia.trading.entry_analysis import EntryAnalysisContext

    encoded = json.dumps(EntryAnalysisContext.model_json_schema(), sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(encoded.encode()).hexdigest() == "9d7e6b0b7ec9ea572c69453ff4864e9da1923a9873befaf6b5dd9a96513f3423"


@pytest.mark.parametrize("entrypoint", ["web", "cli"])
@pytest.mark.parametrize("available", [False, True])
def test_entry_bootstrap_is_absence_safe_and_does_not_resolve_treatment(entrypoint, available):
    script = f"available = {available!r}\n" + '''
import sys
class MissingLearning:
    def find_spec(self, fullname, path=None, target=None):
        if not available and fullname == "aletheia.learning.entry_context":
            raise ModuleNotFoundError("optional Learning absent", name=fullname)
sys.meta_path.insert(0, MissingLearning())
from aletheia.trading import entry_learning, entry_analysis
assert entry_learning._context_loader is None
if available:
    from aletheia.learning import entry_context
    assert not hasattr(entry_context, "EntryLearningContext")
    assert not hasattr(entry_context, "empty_entry_learning_context")
    def forbidden(**kwargs):
        raise AssertionError("bootstrap resolved treatment")
    entry_context.load_entry_learning_context = forbidden
'''
    script += '\nfrom aletheia.server.app import app\n' if entrypoint == "web" else '\nfrom aletheia import main\nmain._configure_runtime()\n'
    script += '\nassert entry_learning._context_loader is (forbidden if available else None)\n'
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
