"""Optional research feedback cannot own Core closure or delivery state."""

from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('entrypoint,available', [
    ('core', False), ('web', False), ('cli', False), ('web', True), ('cli', True),
])
def test_core_outcome_import_and_lazy_bootstrap_binding(entrypoint, available):
    blocked = set() if available else {'aletheia.learning.outcome_feedback', 'aletheia.counterfactual.trades'}
    script = f'blocked = {blocked!r}\n' + """
import sys
class MissingFeedback:
    def find_spec(self, fullname, path=None, target=None):
        if fullname in blocked:
            raise ModuleNotFoundError('optional research absent', name=fullname)
sys.meta_path.insert(0, MissingFeedback())
from aletheia.portfolio import pnl, outcome_hooks
assert not hasattr(pnl, '_close_serenity_feedback')
assert not any(name in sys.modules for name in blocked)
try:
    outcome_hooks._execute_hook('shadow_trades', {'id': 'selected'})
except RuntimeError as exc:
    assert str(exc) == 'outcome_feedback_unavailable'
else:
    raise AssertionError('unbound feedback fabricated delivery')
"""
    if entrypoint == 'web':
        script += '\nfrom aletheia.server.app import app\n'
    elif entrypoint == 'cli':
        script += '\nfrom aletheia import main\nmain._configure_runtime()\n'
    if available:
        script += '\nfrom aletheia.learning.outcome_feedback import execute_outcome_feedback\nassert outcome_hooks._feedback_executor is execute_outcome_feedback\n'
    else:
        script += '\nassert outcome_hooks._feedback_executor is None\n'
    script += '\nassert "aletheia.counterfactual.trades" not in sys.modules\n'
    result = subprocess.run(
        [sys.executable, '-c', script], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr


def test_absent_feedback_preserves_close_and_replays_under_core_lease(tmp_path, monkeypatch):
    from tests.specs.spec_pipeline_outcome_feedback import _setup_db
    from tests.test_outcome_closure import _position, _verified_result
    from aletheia.portfolio import outcome_closure, outcome_hooks
    from aletheia.storage import repositories as repo

    _setup_db(tmp_path, monkeypatch)
    monkeypatch.setattr(outcome_hooks, '_feedback_executor', None)
    position = _position(execution_mode='bitget_demo')
    repo.insert_position(position)
    outcome_closure.close_position_verified(position.id, _verified_result(position))
    before = repo.get_position(position.id)
    assert before['status'] == 'closed' and before['outcome_eligible']
    [job] = repo.list_outcome_hook_jobs(position_id=position.id)
    assert job['status'] == 'failed'
    assert 'outcome_feedback_unavailable' in job['last_error']
    calls = []

    def feedback(hook_type, row):
        assert repo.get_db().in_transaction
        [current] = repo.list_outcome_hook_jobs(position_id=position.id)
        assert current['status'] == 'processing' and current['claim_token']
        calls.append((hook_type, row['id']))

    monkeypatch.setattr(outcome_hooks, '_feedback_executor', feedback)
    result = outcome_hooks.process_outcome_hook_jobs(position_id=position.id, now='2100-01-01T00:00:00+00:00')
    assert result['completed'] == 1
    assert calls == [('shadow_trades', position.id)]
    assert repo.get_position(position.id) == before
    assert outcome_hooks.process_outcome_hook_jobs(position_id=position.id)['claimed'] == 0
