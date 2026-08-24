#!/usr/bin/env python3
"""Produce the canonical machine-readable CI failure handoff (``ci_failure_handoff.v1``).

Phase 1 (#2167 §1.5, addendum B): a future write-capable AI repair executor must
be able to consume a failing CI run deterministically. This script is the
repository-native reference producer of the versioned handoff schema and the
fail-closed retry policy mapping.

Schema (all fields bounded; never includes secrets or unbounded raw logs):

    schema_version: "ci_failure_handoff.v1"
    pr_number, head_sha, workflow_run_id, attempt
    failing_job, failing_step, category
    failure_fingerprint  (bounded, derived from category/job/step)
    evidence            (redacted concise excerpt or stable log pointer)
    retry_without_code_change, repair_commit_required, metadata_edit_required
    confidence          (high / medium / unknown)

Retry policy is fail-closed:

    preflight_metadata  -> edit PR metadata on the SAME head; never a code commit
    runner_or_ci_infra  -> a same-head rerun may be justified
    lint_static / test_contract_drift / product_regression / external_io_in_unit
                        -> require an actual relevant repair unless direct
                           evidence proves otherwise
    unknown             -> no automatic retry/merge assumption

Ambiguous failures must stay ``unknown`` — never automatically downgraded.

Usage:

    python scripts/ci_failure_handoff.py \
        --pr-number 42 --head-sha <sha> --run-id 123 --attempt 1 \
        --failing-job "Python unit" --failing-step "Run unit regression tier" \
        [--excerpt "connect to example.com failed"] [--category preflight_metadata] \
        [--output handoff.json] [--selftest]

When run inside GitHub Actions, PR/head/run facts are read from the standard
environment (GITHUB_EVENT_PATH, GITHUB_RUN_ID, GITHUB_RUN_ATTEMPT) so callers in
CI steps only need to pass ``--failing-job``/``--failing-step`` and optionally
``--category`` and ``--excerpt``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

SCHEMA_VERSION = "ci_failure_handoff.v1"

CATEGORIES = (
    "preflight_metadata",
    "lint_static",
    "test_contract_drift",
    "product_regression",
    "external_io_in_unit",
    "runner_or_ci_infra",
    "unknown",
)

# Fail-closed retry policy (addendum B). Single source of truth for advice flags.
CATEGORY_POLICY: dict[str, dict[str, bool]] = {
    "preflight_metadata": {
        "retry_without_code_change": True,
        "repair_commit_required": False,
        "metadata_edit_required": True,
    },
    "runner_or_ci_infra": {
        "retry_without_code_change": True,
        "repair_commit_required": False,
        "metadata_edit_required": False,
    },
    "lint_static": {
        "retry_without_code_change": False,
        "repair_commit_required": True,
        "metadata_edit_required": False,
    },
    "test_contract_drift": {
        "retry_without_code_change": False,
        "repair_commit_required": True,
        "metadata_edit_required": False,
    },
    "product_regression": {
        "retry_without_code_change": False,
        "repair_commit_required": True,
        "metadata_edit_required": False,
    },
    "external_io_in_unit": {
        "retry_without_code_change": False,
        "repair_commit_required": True,
        "metadata_edit_required": False,
    },
    "unknown": {
        "retry_without_code_change": False,
        "repair_commit_required": False,
        "metadata_edit_required": False,
    },
}

# Job/step evidence signals -> bounded category. First match wins; conservative.
_LINT_SIGNALS = ("ruff", "lint ", "check_docs", "documentation governance")
_DEFAULT_SIGNALS = ("verify change contract", "change contract", "sdd depth", "contract delta")
_INFRA_SIGNALS = (
    "check out source",
    "setup-uv",
    "install uv",
    "synchronize locked dependencies",
    "setup-node",
    "restored gitleaks",
    "download gitleaks",
    "rust-toolchain",
    "install stable rust",
)
_CONTRACT_TEST_SIGNALS = (
    "test_ci_workflow_contract",
    "test_ci_pr_base_contract",
    "test_ci_stage2_status_contract",
    "test_ci_execution_contracts",
    "test_change_contract_check",
    "architecture and ci invariants",
    "test_architecture_fitness",
)
_UNIT_EXTERNAL_IO_SIGNAL = "attempted external network access"
_NETWORK_INFRA_SIGNALS = ("connecttimeout", "connect timeout", "dns")

# Stable fingerprint: hash of the bounded failure surface (no log content).
_FINGERPRINT_SALT = "ci_failure_handoff.v1"
_FIELD_LIMIT = 160
_EVIDENCE_LIMIT = 500
_SECRET_PATTERNS = (
    # Header / environment assignment values (also catches most JSON-ish fields).
    re.compile(r"(?i)\b(authorization)\b([\s:=]+)(?:bearer\s+)?([^\s,;]+)"),
    re.compile(r"(?i)\b(api[-_]?key|token|password|secret)\b([\s:=]+)([^\s,;]+)"),
    # Common provider / GitHub bearer-token forms that may occur without a label.
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,})\b"),
)


def classify(job_name: str, step_name: str, excerpt: str, explicit: str | None = None) -> str:
    """Return the bounded failure category for a failing job/step.

    ``explicit`` wins (a CI step that already knows its category, e.g. the
    metadata gate). Otherwise classify from job/step names and a bounded
    excerpt. Ambiguous input stays ``unknown`` — never downgraded.
    """
    if explicit:
        if explicit not in CATEGORIES:
            raise ValueError(f"unknown category {explicit!r}")
        return explicit

    needle = f"{job_name} {step_name} {excerpt}".lower()
    job_needle = job_name.lower()
    test_lane = any(
        lane in job_needle
        for lane in ("python unit", "python integration", "python specifications", "python end-to-end", "python runtime state")
    )
    if any(s in needle for s in _DEFAULT_SIGNALS):
        return "preflight_metadata"
    if any(s in needle for s in _INFRA_SIGNALS):
        return "runner_or_ci_infra"
    if _UNIT_EXTERNAL_IO_SIGNAL in needle and "python unit" in job_needle:
        return "external_io_in_unit"
    if any(s in needle for s in _NETWORK_INFRA_SIGNALS):
        # A DNS/connect failure outside a test lane is runner/CI infrastructure.
        # Inside a non-unit test lane, ambiguity remains fail-closed as unknown.
        return "unknown" if test_lane else "runner_or_ci_infra"
    if any(s in needle for s in _LINT_SIGNALS):
        return "lint_static"
    if any(s in needle for s in _CONTRACT_TEST_SIGNALS):
        return "test_contract_drift"
    if any(token in job_name.lower() for token in ("unit", "integration", "spec", "end-to-end", "runtime")):
        return "product_regression"
    return "unknown"


def confidence_for(category: str, explicit: bool) -> str:
    if category == "unknown":
        return "unknown"
    return "high" if explicit else "medium"


def fingerprint(category: str, job_name: str, step_name: str) -> str:
    import hashlib

    raw = f"{_FINGERPRINT_SALT}|{category}|{job_name.lower()}|{step_name.lower()}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _bounded_text(value: str, limit: int) -> str:
    """Normalize a handoff field to one printable, bounded line."""
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


def _redact_excerpt(excerpt: str) -> str:
    """Redact common credential forms before emitting bounded diagnostic evidence."""
    text = _bounded_text(excerpt, _EVIDENCE_LIMIT)
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 3:
            text = pattern.sub(r"\1\2[REDACTED]", text)
        else:
            text = pattern.sub("[REDACTED]", text)
    return text


def build_handoff(
    *,
    pr_number: int | None,
    head_sha: str | None,
    workflow_run_id: int | None,
    attempt: int | None,
    job_name: str,
    step_name: str,
    excerpt: str = "",
    category: str | None = None,
) -> dict:
    if category is not None and category not in CATEGORIES:
        raise ValueError(f"unknown category {category!r}")
    explicit = category is not None
    job_name = _bounded_text(_redact_excerpt(job_name), _FIELD_LIMIT)
    step_name = _bounded_text(_redact_excerpt(step_name), _FIELD_LIMIT)
    excerpt = _redact_excerpt(excerpt)
    resolved = classify(job_name, step_name, excerpt, category)
    policy = CATEGORY_POLICY[resolved]
    return {
        "schema_version": SCHEMA_VERSION,
        "pr_number": pr_number,
        "head_sha": _safe_head_sha(head_sha),
        "workflow_run_id": workflow_run_id,
        "attempt": attempt,
        "failing_job": job_name or None,
        "failing_step": step_name or None,
        "category": resolved,
        "failure_fingerprint": fingerprint(resolved, job_name, step_name),
        "evidence": excerpt,
        **policy,
        "confidence": confidence_for(resolved, explicit),
    }


def _env_int(name: str) -> int | None:
    value = os.getenv(name, "").strip()
    return int(value) if value.isdigit() else None


def _safe_head_sha(value: str | None) -> str | None:
    """Keep only an exact-looking Git object ID; never forward arbitrary CLI text."""
    candidate = str(value or "").lower()
    return candidate if re.fullmatch(r"[0-9a-f]{40}", candidate) else None


def _env_facts() -> tuple[int | None, str | None, int | None, int | None]:
    pr_number: int | None = None
    head_sha: str | None = None
    event_path = os.getenv("GITHUB_EVENT_PATH", "")
    if event_path:
        try:
            payload = json.loads(Path(event_path).read_text(encoding="utf-8"))
            pr = payload.get("pull_request") or {}
            pr_number = int(pr["number"]) if pr.get("number") is not None else None
            head_sha = pr.get("head", {}).get("sha") or payload.get("head_sha")
        except (OSError, ValueError, KeyError):
            pass
    run_id = _env_int("GITHUB_RUN_ID")
    attempt = _env_int("GITHUB_RUN_ATTEMPT")
    return pr_number, head_sha, run_id, attempt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Emit ci_failure_handoff.v1 JSON.")
    parser.add_argument("--pr-number", type=int, default=None)
    parser.add_argument("--head-sha", default=None)
    parser.add_argument("--run-id", type=int, default=None)
    parser.add_argument("--attempt", type=int, default=None)
    parser.add_argument("--failing-job", default="")
    parser.add_argument("--failing-step", default="")
    parser.add_argument("--excerpt", default="")
    parser.add_argument(
        "--category", default=None,
        help="explicit bounded category (wins over classification; must be in the taxonomy)",
    )
    parser.add_argument("--output", default=None, help="write JSON to PATH instead of stdout")
    parser.add_argument("--selftest", action="store_true", help="run the self-check and exit")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()

    pr_number, head_sha, run_id, attempt = _env_facts()
    handoff = build_handoff(
        pr_number=args.pr_number if args.pr_number is not None else pr_number,
        head_sha=args.head_sha or head_sha,
        workflow_run_id=args.run_id if args.run_id is not None else run_id,
        attempt=args.attempt if args.attempt is not None else attempt,
        job_name=args.failing_job,
        step_name=args.failing_step,
        excerpt=args.excerpt,
        category=args.category,
    )
    text = json.dumps(handoff, indent=2)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


def selftest() -> int:
    """Smallest runnable proof of the handoff schema/policy (Phase 1 acceptance)."""
    cases = [
        # (job, step, excerpt, explicit, expected_category, repair_commit_required)
        ("Verify Change Contract", "Verify Change Contract structure", "", "preflight_metadata", "preflight_metadata", False),
        ("Verify Change Contract", "Verify Change Contract structure", "", None, "preflight_metadata", False),
        ("Review core", "Lint Python", "ruff found errors", None, "lint_static", True),
        ("Review core", "Verify architecture and CI invariants", "test_ci_workflow_contract failed", None, "test_contract_drift", True),
        ("Python unit", "Run unit regression tier", "attempted external network access to 'yahoo.com'", None, "external_io_in_unit", True),
        ("Python unit", "Run unit regression tier", "assert 1 == 2", None, "product_regression", True),
        ("Review core", "Check out source", "connection reset", None, "runner_or_ci_infra", False),
        ("Review core", "Scan secrets", "DNS resolution failed", None, "runner_or_ci_infra", False),
        ("Python integration", "Synchronize locked dependencies", "uv sync network error", None, "runner_or_ci_infra", False),
        ("Python integration", "Run integration regression tier", "DNS resolution failed", None, "unknown", False),
        ("Mystery job", "Odd step", "weird output", None, "unknown", False),
    ]
    failures = []
    for job, step, excerpt, explicit, expected_cat, expected_repair in cases:
        handoff = build_handoff(
            pr_number=2167, head_sha="0" * 40, workflow_run_id=1, attempt=1,
            job_name=job, step_name=step, excerpt=excerpt, category=explicit,
        )
        assert handoff["schema_version"] == SCHEMA_VERSION
        assert set(CATEGORY_POLICY) == set(CATEGORIES)
        for flag in ("retry_without_code_change", "repair_commit_required", "metadata_edit_required"):
            assert isinstance(handoff[flag], bool), flag
        if handoff["category"] != expected_cat or handoff["repair_commit_required"] != expected_repair:
            failures.append((job, step, handoff["category"], expected_cat))
        if handoff["category"] == "unknown":
            assert handoff["confidence"] == "unknown"
        for field in ("failing_job", "failing_step", "failure_fingerprint", "evidence"):
            assert isinstance(handoff[field], str) and len(handoff[field]) <= 500
    if failures:
        print("selftest FAILED:", failures, file=sys.stderr)
        return 1
    print(f"ci_failure_handoff selftest passed ({len(cases)} representative cases)")
    return 0


if __name__ == "__main__":
    sys.exit(main())