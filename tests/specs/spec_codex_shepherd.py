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


def _workflow() -> dict:
    return yaml.load(WORKFLOW_PATH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _script() -> str:
    return _workflow()["jobs"]["shepherd"]["steps"][0]["with"]["script"]


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
    assert "const triagePattern" in script
    assert "const trustedAssociations" in script
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
    assert source.count(shepherd_token) == 3
    assert source.count("'.github/workflows/auto-merge.yml'") >= 3
    assert source.count(authority_message) == 2

    shepherd_positions: list[int] = []
    start = 0
    while True:
        position = source.find(shepherd_token, start)
        if position < 0:
            break
        shepherd_positions.append(position)
        start = position + len(shepherd_token)

    message_positions: list[int] = []
    start = 0
    while True:
        position = source.find(authority_message, start)
        if position < 0:
            break
        message_positions.append(position)
        start = position + len(authority_message)

    assert len(shepherd_positions) == 3
    assert len(message_positions) == 2
    assert (
        shepherd_positions[0]
        < shepherd_positions[1]
        < message_positions[0]
        < shepherd_positions[2]
        < message_positions[1]
    )

    # Every authority decision must fail closed when GitHub's files endpoint is
    # truncated. Schedule can disable directly; release and ordinary merge return.
    schedule_complete = source.index(
        "const changedFilesComplete = changedFiles.length === pullRequest.changed_files"
    )
    schedule_authority = source.index("const changesMergeAuthority", schedule_complete)
    assert "!changedFilesComplete ||" in source[schedule_authority : schedule_authority + 700]
    assert schedule_complete < schedule_authority < shepherd_positions[0]

    release_summary = source.index("const [pullRequestSummary] = candidates")
    release_metadata = source.index(
        "const {data: pullRequest} = await github.rest.pulls.get", release_summary
    )
    release_complete = source.index(
        "if (changedFiles.length !== pullRequest.changed_files)", release_metadata
    )
    assert release_summary < release_metadata < release_complete < shepherd_positions[1]
    assert shepherd_positions[1] < message_positions[0]
    assert "pull_number: pullRequestSummary.number" in source[release_metadata:release_complete]
    assert "release candidate changed after discovery; owner hold remains" in source[
        release_metadata:release_complete
    ]
    assert "owner hold remains" in source[release_complete:shepherd_positions[1]]

    ordinary_metadata = source.index("const {data: pullRequestMetadata} = await github.rest.pulls.get")
    ordinary_complete = source.index(
        "if (changedFiles.length !== pullRequestMetadata.changed_files)", ordinary_metadata
    )
    assert ordinary_metadata < ordinary_complete < shepherd_positions[2] < message_positions[1]
    assert "pullRequestMetadata.head.sha !== expectedHeadSha" in source[
        ordinary_metadata:ordinary_complete
    ]
    assert "exact-head merge remains disabled" in source[
        ordinary_complete:shepherd_positions[2]
    ]


def test_codex_shepherd_requests_every_ready_or_resumed_exact_head_but_does_not_claim_repair() -> None:
    script = _script()

    assert "const reviewTriggerActions = new Set" in script
    for action in (
        "'opened'",
        "'reopened'",
        "'ready_for_review'",
        "'synchronize'",
        "'unlabeled'",
    ):
        assert action in script
    assert "reviewTriggerActions.has(context.payload.action)" in script
    assert "@codex review" in script
    assert "kind=review" in script
    assert "const exactHeadReviewRequest" in script
    assert "!exactHeadReviewRequest" in script
    assert "!connectorCleanExactHead" in script

    # Missing review creation is a convergence gate shared by every surviving
    # callback; it must run before event-specific CI/comment handling.
    request_gate = script.index("!exactHeadReviewRequest")
    pull_request_return = script.index("if (context.eventName === 'pull_request_target') return", request_gate)
    issue_handler = script.index("if (context.eventName === 'issue_comment')", pull_request_return)
    workflow_handler = script.index("if (context.eventName === 'workflow_run')", issue_handler)
    assert request_gate < pull_request_return < issue_handler < workflow_handler
    assert "surviving trusted callback" in script

    # Runtime evidence showed the current Codex Cloud task can create a local
    # repair commit but cannot push it back to this repository. Until that
    # permission/executor exists, Shepherd must hold rather than fake autonomy.
    assert "@codex fix" not in script
    assert "const holdForOwner" in script
    assert "Codex Cloud repair is not authorized to push back" in script
    assert "review-finding" in script
    assert "ci-failure" in script


def test_codex_shepherd_callbacks_converge_from_current_finding_state() -> None:
    script = _script()

    # GitHub concurrency can replace a pending run. Finding handling therefore
    # must be a current-state gate shared by every surviving callback, not an
    # exclusive pull_request_review event branch.
    finding_gate = script.index("if (exactHeadReviews.length !== 0 && !triageComplete)")
    assert "!triageComplete" in script[finding_gate : finding_gate + 100]
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
    assert ".filter((run) => run.name === 'Aletheia CI')" in latest_ci
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
