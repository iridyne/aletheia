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
def test_paired_context_failures_exit_four_in_json(monkeypatch, command, runner_name, command_args):
    from aletheia.economics import context_effectiveness

    async def stub(**_kwargs):
        return {
            "status": "invalid_pair",
            "errors": {"control": "provider_failed"},
            "metrics": {"invalid_reasons": ["control_not_schema_valid"]},
        }

    monkeypatch.setattr(context_effectiveness, runner_name, stub)
    result = CliRunner().invoke(audit_app, [command, *command_args, "--json"])

    assert result.exit_code == 4
    assert json.loads(result.stdout)["status"] == "invalid_pair"


@pytest.mark.parametrize("command, runner_name, command_args", _COMMANDS)
def test_paired_context_review_ready_exits_zero(monkeypatch, command, runner_name, command_args):
    from aletheia.economics import context_effectiveness

    async def stub(**_kwargs):
        return {"status": "awaiting_owner_review", "evaluation_pair_id": "pair-1"}

    monkeypatch.setattr(context_effectiveness, runner_name, stub)
    result = CliRunner().invoke(audit_app, [command, *command_args, "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["status"] == "awaiting_owner_review"
