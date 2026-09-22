"""Core Candidate evidence does not import optional Learning to read identity."""

from pathlib import Path
import subprocess
import sys


def test_candidate_evidence_imports_without_learning():
    script = '''
import sys
class MissingLearning:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "extensions.learning" or fullname.startswith("extensions.learning."):
            raise ModuleNotFoundError("Learning absent", name=fullname)
sys.meta_path.insert(0, MissingLearning())
from aletheia.analytics import candidate_facts, filter_evidence
from aletheia.universe import service
from aletheia.universe import candidate_selection_authority
assert filter_evidence.POLICY_EPOCH_LABEL == "opportunity_information_integrity/v4"
assert service.POLICY_EPOCH_LABEL == filter_evidence.POLICY_EPOCH_LABEL
assert not hasattr(service, "_active_paper_learning_identity")
assert not hasattr(candidate_selection_authority, "candidate_selection_overlap_block_reason")
assert not hasattr(candidate_selection_authority, "learning_candidate_selection_overlap_block_reason")
'''
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
