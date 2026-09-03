from __future__ import annotations

from pathlib import Path

import pytest
import yaml


pytestmark = pytest.mark.tier_unit

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "ci.yml"
RELEASE_WORKFLOW_PATH = ROOT / ".github" / "workflows" / "release-please.yml"


def _workflow() -> dict:
    return yaml.load(WORKFLOW_PATH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_automatic_pull_request_remote_tests_are_disabled() -> None:
    workflow = _workflow()
    triggers = workflow["on"]

    assert set(triggers) == {"workflow_dispatch"}
    assert "pull_request" not in triggers
    assert "merge_group" not in triggers
    assert "push" not in triggers


def test_release_preparation_does_not_dispatch_ci() -> None:
    release_workflow = RELEASE_WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "workflow_id: 'ci.yml'" not in release_workflow
    assert "actions.createWorkflowDispatch" not in release_workflow
    assert "labels: ['do-not-auto-merge']" in release_workflow


def test_manual_full_validation_keeps_repository_test_surfaces_available() -> None:
    workflow = _workflow()
    jobs = workflow["jobs"]

    assert set(jobs) == {"full-ci-verdict"}
    job = jobs["full-ci-verdict"]
    assert job["name"] == "Full CI verdict"
    assert job["runs-on"] == ["pr-ephemeral"]

    steps = {step["name"]: step for step in job["steps"]}
    assert "uv run ruff check aletheia/ tests/" in steps["Lint Python"]["run"]
    python_tests = steps["Run Python regression suite serially"]["run"]
    assert "find tests -type f" in python_tests
    assert "-name 'test_*.py'" in python_tests
    assert "-name 'spec_*.py'" in python_tests
    assert 'uv run pytest "${test_files[@]:offset:batch_size}"' in python_tests

    dashboard = steps["Test and build dashboard"]["run"]
    assert "npm ci" in dashboard
    assert "npm run check:settings-types" in dashboard
    assert "npm test" in dashboard
    assert "npm run build" in dashboard

    operator = steps["Test and build Operator Shell"]["run"]
    assert "npm ci" in operator
    assert "npm test" in operator
    assert "npm run build" in operator

    assert steps["Test native host"]["run"] == "cargo test --locked --all-targets"


def test_manual_validation_is_serial_and_has_no_test_matrix() -> None:
    job = _workflow()["jobs"]["full-ci-verdict"]

    assert "strategy" not in job
    python_command = job["steps"][5]["run"]
    assert "-n " not in python_command
