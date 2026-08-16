from __future__ import annotations

from pathlib import Path

import pytest
import yaml


pytestmark = pytest.mark.tier_unit

WORKFLOW_PATH = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "auto-merge.yml"


def _workflow() -> dict:
    return yaml.load(WORKFLOW_PATH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


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
    assert "comments(last: 100)" not in update_script
    assert "github.paginate(github.rest.issues.listComments" in update_script
    assert "github.paginate(github.rest.pulls.listReviews" in update_script
    assert "per_page: 100" in update_script
    assert "const resolveReviewedCommitSha" in update_script
    assert "github.rest.repos.getCommit" in update_script
    assert "const connectorCleanCandidates" in update_script
    assert "let connectorCleanExactHead = false" in update_script
    assert "reviewedCommitSha === expectedHeadSha" in update_script
    assert "expectedHeadSha.startsWith" not in update_script
    assert "Didn't find any major issues" in update_script


def test_ordinary_auto_merge_authority_is_bound_to_current_head_and_merge_base() -> None:
    workflow = _workflow()
    update = workflow["jobs"]["update"]
    update_script = update["steps"][0]["with"]["script"]

    concurrency_group = update["concurrency"]["group"]
    assert "github.event.client_payload.pull_request_number" in concurrency_group
    assert "github.event.client_payload.expected_head_sha" in concurrency_group
    assert "github.event.client_payload.expected_base_sha" in concurrency_group
    assert "operation === 'enable'" in update_script
    assert "expected merge-base SHA must be a 40-character lowercase Git object ID" in update_script
    assert "github.rest.repos.compareCommitsWithBasehead" in update_script
    assert "const currentBaseSha = comparison.merge_base_commit.sha" in update_script
    assert "currentBaseSha !== expectedBaseSha" in update_script
    assert "a newer exact-diff event must authorize the merge" in update_script
    assert "pullRequest.headRefOid !== expectedHeadSha" in update_script
    assert "merge-authority workflow changes require an explicit manual merge" in update_script
    assert "sha: expectedHeadSha" in update_script


def test_auto_merge_requires_current_shepherd_review_request_before_codex_evidence() -> None:
    update_script = _workflow()["jobs"]["update"]["steps"][0]["with"]["script"]

    assert "const shepherdLogin = 'github-actions[bot]'" in update_script
    assert "const shepherdReviewMarkerPattern" in update_script
    assert "head=([0-9a-f]{40}) base=([0-9a-f]{40}) kind=review" in update_script
    assert "comment.user?.login !== shepherdLogin" in update_script
    assert "marker[1] !== expectedHeadSha" in update_script
    assert "marker[2] !== expectedBaseSha" in update_script
    assert "const latestReviewRequestAt" in update_script
    assert "if (!latestReviewRequestAt)" in update_script

    marker_gate = update_script.index("const currentReviewRequests")
    request_time = update_script.index("const latestReviewRequestAt", marker_gate)
    no_request = update_script.index("if (!latestReviewRequestAt)", request_time)
    reviews_gate = update_script.index("const exactHeadReviews", no_request)
    clean_candidates = update_script.index("const connectorCleanCandidates", reviews_gate)
    merge_state_gate = update_script.index(
        "if (pullRequest.mergeStateStatus !== 'CLEAN')", clean_candidates
    )
    assert marker_gate < request_time < no_request < reviews_gate < clean_candidates < merge_state_gate


def test_codex_review_clean_and_triage_evidence_must_postdate_current_request() -> None:
    update_script = _workflow()["jobs"]["update"]["steps"][0]["with"]["script"]

    assert "review.commit_id === expectedHeadSha" in update_script
    assert "review.submitted_at >= latestReviewRequestAt" in update_script
    assert "review.state !== 'DISMISSED'" in update_script
    assert "comment.created_at < latestReviewRequestAt" in update_script
    assert "comment.created_at >= latestReviewRequestAt" in update_script

    review_pagination = update_script.index("github.paginate(github.rest.pulls.listReviews")
    comment_pagination = update_script.index("github.paginate(github.rest.issues.listComments")
    request_time = update_script.index("const latestReviewRequestAt", comment_pagination)
    no_findings_gate = update_script.index("if (exactHeadReviews.length === 0)", request_time)
    clean_gate = update_script.index("!connectorCleanExactHead", no_findings_gate)
    findings_triage = update_script.index("const triageComplete", clean_gate)
    merge_state_gate = update_script.index(
        "if (pullRequest.mergeStateStatus !== 'CLEAN')", findings_triage
    )
    assert review_pagination < request_time
    assert comment_pagination < request_time
    assert request_time < no_findings_gate < clean_gate < findings_triage < merge_state_gate
