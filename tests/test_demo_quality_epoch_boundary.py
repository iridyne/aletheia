"""Risk owns quality verdicts; optional Learning owns its epoch persistence."""

import ast
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from aletheia.risk import demo_entry_quality as quality


def test_quality_has_no_concrete_learning_import():
    tree = ast.parse(Path(quality.__file__).read_text())
    assert not any(
        isinstance(node, ast.ImportFrom) and (node.module or "").startswith("aletheia.learning")
        or isinstance(node, ast.Import) and any(alias.name.startswith("aletheia.learning") for alias in node.names)
        for node in ast.walk(tree)
    )


def test_missing_epoch_binding_keeps_existing_fail_closed_token(monkeypatch):
    monkeypatch.setattr(quality, "_policy_epoch_writer", None)
    monkeypatch.setattr(quality, "load_published_regime", lambda **_: (None, None))
    monkeypatch.setattr(quality, "load_recent_stop_directions", lambda *_, **__: set())
    args = dict(eligibility=SimpleNamespace(), scanner_family=None, direction="long")
    assert quality.collect_demo_entry_quality_violations(execution_mode="bitget_demo", **args) == [
        "demo_entry_quality_unavailable:policy_epoch",
    ]
    assert quality.collect_demo_entry_quality_violations(execution_mode="bitget_live_limited", **args) == []
    calls = []
    monkeypatch.setattr(quality, "_policy_epoch_writer", lambda: calls.append("persist"))
    assert quality.collect_demo_entry_quality_violations(execution_mode="bitget_demo", **args) == []
    assert calls == ["persist"]


@pytest.mark.parametrize("entrypoint", ["web", "cli"])
@pytest.mark.parametrize("available", [False, True])
def test_bootstrap_binds_without_persisting_epoch(entrypoint, available):
    script = f"available = {available!r}\n" + '''
import sys
class MissingEpoch:
    def find_spec(self, fullname, path=None, target=None):
        if not available and fullname == "aletheia.learning.demo_entry_quality":
            raise ModuleNotFoundError("optional epoch absent", name=fullname)
sys.meta_path.insert(0, MissingEpoch())
from aletheia.risk import demo_entry_quality as quality
assert quality._policy_epoch_writer is None
if available:
    from aletheia.learning import demo_entry_quality as policy
    def forbidden_write():
        raise AssertionError("bootstrap persisted an epoch")
    policy.ensure_demo_entry_quality_policy_epoch = forbidden_write
'''
    script += '\nfrom aletheia.server.app import app\n' if entrypoint == "web" else '\nfrom aletheia import main\nmain._configure_runtime()\n'
    script += '''
if available:
    assert quality._policy_epoch_writer is forbidden_write
else:
    assert quality._policy_epoch_writer is None
'''
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
