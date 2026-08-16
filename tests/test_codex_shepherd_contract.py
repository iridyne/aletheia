from __future__ import annotations

from pathlib import Path

import pytest
import yaml


pytestmark = pytest.mark.tier_unit

WORKFLOW_PATH = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "codex-shepherd.yml"


def _workflow() -> dict:
    return yaml.load(WORKFLOW_PATH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_codex_shepherd_is_event_driven_and_least_privilege() -> None:
    workflow = _workflow()
    triggers = workflow["on"]
    assert triggers["pull_request_target"]["types"] == ["synchronize"]
    assert triggers["pull_request_review"]["types"] == ["submitted"]
    assert triggers["issue_comment"]["types"] == ["created"]
    assert triggers["workflow_run"]["workflows"] == ["Aletheia CI"]
    assert triggers["workflow_run"]["types"] == ["completed"]

    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["shepherd"]
    assert job["runs-on"] == "ubuntu-24.04"
    assert job["permissions"] == {
        "actions": "read",
        "contents": "read",
        "issues": "write",
        "pull-requests": "read",
    }
    assert workflow["concurrency"]["cancel-in-progress"] == "false"

    step = job["steps"][0]
    assert step["uses"].startswith("actions/github-script@")
    assert "actions/checkout" not in str(job)
    script = step["with"]["script"]
    assert "github.rest.pulls.merge" not in script
    assert "github.rest.repos.createOrUpdateFileContents" not in script
    assert "github.rest.git.updateRef" not in script
    assert "actions: write" not in str(job)


def test_codex_shepherd_binds_every_callback_to_the_exact_current_head() -> None:
    script = _workflow()["jobs"]["shepherd"]["steps"][0]["with"]["script"]

    assert "const expectedHeadSha = pullRequest.head.sha" in script
    assert "context.payload.review.commit_id !== expectedHeadSha" in script
    assert "context.payload.workflow_run.head_sha !== expectedHeadSha" in script
    assert "const reviewedCommitPattern" in script
    assert "github.rest.repos.getCommit" in script
    assert "reviewedCommitSha !== expectedHeadSha" in script
    assert "review.commit_id === expectedHeadSha" in script
    assert "review.state !== 'DISMISSED'" in script

    assert "github.paginate(github.rest.pulls.listReviews" in script
    assert "github.paginate(github.rest.issues.listComments" in script
    assert "github.paginate(github.rest.pulls.listFiles" in script
    assert "per_page: 100" in script


def test_codex_shepherd_repair_loop_is_idempotent_bounded_and_new_head_driven() -> None:
    script = _workflow()["jobs"]["shepherd"]["steps"][0]["with"]["script"]

    assert "const maxRepairAttempts = 3" in script
    assert "kind=(finding|ci) attempt=(\\d+)" in script
    assert "const exactHeadRepair" in script
    assert "repairMarkers.length >= maxRepairAttempts" in script
    assert "labels: ['do-not-auto-merge']" in script
    assert "reason=repair-budget" in script
    assert "a new head SHA is the only repair-success signal" in script

    assert "@codex review" in script
    assert "@codex fix all review findings" in script
    assert "@codex fix the CI failures" in script
    assert "kind=review" in script
    assert "kind=${kind} attempt=${attempt}" in script


def test_codex_shepherd_serializes_review_findings_before_ci_repair() -> None:
    script = _workflow()["jobs"]["shepherd"]["steps"][0]["with"]["script"]

    review_handler = script.index("if (context.eventName === 'pull_request_review')")
    finding_repair = script.index("'finding'", review_handler)
    issue_handler = script.index("if (context.eventName === 'issue_comment')", finding_repair)
    issue_requires_clean = script.index("!connectorCleanExactHead", issue_handler)
    workflow_handler = script.index("if (context.eventName === 'workflow_run')", issue_requires_clean)
    finding_precedence = script.index("if (exactHeadReviews.length !== 0)", workflow_handler)
    wait_for_completion = script.index("if (!connectorCleanExactHead)", finding_precedence)
    ci_repair = script.index("'ci'", wait_for_completion)

    assert review_handler < finding_repair < issue_handler < issue_requires_clean
    assert issue_requires_clean < workflow_handler < finding_precedence < wait_for_completion < ci_repair
    assert "CI failed before exact-head Codex completion; wait for the Connector callback" in script
    assert "exact-head review findings exist; review repair path owns this head" in script


def test_codex_shepherd_fails_closed_on_repository_and_trading_trust_boundaries() -> None:
    script = _workflow()["jobs"]["shepherd"]["steps"][0]["with"]["script"]

    assert "pullRequest.base.ref !== 'master'" in script
    assert "pullRequest.draft" in script
    assert "pullRequest.head.repo?.full_name" in script
    assert "release-please--branches--master" in script
    assert "do-not-auto-merge" in script

    for controlled_path in (
        "aletheia/risk/",
        "aletheia/execution/",
        "aletheia/exchanges/bitget/auth.py",
        "aletheia/exchanges/bitget/client.py",
        "aletheia/config/secrets.py",
        "aletheia/agent_runtime/permissions.py",
        "aletheia/agent_runtime/tool_registry.py",
        "aletheia/agent_shell/registry.py",
        "aletheia/files/jsonl.py",
        "aletheia/server/routes/live.py",
    ):
        assert controlled_path in script

    assert "if (touchesControlledPath)" in script
    assert "controlled trading/trust path requires owner-scoped repair" in script
