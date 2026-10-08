"""Thin jj wrapper for the local gates: changed files, git-format diff, file list.

jj has no staging area, so every view is either the working copy ``@`` against its
parent (``base=None``) or the branch since it forked from ``base`` (a jj revset,
e.g. ``master@origin``).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

_RENAME_RE = re.compile(r"^(?P<prefix>.*)\{(?P<old>.*) => (?P<new>.*)\}(?P<suffix>.*)$")


def run(*args: str) -> str:
    result = subprocess.run(
        ["jj", "--color=never", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise SystemExit(f"jj {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _range(base: str | None) -> list[str]:
    if base:
        return ["--from", f"fork_point(@|{base})", "--to", "@"]
    return ["-r", "@"]


def _summary_path(entry: str) -> str:
    """New-side path of a ``--summary`` entry (``R {old => new}`` or ``R a => b``)."""
    match = _RENAME_RE.match(entry)
    if match:
        return f"{match['prefix']}{match['new']}{match['suffix']}".replace("//", "/")
    return entry.split(" => ", 1)[-1]


def changed_files(base: str | None = None) -> list[str]:
    """Paths this change adds, modifies, renames or copies (deleted paths excluded)."""
    paths: set[str] = set()
    for line in run("diff", "--summary", *_range(base)).splitlines():
        status, _, entry = line.partition(" ")
        if status in {"A", "M", "R", "C"} and entry:
            paths.add(_summary_path(entry))
    return sorted(paths)


def diff_text(base: str | None = None) -> str:
    """Git-format unified diff with zero context lines."""
    return run("diff", "--git", "--context", "0", *_range(base))


def file_list(*filesets: str) -> list[str]:
    """Tracked plus new-but-not-ignored files matching the filesets."""
    return [line for line in run("file", "list", *filesets).splitlines() if line]
