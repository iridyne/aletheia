#!/usr/bin/env python3
"""Local deterministic preflight before PR creation / exact-head repair.

Run this from a feature branch before the first CI-triggering PR creation or
update. It reuses the exact Change Contract parser/semantics from
``scripts/check_change_contract.py`` (single source of truth), resolves the
actual branch/base diff so the docs/governance exemption is evaluated against
the real changed-file set, and emits a bounded structured report for AI
automation.

Deliberately cheap:
- no external network access;
- no Runtime startup, no database access;
- no dependency sync (stdlib only).

Exit codes:
- 0  preflight passed (or PR plainly exempt);
- 1  Change Contract metadata is missing or malformed;
- 2  preflight could not resolve the branch/base diff;
- 3  usage error.

Output (only on failure, to stdout):
  {"schema_version": "ci_preflight.v1", "phase": "preflight_metadata",
   "failures": [{"code": ..., "message": ...}], ...}
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_change_contract import (  # noqa: E402
    structured_check_pr,
)

SCHEMA_VERSION = "ci_preflight.v1"
EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_NO_BASE = 2
EXIT_USAGE = 3


def changed_files_vs_base(base_ref: str | None = None) -> list[str]:
    """Return files changed on this branch relative to ``base_ref``.

    Prefers the merge-base with the given base so a long-lived branch compares
    against the point it actually diverged from, mirroring the synthetic
    merge-base semantics used by the GitHub gate (``HEAD^1``). If ``base_ref``
    is omitted, use the repository's ``origin/master`` and then local ``master``.
    A feature branch's upstream is its remote tracking branch, not its PR base;
    comparing against it after the first push would incorrectly produce an empty
    changed-file set and diverge from GitHub's exemption decision.
    """
    refs = [base_ref] if base_ref else ["origin/master", "master"]
    last_error: Exception | None = None
    for ref in refs:
        try:
            merge_base = subprocess.run(
                ["git", "merge-base", "HEAD", ref],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError) as exc:
            last_error = exc
            continue
        if not merge_base:
            continue
        diff = subprocess.run(
            ["git", "diff", "--name-only", f"{merge_base}...HEAD", "--"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        return [line.strip() for line in diff.splitlines() if line.strip()]
    raise RuntimeError(
        f"unable to resolve a base to compare against ({', '.join(refs)})"
    ) from last_error


def run_preflight(pr_body: str, changed_files: list[str]) -> list[dict[str, str]]:
    """Return the Change Contract failures (empty means pass).

    Delegates entirely to the shared Change Contract semantics.
    """
    return structured_check_pr(pr_body, changed_files)


def _read_pr_body(source: str | None) -> str:
    if source is None:
        return ""
    if source == "-":
        return sys.stdin.read()
    return Path(source).read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ci_preflight.py",
        description=(
            "Local deterministic preflight before PR creation/update. "
            "Validates the PR Change Contract metadata against the real "
            "branch/base diff and reports bounded structured reason codes."
        ),
    )
    body_group = parser.add_mutually_exclusive_group()
    body_group.add_argument(
        "--pr-body-file",
        dest="pr_body_file",
        metavar="PATH",
        help="read the PR body from a file (use '-' for stdin)",
    )
    body_group.add_argument(
        "--pr-body",
        dest="pr_body",
        metavar="TEXT",
        help="pass the PR body inline",
    )
    parser.add_argument(
        "--changed-files",
        nargs="*",
        default=None,
        metavar="PATH",
        help="override the changed-file set (CI/self-test only; by default the "
        "real branch/base diff is resolved with git)",
    )
    parser.add_argument(
        "--base-ref",
        default=None,
        metavar="REF",
        help="base ref to diff against (default @{upstream}, then origin/master)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="always print the structured report to stdout, even on success",
    )
    args = parser.parse_args(argv)

    if args.pr_body is not None:
        pr_body = args.pr_body
    elif args.pr_body_file is not None:
        pr_body = _read_pr_body(args.pr_body_file)
    else:
        print(
            "ci_preflight: missing PR body; pass --pr-body-file or --pr-body",
            file=sys.stderr,
        )
        return EXIT_USAGE

    if args.changed_files is not None:
        changed_files = list(args.changed_files)
    else:
        try:
            changed_files = changed_files_vs_base(args.base_ref)
        except RuntimeError as exc:
            report = {
                "schema_version": SCHEMA_VERSION,
                "phase": "preflight_metadata",
                "ok": False,
                "changed_files": [],
                "failures": [],
                "blocked": str(exc),
            }
            print(json.dumps(report, indent=2))
            return EXIT_NO_BASE

    failures = run_preflight(pr_body, changed_files)
    report = {
        "schema_version": SCHEMA_VERSION,
        "phase": "preflight_metadata",
        "ok": not failures,
        "changed_files": changed_files,
        "failures": failures,
        # Local preflight has no trusted GitHub event metadata, so PR prose
        # can never authorize the release exemption.
        "release_please_exempt": False,
    }
    if args.json or failures:
        print(json.dumps(report, indent=2))
    if failures:
        return EXIT_FAIL
    return EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
