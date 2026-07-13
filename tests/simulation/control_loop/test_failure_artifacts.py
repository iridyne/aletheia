"""Failure packs are automatic for ordinary fixed-scenario assertions."""

import json
import sqlite3

import pytest

from aletheia.simulation.control_loop import ControlLoopHarness
from aletheia.simulation.control_loop.artifacts import REQUIRED_ARTIFACTS


def test_unhandled_scenario_assertion_writes_complete_replay_pack(tmp_path):
    artifact_root = tmp_path / "artifacts"
    run_id = "ordinary-assertion"

    with pytest.raises(AssertionError, match="deliberate fixed-scenario failure"):
        with ControlLoopHarness(
            tmp_path / "run",
            artifact_root=artifact_root,
            run_id=run_id,
            seed=20260713,
            reproduce="uv run pytest tests/simulation/control_loop/test_failure_artifacts.py -q",
        ) as harness:
            harness.seed_candidates(high=1, medium=2)
            raise AssertionError("deliberate fixed-scenario failure")

    evidence = artifact_root / run_id
    assert {path.name for path in evidence.iterdir()} == REQUIRED_ARTIFACTS
    assert (evidence / "seed.txt").read_text().strip() == "20260713"
    failure = json.loads((evidence / "invariant_failure.json").read_text())
    assert failure == {
        "message": "deliberate fixed-scenario failure",
        "type": "AssertionError",
    }
    run = json.loads((evidence / "run.json").read_text())
    assert run["operation_count"] == 1
    assert run["run_id"] == run_id
    config_text = (evidence / "final_config.yaml").read_text()
    assert "api_key" not in config_text
    assert "secret" not in config_text
    with sqlite3.connect(evidence / "app.db") as db:
        assert db.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0] == 3
