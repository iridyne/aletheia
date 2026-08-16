from __future__ import annotations

from pathlib import Path

import yaml


WORKFLOW_PATH = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "auto-merge.yml"


def _workflow() -> dict:
    return yaml.load(WORKFLOW_PATH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_exact_head_clean_connector_comment_reenters_auto_merge_without_manual_triage() -> None:
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
    assert "isConnectorLogin(payload.comment.user?.login)" in schedule_script
    assert "pullRequest.head.sha.startsWith(reviewedCommit[1])" in schedule_script
    assert "ignoring Connector completion for a superseded or unbound head" in schedule_script

    update_script = update["steps"][0]["with"]["script"]
    assert "comments(last: 100)" in update_script
    assert "author { login }" in update_script
    assert "const connectorCleanComments" in update_script
    assert "expectedHeadSha.startsWith(reviewedCommit[1])" in update_script
    assert "Didn't find any major issues" in update_script
    assert "const connectorCleanAfterReview" in update_script
    assert "if (!connectorCleanAfterReview)" in update_script
    assert "a clean Connector result will resume this PR automatically" in update_script

    # A stale review/comment must never authorize a newer head, and merge authority
    # continues to fail closed when this workflow itself is changed.
    assert "pullRequest.headRefOid !== expectedHeadSha" in update_script
    assert "merge-authority workflow changes require an explicit manual merge" in update_script
    assert "sha: expectedHeadSha" in update_script

    automatic_gate = update_script.index("if (!connectorCleanAfterReview)")
    fallback_triage = update_script.index("if (exactHeadReviews.length === 0)", automatic_gate)
    clean_merge_gate = update_script.index(
        "if (pullRequest.mergeStateStatus !== 'CLEAN')", fallback_triage
    )
    assert automatic_gate < fallback_triage < clean_merge_gate
