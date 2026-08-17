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
        "Codex PR Shepherd separates trusted scheduling from PR metadata mutation, serializes mutations per PR, revalidates exact review identity before writes, and cannot obtain repository-content or merge authority.",
        status="implemented",
    )
)
def test_codex_shepherd_uses_two_stage_least_privilege_control_plane() -> None:
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
        "expected_base_sha",
        "source_event",
        "source_action",
    }
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
    assert "github.rest.repos.compareCommitsWithBasehead" in schedule_script
    assert "expected_base_sha: expectedBaseSha" in schedule_script
    assert "github.rest.issues.createComment" not in schedule_script
    assert "github.rest.issues.addLabels" not in schedule_script
    assert "github.rest.pulls.merge" not in schedule_script

    apply = workflow["jobs"]["apply"]
    assert apply["permissions"] == {
        "actions": "read",
        "contents": "read",
        "issues": "write",
        "pull-requests": "read",
    }
    assert apply["runs-on"] == "ubuntu-24.04"
    assert apply["concurrency"] == {
        "group": "codex-shepherd-update-${{ github.event.inputs.pull_request_number }}",
        "cancel-in-progress": "false",
    }
    assert "actions/checkout" not in str(apply)
    script = _apply_script()
    assert "github.rest.pulls.merge" not in script
    assert "github.rest.git.updateRef" not in script
    assert "createOrUpdateFileContents" not in script


def test_shepherd_binds_dispatch_and_markers_to_exact_head_and_merge_base() -> None:
    schedule_script = _schedule_script()
    script = _apply_script()

    assert "const expectedBaseSha = comparison.merge_base_commit.sha" in schedule_script
    assert "expected_head_sha: pullRequest.head.sha" in schedule_script
    assert "expected_base_sha: expectedBaseSha" in schedule_script

    assert "const expectedHeadSha = String(request.expected_head_sha || '')" in script
    assert "const expectedBaseSha = String(request.expected_base_sha || '')" in script
    assert "const loadCurrentIdentity" in script
    assert "comparison.merge_base_commit.sha" in script
    assert "current review identity is" in script
    assert "head=([0-9a-f]{40}) base=([0-9a-f]{40}) kind=review" in script
    assert "halt head=([0-9a-f]{40}) base=([0-9a-f]{40}) reason=" in script
    assert "comment.user?.login === shepherdLogin" in script


def test_shepherd_revalidates_identity_before_every_pr_side_mutation() -> None:
    script = _apply_script()

    assert "const identityStillCurrent = async () =>" in script
    assert "skipping stale Shepherd mutation" in script

    post_comment = script.index("const postComment = async")
    post_recheck = script.index("if (!(await identityStillCurrent())) return false", post_comment)
    comment_write = script.index("github.rest.issues.createComment", post_recheck)
    assert post_comment < post_recheck < comment_write

    label_helper = script.index("const ensureHoldLabel = async")
    label_recheck = script.index("if (!(await identityStillCurrent())) return false", label_helper)
    label_write = script.index("github.rest.issues.addLabels", label_recheck)
    assert label_helper < label_recheck < label_write

    hold_helper = script.index("const holdForOwner = async")
    hold_recheck = script.index("if (!(await identityStillCurrent())) return false", hold_helper)
    hold_comment = script.index("const posted = await postComment", hold_recheck)
    hold_label = script.index("return ensureHoldLabel()", hold_comment)
    assert hold_helper < hold_recheck < hold_comment < hold_label


def test_shepherd_serializes_mutation_lane_per_pr_not_per_head() -> None:
    apply = _workflow()["jobs"]["apply"]
    script = _apply_script()

    group = apply["concurrency"]["group"]
    assert group == "codex-shepherd-update-${{ github.event.inputs.pull_request_number }}"
    assert "expected_head_sha" not in group
    assert "expected_base_sha" not in group
    assert apply["concurrency"]["cancel-in-progress"] == "false"
    assert "pullRequest.head.sha !== expectedHeadSha" in script
    assert "initialIdentity.baseSha !== expectedBaseSha" in script


def test_shepherd_serializes_same_head_review_requests_across_bases() -> None:
    script = _apply_script()

    assert "const sameHeadReviewRequests" in script
    assert "const latestSameHeadReviewRequest" in script
    assert "latestSameHeadReviewRequest?.base === expectedBaseSha" in script
    assert "const requestHasCompletion" in script
    assert "waiting for prior same-head Codex request" in script

    request_log = script.index("const sameHeadReviewRequests")
    completion_times = script.index("const connectorCompletionTimes", request_log)
    request_gate = script.index("if (!exactDiffReviewRequest)", completion_times)
    wait_prior = script.index("!requestHasCompletion(latestSameHeadReviewRequest)", request_gate)
    post_request = script.index("await postComment(", wait_prior)
    assert request_log < completion_times < request_gate < wait_prior < post_request


def test_shepherd_finding_self_change_and_ci_failure_fail_closed() -> None:
    script = _apply_script()

    assert "changedFiles.length !== pullRequest.changed_files" in script
    assert "file-list-incomplete" in script
    assert "cannot prove the authority boundary" in script
    assert "const changesShepherdAuthority" in script
    assert "shepherd-self-change" in script
    assert "requires explicit owner merge" in script
    assert "review-finding" in script
    assert "ci-failure" in script
    assert "labels: ['do-not-auto-merge']" in script
    assert "Codex Cloud repair is not authorized to push back" in script
    assert "@codex fix" not in script

    self_change = script.index("if (changesShepherdAuthority)")
    owner_hold = script.index("if (holdLabelPresent)", self_change)
    findings = script.index("if (exactDiffReviews.length !== 0)", owner_hold)
    review_request = script.index("if (!exactDiffReviewRequest)", findings)
    assert self_change < owner_hold < findings < review_request


def test_shepherd_only_uses_current_request_evidence_and_current_ci_failure() -> None:
    script = _apply_script()

    assert "review.commit_id === expectedHeadSha" in script
    assert "review.submitted_at >= latestReviewRequestAt" in script
    assert "review.state !== 'DISMISSED'" in script
    assert "cleanCompletionTimes.some((completedAt) => completedAt >= latestReviewRequestAt)" in script
    assert "const repairableCiConclusions = new Set(['failure', 'timed_out'])" in script
    assert ".filter((run) => run.name === 'Aletheia CI')" in script
    assert ".sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at))" in script


def test_auto_merge_master_still_owns_merge_authority_and_self_change_gate() -> None:
    source = _auto_merge_source()

    shepherd_token = "'.github/workflows/codex-shepherd.yml'"
    authority_message = "merge-authority workflow changes require an explicit manual merge"
    assert source.count(shepherd_token) == 3
    assert source.count("'.github/workflows/auto-merge.yml'") >= 3
    assert source.count(authority_message) == 2
    assert "github.rest.pulls.merge" in source

    schedule_complete = source.index(
        "const changedFilesComplete = changedFiles.length === pullRequest.changed_files"
    )
    schedule_authority = source.index("const changesMergeAuthority", schedule_complete)
    assert "!changedFilesComplete ||" in source[schedule_authority : schedule_authority + 700]
