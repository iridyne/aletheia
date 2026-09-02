"""Verify pull request Change Contract structure.

Runs in CI on every PR. Checks PR description for required SDD Depth and
Contract Delta structure. Exempts docs-only / governance-only PRs and trusted
release-please PRs from the requirement.

Exit 0: compliant or exempt
Exit 1: missing or malformed Change Contract
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Required headers — matched case-insensitively
REQUIRED_HEADERS = [
    "## SDD Depth",
    "## Contract Delta",
]

# SDD Depth values — at least one must appear in the section
VALID_SDD_DEPTHS = ["D0", "D1", "D2"]

# Exemption rules — if all touched files match one of these patterns, skip check
EXEMPT_PATTERNS = [
    re.compile(r"^\.github/"),
    re.compile(r"^docs/"),
    re.compile(r"^scripts/check_"),
    re.compile(r"^tests/test_ci_"),
    re.compile(r"^tests/test_architecture_fitness\.py$"),
    re.compile(r"^README\.md$"),
    re.compile(r"^AGENTS\.md$"),
]


def _is_exempt_path(path: str) -> bool:
    """Return True if a file path matches an exemption pattern."""
    return any(pattern.match(path) for pattern in EXEMPT_PATTERNS)


def _extract_section(body: str, header: str) -> str:
    """Extract the content between a header and the next header or EOF."""
    pattern = re.compile(
        rf"^{re.escape(header)}\s*$.*?(?=^##\s|\Z)",
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    match = pattern.search(body)
    return match.group(0) if match else ""


def _changed_files_from_checkout() -> list[str]:
    """Return files changed by the PR using GitHub's synthetic merge checkout.

    ``actions/checkout`` checks out the pull-request merge commit in CI. Parent 1
    is the exact current base snapshot for that synthetic merge, so resolving
    ``HEAD^1...HEAD`` avoids the stale ``pull_request.base.sha`` problem on
    long-lived PRs and requires no GitHub API call or second metadata source.
    """
    base = subprocess.run(
        ["git", "rev-parse", "HEAD^1"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not base:
        raise RuntimeError("unable to resolve synthetic PR merge base")

    output = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...HEAD", "--"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [line.strip() for line in output.splitlines() if line.strip()]


def _is_trusted_release_please_pr(payload: dict[str, object]) -> bool:
    """Recognize the repository's release workflow without trusting PR prose."""
    pull_request = payload.get("pull_request")
    repository = payload.get("repository")
    if not isinstance(pull_request, dict) or not isinstance(repository, dict):
        return False

    actor = pull_request.get("user")
    head = pull_request.get("head")
    base = pull_request.get("base")
    head_repo = head.get("repo") if isinstance(head, dict) else None
    repository_name = repository.get("full_name")
    runner_repository = os.getenv("GITHUB_REPOSITORY", "").strip()
    if runner_repository and repository_name != runner_repository:
        return False

    return (
        isinstance(actor, dict)
        and actor.get("login") == "github-actions[bot]"
        and isinstance(base, dict)
        and base.get("ref") == "master"
        and isinstance(head, dict)
        and isinstance(head.get("ref"), str)
        and head["ref"].startswith("release-please--branches--master")
        and isinstance(repository_name, str)
        and bool(repository_name)
        and isinstance(head_repo, dict)
        and head_repo.get("full_name") == repository_name
    )


def check_pr_description(
    pr_body: str,
    changed_files: list[str],
    *,
    release_please_pr: bool = False,
) -> list[str]:
    """Check PR description for required Change Contract structure.

    Returns a list of failure messages. Empty list means pass.
    """
    return [
        failure["message"]
        for failure in structured_check_pr(
            pr_body, changed_files, release_please_pr=release_please_pr
        )
    ]


# Canonical structured reason-code messages for machine-readable preflight / handoff
# use. This is the single mapping between a Change-Contract failure and its bounded
# code/message pair.
FAILURE_MESSAGES = {
    "missing_sdd_depth": "Missing required section: ## SDD Depth",
    "missing_contract_delta": "Missing required section: ## Contract Delta",
    "invalid_sdd_depth_value": "## SDD Depth section must contain one of: {depths}",
}


def structured_check_pr(
    pr_body: str,
    changed_files: list[str],
    *,
    release_please_pr: bool = False,
) -> list[dict[str, str]]:
    """Check PR description for required Change Contract structure.

    Returns a list of ``{"code": ..., "message": ...}`` failure objects. Empty
    list means pass. This is the single source of Change-Contract semantics;
    the local :mod:`scripts.ci_preflight` and the GitHub metadata gate both
    consume it so they can never diverge from each other.
    """
    failures: list[dict[str, str]] = []

    # Exempt only a release PR identified by trusted event metadata.
    if release_please_pr:
        return failures

    # Exempt docs-only / governance-only PRs
    if changed_files and all(_is_exempt_path(f) for f in changed_files):
        return failures

    # Check required headers exist
    normalized_body = pr_body.lower()
    for header in REQUIRED_HEADERS:
        if header.lower() not in normalized_body:
            code = (
                "missing_sdd_depth" if header == "## SDD Depth"
                else "missing_contract_delta"
            )
            failures.append({"code": code, "message": FAILURE_MESSAGES[code]})

    # Check SDD Depth value is present
    sdd_section = _extract_section(pr_body, "## SDD Depth")
    if sdd_section and not any(
        depth in sdd_section for depth in VALID_SDD_DEPTHS
    ):
        failures.append({
            "code": "invalid_sdd_depth_value",
            "message": FAILURE_MESSAGES["invalid_sdd_depth_value"].format(
                depths=", ".join(VALID_SDD_DEPTHS)
            ),
        })

    return failures


def main() -> int:
    """Run from CI. Reads GitHub event payload from GITHUB_EVENT_PATH."""
    event_name = os.getenv("GITHUB_EVENT_NAME", "")
    if event_name != "pull_request":
        # Merge queue / workflow_dispatch: skip check
        print("Skipping Change Contract check (not a pull_request event)", file=sys.stderr)
        return 0

    event_path = os.getenv("GITHUB_EVENT_PATH")
    if not event_path:
        print("GITHUB_EVENT_PATH not set", file=sys.stderr)
        return 1

    payload = json.loads(Path(event_path).read_text(encoding="utf-8"))
    body_file = os.getenv("CHANGE_CONTRACT_PR_BODY_FILE", "")
    try:
        pr_body = (
            Path(body_file).read_text(encoding="utf-8")
            if body_file
            else payload.get("pull_request", {}).get("body", "") or ""
        )
    except OSError as exc:
        print(f"Unable to read current pull-request body: {exc}", file=sys.stderr)
        return 1

    release_please_pr = _is_trusted_release_please_pr(payload)
    # Release automation is exempt by trusted identity and does not need a diff read.
    if release_please_pr:
        changed_files: list[str] = []
    else:
        try:
            changed_files = _changed_files_from_checkout()
        except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
            print(
                f"Unable to resolve pull-request changed files: {exc}",
                file=sys.stderr,
            )
            return 1

    failures = structured_check_pr(
        pr_body,
        changed_files,
        release_please_pr=release_please_pr,
    )
    if failures:
        print("Change Contract check failed:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure['message']}", file=sys.stderr)
        print("\nEvery PR must include:", file=sys.stderr)
        print("  ## SDD Depth", file=sys.stderr)
        print("  (one of: D0, D1, D2)", file=sys.stderr)
        print("  ## Contract Delta", file=sys.stderr)
        print("\nSee .github/PULL_REQUEST_TEMPLATE.md for the full structure.", file=sys.stderr)
        return 1

    print("Change Contract check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
