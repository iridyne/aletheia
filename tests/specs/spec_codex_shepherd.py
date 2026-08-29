"""Executable specification for the Codex PR Shepherd control plane."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.specs.spec_meta import SpecClaim, spec_claim


pytestmark = pytest.mark.tier_spec

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "codex-shepherd.yml"
AUTO_MERGE_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "auto-merge.yml"
SELF_HOSTED_CASI = ["self-hosted", "Linux", "X64", "ubuntu-24.04", "casi"]


def _workflow() -> dict:
    return yaml.load(WORKFLOW_PATH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _script() -> str:
    return next(
        step["with"]["script"]
        for step in _workflow()["jobs"]["shepherd"]["steps"]
        if isinstance(step, dict) and "script" in step.get("with", {})
    )


def _auto_merge_source() -> str:
    return AUTO_MERGE_WORKFLOW_PATH.read_text(encoding="utf-8")


@spec_claim(
    SpecClaim(
        "AG-CODEX-SHEPHERD-01",
        "agent_contracts",
        "§PR/CI control plane",
        "L2a",
        "Codex PR Shepherd is event-driven, least-privilege, and cannot obtain branch-write or merge authority.",
        status="implemented",
    )
)
def test_codex_shepherd_is_event_driven_and_least_privilege() -> None:
    workflow = _workflow()
    triggers = workflow["on"]
    assert triggers["pull_request_target"]["types"] == ["unlabeled"]
    assert triggers["pull_request_review"]["types"] == ["submitted"]
    assert triggers["issue_comment"]["types"] == ["created"]
    assert triggers["workflow_run"]["workflows"] == [
        "Aletheia CI",
        "Aletheia Change Contract gate",
    ]
    assert triggers["workflow_run"]["types"] == ["completed"]

    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["shepherd"]
    assert job["runs-on"] == SELF_HOSTED_CASI
    assert job["permissions"] == {
        "actions": "read",
        "contents": "write",
        "issues": "write",
        "pull-requests": "write",
    }
    assert "concurrency" not in workflow
    assert job["concurrency"] == {
        "group": (
            "codex-shepherd-reconcile-${{ github.event.pull_request.number || "
            "github.event.issue.number || github.event.workflow_run.pull_requests[0].number || "
            "github.run_id }}"
        ),
        "cancel-in-progress": "true",
    }

    step = next(
        step
        for step in job["steps"]
        if isinstance(step, dict) and str(step.get("uses", "")).startswith("actions/github-script@")
    )
    assert step["uses"].startswith("actions/github-script@")
    assert "actions/checkout" in str(job)
    assert job["steps"][0]["with"] == {"ref": "master"}
    script = step["with"]["script"]
    assert "github.rest.pulls.merge" not in script
    assert "github.rest.repos.createOrUpdateFileContents" not in script
    assert "github.rest.git.updateRef" not in script
    assert "actions: write" not in str(job)

    recovery = workflow["jobs"]["metadata-ci-recovery"]
    assert recovery["runs-on"] == SELF_HOSTED_CASI
    assert recovery["concurrency"] == {
        "group": "codex-shepherd-metadata-recovery-${{ github.event.workflow_run.id || github.run_id }}",
        "cancel-in-progress": "false",
    }
    assert recovery["permissions"] == {
        "actions": "write",
        "checks": "read",
        "contents": "read",
        "pull-requests": "read",
    }
    assert "actions/checkout" not in str(recovery)
    assert "reRunWorkflowFailedJobs" in recovery["steps"][0]["with"]["script"]
    assert "pulls.merge" not in recovery["steps"][0]["with"]["script"]


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


def test_codex_shepherd_self_change_and_incomplete_file_listing_fail_closed() -> None:
    script = _script()

    assert "changedFiles.length !== pullRequest.changed_files" in script
    assert "file-list-incomplete" in script
    assert "cannot prove the authority boundary" in script

    assert "const shepherdPath = '.github/workflows/codex-shepherd.yml'" in script
    assert "const changesShepherdAuthority" in script
    assert "[filename, previous_filename].includes(shepherdPath)" in script
    assert "if (changesShepherdAuthority)" in script
    assert "labels: ['do-not-auto-merge']" in script
    assert "reason=shepherd-self-change" in script
    assert "requires explicit owner merge" in script
    assert "Re-apply the hold on `unlabeled` events" in script

    incomplete = script.index("changedFiles.length !== pullRequest.changed_files")
    self_change = script.index("if (changesShepherdAuthority)", incomplete)
    ordinary_hold = script.index("do-not-auto-merge holds this pull request", self_change)
    review_request = script.index("@codex review", ordinary_hold)
    assert incomplete < self_change < ordinary_hold < review_request

    # A prior terminal marker only deduplicates its comment. Removing the safety
    # label must still cause the next trusted callback to restore it first.
    hold_start = script.index("const holdForOwner")
    add_label = script.index("await github.rest.issues.addLabels", hold_start)
    marker_return = script.index("if (exactHeadHalt) return", add_label)
    assert hold_start < add_label < marker_return


def test_auto_merge_atomically_protects_shepherd_authority() -> None:
    source = _auto_merge_source()

    # The merge controller must recognize Shepherd as merge authority everywhere
    # it makes an authority decision. This removes an unlabeled-event race between
    # two independent workflows.
    shepherd_token = "'.github/workflows/codex-shepherd.yml'"
    authority_message = "merge-authority workflow changes require an explicit manual merge"
    assert source.count(shepherd_token) == 0
    assert "path.startsWith('.github/workflows/')" in source
    assert "'scripts/ci_plan.py'" in source
    assert "'scripts/ci_local.py'" in source
    assert source.count(authority_message) == 2

    # Every authority decision must fail closed when GitHub's files endpoint is
    # truncated. Schedule can disable directly; release and ordinary merge return.
    schedule_complete = source.index(
        "const changedFilesComplete = changedFiles.length === pullRequest.changed_files"
    )
    schedule_authority = source.index("const changesMergeAuthority", schedule_complete)
    assert "!changedFilesComplete ||" in source[schedule_authority : schedule_authority + 1400]
    assert schedule_complete < schedule_authority

    release_summary = source.index("const [pullRequestSummary] = candidates")
    release_metadata = source.index(
        "const {data: pullRequest} = await github.rest.pulls.get", release_summary
    )
    release_complete = source.index(
        "if (changedFiles.length !== pullRequest.changed_files)", release_metadata
    )
    release_authority = source.index(authority_message, release_complete)
    assert release_summary < release_metadata < release_complete < release_authority
    assert "pull_number: pullRequestSummary.number" in source[release_metadata:release_complete]
    assert "release candidate changed after discovery; owner hold remains" in source[
        release_metadata:release_complete
    ]
    assert "owner hold remains" in source[release_complete:release_authority]

    ordinary_metadata = source.index("const {data: pullRequestMetadata} = await github.rest.pulls.get")
    ordinary_complete = source.index(
        "if (changedFiles.length !== pullRequestMetadata.changed_files)", ordinary_metadata
    )
    ordinary_authority = source.index(authority_message, ordinary_complete)
    assert ordinary_metadata < ordinary_complete < ordinary_authority
    assert "pullRequestMetadata.head.sha !== expectedHeadSha" in source[
        ordinary_metadata:ordinary_complete
    ]
    assert "exact-head merge remains disabled" in source[
        ordinary_complete:ordinary_authority
    ]


def test_codex_shepherd_requests_only_planned_review_after_trusted_verdict() -> None:
    script = _script()

    assert "const reviewTriggerActions = new Set" not in script
    assert "scripts/ci_plan.py" in script
    assert "const codexRequired = ciPlan?.review?.codex_required === true" in script
    assert "trusted CI planner says Codex review is not required" in script
    assert "const labelArgs = pullRequest.labels.flatMap" in script
    assert "--trusted-label" in script
    assert "exactHeadGithubActionsCheckPassed('Trusted CI verdict')" in script
    assert "waiting for exact-head Trusted CI verdict before requesting Codex review" in script
    assert "@codex review" in script
    assert "kind=review" in script
    assert "const exactHeadReviewRequest" in script
    assert "!exactHeadReviewRequest" in script
    assert "!connectorCleanExactHead" in script
    assert "surviving trusted callback" in script

    # Runtime evidence showed the current Codex Cloud task can create a local
    # repair commit but cannot push it back to this repository. Until that
    # permission/executor exists, Shepherd must hold rather than fake autonomy.
    assert "@codex fix" not in script
    assert "const holdForOwner" in script
    assert "Codex Cloud repair is not authorized to push back" in script
    assert "review-finding" in script
    assert "ci-failure" in script


def test_codex_shepherd_retriggers_auto_merge_after_clean_review_comment() -> None:
    script = _script()
    issue_comment_handler = script[script.index("if (context.eventName === 'issue_comment')") :]

    assert "run.conclusion === 'success'" in issue_comment_handler
    assert "event_type: 'auto_merge_update'" in issue_comment_handler
    assert "expected_head_sha: expectedHeadSha" in issue_comment_handler
    assert "expected_base_sha: pullRequest.base.sha" in issue_comment_handler


def test_auto_merge_paginates_clean_codex_review_comments() -> None:
    source = _auto_merge_source()

    assert "$commentsAfter: String" in source
    assert "comments(first: 100, after: $commentsAfter)" in source
    assert "const comments = []" in source
    assert "comments.push(...page.comments.nodes)" in source
    assert "page.comments.pageInfo" in source
    assert "pullRequest.comments.nodes = comments" in source
    assert "github.rest.repos.getCommit" in source
    assert "[0-9a-f]{7,40}" in source


def test_codex_shepherd_callbacks_converge_from_current_finding_state() -> None:
    script = _script()

    # GitHub concurrency can replace a pending run. Finding handling therefore
    # must be a current-state gate shared by every surviving callback, not an
    # exclusive pull_request_review event branch.
    finding_gate = script.index("if (exactHeadReviews.length !== 0)")
    finding_hold = script.index("'review-finding'", finding_gate)
    clean_scan = script.index("let connectorCleanExactHead", finding_hold)
    review_request = script.index("!exactHeadReviewRequest", clean_scan)
    assert finding_gate < finding_hold < clean_scan < review_request
    assert "Event delivery is not a queue" in script
    assert "if (context.eventName === 'pull_request_review')" not in script[finding_gate:]


def test_codex_shepherd_only_holds_latest_actionable_ci_failure() -> None:
    script = _script()

    assert "const repairableCiConclusions = new Set(['failure', 'timed_out'])" in script
    assert "run.status !== 'completed'" in script
    assert "!repairableCiConclusions.has(run.conclusion)" in script

    # Select the newest Aletheia CI run first. Filtering to completed runs before
    # sorting would let an older failure win while a newer exact-head run is active.
    latest_ci_start = script.index("const latestCiRun")
    latest_ci_end = script.index("if (context.eventName === 'issue_comment')", latest_ci_start)
    latest_ci = script[latest_ci_start:latest_ci_end]
    assert "const primaryCiWorkflow = 'Aletheia CI'" in script
    assert ".filter((run) => run.name === primaryCiWorkflow)" in latest_ci
    assert "run.status === 'completed'" not in latest_ci

    workflow_handler = script[script.index("if (context.eventName === 'workflow_run')") :]
    assert "const currentRun = await latestCiRun()" in workflow_handler
    assert "currentRun.id !== payloadRun.id" in workflow_handler
    assert "currentRun.status !== 'completed'" in workflow_handler
    assert "!repairableCiConclusions.has(currentRun.conclusion)" in workflow_handler
    assert "ignoring stale or non-actionable CI completion callback" in workflow_handler

    assert "pullRequest.base.ref !== 'master'" in script
    assert "pullRequest.draft" in script
    assert "pullRequest.head.repo?.full_name" in script
    assert "release-please--branches--master" in script
    assert "do-not-auto-merge" in script
