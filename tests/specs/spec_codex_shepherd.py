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
        "Codex PR Shepherd is a least-privilege review orchestrator: it serializes per-PR convergence, self-heals stale dispatches, binds review requests to exact head/merge-base identity, and never owns merge or global hold state.",
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
        "contents": "read",
        "pull-requests": "read",
    }
    schedule_script = _schedule_script()
    assert "github.rest.repos.compareCommitsWithBasehead" in schedule_script
    assert "github.rest.actions.createWorkflowDispatch" in schedule_script
    assert "workflow_id: 'codex-shepherd.yml'" in schedule_script
    assert "expected_base_sha: expectedBaseSha" in schedule_script
    assert "github.rest.issues.createComment" not in schedule_script
    assert "github.rest.issues.addLabels" not in schedule_script
    assert "github.rest.pulls.merge" not in schedule_script

    apply = workflow["jobs"]["apply"]
    assert apply["permissions"] == {
        "actions": "write",
        "contents": "read",
        "issues": "write",
        "pull-requests": "read",
    }
    assert apply["concurrency"] == {
        "group": "codex-shepherd-update-${{ github.event.inputs.pull_request_number }}",
        "cancel-in-progress": "false",
    }
    script = _apply_script()
    assert "github.rest.pulls.merge" not in script
    assert "github.rest.git.updateRef" not in script
    assert "createOrUpdateFileContents" not in script
    assert "github.rest.issues.addLabels" not in script


def test_shepherd_rejects_noncanonical_manual_pr_numbers_before_mutation() -> None:
    script = _apply_script()

    canonical = script.index("if (!/^[1-9][0-9]*$/.test(pullRequestNumberInput))")
    parsed = script.index("const pullRequestNumber = Number(pullRequestNumberInput)", canonical)
    first_api_read = script.index("const loadCurrentIdentity = async", parsed)
    assert canonical < parsed < first_api_read
    assert "Number.parseInt" not in script
    assert "Number.isSafeInteger(pullRequestNumber)" in script


def test_shepherd_binds_requests_to_exact_head_and_merge_base() -> None:
    schedule_script = _schedule_script()
    script = _apply_script()

    assert "const expectedBaseSha = comparison.merge_base_commit.sha" in schedule_script
    assert "expected_head_sha: pullRequest.head.sha" in schedule_script
    assert "expected_base_sha: expectedBaseSha" in schedule_script
    assert "const loadCurrentIdentity" in script
    assert "comparison.merge_base_commit.sha" in script
    assert "head=([0-9a-f]{40}) base=([0-9a-f]{40}) kind=review" in script
    assert "kind=note reason=([a-z-]+)" in script
    assert "comment.user?.login === shepherdLogin" in script


def test_stale_apply_redispatches_current_identity_in_same_pr_lane() -> None:
    workflow = _workflow()
    apply = workflow["jobs"]["apply"]
    script = _apply_script()

    assert apply["concurrency"]["group"] == (
        "codex-shepherd-update-${{ github.event.inputs.pull_request_number }}"
    )
    assert apply["concurrency"]["cancel-in-progress"] == "false"
    assert "const dispatchCurrentIdentity = async (reason) =>" in script
    assert "workflow_id: 'codex-shepherd.yml'" in script
    assert "source_action: `reconcile:${reason}`" in script

    stale_gate = script.index("current review identity is")
    stale_redispatch = script.index("await dispatchCurrentIdentity('stale-apply')", stale_gate)
    stale_return = script.index("return;", stale_redispatch)
    assert stale_gate < stale_redispatch < stale_return


def test_comment_boundary_rechecks_identity_and_repairs_liveness() -> None:
    script = _apply_script()

    current_check = script.index("const identityStillCurrent = async () =>")
    redispatch = script.index("await dispatchCurrentIdentity('stale-comment-boundary')", current_check)
    post_helper = script.index("const postComment = async", redispatch)
    post_recheck = script.index("if (!(await identityStillCurrent())) return false", post_helper)
    comment_write = script.index("github.rest.issues.createComment", post_recheck)
    assert current_check < redispatch < post_helper < post_recheck < comment_write


def test_shepherd_does_not_project_global_hold_or_ci_failure_state() -> None:
    script = _apply_script()

    assert "github.rest.issues.addLabels" not in script
    assert "do-not-auto-merge" not in script
    assert "repairableCiConclusions" not in script
    assert "listWorkflowRunsForRepo" not in script
    assert "ci-failure" not in script
    assert "const noteForOwner" in script
    assert "kind=note reason=${reason}" in script


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


def test_shepherd_current_finding_and_self_change_are_diagnostic_only() -> None:
    script = _apply_script()

    assert "changedFiles.length !== pullRequest.changed_files" in script
    assert "file-list-incomplete" in script
    assert "const changesShepherdAuthority" in script
    assert "shepherd-self-change" in script
    assert "requires explicit owner merge" in script
    assert "review-finding" in script
    assert "write-capable repair executor is required" in script
    assert "@codex fix" not in script

    self_change = script.index("if (changesShepherdAuthority)")
    self_note = script.index("'shepherd-self-change'", self_change)
    findings = script.index("if (exactDiffReviews.length !== 0)", self_note)
    review_request = script.index("if (!exactDiffReviewRequest)", findings)
    assert self_change < self_note < findings < review_request


def test_shepherd_only_uses_connector_evidence_after_current_request() -> None:
    script = _apply_script()

    assert "review.commit_id === expectedHeadSha" in script
    assert "review.submitted_at >= latestReviewRequestAt" in script
    assert "review.state !== 'DISMISSED'" in script
    assert "cleanCompletionTimes" in script
    assert "connectorCompletionTimes" in script


def test_auto_merge_master_remains_sole_merge_authority() -> None:
    source = _auto_merge_source()

    shepherd_token = "'.github/workflows/codex-shepherd.yml'"
    authority_message = "merge-authority workflow changes require an explicit manual merge"
    assert source.count(shepherd_token) == 3
    assert source.count(authority_message) == 2
    assert "github.rest.pulls.merge" in source
    assert "github.rest.pulls.merge" not in _apply_script()
