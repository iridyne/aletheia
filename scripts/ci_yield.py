#!/usr/bin/env python3
"""Collect reproducible CI throughput/yield facts from GitHub Actions.

The collector is deliberately read-only and stdlib-only. Authentication and
pagination are delegated to the already-required ``gh`` CLI. Historical logs do
not reliably prove whether a failed lane found a unique actionable defect, so
ambiguous failures remain ``unknown`` instead of being promoted into CI Yield.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ci_failure_handoff import fingerprint  # noqa: E402

SCHEMA_VERSION = "ci_yield.v2"
PRIMARY_WORKFLOW = "Aletheia CI"
METADATA_WORKFLOW = "Aletheia Change Contract gate"
PR_FIELDS = (
    "number,title,body,headRefName,headRefOid,baseRefOid,createdAt,mergedAt,"
    "additions,deletions,changedFiles,commits,closingIssuesReferences,files,url,author"
)
NON_ACTIONABLE_CATEGORIES = {
    "preflight_metadata",
    "runner_or_ci_infra",
    "external_io_in_unit",
}
AUTHORITY_SURFACES = {
    "ci_control",
    "execution",
    "risk",
    "outcome_economics",
    "identity_reconciliation",
    "runtime",
}
JOB_NEEDS = {
    "Plan CI lanes": ("Change Contract preflight for deep CI",),
    "Change Contract preflight for deep CI": (),
    "Review core": ("Plan CI lanes",),
    "Review trust-boundary coverage": ("Review core",),
    "Review Node surfaces": ("Review core",),
    "Review native host": ("Review core",),
    "Review checks": (
        "Review core", "Review trust-boundary coverage",
        "Review Node surfaces", "Review native host",
    ),
    "Python unit": ("Plan CI lanes",),
    "Python runtime state": ("Review checks", "Python unit"),
    "Python integration": ("Review checks", "Python unit"),
    "Python specifications": ("Review checks", "Python unit", "Python runtime state"),
    "Python end-to-end": ("Review checks", "Python unit", "Python integration"),
    "Trusted CI verdict": (
        "Plan CI lanes", "Review checks", "Python unit", "Python runtime state",
        "Python integration", "Python specifications", "Python end-to-end",
        "Review trust-boundary coverage", "Review Node surfaces", "Review native host",
    ),
    "Full CI verdict": ("Trusted CI verdict",),
}


def _gh_json(args: list[str]) -> Any:
    for attempt in range(3):
        result = subprocess.run(
            ["gh", *args], check=False, capture_output=True, text=True,
        )
        if not result.returncode:
            break
        if attempt == 2:
            message = " ".join(result.stderr.split())[:500]
            raise RuntimeError(f"gh {' '.join(args[:3])} failed: {message}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("gh returned invalid JSON") from exc


def _api(repo: str, path: str) -> Any:
    return _gh_json(["api", f"repos/{repo}/{path}"])


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _seconds(start: str | None, end: str | None) -> float | None:
    left, right = _dt(start), _dt(end)
    return max(0.0, (right - left).total_seconds()) if left and right else None


def _logical_lane(name: str | None) -> str:
    return re.sub(r" \(\d+\)$", "", str(name or "unknown"))


def _attempt_created_at(run: dict[str, Any], attempt: dict[str, Any], attempt_number: int) -> str | None:
    created = attempt.get("created_at")
    if attempt_number > 1 and created == run.get("created_at"):
        return None
    return created


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]


def _pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) < 3 or len(left) != len(right):
        return None
    left_mean, right_mean = statistics.mean(left), statistics.mean(right)
    numerator = sum(
        (x - left_mean) * (y - right_mean) for x, y in zip(left, right, strict=True)
    )
    left_scale = sum((x - left_mean) ** 2 for x in left)
    right_scale = sum((y - right_mean) ** 2 for y in right)
    denominator = math.sqrt(left_scale * right_scale)
    return round(numerator / denominator, 4) if denominator else None


def _is_ordinary_code_pr(pr: dict[str, Any]) -> bool:
    if "release-please" in str(pr.get("headRefName", "")).lower():
        return False
    if str((pr.get("author") or {}).get("login", "")).endswith("[bot]"):
        return False
    paths = [item.get("path", "") for item in pr.get("files") or []]
    return bool(paths) and any(
        not (
            path.startswith("docs/")
            or path in {"README.md", "AGENTS.md"}
        )
        for path in paths
    )


def _surfaces(paths: list[str]) -> tuple[list[str], list[str]]:
    surfaces: set[str] = set()
    for path in paths:
        if path.startswith(".github/workflows/") or path.startswith("scripts/ci_"):
            surfaces.add("ci_control")
        if path in {"pyproject.toml", "uv.lock", "tests/conftest.py"}:
            surfaces.add("test_dependency_infra")
        if path.startswith("aletheia/execution/"):
            surfaces.add("execution")
        if path.startswith("aletheia/risk/"):
            surfaces.add("risk")
        if any(token in path for token in ("economics", "outcome", "profit")):
            surfaces.add("outcome_economics")
        if any(token in path for token in ("reconcil", "identity")):
            surfaces.add("identity_reconciliation")
        if any(token in path for token in ("runtime", "gateway", "scheduler")):
            surfaces.add("runtime")
        if path.startswith("aletheia/learning/"):
            surfaces.add("learning")
        if path.startswith(("aletheia/data/", "aletheia/market_data/")):
            surfaces.add("market_data")
        if path.startswith(("dashboard/", "operator-shell/")):
            surfaces.add("frontend_operator")
        if path.startswith("native/"):
            surfaces.add("native")
        if path.startswith("docs/"):
            surfaces.add("docs_governance")
        if path.startswith("tests/"):
            surfaces.add("tests")
    if not surfaces:
        surfaces.add("other")
    return sorted(surfaces), sorted(surfaces & AUTHORITY_SURFACES)


def _linked_issue_count(pr: dict[str, Any]) -> int:
    numbers = {
        int(item["number"])
        for item in pr.get("closingIssuesReferences") or []
        if item.get("number") is not None
    }
    for match in re.finditer(
        r"(?i)\b(?:references|fixes|closes|resolves)\s+(?:[\w.-]+/[\w.-]+)?#(\d+)",
        str(pr.get("body") or ""),
    ):
        numbers.add(int(match.group(1)))
    return len(numbers)


def _failure(job: dict[str, Any]) -> dict[str, Any] | None:
    if job.get("conclusion") not in {"failure", "timed_out"}:
        return None
    step = next(
        (
            item for item in job.get("steps") or []
            if item.get("conclusion") in {"failure", "timed_out"}
        ),
        {},
    )
    job_name = str(job.get("name") or "unknown")[:160]
    step_name = str(step.get("name") or "unknown")[:160]
    surface = f"{job_name} {step_name}".lower()
    if "verify change contract" in surface:
        category, confidence = "preflight_metadata", "high"
    elif any(token in surface for token in (
        "check out source", "install uv", "synchronize locked dependencies",
        "install node", "install stable rust", "set up job",
    )):
        category, confidence = "runner_or_ci_infra", "medium"
    elif any(token in surface for token in (
        "lint python", "check documentation governance", "ruff",
    )):
        category, confidence = "lint_static", "high"
    elif "verify architecture and ci invariants" in surface:
        category, confidence = "test_contract_drift", "high"
    else:
        category, confidence = "unknown", "unknown"
    return {
        "failing_job": job_name,
        "failing_step": step_name,
        "category": category,
        "failure_fingerprint": fingerprint(category, job_name, step_name),
        "confidence": confidence,
    }


def _pr_shape(pr: dict[str, Any]) -> dict[str, Any]:
    files = pr.get("files") or []
    paths = [item.get("path", "") for item in files]
    surfaces, authorities = _surfaces(paths)
    changed_files = int(pr.get("changedFiles") or 0)
    return {
        "pr_number": pr["number"],
        "title": pr.get("title"),
        "url": pr.get("url"),
        "created_at": pr.get("createdAt"),
        "merged_at": pr.get("mergedAt"),
        "head_branch": pr.get("headRefName"),
        "final_head_sha": pr.get("headRefOid"),
        "base_sha_at_pr_snapshot": pr.get("baseRefOid"),
        "synthetic_merge_sha": None,
        "synthetic_merge_identity_available": False,
        "changed_file_count": changed_files,
        "file_list_complete": len(paths) == changed_files,
        "commit_count": len(pr.get("commits") or []),
        "additions": int(pr.get("additions") or 0),
        "deletions": int(pr.get("deletions") or 0),
        "changed_lines": int(pr.get("additions") or 0) + int(pr.get("deletions") or 0),
        "linked_issue_count": _linked_issue_count(pr),
        "touched_subsystems": surfaces if len(paths) == changed_files else ["unknown"],
        "touched_authorities": authorities if len(paths) == changed_files else ["unknown"],
        "authority_count": len(authorities) if len(paths) == changed_files else None,
        "time_to_trusted_merge_seconds": _seconds(pr.get("createdAt"), pr.get("mergedAt")),
    }


def _collect_pr(repo: str, pr: dict[str, Any]) -> dict[str, Any]:
    shape = _pr_shape(pr)
    branch = pr.get("headRefName") or ""
    runs = _api(
        repo,
        f"actions/runs?branch={branch}&event=pull_request&per_page=100",
    ).get("workflow_runs", [])
    opened = (_dt(pr.get("createdAt")) or datetime.min.replace(tzinfo=UTC)) - timedelta(minutes=5)
    merged = (_dt(pr.get("mergedAt")) or datetime.max.replace(tzinfo=UTC)) + timedelta(minutes=5)
    pr_number = int(pr["number"])
    commit_oids = {
        commit.get("oid") for commit in pr.get("commits") or []
        if commit.get("oid")
    }
    commit_oids.add(pr.get("headRefOid"))
    runs = [
        run for run in runs
        if run.get("name") in {PRIMARY_WORKFLOW, METADATA_WORKFLOW}
        and opened <= (_dt(run.get("created_at")) or opened) <= merged
        and (
            any(item.get("number") == pr_number for item in run.get("pull_requests") or [])
            or (
                not run.get("pull_requests")
                and run.get("head_sha") in commit_oids
            )
        )
    ]

    attempts: list[dict[str, Any]] = []
    lanes: list[dict[str, Any]] = []
    for run in runs:
        for attempt_number in range(1, int(run.get("run_attempt") or 1) + 1):
            attempt = _api(repo, f"actions/runs/{run['id']}/attempts/{attempt_number}")
            attempt_created_at = _attempt_created_at(run, attempt, attempt_number)
            jobs = _api(
                repo,
                f"actions/runs/{run['id']}/attempts/{attempt_number}/jobs?per_page=100",
            ).get("jobs", [])
            failures = [item for item in (_failure(job) for job in jobs) if item]
            failures.sort(key=lambda item: next(
                (
                    job.get("started_at") or ""
                    for job in jobs if job.get("name") == item["failing_job"]
                ),
                "",
            ))
            completed_by_name: dict[str, str] = {}
            for job in jobs:
                logical = _logical_lane(job.get("name"))
                completed = job.get("completed_at")
                if completed and (
                    logical not in completed_by_name
                    or (_dt(completed) or datetime.min.replace(tzinfo=UTC))
                    > (_dt(completed_by_name[logical]) or datetime.min.replace(tzinfo=UTC))
                ):
                    completed_by_name[logical] = completed
            attempt_key = f"{run['id']}:{attempt_number}"
            attempts.append({
                "pr_number": pr["number"],
                "attempt_key": attempt_key,
                "workflow_name": run.get("name"),
                "workflow_run_id": run["id"],
                "run_attempt": attempt_number,
                "head_sha": attempt.get("head_sha") or run.get("head_sha"),
                "base_sha": pr.get("baseRefOid"),
                "synthetic_merge_sha": None,
                "queued_at": attempt_created_at,
                "started_at": attempt.get("run_started_at"),
                "completed_at": attempt.get("updated_at"),
                "queue_seconds": _seconds(
                    attempt_created_at, attempt.get("run_started_at")
                ),
                "execution_seconds": _seconds(
                    attempt.get("run_started_at"), attempt.get("updated_at")
                ),
                "result": attempt.get("conclusion") or attempt.get("status"),
                "first_actionable_failure": failures[0] if failures else None,
                "phase1_failure_category": failures[0]["category"] if failures else None,
                "next_event": None,
                "next_event_confidence": None,
            })
            for job in jobs:
                logical_lane = _logical_lane(job.get("name"))
                failure = _failure(job)
                result = job.get("conclusion") or job.get("status")
                if result in {"failure", "timed_out"}:
                    if logical_lane == "Review checks":
                        independent, independent_reason = "no", "aggregate_duplicate"
                    elif failure and failure["category"] in NON_ACTIONABLE_CATEGORIES:
                        independent, independent_reason = "no", "non_actionable_category"
                    else:
                        independent, independent_reason = "unknown", "bounded_evidence_unavailable"
                else:
                    independent, independent_reason = "no", "no_failure"
                execution = _seconds(job.get("started_at"), job.get("completed_at"))
                dependencies = JOB_NEEDS.get(logical_lane)
                dependency_ends = [
                    _dt(completed_by_name.get(name))
                    for name in dependencies or ()
                    if completed_by_name.get(name)
                ]
                eligible = (
                    attempt_created_at
                    if dependencies == ()
                    else max(dependency_ends).isoformat()
                    if dependencies is not None and len(dependency_ends) == len(dependencies)
                    else None
                )
                lanes.append({
                    "pr_number": pr["number"],
                    "attempt_key": attempt_key,
                    "workflow_run_id": run["id"],
                    "run_attempt": attempt_number,
                    "workflow_name": run.get("name"),
                    "head_sha": attempt.get("head_sha") or run.get("head_sha"),
                    "lane": logical_lane,
                    "job_name": job.get("name"),
                    "job_id": job.get("id"),
                    "runner_name": job.get("runner_name"),
                    "runner_group_name": job.get("runner_group_name"),
                    "runner_class": (
                        "pr-ephemeral"
                        if "pr-ephemeral" in {
                            str(label).strip().lower()
                            for label in job.get("labels") or []
                        }
                        else job.get("runner_group_name") or "unknown"
                    ),
                    "runner_registration_id": None,
                    "runner_registration_id_status": "unavailable_from_historical_jobs_api",
                    "started_at": job.get("started_at"),
                    "completed_at": job.get("completed_at"),
                    "eligible_at_estimate": eligible,
                    "runner_queue_wait_seconds_estimate": _seconds(
                        eligible, job.get("started_at")
                    ),
                    "orchestration_and_queue_wait_seconds": _seconds(
                        attempt.get("run_started_at"), job.get("started_at")
                    ),
                    "execution_seconds": execution,
                    "runner_minutes": round(execution / 60, 4) if execution is not None else None,
                    "result": result,
                    "phase1_failure_category": failure["category"] if failure else None,
                    "failure_fingerprint": failure["failure_fingerprint"] if failure else None,
                    "independent_defect": independent,
                    "independent_defect_reason": independent_reason,
                })

    primary = sorted(
        (item for item in attempts if item["workflow_name"] == PRIMARY_WORKFLOW),
        key=lambda item: (_dt(item["queued_at"]) or datetime.min.replace(tzinfo=UTC), item["run_attempt"]),
    )
    metadata = [item for item in attempts if item["workflow_name"] == METADATA_WORKFLOW]
    for index, attempt in enumerate(primary):
        if index + 1 < len(primary):
            following = primary[index + 1]
            between = [
                item for item in metadata
                if item["head_sha"] == attempt["head_sha"]
                and item["result"] == "success"
                and (_dt(attempt["completed_at"]) or opened)
                <= (_dt(item["queued_at"]) or opened)
                <= (_dt(following["queued_at"]) or merged)
            ]
            if (
                attempt["phase1_failure_category"] == "preflight_metadata"
                and attempt["workflow_run_id"] == following["workflow_run_id"]
                and between
            ):
                attempt["next_event"] = "pr_body_only_repair"
                attempt["next_event_confidence"] = "high"
            elif attempt["workflow_run_id"] == following["workflow_run_id"]:
                attempt["next_event"] = "same_head_rerun"
                attempt["next_event_confidence"] = "high"
            elif attempt["head_sha"] != following["head_sha"]:
                attempt["next_event"] = "next_head_repair_commit"
                attempt["next_event_confidence"] = "high"
            else:
                attempt["next_event"] = "same_head_new_run_or_base_movement"
                attempt["next_event_confidence"] = "unknown"
        elif attempt["result"] == "success" and pr.get("mergedAt"):
            attempt["next_event"] = "merge"
            attempt["next_event_confidence"] = "high"

    shape["ci_attempts_before_merge"] = len(primary)
    shape["failed_ci_attempts"] = sum(
        item["result"] in {"failure", "timed_out"} for item in primary
    )
    return {"pr": shape, "attempts": attempts, "lanes": lanes}


def _summary(prs: list[dict[str, Any]], attempts: list[dict[str, Any]], lanes: list[dict[str, Any]]) -> dict[str, Any]:
    primary = [item for item in attempts if item["workflow_name"] == PRIMARY_WORKFLOW]
    successful = [item for item in primary if item["result"] == "success"]
    lane_summary: dict[str, dict[str, Any]] = {}
    for lane in lanes:
        if (
            lane["workflow_name"] != PRIMARY_WORKFLOW
            or lane["result"] == "skipped"
            or str(lane["lane"]).startswith("Hand off verified release")
        ):
            continue
        item = lane_summary.setdefault(lane["lane"], {
            "runs": 0, "failures": 0, "runner_minutes": 0.0,
            "independent_defects": 0, "unknown_defect_failures": 0,
        })
        item["runs"] += 1
        item["failures"] += lane["result"] in {"failure", "timed_out"}
        item["runner_minutes"] += lane["runner_minutes"] or 0.0
        item["independent_defects"] += lane["independent_defect"] == "yes"
        item["unknown_defect_failures"] += lane["independent_defect"] == "unknown"
    for item in lane_summary.values():
        item["runner_minutes"] = round(item["runner_minutes"], 4)
        item["ci_yield"] = (
            round(item["independent_defects"] / item["runner_minutes"], 6)
            if item["runner_minutes"] and not item["unknown_defect_failures"] else None
        )
        item["ci_yield_lower_bound"] = (
            round(item["independent_defects"] / item["runner_minutes"], 6)
            if item["runner_minutes"] else None
        )

    batch_features = (
        "changed_file_count", "changed_lines", "commit_count", "authority_count",
    )
    outcomes = (
        "ci_attempts_before_merge", "time_to_trusted_merge_seconds", "had_failed_ci_attempt",
    )
    correlations = {
        feature: {
            outcome: _pearson(
                [float(pair[0]) for pair in pairs],
                [float(pair[1]) for pair in pairs],
            )
            for outcome in outcomes
            for pairs in [[
                (
                    item.get(feature),
                    item["failed_ci_attempts"] > 0
                    if outcome == "had_failed_ci_attempt" else item.get(outcome),
                )
                for item in prs
                if item.get(feature) is not None
                and (
                    outcome == "had_failed_ci_attempt" or item.get(outcome) is not None
                )
            ]]
        }
        for feature in batch_features
    }
    preflight_seconds = [
        item["execution_seconds"] for item in lanes
        if item["lane"] == "Change Contract preflight for deep CI"
        and item["execution_seconds"] is not None
    ]
    lane_wait_seconds = [
        item["orchestration_and_queue_wait_seconds"] for item in lanes
        if item["workflow_name"] == PRIMARY_WORKFLOW
        and item["orchestration_and_queue_wait_seconds"] is not None
        and item["result"] != "skipped"
    ]
    runner_queue_estimates = [
        item["runner_queue_wait_seconds_estimate"] for item in lanes
        if item["workflow_name"] == PRIMARY_WORKFLOW
        and item["runner_queue_wait_seconds_estimate"] is not None
        and item["result"] != "skipped"
    ]
    jobs_per_runner: dict[str, int] = {}
    for lane in lanes:
        if lane.get("runner_name"):
            jobs_per_runner[lane["runner_name"]] = jobs_per_runner.get(lane["runner_name"], 0) + 1
    cancelled_attempts = {
        item["attempt_key"] for item in primary if item["result"] == "cancelled"
    }
    full_success_keys = {
        item["attempt_key"] for item in lanes
        if item["workflow_name"] == PRIMARY_WORKFLOW
        and item["lane"] == "Full CI verdict"
        and item["result"] == "success"
    }
    successful_full = [
        item for item in successful
        if item["attempt_key"] in full_success_keys
    ]
    full_p50 = _percentile([item["execution_seconds"] for item in successful_full if item["execution_seconds"] is not None], 0.50)
    full_p95 = _percentile([item["execution_seconds"] for item in successful_full if item["execution_seconds"] is not None], 0.95)
    queue_p50 = _percentile(runner_queue_estimates, 0.50)
    queue_p95 = _percentile(runner_queue_estimates, 0.95)
    return {
        "ordinary_pr_count": len(prs),
        "primary_ci_attempt_count": len(primary),
        "successful_primary_ci_count": len(successful),
        "full_required_ci_execution_seconds": {
            "p50": full_p50,
            "p95": full_p95,
        },
        "workflow_queue_seconds": {
            "p50": _percentile([item["queue_seconds"] for item in primary if item["queue_seconds"] is not None], 0.50),
            "p95": _percentile([item["queue_seconds"] for item in primary if item["queue_seconds"] is not None], 0.95),
        },
        "initial_dispatch_wait_seconds": {
            "p50": _percentile([item["queue_seconds"] for item in primary if item["run_attempt"] == 1 and item["queue_seconds"] is not None], 0.50),
            "p95": _percentile([item["queue_seconds"] for item in primary if item["run_attempt"] == 1 and item["queue_seconds"] is not None], 0.95),
        },
        "rerun_attempt_wait_seconds": {
            "p50": _percentile([item["queue_seconds"] for item in primary if item["run_attempt"] > 1 and item["queue_seconds"] is not None], 0.50),
            "p95": _percentile([item["queue_seconds"] for item in primary if item["run_attempt"] > 1 and item["queue_seconds"] is not None], 0.95),
        },
        "runner_queue_wait_seconds_estimate": {
            "p50": queue_p50,
            "p95": queue_p95,
            "source": "eligible_at_estimate_from_direct_needs_completion",
        },
        "slo": {
            "full_ci_p50_le_420": None if full_p50 is None else full_p50 <= 420,
            "full_ci_p95_le_600": None if full_p95 is None else full_p95 <= 600,
            "runner_wait_p50_le_15_estimate": None if queue_p50 is None else queue_p50 <= 15,
            "runner_wait_p95_le_120_estimate": None if queue_p95 is None else queue_p95 <= 120,
        },
        "lane_orchestration_and_queue_wait_seconds": {
            "p50": _percentile(lane_wait_seconds, 0.50),
            "p95": _percentile(lane_wait_seconds, 0.95),
            "note": "dependency orchestration and runner queue cannot be separated by the Jobs API",
        },
        "local_preflight_seconds": {"p95": None, "source": "not recorded by GitHub Actions"},
        "planner_decisions": {
            "policy_version": None,
            "plan_mode_counts": {},
            "plan_fingerprints": [],
            "source": "historical merged PR metadata predates stable planner artifacts",
        },
        "trusted_ci_verdict_seconds": {
            "p95": _percentile([
                item["execution_seconds"] for item in lanes
                if item["lane"] == "Trusted CI verdict"
                and item["execution_seconds"] is not None
            ], 0.95),
        },
        "full_ci_verdict_seconds": {
            "p95": _percentile([
                item["execution_seconds"] for item in lanes
                if item["lane"] == "Full CI verdict"
                and item["execution_seconds"] is not None
            ], 0.95),
        },
        "github_change_contract_preflight_seconds": {"p95": _percentile(preflight_seconds, 0.95)},
        "ci_attempts_per_merged_pr": {
            "median": statistics.median([item["ci_attempts_before_merge"] for item in prs]) if prs else None,
        },
        "metadata_only_red_count": sum(
            item["phase1_failure_category"] == "preflight_metadata" for item in primary
        ),
        "same_head_rerun_without_classified_repair_count": sum(
            item["next_event"] == "same_head_rerun" for item in primary
        ),
        "unit_accidental_network_timeout_count": None,
        "unit_accidental_network_timeout_note": "historical bounded job metadata has no log evidence",
        "lanes": dict(sorted(lane_summary.items())),
        "runner_jobs": dict(sorted(jobs_per_runner.items())),
        "runner_minutes": round(sum(item["runner_minutes"] or 0 for item in lanes), 4),
        "cancelled_runner_minutes": round(sum(
            item["runner_minutes"] or 0 for item in lanes
            if item["attempt_key"] in cancelled_attempts
        ), 4),
        "ephemeral_destruction": {
            "status": "unavailable",
            "reason": "historical Jobs API has no registration lifecycle or external log retention evidence",
        },
        "cancelled_or_superseded_attempt_count": sum(
            item["result"] == "cancelled" for item in primary
        ),
        "batch_size_correlations": correlations,
    }


def collect(repo: str, limit: int, workers: int = 4) -> dict[str, Any]:
    try:
        pages = _gh_json([
            "api", "--paginate", "--slurp",
            f"repos/{repo}/pulls?state=closed&per_page=100",
        ])
        numbers = [
            item["number"] for page in pages for item in page
            if item.get("merged_at")
        ]
    except RuntimeError as exc:
        return {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now(UTC).isoformat(),
            "repository": repo,
            "requested_pr_limit": limit,
            "data_quality": {"status": "unavailable", "reason": str(exc)},
            "summary": {}, "pull_requests": [], "attempts": [], "lanes": [],
        }
    selected = []
    errors: list[dict[str, Any]] = []
    for number in numbers:
        try:
            pr = _gh_json(["pr", "view", str(number), "--repo", repo, "--json", PR_FIELDS])
        except RuntimeError as exc:
            errors.append({"pr_number": number, "error": str(exc)[:500]})
            continue
        if _is_ordinary_code_pr(pr):
            selected.append(pr)
        if len(selected) == limit:
            break
    collected: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [(pr, pool.submit(_collect_pr, repo, pr)) for pr in selected]
        for pr, future in futures:
            try:
                collected.append(future.result())
            except RuntimeError as exc:
                errors.append({"pr_number": pr["number"], "error": str(exc)[:500]})
    prs = [item["pr"] for item in collected]
    attempts = [attempt for item in collected for attempt in item["attempts"]]
    lanes = [lane for item in collected for lane in item["lanes"]]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "repository": repo,
        "requested_pr_limit": limit,
        "data_quality": {
            "status": "complete" if not errors and len(prs) == limit else "partial",
            "complete_prs": len(prs),
            "collection_errors": errors,
            "independent_defect_rule": "unknown unless bounded evidence proves unique actionable repair",
            "queue_note": "runner queue is an estimate derived from direct-needs completion, not a GitHub native field",
            "synthetic_merge_note": "historical Actions API does not expose the checked-out synthetic merge SHA",
        },
        "summary": _summary(prs, attempts, lanes),
        "pull_requests": prs,
        "attempts": attempts,
        "lanes": lanes,
    }


def markdown_report(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    if "full_required_ci_execution_seconds" not in summary:
        quality = report.get("data_quality") or {}
        reason = quality.get("reason") or quality.get("status") or "unavailable"
        return (
            "# CI Yield baseline\n\n"
            f"Data unavailable: `{reason}`. No SLO judgment is emitted.\n"
        )
    full = summary["full_required_ci_execution_seconds"]
    queue = summary["workflow_queue_seconds"]
    lines = [
        "# CI Yield baseline",
        "",
        f"Generated from {summary['ordinary_pr_count']} ordinary merged code PRs. "
        "Independent-defect ambiguity remains `unknown`.",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Primary CI attempts | {summary['primary_ci_attempt_count']} |",
        f"| Successful full CI P50 | {full['p50']}s |",
        f"| Successful full CI P95 | {full['p95']}s |",
        f"| Workflow queue P50 | {queue['p50']}s |",
        f"| Workflow queue P95 | {queue['p95']}s |",
        f"| Estimated eligible runner wait P95 | {summary.get('runner_queue_wait_seconds_estimate', {}).get('p95')}s |",
        f"| Median attempts / merged PR | {summary['ci_attempts_per_merged_pr']['median']} |",
        f"| Metadata-only red | {summary['metadata_only_red_count']} |",
        "",
        "## Lane cost and known yield",
        "",
        "| Lane | Runs | Failures | Runner-min | Independent defects | Unknown failures | CI Yield |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, lane in summary["lanes"].items():
        lines.append(
            f"| {name.replace('|', '\\|')} | {lane['runs']} | {lane['failures']} | "
            f"{lane['runner_minutes']} | {lane['independent_defects']} | "
            f"{lane['unknown_defect_failures']} | {lane['ci_yield']} |"
        )
    lines.extend([
        "",
        "Queue time is reported separately from execution. Lane wait is not labelled "
        "as queue because GitHub combines dependency orchestration and runner wait. "
        "Synthetic merge identity and historical unit-network incidents remain unavailable.",
    ])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect read-only CI Yield facts from GitHub.")
    parser.add_argument("--repo", default="iridyne/aletheia")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 50:
        parser.error("--limit must be between 1 and 50")
    try:
        report = collect(args.repo, args.limit, args.workers)
    except RuntimeError as exc:
        print(f"ci_yield: {exc}", file=sys.stderr)
        return 1
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    markdown = markdown_report(report)
    if args.json_output:
        args.json_output.write_text(encoded, encoding="utf-8")
    if args.markdown_output:
        args.markdown_output.write_text(markdown, encoding="utf-8")
    if not args.json_output and not args.markdown_output:
        print(encoded, end="")
    elif args.markdown_output:
        print(markdown, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
