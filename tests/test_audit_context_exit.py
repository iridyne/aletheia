from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from aletheia.cli.audit_cmd import audit_app


_COMMANDS = (
    ("learning-context-b1", "run_learning_b_pair", ["--review-output", "/tmp/review.json"]),
    ("technical-context-check", "run_technical_context_pair", ["--review-output", "/tmp/review.json"]),
    ("context-ablation", "run_context_ablation_pair", [
        "--fixture", "/tmp/fixture.json", "--modality", "news", "--review-output", "/tmp/review.json",
    ]),
)


@pytest.mark.parametrize("command, runner_name, command_args", _COMMANDS)
@pytest.mark.parametrize("output_json", [False, True])
@pytest.mark.parametrize("report, detail", [
    ({"status": "blocked", "blocker": "provider unavailable"}, "provider unavailable"),
    ({
        "status": "invalid_pair",
        "errors": {"control": "provider_failed"},
        "metrics": {"invalid_reasons": ["control_not_schema_valid"]},
    }, "control_not_schema_valid"),
])
def test_paired_context_failures_exit_four(
    monkeypatch, command, runner_name, command_args, output_json, report, detail,
):
    from extensions.research import context_effectiveness

    async def stub(**_kwargs):
        return report

    monkeypatch.setattr(context_effectiveness, runner_name, stub)
    result = CliRunner().invoke(
        audit_app,
        [command, *command_args, *(["--json"] if output_json else [])],
    )

    assert result.exit_code == 4
    if output_json:
        assert json.loads(result.stdout)["status"] == report["status"]
    else:
        assert report["status"] in result.stdout
        assert detail in result.stdout


@pytest.mark.parametrize("command, runner_name, command_args", _COMMANDS)
@pytest.mark.parametrize("output_json", [False, True])
def test_paired_context_review_ready_exits_zero(
    monkeypatch, command, runner_name, command_args, output_json,
):
    from extensions.research import context_effectiveness

    async def stub(**_kwargs):
        return {
            "status": "awaiting_owner_review",
            "evaluation_pair_id": "pair-1",
            "review_packet": "/tmp/review.json",
        }

    monkeypatch.setattr(context_effectiveness, runner_name, stub)
    result = CliRunner().invoke(
        audit_app,
        [command, *command_args, *(["--json"] if output_json else [])],
    )

    assert result.exit_code == 0
    if output_json:
        assert json.loads(result.stdout)["status"] == "awaiting_owner_review"
    else:
        assert "awaiting_owner_review" in result.stdout
        assert "/tmp/review.json" in result.stdout
