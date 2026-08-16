from __future__ import annotations

from pathlib import Path

import pytest
import yaml


pytestmark = pytest.mark.tier_unit

WORKFLOW_PATH = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "codex-shepherd.yml"


def _workflow() -> dict:
    return yaml.load(WORKFLOW_PATH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _script() -> str:
    return _workflow()["jobs"]["shepherd"]["steps"][0]["with"]["script"]


def test_codex_shepherd_is_event_driven_and_least_privilege() -> None:
    workflow = _workflow()
    triggers = workflow["on"]
    assert triggers["pull_request_target"]["types"] == [
        "opened",
        "reopened",
        "ready_for_review",
        "synchronize",
        "unlabeled",
    ]
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


def test_codex_shepherd_binds_callbacks_and_control_markers_to_trusted_identity() -> None:
    script = _script()

    assert "const expectedHeadSha = pullRequest.head.sha" in script
    assert "context.payload.review.commit_id !== expectedHeadSha" in script
    assert "context.payload.workflow_run.head_sha !== expectedHeadSha" in script
    assert "const reviewedCommitPattern" in script
    assert "github.rest.repos.getCommit" in script
    assert "reviewedCommitSha !== expectedHeadSha" in script
    assert "review.commit_id === expectedHeadSha" in script
    assert "review.state !== 'DISMISSED'" in script

    assert "github.paginate(github.rest.pulls.listFiles" in script
    assert "github.paginate(github.rest.pulls.listReviews" in script
    assert "github.paginate(github.rest.issues.listComments" in script
    assert "per_page: 100" in script

    assert "const shepherdLogin = 'github-actions[bot]'" in script
    assert "comment.user?.login === shepherdLogin" in script
    assert "const shepherdComments" in script
    assert "shepherdComments.flatMap" in script


def test_codex_shepherd_self_change_is_fail_closed_from_trusted_base_workflow() -> None:
    script = _script()

    assert "const shepherdPath = '.github/workflows/codex-shepherd.yml'" in script
    assert "const changesShepherdAuthority" in script
    assert "[filename, previous_filename].includes(shepherdPath)" in script
    assert "if (changesShepherdAuthority)" in script
    assert "labels: ['do-not-auto-merge']" in script
    assert "reason=shepherd-self-change" in script
    assert "requires explicit owner merge" in script
    assert "Re-apply the hold on `unlabeled` events" in script

    self_change = script.index("if (changesShepherdAuthority)")
    ordinary_hold = script.index("do-not-auto-merge holds this pull request", self_change)
    review_request = script.index("@codex review", ordinary_hold)
    assert self_change < ordinary_hold < review_request


def test_codex_shepherd_requests_exact_head_review_but_does_not_claim_repair() -> None:
    script = _script()

    assert "@codex review" in script
    assert "kind=review" in script
    assert "const exactHeadReviewRequest" in script
    assert "!exactHeadReviewRequest" in script
    assert "!connectorCleanExactHead" in script

    # Runtime evidence showed the current Codex Cloud task can create a local
    # repair commit but cannot push it back to this repository. Until that
    # permission/executor exists, Shepherd must hold rather than fake autonomy.
    assert "@codex fix" not in script
    assert "const holdForOwner" in script
    assert "labels: ['do-not-auto-merge']" in script
    assert "Codex Cloud repair is not authorized to push back" in script
    assert "reason=${reason}" in script
    assert "review-finding" in script
    assert "ci-failure" in script


def test_codex_shepherd_serializes_findings_before_ci_failure_handoff() -> None:
    script = _script()

    review_handler = script.index("if (context.eventName === 'pull_request_review')")
    review_hold = script.index("'review-finding'", review_handler)
    issue_handler = script.index("if (context.eventName === 'issue_comment')", review_hold)
    issue_requires_clean = script.index("!connectorCleanExactHead", issue_handler)
    workflow_handler = script.index("if (context.eventName === 'workflow_run')", issue_requires_clean)
    finding_precedence = script.index("if (exactHeadReviews.length !== 0)", workflow_handler)
    wait_for_completion = script.index("if (!connectorCleanExactHead)", finding_precedence)
    ci_hold = script.index("'ci-failure'", wait_for_completion)

    assert review_handler < review_hold < issue_handler < issue_requires_clean
    assert issue_requires_clean < workflow_handler < finding_precedence < wait_for_completion < ci_hold
    assert "review repair takes precedence over CI repair" in script
    assert "CI failed before exact-head Codex completion; wait for the Connector callback" in script


def test_codex_shepherd_only_holds_actionable_current_ci_failures() -> None:
    script = _script()

    assert "const repairableCiConclusions = new Set(['failure', 'timed_out'])" in script
    assert "run.status !== 'completed'" in script
    assert "!repairableCiConclusions.has(run.conclusion)" in script

    # Select the newest Aletheia CI run first. Filtering to completed runs before
    # sorting would let an older failure win while a newer exact-head run is active.
    latest_ci = script[script.index("const latestCiRun") : script.index("if (context.eventName === 'issue_comment')")]
    assert ".filter((run) => run.name === 'Aletheia CI')" in latest_ci
    assert "run.status === 'completed'" not in latest_ci

    assert "pullRequest.base.ref !== 'master'" in script
    assert "pullRequest.draft" in script
    assert "pullRequest.head.repo?.full_name" in script
    assert "release-please--branches--master" in script
    assert "do-not-auto-merge" in script
