"""Optional Learning cannot remove deterministic Core sizing."""

from pathlib import Path
import subprocess
import sys

import pytest


def test_sizing_evidence_falls_back_without_learning():
    script = '''
import sys
class MissingLearning:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "aletheia.learning" or fullname.startswith("aletheia.learning."):
            raise ModuleNotFoundError("Learning absent", name=fullname)
sys.meta_path.insert(0, MissingLearning())
from aletheia.trading.sizing_policy import resolve_learned_notional
for learned in (25.0, 31.0):
    result = resolve_learned_notional(
        baseline_notional=25.0, learned_notional=learned, symbol="BTC/USDT",
        direction="long", regime_bucket="bull", candidate_key=None,
        outcome_candidate_key=None, opportunity_id=None,
    )
    assert result["actual_notional"] == 25.0
    assert result["learned_notional"] == learned
    assert result["authority_applied"] is False
    assert result["experiment_id"] is None and result["exposure_id"] is None
    assert result["reason"] == ("no_learned_delta" if learned == 25 else "sizing_learning_extension_unavailable")
from aletheia.config import loader
from aletheia.config.schema import AletheiaConfig
from aletheia.trading.sizing import vol_target_size
cfg = AletheiaConfig()
cfg.trading.vol_sizing_enabled = False
cfg.trading.persona_sizing_enabled = False
loader.load_config = lambda: cfg
trace = {}
actual = vol_target_size("BTC/USDT", sizing_trace=trace)
assert actual == round(trace["baseline_notional"], 2)
assert trace["authority_applied"] is False
'''
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr


def test_optional_evidence_cannot_grant_persona_size_authority(monkeypatch):
    from aletheia.trading import sizing_policy

    kwargs = dict(
        baseline_notional=25.0, learned_notional=31.0, symbol="BTC/USDT",
        direction="long", regime_bucket="bull", candidate_key=None,
        outcome_candidate_key=None, opportunity_id=None,
    )
    monkeypatch.setattr(sizing_policy, "_learned_notional_resolver", None)
    baseline = sizing_policy.resolve_learned_notional(**kwargs)
    for change in ({"actual_notional": 31.0}, {"authority_applied": True}, {"baseline_notional": 31.0}):
        monkeypatch.setattr(sizing_policy, "_learned_notional_resolver", lambda **_: {**baseline, **change})
        result = sizing_policy.resolve_learned_notional(**kwargs)
        assert result["actual_notional"] == 25.0
        assert result["authority_applied"] is False
        assert result["reason"] == "adapter_error"


@pytest.mark.parametrize("entrypoint", ["web", "cli"])
@pytest.mark.parametrize("available", [False, True])
def test_sizing_bootstrap_binds_without_resolving_evidence(entrypoint, available):
    script = f"available = {available!r}\n" + '''
import sys
class MissingLearning:
    def find_spec(self, fullname, path=None, target=None):
        if not available and fullname == "aletheia.learning.sizing_evidence":
            raise ModuleNotFoundError("optional Learning absent", name=fullname)
sys.meta_path.insert(0, MissingLearning())
from aletheia.trading import sizing_policy
assert sizing_policy._learned_notional_resolver is None
if available:
    from aletheia.learning import sizing_evidence
    def forbidden(**kwargs):
        raise AssertionError("bootstrap resolved sizing evidence")
    sizing_evidence.resolve_sizing_evidence = forbidden
'''
    script += '\nfrom aletheia.server.app import app\n' if entrypoint == "web" else '\nfrom aletheia import main\nmain._configure_runtime()\n'
    script += '\nassert sizing_policy._learned_notional_resolver is (forbidden if available else None)\n'
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
