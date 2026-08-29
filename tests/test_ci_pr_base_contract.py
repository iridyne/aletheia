from __future__ import annotations

from pathlib import Path

import pytest
import yaml


pytestmark = pytest.mark.tier_unit

WORKFLOW_PATH = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


def _workflow() -> dict:
    return yaml.load(WORKFLOW_PATH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _job_step(job_name: str, name: str) -> dict:
    return next(
        step
        for step in _workflow()["jobs"][job_name]["steps"]
        if step.get("name") == name
    )


def test_pull_request_review_attribution_uses_current_merge_ref_base() -> None:
    plan = str(_job_step("plan", "Compute deterministic CI plan")["run"])

    assert "scripts/ci_plan.py" in plan
    assert "--github-output \"$GITHUB_OUTPUT\"" in plan
    assert _workflow()["jobs"]["plan"]["outputs"]["base_sha"] == "${{ steps.plan.outputs.base_sha }}"
    assert "github.event.pull_request.base.sha" not in plan


def test_secret_scan_reuses_the_same_resolved_pull_request_base() -> None:
    scan = str(_job_step("review-core", "Scan the pull-request commit range for leaked secrets")["run"])

    assert 'pr_base="${{ needs.plan.outputs.base_sha }}"' in scan
    assert 'log_opts="$pr_base..HEAD"' in scan
    assert 'log_opts="HEAD^..HEAD"' in scan
    assert 'gitleaks" git --redact --log-opts="$log_opts"' in scan
    assert "github.event.pull_request.base.sha" not in scan


def test_current_base_contract_runs_in_the_review_invariant_gate() -> None:
    verify = str(_job_step("review-core", "Verify architecture and CI invariants")["run"])

    assert "tests/test_ci_pr_base_contract.py" in verify
