"""Focused Phase 3 contracts for the repository-native CI Yield collector."""

from __future__ import annotations

from typing import Any

import scripts.ci_yield as ci_yield


def _pr() -> dict[str, Any]:
    return {
        "number": 2171,
        "title": "fix(ci): recover metadata-blocked exact-head runs",
        "body": "References #2167",
        "url": "https://example.test/pr/2171",
        "author": {"login": "iridite"},
        "headRefName": "ci-fix",
        "headRefOid": "a" * 40,
        "baseRefOid": "b" * 40,
        "createdAt": "2026-08-24T00:00:00Z",
        "mergedAt": "2026-08-24T00:03:00Z",
        "additions": 10,
        "deletions": 2,
        "changedFiles": 2,
        "commits": [{"oid": "a" * 40}],
        "closingIssuesReferences": [],
        "files": [
            {"path": ".github/workflows/ci.yml"},
            {"path": "scripts/ci_yield.py"},
        ],
    }


def test_change_batch_features_are_correlation_only_and_complete() -> None:
    shape = ci_yield._pr_shape(_pr())

    assert shape["changed_file_count"] == 2
    assert shape["changed_lines"] == 12
    assert shape["commit_count"] == 1
    assert shape["linked_issue_count"] == 1
    assert shape["touched_authorities"] == ["ci_control"]
    assert shape["authority_count"] == 1
    assert shape["synthetic_merge_identity_available"] is False


def test_failed_test_lane_stays_unknown_without_bounded_log_evidence() -> None:
    failure = ci_yield._failure({
        "name": "Python integration",
        "conclusion": "failure",
        "steps": [{"name": "Run integration regression tier", "conclusion": "failure"}],
    })

    assert failure is not None
    assert failure["category"] == "unknown"
    assert failure["confidence"] == "unknown"


def test_collect_pr_distinguishes_same_head_body_repair_from_blanket_retry(monkeypatch) -> None:
    head = "a" * 40
    runs = [
        {
            "id": 10,
            "name": ci_yield.PRIMARY_WORKFLOW,
            "head_sha": head,
            "run_attempt": 2,
            "created_at": "2026-08-24T00:00:01Z",
        },
        {
            "id": 20,
            "name": ci_yield.METADATA_WORKFLOW,
            "head_sha": head,
            "run_attempt": 1,
            "created_at": "2026-08-24T00:00:01Z",
        },
        {
            "id": 21,
            "name": ci_yield.METADATA_WORKFLOW,
            "head_sha": head,
            "run_attempt": 1,
            "created_at": "2026-08-24T00:00:30Z",
        },
    ]

    def fake_api(repo: str, path: str) -> Any:  # noqa: ARG001
        if path.startswith("actions/runs?branch="):
            return {"workflow_runs": runs}
        attempts = {
            "actions/runs/10/attempts/1": {
                "head_sha": head,
                "created_at": "2026-08-24T00:00:01Z",
                "run_started_at": "2026-08-24T00:00:02Z",
                "updated_at": "2026-08-24T00:00:20Z",
                "conclusion": "failure",
            },
            "actions/runs/10/attempts/2": {
                "head_sha": head,
                "created_at": "2026-08-24T00:01:00Z",
                "run_started_at": "2026-08-24T00:01:00Z",
                "updated_at": "2026-08-24T00:02:00Z",
                "conclusion": "success",
            },
            "actions/runs/20/attempts/1": {
                "head_sha": head,
                "created_at": "2026-08-24T00:00:01Z",
                "run_started_at": "2026-08-24T00:00:01Z",
                "updated_at": "2026-08-24T00:00:20Z",
                "conclusion": "failure",
            },
            "actions/runs/21/attempts/1": {
                "head_sha": head,
                "created_at": "2026-08-24T00:00:30Z",
                "run_started_at": "2026-08-24T00:00:30Z",
                "updated_at": "2026-08-24T00:00:40Z",
                "conclusion": "success",
            },
        }
        if path in attempts:
            return attempts[path]
        if path == "actions/runs/10/attempts/1/jobs?per_page=100":
            return {"jobs": [{
                "id": 1,
                "name": "Change Contract preflight for deep CI",
                "conclusion": "failure",
                "started_at": "2026-08-24T00:00:02Z",
                "completed_at": "2026-08-24T00:00:20Z",
                "steps": [{
                    "name": "Verify Change Contract before deep CI",
                    "conclusion": "failure",
                }],
            }]}
        if path == "actions/runs/10/attempts/2/jobs?per_page=100":
            return {"jobs": [{
                "id": 2,
                "name": "Change Contract preflight for deep CI",
                "conclusion": "success",
                "started_at": "2026-08-24T00:01:00Z",
                "completed_at": "2026-08-24T00:01:10Z",
                "steps": [],
            }]}
        if path.startswith("actions/runs/20/"):
            return {"jobs": []}
        if path.startswith("actions/runs/21/"):
            return {"jobs": []}
        raise AssertionError(path)

    monkeypatch.setattr(ci_yield, "_api", fake_api)
    collected = ci_yield._collect_pr("iridyne/aletheia", _pr())
    primary = [
        item for item in collected["attempts"]
        if item["workflow_name"] == ci_yield.PRIMARY_WORKFLOW
    ]

    assert primary[0]["phase1_failure_category"] == "preflight_metadata"
    assert primary[0]["next_event"] == "pr_body_only_repair"
    assert primary[0]["next_event_confidence"] == "high"
    assert primary[1]["next_event"] == "merge"
    assert collected["pr"]["ci_attempts_before_merge"] == 2
    assert collected["lanes"][0]["independent_defect"] == "no"
    assert collected["lanes"][0]["eligible_at_estimate"] == "2026-08-24T00:00:02Z"
    assert collected["lanes"][0]["runner_queue_wait_seconds_estimate"] == 0


def test_limit_50_pages_numbers_before_bounded_pr_details(monkeypatch) -> None:
    calls = []

    def fake_gh(args):
        calls.append(args)
        if args[:3] == ["api", "--paginate", "--slurp"]:
            return [[{"number": number, "merged_at": "2026-08-24T00:00:00Z"} for number in range(1, 51)]]
        pr = _pr()
        pr["number"] = int(args[2])
        return pr

    monkeypatch.setattr(ci_yield, "_gh_json", fake_gh)
    def fake_collect(_repo, pr):
        shape = ci_yield._pr_shape(pr)
        shape.update(ci_attempts_before_merge=0, failed_ci_attempts=0)
        return {"pr": shape, "attempts": [], "lanes": []}

    monkeypatch.setattr(ci_yield, "_collect_pr", fake_collect)

    report = ci_yield.collect("iridyne/aletheia", 50, workers=1)

    assert report["schema_version"] == "ci_yield.v2"
    assert report["data_quality"]["status"] == "complete"
    assert len(report["pull_requests"]) == 50
    assert calls[0][:3] == ["api", "--paginate", "--slurp"]


def test_listing_failure_is_structured_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(
        ci_yield, "_gh_json", lambda _args: (_ for _ in ()).throw(RuntimeError("node limit")),
    )

    report = ci_yield.collect("iridyne/aletheia", 50)

    assert report["schema_version"] == "ci_yield.v2"
    assert report["data_quality"] == {
        "status": "unavailable",
        "reason": "node limit",
    }
    assert report["pull_requests"] == []


def test_markdown_keeps_unknown_yield_visible() -> None:
    report = {
        "summary": {
            "ordinary_pr_count": 1,
            "primary_ci_attempt_count": 1,
            "full_required_ci_execution_seconds": {"p50": 60, "p95": 60},
            "workflow_queue_seconds": {"p50": 0, "p95": 0},
            "ci_attempts_per_merged_pr": {"median": 1},
            "metadata_only_red_count": 0,
            "lanes": {
                "Python integration": {
                    "runs": 1,
                    "failures": 1,
                    "runner_minutes": 2.0,
                    "independent_defects": 0,
                    "unknown_defect_failures": 1,
                    "ci_yield": None,
                },
            },
        },
    }

    markdown = ci_yield.markdown_report(report)
    assert "Independent-defect ambiguity remains `unknown`" in markdown
    assert "| Python integration | 1 | 1 | 2.0 | 0 | 1 | None |" in markdown
