"""Focused Phase 3 contracts for the repository-native CI Yield collector."""

from __future__ import annotations

from typing import Any

import pytest

import scripts.ci.ci_yield as ci_yield


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
            {"path": "scripts/ci/ci_yield.py"},
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


@pytest.mark.parametrize("paths", [
    ["aletheia/execution/gateway.py", "aletheia/risk/engine.py", "aletheia/data/provider.py"],
    ["aletheia/market_data/provider.py", "aletheia/execution/gateway.py", "aletheia/risk/engine.py"],
    ["core/aletheia/execution/gateway.py", "core/aletheia/risk/engine.py", "extensions/data/ccxt_provider.py"],
])
def test_pr_shape_preserves_authorities_across_source_layouts(paths) -> None:
    pr = _pr()
    pr.update(changedFiles=len(paths), files=[{"path": path} for path in paths])

    shape = ci_yield._pr_shape(pr)

    assert shape["touched_subsystems"] == ["execution", "market_data", "risk", "runtime"]
    assert shape["touched_authorities"] == ["execution", "risk", "runtime"]
    assert shape["authority_count"] == 3


@pytest.mark.parametrize(
    ("metadata_started_at", "expected_next_event"),
    [
        ("2026-08-24T00:00:30Z", "pr_body_only_repair"),
        ("2026-08-24T00:01:30Z", "same_head_rerun"),
    ],
)
def test_collect_pr_distinguishes_same_head_body_repair_from_blanket_retry(
    monkeypatch,
    metadata_started_at: str,
    expected_next_event: str,
) -> None:
    head = "a" * 40
    earlier = "b" * 40
    pr = _pr()
    pr["commits"] = [{"oid": earlier}, {"oid": head}]
    runs = [
        {
            "id": 9,
            "name": ci_yield.PRIMARY_WORKFLOW,
            "head_sha": earlier,
            "run_attempt": 1,
            "created_at": "2026-08-24T00:00:00Z",
            "pull_requests": [],
        },
        {
            "id": 10,
            "name": ci_yield.PRIMARY_WORKFLOW,
            "head_sha": head,
            "run_attempt": 2,
            "created_at": "2026-08-24T00:00:01Z",
            "pull_requests": [{"number": 2171}],
        },
        {
            "id": 20,
            "name": ci_yield.METADATA_WORKFLOW,
            "head_sha": head,
            "run_attempt": 1,
            "created_at": "2026-08-24T00:00:01Z",
            "pull_requests": [{"number": 2171}],
        },
        {
            "id": 21,
            "name": ci_yield.METADATA_WORKFLOW,
            "head_sha": head,
            "run_attempt": 1,
            "created_at": metadata_started_at,
            "pull_requests": [{"number": 2171}],
        },
        {
            "id": 99,
            "name": ci_yield.PRIMARY_WORKFLOW,
            "head_sha": head,
            "run_attempt": 1,
            "created_at": "2026-08-24T00:00:35Z",
            "pull_requests": [{"number": 9999}],
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
                "created_at": "2026-08-24T00:00:01Z",
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
                "created_at": metadata_started_at,
                "run_started_at": metadata_started_at,
                "updated_at": metadata_started_at,
                "conclusion": "success",
            },
        }
        if path in attempts:
            return attempts[path]
        if path == "actions/runs/9/attempts/1/jobs?per_page=100":
            return {"jobs": [{
                "id": 0,
                "name": "Change Contract preflight for deep CI",
                "conclusion": "failure",
                "started_at": "2026-08-24T00:00:01Z",
                "completed_at": "2026-08-24T00:00:10Z",
                "steps": [],
                "labels": ["self-hosted", "pr-ephemeral"],
            }]}
        if path == "actions/runs/9/attempts/1":
            return {
                "head_sha": earlier,
                "created_at": "2026-08-24T00:00:00Z",
                "run_started_at": "2026-08-24T00:00:01Z",
                "updated_at": "2026-08-24T00:00:10Z",
                "conclusion": "failure",
            }
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
                "labels": ["self-hosted", "pr-ephemeral"],
            }]}
        if path == "actions/runs/10/attempts/2/jobs?per_page=100":
            return {"jobs": [{
                "id": 2,
                "name": "Change Contract preflight for deep CI",
                "conclusion": "success",
                "started_at": "2026-08-24T00:01:00Z",
                "completed_at": "2026-08-24T00:01:10Z",
            "steps": [],
                "labels": ["self-hosted", "pr-ephemeral"],
            }]}
        if path.startswith("actions/runs/20/"):
            return {"jobs": []}
        if path.startswith("actions/runs/21/"):
            return {"jobs": []}
        if path.startswith("actions/runs/99/"):
            raise AssertionError("foreign PR run must not be collected")
        raise AssertionError(path)

    monkeypatch.setattr(ci_yield, "_api", fake_api)
    collected = ci_yield._collect_pr("iridyne/aletheia", pr)
    primary = [
        item for item in collected["attempts"]
        if item["workflow_name"] == ci_yield.PRIMARY_WORKFLOW
    ]

    body_repair = next(
        item for item in primary
        if item["workflow_run_id"] == 10 and item["run_attempt"] == 1
    )
    repaired = next(
        item for item in primary
        if item["workflow_run_id"] == 10 and item["run_attempt"] == 2
    )
    assert body_repair["phase1_failure_category"] == "preflight_metadata"
    assert body_repair["next_event"] == expected_next_event
    assert body_repair["next_event_confidence"] == "high"
    assert repaired["next_event"] == "merge"
    assert repaired["queued_at"] is None
    assert repaired["queue_seconds"] is None
    assert collected["pr"]["ci_attempts_before_merge"] == 3
    body_repair_lane = next(
        item for item in collected["lanes"]
        if item["workflow_run_id"] == 10 and item["run_attempt"] == 1
    )
    assert body_repair_lane["independent_defect"] == "no"
    assert body_repair_lane["eligible_at_estimate"] == "2026-08-24T00:00:01Z"
    assert body_repair_lane["runner_queue_wait_seconds_estimate"] == 1
    assert body_repair_lane["runner_class"] == "pr-ephemeral"


def test_matrix_lanes_are_normalized_before_dependency_estimates(monkeypatch) -> None:
    head = "a" * 40

    def fake_api(repo: str, path: str) -> Any:  # noqa: ARG001
        if path.startswith("actions/runs?branch="):
            return {"workflow_runs": [{
                "id": 30,
                "name": ci_yield.PRIMARY_WORKFLOW,
                "head_sha": head,
                "run_attempt": 1,
                "created_at": "2026-08-24T00:00:00Z",
            }]}
        if path == "actions/runs/30/attempts/1":
            return {
                "head_sha": head,
                "created_at": "2026-08-24T00:00:00Z",
                "run_started_at": "2026-08-24T00:00:01Z",
                "updated_at": "2026-08-24T00:02:00Z",
                "conclusion": "success",
            }
        if path == "actions/runs/30/attempts/1/jobs?per_page=100":
            return {"jobs": [
                {
                    "id": 1,
                    "name": "Change Contract preflight for deep CI",
                    "conclusion": "success",
                    "started_at": "2026-08-24T00:00:03Z",
                    "completed_at": "2026-08-24T00:00:05Z",
                    "steps": [],
                },
                {
                    "id": 2,
                    "name": "Plan CI lanes",
                    "conclusion": "success",
                    "started_at": "2026-08-24T00:00:05Z",
                    "completed_at": "2026-08-24T00:00:06Z",
                    "steps": [],
                },
                {
                    "id": 3,
                    "name": "Review core",
                    "conclusion": "success",
                    "started_at": "2026-08-24T00:00:06Z",
                    "completed_at": "2026-08-24T00:00:07Z",
                    "steps": [],
                },
                {
                    "id": 4,
                    "name": "Review checks",
                    "conclusion": "success",
                    "started_at": "2026-08-24T00:00:07Z",
                    "completed_at": "2026-08-24T00:00:08Z",
                    "steps": [],
                },
                {
                    "id": 5,
                    "name": "Python unit (0)",
                    "conclusion": "success",
                    "started_at": "2026-08-24T00:00:10Z",
                    "completed_at": "2026-08-24T00:00:20Z",
                    "steps": [],
                },
                {
                    "id": 6,
                    "name": "Python unit (1)",
                    "conclusion": "success",
                    "started_at": "2026-08-24T00:00:11Z",
                    "completed_at": "2026-08-24T00:00:30Z",
                    "steps": [],
                },
                {
                    "id": 7,
                    "name": "Python integration",
                    "conclusion": "success",
                    "started_at": "2026-08-24T00:00:40Z",
                    "completed_at": "2026-08-24T00:01:00Z",
                    "steps": [],
                },
            ]}
        raise AssertionError(path)

    monkeypatch.setattr(ci_yield, "_api", fake_api)
    collected = ci_yield._collect_pr("iridyne/aletheia", _pr())
    integration = next(item for item in collected["lanes"] if item["lane"] == "Python integration")
    review_core = next(item for item in collected["lanes"] if item["lane"] == "Review core")
    unit = next(item for item in collected["lanes"] if item["job_name"] == "Python unit (0)")

    assert [item["lane"] for item in collected["lanes"]].count("Python unit") == 2
    assert review_core["eligible_at_estimate"] == "2026-08-24T00:00:06+00:00"
    assert unit["eligible_at_estimate"] == "2026-08-24T00:00:06+00:00"
    assert integration["eligible_at_estimate"] == "2026-08-24T00:00:30+00:00"


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


def test_detail_failure_is_partial_and_does_not_abort_selection(monkeypatch) -> None:
    calls = []

    def fake_gh(args):
        calls.append(args)
        if args[:3] == ["api", "--paginate", "--slurp"]:
            return [[
                {"number": 1, "merged_at": "2026-08-24T00:00:00Z"},
                {"number": 2, "merged_at": "2026-08-24T00:00:00Z"},
            ]]
        if args[0:2] == ["pr", "view"] and args[2] == "1":
            raise RuntimeError("transient detail failure")
        pr = _pr()
        pr["number"] = int(args[2])
        return pr

    monkeypatch.setattr(ci_yield, "_gh_json", fake_gh)
    monkeypatch.setattr(
        ci_yield,
        "_collect_pr",
        lambda _repo, pr: {
            "pr": {"pr_number": pr["number"], "ci_attempts_before_merge": 0, "failed_ci_attempts": 0},
            "attempts": [],
            "lanes": [],
        },
    )

    report = ci_yield.collect("iridyne/aletheia", 1, workers=1)

    assert report["data_quality"]["status"] == "partial"
    assert report["data_quality"]["complete_prs"] == 1
    assert report["data_quality"]["collection_errors"] == [
        {"pr_number": 1, "error": "transient detail failure"},
    ]
    assert report["pull_requests"][0]["pr_number"] == 2


def test_missing_slo_evidence_is_unknown() -> None:
    summary = ci_yield._summary([], [], [])

    assert summary["slo"] == {
        "full_ci_p50_le_420": None,
        "full_ci_p95_le_600": None,
        "runner_wait_p50_le_15_estimate": None,
        "runner_wait_p95_le_120_estimate": None,
    }


def test_full_ci_latency_uses_only_full_verdict_attempts() -> None:
    attempts = [
        {
            "workflow_name": ci_yield.PRIMARY_WORKFLOW,
            "result": "success",
            "execution_seconds": 30,
            "queue_seconds": 1,
            "run_attempt": 1,
            "attempt_key": "quick",
            "phase1_failure_category": None,
            "next_event": None,
        },
        {
            "workflow_name": ci_yield.PRIMARY_WORKFLOW,
            "result": "success",
            "execution_seconds": 300,
            "queue_seconds": 2,
            "run_attempt": 1,
            "attempt_key": "full",
            "phase1_failure_category": None,
            "next_event": None,
        },
    ]
    lanes = [
        {
            "workflow_name": ci_yield.PRIMARY_WORKFLOW,
            "result": "success",
            "lane": "Trusted CI verdict",
            "runner_minutes": 0.1,
            "independent_defect": "no",
            "unknown_defect_failures": 0,
            "runner_queue_wait_seconds_estimate": 1,
            "orchestration_and_queue_wait_seconds": 1,
            "runner_name": "pr-quick",
            "attempt_key": "quick",
            "execution_seconds": 1,
        },
        {
            "workflow_name": ci_yield.PRIMARY_WORKFLOW,
            "result": "success",
            "lane": "Full CI verdict",
            "runner_minutes": 0.1,
            "independent_defect": "no",
            "unknown_defect_failures": 0,
            "runner_queue_wait_seconds_estimate": 2,
            "orchestration_and_queue_wait_seconds": 2,
            "runner_name": "pr-full",
            "attempt_key": "full",
            "execution_seconds": 1,
        },
    ]

    summary = ci_yield._summary([], attempts, lanes)

    assert summary["successful_primary_ci_count"] == 2
    assert summary["full_required_ci_execution_seconds"] == {"p50": 300, "p95": 300}


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


def test_markdown_reports_unavailable_without_summary_shape() -> None:
    markdown = ci_yield.markdown_report({
        "data_quality": {"status": "unavailable", "reason": "node limit"},
        "summary": {},
    })

    assert "Data unavailable: `node limit`" in markdown
