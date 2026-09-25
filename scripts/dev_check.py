#!/usr/bin/env python3
"""Changed-file fast feedback gate — the first step of every repair loop.

Runs only what is cheap, and only against the files this change touched:

  1. ``git diff --check``  whitespace / conflict-marker hygiene on the whole diff
  2. ``ruff check``        E/F plus B,ASYNC,RUF, reported only on changed lines
  3. ``check_test_hygiene`` dead skip/xfail and data-guarded silent-pass audit
  4. ``pytest --lf``       the previous red set, when a pytest cache exists
  5. focused tests         test/spec files that name or import a changed module
  6. ``basedpyright``      basic mode, error level, changed lines only

Why changed lines only: the repository carries roughly a thousand pre-existing
B/ASYNC/RUF findings, so a whole-file gate would be pure noise. The contract this
enforces is "this change adds no new problem", not "the repository is clean".

Why no ``ruff format --check``: 1152 files would be reformatted today, so a
whole-file format check fails on any file someone edits. Adopt it only together
with a repository-wide formatting migration.

This is not a CI replacement: ``scripts/ci_preflight.py`` and
``scripts/ci_local.py`` own the PR-level gate. This is the 5-30s loop.

Usage:
  uv run python scripts/dev_check.py               # working tree vs HEAD
  uv run python scripts/dev_check.py --staged      # staged only
  uv run python scripts/dev_check.py --base origin/master
  uv run python scripts/dev_check.py --no-tests --no-types

Exit codes: 0 clean, 1 findings, 2 usage/tool failure.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_test_hygiene

REPO_ROOT = Path(__file__).resolve().parent.parent

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
# Git plumbing
# --------------------------------------------------------------------------- #
def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def changed_files(*, base: str | None, staged: bool) -> list[str]:
    """Repository-relative paths this change touches, sorted and deduplicated."""
    if base:
        out = _git("diff", "--name-only", "--diff-filter=ACMR", f"{base}...HEAD", "--")
        return sorted({line for line in out.splitlines() if line})
    if staged:
        out = _git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "--")
        return sorted({line for line in out.splitlines() if line})
    tracked = _git("diff", "--name-only", "--diff-filter=ACMR", "HEAD", "--")
    untracked = _git("ls-files", "--others", "--exclude-standard")
    return sorted(
        {line for line in tracked.splitlines() if line}
        | {line for line in untracked.splitlines() if line}
    )


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


def changed_lines(*, base: str | None, staged: bool) -> dict[str, set[int] | None]:
    """Changed line numbers per path; ``None`` means "whole file" (new file)."""
    if base:
        args = ["diff", "-U0", "--no-color", f"{base}...HEAD", "--"]
    elif staged:
        args = ["diff", "-U0", "--no-color", "--cached", "--"]
    else:
        args = ["diff", "-U0", "--no-color", "HEAD", "--"]
    ranges: dict[str, set[int] | None] = dict(parse_added_lines(_git(*args)))
    for path in changed_files(base=base, staged=staged):
        ranges.setdefault(path, None)  # untracked / renamed / unparseable -> whole file
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


def step_diff_check(*, base: str | None, staged: bool) -> list[str]:
    scope = ["--cached"] if staged and not base else []
    if base:
        out = _git("diff", "--check", f"{base}...HEAD", "--")
    else:
        out = _git("diff", "--check", *scope, "--") if staged else _git("diff", "--check", "--")
    return [line for line in out.splitlines() if line.strip()]


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
    """Skip/xfail census and data-guarded silent-pass audit on changed test files."""
    findings: list[str] = []
    for path in files:
        if path.startswith("tests/") and path.endswith(".py"):
            findings.extend(check_test_hygiene.audit_path(REPO_ROOT / path))
    return findings


def last_failed_exists() -> bool:
    cache = REPO_ROOT / ".pytest_cache" / "v" / "cache" / "lastfailed"
    if not cache.exists():
        return False
    try:
        return bool(json.loads(cache.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError):
        return False


def focused_tests(changed: list[str]) -> list[str]:
    """Test/spec files that name or import a changed Python module."""
    found: set[str] = set()
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
        if not dotted:
            continue
        grep = subprocess.run(
            ["git", "grep", "-l", "--untracked", "-F", dotted, "--", "tests"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        found.update(
            line for line in grep.stdout.splitlines() if line.endswith(".py") and line.startswith("tests/")
        )
    return sorted(found)[:FOCUSED_TEST_LIMIT]


def dotted_module(path: str) -> str | None:
    """Import path a test would use for a changed source file."""
    if path.startswith("core/aletheia/"):
        return "aletheia." + path[len("core/aletheia/") : -3].replace("/", ".")
    if path.startswith(("scripts/", "ops/", "extensions/")):
        return path[:-3].replace("/", ".")
    return None


def run_pytest(args: list[str]) -> int:
    command = [*tool_command("pytest"), *args, "-q", "--tb=short", f"--timeout={PYTEST_TIMEOUT_SECONDS}"]
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
    parser.add_argument("--base", help="compare the branch against this ref (merge-base diff)")
    parser.add_argument("--staged", action="store_true", help="check staged files only")
    parser.add_argument("--no-tests", action="store_true", help="skip the pytest steps")
    parser.add_argument("--no-types", action="store_true", help="skip basedpyright")
    parser.add_argument("--fast", action="store_true", help="also run the `-m fast` default feedback set")
    args = parser.parse_args(argv)

    if args.base and args.staged:
        parser.error("--base and --staged are mutually exclusive")

    started = time.monotonic()
    changed = changed_files(base=args.base, staged=args.staged)
    if not changed:
        print("dev_check: no changed files")
        return EXIT_PASS

    ranges = changed_lines(base=args.base, staged=args.staged)
    python_files = [path for path in changed if path.endswith(".py")]

    print(f"dev_check: {len(changed)} changed file(s), {len(python_files)} Python")
    failures: list[str] = []

    whitespace = step_diff_check(base=args.base, staged=args.staged)
    if whitespace:
        failures.extend(f"git diff --check: {line}" for line in whitespace)
        print(f"  diff-check   FAIL ({len(whitespace)})")
    else:
        print("  diff-check   ok")

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

    if failures:
        _report(failures, started)
        return EXIT_FINDINGS

    if not args.no_tests:
        if last_failed_exists():
            print("  pytest --lf  running previous red set ...")
            if run_pytest(["--lf"]) != 0:
                failures.append("pytest --lf: previous failures still red — fix those first")
                print("  pytest --lf  FAIL")
            else:
                print("  pytest --lf  ok")
        else:
            print("  pytest --lf  skipped (no pytest cache)")

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
