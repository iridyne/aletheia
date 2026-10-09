#!/usr/bin/env python3
"""Changed-file fast feedback gate — the first step of every repair loop.

Runs only what is cheap, and only against the files this change touched:

  1. diff scan           trailing whitespace / conflict markers on added lines
  2. ``ruff check``        E/F plus B,ASYNC,RUF, reported only on changed lines
  3. ``check_test_hygiene`` dead skip/xfail and data-guarded silent-pass audit
  4. ``check_docs``        documentation governance, when docs/ or a spec changed
  5. ``pytest --lf``       the previous red set, when a pytest cache exists
  6. focused tests         test/spec files that name or import a changed module
  7. ``basedpyright``      basic mode, error level, changed lines only

Why changed lines only: the repository carries roughly a thousand pre-existing
B/ASYNC/RUF findings, so a whole-file gate would be pure noise. The contract this
enforces is "this change adds no new problem", not "the repository is clean".

Why no ``ruff format --check``: 1152 files would be reformatted today, so a
whole-file format check fails on any file someone edits. Adopt it only together
with a repository-wide formatting migration.

This is not a CI replacement: ``scripts/ci/ci_preflight.py`` and
``scripts/ci/ci_local.py`` own the PR-level gate. This is the 5-30s loop.

Usage:
  uv run python scripts/ci/dev_check.py               # working copy (@) vs its parent
  uv run python scripts/ci/dev_check.py --base master@origin   # jj revset; diff since the fork point
  uv run python scripts/ci/dev_check.py --no-tests --no-types

Exit codes: 0 clean, 1 findings, 2 usage/tool failure.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_docs
import check_test_hygiene
import vcs

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Extended rules are reported for changed lines only; see module docstring.
EXTEND_SELECT = "B,ASYNC,RUF"
# RUF001/002/003 flag ambiguous unicode (full-width punctuation) — this repo
# writes Chinese prose in comments and docstrings, so they are pure noise.
EXTEND_IGNORE = "RUF001,RUF002,RUF003"

PYTEST_TIMEOUT_SECONDS = "120"
FOCUSED_TEST_LIMIT = 8

EXIT_PASS = 0
EXIT_FINDINGS = 1
EXIT_TOOL_FAILURE = 2

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


# --------------------------------------------------------------------------- #
# Change set (jj; there is no staging area, so the view is `@` or the fork-point range)
# --------------------------------------------------------------------------- #
def changed_files(*, base: str | None) -> list[str]:
    """Repository-relative paths this change touches, sorted and deduplicated."""
    return vcs.changed_files(base)


def parse_added_lines(diff_text: str) -> dict[str, set[int]]:
    """Map ``b/<path>`` -> added/changed line numbers on the new side."""
    result: dict[str, set[int]] = {}
    current: str | None = None
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            current = line[len("+++ b/") :]
            result.setdefault(current, set())
        elif line.startswith("@@") and current is not None:
            match = _HUNK_RE.match(line)
            if match:
                start = int(match.group(1))
                count = int(match.group(2) or 1)
                result[current].update(range(start, start + count))
    return result


def changed_lines(*, base: str | None) -> dict[str, set[int] | None]:
    """Changed line numbers per path; ``None`` means "whole file" (new file)."""
    ranges: dict[str, set[int] | None] = dict(parse_added_lines(vcs.diff_text(base)))
    for path in changed_files(base=base):
        ranges.setdefault(path, None)  # renamed / unparseable -> whole file
    return ranges


def is_changed_line(ranges: Mapping[str, set[int] | None], path: str, row: int) -> bool:
    lines = ranges.get(path)
    return lines is None or row in lines


# --------------------------------------------------------------------------- #
# Steps
# --------------------------------------------------------------------------- #
def tool_command(name: str) -> list[str]:
    """Prefer the active venv's executable; fall back to ``uv run``."""
    candidate = Path(sys.executable).parent / name
    if candidate.exists():
        return [str(candidate)]
    if shutil.which(name):
        return [name]
    return ["uv", "run", name]


_CONFLICT_MARKERS = ("<<<<<<< ", ">>>>>>> ")
_ENV_FILE_RE = re.compile(r"^\.(env|env\..*)$")
_SECRETS_RE = re.compile(
    r"(PRIVATE_" "KEY|API_" "KEY|SECRET_" "KEY|ACCESS_" "TOKEN"  # split: this file must not flag itself
    r"|sk-[a-zA-Z0-9]{20,}|dapi[a-f0-9]{32,})"
)


def added_lines(diff_text: str) -> list[tuple[str, int, str]]:
    """``(path, new-side line number, text)`` for every added line of a git-format diff."""
    found: list[tuple[str, int, str]] = []
    path: str | None = None
    row = 0
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            path = line[len("+++ b/") :]
        elif line.startswith("+++ "):
            path = None  # /dev/null: deletion
        elif line.startswith("@@") and (match := _HUNK_RE.match(line)):
            row = int(match.group(1))
        elif path and line.startswith("+"):
            found.append((path, row, line[1:]))
            row += 1
    return found


def step_diff_check(diff_text: str) -> list[str]:
    """Trailing whitespace and unresolved conflict markers on added lines."""
    findings: list[str] = []
    for path, row, text in added_lines(diff_text):
        if text != text.rstrip():
            findings.append(f"{path}:{row}: trailing whitespace")
        if text.startswith(_CONFLICT_MARKERS) or text == "=======":
            findings.append(f"{path}:{row}: conflict marker")
    return findings


def step_secrets(files: list[str], diff_text: str) -> list[str]:
    """``.env`` files and credential-shaped added lines (jj runs no pre-commit hook)."""
    findings = [f"{path}: .env file must not be committed" for path in files if _ENV_FILE_RE.match(path)]
    findings.extend(
        f"{path}:{row}: possible secret/token"
        for path, row, text in added_lines(diff_text)
        if _SECRETS_RE.search(text)
    )
    return findings


def filter_ruff_diagnostics(
    diagnostics: list[dict], ranges: Mapping[str, set[int] | None]
) -> list[str]:
    """Keep only diagnostics that land on a changed line of a changed file."""
    findings: list[str] = []
    for diagnostic in diagnostics:
        path = diagnostic.get("filename", "")
        path = str(Path(path).resolve().relative_to(REPO_ROOT)) if path.startswith("/") else path
        row = int(diagnostic.get("location", {}).get("row", 0))
        if not is_changed_line(ranges, path, row):
            continue
        col = int(diagnostic.get("location", {}).get("column", 0))
        code = diagnostic.get("code") or "?"
        message = (diagnostic.get("message") or "").splitlines()[0]
        findings.append(f"{path}:{row}:{col} {code} {message}")
    return findings


def step_ruff(files: list[str], ranges: Mapping[str, set[int] | None]) -> list[str]:
    command = [
        *tool_command("ruff"),
        "check",
        "--extend-select",
        EXTEND_SELECT,
        "--extend-ignore",
        EXTEND_IGNORE,
        "--output-format",
        "json",
        *files,
    ]
    proc = subprocess.run(command, cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    try:
        diagnostics = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return [f"ruff produced unparseable output: {(proc.stderr or proc.stdout).strip()[:400]}"]
    return filter_ruff_diagnostics(diagnostics, ranges)


def step_hygiene(files: list[str]) -> list[str]:
    """Skip/xfail census, data-guarded silent-pass audit, and assertion presence
    on changed test files."""
    findings: list[str] = []
    test_files = [
        path for path in files if path.startswith("tests/") and path.endswith(".py")
    ]
    for path in test_files:
        findings.extend(check_test_hygiene.audit_path(REPO_ROOT / path))
    findings.extend(check_docs.vacuous_test_failures(test_files))
    return findings


# ``scripts/ci/check_docs.py`` had no automatic trigger at all: not this hook path,
# not ci.yml (``workflow_dispatch``-only), and ``scripts/ci/ci_plan.py`` only lists
# it for a human to run. Gates nothing runs are prose with extra steps. It is
# repository-wide and costs ~5s, so it runs only when the change touches what it
# actually reads — a doc, an executable spec, or the gate itself.
DOCS_GATE_TRIGGERS = ("docs/", "tests/specs/", "AGENTS.md", "README.md", "scripts/ci/check_docs.py")


def step_docs_governance(files: list[str]) -> list[str]:
    """Documentation-governance gates, run whole-repository when relevant.

    Unlike ruff and basedpyright this is not filtered to changed lines: every
    gate in ``check_docs`` is already baselined against the current tree, so it
    reports only what this change introduced.
    """
    if not any(path.startswith(DOCS_GATE_TRIGGERS) for path in files):
        return []
    findings: list[str] = []
    for gate in (
        check_docs.governance_failures,
        check_docs.proposal_metadata_failures,
        check_docs.contract_code_path_failures,
        check_docs.spec_claim_anchor_failures,
        check_docs.semantic_truth_failures,
    ):
        findings.extend(gate())
    findings.extend(
        check_docs.broken_relative_links(check_docs.current_authority_markdown_files())
    )
    return findings


def previous_failed_test_files() -> list[str]:
    """Bound pytest's native last-failed selection to existing cached files.

    Pytest owns async, inherited and parametrized node selection. Do not parse
    Python definitions or rewrite its cache: that can erase real failures.
    Stale selectors may cause a whole-file retry, never a whole-repository run.
    """
    cache = REPO_ROOT / ".pytest_cache" / "v" / "cache" / "lastfailed"
    try:
        failed = json.loads(cache.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return sorted({
        node.split("::", 1)[0]
        for node, was_failed in failed.items()
        if was_failed and (REPO_ROOT / node.split("::", 1)[0]).is_file()
    })


def focused_tests(changed: list[str]) -> list[str]:
    """Test/spec files that name or import a changed Python module."""
    found: set[str] = set()
    dotted_names: list[str] = []
    for path in changed:
        if not path.endswith(".py"):
            continue
        if path.startswith("tests/") and Path(path).name.startswith(("test_", "spec_")):
            found.add(path)
            continue
        stem = Path(path).stem
        for prefix in ("test_", "spec_"):
            found.update(
                str(candidate.relative_to(REPO_ROOT))
                for candidate in REPO_ROOT.glob(f"tests/**/{prefix}{stem}.py")
            )
        dotted = dotted_module(path)
        if dotted:
            dotted_names.append(dotted)
    if dotted_names:
        for candidate in (REPO_ROOT / "tests").rglob("*.py"):
            text = candidate.read_text(encoding="utf-8", errors="ignore")
            if any(name in text for name in dotted_names):
                found.add(str(candidate.relative_to(REPO_ROOT)))
    return sorted(found)[:FOCUSED_TEST_LIMIT]


def dotted_module(path: str) -> str | None:
    """Import path a test would use for a changed source file."""
    if path.startswith("core/aletheia/"):
        return "aletheia." + path[len("core/aletheia/") : -3].replace("/", ".")
    if path.startswith(("scripts/", "ops/", "extensions/")):
        return path[:-3].replace("/", ".")
    return None


def run_pytest(args: list[str]) -> int:
    runner = tool_command("pytest")
    if os.environ.get("ALETHEIA_TEST_HOST"):
        # Keep the dev machine cool: run on the remote sandbox (scripts/dev/remote_pytest.sh).
        runner = [str(REPO_ROOT / "scripts" / "dev" / "remote_pytest.sh")]
    command = [*runner, *args, "-q", "--tb=short", f"--timeout={PYTEST_TIMEOUT_SECONDS}"]
    return subprocess.run(command, cwd=REPO_ROOT, check=False).returncode


def step_pyright(
    files: list[str], ranges: Mapping[str, set[int] | None]
) -> tuple[list[str], str | None]:
    executable = Path(sys.executable).parent / "basedpyright"
    if not executable.exists():
        return [], "basedpyright not installed — skipped"
    proc = subprocess.run(
        [str(executable), "--level", "error", "--outputjson", *files],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return [f"basedpyright produced unparseable output: {(proc.stderr or proc.stdout).strip()[:400]}"], None
    findings: list[str] = []
    for diagnostic in payload.get("generalDiagnostics", []):
        if diagnostic.get("severity") != "error":
            continue
        path = diagnostic.get("file", "")
        path = str(Path(path).resolve().relative_to(REPO_ROOT)) if path.startswith("/") else path
        row = int(diagnostic.get("range", {}).get("start", {}).get("line", -1)) + 1
        if not is_changed_line(ranges, path, row):
            continue
        rule = diagnostic.get("rule") or "type"
        message = (diagnostic.get("message") or "").splitlines()[0]
        findings.append(f"{path}:{row} {rule} {message}")
    return findings, None


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fast changed-file feedback gate.")
    parser.add_argument("--base", help="jj revset to compare the branch against (diff since fork_point(@|BASE)), e.g. master@origin")
    parser.add_argument("--no-secrets-scan", action="store_true", help="skip the .env/secret scan (verified false positive only)")
    parser.add_argument("--no-tests", action="store_true", help="skip the pytest steps")
    parser.add_argument("--no-types", action="store_true", help="skip basedpyright")
    parser.add_argument("--fast", action="store_true", help="also run the `-m fast` default feedback set")
    args = parser.parse_args(argv)

    started = time.monotonic()
    changed = changed_files(base=args.base)
    if not changed:
        print("dev_check: no changed files")
        return EXIT_PASS

    ranges = changed_lines(base=args.base)
    python_files = [path for path in changed if path.endswith(".py")]

    print(f"dev_check: {len(changed)} changed file(s), {len(python_files)} Python")
    failures: list[str] = []

    diff_text = vcs.diff_text(args.base)
    whitespace = step_diff_check(diff_text)
    if whitespace:
        failures.extend(f"diff-check: {line}" for line in whitespace)
        print(f"  diff-check   FAIL ({len(whitespace)})")
    else:
        print("  diff-check   ok")

    if args.no_secrets_scan:
        print("  secrets      skipped (--no-secrets-scan)")
    else:
        secret_findings = step_secrets(changed, diff_text)
        if secret_findings:
            failures.extend(f"secrets {line}" for line in secret_findings)
            print(f"  secrets      FAIL ({len(secret_findings)})")
        else:
            print("  secrets      ok")

    if python_files:
        ruff_findings = step_ruff(python_files, ranges)
        if ruff_findings:
            failures.extend(f"ruff {line}" for line in ruff_findings)
            print(f"  ruff         FAIL ({len(ruff_findings)} new finding(s))")
        else:
            print("  ruff         ok (0 new findings)")
    else:
        print("  ruff         skipped (no Python changes)")

    hygiene_findings = step_hygiene(changed)
    if hygiene_findings:
        failures.extend(f"hygiene {line}" for line in hygiene_findings)
        print(f"  hygiene      FAIL ({len(hygiene_findings)} finding(s))")
    else:
        print("  hygiene      ok")

    if any(path.startswith(DOCS_GATE_TRIGGERS) for path in changed):
        docs_findings = step_docs_governance(changed)
        if docs_findings:
            failures.extend(f"docs {line}" for line in docs_findings)
            print(f"  docs         FAIL ({len(docs_findings)} finding(s))")
        else:
            print("  docs         ok")
    else:
        print("  docs         skipped (no docs/, spec, or gate change)")

    # Audit gates: cheap AST scans that enforce engineering discipline
    # (no silent except-pass, etc). Trigger on any Python change under
    # the areas the audit covers, plus changes to the audit itself.
    audit_touched = any(
        path.startswith(("core/aletheia/", "extensions/", "tests/audit/"))
        and path.endswith(".py")
        for path in changed
    )
    if audit_touched:
        audit_rc = run_pytest(["tests/audit/", "-q", "--no-header"])
        if audit_rc != 0:
            failures.append("audit gates: see tests/audit/ output above")
            print("  audit        FAIL")
        else:
            print("  audit        ok")
    else:
        print("  audit        skipped (no core/, extensions/, or tests/audit/ change)")

    if failures:
        _report(failures, started)
        return EXIT_FINDINGS

    if not args.no_tests:
        previous_failures = previous_failed_test_files()
        if previous_failures:
            print(f"  previous red  retrying failures in {len(previous_failures)} cached file(s) ...")
            previous_rc = run_pytest(["--lf", "--last-failed-no-failures=none", *previous_failures])
            if previous_rc == 5:  # pytest.ExitCode.NO_TESTS_COLLECTED
                print("  previous red  skipped (no tests selected in cached files)")
            elif previous_rc != 0:
                failures.append("previous failures still red — fix those first")
                print("  previous red  FAIL")
            else:
                print("  previous red  ok")
        else:
            print("  previous red  skipped (no existing cached test files)")

        if not failures:
            focused = focused_tests(changed)
            if focused:
                print(f"  focused      running {len(focused)} file(s): {', '.join(focused)}")
                if run_pytest(focused) != 0:
                    failures.append("focused tests: failed")
                    print("  focused      FAIL")
                else:
                    print("  focused      ok")
            else:
                print("  focused      skipped (no test names or imports a changed module)")

        if not failures and args.fast:
            print("  pytest -m fast running default feedback set ...")
            if run_pytest(["-m", "fast", "tests"]) != 0:
                failures.append("pytest -m fast: failed")
                print("  pytest -m fast FAIL")
            else:
                print("  pytest -m fast ok")

    if not failures and python_files and not args.no_types:
        type_findings, note = step_pyright(python_files, ranges)
        if note:
            print(f"  types        {note}")
        elif type_findings:
            failures.extend(f"basedpyright {line}" for line in type_findings)
            print(f"  types        FAIL ({len(type_findings)} new error(s))")
        else:
            print("  types        ok (0 new errors)")
    elif python_files and args.no_types:
        print("  types        skipped (--no-types)")

    if failures:
        _report(failures, started)
        return EXIT_FINDINGS

    print(f"dev_check: PASS in {time.monotonic() - started:.1f}s")
    return EXIT_PASS


def _report(failures: list[str], started: float) -> None:
    print(f"\ndev_check: FAIL in {time.monotonic() - started:.1f}s")
    for failure in failures:
        print(f"  {failure}")


if __name__ == "__main__":
    sys.exit(main())
