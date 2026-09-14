"""Shared Outcome eligibility is a Core contract, not a Learning implementation."""

from pathlib import Path
import subprocess
import sys


def test_outcome_assessment_is_core_owned_without_learning():
    script = '''
import sys
class MissingLearning:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "aletheia.learning" or fullname.startswith("aletheia.learning."):
            raise ModuleNotFoundError("Learning absent", name=fullname)
sys.meta_path.insert(0, MissingLearning())
from aletheia.analytics import outcome_semantics, skip_diagnosis
for name in ("assess_candidate_outcome", "assess_realized_position", "candidate_outcome_temporal_reason"):
    assert getattr(outcome_semantics, name).__module__ == "aletheia.analytics.outcome_semantics"
assert outcome_semantics.assess_candidate_outcome({}).eligible is False
assert outcome_semantics.candidate_outcome_temporal_reason({}, as_of_at="2026-09-01T00:00:00Z") == "unsettled"
'''
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert not (Path(__file__).resolve().parents[1] / "aletheia/learning/outcomes.py").exists()
