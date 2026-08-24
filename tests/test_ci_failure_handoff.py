"""Focused Phase 1 contract tests for ci_failure_handoff.v1."""

from __future__ import annotations

import json

import pytest

import scripts.ci_failure_handoff as handoff


@pytest.mark.parametrize(
    ("category", "retry", "repair", "metadata"),
    [
        ("preflight_metadata", True, False, True),
        ("runner_or_ci_infra", True, False, False),
        ("lint_static", False, True, False),
        ("test_contract_drift", False, True, False),
        ("product_regression", False, True, False),
        ("external_io_in_unit", False, True, False),
        ("unknown", False, False, False),
    ],
)
def test_every_failure_category_has_fail_closed_retry_policy(
    category: str, retry: bool, repair: bool, metadata: bool,
) -> None:
    report = handoff.build_handoff(
        pr_number=2167,
        head_sha="a" * 40,
        workflow_run_id=99,
        attempt=2,
        job_name="job",
        step_name="step",
        category=category,
    )

    assert report["schema_version"] == "ci_failure_handoff.v1"
    assert report["category"] == category
    assert report["retry_without_code_change"] is retry
    assert report["repair_commit_required"] is repair
    assert report["metadata_edit_required"] is metadata
    assert report["confidence"] == ("unknown" if category == "unknown" else "high")


def test_classifies_representative_failures_conservatively() -> None:
    cases = [
        ("Verify Change Contract", "Verify Change Contract structure", "", "preflight_metadata"),
        ("Review core", "Lint Python", "ruff found errors", "lint_static"),
        (
            "Review core", "Verify architecture and CI invariants",
            "test_ci_workflow_contract failed", "test_contract_drift",
        ),
        (
            "Python unit", "Run unit regression tier",
            "attempted external network access to yahoo.com", "external_io_in_unit",
        ),
        ("Python integration", "Run integration regression tier", "assert x == y", "product_regression"),
        ("Review core", "Synchronize locked dependencies", "uv failed", "runner_or_ci_infra"),
        ("Review core", "Scan secrets", "DNS resolution failed", "runner_or_ci_infra"),
        ("Python integration", "Run integration regression tier", "DNS resolution failed", "unknown"),
        ("mystery", "odd", "no bounded evidence", "unknown"),
    ]
    for job, step, excerpt, expected in cases:
        assert handoff.build_handoff(
            pr_number=1,
            head_sha="a" * 40,
            workflow_run_id=2,
            attempt=3,
            job_name=job,
            step_name=step,
            excerpt=excerpt,
        )["category"] == expected


def test_redacts_and_bounds_evidence_and_surface_fields() -> None:
    report = handoff.build_handoff(
        pr_number=1,
        head_sha="a" * 40,
        workflow_run_id=2,
        attempt=1,
        job_name="job" * 100,
        step_name="step" * 100,
        excerpt=(
            "Authorization: Bearer ghp_abcdefghijklmnopqrstuvwxyz123456 "
            "api_key=top-secret token=another-secret " + "x" * 1000
        ),
    )

    assert "[REDACTED]" in report["evidence"]
    assert "ghp_" not in report["evidence"]
    assert "top-secret" not in report["evidence"]
    assert "another-secret" not in report["evidence"]
    assert len(report["failing_job"]) <= 161
    assert len(report["failing_step"]) <= 161
    assert len(report["evidence"]) <= 501


def test_discards_non_sha_head_text_instead_of_forwarding_it() -> None:
    report = handoff.build_handoff(
        pr_number=1,
        head_sha="token=not-a-sha",
        workflow_run_id=2,
        attempt=1,
        job_name="job",
        step_name="step",
    )
    assert report["head_sha"] is None


def test_cli_reads_github_facts_and_writes_machine_readable_output(tmp_path, monkeypatch) -> None:
    event = tmp_path / "event.json"
    event.write_text(json.dumps({
        "pull_request": {"number": 2167, "head": {"sha": "b" * 40}},
    }), encoding="utf-8")
    output = tmp_path / "handoff.json"
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_RUN_ID", "77")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")

    assert handoff.main([
        "--category", "preflight_metadata",
        "--failing-job", "Verify Change Contract",
        "--failing-step", "Verify Change Contract structure",
        "--output", str(output),
    ]) == 0

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["pr_number"] == 2167
    assert report["head_sha"] == "b" * 40
    assert report["workflow_run_id"] == 77
    assert report["attempt"] == 2
    assert report["metadata_edit_required"] is True


def test_invalid_explicit_category_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown category"):
        handoff.build_handoff(
            pr_number=1,
            head_sha="a" * 40,
            workflow_run_id=2,
            attempt=1,
            job_name="job",
            step_name="step",
            category="unsafe_retry_all",
        )
