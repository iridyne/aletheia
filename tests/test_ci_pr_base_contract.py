from __future__ import annotations

from pathlib import Path

import pytest
import yaml


pytestmark = pytest.mark.tier_unit

WORKFLOW_PATH = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


def _workflow() -> dict:
    return yaml.load(WORKFLOW_PATH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _review_step(name: str) -> dict:
    return next(
        step
        for step in _workflow()["jobs"]["review-core"]["steps"]
        if step.get("name") == name
    )


def test_pull_request_review_attribution_uses_current_merge_ref_base() -> None:
    detect = str(_review_step("Detect affected review surfaces")["run"])

    assert 'base="$(git rev-parse HEAD^1)"' in detect
    assert 'merge_pr_head="$(git rev-parse HEAD^2)"' in detect
    assert 'expected_pr_head="${{ github.event.pull_request.head.sha }}"' in detect
    assert 'echo "base=$base" >> "$GITHUB_OUTPUT"' in detect
    assert "github.event.pull_request.base.sha" not in detect
    assert 'git diff --quiet "$base"...HEAD -- .github/workflows/ci.yml' in detect


def test_secret_scan_reuses_the_same_resolved_pull_request_base() -> None:
    scan = str(_review_step("Scan the pull-request commit range for leaked secrets")["run"])

    assert 'pr_base="${{ steps.changes.outputs.base }}"' in scan
    assert 'log_opts="$pr_base..HEAD"' in scan
    assert 'log_opts="HEAD^..HEAD"' in scan
    assert 'gitleaks" git --redact --log-opts="$log_opts"' in scan
    assert "github.event.pull_request.base.sha" not in scan


def test_current_base_contract_runs_in_the_review_invariant_gate() -> None:
    verify = str(_review_step("Verify architecture and CI invariants")["run"])

    assert "tests/test_ci_pr_base_contract.py" in verify
