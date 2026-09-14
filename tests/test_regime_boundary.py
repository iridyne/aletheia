"""Core consumes optional Regime evidence without importing its implementation."""

from pathlib import Path
import subprocess
import sys

import pytest


def test_risk_read_does_not_import_optional_regime():
    script = '''
import sys
attempted = []
class MissingRegime:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "aletheia.regime" or fullname.startswith("aletheia.regime."):
            attempted.append(fullname)
            raise ModuleNotFoundError("optional Regime absent", name=fullname)
sys.meta_path.insert(0, MissingRegime())
from aletheia.risk.demo_entry_quality import load_published_regime
assert load_published_regime() == (None, None)
try:
    load_published_regime(fail_open=False)
except Exception:
    pass
else:
    raise AssertionError("missing Regime must remain a strict read failure")
assert not attempted, attempted
'''
    result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("entrypoint", ["web", "cli"])
@pytest.mark.parametrize("available", [False, True])
def test_regime_binding_never_queries_or_collects(entrypoint, available):
    script = f"available = {available!r}\n" + '''
import sys
class MissingRegime:
    def find_spec(self, fullname, path=None, target=None):
        if not available and fullname == "aletheia.regime":
            raise ModuleNotFoundError("optional Regime absent", name=fullname)
sys.meta_path.insert(0, MissingRegime())
from aletheia.risk import demo_entry_quality as quality
from aletheia.universe import service
def forbidden(*args):
    raise AssertionError("bootstrap queried or collected Regime")
if available:
    from aletheia.regime import detector
    detector.read_published_regime = forbidden
    detector.collect_regime_record = forbidden
'''
    script += '\nfrom aletheia.server.app import app\n' if entrypoint == "web" else '\nfrom aletheia import main\nmain._configure_runtime()\n'
    script += '\nassert quality._published_regime_reader is (forbidden if available else None)\nassert service._regime_refresher is (forbidden if available else None)\n'
    result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_universe_retains_context_when_regime_persistence_fails(tmp_path, monkeypatch):
    from aletheia.storage import repositories as repo
    from tests.test_candidate_selection_utility import test_refresh_freezes_detected_regime_and_short_control_identity

    def unavailable(record):
        raise RuntimeError("regime persistence unavailable")
    monkeypatch.setattr(repo, "insert_market_regime", unavailable)
    test_refresh_freezes_detected_regime_and_short_control_identity(tmp_path, monkeypatch)


def test_universe_keeps_publishing_without_optional_regime(tmp_path, monkeypatch):
    from aletheia.universe import service
    from tests.test_candidate_selection_utility import test_empty_universe_bootstraps_candidate_and_planning_without_slow_cache

    monkeypatch.setattr(service, "_regime_refresher", None)
    test_empty_universe_bootstraps_candidate_and_planning_without_slow_cache(tmp_path, monkeypatch)


def test_published_read_keeps_one_snapshot_during_concurrent_replacement(monkeypatch):
    from types import SimpleNamespace
    from aletheia.data import freshness
    from aletheia.regime import detector
    from aletheia.risk.demo_entry_quality import load_published_regime

    first = detector.MarketRegime(state="bull", confidence=0.8)
    replacement = detector.MarketRegime(state="sideways", confidence=0.2)
    monkeypatch.setattr(detector, "_published_regime", first)
    def assess(*args, **kwargs):
        detector._published_regime = replacement
        return SimpleNamespace(usable=True)
    monkeypatch.setattr(freshness, "assess_freshness", assess)
    assert load_published_regime(fail_open=False) == ("bull", 0.8)
