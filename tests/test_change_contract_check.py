"""Tests for scripts/check_change_contract.py — PR Change Contract enforcement."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.check_change_contract as checker
from scripts.check_change_contract import check_pr_description

# Valid minimal PR description with D0
VALID_D0_PR = """
## SDD Depth

D0 — Implementation Conformance Repair

## Contract Delta

NONE — existing Contract remains unchanged.
"""

# Valid D1 PR with ADDED/MODIFIED/REMOVED
VALID_D1_PR = """
## SDD Depth

D1 — Contract Behavior Change

## Contract Delta

### ADDED
- New behavior X

### MODIFIED
- Old behavior Y → new semantics Z
"""

VALID_D2_PR = """
## SDD Depth

D2 — Substantial Design Change

## Contract Delta

### ADDED
- Approved design behavior X
"""

# Missing SDD Depth section
MISSING_SDD_DEPTH = """
## Contract Delta

NONE
"""

# Missing Contract Delta section
MISSING_CONTRACT_DELTA = """
## SDD Depth

D0
"""

# SDD Depth section exists but no valid depth value
INVALID_SDD_DEPTH_VALUE = """
## SDD Depth

This is a change.

## Contract Delta

NONE
"""

# Release-please PR (exempt)
RELEASE_PLEASE_PR = """
chore(main): release 1.2.3

This PR was opened by release-please.

## Contract Delta
not required
"""


def test_valid_d0_pr_passes() -> None:
    failures = check_pr_description(VALID_D0_PR, [])
    assert not failures


def test_valid_d1_pr_passes() -> None:
    failures = check_pr_description(VALID_D1_PR, [])
    assert not failures


def test_valid_d2_pr_passes() -> None:
    failures = check_pr_description(VALID_D2_PR, [])
    assert not failures


def test_missing_sdd_depth_fails() -> None:
    failures = check_pr_description(MISSING_SDD_DEPTH, [])
    assert failures
    assert any("## SDD Depth" in f for f in failures)


def test_missing_contract_delta_fails() -> None:
    failures = check_pr_description(MISSING_CONTRACT_DELTA, [])
    assert failures
    assert any("## Contract Delta" in f for f in failures)


def test_invalid_sdd_depth_value_fails() -> None:
    failures = check_pr_description(INVALID_SDD_DEPTH_VALUE, [])
    assert failures
    assert any("D0" in f or "D1" in f or "D2" in f for f in failures)


def test_structured_check_exposes_bounded_reason_codes_without_changing_messages() -> None:
    failures = checker.structured_check_pr(MISSING_SDD_DEPTH, ["aletheia/core.py"])

    assert failures == [{
        "code": "missing_sdd_depth",
        "message": "Missing required section: ## SDD Depth",
    }]
    assert check_pr_description(MISSING_SDD_DEPTH, ["aletheia/core.py"]) == [
        failures[0]["message"],
    ]


def test_release_please_pr_is_exempt() -> None:
    failures = check_pr_description(RELEASE_PLEASE_PR, [])
    assert not failures


def test_docs_only_pr_is_exempt() -> None:
    """Docs-only PRs (all changed files under docs/) are exempt."""
    failures = check_pr_description(MISSING_SDD_DEPTH, ["docs/governance/policy.md"])
    assert not failures


def test_github_template_only_pr_is_exempt() -> None:
    """Changes only to .github/ are exempt."""
    failures = check_pr_description(MISSING_SDD_DEPTH, [".github/workflows/ci.yml"])
    assert not failures


def test_mixed_exempt_and_code_requires_contract() -> None:
    """If any non-exempt file is touched, the full check applies."""
    failures = check_pr_description(
        MISSING_SDD_DEPTH, ["docs/governance/policy.md", "aletheia/core.py"]
    )
    assert failures


def test_case_insensitive_header_matching() -> None:
    """Headers are matched case-insensitively."""
    case_varied = """
## sdd depth

D0

## contract delta

NONE
"""
    failures = check_pr_description(case_varied, [])
    assert not failures


def test_pr_template_contains_required_change_contract_headers() -> None:
    """The default PR body must satisfy the structural gate it advertises."""
    template = (
        Path(__file__).resolve().parents[1] / ".github" / "PULL_REQUEST_TEMPLATE.md"
    ).read_text(encoding="utf-8")
    template_lines = {line.strip() for line in template.splitlines()}
    for header in checker.REQUIRED_HEADERS:
        assert header in template_lines


def test_changed_files_from_checkout_uses_exact_merge_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Changed-file resolution uses the synthetic merge parent, not payload base SHA."""
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append(args)
        assert kwargs == {"check": True, "capture_output": True, "text": True}
        if args == ["git", "rev-parse", "HEAD^1"]:
            return SimpleNamespace(stdout="base123\n")
        if args == ["git", "diff", "--name-only", "base123...HEAD", "--"]:
            return SimpleNamespace(
                stdout="docs/governance/policy.md\n.github/PULL_REQUEST_TEMPLATE.md\n"
            )
        raise AssertionError(f"unexpected git invocation: {args}")

    monkeypatch.setattr(checker.subprocess, "run", fake_run)

    assert checker._changed_files_from_checkout() == [
        "docs/governance/policy.md",
        ".github/PULL_REQUEST_TEMPLATE.md",
    ]
    assert calls == [
        ["git", "rev-parse", "HEAD^1"],
        ["git", "diff", "--name-only", "base123...HEAD", "--"],
    ]


def test_main_applies_docs_only_exemption_from_real_checkout_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CI entry point must use resolved changed files, not an empty placeholder."""
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps({"pull_request": {"body": MISSING_SDD_DEPTH, "number": 123}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setattr(
        checker,
        "_changed_files_from_checkout",
        lambda: ["docs/governance/policy.md"],
    )

    assert checker.main() == 0


def test_main_prefers_current_pr_body_file_over_stale_event_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps({"pull_request": {"body": MISSING_SDD_DEPTH, "number": 123}}),
        encoding="utf-8",
    )
    body_path = tmp_path / "current-pr-body.md"
    body_path.write_text(VALID_D1_PR, encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("CHANGE_CONTRACT_PR_BODY_FILE", str(body_path))
    monkeypatch.setattr(
        checker,
        "_changed_files_from_checkout",
        lambda: ["aletheia/core.py"],
    )

    assert checker.main() == 0
