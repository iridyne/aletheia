import json

import pytest
from typer.testing import CliRunner

from aletheia.cli.evidence_cmd import evidence_app


@pytest.mark.parametrize("json_output", [False, True])
def test_register_epoch_fails_on_partial_registration(monkeypatch, json_output):
    monkeypatch.setattr(
        "extensions.learning.evidence_epoch.ensure_evidence_epochs",
        lambda **_: {"exit_trailing_evidence": "epoch-1"},
    )
    monkeypatch.setattr(
        "extensions.learning.evidence_epoch.evidence_epoch_summary",
        lambda: {"paths": []},
    )

    args = ["epoch", "register"] + (["--json"] if json_output else [])
    result = CliRunner().invoke(evidence_app, args)

    assert result.exit_code == 1
    if json_output:
        payload = json.loads(result.stdout)
        assert payload["complete"] is False
        assert payload["missing_paths"] == ["entry_correction_v2", "profit_loop_observability"]
    else:
        assert "Missing required evidence path(s)" in result.output


def test_register_epoch_succeeds_when_all_paths_are_registered(monkeypatch):
    from extensions.learning.evidence_epoch import EXPECTED_EVIDENCE_PATH_IDS

    monkeypatch.setattr(
        "extensions.learning.evidence_epoch.ensure_evidence_epochs",
        lambda **_: {path: f"epoch-{path}" for path in EXPECTED_EVIDENCE_PATH_IDS},
    )
    monkeypatch.setattr(
        "extensions.learning.evidence_epoch.evidence_epoch_summary",
        lambda: {"current_commit": "commit", "paths": []},
    )

    result = CliRunner().invoke(evidence_app, ["epoch", "register", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["complete"] is True
