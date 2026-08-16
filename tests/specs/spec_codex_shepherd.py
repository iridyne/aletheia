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


def _schedule_script() -> str:
    return _workflow()["jobs"]["schedule"]["steps"][0]["with"]["script"]


def _apply_script() -> str:
    return _workflow()["jobs"]["apply"]["steps"][0]["with"]["script"]


def _auto_merge_source() -> str:
    return AUTO_MERGE_WORKFLOW_PATH.read_text(encoding="utf-8")


@spec_claim(
    SpecClaim(
        "AG-CODEX-SHEPHERD-01",
        "agent_contracts",
        "§PR/CI control plane",
        "L2a",
        "Codex PR Shepherd separates trigger-facing workflow dispatch from PR-side metadata mutation without obtaining repository-content or merge authority.",
        status="implemented",
    )
)
def test_codex_shepherd_uses_actions_dispatch_without_contents_write() -> None:
    workflow = _workflow()
    triggers = workflow["on"]
    assert triggers["pull_request_target"]["types"] == [
        "opened",
        "reopened",
        "ready_for_review",
        "edited",
        "synchronize",
        "unlabeled",
    ]
    assert triggers["pull_request_review"]["types"] == ["submitted"]
    assert triggers["issue_comment"]["types"] == ["created"]
    assert triggers["workflow_run"]["workflows"] == ["Aletheia CI"]
    assert triggers["workflow_run"]["types"] == ["completed"]
    assert set(triggers["workflow_dispatch"]["inputs"]) == {
        "pull_request_number",
        "expected_head_sha",
        "source_event",
        "source_action",
    }
    for required in ("pull_request_number", "expected_head_sha", "source_event"):
        assert triggers["workflow_dispatch"]["inputs"][required]["required"] == "true"
    assert workflow["permissions"] == {"contents": "read"}
    assert "contents: write" not in WORKFLOW_PATH.read_text(encoding="utf-8")

    schedule = workflow["jobs"]["schedule"]
    assert schedule["permissions"] == {
        "actions": "write",
        "pull-requests": "read",
    }
    assert schedule["runs-on"] == "ubuntu-24.04"
    assert "actions/checkout" not in str(schedule)
    schedule_script = _schedule_script()
    assert "github.rest.actions.createWorkflowDispatch" in schedule_script
    assert "workflow_id: 'codex-shepherd.yml'" in schedule_script
    assert "ref: 'master'" in schedule_script
    assert "github.rest.repos.createDispatchEvent" not in schedule_script
    assert "github.rest.issues.createComment" not in schedule_script
    assert "github.rest.issues.addLabels" not in schedule_script
    assert "github.rest.pulls.merge" not in schedule_script
    assert "createOrUpdateFileContents" not in schedule_script
    assert "github.rest.git.updateRef" not in schedule_script

    apply = workflow["jobs"]["apply"]
    assert apply["if"] == "github.event_name == 'workflow_dispatch'"
    assert apply["permissions"] == {
        "actions": "read",
        "contents": "read",
        "issues": "write",
        "pull-requests": "read",
    }
    assert apply["runs-on"] == "ubuntu-24.04"
    assert apply["concurrency"]["cancel-in-progress"] == "true"
    assert "github.event.inputs.pull_request_number" in apply["concurrency"]["group"]
    assert "actions/checkout" not in str(apply)
    apply_script = _apply_script()
    assert "github.rest.issues.createComment" in apply_script
    assert "github.rest.issues.addLabels" in apply_script
    assert "github.rest.pulls.merge" not in apply_script
    assert "createOrUpdateFileContents" not in apply_script
    assert "github.rest.git.updateRef" not in apply_script


def test_shepherd_workflow_dispatch_is_exact_head_bound() -> None:
    schedule_script = _schedule_script()
    apply_script = _apply_script()

    assert "const connectorLogin = 'chatgpt-codex-connector'" in schedule_script
    assert "isConnectorLogin(payload.review?.user?.login)" in schedule_script
    assert "isConnectorLogin(payload.comment?.user?.login)" in schedule_script
    assert "run?.event !== 'pull_request'" in schedule_script
    assert "pullRequest.head.repo?.full_name" in schedule_script
    assert "expected_head_sha: pullRequest.head.sha" in schedule_script
    assert "source_event: context.eventName" in schedule_script
    assert "pull_request_number: String(pullRequest.number)" in schedule_script

    assert "const request = context.payload.inputs || {}" in apply_script
    assert "expected head SHA must be a 40-character lowercase Git object ID" in apply_script
    assert "unsupported Shepherd source event" in apply_script
    assert "pullRequest.head.sha !== expectedHeadSha" in apply_script
    assert "Shepherd request for ${expectedHeadSha} is stale" in apply_script
    assert "pullRequest.base.ref !== 'master'" in apply_script
    assert "pullRequest.draft" in apply_script
    assert "pullRequest.head.repo?.full_name" in apply_script
    assert "release-please--branches--master" in apply_script


def test_codex_shepherd_binds_reviews_comments_and_markers_to_exact_head() -> None:
    script = _apply_script()

    assert "const reviewedCommitPattern" in script
    assert "github.rest.repos.getCommit" in script
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
    assert "reviewedCommitSha === expectedHeadSha" in script


def test_codex_shepherd_terminal_holds_are_recoverable_and_fail_closed() -> None:
    script = _apply_script()

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

    # Marker-first write order makes a partial terminal hold reconstructible.
    hold_start = script.index("const holdForOwner")
    hold_comment = script.index("await postComment(", hold_start)
    hold_label = script.index("await ensureHoldLabel()", hold_comment)
    assert hold_start < hold_comment < hold_label
    assert "Persist the exact-head halt marker before the label" in script[
        hold_comment : hold_label + 300
    ]

    # Any exact-head halt marker is durable control state. Every successor restores
    # the label before file/self-change/source-specific early returns.
    label_state = script.index("let holdLabelPresent")
    recovery_gate = script.index("if (exactHeadHalt) {", hold_label)
    recovery_label = script.index("await ensureHoldLabel();", recovery_gate)
    file_gate = script.index("if (changedFiles.length !== pullRequest.changed_files)", recovery_label)
    self_change = script.index("if (changesShepherdAuthority)", file_gate)
    generic_hold = script.index("if (holdLabelPresent)", self_change)
    assert label_state < hold_start < recovery_gate < recovery_label < file_gate
    assert file_gate < self_change < generic_hold

    # Self-change uses the same marker-first ordering.
    self_change_comment = script.index("await postComment(", self_change)
    self_change_label = script.index("await ensureHoldLabel()", self_change_comment)
    assert self_change < self_change_comment < self_change_label < generic_hold


def test_auto_merge_atomically_protects_shepherd_authority() -> None:
    source = _auto_merge_source()
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

    ordinary_metadata = source.index("const {data: pullRequestMetadata} = await github.rest.pulls.get")
    ordinary_complete = source.index(
        "if (changedFiles.length !== pullRequestMetadata.changed_files)", ordinary_metadata
    )
    assert ordinary_metadata < ordinary_complete < shepherd_positions[2] < message_positions[1]
    assert "pullRequestMetadata.head.sha !== expectedHeadSha" in source[
        ordinary_metadata:ordinary_complete
    ]


def test_codex_shepherd_requests_retargeted_and_resumed_exact_heads_without_repair_claim() -> None:
    workflow = _workflow()
    triggers = workflow["on"]["pull_request_target"]["types"]
    for action in (
        "opened",
        "reopened",
        "ready_for_review",
        "edited",
        "synchronize",
        "unlabeled",
    ):
        assert action in triggers

    script = _apply_script()
    assert "@codex review" in script
    assert "kind=review" in script
    assert "const exactHeadReviewRequest" in script
    assert "!exactHeadReviewRequest && !connectorCleanExactHead" in script
    assert "@codex fix" not in script
    assert "Codex Cloud repair is not authorized to push back" in script
    assert "review-finding" in script
    assert "ci-failure" in script


def test_codex_shepherd_callbacks_converge_from_current_finding_state() -> None:
    script = _apply_script()

    finding_gate = script.index("if (exactHeadReviews.length !== 0)")
    finding_hold = script.index("'review-finding'", finding_gate)
    clean_scan = script.index("let connectorCleanExactHead", finding_hold)
    review_request = script.index("!exactHeadReviewRequest && !connectorCleanExactHead", clean_scan)
    assert finding_gate < finding_hold < clean_scan < review_request

    apply = _workflow()["jobs"]["apply"]
    assert apply["concurrency"]["cancel-in-progress"] == "true"
    assert "pullRequest.head.sha !== expectedHeadSha" in script


def test_codex_shepherd_only_holds_current_actionable_ci_failure() -> None:
    script = _apply_script()

    assert "const repairableCiConclusions = new Set(['failure', 'timed_out'])" in script
    latest_ci_start = script.index("const latestCiRun")
    latest_ci_end = script.index("if (sourceEvent === 'issue_comment'", latest_ci_start)
    latest_ci = script[latest_ci_start:latest_ci_end]
    assert ".filter((run) => run.name === 'Aletheia CI')" in latest_ci
    assert ".sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at))" in latest_ci
    assert "run.status === 'completed'" not in latest_ci

    handler = script[latest_ci_end:]
    assert "sourceEvent === 'issue_comment' || sourceEvent === 'workflow_run'" in handler
    assert "!connectorCleanExactHead" in handler
    assert "run.status !== 'completed'" in handler
    assert "!repairableCiConclusions.has(run.conclusion)" in handler
    assert "do-not-auto-merge" in script
