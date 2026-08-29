"""Read-only Learning CLI surfaces are machine-readable and honest at zero."""

import json

from typer.testing import CliRunner

from aletheia.main import app


runner = CliRunner()


def test_learning_knowledge_zero_and_missing_inspect_are_explicit():
    listed = runner.invoke(app, ["learning", "knowledge", "list"])
    assert listed.exit_code == 0, listed.output
    payload = json.loads(listed.stdout)
    assert payload["current_qualified_n"] == 0
    assert payload["items"] == []
    assert payload["production_economic_contribution_state"] == "none"
    assert payload["fixture_evidence_is_not_production_proof"] is True

    inspected = runner.invoke(
        app, ["learning", "knowledge", "inspect", "missing-hypothesis"],
    )
    assert inspected.exit_code == 0, inspected.output
    detail = json.loads(inspected.stdout)
    assert detail["availability"] == "unavailable"
    assert detail["current"] == []
    assert detail["history"] == []


def test_learning_nomination_yield_and_meta_commands_are_read_only():
    from aletheia.learning.hypotheses import publish_hypothesis_nomination
    from tests.test_learning_hypothesis_nominations import _envelope

    nomination = publish_hypothesis_nomination(_envelope("cli-bundle"))
    listed = runner.invoke(app, ["learning", "nominations", "list"])
    inspected = runner.invoke(
        app, ["learning", "nominations", "inspect", nomination.nomination_id],
    )
    yield_result = runner.invoke(
        app,
        [
            "learning", "yield", "report",
            "--start", "2026-08-01T00:00:00+00:00",
            "--end", "2026-09-01T00:00:00+00:00",
        ],
    )
    meta = runner.invoke(
        app,
        [
            "learning", "meta-recommendations",
            "--start", "2026-08-01T00:00:00+00:00",
            "--end", "2026-09-01T00:00:00+00:00",
        ],
    )

    for result in (listed, inspected, yield_result, meta):
        assert result.exit_code == 0, result.output
    assert json.loads(listed.stdout)["items"][0]["nomination_id"] == nomination.nomination_id
    assert json.loads(inspected.stdout)["availability"] == "available"
    assert json.loads(yield_result.stdout)["current_economic_knowledge_n"] == 0
    assert json.loads(meta.stdout)["advisory_only"] is True
