"""Regression cases for the concrete provenance/ownership holes found in #3608."""

import ast

import pytest

from tests.audit.test_audit_events_have_actor_source import literal_audit_failures
from tests.audit.test_position_lifecycle_story_readonly import _repository_access_violations


def test_keyword_audit_payload_cannot_hide_behind_valid_positional_payload():
    tree = ast.parse(
        'write_audit({"actor": "system", "source": "cli"})\n'
        'write_audit(event={"source": "cli"})\n'
    )
    checked, failures = literal_audit_failures(tree)
    assert checked == 2
    assert len(failures) == 1
    assert failures[0].startswith("2:") and "actor" in failures[0]
    assert literal_audit_failures(ast.parse(
        'write_audit(event={"actor": "system", "source": "cli"})'
    )) == (1, [])


def test_story_repository_boundary_rejects_nonprefix_writers_and_aliases(tmp_path):
    source = tmp_path / "reader.py"
    for code in (
        'from aletheia.storage import repositories as repo\nrepo.claim_position_close("x")',
        'import aletheia.storage.repositories as storage\nstorage.prune_pi_decisions(1)',
        'from aletheia.storage.repositories import claim_position_close as claim\nclaim("x")',
    ):
        source.write_text(code)
        failures = _repository_access_violations(source)
        assert len(failures) == 1
        assert "claim_position_close" in failures[0] or "prune_pi_decisions" in failures[0]
    source.write_text('from aletheia.storage import repositories as repo\nrepo.get_db()')
    assert _repository_access_violations(source) == []


def test_nonexported_seam_callback_cannot_mask_missing_local_failure_telemetry(tmp_path, monkeypatch):
    from tests import module_source
    from tests.specs.spec_agent_contracts import test_registered_scheduler_jobs_record_swallowed_failures

    directory = tmp_path / "core/aletheia/agent_runtime"
    directory.mkdir(parents=True)
    (directory / "scheduler.py").write_text(
        'from aletheia.agent_runtime.scheduler_retention import *\n'
        'def _run_probe():\n    pass\n'
        'def init_scheduler():\n    scheduler.add_job(_guarded("probe", _run_probe))\n'
    )
    (directory / "scheduler_retention.py").write_text(
        '__all__ = []\ndef _run_probe():\n    _record_job_failure("probe", "failed")\n'
    )
    monkeypatch.setattr(module_source, "ROOT", tmp_path)
    with pytest.raises(AssertionError, match="probe->_run_probe"):
        test_registered_scheduler_jobs_record_swallowed_failures()
