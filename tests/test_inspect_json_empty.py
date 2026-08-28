import json

import pytest
from typer.testing import CliRunner

from aletheia.cli.inspect_cmd import inspect_app


@pytest.mark.parametrize(
    ("args", "patch_target", "empty_json", "human_text"),
    [
        (["regime"], "aletheia.storage.repositories.list_market_regimes", [], "no regime data"),
        (["calibration"], "aletheia.storage.repositories.calibration_stats", [], "no calibration data"),
        (["reflection"], "aletheia.agent_shell.reflection.get_last_reflection", {"status": "not_run", "available": False}, "no reflection state"),
        (["agent-status"], "aletheia.agent_shell.operator_store.get_latest_heartbeat", {"status": "not_run", "available": False}, "no heartbeat"),
    ],
)
def test_empty_inspect_commands_keep_json_and_human_outputs(
    monkeypatch, args, patch_target, empty_json, human_text
):
    monkeypatch.setattr(
        patch_target,
        lambda **_: [] if args[0] in {"regime", "calibration"} else None,
    )

    json_result = CliRunner().invoke(inspect_app, [*args, "--json"])
    assert json_result.exit_code == 0
    assert json.loads(json_result.stdout) == empty_json

    human_result = CliRunner().invoke(inspect_app, args)
    assert human_result.exit_code == 0
    assert human_text in human_result.stdout
