from __future__ import annotations

from pathlib import Path

import pytest
import yaml


pytestmark = pytest.mark.tier_unit

WORKFLOW_PATH = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "auto-merge.yml"


def _workflow() -> dict:
    return yaml.load(WORKFLOW_PATH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _serialized_request_chain_is_valid(
    request_times: list[int], completion_times: list[int]
) -> bool:
    """Model the workflow rule: every non-latest request must drain before the next."""
    return all(
        any(request <= completion < following for completion in completion_times)
        for request, following in zip(request_times, request_times[1:], strict=True)
    )


def test_exact_diff_clean_connector_comment_reenters_auto_merge_without_manual_triage() -> None:
    workflow = _workflow()
    schedule = workflow["jobs"]["schedule"]
    update = workflow["jobs"]["update"]

    schedule_guard = schedule["if"]
    assert "event_name == 'issue_comment'" in schedule_guard
    assert "github.event.issue.pull_request" in schedule_guard
    assert "github.event.comment.user.login == 'chatgpt-codex-connector'" in schedule_guard
    assert "github.event.comment.user.login == 'chatgpt-codex-connector[bot]'" in schedule_guard

    schedule_script = schedule["steps"][0]["with"]["script"]
    assert "const reviewedCommitPattern" in schedule_script
    assert "const resolveReviewedCommitSha" in schedule_script
    assert "github.rest.repos.getCommit" in schedule_script
    assert "return commit.sha" in schedule_script
    assert "isConnectorLogin(payload.comment.user?.login)" in schedule_script
    assert "reviewedCommitSha !== pullRequest.head.sha" in schedule_script
    assert "pullRequest.head.sha.startsWith" not in schedule_script
    assert "github.rest.repos.compareCommitsWithBasehead" in schedule_script
    assert "const expectedBaseSha = comparison.merge_base_commit.sha" in schedule_script
    assert "expected_base_sha: expectedBaseSha" in schedule_script

    update_script = update["steps"][0]["with"]["script"]
    assert "github.paginate(github.rest.issues.listComments" in update_script
    assert "pullRequest.comments.nodes" not in update_script
    assert "github.paginate(github.rest.pulls.listReviews" in update_script
    assert "per_page: 100" in update_script
    assert "const resolveReviewedCommitSha" in update_script
    assert "github.rest.repos.getCommit" in update_script
    assert "const connectorCleanCandidates" in update_script
    assert "const cleanCompletionTimes" in update_script
    assert "let connectorCleanExactHead = false" in update_script
    assert "reviewedCommitSha === expectedHeadSha" in update_script
    assert "expectedHeadSha.startsWith" not in update_script
    assert "Didn't find any major issues" in update_script


def test_ordinary_auto_merge_authority_is_bound_to_current_head_and_merge_base() -> None:
    workflow = _workflow()
    update = workflow["jobs"]["update"]
    update_script = update["steps"][0]["with"]["script"]

    assert update["concurrency"] == {
        "group": "auto-merge-update-${{ github.event.client_payload.pull_request_number }}",
        "cancel-in-progress": "true",
    }
    assert "const dispatchCurrentUpdate" in update_script
    assert update_script.count("await dispatchCurrentUpdate();") == 2
    assert "currentMetadata.head.sha" in update_script
    assert "expected_base_sha: currentMergeBaseSha" in update_script

    assert "operation === 'enable'" in update_script
    assert "expected merge-base SHA must be a 40-character lowercase Git object ID" in update_script
    assert "github.rest.repos.compareCommitsWithBasehead" in update_script
    assert "const currentBaseSha = comparison.merge_base_commit.sha" in update_script
    assert "currentBaseSha !== expectedBaseSha" in update_script
    assert "dispatching current exact-diff authorization" in update_script
    assert "pullRequest.headRefOid !== expectedHeadSha" in update_script
    assert "merge-authority workflow changes require an explicit manual merge" in update_script
    assert "sha: expectedHeadSha" in update_script


def test_auto_merge_requires_serialized_current_shepherd_request_chain() -> None:
    update_script = _workflow()["jobs"]["update"]["steps"][0]["with"]["script"]

    assert "const shepherdLogin = 'github-actions[bot]'" in update_script
    assert "const shepherdReviewMarkerPattern" in update_script
    assert "head=([0-9a-f]{40}) base=([0-9a-f]{40}) kind=review" in update_script
    assert "const sameHeadReviewRequests" in update_script
    assert "marker[1] !== expectedHeadSha" in update_script
    assert "const latestReviewRequest = sameHeadReviewRequests.at(-1) || null" in update_script
    assert "latestReviewRequest.base !== expectedBaseSha" in update_script
    assert "const connectorCompletionTimes" in update_script
    assert "const drainedBeforeNextRequest" in update_script
    assert "completedAt >= request.createdAt && completedAt < nextRequest.createdAt" in update_script
    assert "same-head Shepherd review request chain is ambiguous" in update_script

    marker_gate = update_script.index("const sameHeadReviewRequests")
    latest_gate = update_script.index("const latestReviewRequest", marker_gate)
    current_diff_gate = update_script.index("latestReviewRequest.base !== expectedBaseSha", latest_gate)
    completion_times = update_script.index("const connectorCompletionTimes", current_diff_gate)
    chain_loop = update_script.index("sameHeadReviewRequests.length - 1", completion_times)
    ambiguous = update_script.index("same-head Shepherd review request chain is ambiguous", chain_loop)
    reviews_gate = update_script.index("const exactHeadReviews", ambiguous)
    assert marker_gate < latest_gate < current_diff_gate < completion_times < chain_loop < ambiguous < reviews_gate


def test_late_old_base_completion_cannot_authorize_new_base() -> None:
    old_request = 10
    new_request = 20

    # An old-base task that completes only after the new-base request cannot drain
    # the old request before the new request. The authority chain is therefore invalid.
    assert not _serialized_request_chain_is_valid([old_request, new_request], [25])

    # Valid serialization requires the old request to complete before the new request;
    # the new request then needs its own completion before it can authorize the new diff.
    assert _serialized_request_chain_is_valid([old_request, new_request], [15, 25])
    assert any(completion >= new_request for completion in [15, 25])


def test_codex_review_clean_and_triage_evidence_must_postdate_latest_current_request() -> None:
    update_script = _workflow()["jobs"]["update"]["steps"][0]["with"]["script"]

    assert "review.commit_id === expectedHeadSha" in update_script
    assert "review.submitted_at >= latestReviewRequestAt" in update_script
    assert "review.state !== 'DISMISSED'" in update_script
    assert "comment.created_at < latestReviewRequestAt" in update_script
    assert "cleanCompletionTimes.some((completedAt) => completedAt >= latestReviewRequestAt)" in update_script

    review_pagination = update_script.index("github.paginate(github.rest.pulls.listReviews")
    comment_pagination = update_script.index("github.paginate(github.rest.issues.listComments")
    request_time = update_script.index("const latestReviewRequestAt", comment_pagination)
    chain_validation = update_script.index("const drainedBeforeNextRequest", request_time)
    no_findings_gate = update_script.index("if (exactHeadReviews.length === 0)", chain_validation)
    clean_gate = update_script.index("!connectorCleanExactHead", no_findings_gate)
    findings_triage = update_script.index("const triageComplete", clean_gate)
    merge_state_gate = update_script.index(
        "if (pullRequest.mergeStateStatus !== 'CLEAN')", findings_triage
    )
    assert review_pagination < request_time
    assert comment_pagination < request_time
    assert request_time < chain_validation < no_findings_gate < clean_gate < findings_triage < merge_state_gate
